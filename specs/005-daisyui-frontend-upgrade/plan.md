# Implementation Plan: DaisyUI Frontend Upgrade

**Branch**: `005-daisyui-frontend-upgrade` | **Date**: 2026-03-31 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/005-daisyui-frontend-upgrade/spec.md`

## Summary

Replace the hand-written `static/style.css` (~600 lines) with DaisyUI component classes applied directly in Jinja2 templates, loaded via CDN. Add light/dark theme support with a header toggle persisted in a cookie. Mobile breakpoint raised to 768px. No backend code changes. Approved visual prototypes in `docs/daisyui-*.html` serve as the design reference.

## Technical Context

**Language/Version**: Python 3.11+ (no changes)
**Primary Dependencies**: FastAPI, Jinja2, HTMX (existing); DaisyUI v4 + Tailwind CSS (CDN, client-side only)
**Storage**: N/A — no data model changes (one new client-side cookie for theme)
**Testing**: pytest (existing tests must pass unchanged); Playwright via `webapp-testing` skill for visual review
**Target Platform**: Lambda + CloudFront (existing); web browsers (desktop + mobile)
**Project Type**: web-service (presentation layer only)
**Performance Goals**: CSS payload < 150KB uncompressed (SC-006)
**Constraints**: GET-only routes, CDN-only for CSS (no build step), HTMX compatibility required
**Scale/Scope**: 7 Jinja2 templates + 1 CSS file to modify

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Graceful Degradation | PASS | Mobile-first (768px breakpoint), works without JS (FR-013), HTMX enhances |
| II. Testable Without External Dependencies | PASS | No new external deps to mock; existing tests unchanged (SC-004); visual review via local Playwright |
| III. Separation of Concerns | PASS | Presentation-only changes; no domain logic affected; templates remain Jinja2 |
| IV. Minimal & Responsible Dependencies | PASS | DaisyUI + Tailwind loaded via CDN — zero impact on Lambda cold start, container size, or pip dependencies. MIT licensed, actively maintained |
| V. Operability | PASS | No changes to health checks, logging, or monitoring |
| VI. Cost-Aware Growth | PASS | CDN resources are free; no new AWS services |
| VII. Prediction Integrity | PASS | No prediction logic changes |
| VIII. Security & Data Minimization | PASS | New `theme` cookie is functional (not tracking), contains only `light`/`dark` string. No PII. Consistent with existing cookie pattern |

**Post-Phase 1 re-check**: All gates still pass. No new dependencies, no architectural changes.

## Research Phase: External Data Formats

Not applicable — this feature does not consume new external data formats. DaisyUI is a CSS framework loaded via CDN.

## Research Findings

| Finding | Impact | Evidence |
|---|---|---|
| DaisyUI v4 CSS is ~40KB gzipped via jsDelivr CDN | Within SC-006 budget (150KB uncompressed) | [research.md R1](research.md#r1-daisyui-cdn-loading-approach) |
| DaisyUI is CSS-only — no JS initialization needed | HTMX innerHTML swap fully compatible | [research.md R3](research.md#r3-htmx-compatibility-with-daisyui-markup) |
| 768px mobile breakpoint eliminates button stacking zone | Approved during visual review | [research.md R4](research.md#r4-mobile-breakpoint-at-768px) |
| Theme toggle uses `data-theme` attr + client-side cookie | No backend route needed | [research.md R2](research.md#r2-theme-toggle-and-cookie-persistence) |
| Custom CSS overrides fit in < 50 lines | Status row styling + mobile card transform + export button states | [research.md R6](research.md#r6-custom-css-overrides) |

## Project Structure

### Documentation (this feature)

```text
specs/005-daisyui-frontend-upgrade/
├── plan.md                        # This file
├── spec.md                        # Feature specification
├── research.md                    # Phase 0 research decisions
├── data-model.md                  # Data model (cookie only)
├── quickstart.md                  # Implementation quickstart
├── contracts/
│   └── ui-components.md           # DaisyUI component vocabulary
├── checklists/
│   └── requirements.md            # Spec quality checklist
└── tasks.md                       # Phase 2 output (next step)
```

### Source Code (repository root)

```text
app/templates/
├── base.html              # CDN links, navbar + theme toggle, data-theme JS
├── index.html             # Card form with loading button
├── schedule.html          # Racer form, meta bar, learned toggle
├── _schedule_body.html    # Table/badges/buttons, alert variants, refresh indicator
├── palmares.html          # Cards, menu, modal, toast, export states
├── defaults.html          # Table in card
└── learned.html           # Table in card, plain text "Used?" column

static/
└── style.css              # Reduced to < 50 lines of app-specific overrides

docs/
├── daisyui-index-prototype.html       # Approved visual reference
├── daisyui-schedule-prototype.html    # Approved visual reference
├── daisyui-palmares-prototype.html    # Approved visual reference
├── daisyui-defaults-prototype.html    # Approved visual reference
└── daisyui-learned-prototype.html     # Approved visual reference
```

**Structure Decision**: Existing single-project structure preserved. Only template files and the CSS file are modified. Prototypes in `docs/` serve as design reference during implementation.

## Complexity Tracking

No constitution violations to justify.
