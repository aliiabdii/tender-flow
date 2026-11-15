# TenderFlow: Technical Design

Status: Design approved by the user on 2026-11-15. Implementation plan pending review.

Business source of truth: [BUSINESS_REQUIREMENTS.md](BUSINESS_REQUIREMENTS.md).
This document records the approved technical realization of the agreed MVP.
Labels such as "proposal" retain the origin of engineering recommendations
accepted with the design. Approval does not authorize automatic implementation,
dependency installation, infrastructure deployment, or commits; the implementation
plan and feature-by-feature review gates still apply.

## 1. Intent and boundaries

Build a backend-only API that reduces manual extraction, product search, and
offer preparation for tenders containing approximately 1,000 lines.

The scope is one supplier organization per deployment. Buyers, products, and
requirements are not tied to an industry. The application orchestrates existing
discovery, pricing, and LiteLLM services rather than replacing them.

The principal correctness requirements are:

- Never confuse an AI assessment with human approval.
- Never send application-held confidential prices to AI.
- Never finalize an offer containing an unresolved line.
- Never let a preparer approve their own tender through another login method.
- Never export content different from what was approved as though it were approved.
- Preserve the buyer's original template outside approved response cells.

Full automatic intake, OCR, task management, multi-organization tenancy,
complex approval chains, and submission integrations remain excluded.

## 2. Decisions and alternatives

| Area | Decision and provenance |
|---|---|
| Application shape | Agreed: one codebase with separately deployed API and worker processes. |
| Durable processing | Agreed: established PostgreSQL-backed queue; no Redis requirement. |
| Queue library | Proposal: Procrastinate, subject to a transactional integration test. |
| API/database | Proposal retained from INIT: Python 3.12+, FastAPI, Pydantic 2, SQLAlchemy 2, PostgreSQL, Alembic. |
| Authentication | Agreed: local accounts plus Microsoft Entra, explicitly linked by administrators. |
| User provisioning | Agreed: administrator-managed; no public signup or just-in-time creation. |
| AI gateway | Agreed: existing LiteLLM, with administrator-selected model. |
| Cloud strategy | Agreed: AWS-native initial deployment; portable application, not identical cross-cloud infrastructure. |
| AWS services | Proposal: ECS/Fargate, RDS PostgreSQL, private S3 storage. |
| Local development | Proposal: Docker Compose with API, worker, PostgreSQL, and S3-compatible local storage. |
| Template review | Agreed: field mapping is reviewed as part of every final approval. |
| Pricing freshness | Agreed: a fresh pricing run before each final review cycle; approved snapshot prices are frozen. |
| Buyer amendments | Agreed: new linked tender; no in-place source reconciliation in the MVP. |
| All lines skipped | Agreed: record No-Bid, generate no empty offer, and permit reopening. |
| No buyer spreadsheet | Agreed: approve the internal worksheet with explicit manual-transfer acknowledgment; mapping is not applicable and no buyer file is generated. |

### Background-processing approaches considered

1. **PostgreSQL-backed queue:** selected to reuse existing database infrastructure
   and avoid operating another broker. Queue load and connection use share
   database capacity with the application.
2. **Celery with Redis:** mature worker ecosystem, but adds a broker and its
   operational concerns; not selected.

In-process FastAPI background tasks are unsuitable for durable tender processing.
Microservices add deployment and consistency complexity without an MVP need.

### Cloud approaches considered

1. **Portable application with AWS-native deployment:** selected. Containers,
   PostgreSQL, and separated infrastructure integrations support later migration,
   but cloud configuration and storage integration can change.
2. **Common Kubernetes deployment:** not selected because the operational cost is
   not justified by current portability needs. Kubernetes would not eliminate
   differences in storage, networking, or identity.

## 3. Functional requirements

| ID | Requirement |
|---|---|
| FR-01 | Administrators provision users, assign permissions, link Entra identities, and disable access. |
| FR-02 | Authenticate local accounts or mapped Entra identities as one internal user. |
| FR-03 | Register a tender against a buyer and pricing customer number. |
| FR-04 | Upload immutable source documents and retain their original bytes and checksums. |
| FR-05 | Extract tender information, requirements, and lines with source references and warnings. |
| FR-06 | Permit extraction correction without a separate extraction approval. |
| FR-07 | Support separate extraction/matching and an explicit user-requested direct continuation. |
| FR-08 | Discover and assess candidates against shared and line-specific requirements. |
| FR-09 | Preselect only a candidate assessed as meeting every requirement; explain promotion. |
| FR-10 | Allow alternative selection, exact article lookup, and manual selection with deviations. |
| FR-11 | Record individual skips and bulk-skip unresolved lines without changing selected lines. |
| FR-12 | Prevent continuation beyond matching while any line is unresolved. |
| FR-13 | Retrieve buying/selling prices and net totals from the existing pricing service, freshly before final review. |
| FR-14 | Preserve requested/offered quantities and units; require resolution of quantity discrepancies. |
| FR-15 | Produce an internal worksheet and fill buyer XLSX/CSV response fields without internal costs. |
| FR-16 | Require independent approval of current offer content, deviations, and template mapping. |
| FR-17 | Invalidate approval after offer-affecting changes. |
| FR-18 | Record manual submission and warn, rather than block, after deadlines. |
| FR-19 | Learn buyer article relationships from approved offers. |
| FR-20 | Expose durable processing progress, partial failures, and retryable work through the API. |
| FR-21 | Link amended requests to prior tenders without transferring approval or automatically reconciling preparation. |
| FR-22 | Record No-Bid when all lines are explicitly skipped and permit reopening without generating an empty offer. |

## 4. Non-functional requirements

### Correctness and recovery

- Processing must survive API or worker restarts.
- Assume a task can execute more than once. Results must be idempotent and
  conditional on current input versions; do not promise exactly-once execution.
- Persist domain changes and job creation in the same PostgreSQL transaction.
  A successful API response must not leave committed work without an enqueued job.
- Never hold database transactions open while calling external services.
- Distinguish no-match results from service failures.
- Persist progress per document or line so one failure does not erase successes.
- Reject stale mutations rather than silently overwrite another user's work.

### Confidentiality and integrity

- Use TLS, private object storage, least-privilege service access, and encryption
  at rest through deployment services.
- AI request models explicitly allow permitted source content and product
  attributes. Never serialize complete pricing, offer, or internal worksheet
  objects into prompts.
- Apply the same boundary to retries, follow-up prompts, and AI diagnostics.
- Logs, traces, and metric labels must not contain credentials, document bodies,
  prompts, buying/selling prices, or customer-specific prices.
- Keep original files immutable; generated files are separate objects.
- Do not execute uploaded macros or evaluate spreadsheet formulas.
- Validate AI output structure and source references before writing domain data.
  Valid JSON alone is not evidence that an assessment is factually correct.

### Capacity and responsiveness

Proposal: benchmark a 1,000-line tender with realistic external service quotas
before setting an end-to-end processing SLO. Do not invent a completion-time
guarantee while discovery/LLM rate limits are unknown.

Use pagination and bounded worker concurrency. Worker replica caps and
per-worker limits must collectively respect upstream capacity; a per-process
semaphore alone does not establish a global rate limit.

Provisional, configurable upload limits for review: 25 MiB per file, 20 documents
per tender, and 5,000 extracted lines per tender. Reject excess explicitly;
never silently truncate. Archive expansion and parsing resource limits must
also be bounded.

### Operations

Expose liveness, readiness, structured logs, operational metrics, and business
metrics. Back up PostgreSQL and object storage under an agreed recovery policy.
Retention, recovery objectives, and availability targets remain production
readiness decisions, not assumed business commitments.

## 5. Runtime architecture

| Component | Responsibilities |
|---|---|
| FastAPI service | Authentication, authorization, validation, short transactions, processing commands, review and downloads. |
| Worker service | Extraction, matching, exact lookup, pricing, and artifact generation. |
| PostgreSQL | Domain records, identities, audit history, processing progress, and Procrastinate queue schema. |
| Object storage | Immutable source files and separate generated artifacts. |
| LiteLLM | Model access for extraction, requirement interpretation, and candidate assessment. |
| Discovery API | Semantic candidates and exact product lookup. |
| Pricing API | Customer-aware prices and net totals. |
| Microsoft Entra | External user authentication; not application authorization. |

Both processes use the same domain/application services. API modules do not
contain business rules; workers do not bypass them. Infrastructure clients are
small integrations, not a generic plugin framework.

On AWS, run API and workers as separate ECS services, with RDS and S3 accessible
through appropriate private networking and service permissions. The API is
exposed through a TLS load balancer. Supply secrets through the platform's
secret facilities; local configuration comes from environment variables.

Keep AWS SDK usage inside storage/configuration integration code. Moving clouds
can require a different storage implementation and deployment configuration.
Do not introduce a cloud orchestration abstraction.

Downloads are authenticated API operations streaming from private storage.
This keeps authorization checks at download time; already downloaded files
cannot be recalled after approval is invalidated.

## 6. Identity, authentication, and authorization

### Canonical identity

Each human has one administrator-managed internal user record. Local credentials
and explicitly linked Entra identities point to that record.

Identify Entra identities by validated tenant ID and object ID, not email or
display name. Accept only the configured tenant and tokens issued for the
TenderFlow API with the required delegated scope. Validate signature, issuer,
audience, lifetime, and token type using established libraries and provider keys.

Proposal: API clients obtain Entra access tokens using Microsoft's supported
authorization-code/PKCE flow; the API validates those tokens directly. No
frontend or custom Microsoft login page is built. OpenAPI documentation can
describe the Entra authorization-code flow.

For local login, issue short-lived application access tokens and rotating,
revocable refresh tokens. Store only password hashes and refresh-token hashes.
Use established Argon2id and JWT libraries rather than custom cryptography.
Local tokens have a distinct, configured issuer and audience; unverified token
claims never establish trust or choose arbitrary signing-key endpoints.

Check internal account activation and current permissions on every request,
including requests authenticated by Entra. Disabling a user or unlinking an
identity must prevent subsequent access without waiting for token expiry.

Proposal: initial local access lifetime 10 minutes and refresh lifetime 7 days,
configurable. Administrator-mediated activation and password reset use expiring,
single-use credentials; reset revokes local sessions. Email delivery and public
password recovery are not prerequisites for this internal MVP.

Bootstrap the first administrator through an operational command using supplied
configuration, not a default account or committed password. Audit provisioning,
linking, role changes, resets, and disabling.

Administrators must avoid creating separate internal users for the same person.
Software cannot prove distinct humans merely from distinct accounts; account
governance remains necessary for meaningful separation of duties.

### Permission model

Proposal: explicit roles `admin`, `tender_manager`, and `approver`; a user can
hold several. Approval requires manager and approver permissions plus the
per-tender independence check. Being an administrator does not bypass that check.

Tender managers may manage preparation and record submissions. Approvers may
read preparation data and approve/reject review without becoming preparers merely
by viewing it.

Record a human as a contributor when they create a tender, upload/change sources,
correct requirements, initiate extraction/matching, change selections or skips,
resolve quantities/deviations, request pricing, or edit the export mapping.
Workers retain the initiating human actor, not a fictitious approving identity.

Reading, reviewing, and the approval action itself are not preparation actions.
If an approver edits preparation content, they become a contributor and another
eligible person must approve.

## 7. Processing workflow and state

Avoid one large status enum attempting to represent extraction, matching,
pricing, and approval simultaneously. Expose lifecycle status together with
derived readiness and processing-run states.

### Extraction

1. Store the uploaded original and its checksum; do not enqueue incomplete uploads.
2. Parse supported content with source coordinates: sheet/cell or row for
   spreadsheets, page/text location for PDFs, paragraph/table location for DOCX.
3. Use LiteLLM where semantic interpretation is needed.
4. Validate output and references, retaining warnings rather than inventing values.
5. Persist structured lines and requirements against an immutable source revision.

Proposal: support `.xlsx`, `.csv`, text `.pdf`, and `.docx` initially. Legacy
`.doc`, `.xls`, macro-enabled files, encrypted documents, and image-only PDFs
receive explicit unsupported-format errors, not misleading empty extraction.

Direct continuation is an explicit option on the extraction command. After
successful extraction, enqueue matching with the same actor. Do not automatically
proceed after a failed or incomplete extraction; expose the failure/warnings.
Normal successful extraction can still include field-level uncertainty.

Managers can correct extracted information. Such changes invalidate affected
assessments, pricing, and approval; old records remain available as history.
Duplicate files are not silently merged into existing lines.

Once extraction has produced lines, source-set changes use a new linked tender
in the MVP. Register amended buyer files using `supersedes_tender_id`, retaining
the old request and its history. Do not copy approval, prices, or selections
automatically. Correcting an extracted field does not require a new tender.
Retrying failed extraction work must not duplicate already published lines.

### Matching and resolution

For each line, combine shared and line-specific requirements. Use current
exact-lookup information for historical buyer article relationships, and semantic
discovery for candidate retrieval and alternatives as needed.

Candidate assessment records each requirement as `meets`, `does_not_meet`, or
`unknown`, with available evidence and the assessment's provenance. Missing
evidence is unknown. A score or model confidence cannot substitute for evidence.

Requirements depending on confidential prices remain unknown during AI matching.
Do not disclose prices to resolve them. After product selection and pricing,
evaluate such constraints deterministically or through human review. They must
not be silently omitted from the all-requirements preselection check.

Preselection is allowed only if every applicable requirement is assessed as met.
Otherwise retain suggestions but leave the line unresolved.

Line resolution has three states:

- `unresolved`: no selected candidate and no explicit skip.
- `selected`: a candidate is selected by AI or a manager.
- `skipped`: a manager explicitly excluded the line.

Keep the last matching outcome separately, so a failed service call and a
successful search with no candidates remain distinguishable.

Managers may select imperfect matches with recorded deviations, or introduce a
product using exact article lookup. Preserve failed/unknown assessments after
selection. A lookup failure does not create a product or resolve the line.

Bulk skip changes only lines unresolved at the time of the transaction. Preserve
selected and already skipped lines. Record actor and time for each changed line.

Continuation to pricing checks that every line is selected or explicitly skipped.
This is a completeness check, not another human approval workflow.

If a tender with extracted lines has every line explicitly skipped, record
`no_bid`; do not request prices or generate an empty offer. Reopening a line
returns the tender to preparation and makes that line unresolved. Zero extracted
lines or an extraction failure cannot automatically establish No-Bid.

### Pricing

Resolve offered quantity/unit before pricing; never silently round packages.
Send selected product identifiers, offered quantities in the service's agreed
basis, and the tender's pricing customer number.

Store the returned unit buying price, unit selling price, and total net amount
with currency, quantity basis, retrieval time, and input fingerprint. Use decimal
arithmetic and exact decimal strings over the API, never binary floating point.

Do not silently aggregate repeated products across tender lines: this can change
quantity-tier pricing. Confirm upstream correlation and quantity semantics first.
Incomplete pricing leaves affected lines unready; completed lines remain usable.

### Offer review and approval

The preparer explicitly requests final review. This starts a new pricing run
for every included line; cached quotes or an earlier pricing run cannot satisfy
this review cycle. If any included line fails pricing, the review is not ready.
Retries may finish failed items from the same still-current review cycle.

Persist a review request tying the fresh pricing run to the preparation versions
and template mapping being reviewed. Offer-affecting edits supersede a pending
or ready review; the next review request requires another fresh pricing run.
This prevents a stale background response from making changed content reviewable.
The reviewer reads the completed review without triggering pricing or becoming
a preparer.

Build a review representation containing requested/offered line data, selected
products, price results, deviations, skips, and the proposed template mapping.
Return a content revision and digest covering everything that affects the offer.

Approval requires:

- An eligible, active, independent manager.
- Every line selected or explicitly skipped.
- At least one included line; all-skipped tenders follow No-Bid instead.
- Resolved quantities and current pricing for every included line.
- A ready review request with the fresh pricing results for that review cycle.
- Recorded deviations for manually selected imperfect matches.
- Review of deviations and, in spreadsheet mode, confirmation of the exact
  template mapping.
- For the manual-transfer path without a supported buyer spreadsheet, explicit
  acknowledgment of manual transfer replaces mapping confirmation.
- A review digest matching the current content.

Within one transaction, lock the tender, recheck eligibility and revision,
persist an immutable offer snapshot and approval, and learn buyer article
relationships. Other mutations use the same tender-level locking convention.
An approval and a simultaneous edit must not both succeed against old content.

Editing offer-affecting data increments its content revision and invalidates
approval. Approval of a revised offer requires another eligible review.
Reviewer acceptance of existing deviations belongs to approval, not to an edit
that would itself make the reviewer a preparer.

The chosen freshness policy is event-based, not a maximum elapsed age: prices
can age while a review waits. Show retrieval time and honor any explicit upstream
expiry. No additional timeout is assumed. Do not automatically reprice at export
or submission; a deliberate repricing invalidates the approval and requires a
new review cycle.

Review requests and approved snapshots include an explicit output mode:
`spreadsheet` or `manual_transfer`. Spreadsheet mode requires its source-bound
mapping and mapping digest. Manual-transfer mode applies when no supported buyer
spreadsheet is available; mapping is explicitly not applicable and the reviewer
must acknowledge manual transfer. It is not a silent fallback for an invalid
spreadsheet mapping.

### Export and submission

Internal worksheet exports may be created as clearly marked drafts before
approval. Proposal: XLSX for the internal worksheet.

Final buyer exports use the immutable approved snapshot and approved mapping,
not live catalog fields or current price lookups. Reject stale export requests.
Recheck approval before publishing an asynchronously generated final artifact
and before allowing a final-artifact download.

Manual-transfer tenders can export an approved internal worksheet, but not a
buyer-facing artifact. Recording manual submission references that approved
snapshot; it does not require a nonexistent system-generated buyer file.
TenderFlow cannot verify the contents of a file assembled outside the application.

Template mappings bind source checksum, sheet/row/cell references, and allowed
response fields. Detect ambiguous destinations, overlapping mappings, or
unintended formula overwrites and require correction before approval.
Read from a copy of the buyer original; never use the internal worksheet as the
buyer export source.

Write only approved response cells. Do not write buying prices, internal notes,
or confidential metadata. Treat inserted text as data rather than executable
spreadsheet formulas. Preserve untouched workbook content within supported
format capabilities; fail explicitly if safe preservation cannot be assured.

Manual submission records the approved offer/artifact reference, submitting
user, reported submission time, and optional external reference. Recording a
submission must not silently mutate or reprice that offer.
Deadline warnings do not prevent any otherwise authorized action.

## 8. Persistence model

Use UUID primary keys and timezone-aware timestamps. Monetary amounts and
quantities use PostgreSQL `numeric`/Python `Decimal`; precision limits are
validated against the actual upstream contracts. Store normalized relational
fields for identity, ownership, selection, money, and state. Use validated JSONB
for variable requirements, evidence, locators, mappings, and immutable snapshots.

### Identity

| Table | Principal fields and constraints |
|---|---|
| `users` | `id`, normalized unique login name, display name, active flag, timestamps. |
| `user_roles` | `user_id`, validated role; composite primary key. |
| `local_credentials` | Unique `user_id`, password hash, password-change time, activation/reset state. |
| `external_identities` | `user_id`, provider, tenant ID, object ID; unique provider/tenant/object tuple. |
| `refresh_sessions` | `user_id`, unique token hash, expiry, revocation and rotation-family identifiers. |

### Tender preparation

| Table | Principal fields and constraints |
|---|---|
| `buyers` | `id`, name, customer number; no assumption that a display name is unique. |
| `tenders` | `id`, `buyer_id`, optional `supersedes_tender_id`, pricing customer-number snapshot, title, deadline, creator, source/content revisions, lifecycle status. |
| `tender_contributors` | `tender_id`, `user_id`, first contribution; unique pair, retained after reassignment. |
| `documents` | `id`, `tender_id`, source revision, original filename, validated type, checksum, private object key, processing state. |
| `tender_lines` | `id`, `tender_id`, order/reference, description/context, requested and offered quantities/units, optional buyer article, resolution, selected candidate, versions, notes and skip actor/time. |
| `requirements` | `id`, `tender_id`, optional `line_id` for line-specific requirements, dimension, requirement text/value, source locator. |
| `candidates` | `id`, `line_id`, matching run, external product ID, article/title/unit snapshot, selection eligibility, promotion reason, validated assessments/evidence. |
| `line_deviations` | `id`, `line_id`, candidate/selection revision, applicable requirement reference, note, author and time; stable IDs for independent review. |
| `buyer_article_mappings` | Buyer, buyer article, product ID, approved offer/line provenance and date; retain historical approved relationships. |
| `price_quotes` | `id`, `line_id`, input fingerprint, product/quantity/unit/customer basis, currency, unit costs/prices, net total, retrieval time. Immutable result records. |
| `template_mappings` | `id`, `tender_id`, target document/checksum, version, validated response-cell definitions, creator. |

Requirements apply either to the whole tender or to one line in that tender.
Evidence references actual documents or product attributes, not arbitrary
model-invented URLs.

Enforce that a selected candidate belongs to its line, using a composite foreign
key or equivalent database constraint. Enforce resolution consistency:
selected requires a selected candidate; skipped/unresolved do not have one.
Skipped lines retain skip provenance; selected lines retain selection source.
Scope document, mapping, candidate, and offer references to the correct tender.

Buyer article history is not a permanent catalog copy. Refresh current product
attributes before evaluating a historical product. If history contains several
products, treat them as candidates rather than silently overwriting history.

### Approval and operations

| Table | Principal fields and constraints |
|---|---|
| `offer_snapshots` | Tender, content revision/digest, immutable internal line and mapping snapshot, creation time. Contains confidential prices; never sent to AI. |
| `review_requests` | Tender, preparer, output mode, expected preparation/mapping versions, fresh pricing run, readiness/supersession state, final review digest. |
| `approvals` | Offer snapshot, approver, decision/time, reviewed mapping digest or manual-transfer acknowledgment, accepted deviations, invalidation metadata. |
| `exports` | Snapshot or draft revision, audience, type, mapping version, object key/checksum, generation state. |
| `submissions` | Tender, approved snapshot/export, reported submission time, recorder, external reference/status. |
| `processing_runs` | Tender, kind, actor, status, model/config snapshot, source revision, progress/timestamps. |
| `processing_items` | Run, optional line/document subject, expected input versions, state, error code, queue job reference. |
| `idempotency_keys` | Actor, route/target, request key/hash, result reference, expiry; unique scoped key. |
| `audit_events` | Actor, action, entity reference, revision, request/run correlation, time; sanitized details. |
| `processing_configuration` | Selected LiteLLM model alias and configuration version, changed by administrator. No gateway secrets. |

Processing item subjects are constrained: line and document cannot both be set;
run-level items have neither. Foreign keys and service checks enforce subject
membership in the run's tender. Keep application progress records separate from
library-owned queue tables; do not expose internal queue arguments through the API.

Review-request version checks use preparation inputs separately from price-result
publication: the fresh run's own quote writes must not supersede its review.
Do not use the aggregate content revision as the only async-input fingerprint.
Tender links cannot refer to themselves or introduce amendment cycles.

Index foreign keys and common access paths: tender line ordering/resolution,
buyer article lookup, current price fingerprint, processing run/state, and
tender contributor membership. Use Alembic for application migrations and
Procrastinate's supported migration mechanism for its separate queue schema.

## 9. Jobs, concurrency, and retries

Use task arguments containing record IDs and version references, not document
bodies, tokens, or prices. Workers load authorized inputs from application data.

Proposal: use Procrastinate's documented SQLAlchemy connector for atomic job
deferral. Prove commit/rollback behavior with the selected SQLAlchemy/driver
versions before building feature workflows. Do not substitute enqueue-after-commit
without an explicit, reviewed recovery mechanism.

Use document-sized extraction jobs, line-sized matching/lookup jobs, bounded
pricing batches, and separate export jobs. Jobs may spawn further work through
transactional deferral. Worker concurrency, connection pools, and maximum cloud
replicas must have explicit limits.

Run/item states: `queued`, `running`, `succeeded`, `partial_failure` (run only),
`failed`, and `superseded`. No-match is a successful matching result, not a
service failure. Derive progress from persisted item states, not process memory.

Configure bounded retries for transient timeouts, rate limits, and recoverable
upstream errors, respecting `Retry-After`. Authentication/configuration failures
and unsupported files do not enter unlimited retry loops.
Never include raw upstream error bodies in public responses or logs.

Procrastinate requires explicit stalled-job recovery based on worker heartbeats.
Configure its periodic recovery pattern and test forced worker termination.
Do not infer a job is dead just because it has run for a long time.

Use per-line input and selection versions, not only a global tender revision:

- A correction invalidates results based on the old requirements.
- A manual selection or skip while matching runs must not be overwritten later.
- A delayed exact-lookup result must not replace a more recent user choice.
- A quote applies only if product, offered quantity/unit, and customer basis match.
- A new review uses fresh quote records from its own pricing run, not a prior run.
- Results from superseded runs cannot replace newer published results.
- Work completing on another line must not make every sibling task stale.

Conditional writes and database constraints must make repeated completion safe.
Increment the tender content revision whenever published offer content changes;
approval/export checks use that aggregate revision.

## 10. REST API specification

Prefix: `/api/v1`. All business endpoints require an active authorized user.
Only health endpoints and local authentication/bootstrap-token consumption are
unauthenticated as appropriate. Administrative operations require `admin`.

Use UUID identifiers, ISO 8601 timestamps with offsets, decimal strings, and
cursor pagination. Default page size proposal: 100, maximum 200.
Return ETags for mutable resources and require `If-Match` for preparation edits
and approval-sensitive commands.

Long-running commands return `202 Accepted`, a run representation, and a
`Location` pointing to its status resource. Support `Idempotency-Key` for commands
that enqueue work or record approval/submission; the same scoped key with a
different request is a conflict.

### Authentication and administration

| Method and path | Request / result |
|---|---|
| `POST /auth/local/login` | Login/password; local access token and rotating refresh token. |
| `POST /auth/local/refresh` | Refresh token; rotated token pair. |
| `POST /auth/local/logout` | Revoke the supplied refresh session/family. |
| `POST /auth/local/activate` | Expiring administrator-issued token and new password. |
| `GET /auth/me` | Canonical internal user and current roles. |
| `POST /admin/users` | Provision identity, permissions, and optional local activation. |
| `PATCH /admin/users/{id}` | Enable/disable or change roles; audited. |
| `POST /admin/users/{id}/entra-identities` | Explicitly link tenant/object identity. |
| `DELETE /admin/users/{id}/entra-identities/{identity_id}` | Unlink external login. |
| `POST /admin/users/{id}/password-resets` | Create a one-time reset; no password disclosure. |
| `GET, PUT /admin/processing-configuration` | Read/change LiteLLM model alias; configuration version returned. |

Activation and reset secrets are delivered once to the authorized administrator
through the administrative operation; do not log them. API clients present Entra
access tokens directly as bearer tokens; no password-exchange endpoint for Entra.

### Tender preparation

| Method and path | Request / result |
|---|---|
| `POST, GET /buyers` | Create or list buyers and pricing customer numbers. |
| `GET, PATCH /buyers/{id}` | Read/update buyer metadata; tender pricing uses its explicit snapshot. |
| `POST, GET /tenders` | Register or list tenders; optional `supersedes_tender_id` links an amended request. |
| `GET, PATCH /tenders/{id}` | Read readiness/warnings or update permitted metadata with `If-Match`. |
| `POST /tenders/{id}/documents` | Multipart upload; stored document metadata. |
| `GET /tenders/{id}/documents` | List source documents. |
| `GET /documents/{id}/download` | Authorized original-file download. |
| `POST /tenders/{id}/extraction-runs` | Source IDs and `continue_to_matching`; return `202`. |
| `GET /tenders/{id}/lines` | Ordered/paginated lines, selections, readiness and warnings. |
| `GET, PATCH /lines/{id}` | Read/correct permitted extracted fields or offered quantity/unit. |
| `GET, PATCH /tenders/{id}/requirements` | Read/correct shared or line requirements with version checks. |
| `POST /tenders/{id}/matching-runs` | All or specified line IDs; return `202`. |
| `GET /lines/{id}/candidates` | Ranked candidates and requirement assessments. |
| `PUT /lines/{id}/selection` | Candidate ID and recorded deviations; manager selection. |
| `POST /lines/{id}/candidate-lookups` | Internal article number; return `202`, then add/select if still current. |
| `POST /lines/{id}/skip` | Explicit skip, optional reason. |
| `POST /lines/{id}/reopen` | Clear resolution deliberately; invalidate dependent readiness. |
| `POST /tenders/{id}/skip-unresolved` | Transactionally skip unresolved lines only; changed count. |
| `POST /tenders/{id}/pricing-runs` | Require matching completeness and priceable offered quantities; return `202`. |
| `GET /processing-runs/{id}` | Stage, counts, item failures, warnings and continuation references. |
| `POST /processing-runs/{id}/retries` | Retry failed, still-current items; return `202`. |

PATCH endpoints expose explicit schemas, not arbitrary database field updates.
Do not allow clients to set approval flags, contributor history, selection
provenance, AI assessments, or queue state through generic updates.

### Review, output, and submission

| Method and path | Request / result |
|---|---|
| `GET, PUT /tenders/{id}/template-mapping` | Read/propose/correct the source-bound response-cell mapping. |
| `POST /tenders/{id}/review-requests` | Preparer requests a new review cycle and output mode; enqueue fresh pricing and return `202`. |
| `GET /tenders/{id}/review` | Active review's offer content, deviations, pricing readiness, mapping and digest. |
| `POST /tenders/{id}/approvals` | Review digest, mapping digest or manual-transfer acknowledgment, accepted deviations; independent approval. |
| `POST /tenders/{id}/review-rejections` | Review note; retain work for correction, no approval granted. |
| `POST /tenders/{id}/exports` | Audience/type and expected revision or approval; return `202`. |
| `GET /exports/{id}` | State, audience, revision, checksum and download availability. |
| `GET /exports/{id}/download` | Authorized download; final buyer artifacts require current approval. |
| `POST, GET /tenders/{id}/submissions` | Record or inspect manual submissions against approved artifacts. |
| `GET /tenders/{id}/audit-events` | Authorized, paginated sanitized history. |

System endpoints: `/health/live`, `/health/ready`, and an operationally protected
`/metrics`. Business-operation authorization must not depend on obscurity of URLs.

### Errors

Return RFC 9457-style problem details with stable application codes.

- `401`: invalid/missing authentication.
- `403`: missing permission or ineligible approver.
- `404`: absent or inaccessible resource.
- `409`: readiness, idempotency, or lifecycle conflict.
- `412`: stale `If-Match` or review digest.
- `413`: file/body limit exceeded.
- `415`: unsupported document format.
- `422`: invalid fields, quantity basis, or mapping.
- `428`: required mutation precondition missing.
- `429`: request/login throttling.

Example:

```json
{
  "type": "urn:tenderflow:problem:unresolved-lines",
  "title": "Matching is incomplete",
  "status": 409,
  "code": "unresolved_lines",
  "detail": "Select a candidate or explicitly skip every line before pricing.",
  "unresolved_count": 12,
  "request_id": "req-example"
}
```

### Request and response examples

Identifiers below are illustrative. No upstream endpoint paths or payloads are
claimed here; those must come from the real service contracts.

Tender registration:

```http
POST /api/v1/tenders
Authorization: Bearer <access-token>
Content-Type: application/json
```

```json
{
  "buyer_id": "f70c2b11-d547-414e-8ec6-2bf840bfa4fb",
  "title": "Buyer request November",
  "pricing_customer_number": "CUSTOMER-EXAMPLE",
  "submission_deadline": "2026-12-01T12:00:00+01:00"
}
```

Response: `201 Created`, `Location: /api/v1/tenders/{id}`.

```json
{
  "id": "fdbe1f26-a065-45b0-a71b-04370ea78c36",
  "content_revision": 1,
  "status": "draft",
  "warnings": []
}
```

Extraction command, after multipart document upload:

```json
{
  "document_ids": ["4f41550c-ac5f-45cd-8b5a-d2703fca73c1"],
  "continue_to_matching": true
}
```

Response: `202 Accepted`, with a run status URL.

```json
{
  "id": "776487c3-e916-4c12-90cd-5f950956c002",
  "kind": "extraction",
  "status": "queued",
  "actor_id": "a5f841b0-6841-422c-b511-33d943c4d2ec",
  "model_alias": "configured-tender-model",
  "configuration_version": 3,
  "continue_to_matching": true
}
```

Line representation with a suggested but imperfect candidate:

```json
{
  "id": "92825652-9bd6-4474-b1a0-654b570c1f93",
  "revision": 4,
  "description": "Requested item with certificate requirement",
  "requested_quantity": "25",
  "requested_unit": "piece",
  "resolution": "unresolved",
  "selected_candidate_id": null,
  "last_matching_outcome": "candidates_found",
  "warnings": ["No candidate is assessed as meeting every requirement."]
}
```

Manual selection request:

```http
PUT /api/v1/lines/92825652-9bd6-4474-b1a0-654b570c1f93/selection
If-Match: "line-4"
```

```json
{
  "candidate_id": "80336436-9da2-46f8-b181-c0265d2f3e63",
  "deviations": [
    {
      "requirement_id": "b9ac1784-dd38-4bf3-b345-fc7ec091cc16",
      "note": "Certificate evidence is unavailable; independent review required."
    }
  ]
}
```

The result is manager-selected, not fully compliant. Offered quantity/unit and
any packaging discrepancy must still be resolved before pricing.

Bulk-skip response:

```json
{
  "skipped_count": 12,
  "selected_count": 988,
  "unresolved_count": 0,
  "matching_complete": true
}
```

Internal priced-line representation; never sent to AI:

```json
{
  "internal_article_number": "ARTICLE-EXAMPLE",
  "title": "Example product",
  "offered_quantity": "3",
  "selling_unit": "box_of_10",
  "currency": "EUR",
  "unit_buying_price": "12.00",
  "unit_selling_price": "15.00",
  "total_net_price": "45.00",
  "quantity_note": "30 pieces offered for a request of 25 pieces."
}
```

Prices and currency above are illustrative, not agreed upstream defaults.
The buyer-facing projection omits `unit_buying_price` and other internal fields.

Approval request:

```json
{
  "review_digest": "sha256:<current-review-digest>",
  "template_mapping_digest": "sha256:<reviewed-mapping-digest>",
  "accepted_deviation_ids": ["f613f88a-a628-4545-a3d5-fd964d061891"]
}
```

Recheck independence and readiness server-side. Do not trust a client-supplied
approver ID, approval status, or previously computed readiness flag.

## 11. Integration boundaries

### LiteLLM

Use the existing instance and its model aliases. Administrators select an allowed
alias; endpoint credentials come from deployment configuration, not the public
model-setting API. Capture model alias, configuration version, and prompt/schema
version for each processing run.

A configuration change affects new runs, not in-flight work. Gateway safeguards
are not reimplemented. Explicit input projection and schema validation remain
application responsibilities.

Uploaded sources are assumed price-free. Pre-AI redaction is deferred; the
application cannot guarantee that an accidentally uploaded confidential document
will not reach AI. Never send internal worksheets or pricing-enriched content
back through extraction or matching.

### Discovery and exact lookup

Define internal typed request/result models around the required business data.
Do not invent remote URL paths, authentication, rate limits, field names, or
document-access capabilities.

Before implementing the integration, obtain or confirm:

- Semantic search and exact-lookup contracts and product identifiers.
- Product units/packaging, lead times, document references, and evidence fields.
- Pagination/top-k behavior, quotas, timeouts, and error semantics.
- Whether price fields can be omitted; filter them before any AI call regardless.

### Pricing

Confirm authentication, currency, quantity/unit basis, customer identification,
batch limits, duplicate-product handling, per-line correlation, validity, and
partial-failure behavior. Do not infer price validity or invent contract discounts.

Request fresh prices for each final review cycle, capture retrieval time, and
honor any supplied expiry. Approved snapshots remain frozen; later repricing
invalidates approval. There is no additional agreed maximum elapsed quote age.

## 12. Project structure

Proposal: a small layered application, not a framework for future microservices.

```text
src/tenderflow/
  main.py
  config.py
  api/
    dependencies.py
    auth.py
    admin.py
    buyers.py
    tenders.py
    processing.py
    offers.py
  domain/
    requirements.py
    matching.py
    quantities.py
    approval.py
  services/
    extraction.py
    matching.py
    pricing.py
    offers.py
  infrastructure/
    database/
      models.py
      sessions.py
    auth/
    integrations/
      litellm.py
      discovery.py
      pricing.py
    storage.py
  schemas/
  workers/
    app.py
    tasks.py
tests/
  unit/
  integration/
  api/
  fixtures/
alembic/
docs/
```

Only introduce interfaces at real external boundaries where they support testing
or cloud substitution. No generic repository per table, dependency-injection
container, event bus, or speculative plugin registry.

## 13. Testing and verification strategy

Use pytest and small behavior-focused tests. Keep all test documents, products,
and identities synthetic; do not commit real buyer files or credentials.

### Unit tests

- Unknown/failed requirement assessments prevent AI preselection.
- Manual imperfect selection preserves deviations and assessment status.
- Bulk skip preserves selected lines and resolves only unresolved lines.
- Matching continuation rejects unresolved lines.
- Quantity discrepancies never silently round.
- Buyer projections cannot contain internal cost fields.
- Canonical identity/contributor rules prevent self-approval.
- Content changes invalidate approval.
- All-skipped tenders become No-Bid and can reopen without empty-offer approval.

### PostgreSQL integration tests

Use real PostgreSQL, not SQLite, for database/queue semantics.

- Domain writes and queue insertion commit or roll back together.
- Composite foreign keys and state constraints reject invalid selection ownership.
- Duplicate task completion is harmless.
- A manual edit, skip, or newer run wins over an older asynchronous result.
- Simultaneous approval and edit cannot approve stale content.
- A terminated worker's task is recovered and completes safely.
- A new final review cannot reuse earlier pricing or become ready from stale
  results after preparation changes.
- Refresh rotation, identity uniqueness, and account disabling work as designed.

### API and integration-contract tests

- Local and mocked Entra authentication resolve to the same internal identity.
- Wrong tenant/audience, unlinked identities, and inactive accounts are rejected.
- Permissions, independent approval, pagination, ETags, idempotency, and errors.
- Complete upload-to-submission flow with fake discovery/pricing/LiteLLM services.
- New linked tenders preserve amendment history without inheriting approval/prices.
- No prices appear in any captured AI request, retry, or diagnostic event.
- External timeouts, rate limits, empty results, partial pricing and malformed
  model output remain distinguishable.

Use recorded synthetic contract fixtures, then explicitly authorized sandbox
checks against real service contracts before release. Do not use production
customer data for live tests.

### Document/export tests

- XLSX/CSV, text PDF, and DOCX extraction with source references.
- Ambiguous/malformed documents and unsupported formats fail explicitly.
- Multiple sheets, blank rows, repeated descriptions, and buyer article numbers.
- Approved response cells are correct; untouched cells and line associations remain.
- Excluded lines are unfilled, cost data is absent, text is not executable, and
  stale/unapproved artifacts cannot be downloaded as final offers.

### Operational and capacity checks

- Process 1,000 synthetic lines under bounded simulated upstream quotas.
- Restart API/workers mid-run and confirm durable progress without lost selections.
- Verify migrations, readiness, backup/restore procedure, metrics, and secret
  configuration in a non-production environment.
- Measure real extraction accuracy and candidate usefulness with an agreed
  representative dataset before claiming business improvement.

## 14. Observability

Structured logs carry request, tender, run, task, and internal actor identifiers
where appropriate, plus safe error codes and durations. Correlation must work
across API commands and worker execution.

Operational metrics: request rate/errors/latency, queue age, run duration,
retries, stalled jobs, upstream latency/errors/rate limits, database pool use,
and artifact-generation failures.

Business metrics: extracted lines, AI-preselected lines, manually changed
selections, manual lookups, unresolved/skipped lines, offered-line coverage,
and time from upload to approved offer.

Do not label metrics with user/customer identifiers or unbounded tender IDs.
Report AI-preselection and actual offered coverage separately; neither proves
matching accuracy.

## 15. Proposed delivery slices

These are design-level increments, not an approved execution plan. After design
approval, create the detailed implementation plan with focused TDD steps.

1. Project configuration, database/migrations, health, and transactional durable
   job proof including crash recovery.
2. Canonical users, local authentication, administrator provisioning, and Entra.
3. Buyers, tenders, contributor tracking, immutable uploads and storage.
4. Extraction, provenance, correction, and direct-continuation orchestration.
5. Discovery/assessment, preselection, manual lookup, deviations, and skip gates.
6. Quantity resolution and upstream pricing with stale-result protection.
7. Review snapshots, template mappings, and independent approval.
8. Internal and buyer exports, manual submission records, deadline warnings.
9. End-to-end recovery, capacity checks, operational deployment, and acceptance.

Each feature requires a separate code review and the user's review before any
commit. Wait for feedback before starting the next major feature. No automatic
commit, merge, or implementation follows design approval alone.

## 16. Review decisions and release prerequisites

The builder check identified three explicitly open behavioral decisions. They
were subsequently resolved by the user:

- Fresh pricing is required before final review; approved prices remain frozen.
- Amended requests create new linked tenders, without in-place reconciliation.
- All-skipped tenders record No-Bid, produce no empty offer, and can reopen.

### Engineering defaults accepted with the design

- Initial Word support is DOCX, not legacy DOC.
- Internal worksheet format is XLSX.
- Authentication uses direct validation of Entra API access tokens, with login
  performed by the API client; no application-hosted login frontend.
- File limits and token lifetimes above are configurable initial defaults.
- Procrastinate and ECS/Fargate/RDS/S3 are proposed concrete products.

### Integration/production prerequisites

- Obtain actual discovery/pricing/LiteLLM contracts and service limits.
- Configure the Entra tenant, API registration, delegated scope, and key validation.
- Agree operational SLOs, backup/recovery objectives, and retention/deletion policy.
- Select representative synthetic or explicitly approved evaluation data.
- Verify the queue's transactional integration and recovery behavior.

## 17. References

Queue capabilities were checked against official documentation while drafting.
Implementation must pin compatible versions and verify them in tests.

- [Procrastinate overview](https://procrastinate.readthedocs.io/en/stable/)
- [Transactional external connections](https://procrastinate.readthedocs.io/en/stable/howto/production/external_connection.html)
- [Retry strategies](https://procrastinate.readthedocs.io/en/stable/howto/advanced/retry.html)
- [Worker concurrency](https://procrastinate.readthedocs.io/en/stable/howto/production/concurrency.html)
- [Stalled-job recovery](https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html)
