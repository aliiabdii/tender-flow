import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from threading import BoundedSemaphore
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from tenderflow.config import Settings

_hasher = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=1)
_hash_slots = BoundedSemaphore(4)
_dummy_hash = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    with _hash_slots:
        return _hasher.hash(password)


def verify_password(password: str, encoded: str | None) -> bool:
    with _hash_slots:
        try:
            valid = _hasher.verify(encoded or _dummy_hash, password)
        except VerificationError:
            return False
    return bool(encoded) and valid


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def issue_access_token(
    *, settings: Settings, user_id: UUID, session_id: UUID, version: int
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "sid": str(session_id),
            "ver": version,
            "token_use": "access",
            "iss": settings.local_token_issuer,
            "aud": settings.local_token_audience,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(seconds=settings.local_access_seconds),
        },
        settings.local_signing_key.get_secret_value(),
        algorithm="HS256",
    )


def decode_access_token(*, settings: Settings, token: str) -> dict:
    claims = jwt.decode(
        token,
        settings.local_signing_key.get_secret_value(),
        algorithms=["HS256"],
        issuer=settings.local_token_issuer,
        audience=settings.local_token_audience,
        options={
            "require": [
                "sub",
                "sid",
                "ver",
                "token_use",
                "iss",
                "aud",
                "iat",
                "nbf",
                "exp",
            ]
        },
    )
    if claims["token_use"] != "access" or type(claims["ver"]) is not int:
        raise jwt.InvalidTokenError()
    return claims
