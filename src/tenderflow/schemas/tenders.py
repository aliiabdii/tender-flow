import unicodedata
from datetime import datetime
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class BusinessInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid", hide_input_in_errors=True, str_strip_whitespace=True
    )

    @field_validator("*")
    @classmethod
    def valid_text(cls, value):
        if isinstance(value, str) and any(
            unicodedata.category(c).startswith("C") for c in value
        ):
            raise ValueError("Text must not contain control characters")
        return value


class BuyerCreate(BusinessInput):
    name: str = Field(min_length=1, max_length=200)
    customer_number: str = Field(min_length=1, max_length=128)


class BuyerUpdate(BusinessInput):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    customer_number: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def valid_changes(self):
        if not self.model_fields_set or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("Supply non-null buyer changes")
        return self


class TenderCreate(BusinessInput):
    buyer_id: UUID
    title: str = Field(min_length=1, max_length=300)
    pricing_customer_number: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    submission_deadline: AwareDatetime | None = None
    supersedes_tender_id: UUID | None = None


class TenderUpdate(BusinessInput):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    pricing_customer_number: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    submission_deadline: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_changes(self):
        if not self.model_fields_set or any(
            name != "submission_deadline" and getattr(self, name) is None
            for name in self.model_fields_set
        ):
            raise ValueError("Supply permitted tender changes")
        return self


class BuyerView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    customer_number: str
    revision: int
    created_at: datetime
    updated_at: datetime


class TenderView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    buyer_id: UUID
    supersedes_tender_id: UUID | None
    title: str
    pricing_customer_number: str
    submission_deadline: datetime | None
    created_by: UUID
    status: str
    source_revision: int
    preparation_version: int
    content_revision: int
    created_at: datetime
    updated_at: datetime
    warnings: list[str] = Field(default_factory=list)


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None
