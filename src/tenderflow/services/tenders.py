import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tenderflow.infrastructure.database.models import (
    AuditEvent,
    Buyer,
    IdempotencyKey,
    Tender,
    TenderContributor,
    User,
)
from tenderflow.schemas.identity import Actor
from tenderflow.schemas.tenders import (
    BuyerCreate,
    BuyerUpdate,
    TenderCreate,
    TenderUpdate,
    TenderView,
)
from tenderflow.services.audit import write_audit
from tenderflow.services.errors import ServiceError


def _authorize(session: Session, actor: Actor, *, write: bool = False) -> None:
    user = session.get(User, actor.id, with_for_update=write, populate_existing=True)
    roles = {role.role for role in user.roles} if user is not None else set()
    allowed = {"tender_manager"} if write else {"tender_manager", "approver"}
    if user is None or not user.active or not roles.intersection(allowed):
        raise ServiceError("forbidden")


def require_version(*, actual: int, expected: int | None) -> None:
    if expected is None:
        raise ServiceError("precondition_required")
    if expected != actual:
        raise ServiceError("precondition_failed")


def record_contribution(*, session: Session, tender_id: UUID, actor: Actor) -> None:
    session.execute(
        insert(TenderContributor)
        .values(tender_id=tender_id, user_id=actor.id)
        .on_conflict_do_nothing()
    )


def lock_tender(
    *, session: Session, actor: Actor, tender_id: UUID, expected: int | None
) -> Tender:
    _authorize(session, actor, write=True)
    tender = session.get(
        Tender, tender_id, with_for_update=True, populate_existing=True
    )
    if tender is None:
        raise ServiceError("tender_not_found")
    require_version(actual=tender.content_revision, expected=expected)
    return tender


def touch_preparation(*, session: Session, tender: Tender, actor: Actor) -> None:
    session.execute(
        update(Tender)
        .where(Tender.id == tender.id)
        .values(
            preparation_version=Tender.preparation_version + 1,
            content_revision=Tender.content_revision + 1,
            updated_at=datetime.now(UTC),
        )
        .execution_options(synchronize_session="fetch")
    )
    record_contribution(session=session, tender_id=tender.id, actor=actor)


def create_buyer(*, session: Session, actor: Actor, command: BuyerCreate) -> Buyer:
    _authorize(session, actor, write=True)
    buyer = Buyer(**command.model_dump())
    session.add(buyer)
    session.flush()
    write_audit(
        session=session,
        actor=actor,
        action="buyer.created",
        entity_id=buyer.id,
        metadata={"buyer_id": str(buyer.id)},
    )
    return buyer


def update_buyer(
    *,
    session: Session,
    actor: Actor,
    buyer_id: UUID,
    command: BuyerUpdate,
    expected: int | None,
) -> Buyer:
    _authorize(session, actor, write=True)
    buyer = session.get(Buyer, buyer_id, with_for_update=True, populate_existing=True)
    if buyer is None:
        raise ServiceError("buyer_not_found")
    require_version(actual=buyer.revision, expected=expected)
    for name, value in command.model_dump(exclude_unset=True).items():
        setattr(buyer, name, value)
    buyer.revision += 1
    buyer.updated_at = datetime.now(UTC)
    write_audit(
        session=session,
        actor=actor,
        action="buyer.updated",
        entity_id=buyer.id,
        metadata={"buyer_id": str(buyer.id)},
    )
    return buyer


def get_buyer(*, session: Session, actor: Actor, buyer_id: UUID) -> Buyer:
    _authorize(session, actor)
    buyer = session.get(Buyer, buyer_id)
    if buyer is None:
        raise ServiceError("buyer_not_found")
    return buyer


def create_tender(*, session: Session, actor: Actor, command: TenderCreate) -> Tender:
    _authorize(session, actor, write=True)
    buyer = session.get(Buyer, command.buyer_id, with_for_update=True)
    if buyer is None:
        raise ServiceError("buyer_not_found")
    if (
        command.supersedes_tender_id is not None
        and session.get(Tender, command.supersedes_tender_id) is None
    ):
        raise ServiceError("tender_not_found")
    tender = Tender(
        **command.model_dump(exclude={"pricing_customer_number"}),
        pricing_customer_number=command.pricing_customer_number
        or buyer.customer_number,
        created_by=actor.id,
    )
    session.add(tender)
    session.flush()
    record_contribution(session=session, tender_id=tender.id, actor=actor)
    metadata = {"buyer_id": str(buyer.id), "content_revision": tender.content_revision}
    if command.supersedes_tender_id is not None:
        metadata["supersedes_tender_id"] = str(command.supersedes_tender_id)
    write_audit(
        session=session,
        actor=actor,
        action="tender.created",
        entity_id=tender.id,
        metadata=metadata,
    )
    return tender


def idempotent_command(
    *,
    session: Session,
    actor: Actor,
    scope: str,
    key: str,
    request_hash: str,
    execute: Callable[[], UUID],
    lifetime_seconds: int = 86_400,
) -> UUID:
    if (
        not 1 <= len(key) <= 128
        or any(not 33 <= ord(c) <= 126 for c in key)
        or not 1 <= len(scope) <= 200
        or not re.fullmatch(r"[a-f0-9]{64}", request_hash)
        or lifetime_seconds <= 0
    ):
        raise ServiceError("invalid_request")
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    lock = hashlib.sha256(
        json.dumps([str(actor.id), scope, key_hash]).encode()
    ).digest()[:8]
    session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(lock, signed=True)},
    )
    identity = (actor.id, scope, key_hash)
    previous = session.get(IdempotencyKey, identity, populate_existing=True)
    if previous is not None:
        if previous.expires_at > datetime.now(UTC):
            if previous.request_hash != request_hash:
                raise ServiceError("idempotency_conflict")
            return previous.result_id
        session.delete(previous)
        session.flush()
    result_id = execute()
    session.add(
        IdempotencyKey(
            actor_id=actor.id,
            scope=scope,
            key_hash=key_hash,
            request_hash=request_hash,
            result_id=result_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=lifetime_seconds),
        )
    )
    session.flush()
    return result_id


def register_tender(
    *,
    session: Session,
    actor: Actor,
    command: TenderCreate,
    key: str | None = None,
    lifetime_seconds: int = 86_400,
) -> Tender:
    if key is None:
        return create_tender(session=session, actor=actor, command=command)
    _authorize(session, actor, write=True)

    def execute():
        return create_tender(session=session, actor=actor, command=command).id

    request_hash = hashlib.sha256(
        json.dumps(
            command.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    result_id = idempotent_command(
        session=session,
        actor=actor,
        scope="tenders.create",
        key=key,
        request_hash=request_hash,
        execute=execute,
        lifetime_seconds=lifetime_seconds,
    )
    return session.get(Tender, result_id)


def get_tender(*, session: Session, actor: Actor, tender_id: UUID) -> Tender:
    _authorize(session, actor)
    tender = session.get(Tender, tender_id)
    if tender is None:
        raise ServiceError("tender_not_found")
    return tender


def update_tender(
    *,
    session: Session,
    actor: Actor,
    tender_id: UUID,
    command: TenderUpdate,
    expected: int | None,
    expected_deadline_passed: bool | None = None,
) -> Tender:
    tender = lock_tender(
        session=session, actor=actor, tender_id=tender_id, expected=expected
    )
    if (
        expected_deadline_passed is not None
        and expected_deadline_passed != has_passed_deadline(tender)
    ):
        raise ServiceError("precondition_failed")
    for name, value in command.model_dump(exclude_unset=True).items():
        setattr(tender, name, value)
    touch_preparation(session=session, tender=tender, actor=actor)
    write_audit(
        session=session,
        actor=actor,
        action="tender.updated",
        entity_id=tender.id,
        metadata={
            "content_revision": tender.content_revision,
            "preparation_version": tender.preparation_version,
        },
    )
    return tender


def has_passed_deadline(tender: Tender) -> bool:
    return (
        tender.submission_deadline is not None
        and tender.submission_deadline <= datetime.now(UTC)
    )


def tender_view(tender: Tender) -> TenderView:
    result = TenderView.model_validate(tender)
    if result.submission_deadline is not None:
        result.submission_deadline = result.submission_deadline.astimezone(UTC)
    if has_passed_deadline(tender):
        result.warnings.append("submission_deadline_passed")
    return result


def _page(session, statement, model, after, limit):
    if after is not None:
        statement = statement.where(tuple_(model.created_at, model.id) > tuple_(*after))
    return list(
        session.scalars(statement.order_by(model.created_at, model.id).limit(limit + 1))
    )


def list_buyers(*, session: Session, actor: Actor, after, limit: int) -> list[Buyer]:
    _authorize(session, actor)
    return _page(session, select(Buyer), Buyer, after, limit)


def list_tenders(*, session: Session, actor: Actor, after, limit: int) -> list[Tender]:
    _authorize(session, actor)
    return _page(session, select(Tender), Tender, after, limit)


def list_tender_audit(
    *, session: Session, actor: Actor, tender_id: UUID, after, limit: int
) -> list[AuditEvent]:
    get_tender(session=session, actor=actor, tender_id=tender_id)
    return _page(
        session,
        select(AuditEvent).where(AuditEvent.entity_id == tender_id),
        AuditEvent,
        after,
        limit,
    )
