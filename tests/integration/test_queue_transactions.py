import pytest

from tenderflow.workers.app import enqueue_job


def test_rollback_does_not_leave_queued_work(queue_probe) -> None:
    with pytest.raises(RuntimeError, match="abort transaction"):
        with queue_probe.session() as session:
            queue_probe.add_marker(session, "rollback")
            enqueue_job(
                session=session,
                task_name="tests.complete",
                arguments={"key": "rollback"},
            )
            raise RuntimeError("abort transaction")

    assert queue_probe.marker_count() == 0
    assert queue_probe.job_count() == 0


def test_commit_publishes_marker_and_job_together(queue_probe) -> None:
    with queue_probe.session() as session:
        queue_probe.add_marker(session, "committed")
        job_id = enqueue_job(
            session=session,
            task_name="tests.complete",
            arguments={"key": "committed"},
        )
        assert isinstance(job_id, int)
        assert queue_probe.marker_count() == 0
        assert queue_probe.job_count() == 0

    assert queue_probe.marker_count() == 1
    assert queue_probe.job_count() == 1


def test_repeated_delivery_publishes_only_once(queue_probe) -> None:
    queue_probe.enqueue("duplicate", deliveries=2)
    with queue_probe.worker():
        queue_probe.wait_for_job_status("succeeded", count=2)

    assert queue_probe.completion_count() == 1
