# Implementation Plan: Constitution Compliance Fixes

**Branch**: `003-constitution-compliance` | **Date**: 2026-03-22 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-constitution-compliance/spec.md`

## Summary

Address all 7 constitution violations identified in `plans/constitution-observations.md`: migrate config to Pydantic BaseSettings, replace per-call httpx clients with a lifespan-managed shared instance, inject settings and HTTP client via `Depends()`, add no-JS fallbacks for index form and learned-duration toggle, upgrade health endpoint to check database connectivity, and remove the unused `python-multipart` dependency.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI, httpx, Pydantic, pydantic-settings (new), Jinja2, BeautifulSoup, boto3, Mangum
**Storage**: SQLite (local dev) / DynamoDB (production) — no schema changes
**Testing**: pytest + pytest-asyncio, moto for DynamoDB, captured HTML fixtures
**Target Platform**: AWS Lambda (Docker image from ECR) behind CloudFront OAC
**Project Type**: Web service (FastAPI + server-rendered Jinja2 templates)
**Performance Goals**: Per-host connection pool limit of 50 (2x semaphore max of 25)
**Constraints**: All routes must be GET (CloudFront OAC/SigV4 limitation), Lambda 60s timeout / 512MB memory
**Scale/Scope**: Single Lambda function, single DynamoDB table, low-traffic prediction tool

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Graceful Degradation | FIXING | P1/P2: Adding no-JS form fallbacks. Data chain unaffected. |
| II. Testable Without External Dependencies | PASS | conftest.py isolation pattern preserved. New tests follow fixture-based patterns. |
| III. Separation of Concerns | FIXING | P3: BaseSettings migration. P4: Shared client via lifespan. P5: Depends() injection for settings + client. |
| IV. Minimal Dependencies | FIXING | P7: Removing python-multipart. Adding pydantic-settings (justified: constitution requires it). |
| V. Operability | FIXING | P6: Health endpoint upgraded to check database. |
| VI. Cost-Aware Growth | PASS | No new AWS services or compute changes. |
| VII. Prediction Integrity | PASS | Prediction logic unchanged. |
| VIII. Security & Data Minimization | PASS | No PII changes. Attack surface reduced by removing unused dep. |
| External Data Sources | FIXING | Shared httpx.AsyncClient via lifespan (P4). |
| Development Workflow | PASS | All routes remain GET. CLAUDE.md will be updated. |

**Gate result: PASS** — all violations are being addressed by this feature; no new violations introduced. Adding `pydantic-settings` is justified by the constitution's explicit requirement for Pydantic BaseSettings.

### Post-Design Re-Check

| Concern | Status | Notes |
|---------|--------|-------|
| New route `GET /schedule` (redirect) | PASS | GET-only, compliant with CloudFront OAC constraint |
| `pydantic-settings` dependency | PASS | MIT license, Pydantic team, constitution requires it |
| Health endpoint response change | PASS | Always HTTP 200, no PII exposed, no infrastructure details leaked |
| Fetcher function signature change | PASS | Internal refactor, no public API change |
| conftest.py adaptation | PASS | Tests remain isolated via temp DB pattern |

**Post-design gate: PASS** — no new constitution violations introduced by the design.

## Research Phase: External Data Formats

No new external data formats consumed by this feature. All changes are internal refactoring.

## Research Findings

| Finding | Impact | Evidence |
|---------|--------|----------|
| `pydantic-settings` is MIT-licensed, maintained by the Pydantic team, and the standard way to configure FastAPI apps | Justifies new dependency per Principle IV | [PyPI: pydantic-settings](https://pypi.org/project/pydantic-settings/) |
| Current index form uses JS-only `onsubmit` with `preventDefault` and no `action` attribute | Must add `action="/schedule/"` — but path-based routing needs the ID in the URL, not as a query param | `app/templates/index.html:7` |
| Starlette's form action with `method="get"` sends field values as query params (`/schedule/?event_id=26008`) | Need a redirect route to rewrite `?event_id=X` → `/schedule/X` for clean URLs | FastAPI/Starlette routing behavior |
| The learned-duration toggle uses `onchange` JS handler to navigate to `/settings/use-learned?event_id=X&use_learned=on\|off` | Can wrap checkbox in a `<form>` with `action="/settings/use-learned"` and a hidden `event_id` field; needs a submit button for no-JS | `app/templates/schedule.html:17-19` |
| `conftest.py` directly mutates `settings.db_path` and `settings.dynamodb_table` on the dataclass | With BaseSettings, use FastAPI `dependency_overrides` or `monkeypatch` instead of direct attribute mutation | `tests/conftest.py:18-21` |
| httpx default pool limits: 100 total connections, 20 per-host | Configure `limits=httpx.Limits(max_connections=50)` — since all traffic goes to one host (tracktiming.live), `max_connections` is effectively the per-host limit. 50 = 2x semaphore max of 25. | httpx documentation |
| `fetcher.py` functions access `settings.tracktiming_base_url` directly via module-level import | After DI refactor, fetcher functions need the client and base_url passed as parameters | `app/fetcher.py:3,14` |
| `database.py:init_db()` is called in lifespan; health check can call `get_db()` to test SQLite connectivity or check DynamoDB table via `describe_table` | Health check needs to handle both backends | `app/database.py:76-79` |
| Lambda processes one request at a time per execution environment — shared client serves one user request's concurrent fetches | Pool limit of 50 is safe; no cross-request contention within an environment | AWS Lambda execution model |

## Project Structure

### Documentation (this feature)

```text
specs/003-constitution-compliance/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output (minimal — no data model changes)
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
app/
├── config.py            # MODIFY: dataclass → Pydantic BaseSettings
├── fetcher.py           # MODIFY: accept client param instead of creating per-call
├── main.py              # MODIFY: lifespan (create/close client), Depends() injection, health endpoint, redirect route
├── database.py          # MODIFY: add health_check() function
├── templates/
│   ├── index.html       # MODIFY: add form action for no-JS fallback
│   └── schedule.html    # MODIFY: wrap toggle in form for no-JS fallback

tests/
├── conftest.py          # MODIFY: adapt to BaseSettings (dependency_overrides or env vars)
├── test_main.py         # MODIFY: add health endpoint tests, update for DI
└── (other test files)   # VERIFY: no regressions

requirements.txt         # MODIFY: add pydantic-settings, remove python-multipart
CLAUDE.md                # MODIFY: update architecture section
```

**Structure Decision**: Existing flat `app/` structure preserved. No new modules needed — changes are within existing files.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Adding `pydantic-settings` dependency | Constitution explicitly requires Pydantic BaseSettings for configuration | Manual validation of os.getenv() is what the constitution is replacing |
| Redirect route for index form | HTML forms can only submit to a single URL with query params; clean `/schedule/{id}` URLs require a server-side rewrite | Client-side JS redirect is the current approach — and is the very problem being fixed |
