import time
from functools import lru_cache
from uuid import UUID

import jwt
from jwt import PyJWKClient

from tenderflow.config import Settings
from tenderflow.schemas.identity import EntraIdentity
from tenderflow.services.errors import ServiceError


class _EntraJWKClient(PyJWKClient):
    def __init__(self, tenant_id: UUID):
        super().__init__(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
            timeout=3,
            lifespan=300,
            cache_keys=False,
            cooldown_duration=30,
        )
        self._retry_after = 0.0

    def fetch_data(self):
        # PyJWT serializes fetches, but its cooldown covers only successful ones.
        if time.monotonic() < self._retry_after:
            raise jwt.PyJWKClientConnectionError("JWKS temporarily unavailable")
        try:
            data = super().fetch_data()
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            self._retry_after = time.monotonic() + 30
            raise
        self._retry_after = 0.0
        return data


class EntraTokenValidator:
    def __init__(self, settings: Settings):
        if not settings.entra_enabled:
            raise ServiceError("authentication_unavailable")
        self.tenant_id = settings.entra_tenant_id
        self.audience = str(settings.entra_audience)
        self.scope = settings.entra_required_scope.rsplit("/", 1)[-1]
        self.issuer = f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"
        self.keys = _EntraJWKClient(self.tenant_id)

    def validate(self, token: str) -> EntraIdentity:
        try:
            header = jwt.get_unverified_header(token)
            if (
                header.get("alg") != "RS256"
                or header.get("typ") != "JWT"
                or not isinstance(header.get("kid"), str)
                or not 1 <= len(header["kid"]) <= 256
            ):
                raise jwt.InvalidTokenError()
        except jwt.PyJWTError:
            raise ServiceError("invalid_credentials") from None
        try:
            key = self.keys.get_signing_key(header["kid"])
        except jwt.PyJWKClientConnectionError:
            raise ServiceError("identity_provider_unavailable") from None
        except jwt.PyJWKClientError:
            raise ServiceError("invalid_credentials") from None
        except (jwt.PyJWKSetError, ValueError, TypeError, KeyError):
            raise ServiceError("identity_provider_unavailable") from None
        try:
            key_issuer = key._jwk_data.get("issuer")
            if key_issuer is not None and (
                not isinstance(key_issuer, str)
                or key_issuer.replace("{tenantid}", str(self.tenant_id)) != self.issuer
            ):
                raise jwt.InvalidTokenError()
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "strict_aud": True,
                    "require": [
                        "iss",
                        "aud",
                        "iat",
                        "nbf",
                        "exp",
                        "sub",
                        "tid",
                        "oid",
                        "ver",
                        "scp",
                    ],
                },
            )
            if (
                claims["ver"] != "2.0"
                or claims.get("idtyp") not in (None, "user")
                or not isinstance(claims["scp"], str)
                or self.scope not in claims["scp"].split()
                or not isinstance(claims["tid"], str)
                or not isinstance(claims["oid"], str)
            ):
                raise jwt.InvalidTokenError()
            identity = EntraIdentity(UUID(claims["tid"]), UUID(claims["oid"]))
            if identity.tenant_id != self.tenant_id:
                raise jwt.InvalidTokenError()
            return identity
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise ServiceError("invalid_credentials") from None


@lru_cache(maxsize=8)
def get_entra_validator(settings: Settings) -> EntraTokenValidator:
    return EntraTokenValidator(settings)


def validate_entra_access_token(
    *, token: str, validator: EntraTokenValidator | None = None
) -> EntraIdentity:
    validator = validator if validator is not None else get_entra_validator(Settings())
    return validator.validate(token)
