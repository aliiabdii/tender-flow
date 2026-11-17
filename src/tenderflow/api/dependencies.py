from collections.abc import Iterator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from tenderflow.infrastructure.database.sessions import session_scope
from tenderflow.schemas.identity import Actor
from tenderflow.services.errors import ServiceError
from tenderflow.services.identity import IdentityRuntime, get_actor

_bearer = HTTPBearer(auto_error=False)


def get_identity_runtime(request: Request) -> IdentityRuntime:
    if request.app.state.engine is None or (
        request.app.state.settings.local_signing_key is None
        and not request.app.state.settings.entra_enabled
    ):
        raise ServiceError("authentication_unavailable")
    return IdentityRuntime(
        request.app.state.engine,
        request.app.state.settings,
        request.app.state.entra_validator,
    )


def current_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
) -> Actor:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ServiceError("invalid_credentials")
    return get_actor(bearer_token=credentials.credentials, runtime=runtime)


def require_roles(*roles: str):
    def authorize(actor: Actor = Depends(current_actor)) -> Actor:
        if not set(roles) <= actor.roles:
            raise ServiceError("forbidden")
        return actor

    return authorize


def database_session(
    runtime: IdentityRuntime = Depends(get_identity_runtime),
) -> Iterator[Session]:
    with session_scope(engine=runtime.engine) as session:
        yield session
