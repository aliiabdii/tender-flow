# TenderFlow MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved tender preparation API incrementally, from durable processing and identity through extraction, matching, pricing, independent approval, exports, and manual submission tracking.

**Architecture:** One application codebase supplies separate FastAPI and Procrastinate worker processes, sharing PostgreSQL and private object storage. Small integrations call the existing discovery, pricing, and LiteLLM services. Immutable sources, conditional asynchronous writes, and approved snapshots protect user decisions.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL, Procrastinate, pytest, Docker Compose; initial AWS deployment on ECS/Fargate, RDS, and S3.

**Spec:** `docs/TECHNICAL_DESIGN.md` and `docs/BUSINESS_REQUIREMENTS.md`. Read both before execution.

**Status:** Approved by the user on 2026-11-15. Native execution selected:
the main agent implements and a separate agent reviews each feature. No
application implementation has started; beginning Task 1 requires the user's
instruction to start.

## Global Constraints

- "The scope is one supplier organization per deployment."
- "Never confuse an AI assessment with human approval."
- "Never send application-held confidential prices to AI."
- "Never finalize an offer containing an unresolved line."
- "Never let a preparer approve their own tender through another login method."
- "Never export content different from what was approved as though it were approved."
- "Preserve the buyer's original template outside approved response cells."
- "Never hold database transactions open while calling external services."
- "Do not execute uploaded macros or evaluate spreadsheet formulas."
- "Use real PostgreSQL, not SQLite, for database/queue semantics."
- Runtime floor: Python 3.12+; Pydantic 2 and SQLAlchemy 2.
- API prefix `/api/v1`; local access lifetime 10 minutes, refresh lifetime 7 days.
- Configurable initial limits: 25 MiB/file, 20 documents/tender, 5,000 lines/tender.
- Cursor page size: default 100, maximum 200. Quantities/money use `Decimal` and API decimal strings.
- Model changes affect new runs only. Source uploads are assumed price-free; pre-AI redaction remains deferred.
- Every final review requires fresh pricing. No additional elapsed-age threshold is agreed.
- Amendments use new linked tenders; all-skipped tenders become reopenable No-Bid.
- Manual-transfer review without a supported buyer spreadsheet approves the internal worksheet with explicit acknowledgment; it produces no buyer file.
- Each feature needs a separate agent review and user review before committing. Wait for user feedback before the next major feature.
- Design approval is not permission to install, deploy, commit, or implement the entire plan automatically.

## Review Focus

1. Repeated descriptions and buyer article `000123` must preserve separate source rows and leading zeros; pinned in Task 8.
2. Ambiguous quantity text such as `1,250` must remain unresolved with a warning rather than be silently interpreted; pinned in Task 8.
3. Repeated product IDs at different quantities must remain separate price requests/results, even when responses arrive reordered; pinned in Task 14.
4. Unexpected nested price fields in discovery responses must never enter AI requests, including retries; pinned in Tasks 9 and 11.
5. Reading a review must not make its reviewer a preparer, while editing the mapping must; pinned in Tasks 15 and 17.

---

## Execution contract

This is one dependent workflow, not several independent products. Execute the
tasks in order; their interfaces are the contracts between increments. Stop
at the review gate of each completed task. The user may group adjacent tasks
for review, but the executor must not assume that permission.

### Environment and dependencies

The existing `.venv` is Python 3.15.2. Do not delete, replace, or install into it
without permission. Use a separate `.venv-mvp` for execution; Python 3.12 is the
initial reproducible baseline, satisfying the approved 3.12+ floor. If that
interpreter is unavailable, resolve the runtime prerequisite before proceeding;
do not install packages globally in this shared environment.

Commands below use `.venv-mvp/bin/python`. Create this environment only when
execution starts. Add dependency manifests before installing packages. Resolve
and record compatible versions when the first consuming task adds a dependency;
do not claim a version combination works until its tests pass.

Initial development tools: pytest, pytest-cov, HTTPX, and Ruff. Subsequent tasks
add only their required runtime dependencies. No live AI, discovery, pricing,
Entra, or AWS calls are permitted without explicitly authorized test environments.

### Common verification and review gate

Each task has a RED command and a GREEN command. Run RED before implementation;
it must fail for the described missing behavior, not an unrelated environment
failure. After GREEN, run:

```bash
.venv-mvp/bin/python -m ruff check src tests
.venv-mvp/bin/python -m ruff format --check src tests
```

Add these only when the corresponding paths exist. Fix behavior introduced by
the task; do not broaden into unrelated cleanup.

Then obtain a separate agent's code review, address material findings, and
present the implementation, tests, and remaining limitations to the user.
Only after explicit approval to commit, stage the reviewed task files and commit:

```bash
git add -- <exact reviewed paths listed in the task>
git commit -m "<task's commit message>" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

The placeholders are instructions to enumerate the task's actual paths, not a
command to run verbatim. Never use `git add .`; existing `.idea` changes and
`docs/INIT.md` changes belong to the user. Do not commit this plan or other
pre-existing documentation changes without explicit approval.

### Cross-task conventions

- `Session` means a SQLAlchemy session; `UUID` and `Decimal` are standard types.
- `JsonValue` means Pydantic's JSON value type; `T` in the AI client is bounded
  by Pydantic `BaseModel`.
- Service signatures below are keyword-only. `Actor` is defined in Task 3.
- Source paths without `src/tenderflow/` in task file lists are relative to that
  package; paths beginning `tests/`, `docs/`, `deploy/`, or `alembic/` are repository-relative.
- ORM records and response schemas are distinct; API adapters serialize explicitly.
- Pure domain rules do not open sessions or call external services.
- Mutations lock the tender first, then affected lines in stable ID order.
- Capture inputs in a short transaction, make network calls outside it, then
  conditionally publish in a new transaction.
- API errors use the design's problem-details codes and status distinctions.
- Test fixtures belong to the task introducing their domain records; keep them
  in that task's test module until multiple modules actually need them.
- Model modules are split by table group rather than growing the design's
  illustrative `models.py` into one large file. No generic repository layer.

## File ownership map

| Area | Files and responsibility |
|---|---|
| Runtime | `pyproject.toml`, `src/tenderflow/main.py`, `config.py`; package, settings, app composition. |
| API conventions | `api/dependencies.py`, `api/problems.py`, `api/preconditions.py`, `api/pagination.py`. |
| Database | `infrastructure/database/base.py`, `sessions.py`, `models/`; metadata, sessions, focused table groups. |
| Identity | `schemas/identity.py`, `services/identity.py`, `infrastructure/auth/`, `api/auth.py`, `api/admin.py`. |
| Tenders | `schemas/tenders.py`, `services/tenders.py`, `services/audit.py`, `api/buyers.py`, `api/tenders.py`. |
| Durable processing | `schemas/processing.py`, `services/processing.py`, `workers/app.py`, `workers/tasks.py`; task modules added as needed. |
| Documents | `infrastructure/storage.py`, `infrastructure/documents/`, `schemas/documents.py`, `services/documents.py`. |
| AI | `schemas/ai.py`, `infrastructure/integrations/litellm.py`, `services/processing_configuration.py`. |
| Lines and matching | `schemas/lines.py`, `domain/requirements.py`, `domain/matching.py`, `services/lines.py`, `services/matching.py`. |
| Pricing | `schemas/pricing.py`, `domain/quantities.py`, `services/pricing.py`, `infrastructure/integrations/pricing.py`. |
| Review and approval | `schemas/offers.py`, `services/template_mapping.py`, `services/reviews.py`, `services/approvals.py`, `domain/approval.py`. |
| Output | `services/exports.py`, `infrastructure/documents/exports.py`, `services/submissions.py`, `api/offers.py`. |
| Operations | `observability.py`, `Dockerfile`, `compose.yaml`, `deploy/aws/template.yaml`, `docs/OPERATIONS.md`. |

Paths in the table are under `src/tenderflow/` unless explicitly rooted elsewhere.
Each task lists the exact additions and modifications it owns. Alembic revisions
use the sequence below plus a generated revision identifier.

## Task 1: Validated API runtime and health

**Dependencies:** None.

**Files:** Create `pyproject.toml`, `.gitignore`, `src/tenderflow/__init__.py`,
`src/tenderflow/main.py`, `src/tenderflow/config.py`,
`tests/conftest.py`, `tests/api/test_health.py`.

**Interfaces:**
- Produce `Settings` in `config.py`: environment-backed configuration with secrets represented as secret values, not printable strings.
- Produce `create_app(*, settings: Settings) -> FastAPI` in `main.py`.
- Produce the `client` pytest fixture using HTTPX/TestClient and explicit test settings.

- [ ] **Step 1: Write `test_liveness_and_invalid_configuration`** with these assertions:

```python
def test_liveness_and_invalid_configuration(client):
    assert client.get("/health/live").status_code == 200
    with pytest.raises(ValidationError):
        Settings(max_upload_bytes=0)
```

- [ ] **Step 2: Declare the package, Python floor, test tools and empty package marker.**
  Use `src` layout and pytest paths; declare FastAPI/Pydantic/settings dependencies.
  Ignore `.venv-mvp`, caches, local secrets and generated artifacts; do not remove tracked user files.
- [ ] **Step 3: Prepare the isolated interpreter and install the declared manifest.**
  Verify `python3.12 --version`, create `.venv-mvp` if absent, then run
  `.venv-mvp/bin/python -m pip install -e '.[dev]'`.
- [ ] **Step 4: RED:** run `.venv-mvp/bin/python -m pytest tests/api/test_health.py -q`.
  Expect missing `create_app` or failing health/config assertions.
- [ ] **Step 5: Implement the two interfaces.**
  Validate the approved limits, register liveness, and keep app creation free of network calls.
- [ ] **Step 6: GREEN:** repeat Step 4, then the common lint/format commands.
  Expect all selected tests and checks to pass.
- [ ] **Step 7: Apply the review gate.** Commit message, if approved: `feat: add validated API runtime`.

## Task 2: PostgreSQL migrations and durable transactional jobs

**Dependencies:** Task 1.

**Files:** Create `compose.yaml`, `alembic.ini`, `alembic/env.py`,
`src/tenderflow/infrastructure/database/base.py`,
`src/tenderflow/infrastructure/database/sessions.py`,
`src/tenderflow/workers/app.py`, `src/tenderflow/workers/tasks.py`,
`tests/integration/conftest.py`, `tests/integration/test_queue_transactions.py`,
`tests/integration/test_worker_recovery.py`.
Modify `pyproject.toml`, `src/tenderflow/main.py`, `config.py`.

**Interfaces:**
- Produce `Base` and `session_scope() -> ContextManager[Session]`.
- Produce `enqueue_job(*, session: Session, task_name: str, arguments: dict[str, JsonValue]) -> int`.
- Produce `recover_stalled_jobs() -> int` and the worker registration entry point.
- Produce isolated PostgreSQL fixtures `db_session`, `queue_probe`, and
  `queue_job_count(session: Session) -> int` in integration test support.

- [ ] **Step 1: Write queue tests**: a transaction inserting a test-only marker and
  deferring a job rolls both back; a commit exposes both; retrying completion
  does not duplicate its marker. Include:

```python
def test_rollback_does_not_leave_queued_work(queue_probe):
    queue_probe.enqueue_then_rollback()
    assert queue_probe.marker_count() == 0
    assert queue_probe.job_count() == 0
```

  `queue_probe` is a test fixture with its own disposable schema, not a production feature.
  Add a subprocess test killing its specific worker PID, restarting, and checking
  heartbeat-based recovery completes the task once at the domain boundary.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/integration/test_queue_transactions.py tests/integration/test_worker_recovery.py -q`.
  Expect the missing queue integration/recovery behavior.
- [ ] **Step 3: Add compatible SQLAlchemy, Alembic, Procrastinate and driver dependencies.**
  Use the documented SQLAlchemy connector for same-transaction deferral; verify
  its real API against the resolved release. Configure PostgreSQL and worker
  services in Compose and separate queue/application migration procedures.
- [ ] **Step 4: Implement transactional enqueue, bounded retries and explicit stalled recovery.**
  Add readiness checking database access/schema availability, without making
  liveness depend on upstream services. No user-facing demonstration queue endpoint.
- [ ] **Step 5: GREEN:** repeat Step 2 and `tests/api/test_health.py`; run common checks.
  A connector failure is a blocking architecture finding, not permission to
  enqueue after commit.
- [ ] **Step 6: Apply the review gate.** Message: `feat: add durable PostgreSQL processing`.

## Task 3: Local identity, administrator provisioning, and sessions

**Dependencies:** Task 2.

**Files:** Create `schemas/identity.py`, `services/identity.py`, `services/audit.py`,
`infrastructure/auth/local.py`, `infrastructure/database/models/identity.py`,
`infrastructure/database/models/audit.py`,
`api/auth.py`, `api/admin.py`, `api/dependencies.py`,
`infrastructure/auth/bootstrap.py` under `src/tenderflow/`;
create the identity Alembic revision, `tests/api/test_local_auth.py`,
`tests/integration/test_refresh_sessions.py`.
Modify `main.py`, `config.py`, `pyproject.toml`, Alembic metadata imports.

**Interfaces:**
- Produce `Actor(id: UUID, roles: frozenset[str])`.
- Produce `authenticate_local(*, login: str, password: str) -> TokenPair`,
  `refresh_local(*, refresh_token: str) -> TokenPair`,
  `get_actor(*, bearer_token: str) -> Actor`.
- Produce `require_roles(*roles: str)` API dependency.
- Produce `provision_user(*, actor: Actor, command: ProvisionUser) -> UserProvisioned`,
  `activate_local(*, token: str, password: str) -> None`,
  `reset_local_password(*, actor: Actor, user_id: UUID) -> ActivationSecret`.
- Produce `write_audit(*, session: Session, actor: Actor | None, action: str, entity_id: UUID, metadata: dict[str, JsonValue]) -> None`;
  allow only safe identifiers, revision numbers and action/error codes in metadata.
- Tests introduce fixtures `manager`, `other_manager`, and `administrator`.

- [ ] **Step 1: Write local-auth/session tests**, including:

```python
def test_refresh_rotates_and_replay_revokes_family(local_accounts):
    first = local_accounts.login()
    second = local_accounts.refresh(first.refresh_token)
    assert second.refresh_token != first.refresh_token
    assert local_accounts.try_refresh(first.refresh_token).status_code == 401
    assert local_accounts.try_refresh(second.refresh_token).status_code == 401
```

  `local_accounts` exercises the HTTP endpoints with synthetic identities.
  Also assert inactive users cannot reuse access tokens, reset invalidates
  existing sessions, activation is single-use, no signup route exists, and
  non-admin provisioning is forbidden. Include expired-token rejection and
  repeated failed logins producing `429` without disclosing account existence.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/api/test_local_auth.py tests/integration/test_refresh_sessions.py -q`.
- [ ] **Step 3: Implement the interfaces and identity tables.**
  Use established Argon2id/JWT libraries, unique normalized login names, stored
  token hashes, explicit roles, 10-minute access and 7-day refresh defaults.
  Bind access tokens to the local session and credential version; check their
  revocation state per request so reset/logout/replay revocation takes effect.
  Add login throttling with a safe `429`, no logged credentials, and administrator
  bootstrap without default passwords. Activation/reset secrets are single-use
  and expiring; return them only to the authorized administrator.
- [ ] **Step 4: Wire the design's local auth and user-administration routes.**
  Preserve the distinct application token issuer/audience; load active account
  and role state per request.
- [ ] **Step 5: GREEN:** repeat Step 2 and common checks.
- [ ] **Step 6: Apply the review gate.** Message: `feat: add administrator-managed local accounts`.

## Task 4: Explicitly linked Microsoft Entra authentication

**Dependencies:** Task 3.

**Files:** Create `src/tenderflow/infrastructure/auth/entra.py`,
`tests/api/test_entra_auth.py`; modify `services/identity.py`, `api/admin.py`,
`api/dependencies.py`, `schemas/identity.py`, `config.py`, and identity migrations.

**Interfaces:**
- Consume Task 3's `Actor` and account state.
- Produce `validate_entra_access_token(*, token: str) -> EntraIdentity`,
  where `EntraIdentity` contains tenant ID/object ID, not application roles.
- Produce `link_entra_identity(*, actor: Actor, user_id: UUID, tenant_id: str, object_id: str) -> None`.
- Extend `get_actor` to resolve a validated, explicitly linked identity.

- [ ] **Step 1: Write Entra tests**, using locally signed synthetic JWTs and a
  mocked JWKS endpoint:

```python
def test_two_login_methods_resolve_to_one_user(linked_logins):
    assert linked_logins.local_actor().id == linked_logins.entra_actor().id
    assert linked_logins.unlinked_same_email_login().status_code == 401
```

  Assert wrong audience/tenant/scope, ID tokens, unlinked identities and disabled
  users fail; unlinking takes effect on the next request.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/api/test_entra_auth.py -q`.
- [ ] **Step 3: Implement validation, unique tenant/object linkage, and unlinking.**
  Use configured trusted issuers/key endpoints; never trust email for linking
  or use unverified issuer text to fetch arbitrary keys. Document the client-side
  authorization-code/PKCE flow in OpenAPI; no custom login frontend.
- [ ] **Step 4: GREEN:** repeat Step 2 plus Task 3 auth tests and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: support explicitly linked Entra identities`.

## Task 5: Buyers, tenders, contributor history, and API mutation rules

**Dependencies:** Tasks 2–4.

**Files:** Create `schemas/tenders.py`, `services/tenders.py`,
`infrastructure/database/models/tenders.py`, `api/buyers.py`, `api/tenders.py`,
`api/problems.py`, `api/preconditions.py`, `api/pagination.py` under `src/tenderflow/`;
create tender/idempotency migrations, `tests/api/test_tenders.py`,
`tests/integration/test_tender_revisions.py`. Extend Task 3's audit service without
changing its signature.

**Interfaces:**
- Produce `create_tender(*, session: Session, actor: Actor, command: TenderCreate) -> Tender`.
- Produce `record_contribution(*, session: Session, tender_id: UUID, actor: Actor) -> None`.
- Produce `touch_preparation(*, session: Session, tender: Tender, actor: Actor) -> None`:
  increment preparation/content versions under the tender lock.
- Produce `require_version(*, actual: int, expected: int | None) -> None`,
  `Page[T]`, and sanitized `Problem` schemas.
- Produce `idempotent_command(*, session: Session, actor: Actor, scope: str, key: str, request_hash: str, execute: Callable[[], UUID]) -> UUID`.

- [ ] **Step 1: Write CRUD/revision tests**:

```python
def test_amendment_starts_fresh_without_rewriting_history(tenders_api):
    old = tenders_api.prepared_tender()
    new = tenders_api.create(supersedes_tender_id=old.id)
    assert new.supersedes_tender_id == old.id
    assert new.line_count == 0
    assert new.approval_id is None
    assert tenders_api.get(old.id).content_revision == old.content_revision
```

  Also test `412` for stale versions, `428` for missing preconditions,
  idempotency key/payload conflicts, stable pagination at 100/200 limits,
  retained contributors, customer-number snapshots, amendment cycles, and
  offset-aware deadlines that produce warnings rather than action blocks.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/api/test_tenders.py tests/integration/test_tender_revisions.py -q`.
- [ ] **Step 3: Implement the interfaces and buyer/tender routes.**
  All mutating services use the same lock/revision/contribution helpers.
  Audit identifiers/actions, not raw request bodies. Do not expose arbitrary
  ORM fields through PATCH. Expose the authorized, paginated tender audit route.
- [ ] **Step 4: GREEN:** repeat Step 2 and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: add versioned tenders and contribution history`.

## Task 6: Durable run progress and retry commands

**Dependencies:** Task 5.

**Files:** Create `schemas/processing.py`, `services/processing.py`,
`infrastructure/database/models/processing.py`, `api/processing.py` under
`src/tenderflow/`; create processing migrations,
`tests/api/test_processing_runs.py`, `tests/integration/test_processing_publication.py`.

**Interfaces:**
- Produce `RunKind` for extraction, matching, lookup, pricing, export.
- Produce `start_run(*, session: Session, tender_id: UUID, kind: RunKind, actor: Actor, key: str) -> ProcessingRun`.
- Produce `publish_item(*, session: Session, item_id: UUID, expected_inputs: InputVersions, result: ItemResult) -> bool`.
- Produce `retry_failed_items(*, session: Session, run_id: UUID, actor: Actor, key: str) -> ProcessingRun`.
- `InputVersions` carries subject-specific versions; `ItemResult` carries a
  validated result reference or safe error code, never a raw exception body.

- [ ] **Step 1: Write progress/publication tests**:

```python
def test_duplicate_completion_does_not_double_count(processing_items):
    item = processing_items.queued()
    processing_items.complete(item)
    processing_items.complete(item)
    assert processing_items.run(item).completed_count == 1
```

  Test partial failure, superseded runs, duplicate commands returning one run,
  valid item subject constraints, ID-only queue payloads, and retrying only failed
  still-current items.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/api/test_processing_runs.py tests/integration/test_processing_publication.py -q`.
- [ ] **Step 3: Implement persisted progress and transactional task deferral.**
  Expose the design's run/retry routes with `202`/`Location`. No-match will later
  be a successful result, not a failed run. Keep worker counts out of process memory.
- [ ] **Step 4: GREEN:** repeat Step 2 and Task 2 transaction tests; run common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: expose durable processing progress`.

## Task 7: Immutable private source uploads

**Dependencies:** Tasks 5–6.

**Files:** Create `schemas/documents.py`, `services/documents.py`,
`infrastructure/storage.py`, `infrastructure/database/models/documents.py` under
`src/tenderflow/`; create document migrations, `tests/api/test_documents.py`,
`tests/integration/test_object_storage.py`; modify `compose.yaml`, `pyproject.toml`,
`api/tenders.py`.

**Interfaces:**
- Produce `ObjectStore.put(*, key: str, content: BinaryIO) -> StoredObject`,
  `open(*, key: str) -> ContextManager[BinaryIO]`, `delete(*, key: str) -> None`.
- Produce `upload_source(*, tender_id: UUID, actor: Actor, filename: str, content: BinaryIO, expected_version: int) -> Document`.
- `Document` exposes immutable checksum, validated format, source revision,
  original name and private storage key; the key is not a public URL.

- [ ] **Step 1: Write upload tests**:

```python
def test_failed_upload_never_becomes_a_source(document_uploads):
    response = document_uploads.interrupted()
    assert response.status_code >= 400
    assert document_uploads.published_document_count() == 0
    assert document_uploads.queued_job_count() == 0
```

  Assert the 25 MiB/20-document boundaries, malformed/unsupported formats,
  unauthorized reads, immutable original bytes/checksum, post-extraction source
  freeze, and cleanup of a specifically identified orphan after database failure.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/api/test_documents.py tests/integration/test_object_storage.py -q`.
- [ ] **Step 3: Implement S3 storage and the source service.**
  Add local S3-compatible storage to Compose; mock AWS in most tests and use the
  local service for storage integration. Stream bounded uploads; do not derive
  storage paths directly from filenames. Reject uploads of generated artifacts
  as extraction sources.
- [ ] **Step 4: GREEN:** repeat Step 2 and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: store immutable tender source documents`.

## Task 8: Source-preserving document parsing

**Dependencies:** Task 7.

**Files:** Create `infrastructure/documents/parsers.py`,
`schemas/source_content.py`, `tests/unit/test_document_parsers.py`,
`tests/fixtures/documents/` synthetic fixtures; modify `pyproject.toml`.
Source modules are under `src/tenderflow/`.

**Interfaces:**
- Produce `parse_document(*, document_id: UUID, content: BinaryIO, format: DocumentFormat, limits: ParseLimits) -> ParsedDocument`.
- `ParsedDocument` contains ordered text/table blocks, flattened table `rows`,
  warnings and `SourceRef`
  locators; `SourceRef` binds document ID, page/paragraph or sheet/row/cells.
  Each row exposes literal string `cells` and a distinct `source` reference.
- Produce `parse_quantity(*, text: str, declared_locale: str | None) -> QuantityParse`:
  optional `Decimal` value plus ambiguity warning, never a guessed locale.

- [ ] **Step 1: Write parser tests**, including the two review-focus cases:

```python
def test_repeated_rows_and_article_zeros_are_preserved(parsed_repeated_rows):
    assert len(parsed_repeated_rows.rows) == 2
    assert parsed_repeated_rows.rows[0].cells[0] == "000123"
    assert parsed_repeated_rows.rows[0].source != parsed_repeated_rows.rows[1].source

def test_ambiguous_quantity_is_not_guessed():
    result = parse_quantity(text="1,250", declared_locale=None)
    assert result.value is None
    assert result.warning == "ambiguous_quantity"
```

  Add focused XLSX/CSV/text-PDF/DOCX examples, multi-sheet and non-ASCII text,
  image-only PDF rejection, expanded-archive/resource limits, and explicit
  unsupported DOC/XLS/macro/encrypted formats.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/unit/test_document_parsers.py -q`.
- [ ] **Step 3: Implement parsers using established format libraries.**
  Keep raw text and coordinates, preserve article identifiers as text, do not
  evaluate formulas, and stop explicitly at configured line/resource limits.
- [ ] **Step 4: GREEN:** repeat Step 2 and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: parse supported tender documents with provenance`.

## Task 9: Price-free LiteLLM boundary and model configuration

**Dependencies:** Tasks 3, 6, 8.

**Files:** Create `schemas/ai.py`, `infrastructure/integrations/litellm.py`,
`services/processing_configuration.py`,
`infrastructure/database/models/configuration.py` under `src/tenderflow/`;
create configuration migrations, `tests/unit/test_ai_boundary.py`,
`tests/api/test_processing_configuration.py`; modify `api/admin.py`,
`services/processing.py`, `config.py`, `pyproject.toml`.

**Interfaces:**
- Produce `PriceFreeProduct` with `product_id`, `internal_article_number`, `title`,
  `description`, optional `selling_unit`, non-price `attributes`, and available
  `evidence`. Technical attribute entries contain name/value/unit, not arbitrary
  upstream objects.
- Produce `RequirementInput`, `DocumentAIInput`, `CandidateAIInput`, and
  `SafeAIInput = DocumentAIInput | CandidateAIInput`, all forbidding extra fields.
- Produce `LiteLLMClient.complete(*, model_alias: str, task: str, input: SafeAIInput, response_model: type[T]) -> T`, with `T` a Pydantic model.
- Produce `get_processing_configuration() -> ProcessingConfiguration` and
  `set_processing_configuration(*, actor: Actor, model_alias: str, expected_version: int) -> ProcessingConfiguration`.

- [ ] **Step 1: Write the boundary/configuration tests**:

```python
def test_price_fields_cannot_enter_candidate_input(valid_price_free_product):
    payload = {**valid_price_free_product, "unit_buying_price": "12.00"}
    with pytest.raises(ValidationError) as error:
        PriceFreeProduct.model_validate(payload)
    assert any(
        entry["loc"] == ("unit_buying_price",) and entry["type"] == "extra_forbidden"
        for entry in error.value.errors()
    )
```

  `valid_price_free_product` is a complete valid dictionary owned by this test module.
  Capture retry requests and assert price fields/values never appear; test
  nested unexpected fields, malformed structured output, invalid source refs,
  administrator-only changes, and unchanged model alias on an already-started run.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/unit/test_ai_boundary.py tests/api/test_processing_configuration.py -q`.
- [ ] **Step 3: Confirm the existing LiteLLM contract, then implement the typed client.**
  Obtain approved endpoint/auth/model/schema capabilities; do not guess remote
  payloads or call a public model as fallback. Filter through positive projections,
  store model/config/prompt versions per run, and sanitize diagnostics.
  No new gateway safety framework or source redaction feature.
- [ ] **Step 4: GREEN:** repeat Step 2 using a fake HTTP transport and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: add price-free LiteLLM processing boundary`.

## Task 10: Lines, requirements, candidate persistence, and correction

**Dependencies:** Tasks 5–6, 8–9.

**Files:** Create `schemas/lines.py`, `domain/requirements.py`, `services/lines.py`,
`infrastructure/database/models/lines.py`, line/requirement/candidate migrations,
`tests/api/test_line_corrections.py`, `tests/integration/test_line_constraints.py`.
Modify `api/tenders.py`.

**Interfaces:**
- Produce ORM records `TenderLine`, `Requirement`, `Candidate` following design §8.
- Produce `correct_line(*, session: Session, line_id: UUID, actor: Actor, expected_version: int, patch: LineCorrection) -> TenderLine`.
- Produce `correct_requirements(*, session: Session, tender_id: UUID, actor: Actor, expected_version: int, changes: RequirementChanges) -> None`.
- Produce `requirements_for_line(*, session: Session, line_id: UUID) -> list[RequirementInput]`.

- [ ] **Step 1: Write correction/constraint tests**:

```python
def test_correction_invalidates_old_requirements_without_erasing_history(line_records):
    line = line_records.with_candidate()
    old_version = line.requirements_version
    corrected = line_records.correct_description(line, "Revised request")
    assert corrected.requirements_version > old_version
    assert line_records.old_candidate_is_current(line) is False
```

  Also assert candidate ownership at the database boundary, resolution/selection
  consistency, source references remaining intact, shared requirements affecting
  all relevant lines, and stale correction preconditions failing.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/api/test_line_corrections.py tests/integration/test_line_constraints.py -q`.
- [ ] **Step 3: Implement records, versioned corrections, and line/requirement routes.**
  Use separate requirements/selection/preparation versions so unrelated line
  work does not become stale. Expose ordered, paginated line views.
- [ ] **Step 4: GREEN:** repeat Step 2 and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: add correctable tender lines and requirements`.

## Task 11: Discovery, evidence-based preselection, and matching jobs

**Dependencies:** Tasks 6, 9–10.

**Files:** Create `infrastructure/integrations/discovery.py`,
`domain/matching.py`, `services/matching.py`, `workers/matching.py`,
`tests/unit/test_preselection.py`, `tests/integration/test_matching_runs.py`.
Modify `workers/tasks.py`, `api/processing.py`.

**Interfaces:**
- Produce `DiscoveryClient.search(*, description: str, requirements: list[RequirementInput]) -> list[PriceFreeProduct]`,
  `lookup(*, article_number: str) -> PriceFreeProduct | None`.
- Produce `choose_preselection(*, requirements: list[RequirementInput], assessments: list[CandidateAssessment]) -> UUID | None`.
- Produce `start_matching_run(*, session: Session, tender_id: UUID, line_ids: list[UUID] | None, actor: Actor, expected_version: int, key: str) -> ProcessingRun`,
  `process_matching_item(*, item_id: UUID) -> None`.
- `CandidateAssessment` carries candidate ID, criterion outcomes, evidence, and promotion explanation.

- [ ] **Step 1: Write preselection/matching tests**; `assessed_candidates` below
  contains one candidate initially meeting every requirement:

```python
@pytest.mark.parametrize("outcome", ["unknown", "does_not_meet"])
def test_every_requirement_must_be_met(requirements, assessed_candidates, outcome):
    assessed_candidates[0].assessments[0].status = outcome
    assert choose_preselection(
        requirements=requirements, assessments=assessed_candidates
    ) is None
```

  Include missing assessment/empty requirement evidence, confidential-price-dependent
  requirements staying unknown, a genuine no-match versus timeout, extra nested
  upstream prices excluded from captured AI requests, and a delayed task unable
  to overwrite a changed selection or skip. A zero-requirement extraction must
  not gain eligibility merely through an empty `all()` check.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/unit/test_preselection.py tests/integration/test_matching_runs.py -q`.
- [ ] **Step 3: Obtain the actual discovery/lookup contract before writing the adapter.**
  Stop this integration if contract access is blocked; do not fabricate URLs,
  response fields, quotas, or product-document access.
- [ ] **Step 4: Implement retrieval, typed assessment and conditional publication.**
  Historical retrieval is added in Task 17, together with approved history
  persistence; do not introduce a placeholder history service or create mappings here.
  Do not pad candidate lists or invent evidence. Distinguish stale from failed results.
- [ ] **Step 5: GREEN:** repeat Step 2 plus AI-boundary tests and common checks.
- [ ] **Step 6: Apply the review gate.** Message: `feat: add evidence-based product matching`.

## Task 12: Extraction orchestration and optional direct matching

**Dependencies:** Tasks 6–11.

**Files:** Create `schemas/extraction.py`, `services/extraction.py`,
`workers/extraction.py`, `tests/integration/test_extraction_runs.py`.
Modify `api/processing.py`, `workers/tasks.py`.

**Interfaces:**
- Produce `ExtractedTender`: validated metadata, shared/line requirements,
  ordered lines with `SourceRef`, and warnings; no authoritative model-created IDs.
- Produce `start_extraction_run(*, session: Session, tender_id: UUID, document_ids: list[UUID], continue_to_matching: bool, actor: Actor, expected_version: int, key: str) -> ProcessingRun`.
- Produce `process_extraction_item(*, item_id: UUID) -> None`.
- Consume `parse_document`, `LiteLLMClient.complete`, and `start_matching_run`.

- [ ] **Step 1: Write extraction orchestration tests**:

```python
def test_direct_path_waits_for_complete_extraction(extraction_runs):
    run = extraction_runs.start(two_documents=True, continue_to_matching=True)
    extraction_runs.complete_first_document(run)
    assert extraction_runs.matching_run_count(run) == 0
    extraction_runs.complete_second_document(run)
    assert extraction_runs.matching_run_count(run) == 1
```

  Assert duplicate completion does not duplicate lines or continuation, failure
  prevents direct continuation, model output references real source locations,
  a corrected value wins over an older task, and 5,001 lines fail explicitly
  rather than publishing a truncated request.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/integration/test_extraction_runs.py -q`.
- [ ] **Step 3: Implement extraction commands, source-bound persistence and completion fan-in.**
  Direct matching retains the initiating actor and warnings. Multiple documents
  contribute their own source rows; do not deduplicate lines by description or
  silently resolve contradictory shared requirements.
- [ ] **Step 4: GREEN:** repeat Step 2, parser tests and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: orchestrate extraction and direct matching`.

## Task 13: Manager choices, exact lookup, deviations, skips, and No-Bid

**Dependencies:** Tasks 10–12.

**Files:** Create `domain/resolution.py`, `workers/lookup.py`,
`tests/api/test_line_resolution.py`, `tests/integration/test_lookup_races.py`;
modify `services/lines.py`, `services/matching.py`, `schemas/lines.py`,
`api/tenders.py`, `workers/tasks.py`; create deviation/skip provenance migrations.

**Interfaces:**
- Produce `select_candidate(*, session: Session, line_id: UUID, candidate_id: UUID, deviations: list[DeviationInput], actor: Actor, expected_version: int) -> TenderLine`.
- Produce `skip_line(*, session: Session, line_id: UUID, actor: Actor, expected_version: int, reason: str | None) -> TenderLine`.
- Produce `skip_unresolved(*, session: Session, tender_id: UUID, actor: Actor, expected_version: int) -> SkipSummary`,
  `reopen_line(*, session: Session, line_id: UUID, actor: Actor, expected_version: int) -> TenderLine`.
- Produce `matching_readiness(*, resolutions: list[str]) -> MatchingReadiness`
  in `domain/resolution.py`: completeness, selected/unresolved counts and No-Bid,
  with an empty list neither complete nor No-Bid.
- Produce `start_candidate_lookup(*, session: Session, line_id: UUID, article_number: str, actor: Actor, expected_version: int, key: str) -> ProcessingRun`.

- [ ] **Step 1: Write resolution tests**:

```python
def test_skip_all_preserves_selections(resolution_api):
    selected, unresolved = resolution_api.two_lines()
    resolution_api.skip_unresolved()
    assert resolution_api.get(selected.id).resolution == "selected"
    assert resolution_api.get(unresolved.id).resolution == "skipped"
```

  Test imperfect manual selection requiring recorded deviations without changing
  assessments, exact lookup adding/selecting a candidate, lookup failure leaving
  the line unresolved, stale lookup preserving a newer choice, all-skipped
  No-Bid/reopening, and zero extracted lines never becoming No-Bid automatically.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/api/test_line_resolution.py tests/integration/test_lookup_races.py -q`.
- [ ] **Step 3: Implement explicit resolution commands and stable deviation IDs.**
  Record manager provenance even when choosing an AI-preselected product.
  Apply skip-all atomically to unresolved lines only. Add the no-empty-offer
  readiness outcome without adding a separate qualification workflow.
- [ ] **Step 4: GREEN:** repeat Step 2, preselection tests and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: add manager-controlled line resolution`.

## Task 14: Offered quantities and customer-aware upstream pricing

**Dependencies:** Task 13; actual pricing contract is required.

**Files:** Create `schemas/pricing.py`, `domain/quantities.py`,
`services/pricing.py`, `infrastructure/integrations/pricing.py`,
`infrastructure/database/models/pricing.py`, `workers/pricing.py`,
pricing migrations, `tests/unit/test_quantities.py`,
`tests/integration/test_pricing_runs.py`.
Modify `services/lines.py`, `schemas/lines.py`, `api/tenders.py`,
`api/processing.py`, `workers/tasks.py`.

**Interfaces:**
- Produce `set_offered_quantity(*, session: Session, line_id: UUID, quantity: Decimal, unit: str, note: str | None, actor: Actor, expected_version: int) -> TenderLine`.
- Produce `PricingClient.quote(*, customer_number: str, lines: list[PriceRequestLine]) -> list[PriceResult]`.
- `PriceRequestLine` carries correlation ID, product, offered quantity/unit;
  `PriceResult` carries correlation, currency/basis, exact prices/net total or safe error.
- Produce `start_pricing_run(*, session: Session, tender_id: UUID, actor: Actor, expected_version: int, key: str, review_request_id: UUID | None = None) -> ProcessingRun`,
  `process_pricing_batch(*, item_ids: list[UUID]) -> None`.

- [ ] **Step 1: Write quantity and pricing tests**:

```python
def test_repeated_product_quantities_are_not_aggregated(pricing_batches):
    result = pricing_batches.quote_same_product(quantities=["2", "7"], reverse_response=True)
    assert pricing_batches.request_quantities == [Decimal("2"), Decimal("7")]
    assert result.first.quantity == Decimal("2")
    assert result.second.quantity == Decimal("7")
```

  Also test 25 pieces versus boxes of 10 requiring manager-entered quantity/note,
  preserved requested quantity, any unresolved line blocking pricing, partial
  results, exact upstream net totals, stale quantity/customer fingerprints,
  and prices never entering the AI path.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/unit/test_quantities.py tests/integration/test_pricing_runs.py -q`.
- [ ] **Step 3: Confirm quantity/currency/correlation, batch, expiry and error semantics.**
  If the upstream cannot correlate duplicate product lines, use separate
  unambiguous calls; do not assume response order or aggregate quantities.
- [ ] **Step 4: Implement deterministic quantity checks and pricing persistence.**
  Use returned net prices; no local discount recreation. Quote writes update
  content, not preparation inputs, so they cannot supersede their own review run.
- [ ] **Step 5: GREEN:** repeat Step 2 and common checks.
- [ ] **Step 6: Apply the review gate.** Message: `feat: add explicit offer quantities and upstream pricing`.

## Task 15: Source-bound template mappings and manual-transfer mode

**Dependencies:** Tasks 8–10, 13.

**Files:** Create `schemas/offers.py`, `services/template_mapping.py`,
`infrastructure/database/models/templates.py`, template migrations,
`api/offers.py`, `tests/api/test_template_mappings.py`.

**Interfaces:**
- Produce `OutputMode = Literal["spreadsheet", "manual_transfer"]`.
- Produce `TemplateMapping` with target document/checksum, row/cell destinations,
  allowed buyer fields, version and digest.
- Produce `set_template_mapping(*, session: Session, tender_id: UUID, mapping: TemplateMappingInput, actor: Actor, expected_version: int) -> TemplateMapping`.
- Produce `validate_output_mode(*, tender_id: UUID, mode: OutputMode) -> None`.

- [ ] **Step 1: Write mapping/mode tests**:

```python
def test_mapping_edit_records_contributor(template_mapping_api, other_manager):
    template_mapping_api.update(actor=other_manager)
    assert template_mapping_api.is_contributor(other_manager.id)

def test_manual_transfer_does_not_require_spreadsheet_mapping(pdf_tender):
    validate_output_mode(tender_id=pdf_tender.id, mode="manual_transfer")
```

  Add repeated-table row identity, checksum mismatch, destination overlap,
  unintended formula overwrite, forbidden buying-price fields, and inability
  to silently bypass an invalid spreadsheet mapping through an automatic fallback.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/api/test_template_mappings.py -q`.
- [ ] **Step 3: Implement deterministic mapping validation and read/update routes.**
  AI may propose semantics from price-free source content; users can correct
  the mapping. Preserve original coordinates. Manual-transfer mode explicitly
  represents no supported buyer spreadsheet, not an invented buyer export.
- [ ] **Step 4: GREEN:** repeat Step 2 and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: add reviewed buyer-template mappings`.

## Task 16: Fresh-pricing review cycles and immutable review digests

**Dependencies:** Tasks 14–15.

**Files:** Create `services/reviews.py`,
`infrastructure/database/models/reviews.py`, review-request migrations,
`tests/integration/test_review_cycles.py`; modify `schemas/offers.py`,
`api/offers.py`, `services/pricing.py`.

**Interfaces:**
- Produce `request_review(*, session: Session, tender_id: UUID, mode: OutputMode, actor: Actor, expected_version: int, key: str) -> ReviewRequest`.
- Produce `get_review(*, session: Session, tender_id: UUID, actor: Actor) -> ReviewView`.
- Produce `finalize_review_pricing(*, session: Session, review_request_id: UUID) -> None`.
- `ReviewView` exposes state, retrieval times, content/mapping digests, output
  mode, deviations and readiness; it contains internal prices and is never AI input.

- [ ] **Step 1: Write review-cycle tests**:

```python
def test_review_does_not_reuse_previous_prices(review_cycles):
    review_cycles.complete_initial_pricing()
    review = review_cycles.request()
    assert review.state == "pricing"
    assert review_cycles.fresh_request_count(review) == 1
    assert review_cycles.get().ready is False
```

  Assert edits during pricing supersede the review, sibling quote writes do not
  supersede it, failures prevent readiness, retries finish the same current cycle,
  mode/mapping/deviation edits change the digest, and GET never starts pricing.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/integration/test_review_cycles.py -q`.
- [ ] **Step 3: Implement fresh-run linkage, deterministic canonical digests and read-only review.**
  The preparer requests pricing; the reviewer merely reads. Check explicit
  upstream expiry, but add no arbitrary maximum elapsed age.
- [ ] **Step 4: GREEN:** repeat Step 2, pricing-run tests and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: require fresh pricing for final review`.

## Task 17: Independent approval, invalidation, and approved article history

**Dependencies:** Task 16.

**Files:** Create `domain/approval.py`, `services/approvals.py`,
`infrastructure/database/models/approvals.py`, approval/snapshot/history migrations,
`tests/integration/test_independent_approval.py`,
`tests/integration/test_approval_races.py`, `tests/integration/test_article_history.py`.
Modify `schemas/offers.py`, `api/offers.py`, `services/tenders.py`,
`services/matching.py`.

**Interfaces:**
- Produce `approve_offer(*, session: Session, tender_id: UUID, actor: Actor, command: ApprovalInput, expected_version: int, key: str) -> Approval`.
- `ApprovalInput` contains review digest, accepted deviation IDs, and either
  mapping digest or explicit manual-transfer acknowledgment, never approver ID.
- Produce `reject_review(*, session: Session, tender_id: UUID, actor: Actor, note: str, expected_version: int) -> None`.
- Produce `approved_article_products(*, session: Session, buyer_id: UUID, buyer_article: str) -> list[ApprovedProductReference]` for Task 11.

- [ ] **Step 1: Write independent approval tests**:

```python
def test_reading_does_not_disqualify_but_preparing_does(approval_flow):
    approval_flow.read_review_as_independent_manager()
    assert approval_flow.reviewer_is_contributor() is False
    approval_flow.edit_mapping_as_reviewer()
    assert approval_flow.try_approve_as_reviewer().status_code == 403
```

  Test local-preparer/Entra-approver identity equivalence, admin non-bypass,
  acknowledgment of every active deviation, manual-transfer acknowledgment,
  all-skipped no-empty-offer, and a two-connection approval/edit race.
  Verify approved mappings are buyer-scoped and absent before approval.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/integration/test_independent_approval.py tests/integration/test_approval_races.py tests/integration/test_article_history.py -q`.
- [ ] **Step 3: Implement transactional readiness/identity rechecks and immutable snapshots.**
  Lock consistently with every mutator. Extend the common preparation-change
  helper to invalidate current review/approval while retaining history.
  Publish approved article relationships once; refresh products before future reuse.
  Test that a known buyer article seeds current exact-lookup candidates alongside
  alternatives, without accepting stale history as proof of suitability.
- [ ] **Step 4: GREEN:** repeat Step 2, Entra tests, review tests and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: enforce independent offer approval`.

## Task 18: Internal worksheets and approved buyer-template exports

**Dependencies:** Task 17.

**Files:** Create `services/exports.py`, `infrastructure/documents/exports.py`,
`infrastructure/database/models/exports.py`, `workers/exports.py`, export migrations,
`tests/integration/test_exports.py`, `tests/api/test_export_access.py`.
Modify `schemas/offers.py`, `api/offers.py`, `workers/tasks.py`, `pyproject.toml`.

**Interfaces:**
- Produce `request_export(*, session: Session, tender_id: UUID, audience: Literal["internal", "buyer"], actor: Actor, expected_version: int, key: str) -> ProcessingRun`.
- Produce `build_internal_worksheet(*, snapshot: InternalOffer, draft: bool) -> BinaryIO`.
- Produce `fill_buyer_template(*, original: BinaryIO, mapping: TemplateMapping, offer: BuyerOffer) -> BinaryIO`.
- Produce `download_export(*, export_id: UUID, actor: Actor) -> ContextManager[BinaryIO]`.
- `BuyerOffer` is a separate allowlisted projection without buying prices/internal notes.
  Define `InternalOffer` and `BuyerOffer` in `schemas/offers.py`; neither is an AI input.

- [ ] **Step 1: Write export tests**:

```python
def test_buyer_export_changes_only_reviewed_cells(export_flow):
    artifact = export_flow.approved_buyer_export()
    assert artifact.unmodified_cells_equal_original()
    assert artifact.buying_price_values() == []
    assert artifact.skipped_line_response_cells_are_blank()
```

  Add draft internal marking, approved manual-transfer worksheet/no buyer export,
  CSV injection-like product names treated literally, XLSX formulas untouched
  outside mapped cells, stale generation not published, and invalidated approval
  preventing final download. Test storage objects do not overwrite originals.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/integration/test_exports.py tests/api/test_export_access.py -q`.
- [ ] **Step 3: Implement snapshot-based generation and authenticated streaming downloads.**
  Never re-fetch catalog/prices during final generation. Check approval both
  before publication and at download. Fail explicitly for unsupported preservation
  rather than silently dropping workbook content.
- [ ] **Step 4: GREEN:** repeat Step 2, storage tests and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: export approved offers without internal costs`.

## Task 19: Manual submission records and deadline warnings

**Dependencies:** Tasks 17–18.

**Files:** Create `services/submissions.py`,
`domain/deadlines.py`,
`infrastructure/database/models/submissions.py`, submission migrations,
`tests/api/test_submissions.py`; modify `api/offers.py`, `schemas/offers.py`.

**Interfaces:**
- Produce `record_submission(*, session: Session, tender_id: UUID, actor: Actor, approved_snapshot_id: UUID, export_id: UUID | None, submitted_at: datetime, external_reference: str | None, key: str) -> Submission`.
- Produce `deadline_warnings(*, deadline: datetime | None, now: datetime) -> list[str]` in `domain/deadlines.py`.

- [ ] **Step 1: Write submission tests**:

```python
def test_late_manual_transfer_submission_is_recorded(submission_flow):
    response = submission_flow.record_after_deadline(manual_transfer=True)
    assert response.status_code == 201
    assert "submission_deadline_passed" in response.json()["warnings"]
    assert response.json()["export_id"] is None
```

  Also assert repeat idempotency does not duplicate records, snapshot/export
  ownership is checked, spreadsheet submission uses its approved buyer artifact,
  and submission recording never reprices or changes approved content.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/api/test_submissions.py -q`.
- [ ] **Step 3: Implement authorized manual records and read-only history.**
  Bind manual-transfer submissions to the approved snapshot without requiring a
  nonexistent buyer file. Preserve reported submission time and recorder identity.
- [ ] **Step 4: GREEN:** repeat Step 2 and common checks.
- [ ] **Step 5: Apply the review gate.** Message: `feat: record manual tender submissions`.

## Task 20: Operational visibility and end-to-end recovery acceptance

**Dependencies:** Tasks 1–19.

**Files:** Create `src/tenderflow/observability.py`,
`tests/integration/test_end_to_end.py`, `tests/integration/test_capacity_recovery.py`,
`tests/unit/test_safe_telemetry.py`; modify `main.py`, worker registration and
external integrations for correlation/metrics, plus `pyproject.toml`.

**Interfaces:**
- Produce `configure_observability(*, settings: Settings) -> None` and safe
  request/run correlation propagation.
- Expose protected `/metrics`; no user/customer/tender IDs as metric labels.
- Keep public health APIs and domain service signatures unchanged.

- [ ] **Step 1: Write end-to-end and telemetry tests**:

```python
def test_restart_preserves_thousand_line_work(capacity_run):
    capacity_run.start(line_count=1000)
    capacity_run.make_manual_selection()
    capacity_run.restart_worker()
    capacity_run.finish()
    assert capacity_run.lost_selection_count == 0
    assert capacity_run.duplicate_domain_result_count == 0
```

  Run this only against isolated synthetic services/processes owned by the test.
  Capture logs/metrics on a pricing exception and AI retry; assert secret/price
  values and document bodies are absent. Test upload-to-submission in spreadsheet
  and manual-transfer modes, with different preparer and approver identities.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/integration/test_end_to_end.py tests/integration/test_capacity_recovery.py tests/unit/test_safe_telemetry.py -q`.
- [ ] **Step 3: Add the design's bounded operational/business metrics and sanitized logging.**
  Record queue age, failures, retries, stalled jobs and offered-line coverage;
  distinguish preselection coverage from actual offer coverage and accuracy.
  No production service calls or unsupported completion-time promises.
- [ ] **Step 4: GREEN:** repeat Step 2, then the full existing pytest suite and
  common checks because this is the release integration boundary.
- [ ] **Step 5: Apply the review gate.** Message: `feat: add processing observability and recovery acceptance`.

## Task 21: Portable containers and AWS-native deployment

**Dependencies:** Task 20; explicit target environment and operational policies.

**Files:** Create `Dockerfile`, `.dockerignore`, `deploy/aws/template.yaml`,
`deploy/aws/parameters.example.json`, `docs/OPERATIONS.md`,
`tests/integration/test_container_readiness.py`, `tests/unit/test_aws_template.py`.
Modify `compose.yaml`, `pyproject.toml`.

**Interfaces:**
- One application image provides API and worker entry points.
- CloudFormation parameters reference existing VPC/subnets, TLS certificate,
  image tag/digest, secret references and bounded worker scaling.
- Infrastructure supplies environment settings already consumed by `Settings`;
  no AWS SDK calls enter domain or application services.

- [ ] **Step 1: Write the container readiness test** to start only test-owned
  Compose resources, verify health/migrations and a queued job, and then clean
  up those exact resources. Add infrastructure lint for private S3, encrypted
  RDS, separate least-privilege API/worker roles, TLS and no plaintext secrets.
- [ ] **Step 2: RED:** run `.venv-mvp/bin/python -m pytest tests/integration/test_container_readiness.py tests/unit/test_aws_template.py -q`.
  Expect missing image/entry points rather than attempting an AWS deployment.
- [ ] **Step 3: Implement containers and AWS CloudFormation deployment files.**
  CloudFormation is the plan's proposed native AWS IaC format; it adds no
  cross-cloud framework. Require existing network connectivity to the private
  LiteLLM/upstream services, bounded replicas/connections and explicit operator
  migration steps. Add `cfn-lint` as optional infrastructure tooling.
- [ ] **Step 4: Document operations and release prerequisites.**
  Include local setup, Entra API registration, account bootstrap, secret supply,
  source-upload confidentiality assumption, stale-job recovery, versioned
  deployment, backup/restore and rollback. Obtain retention, recovery/SLO targets,
  actual upstream contracts and authorized evaluation data before production.
- [ ] **Step 5: GREEN:** repeat Step 2, run
  `.venv-mvp/bin/python -m pytest tests/unit/test_aws_template.py -q`,
  `.venv-mvp/bin/cfn-lint deploy/aws/template.yaml`, and common checks.
  Validate a deployment only in an explicitly authorized non-production AWS
  account; template lint is not evidence that a cloud deployment works.
- [ ] **Step 6: Apply the review gate.** Message: `build: add portable containers and AWS deployment`.

## Specification coverage

| Design requirement | Owning tasks |
|---|---|
| FR-01–02: accounts, administration, local/Entra identity | 3–4 |
| FR-03: buyer/tender registration | 5 |
| FR-04: immutable documents | 7 |
| FR-05–07: extraction, correction, direct matching | 8–12 |
| FR-08–09: candidate discovery and strict preselection | 9, 11 |
| FR-10–12: manager overrides, deviations, skips, completion gate | 10, 13–14 |
| FR-13–14: upstream prices, fresh review and quantity decisions | 14, 16 |
| FR-15: internal/buyer outputs and manual-transfer path | 15, 18 |
| FR-16–17: independent approval and invalidation | 5, 15–18 |
| FR-18: submission and deadlines | 5, 19 |
| FR-19: approved buyer article history | 11, 17 |
| FR-20: durable progress, partial failure and retry | 2, 6, 11–12, 14, 18 |
| FR-21–22: amendments and No-Bid | 5, 13 |
| Transactional queue and crash recovery | 2, 6, 20 |
| Per-input versions, duplicate completion, approval/export races | 5–6, 10–14, 16–18 |
| Price-free AI, model/config snapshots, safe diagnostics | 9, 11, 20 |
| Limits, source references, format fidelity and no formula execution | 7–8, 10, 15, 18 |
| Access, private storage, secrets, TLS and cloud portability | 3–4, 7, 18, 21 |
| Observability, capacity and production release prerequisites | 20–21 |

## Plan self-review and handoff

Self-review completed on 2026-11-15: specification coverage is mapped above,
tasks name their files/interfaces and RED/GREEN checks, cross-task names were
checked, and all five Review Focus cases have owning tests. Test snippets describe
behavior rather than implementation bodies. The plan remains comparable in
size to the design; unresolved service contracts are explicit task prerequisites,
not invented interfaces to remote systems. This was a direct self-review.

The user approved the plan and selected native execution on 2026-11-15.
Use `superpowers:executing-plans` when instructed to begin implementation.
A separate agent reviews each feature; the user's review/commit gates remain
mandatory. No automatic task pipeline or workflow run is requested.
