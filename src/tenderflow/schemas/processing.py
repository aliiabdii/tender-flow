from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, PositiveInt, model_validator


class RunKind(StrEnum):
    EXTRACTION = "extraction"
    MATCHING = "matching"
    LOOKUP = "lookup"
    PRICING = "pricing"
    EXPORT = "export"


ErrorCode = Literal[
    "upstream_timeout",
    "upstream_unavailable",
    "upstream_rate_limited",
    "configuration_error",
    "unsupported_input",
    "invalid_result",
    "worker_failed",
]
ItemState = Literal["queued", "running", "succeeded", "failed", "superseded"]
RunState = Literal[
    "queued", "running", "succeeded", "partial_failure", "failed", "superseded"
]


class InputVersions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    source_revision: PositiveInt
    preparation_version: PositiveInt | None = None


class ItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    status: Literal["succeeded", "failed"]
    result_id: UUID | None = None
    error_code: ErrorCode | None = None

    @model_validator(mode="after")
    def valid_result(self):
        if self.status == "failed":
            if self.error_code is None or self.result_id is not None:
                raise ValueError("Failure requires only a safe error code")
        elif self.error_code is not None:
            raise ValueError("Success cannot contain an error")
        return self


class RetryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class RunView(BaseModel):
    id: UUID
    tender_id: UUID
    kind: RunKind
    status: RunState
    is_current: bool
    retry_of_run_id: UUID | None
    superseded_by_run_id: UUID | None
    total_count: int
    completed_count: int
    queued_count: int
    running_count: int
    succeeded_count: int
    failed_count: int
    superseded_count: int
    warnings: list[str]
    items_url: str
    created_at: datetime
    finished_at: datetime | None


class ItemView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    run_id: UUID
    state: ItemState
    retry_of_item_id: UUID | None
    result_id: UUID | None
    error_code: ErrorCode | None
    created_at: datetime
    finished_at: datetime | None
