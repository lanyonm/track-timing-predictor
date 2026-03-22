# Feature Specification: Constitution Compliance Fixes

**Feature Branch**: `003-constitution-compliance`
**Created**: 2026-03-22
**Status**: Draft
**Input**: User description: "Address all issues identified in plans/constitution-observations.md"

## Clarifications

### Session 2026-03-22

- Q: What HTTP status code should the health endpoint return when a subsystem is degraded? → A: Always return HTTP 200 with JSON body indicating per-component status (healthy/degraded). This keeps the app routable via CloudFront and aligns with graceful degradation.
- Q: Should the health endpoint probe the upstream tracktiming.live API? → A: No. Check database only; upstream monitoring handled separately via logging. The app degrades gracefully when tracktiming.live is unavailable.
- Q: Should route dependency injection be MUST or SHOULD? → A: SHOULD, matching the constitution. Inject settings and HTTP client via Depends(); other lightweight utilities may be imported directly.
- Q: What should the shared HTTP client's per-host connection pool limit be? → A: 50, which is 2x the max theoretical concurrent connection count (25, from semaphores 5+10+10). Semaphores remain the primary concurrency control; the pool limit is a defense-in-depth backstop.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - App Works Without JavaScript (Priority: P1)

A user visits the landing page on a device or browser with JavaScript disabled (or where JS fails to load). They enter a competition ID and submit the form. The app navigates them to the schedule page without requiring any client-side scripting.

**Why this priority**: The constitution mandates graceful degradation. The current index form has no `action` attribute — it is completely non-functional without JavaScript. This is a HIGH severity violation affecting every user's entry point to the app.

**Independent Test**: Can be fully tested by disabling JavaScript in the browser, loading the landing page, entering a competition ID, and confirming form submission navigates to the schedule page.

**Acceptance Scenarios**:

1. **Given** a user visits the landing page with JavaScript disabled, **When** they enter a competition ID and submit the form, **Then** the browser navigates to `/schedule/{id}` and displays the schedule.
2. **Given** a user visits the landing page with JavaScript enabled, **When** they enter a competition ID and submit the form, **Then** the behavior is identical to the no-JS case (clean URL navigation).

---

### User Story 2 - Learned Duration Toggle Works Without JavaScript (Priority: P2)

A user viewing a schedule page with JavaScript disabled can still toggle the "Use learned durations" setting. The toggle degrades to a standard form-based interaction rather than relying on an `onchange` JavaScript handler.

**Why this priority**: This is a MEDIUM severity graceful degradation violation. While the schedule page itself renders server-side correctly, the toggle is completely inoperable without JavaScript.

**Independent Test**: Can be tested by disabling JavaScript, loading a schedule page, and verifying the learned-durations toggle can be changed and takes effect on the next page load.

**Acceptance Scenarios**:

1. **Given** a user views a schedule with JavaScript disabled, **When** they interact with the learned-duration toggle, **Then** the setting change takes effect and the page reloads with updated predictions.
2. **Given** a user views a schedule with JavaScript enabled, **When** they interact with the toggle, **Then** the experience is at least as smooth as the current behavior (immediate navigation).

---

### User Story 3 - Configuration Is Validated at Startup (Priority: P3)

When the application starts, configuration values are validated (e.g., types are correct, required values are present). Invalid configuration causes a clear startup error rather than a runtime failure later.

**Why this priority**: The constitution requires configuration via validated settings rather than raw environment variable reads. Using validated settings prevents subtle runtime errors from misconfigured environment variables (e.g., a non-integer refresh interval, an empty DB path).

**Independent Test**: Can be tested by setting an invalid environment variable value and confirming the application fails to start with a descriptive error message.

**Acceptance Scenarios**:

1. **Given** all environment variables are correctly set, **When** the application starts, **Then** it boots successfully with validated configuration.
2. **Given** an environment variable has an invalid value (e.g., non-numeric), **When** the application starts, **Then** it fails immediately with a clear validation error.
3. **Given** no environment variables are set, **When** the application starts, **Then** it boots with documented default values.

---

### User Story 4 - Shared HTTP Client Lifecycle (Priority: P4)

The application manages upstream HTTP connections through a shared client that is created at startup and properly closed at shutdown. This prevents resource leaks and ensures connection pooling works correctly.

**Why this priority**: The constitution explicitly requires HTTP clients to be shared via a lifespan-managed instance. The current per-call client pattern creates unnecessary overhead and risks resource leaks, though it has been functional in practice.

**Independent Test**: Can be tested by starting the app, making several schedule requests, and confirming that a single HTTP client is reused across requests rather than creating new ones per call.

**Acceptance Scenarios**:

1. **Given** the application is running, **When** multiple schedule requests are made, **Then** all upstream HTTP calls share the same client connection pool.
2. **Given** the application is shutting down, **When** the shutdown sequence completes, **Then** the shared HTTP client is properly closed and all connections are released.

---

### User Story 5 - Route Dependencies Are Injected (Priority: P5)

Route handlers receive key dependencies (configuration settings, HTTP client) through dependency injection rather than importing module-level singletons directly. This applies to cross-cutting concerns with setup/teardown semantics or that benefit from test substitution. Other lightweight utilities (e.g., pure helper functions) may still be imported directly.

**Why this priority**: MEDIUM severity constitution recommendation. While functional, direct imports of settings and HTTP clients in route handlers make testing harder and miss the constitution's SHOULD-level guidance.

**Independent Test**: Can be tested by verifying route handlers accept settings and HTTP client as injected parameters, and that tests can substitute mock dependencies without monkey-patching module-level imports.

**Acceptance Scenarios**:

1. **Given** a route handler needs configuration settings or an HTTP client, **When** it processes a request, **Then** it receives those dependencies via injection rather than direct module imports.
2. **Given** a test needs to isolate a route handler, **When** the test overrides injected settings or HTTP client, **Then** the handler uses the overridden values without requiring module-level patching.
3. **Given** a route handler uses a lightweight utility (e.g., a pure function), **When** it processes a request, **Then** the utility may be imported directly without requiring injection.

---

### User Story 6 - Health Endpoint Checks System Readiness (Priority: P6)

The health endpoint reports whether the application's key local subsystems (database connectivity) are operational, rather than always returning a static "ok" response. Upstream API availability is monitored separately via logging, not via the health endpoint.

**Why this priority**: LOW severity gap. The current health check doesn't verify actual system readiness, which limits its usefulness for monitoring and load balancer health checks.

**Independent Test**: Can be tested by verifying the health endpoint returns healthy status when subsystems are reachable, and degraded status when a subsystem is unavailable.

**Acceptance Scenarios**:

1. **Given** the database is reachable, **When** the health endpoint is called, **Then** it returns HTTP 200 with the database component marked healthy.
2. **Given** the database is unreachable, **When** the health endpoint is called, **Then** it returns HTTP 200 with the database component marked degraded and a description of the issue.
3. **Given** the health endpoint is called, **Then** it always returns HTTP 200 and responds within a reasonable time (does not block on slow subsystem checks).

---

### User Story 7 - Unused Dependency Removed (Priority: P7)

The `python-multipart` package is removed from the project's dependencies since no routes use form POST bodies and all routes are GET-only.

**Why this priority**: LOW severity. An unnecessary dependency increases the attack surface and image size, but has no functional impact.

**Independent Test**: Can be tested by removing the dependency, running the full test suite, and confirming all tests pass.

**Acceptance Scenarios**:

1. **Given** `python-multipart` is removed from dependencies, **When** the full test suite is run, **Then** all tests pass.
2. **Given** `python-multipart` is removed, **When** the application is started, **Then** all routes function correctly.

---

### Edge Cases

- The shared HTTP client's connection pool limit (50) is set to 2x the semaphore-governed maximum (25). Semaphores remain the primary concurrency control; the pool limit is a defense-in-depth backstop against accidental semaphore changes.
- Health endpoint slow checks: handled by FR-009 (5-second timeout via `asyncio.wait_for`). If the database check exceeds 5 seconds, the endpoint returns a degraded status with "Health check timed out."
- Database backend determined at startup from configuration. Runtime environment variable changes require an app restart; the health check uses whatever backend was configured at startup.
- Index form with partial JS: after the fix, the `onsubmit` JS handler is removed and the form uses a native `action` attribute. The form works via standard HTML submission regardless of JavaScript state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The landing page form MUST submit correctly without JavaScript by using a standard HTML form action
- **FR-002**: The learned-duration toggle MUST function without JavaScript through a form-based fallback mechanism
- **FR-003**: Application configuration MUST be validated at startup, rejecting invalid values with clear error messages
- **FR-004**: All configuration values MUST have documented defaults that allow the application to start without any environment variables set
- **FR-005**: The application MUST create a single shared HTTP client at startup and close it on shutdown, with a per-host connection pool limit of 50 (2x the max theoretical concurrent connections governed by semaphores)
- **FR-006**: All upstream HTTP requests MUST use the shared client instance rather than creating per-call clients
- **FR-007**: Route handlers SHOULD receive configuration and HTTP client dependencies via injection (matching the constitution's SHOULD requirement); other lightweight utilities may be imported directly
- **FR-008**: The health endpoint MUST always return HTTP 200, checking database connectivity and reporting per-component status (healthy/degraded) in the JSON body; upstream API availability is not checked by the health endpoint
- **FR-009**: The health endpoint MUST respond within 5 seconds even if subsystem checks are slow
- **FR-010**: The `python-multipart` dependency MUST be removed from the project's dependency list
- **FR-011**: All existing tests MUST continue to pass after these changes
- **FR-012**: The existing user-facing behavior (schedule predictions, racer highlighting, live polling) MUST remain unchanged

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 7 constitution violations identified in the audit are resolved, bringing the compliance score to 100%
- **SC-002**: The landing page is fully functional with JavaScript disabled — users can enter a competition ID and navigate to the schedule
- **SC-003**: The full test suite passes with no regressions
- **SC-004**: The health endpoint accurately reflects system state — returning degraded status when a subsystem is unavailable
- **SC-005**: No unused dependencies remain in the project's dependency list

## Assumptions

- The existing route URL structure (`/schedule/{event_id}`) and user-facing behavior will not change
- The shared HTTP client pattern is appropriate for the Lambda deployment model (the constitution requires it, and Mangum's lifecycle management will handle client teardown)
- Health check database probe can use a lightweight operation (e.g., a simple query) to avoid impacting response time; upstream API is not probed by the health endpoint
- The learned-duration toggle can be wrapped in a small form element that submits via GET without affecting the page layout or UX
