import asyncio

import procrastinate
from procrastinate.contrib.sqlalchemy import SQLAlchemyPsycopg2Connector
from procrastinate.jobs import Status
from pydantic import JsonValue
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from tenderflow.config import Settings

_enqueue_app = procrastinate.App(connector=SQLAlchemyPsycopg2Connector())
_RECOVERY_LOCK = 7_132_408_611


def enqueue_job(
    *, session: Session, task_name: str, arguments: dict[str, JsonValue]
) -> int:
    return _enqueue_app.configure_task(
        task_name, connection=session.connection()
    ).defer(**arguments)


async def _recover_stalled_jobs(app: procrastinate.App, settings: Settings) -> int:
    recovered = 0
    # Serialize sweepers without holding job locks across library-owned transactions.
    async with app.connector.pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT pg_try_advisory_xact_lock(%s)", (_RECOVERY_LOCK,)
        )
        if not (await cursor.fetchone())[0]:
            return 0
        jobs = await app.job_manager.get_stalled_jobs(
            seconds_since_heartbeat=settings.worker_stalled_seconds
        )
        for job in jobs:
            if job.attempts >= settings.job_max_retries:
                await app.job_manager.finish_job(
                    job, status=Status.FAILED, delete_job=False
                )
            else:
                await app.job_manager.retry_job(job)
                recovered += 1
    return recovered


def create_worker_app(*, settings: Settings | None = None) -> procrastinate.App:
    from tenderflow.workers.tasks import retry_strategy

    settings = settings if settings is not None else Settings()
    if settings.database_url is None:
        raise RuntimeError("TENDERFLOW_DATABASE_URL is required for the worker")
    url = make_url(settings.database_url.get_secret_value()).set(
        drivername="postgresql"
    )
    app = procrastinate.App(
        connector=procrastinate.PsycopgConnector(
            conninfo=url.render_as_string(hide_password=False),
            min_size=1,
            max_size=settings.worker_concurrency + 2,
            kwargs={"connect_timeout": 3},
        ),
        worker_defaults={
            "concurrency": settings.worker_concurrency,
            "update_heartbeat_interval": settings.worker_heartbeat_seconds,
            "stalled_worker_timeout": settings.worker_stalled_seconds,
            "shutdown_graceful_timeout": 10,
        },
    )

    @app.periodic(cron="* * * * *")
    @app.task(
        name="tenderflow.recover_stalled",
        retry=retry_strategy(settings=settings),
    )
    async def recover(timestamp: int) -> None:
        await _recover_stalled_jobs(app, settings)

    app.tenderflow_settings = settings
    return app


def recover_stalled_jobs(*, app: procrastinate.App | None = None) -> int:
    app = app if app is not None else create_worker_app()

    async def recover() -> int:
        async with app.open_async():
            return await _recover_stalled_jobs(app, app.tenderflow_settings)

    return asyncio.run(recover())


async def run_worker(app: procrastinate.App) -> None:
    async with app.open_async():
        await _recover_stalled_jobs(app, app.tenderflow_settings)
        await app.run_worker_async()


def main() -> None:
    import sys

    app = create_worker_app()
    if sys.argv[1:] == ["init-schema"]:
        with app.open():
            app.schema_manager.apply_schema()
    elif sys.argv[1:]:
        raise SystemExit("Usage: python -m tenderflow.workers.app [init-schema]")
    else:
        asyncio.run(run_worker(app))


if __name__ == "__main__":
    main()
