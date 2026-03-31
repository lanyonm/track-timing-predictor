# Quickstart: DaisyUI Frontend Upgrade

**Branch**: `005-daisyui-frontend-upgrade` | **Date**: 2026-03-31

## What This Feature Does

Replaces the hand-written CSS (`static/style.css`) with DaisyUI component classes applied directly in the Jinja2 templates. Adds light/dark theme support with a header toggle and cookie persistence.

## Prerequisites

No new dependencies. DaisyUI and Tailwind CSS are loaded via CDN in `base.html`. No pip packages, no build step changes.

## Key Files to Modify

| File | Change |
|---|---|
| `app/templates/base.html` | Add CDN links, navbar with theme toggle, `data-theme` + cookie JS |
| `app/templates/index.html` | Replace custom form CSS with DaisyUI card/form/button classes |
| `app/templates/schedule.html` | Replace racer form, meta bar, toggle with DaisyUI components |
| `app/templates/_schedule_body.html` | Replace table/badge/button CSS with DaisyUI classes, add alert variants |
| `app/templates/palmares.html` | Replace cards, menus, modals, toasts with DaisyUI components |
| `app/templates/defaults.html` | Replace table CSS with DaisyUI table inside card |
| `app/templates/learned.html` | Replace table CSS with DaisyUI table inside card, plain text for "Used?" |
| `static/style.css` | Replace ~600 lines with < 50 lines of app-specific overrides |

## Design Reference

Open the approved prototypes in a browser for visual reference:

```bash
open docs/daisyui-index-prototype.html
open docs/daisyui-schedule-prototype.html
open docs/daisyui-palmares-prototype.html
open docs/daisyui-defaults-prototype.html
open docs/daisyui-learned-prototype.html
```

## Verification

```bash
# All existing tests must pass unchanged
pytest

# Visual review via webapp-testing skill (Playwright)
# Capture screenshots at 1280px and 375px in both light and dark themes
# Review with UX designer agent
```

## What NOT to Change

- No Python backend code (routes, models, fetchers, parsers, predictors, database)
- No route signatures or response shapes
- No HTMX attributes (hx-get, hx-trigger, hx-swap, hx-indicator)
- No form `action` or `method` attributes
- No JavaScript business logic (CSV export fetch, copy link, HTMX event listeners)
