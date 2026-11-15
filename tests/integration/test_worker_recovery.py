from procrastinate.contrib.sqlalchemy import SQLAlchemyPsycopg2Connector
from sqlalchemy import text

from tenderflow.workers.app import recover_stalled_jobs


def test_killed_worker_is_recovered_without_duplicate_publication(queue_probe) -> None:
    queue_probe.enqueue("crash", pause_after_publication=True)
    with queue_probe.worker() as worker:
        queue_probe.wait_for_completion()
        queue_probe.wait_for_job_status("doing")
        worker.kill()
        worker.wait(timeout=10)

    queue_probe.wait_for_stale_heartbeat()
    with queue_probe.worker():
        queue_probe.wait_for_job_status("succeeded")

    assert queue_probe.completion_count() == 1
    assert queue_probe.job_attempts() == 2


def test_live_worker_is_not_recovered_by_job_age(queue_probe) -> None:
    queue_probe.enqueue("alive", pause_after_publication=True)
    with queue_probe.worker():
        queue_probe.wait_for_completion()
        queue_probe.wait_for(
            lambda: queue_probe.scalar(
                "SELECT EXISTS(SELECT 1 FROM procrastinate_events e "
                "JOIN procrastinate_jobs j ON j.id=e.job_id "
                "WHERE j.task_name='tests.complete' AND e.type='started' "
                "AND e.at < now() - interval '2 seconds')"
            )
        )
        assert recover_stalled_jobs(app=queue_probe.app) == 0
        assert queue_probe.job_status() == "doing"


def test_transient_failures_have_bounded_retries(queue_probe) -> None:
    queue_probe.enqueue("timeout", fail=True)
    with queue_probe.worker():
        queue_probe.wait_for_job_status("failed")

    assert queue_probe.job_attempts() == 4
    assert queue_probe.completion_count() == 0


def test_repeated_worker_crashes_exhaust_the_retry_budget(queue_probe) -> None:
    queue_probe.enqueue("last-crash", pause_after_publication=True)
    with queue_probe.session() as session:
        session.execute(
            text(
                "UPDATE procrastinate_jobs SET attempts=3 "
                "WHERE task_name='tests.complete'"
            )
        )
    with queue_probe.worker() as worker:
        queue_probe.wait_for_completion()
        worker.kill()
        worker.wait(timeout=10)

    queue_probe.wait_for_stale_heartbeat()
    with queue_probe.worker():
        queue_probe.wait_for_job_status("failed")

    assert queue_probe.job_attempts() == 4
    assert queue_probe.completion_count() == 1


def test_restart_recovers_maintenance_with_a_queued_successor(queue_probe) -> None:
    task_name = "tenderflow.recover_stalled"
    with queue_probe.app.replace_connector(SQLAlchemyPsycopg2Connector()):
        with queue_probe.session() as session:
            first = queue_probe.app.configure_task(
                task_name, connection=session.connection()
            ).defer(timestamp=0)
            session.execute(
                text("UPDATE procrastinate_jobs SET status='doing' WHERE id=:id"),
                {"id": first},
            )
            second = queue_probe.app.configure_task(
                task_name, connection=session.connection()
            ).defer(timestamp=1)

    with queue_probe.worker():
        queue_probe.wait_for(
            lambda: (
                queue_probe.scalar(
                    f"SELECT count(*) FROM procrastinate_jobs "
                    f"WHERE id IN ({first}, {second}) AND status='succeeded'"
                )
                == 2
            )
        )
