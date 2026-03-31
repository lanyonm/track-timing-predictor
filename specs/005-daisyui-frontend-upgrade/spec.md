# Feature Specification: DaisyUI Frontend Upgrade

**Feature Branch**: `005-daisyui-frontend-upgrade`
**Created**: 2026-03-26
**Status**: Draft
**Input**: User description: "I would like to upgrade the front-end of the application with DaisyUI to bring more consistency and visual appeal to the UI of the application. Mobile and desktop viewports should both be considered for this feature."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Consistent Visual Design Across All Pages (Priority: P1)

A user navigates between the landing page, schedule view, palmares page, and data reference pages (defaults and learned durations). Every page shares the same visual language — consistent card styling, button shapes, colors, typography, spacing, and navigation. The application feels cohesive rather than a collection of separately styled pages.

**Why this priority**: Visual consistency is the primary driver of this feature. If only one story is delivered, a unified look across all pages provides the most value.

**Independent Test**: Can be tested by navigating every page in the application and verifying consistent use of component styles, colors, and spacing.

**Acceptance Scenarios**:

1. **Given** the landing page, **When** a user loads it on desktop, **Then** they see a styled card with form input and button using the application's design system
2. **Given** the schedule page, **When** a user views the event table within collapsible sessions, **Then** they see styled session headers, table rows, badges, and action buttons consistent with the rest of the application
3. **Given** the palmares page, **When** a user views their competition cards, **Then** they see styled cards, event rows, share controls, and edit/remove actions using the same component vocabulary as other pages
4. **Given** the defaults or learned durations page, **When** a user views the data table, **Then** they see a styled table with consistent header, row, and cell formatting
5. **Given** any page, **When** a user views the site header, **Then** they see a styled navigation bar with site title and nav links

---

### User Story 2 - Mobile-Optimized Experience (Priority: P1)

A user opens the application on a mobile device (viewport width ≤ 768px). The layout adapts gracefully — the schedule transforms from table rows to card-based layouts, forms stack vertically, buttons have adequate tap targets, and no horizontal scrolling is required. The mobile experience feels intentional, not just a scaled-down desktop.

**Why this priority**: A large portion of users check race schedules on their phones at the velodrome. Mobile is a primary use case, not an afterthought.

**Independent Test**: Can be tested by resizing the browser to mobile widths and verifying all pages are usable without horizontal scrolling, with adequate touch targets.

**Acceptance Scenarios**:

1. **Given** a mobile viewport, **When** a user views the schedule page, **Then** events display as stacked cards rather than table rows, with event name, time, duration, and action buttons clearly visible
2. **Given** a mobile viewport, **When** a user taps a form button, **Then** the button has a minimum touch target of 44px height
3. **Given** a mobile viewport, **When** a user views any page, **Then** no horizontal scrolling is required
4. **Given** a desktop viewport, **When** a user views the schedule page, **Then** events display in a traditional table layout with columns for event, predicted start, duration, and status

---

### User Story 3 - Theme Support with Light and Dark Modes (Priority: P2)

A user can view the application in either a light or dark color scheme. The application respects the user's system preference by default and provides a visible toggle in the header to manually override the theme. The chosen theme applies consistently across all pages and components.

**Why this priority**: Dark mode improves readability in dimly lit velodromes and is a common user expectation for modern web applications. It builds on the visual consistency achieved in US1.

**Independent Test**: Can be tested by toggling system color scheme preference and using the manual theme toggle, verifying all pages render correctly in both themes.

**Acceptance Scenarios**:

1. **Given** a user with a dark system preference, **When** they load any page, **Then** the application renders in a dark color scheme
2. **Given** a user with a light system preference, **When** they load any page, **Then** the application renders in a light color scheme
3. **Given** any page, **When** a user clicks the theme toggle in the header, **Then** the application switches between light and dark modes and the choice is persisted in a cookie
4. **Given** a user who previously selected dark mode via the toggle, **When** they return to the application in a new session, **Then** the application renders in dark mode regardless of system preference
5. **Given** a dark theme, **When** the user views schedule status badges (delay, ahead), racer highlights, and active event indicators, **Then** each is clearly distinguishable with appropriate contrast

---

### User Story 4 - Improved Interactive Feedback (Priority: P3)

A user performs interactive actions — submitting the homepage form, copying a share link, confirming deletion of a palmares competition, toggling learned durations, exporting CSV data — and receives polished visual feedback. Confirmations use styled dialogs instead of browser-native `window.confirm()`, status messages use styled notifications instead of plain inline text, and long-running actions show loading indicators.

**Why this priority**: These are incremental polish improvements that enhance the feel of the application but are not critical to usability.

**Independent Test**: Can be tested by performing each interactive action and verifying appropriate visual feedback appears.

**Acceptance Scenarios**:

1. **Given** the landing page, **When** a user submits the Event ID form, **Then** the "Load Schedule" button shows a loading state while the schedule is being fetched
2. **Given** the palmares page, **When** a user clicks "Copy Link", **Then** a styled notification confirms the link was copied
3. **Given** the palmares page, **When** a user clicks "Remove" on a competition, **Then** a styled confirmation dialog appears instead of the browser-native confirm box
4. **Given** the schedule page, **When** the "Use learned durations" toggle is present, **Then** it uses a styled toggle or switch control consistent with the design system
5. **Given** the palmares page, **When** a user clicks the CSV export button, **Then** the button shows a loading state during download and a styled error indicator on failure
6. **Given** the schedule page, **When** the HTMX auto-refresh fires every 30 seconds, **Then** a styled loading indicator (spinner or similar) is shown instead of plain text

---

### Edge Cases

- What happens when a user has JavaScript disabled? The application must remain functional — forms submit, pages render, and navigation works without JS. Interactive enhancements (toasts, modals) gracefully degrade to basic alternatives (inline text, page redirects).
- What happens when a page has no data (empty learned durations, empty palmares)? Empty states should be visually styled with appropriate messaging, not just unstyled text.
- What happens with very long event names or competition names? Text should truncate or wrap gracefully without breaking the layout on both mobile and desktop.
- What happens with sessions containing 30+ events? The schedule table/card list should remain performant and scrollable without layout issues.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: All application pages (landing, schedule, schedule partial, palmares, defaults, learned durations) MUST use a consistent component design system
- **FR-002**: The base layout (header, navigation, main content area) MUST be styled consistently across all pages
- **FR-003**: The schedule page's collapsible session sections MUST render with styled accordion/collapsible components
- **FR-004**: Schedule event rows MUST display status visually (completed, upcoming, active, not ready) using styled badges, background colors, or border indicators
- **FR-005**: Racer-matched events MUST remain visually distinct from non-matched events in both light and dark themes
- **FR-006**: The mobile layout (≤ 768px) MUST transform schedule tables into a card-based layout
- **FR-007**: All interactive buttons MUST have a minimum touch target height of 44px on mobile viewports
- **FR-008**: The application MUST support light and dark color schemes, defaulting to the user's system preference, with a visible toggle in the header to manually override; the manual choice MUST be persisted in a cookie across sessions
- **FR-009**: All form inputs and buttons MUST use styled components from the design system
- **FR-010**: Empty states (no palmares, no learned durations, no schedule data) MUST display styled placeholder content
- **FR-011**: The palmares remove confirmation MUST use a styled modal dialog
- **FR-012**: All existing functionality (HTMX live polling, form submissions, cookie-based settings, CSV export, share link copy) MUST continue to work unchanged after the upgrade
- **FR-013**: The application MUST function without JavaScript for core navigation and form submission (progressive enhancement)
- **FR-014**: Event action buttons (Live, Results, Audit, Start List) MUST be visually distinguishable from each other
- **FR-015**: The homepage "Load Schedule" button MUST show a loading state while the schedule is being fetched
- **FR-016**: The palmares CSV export button MUST show a loading state during download and a styled error indicator on failure
- **FR-017**: The schedule page HTMX auto-refresh MUST use a styled loading indicator instead of plain "Refreshing..." text

### Assumptions

- The existing server-rendered architecture (Jinja2 templates + HTMX) will be preserved; this is a CSS/component-class upgrade, not a framework migration
- DaisyUI will be loaded via CDN; no build-step changes are required for the existing deployment pipeline
- The current custom `static/style.css` will be replaced by DaisyUI classes applied directly in the Jinja2 templates
- No changes to Python backend code, route handlers, or data models are needed
- The HTMX polling mechanism on the schedule page will continue to work with the updated markup

## Clarifications

### Session 2026-03-31

- Q: Which additional interactive feedback points beyond copy-link, remove-confirm, and learned-toggle should be in scope for US4? → A: CSV export states, HTMX refresh indicator, and homepage Load Schedule loading state
- Q: Should the application offer a user-facing theme toggle, or only follow system preference? → A: System preference by default + visible toggle in the header to override
- Q: Should the theme preference persist across sessions? → A: Yes, persist in a cookie (consistent with existing racer-name/learned cookies)
- Q: Should success criteria include a visual review step using Playwright screenshots reviewed by a UX agent? → A: Yes, add SC-007 requiring webapp-testing skill (Playwright) screenshots at both viewports and themes, reviewed by UX designer agent

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 7 templates (base, index, schedule, _schedule_body, palmares, defaults, learned) use a unified component vocabulary with no page-specific one-off styles
- **SC-002**: On mobile viewports (≤ 768px), all pages pass usability checks: no horizontal scrolling, all tap targets ≥ 44px, text is readable without zooming
- **SC-003**: Both light and dark themes render all pages with readable contrast (WCAG AA level — 4.5:1 for normal text, 3:1 for large text)
- **SC-004**: All existing automated tests continue to pass without modification (the upgrade is purely presentational)
- **SC-005**: The custom stylesheet (`static/style.css`) is replaced by DaisyUI classes, reducing the amount of hand-written CSS to under 50 lines for app-specific overrides
- **SC-006**: Page load performance does not regress — total CSS payload (DaisyUI + any overrides) remains under 150KB uncompressed
- **SC-007**: Visual review using the `webapp-testing` skill (Playwright): capture screenshots of all pages at desktop (1280px) and mobile (375px) viewports in both light and dark themes; a UX designer agent reviews the screenshots using the approved prototypes in `docs/daisyui-*.html` as the visual baseline — pixel-perfect matching is not required, but all components, layout structure, and interactive elements from the prototypes and component contracts must be present
