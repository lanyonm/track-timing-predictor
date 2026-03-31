# Tasks: DaisyUI Frontend Upgrade

**Input**: Design documents from `/specs/005-daisyui-frontend-upgrade/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ui-components.md, quickstart.md

**Design Reference**: Approved prototypes in `docs/daisyui-*.html` — each task references its corresponding prototype.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Replace the hand-written CSS with minimal app-specific overrides

- [ ] T001 Replace `static/style.css` with < 50 lines of app-specific overrides: `tr.status-completed` opacity/strikethrough, `.tabular-nums` font variant, `@media (max-width: 768px)` schedule card transform for `.schedule-table`, and `.export-btn` loading/done icon visibility toggle. Reference: `<style>` blocks in `docs/daisyui-schedule-prototype.html` and `docs/daisyui-palmares-prototype.html`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Base template with CDN links, navbar, and theme toggle — MUST be complete before any child template conversion

**⚠️ CRITICAL**: All child templates extend `base.html`. This must be done first.

- [ ] T002 Rewrite `app/templates/base.html`: add DaisyUI v4 CDN link (`cdn.jsdelivr.net/npm/daisyui@4/dist/full.min.css`), Tailwind CDN (`cdn.tailwindcss.com`), `data-theme` attribute on `<html>`, DaisyUI `navbar` with site title and Palmares nav link, `swap swap-rotate` theme toggle with sun/moon SVGs, inline JS for cookie read/write (`theme` cookie, `max-age=31536000`, `SameSite=Lax`) with `prefers-color-scheme` fallback, `bg-base-200` on `<body>`. Delivers FR-002, FR-008, US3. Reference: navbar and `<script>` in `docs/daisyui-index-prototype.html`

**Checkpoint**: Base template ready — child template conversion can begin

---

## Phase 3: US1+US2 - Consistent Visual Design + Mobile (Priority: P1) 🎯 MVP

**Goal**: Convert all 6 child templates to DaisyUI classes per approved prototypes, delivering consistent design (US1), mobile responsiveness (US2), and interactive feedback elements (US4) in a single pass

**Independent Test**: Navigate every page at desktop (1280px) and mobile (375px) widths; verify consistent component vocabulary, no horizontal scrolling, adequate tap targets

### Implementation

- [ ] T003 [P] [US1] Convert `app/templates/index.html` to DaisyUI: `card` with `card-body`, `form-control`/`input input-bordered`/`btn btn-primary w-full`, `divider`, meta links. Add inline `loading loading-spinner loading-sm` element in submit button with JS toggle for loading state (FR-015, US4-AS1). Reference: `docs/daisyui-index-prototype.html`

- [ ] T004 [P] [US1] Convert `app/templates/defaults.html` to DaisyUI: wrap table in `card bg-base-100 shadow-md` with `overflow-x-auto`, apply `table table-sm`, back link with `link link-hover`. Reference: `docs/daisyui-defaults-prototype.html`

- [ ] T005 [P] [US1] Convert `app/templates/learned.html` to DaisyUI: same card+table pattern as defaults, remove colored badges from "Used?" column — use plain text with `opacity-50` for "Not yet" rows, add styled empty state with icon and message. Reference: `docs/daisyui-learned-prototype.html`

- [ ] T006 [P] [US1] Convert `app/templates/schedule.html` to DaisyUI: schedule header with `flex items-baseline`, meta bar with `flex flex-wrap`, racer name form with `input input-bordered` + `btn btn-primary` (matching sizes), `toggle toggle-sm toggle-primary` for learned durations (FR-009, US4-AS4), `collapse collapse-arrow` for session containers, HTMX `hx-indicator` wired to `loading loading-spinner loading-xs` element (FR-017, US4-AS6). Preserve all `hx-get`/`hx-trigger`/`hx-swap` attributes and session open/close JS. Reference: `docs/daisyui-schedule-prototype.html`

- [ ] T007 [P] [US1] Convert `app/templates/_schedule_body.html` to DaisyUI: racer messages as `alert border border-info/20 bg-info/5` (info) and `alert border border-warning/20 bg-warning/5` (warning) per message type, `table table-sm schedule-table`, row states (`status-completed`, `active-row bg-warning/10`, `bg-success/5`, `opacity-50`, `racer-row bg-info/5`), action buttons as `btn btn-xs btn-outline` with faint color borders (`border-error/30`, `border-success/30`, `border-warning/30`, `border-info/30`), session badges as `badge badge-outline` with `px-3 py-2.5`, racer heat badges with `px-2 py-1` (FR-003, FR-004, FR-005, FR-006, FR-014). Reference: `docs/daisyui-schedule-prototype.html`

- [ ] T008 [P] [US1] Convert `app/templates/palmares.html` to DaisyUI: competition cards with `card bg-base-100 shadow-md` + `card-body p-0`, event list with `menu menu-sm`, edit form with `join`, share bar with `alert` + `btn btn-sm btn-ghost`, `<dialog class="modal">` for remove confirmation replacing `window.confirm()` (FR-011, US4-AS3), `toast toast-end toast-bottom` with `alert alert-success` for copy link feedback (US4-AS2), `.export-btn` with CSS-toggled loading/done icon states (FR-016, US4-AS5), empty state with icon + message + CTA button, visitor name form as card. The Remove action MUST remain an `<a href>` link (not a `<button>`); JS intercepts with `showModal()` and `preventDefault()`, falling back to direct navigation when JS is disabled (FR-013). Preserve all existing JS logic (copy link, CSV export fetch, edit toggle). Reference: `docs/daisyui-palmares-prototype.html`

**Checkpoint**: All pages should now display consistently with DaisyUI components, work on mobile, and include interactive feedback elements. Run `pytest` to verify existing tests still pass.

---

## Phase 4: Verification & Polish

**Purpose**: Validate all success criteria and update documentation

- [ ] T009 Run `pytest` to confirm all existing tests pass without modification (SC-004). Also verify SC-006: confirm `static/style.css` is under 50 lines (`wc -l static/style.css`) and that the DaisyUI CDN CSS payload is under 150KB uncompressed (`curl -sI 'https://cdn.jsdelivr.net/npm/daisyui@4/dist/full.min.css' | grep content-length`)

- [ ] T010 Visual review using `webapp-testing` skill (Playwright): start app with `uvicorn app.main:app`, capture screenshots of all pages (landing, schedule with sample data, palmares, defaults, learned) at desktop (1280px) and mobile (375px) viewports in both `data-theme="light"` and `data-theme="dark"`. Compare screenshots against the approved prototypes in `docs/daisyui-*.html` as the visual baseline — the implementation does not need to match pixel-for-pixel, but all components, layout structure, and interactive elements specified in the prototypes and `contracts/ui-components.md` must be present. Have UX designer agent review for consistency, contrast, layout, and adherence to acceptance criteria (SC-001, SC-002, SC-003, SC-007). Include test cases for: long event/competition names to verify text wrapping, a schedule with many events to verify scrolling, and the palmares Remove link with JS disabled to verify direct navigation fallback

- [ ] T011 Update `CLAUDE.md` Active Technologies section to include DaisyUI v4 + Tailwind CSS (CDN) for feature 005; note the 768px mobile breakpoint and theme cookie in relevant sections

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on T001 (style.css must exist before templates reference it)
- **Template Conversion (Phase 3)**: All depend on T002 (base.html) — BLOCKED until foundational complete
- **Verification (Phase 4)**: Depends on all Phase 3 tasks being complete

### Within Phase 3

- T003, T004, T005, T006, T007, T008 are all [P] — different files, can run in parallel
- T008 (palmares) is the most complex, so starting it early is recommended
- T006 and T007 (schedule pair) reference the same prototype but modify different files; can be parallel

### User Story Delivery

- **US1 (Consistent Design)**: Delivered by T001–T008
- **US2 (Mobile)**: Delivered by T001 (768px card transform CSS) + T003–T008 (DaisyUI responsive classes)
- **US3 (Theme Support)**: Delivered by T002 (base.html theme toggle + cookie JS)
- **US4 (Interactive Feedback)**: Delivered within T003 (loading button), T006 (learned toggle, refresh spinner), T008 (modal, toast, export states)

### Parallel Opportunities

```
Phase 1: T001
              ↓
Phase 2: T002
              ↓
Phase 3: T003 ─┬─ T004 ─┬─ T005 ─┬─ T006 ─┬─ T007 ─┬─ T008 [P]
               │         │        │         │         │
              (all 6 parallel — different files)
              ↓
Phase 4: T009 → T010 → T011
```

---

## Implementation Strategy

### MVP First (Phase 1 + 2 + 3)

1. T001: Replace style.css (5 min)
2. T002: Rewrite base.html with CDN + navbar + theme toggle (15 min)
3. T003–T008 in parallel: Convert all child templates (bulk of work)
4. **STOP and VALIDATE**: Run pytest, check all pages in browser at both viewports

### Incremental Delivery

1. T001 + T002 → Base ready, theme toggle works on bare pages
2. T003–T005 → Simple pages done (index, defaults, learned)
3. T006–T007 → Schedule pages done (most complex)
4. T008 → Palmares done (interactive feedback complete)
5. T009–T011 → Verification and documentation

---

## Notes

- All template tasks reference their approved prototype in `docs/` — implementation should match the prototype's HTML structure and DaisyUI classes
- The component vocabulary in `specs/005-daisyui-frontend-upgrade/contracts/ui-components.md` is the authoritative class reference
- Do NOT modify Python backend code, route handlers, HTMX attributes, form actions, or JavaScript business logic
- Existing JS (HTMX session open/close, CSV export fetch, copy link clipboard) must be preserved — only the markup it targets changes
