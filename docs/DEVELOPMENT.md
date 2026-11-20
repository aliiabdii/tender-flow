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
Revision `0002` adds local identities, sessions, throttling and audit records.
Revision `0003` adds explicitly linked external identities.
Revision `0004` adds buyers, versioned tenders, contributor history and
transactional idempotency records.
Revision `0005` adds durable processing runs and items.

To run the API on the host, override only that command's URL using the
loopback port assigned by Compose:

```bash
TENDERFLOW_DATABASE_URL="postgresql+psycopg2://tenderflow:${POSTGRES_PASSWORD}@$(docker compose port postgres 5432)/tenderflow" \
  .venv/bin/python -m uvicorn tenderflow.main:create_app --factory
```

`GET /health/live` does not access the database. `GET /health/ready` returns
503 until both application and queue schemas are accessible and at least one
authentication provider is configured. Liveness remains independent of
authentication setup.

Worker concurrency defaults to 4, with bounded pools. Heartbeats default to
10 seconds and the stalled-worker timeout to 30 seconds; the timeout must
exceed two heartbeat intervals. Recovery runs on startup and every minute,
uses heartbeats rather than elapsed job time, and serializes concurrent
sweepers. Both transient exceptions and worker crashes allow at most three
retries after the initial attempt. Future domain tasks must use the shared
retry policy and make their own publication idempotent.

## Local accounts

There are no default accounts or passwords, public signup, or public password
reset. Generate a signing key and keep it outside version control:

```bash
export TENDERFLOW_LOCAL_SIGNING_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

Use the same key for every API process. The key must contain at least 32 bytes;
changing it invalidates issued access tokens. Keep a stable key in your local
secret configuration rather than generating a new one at every startup.
The host API inherits this environment variable; container commands must
explicitly receive it with `-e TENDERFLOW_LOCAL_SIGNING_KEY`.

Bootstrap the first administrator against the migrated database:

```bash
docker compose run --rm -e TENDERFLOW_LOCAL_SIGNING_KEY app-migrate \
  python -m tenderflow.infrastructure.auth.bootstrap \
  --login administrator --display-name Administrator
```

The command refuses to run when an administrator role already exists. Its JSON
output intentionally contains a one-time activation token: run it in a trusted
terminal, avoid captured CI logs, and deliver the token through a secure channel.
Do not pass a password in command arguments.

Use the activation token and a new password with
`POST /api/v1/auth/local/activate` (`token`, `password`), then log in through
`POST /api/v1/auth/local/login` (`login`, `password`).
Send the returned access token as `Authorization: Bearer ...`.
`GET /api/v1/auth/me` returns the current account and roles.
See `/docs` for the request/response schemas.

Administrators provision accounts with `POST /api/v1/admin/users`
(`login`, `display_name`, `roles`, optional `local_access`), update roles or
active status through `PATCH /api/v1/admin/users/{id}`, and issue password-reset
tokens through `POST /api/v1/admin/users/{id}/password-resets`.
Provisioning with `local_access: false` creates an identity without a password
or activation token; external-provider linking is a later feature.
Activation and reset tokens use the same activation endpoint.
Only their hashes are stored; tokens are single-use and expire after 24 hours
for activation or one hour for reset. Issuing a reset immediately invalidates
the old password and local sessions; it does not reactivate a disabled account.

Passwords default to 15-128 characters, without composition rules or silent
truncation, and are hashed using Argon2id. Logins are normalized with Unicode
NFKC and case folding. Failed login attempts are limited to five per normalized
login and 100 per peer IP in a 15-minute window. Unknown and existing accounts
receive the same generic authentication/throttling responses.
Do not trust client-supplied forwarding headers: when deploying behind a proxy,
configure Uvicorn's trusted proxy addresses explicitly so peer-IP limits reflect
the intended client rather than an untrusted header or a shared proxy.

Access tokens last 10 minutes by default. Refresh through
`POST /api/v1/auth/local/refresh` (`refresh_token`); refresh tokens rotate on
every use. The refresh family has an **absolute seven-day lifetime**, not a
sliding expiry. Reusing an old refresh token revokes the whole family, including
its access tokens; clients must serialize refresh requests.
`POST /api/v1/auth/local/logout` accepts the refresh token and revokes its family.
Disabling a user, resetting their password, logout and replay revocation take
effect on subsequent access-token requests. Role changes are read from the
database on every request.

All policy values are configurable through the corresponding `TENDERFLOW_`
settings in `config.py`. Local JWTs use a fixed HS256 algorithm with separate
issuer and audience settings; they do not establish trust in external tokens.
Keep tokens, passwords and signing keys out of logs. Expired session, activation
and throttle rows currently require an operational retention policy; automated
cleanup is not part of this identity feature.

## Microsoft Entra accounts

Entra authentication is optional and disabled by default. This implementation
accepts **single-tenant Microsoft public-cloud v2.0 delegated API access tokens
signed with RS256**, not v1.0, Microsoft Graph, ID tokens, or app-only tokens.
No live tenant or application registrations are created by the tests.

An administrator must first register the TenderFlow API in Entra, set
`api.requestedAccessTokenVersion` to `2`, expose a delegated API scope, and
register a **separate public client** authorized to request it. Configure all
three settings together, using the real registration values:

```bash
export TENDERFLOW_ENTRA_TENANT_ID="<tenant-directory-UUID>"
export TENDERFLOW_ENTRA_AUDIENCE="<TenderFlow-API-application-client-UUID>"
export TENDERFLOW_ENTRA_REQUIRED_SCOPE="api://<TenderFlow-API-application-client-UUID>/access_as_user"
```

`ENTRA_AUDIENCE` is the API registration's client ID, not the public client's
ID or an App ID URI. `ENTRA_REQUIRED_SCOPE` is the **full** scope URI copied
from "Expose an API"; custom HTTPS App ID URIs are also supported. Validation
requires the final scope name (for example `access_as_user`) as a whole value
in the signed `scp` claim. No scopes or application roles in the token grant
TenderFlow roles: administrators manage those on the internal user.

The issuer and JWKS URL are derived only from the configured tenant:
`https://login.microsoftonline.com/<tenant>/v2.0` and
`https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys`.
Token-provided issuers, `jku`, or other key URLs never select an endpoint.
Keys are fetched lazily with a three-second timeout and cached for five minutes.
Unknown key IDs can trigger a refresh at most once per 30 seconds; a newly
rotated key may be rejected temporarily during that cooldown. Individual keys
are not cached indefinitely. An unavailable key service returns a safe `503`
when a refresh is needed; unexpired cached keys remain usable.
Failed fetches also have a 30-second retry backoff, preventing repeated
outage timeouts from blocking cached-key authentication.
Neither app startup nor liveness performs key discovery.

Provision the internal user first (optionally with `local_access: false`), then
link the tenant's user object ID:

```text
POST /api/v1/admin/users/{user_id}/entra-identities
{"tenant_id": "<configured-tenant-UUID>", "object_id": "<user-object-UUID>"}
```

The authorized administrator receives `201` with the link's `id`, `user_id`,
`tenant_id`, and `object_id`. Save the link ID for removal through
`DELETE /api/v1/admin/users/{user_id}/entra-identities/{identity_id}` (`204`).
The provider/tenant/object tuple is unique across internal users. Links and
unlinks are audited in the same transaction. Use the user's **Object ID** in
the configured tenant (including the tenant-local object for a guest), not an
email, display name, application ID, or object ID from another tenant.
Unknown identities are rejected; there is no JIT provisioning or email linking.

Clients obtain API access tokens through Microsoft's supported
authorization-code flow with PKCE and consent for the configured scope. Send
the **access token**, not an ID token, as `Authorization: Bearer ...`.
Token acquisition, Microsoft refresh tokens, and interactive sign-in belong
to the client; TenderFlow does not provide an Entra password exchange or login
frontend. `/docs` advertises the tenant-specific OAuth2 authorization-code flow
with PKCE enabled. For Swagger's Authorize dialog, enter the separate public
client's ID, leave the client secret empty, and configure its permitted redirect
URI to the API's `/docs/oauth2-redirect` URL in Entra.

Local and linked Entra logins resolve to the same canonical internal user ID.
Disabling a user, changing roles, or deleting a link is reflected on subsequent
requests even if an Entra token has not expired. Unlinking does not revoke the
user's separate local sessions; a local password reset/logout does not revoke
Microsoft-issued tokens. Disable the internal user to block both methods.

Entra can run without a local signing key, but initial administrator creation
still uses the bootstrap/activation workflow described above. Keep local auth
configured until an existing administrator has a verified working Entra link;
removing the local key too early can lock out all administrators.
Restart the API when changing provider settings. Pass the Entra environment
variables explicitly with `docker compose run -e ...` for container commands;
the development Compose definition forwards only its database URL by default.

## Buyers and tender registration

Managers (`tender_manager`) create and edit buyers and tenders. Managers and
approvers can read them and their tender audit history; `admin` alone grants
no business permissions. Business services recheck current account status and
roles inside the database transaction.

Use `POST /api/v1/buyers` with `name` and string `customer_number`, then
`POST /api/v1/tenders` with `buyer_id`, `title`, and optionally
`pricing_customer_number`, `submission_deadline`, and `supersedes_tender_id`.
Customer numbers remain strings, including leading zeroes. A tender snapshots
the buyer's customer number unless explicitly overridden; later buyer edits
never rewrite existing snapshots.

Create and item-read responses include `ETag`; creation also returns `Location`.
Send the item's current, quoted ETag as `If-Match` when using
`PATCH /api/v1/buyers/{id}` or `PATCH /api/v1/tenders/{id}`. Missing preconditions
return `428`; stale, cross-resource, weak, wildcard, or multiple validators
return `412`. Buyer patches accept only `name` and `customer_number`; tender
patches accept only `title`, `pricing_customer_number`, and
`submission_deadline`. Empty patches and null values other than a cleared
deadline return `422`. Every accepted patch advances the item's revision,
including same-value changes. Tender patches advance preparation and content
versions together without changing the source revision.
Tender ETags also reflect whether the deadline warning currently applies.
Crossing the deadline changes that representation validator without a content
edit; refresh the item and retry a `412` with its new ETag.

Deadlines require a timezone offset and are returned in UTC. Passed deadlines produce the
`submission_deadline_passed` warning, not an edit block. An amendment creates
a new draft tender linked to an existing tender, with all versions starting
at one and no automatic preparation copying. The link cannot be patched:
API/service callers cannot rewrite history or introduce amendment cycles.
Direct SQL access remains an operational trust boundary; the database separately
rejects self-links.

`POST /api/v1/tenders` optionally accepts `Idempotency-Key`: 1-128 visible ASCII
characters without spaces. Keys are scoped to the current internal user and
command. Reusing an unexpired key with the same validated request returns the
same resource; a changed request returns `409`. A replay returns the resource's
current representation, not a byte-for-byte copy of the original response.
The key record, tender, contributor and audit writes commit or roll back
together, before a success response is sent. Only hashes of keys and requests
are stored. Retention defaults to 24 hours, configurable through
`TENDERFLOW_IDEMPOTENCY_SECONDS`; expiry permits a new result. Expired records
are replaced on reuse; bulk retention cleanup remains operational.

`GET /api/v1/buyers`, `GET /api/v1/tenders`, and
`GET /api/v1/tenders/{id}/audit-events` return `items` and `next_cursor`.
Pass `limit` (default 100, maximum 200) and the opaque `cursor` to continue.
Ordering uses immutable creation time plus ID, including timestamp ties.
Cursors are endpoint-specific and are not authorization tokens or a frozen
snapshot; concurrent inserts after the marker may appear on later pages.
Creation and preparation edits retain unique contributor membership, while
reads do not contribute. Audit responses project only validated identifier,
version, role and state metadata, never titles, customer numbers or raw requests.

## Processing progress and retries

`GET /api/v1/processing-runs/{id}` returns the run kind, status, persisted item
counts, warnings, timestamps, replacement/retry references and an `items_url`.
Follow that URL for cursor-paginated item results and safe failure codes.
Managers and approvers can read progress; `admin` alone grants no access.
Responses use `Cache-Control: no-store`; reads do not add contributors.
Counts come from persisted item states, not worker-process memory.
`completed_count` includes successes and failures; superseded items are counted
separately. A run becomes `partial_failure` when all work finishes with a mix of
successes and failures. No-match is a success, not a technical failure.

Managers explicitly retry through:

```http
POST /api/v1/processing-runs/{id}/retries
Idempotency-Key: <new-key-for-this-retry-command>
```

No body is required; `{}` is accepted and other fields are rejected. The command
returns `202`, a **new run ID**, and its status URL in `Location`. Only failed
items whose input versions still match are copied, with new item IDs and links
to the original failures. Successful work is not repeated. The original items
and their outcomes remain available; the original run becomes superseded and
points to the replacement. Repeating the command with the same user/key returns
the same retry run. Keys use the existing configurable idempotency retention.
A different retry key against a superseded run, a still-active run, or a run
without eligible failures returns `409`.

Automatic worker retries remain separate and bounded. When the queue reports
an exhausted failure (including recovered crashes), status reads and retry
commands reconcile unfinished application items to the safe `worker_failed`
code. Cancelled, aborted or removed jobs are also reconciled without erasing
already-published outcomes. A job that finishes without publishing a result becomes
an application failure. Upstream exception bodies, task names, queue arguments
and internal job IDs are not exposed through these APIs.

This increment supplies the shared processing foundation, not a public
start-arbitrary-task endpoint. Stage-specific creation endpoints and real
executors arrive with extraction, matching, lookup, pricing and export features.
The internal services create runs and defer tasks transactionally with only
`item_id` in job arguments. Workers capture validated input versions in a short
transaction, perform external work outside transactions, then conditionally
publish in another short transaction. Duplicate, stale or superseded completion
cannot replace a current outcome. Test-only worker tasks exercise real execution,
bounded failures and crash recovery without contacting any external service.

Currently items are run-level and current-run uniqueness is per tender/kind.
Document/line foreign keys, ownership checks and subject-specific version
fingerprints must be added alongside their real models before those workers
are enabled. Run-level extraction checks source revision; other run-level work
also checks preparation version, never aggregate content revision.
Concrete adapters must include all command inputs in their idempotency hash,
refine replacement scope for independent line work, and apply actual offer
changes/revision increments atomically only when publication is accepted.

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
