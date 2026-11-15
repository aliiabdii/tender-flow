from typing import Self

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
    database_url: SecretStr | None = None
    database_pool_size: PositiveInt = 5
    database_max_overflow: NonNegativeInt = 5
    worker_concurrency: PositiveInt = 4
    worker_heartbeat_seconds: PositiveFloat = 10
    worker_stalled_seconds: PositiveFloat = 30
    job_max_retries: PositiveInt = 3

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
        if self.default_page_size > self.max_page_size:
            raise ValueError("default_page_size must not exceed max_page_size")
        if self.worker_stalled_seconds <= self.worker_heartbeat_seconds * 2:
            raise ValueError("Worker timeout must exceed two heartbeat intervals")
        return self
