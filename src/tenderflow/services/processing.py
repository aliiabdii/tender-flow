import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from tenderflow.infrastructure.database.models import (
    ProcessingItem,
    ProcessingRun,
    Tender,
)
from tenderflow.schemas.identity import Actor
from tenderflow.schemas.processing import InputVersions, ItemResult, RunKind, RunView
from tenderflow.services.audit import write_audit
from tenderflow.services.errors import ServiceError
from tenderflow.services.tenders import (
    _authorize,
    _page,
    idempotent_command,
    record_contribution,
)
from tenderflow.workers.app import enqueue_job

_ACTIVE = ("queued", "running")


def _lock_run(session: Session, run_id: UUID) -> tuple[ProcessingRun, Tender]:
    tender_id = session.scalar(
        select(ProcessingRun.tender_id).where(ProcessingRun.id == run_id)
    )
    if tender_id is None:
        raise ServiceError("processing_run_not_found")
    tender = session.get(
        Tender, tender_id, with_for_update=True, populate_existing=True
    )
    run = session.get(
        ProcessingRun, run_id, with_for_update=True, populate_existing=True
    )
    return run, tender


def _counts(session: Session, run_id: UUID) -> dict[str, int]:
    session.flush()
    return dict(
        session.execute(
            select(ProcessingItem.state, func.count())
            .where(ProcessingItem.run_id == run_id)
            .group_by(ProcessingItem.state)
        ).all()
    )


def _refresh(session: Session, run: ProcessingRun) -> dict[str, int]:
    counts = _counts(session, run.id)
    pending = counts.get("queued", 0) + counts.get("running", 0)
    if not run.is_current or counts.get("superseded", 0):
        status = "superseded"
    elif pending:
        status = (
            "queued" if counts.get("queued", 0) == sum(counts.values()) else "running"
        )
    elif not counts:
        status = "queued"
    elif counts.get("failed", 0):
        status = "partial_failure" if counts.get("succeeded", 0) else "failed"
    else:
        status = "succeeded"
    run.status = status
    if status not in _ACTIVE and run.finished_at is None:
        run.finished_at = datetime.now(UTC)
    return counts


def _inputs(tender: Tender, kind: str) -> InputVersions:
    return InputVersions(
        source_revision=tender.source_revision,
        preparation_version=None
        if kind == RunKind.EXTRACTION
        else tender.preparation_version,
    )


def _current(tender: Tender, inputs: InputVersions) -> bool:
    return tender.source_revision == inputs.source_revision and (
        inputs.preparation_version is None
        or tender.preparation_version == inputs.preparation_version
    )


def _supersede_pending(session: Session, run: ProcessingRun) -> None:
    session.execute(
        update(ProcessingItem)
        .where(ProcessingItem.run_id == run.id, ProcessingItem.state.in_(_ACTIVE))
        .values(state="superseded", finished_at=datetime.now(UTC))
    )


def _new_run(
    session: Session,
    tender: Tender,
    kind: RunKind,
    actor: Actor,
    retry_of: UUID | None = None,
) -> ProcessingRun:
    previous = session.scalar(
        select(ProcessingRun)
        .where(
            ProcessingRun.tender_id == tender.id,
            ProcessingRun.kind == kind,
            ProcessingRun.is_current.is_(True),
        )
        .with_for_update()
    )
    if previous is not None:
        previous.is_current = False
        _supersede_pending(session, previous)
        _refresh(session, previous)
        session.flush()
    run = ProcessingRun(
        tender_id=tender.id, kind=kind, actor_id=actor.id, retry_of_run_id=retry_of
    )
    session.add(run)
    session.flush()
    if previous is not None:
        previous.superseded_by_run_id = run.id
    record_contribution(session=session, tender_id=tender.id, actor=actor)
    write_audit(
        session=session,
        actor=actor,
        action="processing.started",
        entity_id=tender.id,
        metadata={"run_id": str(run.id)},
    )
    return run


def start_run(
    *,
    session: Session,
    tender_id: UUID,
    kind: RunKind,
    actor: Actor,
    key: str,
    request_hash: str | None = None,
    lifetime_seconds: int = 86_400,
) -> ProcessingRun:
    _authorize(session, actor, write=True)
    kind = RunKind(kind)
    tender = session.get(
        Tender, tender_id, with_for_update=True, populate_existing=True
    )
    if tender is None:
        raise ServiceError("tender_not_found")
    request_hash = (
        request_hash
        or hashlib.sha256(json.dumps([str(tender_id), kind]).encode()).hexdigest()
    )
    result_id = idempotent_command(
        session=session,
        actor=actor,
        scope=f"processing.start:{tender_id}:{kind}",
        key=key,
        request_hash=request_hash,
        lifetime_seconds=lifetime_seconds,
        execute=lambda: _new_run(session, tender, kind, actor).id,
    )
    return session.get(ProcessingRun, result_id)


def enqueue_item(
    *,
    session: Session,
    run_id: UUID,
    key: str,
    task_name: str,
    expected_inputs: InputVersions | None = None,
    retry_of_item_id: UUID | None = None,
) -> ProcessingItem:
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", key) or not re.fullmatch(
        r"[a-zA-Z][a-zA-Z0-9_.]{0,127}", task_name
    ):
        raise ServiceError("invalid_request")
    run, tender = _lock_run(session, run_id)
    existing = session.scalar(
        select(ProcessingItem).where(
            ProcessingItem.run_id == run.id, ProcessingItem.key == key
        )
    )
    if existing is not None:
        if existing.task_name != task_name or (
            expected_inputs is not None
            and existing.expected_inputs != expected_inputs.model_dump()
        ):
            raise ServiceError("processing_conflict")
        return existing
    inputs = expected_inputs or _inputs(tender, run.kind)
    if not run.is_current or not _current(tender, inputs):
        raise ServiceError("processing_conflict")
    item = ProcessingItem(
        run_id=run.id,
        key=key,
        task_name=task_name,
        expected_inputs=inputs.model_dump(),
        retry_of_item_id=retry_of_item_id,
    )
    session.add(item)
    session.flush()
    item.job_id = enqueue_job(
        session=session, task_name=task_name, arguments={"item_id": str(item.id)}
    )
    _refresh(session, run)
    return item


def _reconcile(session: Session, run: ProcessingRun, tender: Tender) -> None:
    if not run.is_current:
        return
    active = list(
        session.scalars(
            select(ProcessingItem)
            .where(ProcessingItem.run_id == run.id, ProcessingItem.state.in_(_ACTIVE))
            .order_by(ProcessingItem.id)
            .with_for_update()
        )
    )
    job_ids = [item.job_id for item in active if item.job_id is not None]
    states = (
        dict(
            session.execute(
                text(
                    "SELECT id, status::text FROM procrastinate_jobs WHERE id = ANY(:ids)"
                ),
                {"ids": job_ids},
            ).all()
        )
        if job_ids
        else {}
    )
    for item in active:
        if not _current(tender, InputVersions.model_validate(item.expected_inputs)):
            item.state = "superseded"
            item.finished_at = datetime.now(UTC)
        elif states.get(item.job_id) in {
            "failed",
            "succeeded",
            "cancelled",
            "aborted",
        } or (item.job_id is not None and item.job_id not in states):
            item.state = "failed"
            item.error_code = "worker_failed"
            item.finished_at = datetime.now(UTC)
        elif states.get(item.job_id) == "doing":
            item.state = "running"
    _refresh(session, run)


def mark_item_running(*, session: Session, item_id: UUID) -> InputVersions | None:
    run_id = session.scalar(
        select(ProcessingItem.run_id).where(ProcessingItem.id == item_id)
    )
    if run_id is None:
        return None
    run, tender = _lock_run(session, run_id)
    item = session.get(
        ProcessingItem, item_id, with_for_update=True, populate_existing=True
    )
    inputs = InputVersions.model_validate(item.expected_inputs)
    if item.state not in _ACTIVE:
        return None
    if not run.is_current or not _current(tender, inputs):
        item.state = "superseded"
        item.finished_at = datetime.now(UTC)
        _refresh(session, run)
        return None
    item.state = "running"
    _refresh(session, run)
    return inputs


def publish_item(
    *,
    session: Session,
    item_id: UUID,
    expected_inputs: InputVersions,
    result: ItemResult,
) -> bool:
    run_id = session.scalar(
        select(ProcessingItem.run_id).where(ProcessingItem.id == item_id)
    )
    if run_id is None:
        return False
    run, tender = _lock_run(session, run_id)
    item = session.get(
        ProcessingItem, item_id, with_for_update=True, populate_existing=True
    )
    if (
        item.state not in _ACTIVE
        or item.expected_inputs != expected_inputs.model_dump()
    ):
        return False
    if not run.is_current or not _current(tender, expected_inputs):
        item.state = "superseded"
        item.finished_at = datetime.now(UTC)
        _refresh(session, run)
        return False
    item.state = result.status
    item.result_id = result.result_id
    item.error_code = result.error_code
    item.finished_at = datetime.now(UTC)
    _refresh(session, run)
    return True


def retry_failed_items(
    *,
    session: Session,
    run_id: UUID,
    actor: Actor,
    key: str,
    lifetime_seconds: int = 86_400,
) -> ProcessingRun:
    _authorize(session, actor, write=True)
    run, tender = _lock_run(session, run_id)

    def execute() -> UUID:
        _reconcile(session, run, tender)
        counts = _counts(session, run.id)
        if not run.is_current or any(counts.get(state) for state in _ACTIVE):
            raise ServiceError("processing_conflict")
        failed = [
            item
            for item in session.scalars(
                select(ProcessingItem)
                .where(
                    ProcessingItem.run_id == run.id, ProcessingItem.state == "failed"
                )
                .order_by(ProcessingItem.id)
                .with_for_update()
            )
            if _current(tender, InputVersions.model_validate(item.expected_inputs))
        ]
        if not failed:
            raise ServiceError("processing_conflict")
        retry = _new_run(session, tender, RunKind(run.kind), actor, retry_of=run.id)
        for item in failed:
            enqueue_item(
                session=session,
                run_id=retry.id,
                key=item.key,
                task_name=item.task_name,
                expected_inputs=InputVersions.model_validate(item.expected_inputs),
                retry_of_item_id=item.id,
            )
        return retry.id

    result_id = idempotent_command(
        session=session,
        actor=actor,
        scope=f"processing.retry:{run.id}",
        key=key,
        request_hash=hashlib.sha256(str(run.id).encode()).hexdigest(),
        lifetime_seconds=lifetime_seconds,
        execute=execute,
    )
    return session.get(ProcessingRun, result_id)


def get_run(*, session: Session, run_id: UUID, actor: Actor) -> ProcessingRun:
    _authorize(session, actor)
    run, tender = _lock_run(session, run_id)
    _reconcile(session, run, tender)
    return run


def run_view(*, session: Session, run: ProcessingRun) -> RunView:
    counts = _refresh(session, run)
    return RunView(
        id=run.id,
        tender_id=run.tender_id,
        kind=run.kind,
        status=run.status,
        is_current=run.is_current,
        retry_of_run_id=run.retry_of_run_id,
        superseded_by_run_id=run.superseded_by_run_id,
        total_count=sum(counts.values()),
        completed_count=counts.get("succeeded", 0) + counts.get("failed", 0),
        **{
            f"{state}_count": counts.get(state, 0)
            for state in ("queued", "running", "succeeded", "failed", "superseded")
        },
        warnings=(["some_items_failed"] if counts.get("failed") else [])
        + (["inputs_superseded"] if counts.get("superseded") else []),
        items_url=f"/api/v1/processing-runs/{run.id}/items",
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def list_items(
    *,
    session: Session,
    run_id: UUID,
    actor: Actor,
    after,
    limit: int,
) -> list[ProcessingItem]:
    get_run(session=session, run_id=run_id, actor=actor)
    return _page(
        session,
        select(ProcessingItem).where(ProcessingItem.run_id == run_id),
        ProcessingItem,
        after,
        limit,
    )
