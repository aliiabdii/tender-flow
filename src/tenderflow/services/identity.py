import hashlib
import hmac
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from pydantic import SecretStr
from sqlalchemy import Engine, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tenderflow.config import Settings
from tenderflow.infrastructure.auth.local import (
    decode_access_token,
    hash_password,
    hash_secret,
    issue_access_token,
    new_secret,
    verify_password,
)
from tenderflow.infrastructure.database.models.identity import (
    ActivationToken,
    LocalCredential,
    LoginAttemptWindow,
    RefreshSession,
    User,
    UserRole,
)
from tenderflow.infrastructure.database.sessions import get_engine, session_scope
from tenderflow.schemas.identity import (
    ActivationSecret,
    Actor,
    ProvisionUser,
    TokenPair,
    UpdateUser,
    UserProvisioned,
    UserView,
    normalize_login,
)
from tenderflow.services.audit import write_audit
from tenderflow.services.errors import ServiceError


@dataclass(frozen=True)
class IdentityRuntime:
    engine: Engine
    settings: Settings


def _runtime(runtime: IdentityRuntime | None) -> IdentityRuntime:
    return runtime if runtime is not None else IdentityRuntime(get_engine(), Settings())


def _require_signing_key(settings: Settings) -> None:
    if settings.local_signing_key is None:
        raise ServiceError("authentication_unavailable")


def _view(user: User) -> UserView:
    return UserView(
        id=user.id,
        login=user.login,
        display_name=user.display_name,
        active=user.active,
        roles=frozenset(role.role for role in user.roles),
    )


def _admin_users(
    session: Session, actor: Actor, target: UUID | None = None
) -> dict[UUID, User]:
    ids = {actor.id}
    if target is not None:
        ids.add(target)
    users = {
        user.id: user
        for user in session.scalars(
            select(User).where(User.id.in_(ids)).order_by(User.id).with_for_update()
        )
    }
    administrator = users.get(actor.id)
    if (
        administrator is None
        or not administrator.active
        or "admin" not in {r.role for r in administrator.roles}
    ):
        raise ServiceError("forbidden")
    if target is not None and target not in users:
        raise ServiceError("user_not_found")
    return users


def _activation(
    session: Session, user: User, settings: Settings, purpose: str
) -> ActivationSecret:
    token = new_secret()
    expires = datetime.now(UTC) + timedelta(
        seconds=settings.activation_seconds
        if purpose == "activation"
        else settings.password_reset_seconds
    )
    session.add(
        ActivationToken(
            user_id=user.id,
            token_hash=hash_secret(token),
            purpose=purpose,
            credential_version=user.credential_version,
            expires_at=expires,
        )
    )
    return ActivationSecret(token=SecretStr(token), expires_at=expires)


def _create_user(
    session: Session, command: ProvisionUser, actor: Actor | None, settings: Settings
) -> UserProvisioned:
    user = User(
        id=uuid4(),
        login=command.login,
        display_name=command.display_name,
        active=True,
        credential_version=0,
        roles=[UserRole(role=role) for role in sorted(command.roles)],
    )
    if command.local_access:
        user.credentials = LocalCredential(password_hash=None)
    session.add(user)
    session.flush()
    activation = (
        _activation(session, user, settings, "activation")
        if command.local_access
        else None
    )
    write_audit(
        session=session,
        actor=actor,
        action="user.provisioned",
        entity_id=user.id,
        metadata={"roles": sorted(command.roles)},
    )
    return UserProvisioned(user=_view(user), activation=activation)


def provision_user(
    *, actor: Actor, command: ProvisionUser, runtime: IdentityRuntime | None = None
) -> UserProvisioned:
    runtime = _runtime(runtime)
    try:
        with session_scope(engine=runtime.engine) as session:
            _admin_users(session, actor)
            return _create_user(session, command, actor, runtime.settings)
    except IntegrityError as error:
        if (
            getattr(getattr(error.orig, "diag", None), "constraint_name", None)
            == "users_login_key"
        ):
            raise ServiceError("login_taken") from None
        raise


def bootstrap_administrator(
    *, login: str, display_name: str, runtime: IdentityRuntime | None = None
) -> UserProvisioned:
    runtime = _runtime(runtime)
    command = ProvisionUser(login=login, display_name=display_name, roles={"admin"})
    with session_scope(engine=runtime.engine) as session:
        session.execute(text("SELECT pg_advisory_xact_lock(7132408612)"))
        if (
            session.scalar(
                select(UserRole.user_id).where(UserRole.role == "admin").limit(1)
            )
            is not None
        ):
            raise ServiceError("administrator_exists")
        return _create_user(session, command, None, runtime.settings)


def _valid_activation(
    session: Session, token_hash: str, *, lock: bool = False
) -> tuple[User, ActivationToken]:
    statement = select(ActivationToken).where(ActivationToken.token_hash == token_hash)
    initial = session.scalar(statement)
    if initial is None:
        raise ServiceError("invalid_credentials")
    user = session.get(User, initial.user_id, with_for_update=lock)
    if lock:
        initial = session.scalar(
            statement.with_for_update().execution_options(populate_existing=True)
        )
    if (
        user is None
        or not user.active
        or initial.consumed_at is not None
        or initial.expires_at <= datetime.now(UTC)
        or initial.credential_version != user.credential_version
    ):
        raise ServiceError("invalid_credentials")
    return user, initial


def activate_local(
    *, token: str, password: str, runtime: IdentityRuntime | None = None
) -> None:
    runtime = _runtime(runtime)
    if (
        not runtime.settings.password_min_length
        <= len(password)
        <= runtime.settings.password_max_length
    ):
        raise ServiceError("invalid_password")
    token_hash = hash_secret(token)
    with session_scope(engine=runtime.engine) as session:
        _valid_activation(session, token_hash)
    encoded = hash_password(password)
    with session_scope(engine=runtime.engine) as session:
        user, activation = _valid_activation(session, token_hash, lock=True)
        user.credentials.password_hash = encoded
        user.credentials.password_changed_at = datetime.now(UTC)
        user.credential_version += 1
        activation.consumed_at = datetime.now(UTC)
        write_audit(
            session=session,
            actor=Actor(user.id, frozenset(r.role for r in user.roles)),
            action="user.local_activated",
            entity_id=user.id,
            metadata={"credential_version": user.credential_version},
        )


def _throttle_keys(
    settings: Settings, login: str, client_ip: str | None
) -> list[tuple[str, int, str]]:
    secret = settings.local_signing_key.get_secret_value().encode()
    subjects = [("login", login, settings.login_max_attempts)]
    if client_ip is not None:
        subjects.append(("ip", client_ip, settings.login_ip_max_attempts))
    return sorted(
        (
            hmac.new(secret, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest(),
            limit,
            kind,
        )
        for kind, value, limit in subjects
    )


def _reserve_login(runtime: IdentityRuntime, keys: list[tuple[str, int, str]]) -> None:
    now = datetime.now(UTC)
    with session_scope(engine=runtime.engine) as session:
        for key, limit, _ in keys:
            session.execute(
                insert(LoginAttemptWindow)
                .values(
                    key=key,
                    started_at=now,
                    attempts=0,
                )
                .on_conflict_do_nothing()
            )
            window = session.get(LoginAttemptWindow, key, with_for_update=True)
            if (
                window.started_at
                + timedelta(seconds=runtime.settings.login_window_seconds)
                <= now
            ):
                window.started_at, window.attempts = now, 0
            if window.attempts >= limit:
                remaining = (
                    window.started_at
                    + timedelta(seconds=runtime.settings.login_window_seconds)
                    - now
                )
                raise ServiceError(
                    "rate_limited",
                    retry_after=max(1, math.ceil(remaining.total_seconds())),
                )
            window.attempts += 1


def _successful_login(session: Session, keys: list[tuple[str, int, str]]) -> None:
    for key, _, kind in keys:
        window = session.get(LoginAttemptWindow, key, with_for_update=True)
        window.attempts = 0 if kind == "login" else max(0, window.attempts - 1)


def _issue_pair(
    session: Session,
    runtime: IdentityRuntime,
    user: User,
    *,
    family_id: UUID | None = None,
    expires_at: datetime | None = None,
) -> TokenPair:
    token = new_secret()
    refresh = RefreshSession(
        id=uuid4(),
        user_id=user.id,
        family_id=family_id or uuid4(),
        token_hash=hash_secret(token),
        credential_version=user.credential_version,
        expires_at=expires_at
        or datetime.now(UTC)
        + timedelta(seconds=runtime.settings.local_refresh_seconds),
    )
    session.add(refresh)
    session.flush()
    return TokenPair(
        access_token=SecretStr(
            issue_access_token(
                settings=runtime.settings,
                user_id=user.id,
                session_id=refresh.id,
                version=user.credential_version,
            )
        ),
        refresh_token=SecretStr(token),
        expires_in=runtime.settings.local_access_seconds,
    )


def authenticate_local(
    *,
    login: str,
    password: str,
    client_ip: str | None = None,
    runtime: IdentityRuntime | None = None,
) -> TokenPair:
    runtime = _runtime(runtime)
    _require_signing_key(runtime.settings)
    try:
        login = normalize_login(login)
    except ValueError:
        raise ServiceError("invalid_credentials") from None
    keys = _throttle_keys(runtime.settings, login, client_ip)
    _reserve_login(runtime, keys)
    with session_scope(engine=runtime.engine) as session:
        user = session.scalar(select(User).where(User.login == login))
        encoded = user.credentials.password_hash if user and user.credentials else None
        user_id = user.id if user else None
        version = user.credential_version if user else None
    if not verify_password(password, encoded):
        raise ServiceError("invalid_credentials")
    with session_scope(engine=runtime.engine) as session:
        user = session.get(User, user_id, with_for_update=True)
        if (
            user is None
            or not user.active
            or user.credential_version != version
            or user.credentials is None
            or user.credentials.password_hash != encoded
        ):
            raise ServiceError("invalid_credentials")
        _successful_login(session, keys)
        return _issue_pair(session, runtime, user)


def _revoke(session: Session, user_id: UUID, family_id: UUID | None = None) -> None:
    statement = update(RefreshSession).where(RefreshSession.user_id == user_id)
    if family_id is not None:
        statement = statement.where(RefreshSession.family_id == family_id)
    session.execute(statement.values(revoked_at=datetime.now(UTC)))


def refresh_local(
    *, refresh_token: str, runtime: IdentityRuntime | None = None
) -> TokenPair:
    runtime = _runtime(runtime)
    _require_signing_key(runtime.settings)
    replay = False
    with session_scope(engine=runtime.engine) as session:
        initial = session.scalar(
            select(RefreshSession).where(
                RefreshSession.token_hash == hash_secret(refresh_token)
            )
        )
        if initial is None:
            raise ServiceError("invalid_credentials")
        user = session.get(User, initial.user_id, with_for_update=True)
        refresh = session.get(
            RefreshSession, initial.id, with_for_update=True, populate_existing=True
        )
        if refresh.consumed_at is not None:
            _revoke(session, user.id, refresh.family_id)
            write_audit(
                session=session,
                actor=Actor(user.id, frozenset()),
                action="session.revoked",
                entity_id=user.id,
                metadata={
                    "session_family_id": str(refresh.family_id),
                    "reason_code": "REPLAY",
                },
            )
            replay = True
        elif (
            not user.active
            or refresh.revoked_at is not None
            or refresh.expires_at <= datetime.now(UTC)
            or refresh.credential_version != user.credential_version
        ):
            raise ServiceError("invalid_credentials")
        else:
            refresh.consumed_at = datetime.now(UTC)
            pair = _issue_pair(
                session,
                runtime,
                user,
                family_id=refresh.family_id,
                expires_at=refresh.expires_at,
            )
    # Commit replay revocation before returning an authentication error.
    if replay:
        raise ServiceError("invalid_credentials")
    return pair


def logout_local(*, refresh_token: str, runtime: IdentityRuntime | None = None) -> None:
    runtime = _runtime(runtime)
    with session_scope(engine=runtime.engine) as session:
        refresh = session.scalar(
            select(RefreshSession).where(
                RefreshSession.token_hash == hash_secret(refresh_token)
            )
        )
        if refresh is not None:
            session.get(User, refresh.user_id, with_for_update=True)
            _revoke(session, refresh.user_id, refresh.family_id)
            write_audit(
                session=session,
                actor=Actor(refresh.user_id, frozenset()),
                action="session.revoked",
                entity_id=refresh.user_id,
                metadata={
                    "session_family_id": str(refresh.family_id),
                    "reason_code": "LOGOUT",
                },
            )


def get_actor(*, bearer_token: str, runtime: IdentityRuntime | None = None) -> Actor:
    runtime = _runtime(runtime)
    _require_signing_key(runtime.settings)
    try:
        claims = decode_access_token(settings=runtime.settings, token=bearer_token)
        user_id, session_id = UUID(claims["sub"]), UUID(claims["sid"])
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise ServiceError("invalid_credentials") from None
    with session_scope(engine=runtime.engine) as session:
        user = session.get(User, user_id)
        refresh = session.get(RefreshSession, session_id)
        if (
            user is None
            or not user.active
            or refresh is None
            or refresh.user_id != user.id
            or refresh.revoked_at is not None
            or refresh.expires_at <= datetime.now(UTC)
            or claims["ver"] != user.credential_version
            or refresh.credential_version != user.credential_version
        ):
            raise ServiceError("invalid_credentials")
        return Actor(user.id, frozenset(role.role for role in user.roles))


def current_user(*, actor: Actor, runtime: IdentityRuntime | None = None) -> UserView:
    runtime = _runtime(runtime)
    with session_scope(engine=runtime.engine) as session:
        user = session.get(User, actor.id)
        if user is None or not user.active:
            raise ServiceError("invalid_credentials")
        return _view(user)


def update_user(
    *,
    actor: Actor,
    user_id: UUID,
    command: UpdateUser,
    runtime: IdentityRuntime | None = None,
) -> UserView:
    runtime = _runtime(runtime)
    with session_scope(engine=runtime.engine) as session:
        user = _admin_users(session, actor, user_id)[user_id]
        if command.roles is not None:
            previous = {role.role for role in user.roles}
            user.roles[:] = [role for role in user.roles if role.role in command.roles]
            user.roles.extend(UserRole(role=role) for role in command.roles - previous)
        if command.active is not None:
            user.active = command.active
            if not user.active:
                _revoke(session, user.id)
        user.updated_at = datetime.now(UTC)
        write_audit(
            session=session,
            actor=actor,
            action="user.updated",
            entity_id=user.id,
            metadata={
                "roles": sorted(role.role for role in user.roles),
                "status": "ACTIVE" if user.active else "INACTIVE",
            },
        )
        return _view(user)


def reset_local_password(
    *, actor: Actor, user_id: UUID, runtime: IdentityRuntime | None = None
) -> ActivationSecret:
    runtime = _runtime(runtime)
    with session_scope(engine=runtime.engine) as session:
        user = _admin_users(session, actor, user_id)[user_id]
        if user.credentials is None:
            user.credentials = LocalCredential(password_hash=None)
        user.credentials.password_hash = None
        user.credential_version += 1
        _revoke(session, user.id)
        token = _activation(session, user, runtime.settings, "reset")
        write_audit(
            session=session,
            actor=actor,
            action="user.password_reset",
            entity_id=user.id,
            metadata={
                "credential_version": user.credential_version,
                "reason_code": "RESET",
            },
        )
        return token
