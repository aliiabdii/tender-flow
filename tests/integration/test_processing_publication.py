from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text, update


def test_duplicate_completion_does_not_double_count(processing_items) -> None:
    from tenderflow.infrastructure.database.models import ProcessingItem

    run_id, items = processing_items.queued()
    result_id = uuid4()
    assert processing_items.complete(items[0], result_id=result_id) is True
    assert processing_items.complete(items[0], failed=True) is False
    assert processing_items.get(run_id).json()["completed_count"] == 1
    with processing_items.probe.session() as session:
        item = session.get(ProcessingItem, items[0])
        assert item.state == "succeeded"
        assert item.result_id == result_id
        assert item.error_code is None


def test_concurrent_completion_publishes_only_once(processing_items) -> None:
    run_id, items = processing_items.queued()
    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = list(
            pool.map(lambda _: processing_items.complete(items[0]), range(2))
        )
    assert sorted(accepted) == [False, True]
    assert processing_items.get(run_id).json()["succeeded_count"] == 1


def test_no_match_is_success_and_sibling_publication_does_not_stale_inputs(
    processing_items,
) -> None:
    from tenderflow.infrastructure.database.models import Tender

    run_id, items = processing_items.queued(count=2, kind="matching")
    assert processing_items.complete(items[0]) is True
    with processing_items.probe.session() as session:
        session.execute(
            update(Tender)
            .where(Tender.id == processing_items.tender_id)
            .values(content_revision=Tender.content_revision + 1)
        )
    assert processing_items.complete(items[1]) is True
    result = processing_items.get(run_id).json()
    assert result["status"] == "succeeded"
    assert result["succeeded_count"] == 2


def test_new_run_supersedes_old_work_but_duplicate_command_returns_original(
    processing_items,
) -> None:
    old, items = processing_items.queued(key="original")
    new, _ = processing_items.queued(key="replacement")
    replay, replay_items = processing_items.queued(key="original")
    assert replay == old and replay_items == items
    assert new != old
    assert processing_items.complete(items[0]) is False
    previous = processing_items.get(old).json()
    assert previous["status"] == "superseded"
    assert previous["superseded_by_run_id"] == str(new)
    assert previous["superseded_count"] == 1


def test_input_snapshot_must_match_item_and_current_tender(processing_items) -> None:
    from tenderflow.infrastructure.database.models import Tender
    from tenderflow.schemas.processing import InputVersions

    run_id, items = processing_items.queued()
    assert (
        processing_items.complete(items[0], expected=InputVersions(source_revision=99))
        is False
    )
    assert processing_items.get(run_id).json()["queued_count"] == 1
    with processing_items.probe.session() as session:
        session.execute(
            update(Tender)
            .where(Tender.id == processing_items.tender_id)
            .values(source_revision=Tender.source_revision + 1)
        )
    assert processing_items.complete(items[0]) is False
    assert processing_items.get(run_id).json()["status"] == "superseded"


def test_extraction_does_not_depend_on_unrelated_preparation_edits(
    processing_items,
) -> None:
    from tenderflow.infrastructure.database.models import Tender

    run_id, items = processing_items.queued()
    with processing_items.probe.session() as session:
        session.execute(
            update(Tender)
            .where(Tender.id == processing_items.tender_id)
            .values(preparation_version=Tender.preparation_version + 1)
        )
    assert processing_items.complete(items[0]) is True
    assert processing_items.get(run_id).json()["status"] == "succeeded"


def test_queue_payload_contains_only_item_identity_and_rolls_back_with_run(
    processing_items,
) -> None:
    from tenderflow.infrastructure.database.models import ProcessingRun
    from tenderflow.schemas.processing import RunKind
    from tenderflow.services.processing import enqueue_item, start_run

    run_id, items = processing_items.queued()
    with processing_items.probe.session() as session:
        jobs = (
            session.execute(
                text(
                    "SELECT args FROM procrastinate_jobs WHERE task_name='tests.process_item'"
                )
            )
            .scalars()
            .all()
        )
    assert jobs == [{"item_id": str(items[0])}]
    rolled_back = None
    with pytest.raises(RuntimeError):
        with processing_items.probe.session() as session:
            run = start_run(
                session=session,
                tender_id=processing_items.tender_id,
                actor=processing_items.actor,
                kind=RunKind.EXTRACTION,
                key="rollback",
            )
            rolled_back = run.id
            enqueue_item(
                session=session,
                run_id=run.id,
                key="run",
                task_name="tests.process_item",
            )
            raise RuntimeError("Rollback publication")
    with processing_items.probe.session() as session:
        assert session.get(ProcessingRun, rolled_back) is None
        assert session.get(ProcessingRun, run_id).is_current is True
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM procrastinate_jobs WHERE task_name='tests.process_item'"
                )
            )
            == 1
        )


def test_repeated_item_deferral_cannot_create_duplicate_jobs(processing_items) -> None:
    from tenderflow.services.errors import ServiceError
    from tenderflow.services.processing import enqueue_item

    run_id, items = processing_items.queued()
    with processing_items.probe.session() as session:
        assert (
            enqueue_item(
                session=session, run_id=run_id, key="0", task_name="tests.process_item"
            ).id
            == items[0]
        )
    with pytest.raises(ServiceError, match="processing_conflict"):
        with processing_items.probe.session() as session:
            enqueue_item(
                session=session, run_id=run_id, key="0", task_name="tests.different"
            )
    with processing_items.probe.session() as session:
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM procrastinate_jobs WHERE task_name='tests.process_item'"
                )
            )
            == 1
        )


def test_retry_has_fresh_item_ids_and_late_original_completion_cannot_publish(
    processing_items,
) -> None:
    run_id, items = processing_items.queued()
    expected = processing_items.inputs(items[0])
    processing_items.complete(items[0], failed=True)
    retried = processing_items.retry(run_id).json()
    [(new_id, state, old_id)] = processing_items.items(UUID(retried["id"]))
    assert new_id != items[0] and old_id == items[0] and state == "queued"
    assert processing_items.complete(items[0], expected=expected) is False
    assert processing_items.complete(new_id) is True
    assert processing_items.get(retried["id"]).json()["status"] == "succeeded"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "failed", "error_code": "raw secret exception"},
        {"status": "failed"},
        {"status": "succeeded", "error_code": "worker_failed"},
        {"status": "succeeded", "raw_body": "secret"},
    ],
)
def test_item_results_reject_raw_errors_and_inconsistent_outcomes(payload) -> None:
    from tenderflow.schemas.processing import ItemResult

    with pytest.raises(ValidationError):
        ItemResult.model_validate(payload)


def test_start_rechecks_database_permissions_despite_stale_actor(
    processing_items,
) -> None:
    from tenderflow.schemas.processing import RunKind
    from tenderflow.services.errors import ServiceError
    from tenderflow.services.processing import start_run

    accounts = processing_items.api.accounts
    processing_items.queued(key="before-disable")
    accounts.update(accounts.manager_id, active=False)
    with pytest.raises(ServiceError, match="forbidden"):
        with processing_items.probe.session() as session:
            start_run(
                session=session,
                tender_id=processing_items.tender_id,
                kind=RunKind.EXTRACTION,
                actor=processing_items.actor,
                key="before-disable",
            )


def test_actual_worker_publishes_persisted_processing_results(processing_items) -> None:
    run_id, _ = processing_items.queued(count=2)
    with processing_items.probe.worker():
        processing_items.probe.wait_for(
            lambda: processing_items.get(run_id).json()["status"] == "succeeded"
        )
    progress = processing_items.get(run_id).json()
    assert progress["completed_count"] == 2
    assert progress["succeeded_count"] == 2


def test_actual_exhausted_worker_retries_surface_as_safe_failures(
    processing_items,
) -> None:
    from tenderflow.schemas.processing import RunKind
    from tenderflow.services.processing import enqueue_item, start_run

    with processing_items.probe.session() as session:
        run = start_run(
            session=session,
            tender_id=processing_items.tender_id,
            kind=RunKind.MATCHING,
            actor=processing_items.actor,
            key="exhausted",
        )
        run_id = run.id
        enqueue_item(
            session=session,
            run_id=run_id,
            key="failure",
            task_name="tests.process_item_exhausted",
        )
    with processing_items.probe.worker():
        processing_items.probe.wait_for(
            lambda: processing_items.get(run_id).json()["status"] == "failed"
        )
    assert (
        processing_items.probe.scalar(
            "SELECT attempts FROM procrastinate_jobs "
            "WHERE task_name='tests.process_item_exhausted'"
        )
        == 4
    )
    response = processing_items.api.client.get(
        f"/api/v1/processing-runs/{run_id}/items", headers=processing_items.api.headers
    )
    assert response.json()["items"][0]["error_code"] == "worker_failed"
    assert "Synthetic processing failure" not in response.text


def test_crash_after_publication_does_not_repeat_or_lose_completion(
    processing_items,
) -> None:
    from tenderflow.schemas.processing import RunKind
    from tenderflow.services.processing import enqueue_item, start_run

    with processing_items.probe.session() as session:
        run = start_run(
            session=session,
            tender_id=processing_items.tender_id,
            kind=RunKind.EXTRACTION,
            actor=processing_items.actor,
            key="crash-after-publish",
        )
        run_id = run.id
        enqueue_item(
            session=session,
            run_id=run_id,
            key="crash",
            task_name="tests.process_item_pause",
        )
    with processing_items.probe.worker() as worker:
        processing_items.probe.wait_for(
            lambda: processing_items.get(run_id).json()["status"] == "succeeded"
        )
        worker.kill()
        worker.wait(timeout=10)
    processing_items.probe.wait_for_stale_heartbeat()
    with processing_items.probe.worker():
        processing_items.probe.wait_for(
            lambda: (
                processing_items.probe.scalar(
                    "SELECT count(*) FROM procrastinate_jobs "
                    "WHERE task_name='tests.process_item_pause' AND status='succeeded'"
                )
                == 1
            )
        )
    progress = processing_items.get(run_id).json()
    assert progress["completed_count"] == progress["succeeded_count"] == 1


def test_same_key_conflicting_command_does_not_replace_original_run(
    processing_items,
) -> None:
    from tenderflow.schemas.processing import RunKind
    from tenderflow.services.errors import ServiceError
    from tenderflow.services.processing import start_run

    with processing_items.probe.session() as session:
        first = start_run(
            session=session,
            tender_id=processing_items.tender_id,
            kind=RunKind.EXTRACTION,
            actor=processing_items.actor,
            key="payload-conflict",
            request_hash="a" * 64,
        )
        first_id = first.id
    with pytest.raises(ServiceError, match="idempotency_conflict"):
        with processing_items.probe.session() as session:
            start_run(
                session=session,
                tender_id=processing_items.tender_id,
                kind=RunKind.EXTRACTION,
                actor=processing_items.actor,
                key="payload-conflict",
                request_hash="b" * 64,
            )
    assert processing_items.get(first_id).json()["is_current"] is True
