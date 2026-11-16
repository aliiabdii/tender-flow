import os
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from alembic import command
from tenderflow.config import Settings
from tenderflow.infrastructure.database.sessions import session_scope
from tenderflow.workers.app import create_worker_app, enqueue_job


@pytest.fixture(scope="session")
def postgres_url():
    if url := os.environ.get("TEST_DATABASE_URL"):
        yield SecretStr(url)
        return
    project = f"tenderflow-test-{uuid4().hex[:12]}"
    password = secrets.token_urlsafe(24)
    env = {
        **os.environ,
        "POSTGRES_PASSWORD": password,
        "TENDERFLOW_DATABASE_URL": (
            f"postgresql+psycopg2://tenderflow:{password}@postgres/tenderflow"
        ),
    }
    compose = ["docker", "compose", "-p", project]
    try:
        subprocess.run(
            [*compose, "up", "-d", "--wait", "--wait-timeout", "60", "postgres"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        address = subprocess.check_output(
            [*compose, "port", "postgres", "5432"], env=env, text=True, timeout=10
        ).strip()
        port = address.rsplit(":", 1)[1]
        yield SecretStr(
            f"postgresql+psycopg2://tenderflow:{password}@127.0.0.1:{port}/tenderflow"
        )
    finally:
        subprocess.run(
            [*compose, "down", "--volumes"],
            env=env,
            check=True,
            capture_output=True,
            timeout=30,
        )


def queue_job_count(session: Session) -> int:
    return session.scalar(
        text("SELECT count(*) FROM procrastinate_jobs WHERE task_name='tests.complete'")
    )


class QueueProbe:
    def __init__(self, url, directory):
        self.settings = Settings(
            database_url=SecretStr(url),
            local_signing_key=SecretStr(
                "synthetic-test-signing-key-not-for-production-12345"
            ),
            worker_concurrency=1,
            worker_heartbeat_seconds=0.1,
            worker_stalled_seconds=1,
        )
        self.engine = create_engine(url, hide_parameters=True)
        self.app = create_worker_app(settings=self.settings)
        self.directory = directory
        self.log_paths = []

    def session(self):
        return session_scope(engine=self.engine)

    def scalar(self, query):
        with self.session() as session:
            return session.scalar(text(query))

    def add_marker(self, session, key):
        session.execute(
            text("INSERT INTO test_inputs(key) VALUES (:key) ON CONFLICT DO NOTHING"),
            {"key": key},
        )

    def marker_count(self):
        return self.scalar("SELECT count(*) FROM test_inputs")

    def job_count(self):
        with self.session() as session:
            return queue_job_count(session)

    def completion_count(self):
        return self.scalar("SELECT count(*) FROM test_completions")

    def job_status(self):
        return self.scalar(
            "SELECT status FROM procrastinate_jobs WHERE task_name='tests.complete'"
        )

    def job_attempts(self):
        return self.scalar(
            "SELECT attempts FROM procrastinate_jobs WHERE task_name='tests.complete'"
        )

    def enqueue(self, key, deliveries=1, **arguments):
        with self.session() as session:
            self.add_marker(session, key)
            for _ in range(deliveries):
                enqueue_job(
                    session=session,
                    task_name="tests.complete",
                    arguments={"key": key, **arguments},
                )

    def wait_for(self, condition):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.05)
        logs = "\n".join(path.read_text() for path in self.log_paths)
        pytest.fail(f"Queue condition timed out.\n{logs}")

    def wait_for_job_status(self, status, count=1):
        def reached():
            with self.session() as session:
                return (
                    session.scalar(
                        text(
                            "SELECT count(*) FROM procrastinate_jobs "
                            "WHERE task_name='tests.complete' AND status=:status"
                        ),
                        {"status": status},
                    )
                    == count
                )

        self.wait_for(reached)

    def wait_for_completion(self):
        self.wait_for(lambda: self.completion_count() == 1)

    def wait_for_stale_heartbeat(self):
        self.wait_for(
            lambda: self.scalar(
                "SELECT coalesce(bool_and(last_heartbeat < "
                "now() - interval '1 second'), true) FROM procrastinate_workers"
            )
        )

    @contextmanager
    def worker(self):
        path = self.directory / f"worker-{len(self.log_paths)}.log"
        self.log_paths.append(path)
        env = {
            **os.environ,
            "TENDERFLOW_DATABASE_URL": self.settings.database_url.get_secret_value(),
            "TENDERFLOW_WORKER_CONCURRENCY": "1",
            "TENDERFLOW_WORKER_HEARTBEAT_SECONDS": "0.1",
            "TENDERFLOW_WORKER_STALLED_SECONDS": "1",
        }
        with path.open("w") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).parents[1] / "integration" / "worker_probe.py"),
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                self.wait_for(
                    lambda: (
                        process.poll() is not None
                        or self.scalar("SELECT count(*) FROM procrastinate_workers") > 0
                    )
                )
                if process.poll() is not None:
                    pytest.fail(path.read_text())
                yield process
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)


@pytest.fixture
def queue_probe(postgres_url, tmp_path):
    schema = f"tf_test_{uuid4().hex}"
    admin = create_engine(postgres_url.get_secret_value())
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    url = make_url(postgres_url.get_secret_value()).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    probe = QueueProbe(url.render_as_string(hide_password=False), tmp_path)
    try:
        with probe.app.open() as app:
            app.schema_manager.apply_schema()
        config = Config("alembic.ini")
        with probe.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.execute(text("CREATE TABLE test_inputs(key text PRIMARY KEY)"))
            connection.execute(
                text(
                    "CREATE TABLE test_completions("
                    "key text PRIMARY KEY REFERENCES test_inputs(key))"
                )
            )
        yield probe
    finally:
        probe.engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.fixture
def db_session(queue_probe):
    with queue_probe.session() as session:
        yield session
