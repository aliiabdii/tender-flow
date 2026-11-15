# Local runtime and database

Use the existing `.venv` and install the recorded dependencies:

```bash
.venv/bin/python -m pip install -c requirements-dev.lock -e '.[dev]'
```

Docker Compose supplies PostgreSQL 17 and a separately runnable worker.
Generate a local password, then configure the **container** connection URL:

```bash
export POSTGRES_PASSWORD="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(24))')"
export TENDERFLOW_DATABASE_URL="postgresql+psycopg2://tenderflow:${POSTGRES_PASSWORD}@postgres:5432/tenderflow"
docker compose up -d --wait postgres
```

Keep these variables in the same shell, or persist them in an ignored `.env`.
The generated password is URL-safe. Encode special characters if supplying
your own password in a connection URL.

## Migrations and worker

Application migrations and queue initialization are separate operations:

```bash
docker compose run --rm app-migrate
docker compose run --rm queue-init
docker compose up -d worker
```

`queue-init` installs Procrastinate's schema only when its tables and functions
**do not yet exist**. Do not rerun it on an existing queue schema.
For upgrades, stop producers and workers,
apply the release's documented Procrastinate SQL migrations in order, and keep
an operational record of those migrations. Never include library-owned queue
tables in Alembic revisions. The installed release lists its migration scripts:

```bash
.venv/bin/python -m procrastinate schema --migrations-path
```

New application migrations must also update `APPLICATION_SCHEMA_REVISION`
in `infrastructure/database/base.py`; readiness rejects a different revision.
No application domain tables are needed until the next feature.

To run the API on the host, override only that command's URL using the
loopback port assigned by Compose:

```bash
TENDERFLOW_DATABASE_URL="postgresql+psycopg2://tenderflow:${POSTGRES_PASSWORD}@$(docker compose port postgres 5432)/tenderflow" \
  .venv/bin/python -m uvicorn tenderflow.main:create_app --factory
```

`GET /health/live` does not access the database. `GET /health/ready` returns
503 until both application and queue schemas are accessible.

Worker concurrency defaults to 4, with bounded pools. Heartbeats default to
10 seconds and the stalled-worker timeout to 30 seconds; the timeout must
exceed two heartbeat intervals. Recovery runs on startup and every minute,
uses heartbeats rather than elapsed job time, and serializes concurrent
sweepers. Both transient exceptions and worker crashes allow at most three
retries after the initial attempt. Future domain tasks must use the shared
retry policy and make their own publication idempotent.

## Tests

```bash
.venv/bin/python -m pytest -W error
.venv/bin/python -m ruff check src tests alembic
.venv/bin/python -m ruff format --check src tests alembic
```

Integration tests start a uniquely named, disposable Compose PostgreSQL
instance, use a unique schema per test, and remove their own containers and
volume afterwards. Alternatively, set `TEST_DATABASE_URL` to a **dedicated
test database** using `postgresql+psycopg2`; its account must be able to create
and drop schemas. Tests never use `TENDERFLOW_DATABASE_URL` as a fallback.

The worker-crash tests start real subprocesses, terminate only their own
worker PIDs, and verify recovery and duplicate-safe publication in PostgreSQL.
