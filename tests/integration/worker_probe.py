import asyncio

from sqlalchemy import text

from tenderflow.config import Settings
from tenderflow.infrastructure.database.sessions import session_scope
from tenderflow.workers.app import create_worker_app, run_worker
from tenderflow.workers.tasks import retry_strategy

settings = Settings()
app = create_worker_app(settings=settings)
app.worker_defaults["shutdown_graceful_timeout"] = 0.2
app.worker_defaults["fetch_job_polling_interval"] = 0.1


@app.task(name="tests.complete", retry=retry_strategy(settings=settings))
async def complete(
    key: str, pause_after_publication: bool = False, fail: bool = False
) -> None:
    if fail:
        raise TimeoutError("Synthetic transient failure")
    with session_scope() as session:
        published = session.scalar(
            text(
                "INSERT INTO test_completions(key) VALUES (:key) "
                "ON CONFLICT DO NOTHING RETURNING key"
            ),
            {"key": key},
        )
    if pause_after_publication and published:
        await asyncio.sleep(600)


if __name__ == "__main__":
    asyncio.run(run_worker(app))
