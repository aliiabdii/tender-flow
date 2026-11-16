import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)

Role = Literal["admin", "tender_manager", "approver"]
ROLES = frozenset({"admin", "tender_manager", "approver"})


def normalize_login(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    if not 1 <= len(value) <= 254 or any(
        unicodedata.category(c).startswith("C") for c in value
    ):
        raise ValueError("Invalid login name")
    return value


@dataclass(frozen=True)
class Actor:
    id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class EntraIdentity:
    tenant_id: UUID
    object_id: UUID


class IdentityInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    @field_validator("*")
    @classmethod
    def valid_secret_encoding(cls, value):
        if isinstance(value, SecretStr):
            try:
                value.get_secret_value().encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("Secret must be valid UTF-8") from None
        return value


class LoginRequest(IdentityInput):
    login: str = Field(min_length=1, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=4096)


class RefreshRequest(IdentityInput):
    refresh_token: SecretStr = Field(min_length=1, max_length=256)


class ActivationRequest(IdentityInput):
    token: SecretStr = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=4096)


class ProvisionUser(IdentityInput):
    login: str
    display_name: str = Field(min_length=1, max_length=200)
    roles: frozenset[Role] = frozenset()
    local_access: bool = True

    @field_validator("login")
    @classmethod
    def normalized_login(cls, value: str) -> str:
        return normalize_login(value)


class UpdateUser(IdentityInput):
    active: bool | None = None
    roles: frozenset[Role] | None = None

    @model_validator(mode="after")
    def supplied_changes(self):
        if not self.model_fields_set or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("Supply active and/or roles with non-null values")
        return self


class LinkEntraIdentity(IdentityInput):
    tenant_id: UUID
    object_id: UUID


class EntraIdentityView(BaseModel):
    id: UUID
    user_id: UUID
    tenant_id: UUID
    object_id: UUID


class UserView(BaseModel):
    id: UUID
    login: str
    display_name: str
    active: bool
    roles: frozenset[Role]


class ActivationSecret(BaseModel):
    token: SecretStr
    expires_at: datetime

    @field_serializer("token", when_used="json")
    def expose_token(self, value: SecretStr) -> str:
        return value.get_secret_value()


class UserProvisioned(BaseModel):
    user: UserView
    activation: ActivationSecret | None


class TokenPair(BaseModel):
    access_token: SecretStr
    refresh_token: SecretStr
    token_type: Literal["bearer"] = "bearer"
    expires_in: int

    @field_serializer("access_token", "refresh_token", when_used="json")
    def expose_tokens(self, value: SecretStr) -> str:
        return value.get_secret_value()
