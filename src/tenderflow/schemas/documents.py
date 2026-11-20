import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceName(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    filename: str = Field(min_length=1, max_length=200)

    @field_validator("filename")
    @classmethod
    def safe_name(cls, value):
        if value in {".", ".."} or any(
            char in "/\\" or unicodedata.category(char).startswith("C")
            for char in value
        ):
            raise ValueError("Invalid source filename")
        return value


class DocumentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tender_id: UUID
    original_name: str
    format: Literal["csv", "xlsx", "pdf", "docx"]
    checksum: str
    size_bytes: int
    source_revision: int
    created_at: datetime
