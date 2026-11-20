from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from tenderflow.infrastructure.database.base import Base


class Buyer(Base):
    __tablename__ = "buyers"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_buyer_revision"),
        Index("ix_buyers_page", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    customer_number: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Tender(Base):
    __tablename__ = "tenders"
    __table_args__ = (
        CheckConstraint(
            "source_revision >= 1 AND preparation_version >= 1 AND content_revision >= 1",
            name="ck_tender_revisions",
        ),
        CheckConstraint(
            "supersedes_tender_id IS NULL OR supersedes_tender_id <> id",
            name="ck_tender_not_self_amendment",
        ),
        Index("ix_tenders_page", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    buyer_id: Mapped[UUID] = mapped_column(ForeignKey("buyers.id"), index=True)
    supersedes_tender_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenders.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    pricing_customer_number: Mapped[str] = mapped_column(String(128))
    submission_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    sources_frozen: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    source_revision: Mapped[int] = mapped_column(Integer, default=1)
    preparation_version: Mapped[int] = mapped_column(Integer, default=1)
    content_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TenderContributor(Base):
    __tablename__ = "tender_contributors"
    tender_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), primary_key=True, index=True
    )
    first_contributed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(200), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    result_id: Mapped[UUID] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
