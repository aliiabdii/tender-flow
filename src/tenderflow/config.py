from typing import Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TENDERFLOW_", frozen=True, hide_input_in_errors=True
    )

    max_upload_bytes: PositiveInt = 25 * 1024 * 1024
    max_documents_per_tender: PositiveInt = 20
    max_lines_per_tender: PositiveInt = 5_000
    default_page_size: PositiveInt = 100
    max_page_size: PositiveInt = 200
    idempotency_seconds: PositiveInt = 86_400
    database_url: SecretStr | None = None
    database_pool_size: PositiveInt = 5
    database_max_overflow: NonNegativeInt = 5
    worker_concurrency: PositiveInt = 4
    worker_heartbeat_seconds: PositiveFloat = 10
    worker_stalled_seconds: PositiveFloat = 30
    job_max_retries: PositiveInt = 3
    local_signing_key: SecretStr | None = None
    local_token_issuer: str = "urn:tenderflow:local"
    local_token_audience: str = "urn:tenderflow:api"
    local_access_seconds: PositiveInt = 600
    local_refresh_seconds: PositiveInt = 604_800
    activation_seconds: PositiveInt = 86_400
    password_reset_seconds: PositiveInt = 3_600
    password_min_length: PositiveInt = 15
    password_max_length: PositiveInt = 128
    login_window_seconds: PositiveInt = 900
    login_max_attempts: PositiveInt = 5
    login_ip_max_attempts: PositiveInt = 100
    entra_tenant_id: UUID | None = None
    entra_audience: UUID | None = None
    entra_required_scope: str | None = None

    @property
    def entra_enabled(self) -> bool:
        return self.entra_tenant_id is not None

    @field_validator("entra_required_scope")
    @classmethod
    def validate_entra_scope(cls, value: str | None) -> str | None:
        if value is not None:
            uri = urlsplit(value)
            if (
                uri.scheme not in {"api", "https"}
                or not uri.netloc
                or not uri.path.strip("/")
                or value.endswith("/")
                or uri.query
                or uri.fragment
                or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in value)
            ):
                raise ValueError("Entra scope must be a full delegated API scope URI")
        return value

    @field_validator("local_token_issuer", "local_token_audience")
    @classmethod
    def validate_token_identifier(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Local token trust identifiers must not be blank")
        return value

    @field_validator("local_signing_key")
    @classmethod
    def validate_signing_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode()) < 32:
            raise ValueError("Local signing key must contain at least 32 bytes")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                url = make_url(value.get_secret_value())
            except ArgumentError:
                raise ValueError("Invalid PostgreSQL connection URL") from None
            if url.drivername != "postgresql+psycopg2":
                raise ValueError("Database URL must use postgresql+psycopg2")
        return value

    @model_validator(mode="after")
    def validate_page_sizes(self) -> Self:
        entra_values = (
            self.entra_tenant_id,
            self.entra_audience,
            self.entra_required_scope,
        )
        if any(value is not None for value in entra_values) and any(
            value is None for value in entra_values
        ):
            raise ValueError(
                "Configure the Entra tenant, API audience and delegated scope together"
            )
        if self.default_page_size > self.max_page_size:
            raise ValueError("default_page_size must not exceed max_page_size")
        if self.worker_stalled_seconds <= self.worker_heartbeat_seconds * 2:
            raise ValueError("Worker timeout must exceed two heartbeat intervals")
        if (
            self.password_min_length > self.password_max_length
            or self.password_max_length > 4096
        ):
            raise ValueError("Invalid password length policy")
        return self
