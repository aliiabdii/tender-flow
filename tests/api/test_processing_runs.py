from uuid import uuid4

import pytest


def test_processing_status_requires_authentication(local_accounts) -> None:
    assert (
        local_accounts.client.get(f"/api/v1/processing-runs/{uuid4()}").status_code
        == 401
    )


def test_missing_run_returns_safe_not_found(local_accounts) -> None:
    response = local_accounts.client.get(
        f"/api/v1/processing-runs/{uuid4()}",
        headers=local_accounts.headers(local_accounts.manager_pair),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "processing_run_not_found"


def test_partial_progress_is_persisted_and_item_details_are_paginated(
    processing_items,
) -> None:
    run_id, items = processing_items.queued(count=3)
    processing_items.complete(items[0], result_id=uuid4())
    processing_items.complete(items[1], failed=True)
    response = processing_items.get(run_id)
    assert response.status_code == 200
    progress = response.json()
    assert progress["status"] == "running"
    assert (
        progress["total_count"],
        progress["completed_count"],
        progress["succeeded_count"],
        progress["failed_count"],
        progress["queued_count"],
    ) == (3, 2, 1, 1, 1)
    assert progress["warnings"] == ["some_items_failed"]
    path = progress["items_url"]
    first = processing_items.api.client.get(
        path, headers=processing_items.api.headers, params={"limit": 2}
    ).json()
    second = processing_items.api.client.get(
        path,
        headers=processing_items.api.headers,
        params={"cursor": first["next_cursor"]},
    ).json()
    all_items = first["items"] + second["items"]
    assert len({item["id"] for item in all_items}) == 3
    assert second["next_cursor"] is None
    failed = next(item for item in all_items if item["state"] == "failed")
    assert failed["error_code"] == "upstream_unavailable"
    assert "job_id" not in failed and "task_name" not in failed
    processing_items.complete(items[2])
    finished = processing_items.get(run_id).json()
    assert finished["status"] == "partial_failure"
    assert finished["completed_count"] == 3
    assert finished["finished_at"] is not None


def test_retry_creates_one_new_run_with_only_failures_and_preserves_history(
    processing_items,
) -> None:
    run_id, items = processing_items.queued(count=3)
    processing_items.complete(items[0])
    for item in items[1:]:
        processing_items.complete(item, failed=True)
    first = processing_items.retry(run_id)
    assert first.status_code == 202
    retry = first.json()
    assert retry["id"] != str(run_id)
    assert retry["retry_of_run_id"] == str(run_id)
    assert retry["status"] == "queued"
    assert retry["total_count"] == 2
    assert first.headers["Location"] == f"/api/v1/processing-runs/{retry['id']}"
    assert first.headers["Cache-Control"] == "no-store"
    repeated = processing_items.retry(run_id)
    assert repeated.status_code == 202
    assert repeated.json()["id"] == retry["id"]
    assert {row[2] for row in processing_items.items(retry["id"])} == set(items[1:])
    assert sorted(row[1] for row in processing_items.items(run_id)) == [
        "failed",
        "failed",
        "succeeded",
    ]
    assert processing_items.get(run_id).json()["failed_count"] == 2
    assert processing_items.retry(run_id, key="different-retry").status_code == 409


def test_retry_requires_a_key_and_rejects_arbitrary_request_fields(
    processing_items,
) -> None:
    run_id, items = processing_items.queued()
    processing_items.complete(items[0], failed=True)
    path = f"/api/v1/processing-runs/{run_id}/retries"
    assert (
        processing_items.api.client.post(
            path, headers=processing_items.api.headers
        ).status_code
        == 422
    )
    assert (
        processing_items.retry(
            run_id, json={"task_name": "not-allowed", "raw_error": "secret"}
        ).status_code
        == 422
    )
    assert processing_items.retry(run_id, key="bad key").status_code == 422


def test_approvers_read_but_do_not_retry_and_admin_alone_has_no_business_access(
    processing_items,
) -> None:
    accounts = processing_items.api.accounts
    run_id, items = processing_items.queued()
    processing_items.complete(items[0], failed=True)
    user = accounts.provision("progress-approver", roles=["approver"])
    accounts.activate(user["activation"]["token"])
    headers = accounts.headers(accounts.login("progress-approver"))
    path = f"/api/v1/processing-runs/{run_id}"
    assert accounts.client.get(path, headers=headers).status_code == 200
    assert accounts.client.get(path + "/items", headers=headers).status_code == 200
    assert (
        accounts.client.post(
            path + "/retries", headers={**headers, "Idempotency-Key": "reviewer-retry"}
        ).status_code
        == 403
    )
    assert accounts.client.get(path, headers=accounts.admin_headers).status_code == 403
    accounts.update(accounts.manager_id, roles=["approver"])
    assert processing_items.retry(run_id).status_code == 403


def test_only_failed_current_finished_work_can_be_retried(processing_items) -> None:
    from sqlalchemy import update

    from tenderflow.infrastructure.database.models import Tender

    run_id, items = processing_items.queued(count=2)
    processing_items.complete(items[0], failed=True)
    assert processing_items.retry(run_id).status_code == 409
    processing_items.complete(items[1], failed=True)
    with processing_items.probe.session() as session:
        session.execute(
            update(Tender)
            .where(Tender.id == processing_items.tender_id)
            .values(source_revision=Tender.source_revision + 1)
        )
    assert processing_items.retry(run_id).status_code == 409


def test_exhausted_queue_jobs_become_safe_item_failures(processing_items) -> None:
    from sqlalchemy import text

    run_id, _ = processing_items.queued()
    with processing_items.probe.session() as session:
        session.execute(
            text(
                "UPDATE procrastinate_jobs SET status='failed' "
                "WHERE task_name='tests.process_item'"
            )
        )
    progress = processing_items.get(run_id).json()
    assert progress["status"] == "failed"
    assert progress["failed_count"] == 1
    details = processing_items.api.client.get(
        progress["items_url"], headers=processing_items.api.headers
    ).json()
    assert details["items"][0]["error_code"] == "worker_failed"
    assert processing_items.retry(run_id).status_code == 202


@pytest.mark.parametrize("terminal", ["cancelled", "aborted", "deleted"])
def test_terminal_queue_operations_do_not_strand_or_erase_published_work(
    processing_items, terminal
) -> None:
    from sqlalchemy import select, text

    from tenderflow.infrastructure.database.models import ProcessingItem

    run_id, items = processing_items.queued(count=2)
    processing_items.complete(items[0])
    with processing_items.probe.session() as session:
        jobs = list(
            session.scalars(
                select(ProcessingItem.job_id).where(ProcessingItem.run_id == run_id)
            )
        )
    if terminal == "aborted":
        with processing_items.probe.session() as session:
            session.execute(
                text(
                    "UPDATE procrastinate_jobs SET status='aborted' WHERE id = ANY(:ids)"
                ),
                {"ids": jobs},
            )
    else:
        with processing_items.probe.app.open() as app:
            for job_id in jobs:
                assert app.job_manager.cancel_job_by_id(
                    job_id, delete_job=terminal == "deleted"
                )
    progress = processing_items.get(run_id).json()
    assert progress["status"] == "partial_failure"
    assert progress["succeeded_count"] == progress["failed_count"] == 1
    states = {row[0]: row[1] for row in processing_items.items(run_id)}
    assert states[items[0]] == "succeeded"
    assert states[items[1]] == "failed"
    assert processing_items.retry(run_id).status_code == 202
