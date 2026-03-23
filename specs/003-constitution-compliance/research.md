# Research: Constitution Compliance Fixes

## R-001: Pydantic BaseSettings migration pattern

**Question**: How to migrate from `dataclass` + `os.getenv()` to Pydantic `BaseSettings` without breaking tests?

**Decision**: Use `pydantic-settings` `BaseSettings` with `model_config = SettingsConfigDict(env_prefix="")` for environment variable mapping. Fields use default values matching current behavior. Tests override settings via `dependency_overrides` on the FastAPI app or environment variable patching.

**Rationale**: Constitution Principle III explicitly requires `pydantic-settings.BaseSettings`. This provides automatic type validation, `.env` file support, and clean integration with FastAPI's `Depends()` system. The `conftest.py` pattern of mutating settings attributes directly won't work with BaseSettings (frozen models), so tests must use either `dependency_overrides` or `monkeypatch.setenv` with fresh Settings construction.

**Alternatives considered**:
- Keep dataclass + add manual validation: Rejected — violates constitution's explicit requirement
- Use Pydantic `BaseModel` without `pydantic-settings`: Rejected — doesn't provide env var loading

## R-002: Index form no-JS fallback

**Question**: How to make the index form work without JavaScript when the target URL is `/schedule/{id}` (path parameter, not query parameter)?

**Decision**: Add a lightweight redirect route `GET /schedule` that accepts `?event_id=X` and redirects to `/schedule/{X}`. The HTML form gets `action="/schedule"` with `method="get"` — the browser submits as `/schedule?event_id=26008`, and the server redirects to `/schedule/26008`.

**Rationale**: Standard HTML forms can only submit field values as query parameters. The existing `/schedule/{event_id}` route expects the ID in the path. A server-side redirect is the simplest bridge. The redirect route is a GET (compliant with CloudFront OAC constraint) and uses `303 See Other` for correct HTTP semantics.

**Alternatives considered**:
- JavaScript progressive enhancement (keep JS, add `action` fallback): More complex, JS version diverges from no-JS behavior
- Change `/schedule/{id}` to accept query param `?event_id=X`: Would break existing bookmarks and links

## R-003: Learned-duration toggle no-JS fallback

**Question**: How to make the checkbox toggle work without JavaScript?

**Decision**: Wrap the checkbox in a `<form action="/settings/use-learned" method="get">` with hidden `event_id` field. Add a small submit button (e.g., "Apply") next to the checkbox that's visible when JS is disabled, or use a `<noscript>` block with a submit button. With JS enabled, the existing `onchange` behavior can remain for instant toggle.

**Rationale**: The `/settings/use-learned` route already exists and accepts GET with `?event_id=X&use_learned=on|off`. Wrapping in a form makes it natively submittable. The `checked` state of a checkbox is not sent if unchecked, so the form submission logic needs to handle the absence of the `use_learned` field as "off".

**Alternatives considered**:
- Replace checkbox with two links ("Enable" / "Disable"): Works but changes the UI pattern significantly
- Use `<select>` dropdown instead of checkbox: Overkill for a boolean toggle

## R-004: Shared httpx.AsyncClient lifecycle

**Question**: How to create, share, and close the httpx.AsyncClient in a way that works with both FastAPI lifespan and Lambda/Mangum?

**Decision**: Create the client in the FastAPI `lifespan` async context manager. Store on `app.state.http_client`. Inject into routes via `Depends()`. Fetcher functions accept the client as a parameter rather than creating their own. Configure `httpx.Limits(max_connections=50)` — since all traffic goes to one host (tracktiming.live), `max_connections` is effectively the per-host limit. 50 = 2x the semaphore-governed maximum of 25.

**Rationale**: FastAPI's lifespan is the standard pattern for resource lifecycle management. Mangum with `lifespan="auto"` correctly invokes the lifespan on Lambda cold starts and cleanup on shutdown. `app.state` is the standard place to store lifespan-created resources. The per-host limit of 50 provides 2x headroom over the semaphore-governed maximum of 25 concurrent connections.

**Alternatives considered**:
- Module-level client singleton: Doesn't integrate with FastAPI lifespan, no clean shutdown
- Create client in a `Depends()` with `yield`: Would create/close per-request, same as current bug

## R-005: Health endpoint database check

**Question**: How to check database connectivity for both SQLite and DynamoDB backends?

**Decision**: Add an async `check_health()` function in `database.py` that wraps synchronous DB calls via `asyncio.to_thread()`:
- SQLite path: attempts `SELECT 1` via `get_db()`
- DynamoDB path: calls `describe_table()` on the configured table
Both paths catch exceptions and return a status dict `{"status": "healthy"|"degraded", "detail": "<sanitized summary>"}`. The `detail` field uses human-readable summaries, not raw exception messages. The health endpoint wraps the call with `asyncio.wait_for(..., timeout=5.0)`.

**Rationale**: Both backends have lightweight "ping" operations. `SELECT 1` is the standard SQLite health check. DynamoDB `describe_table` confirms the table exists and is accessible without reading data. The 5-second timeout (FR-009) prevents slow subsystem checks from blocking the response.

**Alternatives considered**:
- Just check if settings are configured (no actual connectivity test): Doesn't verify actual readiness
- Read a record from the database: Heavier than needed, could fail if no data exists yet

## R-006: conftest.py adaptation for BaseSettings

**Question**: How should tests override settings when BaseSettings fields are validated and potentially frozen?

**Decision**: Use `monkeypatch.setenv` to set environment variables, then construct a fresh `Settings()` instance that reads from the patched environment. Use FastAPI's `app.dependency_overrides[get_settings]` to inject the test settings instance into routes. Keep settings non-frozen (Pydantic BaseSettings defaults to non-frozen) so existing direct-mutation patterns in tests continue to work during the transition.

**Rationale**: BaseSettings reads from environment variables at construction time. By patching env vars before constructing the instance, we get validated settings with test values. The `dependency_overrides` pattern is the standard FastAPI test pattern. Keeping settings mutable avoids a large test refactor while still gaining validation benefits.

**Alternatives considered**:
- Use `model_validate()` with explicit values: More verbose, doesn't test env var loading
- Make settings frozen and refactor all tests: More correct long-term but excessive scope for this feature
