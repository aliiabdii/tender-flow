# TenderFlow: Business Requirements

Status: MVP business scope and safeguards approved on 2026-11-15.

This document records the business discovery conducted from `docs/INIT.md`.
It does not constitute a technical design or authorization to implement.
Open details below must not be treated as agreed requirements.

## 1. Purpose and context

TenderFlow is a backend-only REST API that helps a supplier prepare offers
against buyers' tender requests. It must be general-purpose across product
domains, rather than encode one industry's matching rules.

The MVP serves one supplier organization per deployment. Multiple users may
participate in preparing and reviewing tenders within that organization.
There is no frontend in scope.

### Current workflow

1. Staff manually extract incoming tender information into a unified Excel sheet.
2. Staff assess whether the request fits the supplier's product range and decide
   Go/No-Go.
3. Tender managers search the catalog for products matching each requested line.
4. Staff assemble a proposal.
5. The proposal undergoes quality checking and validation.
6. Staff finalize and submit the offer through the buyer's portal.

Product matching is the largest bottleneck. A request may contain 1,000 lines,
and manual searching can extend beyond the deadline. The team reports being
able to bid on approximately 10% of requested lines on average. This is a
reported baseline, not an agreed acceptance target.

Manual extraction is the next-largest bottleneck, followed by proposal
preparation and submission.

### Desired outcome

Increase the number of tender lines the team can evaluate and offer before
submission deadlines, while reducing extraction and offer-preparation effort.
AI assists with extraction and recommendations; humans retain control over
commercial decisions and final approval.

## 2. MVP approach

The agreed MVP is a thin end-to-end workflow: upload, extract, match, price,
prepare, independently approve, export, and record manual submission.

A matching-only pilot would be smaller, but would leave pricing and transfer
into buyer offer files largely manual. The approved scope includes those steps
while deferring channel and submission integrations.

### Included

- File upload through the API.
- Extraction from XLSX/CSV, text PDFs, and Word documents without a fixed
  buyer-specific input template.
- Separate extraction and product-matching steps.
- Optional progression directly from extraction to matching without presenting
  extracted lines first.
- Multiple product candidates when available, an AI-promoted best candidate,
  and an explanation of the recommendation.
- AI preselection, manager overrides, exact article lookup, and line exclusion.
- Buyer-specific historical article mappings.
- Integration with existing upstream discovery and pricing services.
- Manager-controlled quantity and unit discrepancies.
- An internal worksheet and completion of buyer-provided XLSX/CSV offer templates.
- One independent authorized tender-manager approval.
- Manual submission with recorded status and reference.
- Submission-deadline warnings.

### Deferred

- Multiple isolated supplier organizations in one deployment.
- Automatic intake from email, procurement portals, or EDI.
- Scanned-document/OCR support.
- A separate AI qualification or Go/No-Go recommendation capability.
- Building replacement discovery or pricing services.
- Category-specific or multi-stage approval chains.
- Automatic resolution of commercial exceptions.
- Filling Word/PDF offer templates.
- Platform-specific XML exports, Ariba integrations, or OCI workflows.
- Automatic submission to buyer systems.
- Preparation-task management.
- A dedicated deadline-extension workflow.

## 3. Actors

- **Tender preparer/manager:** uploads requests, reviews or corrects extraction,
  initiates matching, changes selections, handles exceptions, and prepares offers.
- **Authorized approving tender manager:** approves the offer but must not have
  created or processed that tender.
- **Submission recorder:** records the result of manual submission. The precise
  authorization for this action remains to be specified.
- **Buyer/customer:** issues the request and receives the buyer-facing offer.
  A customer number is supplied to upstream pricing for applicable contract prices.
- **Administrator:** provisions user accounts, explicitly links Microsoft Entra
  identities, and selects the processing model available through LiteLLM.

Exact permission assignments and authentication are technical-design topics.
The independent-approval rule is a business requirement.

## 4. Intake and extraction

Requests currently arrive through individual emails, e-procurement portals,
or EDI systems. Users obtain the files themselves and upload them through the
API for the MVP.

Each extracted tender line contains:

- The requested product description.
- Additional context, when supplied.
- Requested unit of measure.
- Requested quantity.
- The buyer's article number, when supplied.

The buyer's article number identifies a product in that buyer's own system,
often from prior purchases. It is not the supplier's internal article number.

An amended buyer request is registered as a new tender linked to the previous
one. The MVP does not reconcile amended source documents with existing
preparation. Keep the previous tender and its history; do not carry forward
approval or prices automatically. Corrections to extracted values are still
allowed within the existing tender.

Tender information can include shared requirements, submission deadlines,
budget information, eligibility criteria, mandatory requirements, and required
documentation where supplied. Missing information must not be invented.
The exact extraction fields and handling of requirements spread across files
will be specified during technical design.

Extraction does not require a separate approval. Users can inspect and correct
the results before matching, or choose to proceed directly.
Skipping presentation does not make extracted information human-approved or
remove extraction warnings.

## 5. Go/No-Go and progression

The transition into matching expresses the user's Go decision:

- In the normal path, the user reviews extracted lines and starts matching.
- In the direct path, the user chooses to proceed without extraction presentation.

There is no separate AI qualification decision or separate Go approval gate
in the MVP. Choosing the direct path represents user intent, not an AI decision.
An all-skipped tender records No-Bid as described below. A separate early
No-Go workflow is not defined for the MVP.

## 6. Matching and product selection

### Suitability dimensions

Candidates are evaluated against requirements from the tender, across:

1. **Functional fit:** performs the required task in the buyer's environment.
2. **Technical and physical specifications:** dimensions, capacity, performance,
   compatibility, and stated tolerances.
3. **Regulatory and quality compliance:** required certifications and applicable
   sustainability or origin requirements.
4. **Commercial and logistics alignment:** units, packaging, minimum order
   quantities, warranty, and delivery lead times.

These dimensions must not become hardcoded industry-specific criteria.
Retrieval relevance alone is not proof of suitability or compliance.
Certificates may be described in supporting documents rather than product text.
Unavailable evidence remains unknown, not compliant.

### Existing discovery dependency

The organization already has a semantic product-discovery service backed by
vector search and reranking. It accepts semantic descriptions and exposes
product information relevant to the matching dimensions. A separate endpoint
retrieves product information by internal article number.

TenderFlow uses these services rather than building its own product search.
The availability of supporting documents and evidence references must be
confirmed when designing the integration.

### Candidate behavior

- Provide multiple candidates when credible alternatives are available.
- Promote and preselect the AI's best candidate only when it is assessed as
  meeting all requirements, with a reason. If none meets all requirements,
  suggestions may still be shown but no candidate is preselected.
- Do not fabricate alternatives when only one or no credible candidate is found.
- No individual manual confirmation is required for every preselected line.
- Managers can replace the selected candidate with another suggestion.
- Managers can enter an internal article number for a product they know.
  Retrieve it through exact lookup, add it to the candidates, and select it.
- Distinguish an AI-preselected product from a manager-selected product.
- Neither an AI selection nor a manual override is itself proof of compliance.
  Unresolved mismatches remain visible for review.
- Managers may manually select an imperfect match, including one with unmet or
  unverified requirements, with recorded deviations for independent review.
  Selection does not relabel those requirements as satisfied.

### Historical mappings

Maintain a mapping between a buyer, that buyer's article number, and an
approved supplier product. Learn mappings from approved offers, not unreviewed
AI selections.

A historical mapping can inform promotion of a candidate but does not bypass
alternatives or human control. It does not prove that current requirements,
product specifications, packaging, or delivery conditions are unchanged.

### Partial offers

Each line can be offered independently; complete tender coverage is not required.

Distinguish:

- A line included in the offer with a selected product.
- A line explicitly excluded by a manager.
- A line for which no product was found.

Managers may exclude a line even when suggestions exist. Excluded and unmatched
lines leave the buyer's offer fields unfilled, without erasing the original
request information.

### Matching-completion gate

Before proceeding beyond matching, every line must have a selected candidate
or have been explicitly skipped by a manager. A no-match result alone does not
count as a skip.

Managers can skip individual lines or skip all unresolved lines in one explicit
action. The bulk action preserves existing selections. Explicitly skipped lines
remain distinguishable from lines still unresolved after a no-match result.

When every line has been explicitly skipped, record No-Bid rather than generate
an empty offer. Users can reopen preparation. An empty or failed extraction
does not itself establish a No-Bid decision.

## 7. Quantities, units, and pricing

### Quantity discrepancies

Managers decide whether to offer a product whose selling unit or packaging
does not exactly match the request, with an explanatory side-note as appropriate.
For example, the system must not silently turn a request for 25 individual items
into an offer for 3 boxes of 10.

Preserve the requested quantity/unit and offered quantity/unit separately.
Flag discrepancies and provide a place for the manager's explanatory note.
Unresolved quantity issues on included lines prevent final approval.

### Existing pricing dependency

A separate upstream pricing service already exists.

Inputs:

- A list of products and their quantities.
- A customer number, allowing customer contract prices to apply.

Returned for each product:

- Unit buying price.
- Unit selling price.
- Total net price.

Use the returned net totals rather than reproducing the upstream contract-pricing
rules. Confirm the price unit, quantity basis, and currency contract during
technical design.

Product suitability and price availability are separate concerns. Failed or
missing pricing must not cause invented prices. Unresolved pricing on an
included line prevents final approval.

A fresh pricing run is required before final review. Approved prices remain
frozen in their offer snapshot; later repricing invalidates approval. No
additional maximum elapsed price age has been agreed.

## 8. Offer preparation and exports

### Internal worksheet

Include:

- Internal article number.
- Product title.
- Buying price.
- Selling price.
- Selling unit.
- Total net price for the offered product and quantity corresponding to the
  buyer's request, including manager-decided quantity adjustments.

Requested and offered quantities/units must remain distinguishable.
The exact internal worksheet layout and file format remain to be specified.

### Buyer-facing offer

- Exclude buying prices and other internal cost information.
- Fill the supplier-response fields of the buyer's original XLSX/CSV template.
- Preserve the buyer's requirements and correspondence between original lines
  and offered products.
- Leave unmatched or explicitly excluded lines' offer fields unfilled.
- Carry relevant quantity-discrepancy explanations into the offer.

For Word/PDF requests, extraction and matching are supported, but staff manually
transfer the offer from the internal worksheet into the required buyer artifact.
When no supported buyer spreadsheet is available, independent approval covers
the internal worksheet with an explicit manual-transfer acknowledgment.
Buyer-template mapping is not applicable on that path, and TenderFlow does
not generate a buyer-facing file.

If a template cannot be filled reliably, do not claim that a valid completed
offer has been produced. The exact field-identification and recovery workflow
remains a design topic.

## 9. Approval

One approval is required from an authorized tender manager who neither created
nor processed the tender. Category-specific approvers and approval chains are
outside the MVP.

Track contributors sufficiently to enforce this separation; checking only the
creator is insufficient.

- AI output remains distinguishable from human-approved information.
- Every line must be selected or explicitly skipped.
- Unresolved pricing or quantity issues on included lines prevent final approval.
- Recorded deviations for manually selected imperfect matches are included in
  independent review.
- Approval includes confirmation of the buyer-template field mapping: where
  offered articles, prices, units, quantities, and notes will be written.
- Without a supported buyer spreadsheet, approval instead includes an explicit
  manual-transfer acknowledgment and review of the internal worksheet.
- Final review follows a fresh pricing run; previously fetched prices alone
  do not satisfy a new review cycle.
- Changes to offer content after approval invalidate that approval and require
  another independent review.
- An internal draft and an approved buyer-facing offer are distinct artifacts.
- Final buyer-facing export follows approval.

Independent review includes the recorded deviations of manually selected
imperfect matches. An accepted deviation does not turn unknown evidence into
verified compliance. The detailed presentation of the review checklist remains
a design detail.

## 10. Submission and deadlines

Users manually submit the approved offer through the buyer's channel and record
submission status and a reference in TenderFlow.

Submission deadlines generate warnings, not hard blocks. Users may continue
after a deadline, including when they have negotiated an extension externally.
A dedicated extension feature is not needed for the MVP.

The exact submission-record fields and post-submission outcome tracking remain
open. No automated portal submission is included.

## 11. Main business entities and relationships

These are conceptual entities, not a committed database schema.

| Entity | Purpose and relationships |
|---|---|
| Buyer/customer | Issues tenders; scopes buyer article numbers; supplies the customer identity for pricing. |
| Tender | Groups a buyer's request, documents, extracted information, lines, preparation, approval, and submission. |
| Source document | Uploaded request material associated with a tender. |
| Tender line | One requested product with description, context, quantity/unit, and optional buyer article number. |
| Product candidate | A discovery or exact-lookup product associated with a line, with recommendation information and available evidence. |
| Line selection | Identifies the selected candidate and whether selection came from AI or a manager; distinct from exclusion or no match. |
| Buyer article mapping | Relates a buyer and buyer article number to a product from an approved offer. |
| Priced offer line | Associates an included selection with offered quantity/unit, upstream prices, and relevant notes. |
| Offer | Collects offered lines and supports separate internal and buyer-facing artifacts. |
| Approval | Records the independent manager's approval of the offer content; invalidated by content changes. |
| Submission record | Records the status and reference for manual submission. |
| User/contribution | Identifies participants and their actions for selection provenance and independent approval. |

## 12. Failure scenarios and safeguards

| Scenario | Required behavior or remaining decision |
|---|---|
| Missing or ambiguous extracted information | Allow correction; expose uncertainty; do not invent facts. |
| User skips extraction presentation | Preserve the distinct extraction step and warnings. |
| Discovery failure | Do not fabricate candidates or present failure as a successful no-match result. |
| Only one or no credible candidate | Return the available result without padding the candidate list. |
| No candidate meets all requirements | Do not preselect; require manual selection with recorded deviations or an explicit skip before proceeding. |
| Manager chooses bulk skip | Skip unresolved lines only; preserve existing selections. |
| Certificate or other evidence unavailable | Keep the requirement unverified; do not infer compliance. |
| Historical article mapping is stale | Do not treat history as proof of present suitability. |
| User-supplied article lookup fails | Do not populate a fabricated product. |
| Unit, packaging, or quantity discrepancy | Flag it; require a manager decision; no silent rounding. |
| Pricing is unavailable or incomplete | Preserve the distinction from no product match; do not approve affected included lines. |
| Template cannot be completed reliably | Surface the problem; do not present the output as a valid completed offer. |
| Creator or contributor attempts approval | Reject approval under the independence rule. |
| Offer content changes after approval | Invalidate approval and require review again. |
| Deadline passes | Warn and allow continued work. |
| Internal worksheet is exported | Keep it distinct from the buyer-facing artifact; never include buying prices in the buyer-facing offer. |

## 13. Open details for the next design stage

Resolve these explicitly rather than infer business rules:

- Supported Word variants, file limits, and handling of malformed or protected files.
- Combining multiple documents, conflicting requirements, duplicate uploads,
  and corrections after downstream processing has started. Amended requests
  use new linked tenders, not in-place reconciliation.
- Discovery response contracts, document access, evidence availability,
  candidate counts, and recommendation evaluation.
- Buyer identity and customer-number entry or lookup.
- Historical mapping conflicts and product replacement or discontinuation.
- Exact selling-unit conversions, rounding, minimum order quantities, and notes
  required for quantity exceptions.
- Pricing currency, quantity basis, partial failures, and any upstream expiry
  information. Fresh pricing before final review and approval invalidation after
  repricing are required.
- Spreadsheet response-field identification, unsupported layouts, and internal
  worksheet format.
- Review presentation and rejection/rework details. Preparation handover must
  retain contributor history and cannot restore a contributor's independence.
- Submission metadata and post-submission outcome tracking. All-skipped No-Bid
  closure and reopening are required; a separate early No-Go workflow is deferred.
- Concurrent editing, operational limits for large tenders, and recovery from
  interrupted processing.
- Access control, data retention, permitted document processing by external AI,
  and audit requirements.
- Measurable acceptance targets for extraction accuracy, candidate usefulness,
  processing time, and improvement over the reported line-coverage baseline.

Business requirements precede technical design. Technical decisions must be
documented under `docs`, and implementation must wait until requirements and
technical design have been documented and agreed.

## 14. Constraints agreed during technical discovery

- Support local application accounts and Microsoft Entra login.
- Administrators provision users and explicitly link Entra identities. There is
  no public registration or automatic first-login provisioning.
- Both login methods must resolve to the same internal user identity so they
  do not bypass contributor-based approval restrictions.
- Use an existing LiteLLM instance for LLM integration. Administrators select
  the model used for tender processing.
- Product attributes may be sent to AI. Buying prices, selling prices, and
  customer-specific prices must not be sent to AI.
- Rely on LiteLLM's existing gateway safeguards rather than duplicate them.
  TenderFlow must still keep confidential pricing out of its AI requests.
- The MVP assumes uploaded source files are buyer requests without confidential
  pricing. Pre-AI redaction is deferred as an improvement. This assumption does
  not guarantee protection against accidentally uploading a confidential file.
- Target AWS initially, with application-level portability to other major
  clouds. Cloud deployment configuration and integrations need not be identical.
- Use one application codebase with separate API and worker processes and an
  established PostgreSQL-backed durable job queue.
