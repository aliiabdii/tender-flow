from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tenderflow.config import Settings
from tenderflow.infrastructure.database.base import APPLICATION_SCHEMA_REVISION


def create_database_engine(settings: Settings) -> Engine:
    if settings.database_url is None:
        raise RuntimeError("TENDERFLOW_DATABASE_URL is required")
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=5,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )


@lru_cache
def get_engine() -> Engine:
    return create_database_engine(Settings())


@contextmanager
def session_scope(*, engine: Engine | None = None) -> Iterator[Session]:
    with Session(engine if engine is not None else get_engine()) as session:
        with session.begin():
            yield session


def database_is_ready(engine: Engine | None) -> bool:
    if engine is None:
        return False
    try:
        with engine.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            connection.execute(
                text("SELECT id, status, worker_id FROM procrastinate_jobs LIMIT 0")
            )
            queue_ready = connection.scalar(
                text(
                    "SELECT to_regprocedure("
                    "'procrastinate_defer_jobs_v1(procrastinate_job_to_defer_v1[])'"
                    ") IS NOT NULL"
                )
            )
            return revision == APPLICATION_SCHEMA_REVISION and bool(queue_ready)
    except SQLAlchemyError:
        return False
