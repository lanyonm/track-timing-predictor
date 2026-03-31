# Research: DaisyUI Frontend Upgrade

**Branch**: `005-daisyui-frontend-upgrade` | **Date**: 2026-03-31

## R1: DaisyUI CDN Loading Approach

**Decision**: Load DaisyUI v4 CSS via jsDelivr CDN (`cdn.jsdelivr.net/npm/daisyui@4/dist/full.min.css`) alongside the Tailwind CSS CDN (`cdn.tailwindcss.com`).

**Rationale**: The CDN approach requires zero build-step changes. The existing deployment pipeline (Docker image → ECR → Lambda) is unaffected. DaisyUI's full CSS is ~40KB gzipped, well within the SC-006 budget of 150KB uncompressed. The Tailwind CDN is needed for utility classes used alongside DaisyUI semantic classes (e.g., `flex`, `gap-2`, `opacity-50`).

**Alternatives considered**:
- **npm install + build step**: Would require adding Node.js tooling, a Tailwind config file, and a CSS build command to the Docker build. Rejected — adds complexity for no functional benefit in this project.
- **DaisyUI v5**: Available but newer and less battle-tested. v4 is stable and well-documented. Can upgrade later.
- **Self-hosted CSS**: Download and serve from `/static/`. Rejected — loses CDN caching benefits, adds maintenance burden for updates.

**Evidence**: Validated in approved prototypes (`docs/daisyui-*.html`) — all load correctly via CDN with no CORS or CSP issues when opened as local files.

## R2: Theme Toggle and Cookie Persistence

**Decision**: Use DaisyUI's `data-theme` attribute on `<html>` for theme switching. A `swap` component in the navbar provides the toggle. Client-side JavaScript reads/writes a `theme` cookie (`max-age=31536000`, `SameSite=Lax`, `path=/`). On load: cookie wins over system preference; if no cookie, `prefers-color-scheme` media query determines default.

**Rationale**: DaisyUI themes are purely CSS-driven via `data-theme` — no server-side rendering differences needed. Cookie approach is consistent with existing racer-name and use-learned cookies. No new backend route is needed (client-side JS sets the cookie directly and toggles the attribute).

**Alternatives considered**:
- **Server-side route for theme cookie**: Would require a new GET route (e.g., `/settings/theme`). Rejected — unnecessary round-trip; client-side JS can set cookies and toggle instantly.
- **localStorage**: Works but cookies are accessible server-side if needed later. Consistent with existing cookie patterns.

**Evidence**: Implemented and approved in prototypes. Theme persists across page navigations within the prototype set.

## R3: HTMX Compatibility with DaisyUI Markup

**Decision**: DaisyUI classes applied to Jinja2 templates are fully compatible with HTMX `hx-swap="innerHTML"`. The HTMX partial (`_schedule_body.html`) renders content that gets swapped into a container — DaisyUI classes on the swapped content work without re-initialization since DaisyUI is CSS-only (no JS component initialization required).

**Rationale**: Unlike React-based component libraries, DaisyUI is a Tailwind plugin that outputs CSS classes. There is no JavaScript runtime or component lifecycle. HTMX can freely swap HTML content and the new content will be styled correctly as long as the CSS is loaded in the parent page.

**Alternatives considered**: None — this is a key advantage of choosing DaisyUI over React-based alternatives.

**Evidence**: DaisyUI documentation confirms CSS-only approach. The `collapse`, `table`, `badge`, `btn`, `alert`, `modal`, and `toast` components all work via class names without JS initialization.

## R4: Mobile Breakpoint at 768px

**Decision**: The schedule table-to-card transformation triggers at `max-width: 768px` instead of the original 600px.

**Rationale**: During visual review, an intermediate viewport zone (~601–768px) was identified where action buttons (e.g., "Results" + "Audit") stack vertically inside narrow table cells, creating a poor layout. Raising the breakpoint to 768px (standard tablet boundary) eliminates this zone entirely.

**Alternatives considered**:
- **Keep 600px, make buttons smaller**: Rejected — would reduce tap targets below the 44px minimum.
- **Use 640px or 700px**: Rejected — 768px is a standard breakpoint that aligns with common device widths and Tailwind's `md:` default.

**Evidence**: Observed in approved schedule prototype. 768px breakpoint tested and approved.

## R5: DaisyUI Components to Use Per Template

**Decision**: Component mapping based on approved prototypes:

| Template | DaisyUI Components |
|---|---|
| `base.html` | `navbar`, `swap` (theme toggle) |
| `index.html` | `card`, `form-control`, `input`, `btn`, `loading`, `divider` |
| `schedule.html` | `collapse`, `toggle`, `btn`, `input`, `alert`, `loading` |
| `_schedule_body.html` | `table`, `badge`, `btn` (outline variants), `alert`, `loading` |
| `palmares.html` | `card`, `menu`, `join`, `btn`, `modal`, `toast`, `alert`, `divider` |
| `defaults.html` | `card`, `table` |
| `learned.html` | `card`, `table` |

**Rationale**: Mapping derived directly from the approved prototype HTML files. Each component was visually reviewed and approved.

## R6: Custom CSS Overrides

**Decision**: A minimal `static/style.css` (target: < 50 lines) will contain only:
1. `tr.status-completed td` opacity and strikethrough rules
2. `.tabular-nums` for time column font variant
3. `@media (max-width: 768px)` card transform for `.schedule-table`
4. `.export-btn` state toggling (loading/done icon visibility)

**Rationale**: These are app-specific behaviors that don't map to DaisyUI semantic classes. Everything else (colors, spacing, borders, typography, badges, buttons, forms) is handled by DaisyUI + Tailwind utility classes.

**Evidence**: Approved prototypes use exactly this pattern — DaisyUI classes for components with a small `<style>` block for schedule-specific overrides.
