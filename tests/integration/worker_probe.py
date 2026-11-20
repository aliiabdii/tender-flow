import asyncio
from uuid import UUID

from sqlalchemy import text

from tenderflow.config import Settings
from tenderflow.infrastructure.database.sessions import session_scope
from tenderflow.schemas.processing import ItemResult
from tenderflow.services.processing import mark_item_running, publish_item
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


def publish_processing_item(item_id: str) -> bool:
    with session_scope() as session:
        inputs = mark_item_running(session=session, item_id=UUID(item_id))
    if inputs is None:
        return False
    with session_scope() as session:
        return publish_item(
            session=session,
            item_id=UUID(item_id),
            expected_inputs=inputs,
            result=ItemResult(status="succeeded"),
        )


@app.task(name="tests.process_item", retry=retry_strategy(settings=settings))
async def process_item(item_id: str) -> None:
    publish_processing_item(item_id)


@app.task(name="tests.process_item_exhausted", retry=retry_strategy(settings=settings))
async def process_item_exhausted(item_id: str) -> None:
    raise TimeoutError("Synthetic processing failure")


@app.task(name="tests.process_item_pause", retry=retry_strategy(settings=settings))
async def process_item_pause(item_id: str) -> None:
    if publish_processing_item(item_id):
        await asyncio.sleep(600)


if __name__ == "__main__":
    asyncio.run(run_worker(app))
