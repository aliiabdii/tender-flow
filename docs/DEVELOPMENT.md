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
