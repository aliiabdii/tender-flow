from typing import Self

from pydantic import PositiveInt, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TENDERFLOW_", frozen=True)

    max_upload_bytes: PositiveInt = 25 * 1024 * 1024
    max_documents_per_tender: PositiveInt = 20
    max_lines_per_tender: PositiveInt = 5_000
    default_page_size: PositiveInt = 100
    max_page_size: PositiveInt = 200

    @model_validator(mode="after")
    def validate_page_sizes(self) -> Self:
        if self.default_page_size > self.max_page_size:
            raise ValueError("default_page_size must not exceed max_page_size")
        return self
