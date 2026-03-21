<!--
Sync Impact Report (2026-03-14)
- Version change: 1.0.0 → 1.1.0
- Modified principles:
  - III. Separation of Concerns & Architectural Consistency — expanded
    with FastAPI-specific guidance (Pydantic BaseSettings, dependency
    injection, shared async HTTP client lifecycle)
- Modified sections:
  - External Data Sources — added shared httpx.AsyncClient requirement
  - Development Workflow — added GET-only route constraint
- Templates requiring updates:
  - .specify/templates/plan-template.md — ✅ no changes needed
  - .specify/templates/spec-template.md — ✅ no changes needed
  - .specify/templates/tasks-template.md — ✅ no changes needed
  - .specify/templates/checklist-template.md — ✅ no changes needed
- No command files exist to update.
- Follow-up TODOs: none
-->

# Track Timing Predictor Constitution

## Core Principles

### I. Graceful Degradation

**Data:** The multi-source duration fallback chain (observed → generated
→ heat count → learned → default) MUST be maintained. New features MUST
NOT let a missing data source break the prediction pipeline.

**UI:** MUST be navigable on mobile viewports first, progressively
enhancing for larger screens. Core functionality (viewing predictions,
entering event IDs) MUST work without JavaScript; HTMX enhances rather
than gates the experience.

### II. Testable Without External Dependencies

All code MUST be testable using captured fixtures and the SQLite
backend. Tests MUST NOT require live API calls or DynamoDB.

The `conftest.py` isolation pattern (temp DB, forced SQLite mode) is the
standard for database-dependent tests.

Parsing functions consuming external data MUST be tested against captured
real-world fixtures (in `tests/fixtures/`), not solely synthetic data.
Fixtures preserve the exact upstream format and catch regressions when
parsing logic changes.

### III. Separation of Concerns & Architectural Consistency

Each module MUST have a single clear responsibility (fetching, parsing,
prediction, storage, presentation). Domain logic MUST NOT leak across
boundaries.

External systems (data sources, storage backends) MUST be accessed
through abstractions that can be swapped without changing consuming code.
The dual DynamoDB/SQLite backend is the model pattern.

The domain taxonomy (Competition → Session → Event → Heat) MUST be used
consistently across code, templates, and documentation. Where external
systems use different terms, map them at the boundary layer only.

Public interfaces (URLs, API responses) MUST remain stable. New external
identifiers MUST be mapped at the boundary, not propagated into internal
code.

**FastAPI conventions:**

- Configuration MUST use Pydantic `BaseSettings` (from
  `pydantic-settings`) instead of plain dataclasses with raw
  `os.getenv()` calls. This provides type validation, `.env` file
  support, and compatibility with FastAPI's `Depends()` system.
- Shared resources with setup/teardown semantics (HTTP clients, database
  connections) MUST be managed via the FastAPI `lifespan` context and
  injected into route handlers via `Depends()`, not created inline.
- Route handlers SHOULD use `Depends()` for cross-cutting concerns
  (settings access, cookie/header reading, shared clients) rather than
  importing module-level singletons directly.

### IV. Minimal & Responsible Dependencies

**Scope:** build-time footprint — packages, licenses, container image
size, cold-start impact.

- New dependencies MUST justify their cost against Lambda constraints
  (60s timeout, 512 MB memory).
- Dependencies MUST be open source under a permissive license (MIT,
  Apache 2.0, BSD, or equivalent).
- Dependencies MUST be established projects with a track record of
  stability and active maintenance.
- Prefer stdlib and existing deps (FastAPI, httpx, BeautifulSoup,
  Pydantic, boto3) over adding new libraries.

### V. Operability

- Structured JSON logging MUST provide enough context to debug incorrect
  predictions after the fact (discipline detected, duration source used,
  delay applied).
- Health checks (`/health`) MUST be maintained and reflect actual system
  readiness.
- Alerting thresholds (e.g., >5 errors/5min) MUST be documented
  alongside infrastructure.
- Upstream data source availability SHOULD be monitored — the app MUST
  behave predictably (clear error or stale data) when a source is
  unavailable.

### VI. Cost-Aware Growth

**Scope:** runtime infrastructure — AWS services, compute, storage,
network.

The app runs on a low-cost serverless stack (Lambda + DynamoDB on-demand
+ CloudFront). Changes that would require always-on compute, provisioned
capacity, additional AWS services, or significantly larger container
images MUST justify the operational cost increase and document the
trade-off.

### VII. Prediction Integrity

- Predictions MUST be validated and bounded. Showing no prediction is
  preferable to showing a wildly wrong one.
- Duration observations MUST be validated against plausibility bounds
  before being accepted (e.g., generated-time validation at 0.5×–2.0×
  expected, wall-clock learning capped at 3× static default).
- New data sources or prediction methods MUST include explicit
  validation logic to reject implausible values.

### VIII. Security & Data Minimization

- The app MUST NOT transmit or store PII or other sensitive data.
- No user accounts, no tracking, no unnecessary cookies.
- The app processes public competition data — nothing is collected about
  users themselves.
- New features MUST be evaluated for whether they introduce PII
  handling; if so, that is a blocking concern requiring explicit
  justification.

## External Data Sources

- External data sources (e.g., tracktiming.live) may be unversioned,
  undocumented, and could change without notice.
- Parsing code MUST be defensive.
- Format assertions MUST include a captured sample committed to
  `tests/fixtures/`. Assumptions about external data formats without
  evidence are treated as NEEDS CLARIFICATION.
- New data sources SHOULD follow the established fetch → parse → model
  pattern.
- Data source-specific logic MUST be isolated so it doesn't leak into
  the prediction engine.
- HTTP clients for upstream fetching MUST be shared via a single
  `httpx.AsyncClient` instance managed by the FastAPI lifespan, not
  created and destroyed per call. This reuses TCP connections and
  reduces latency, especially under Lambda's concurrent fetch patterns.

## Development Workflow

- Non-trivial features SHOULD start with a spec (using speckit) before
  implementation begins.
- New code MUST include tests following existing patterns (fixture-based,
  isolated DB).
- PRs MUST pass `pytest` via CI (GitHub Actions) before merge — CI is
  the enforcement mechanism, not human discipline.
- Changes should be focused — one concern per PR.
- Implementation tasks (e.g., from speckit tasks.md) MUST be scoped to
  fit within a single LLM context window. A task that requires reading
  more than ~800 lines of reference code plus producing significant
  output SHOULD be split into sub-tasks with distinct reference file
  sets. This prevents context compaction from degrading implementation
  quality.
- CLAUDE.md is the authoritative development reference and MUST be kept
  current with architectural changes.
- When implementation reveals behavior-affecting deviations from the spec
  or design artifacts, those artifacts MUST be updated before the feature
  is considered complete. Internal-only changes (refactoring,
  optimization) are exempt from this requirement.
- All routes MUST use GET. CloudFront OAC with Lambda Function URLs
  does not support POST request bodies (SigV4 payload signature
  mismatch causes 403s). Form submissions MUST use query parameters
  on GET requests, not POST bodies.
- When analysis or remediation proposes changes that alter plan
  assumptions (architecture choices, technical constraints, dependency
  decisions, or data model structure), the research decisions that
  informed those assumptions MUST be reviewed before the changes are
  applied. Specifically:
  - Each affected research.md entry MUST be evaluated for whether its
    original question, rationale, or alternatives considered still hold
    under the proposed change.
  - Research entries whose assumptions are invalidated MUST be re-resolved
    (new research or explicit re-confirmation) before proceeding.
  - The remediation plan MUST identify which research entries are affected
    and recommend review or re-research as a prerequisite to the plan
    change.

## Governance

- Lightweight: constitution lives in the repo, changes via normal
  commits.
- Amendments SHOULD include a version bump and brief rationale in the
  commit message.
- CLAUDE.md is the runtime development guidance file; the constitution
  governs design principles and trade-off evaluation.

**Version**: 1.5.0 | **Ratified**: 2026-03-13 | **Last Amended**: 2026-03-19
