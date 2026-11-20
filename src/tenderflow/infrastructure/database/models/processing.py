from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tenderflow.infrastructure.database.base import Base


class ProcessingRun(Base):
    __tablename__ = "processing_runs"
    __table_args__ = (
        UniqueConstraint("id", "tender_id", name="uq_processing_run_tender"),
        CheckConstraint(
            "kind IN ('extraction','matching','lookup','pricing','export')",
            name="ck_processing_run_kind",
        ),
        CheckConstraint(
            "status IN ('queued','running','succeeded','partial_failure','failed','superseded')",
            name="ck_processing_run_status",
        ),
        Index(
            "uq_processing_run_current",
            "tender_id",
            "kind",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tender_id: Mapped[UUID] = mapped_column(ForeignKey("tenders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    retry_of_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("processing_runs.id"), index=True
    )
    superseded_by_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("processing_runs.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessingItem(Base):
    __tablename__ = "processing_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "tender_id"],
            ["processing_runs.id", "processing_runs.tender_id"],
            name="fk_processing_item_run_tender",
        ),
        ForeignKeyConstraint(
            ["document_id", "tender_id"],
            ["documents.id", "documents.tender_id"],
            name="fk_processing_item_document_tender",
        ),
        UniqueConstraint("run_id", "key", name="uq_processing_item_key"),
        CheckConstraint(
            "state IN ('queued','running','succeeded','failed','superseded')",
            name="ck_processing_item_state",
        ),
        CheckConstraint(
            "(state='failed' AND error_code IS NOT NULL AND result_id IS NULL) "
            "OR (state<>'failed' AND error_code IS NULL)",
            name="ck_processing_item_outcome",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN "
            "('upstream_timeout','upstream_unavailable','upstream_rate_limited',"
            "'configuration_error','unsupported_input','invalid_result','worker_failed')",
            name="ck_processing_item_error",
        ),
        Index("ix_processing_items_page", "run_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("processing_runs.id"), index=True)
    tender_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID | None] = mapped_column(index=True)
    key: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(32), default="queued")
    expected_inputs: Mapped[dict] = mapped_column(JSONB)
    task_name: Mapped[str] = mapped_column(String(128))
    job_id: Mapped[int | None] = mapped_column(Integer)
    retry_of_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("processing_items.id"), index=True
    )
    result_id: Mapped[UUID | None] = mapped_column()
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
