# Feature Specification: Racer Schedule Lookup

**Feature Branch**: `001-racer-schedule-lookup`
**Created**: 2026-03-14
**Status**: Draft
**Input**: User description: "Individual racer predicted race time lookup — allow a racer to enter their name and see personalized predicted start times for events they are competing in."

## Clarifications

### Session 2026-03-14
- Q: Should a returning user's cookie-stored name be auto-applied on page load (events highlighted immediately) or only pre-fill the input? → A: Auto-apply — cookie name is used on page load, events highlighted immediately.
- Q: How should the name input communicate its purpose to first-time visitors? → A: Placeholder text inside the input (e.g., "Your name to highlight your events"). A privacy policy page may be needed in the future given name/cookie storage.
- Q: When should the URL update to include the encoded racer name? → A: On form submission — URL updates when the name is applied to the schedule, not live as the user types.
- Q: Should name matching support partial/substring input or require a full name? → A: Full name match — racer must enter their complete name (case-insensitive).
- Q: Should the system require the tracktiming "LASTNAME Firstname" order, or accept either name order? → A: Accept either order — system normalizes and matches regardless of first/last name order.

### Session 2026-03-18
- Q: How long should the racer name cookie persist? → A: 1-year expiry (supports palmares groundwork and multi-competition returning users).
- Q: When a user with a stored cookie name opens a shared link with a different encoded name, which takes precedence? → A: URL parameter wins and updates the cookie to match.
- Q: Should the success message surface the racer's next upcoming event for at-track glanceability? → A: Yes — append "Your next race:" with event name, heat, and timing to the success message (FR-010a).
- Q: What should the success message say when the racer's nearest event is active (in progress), not upcoming? → A: Use "Racing now:" label for active events, "Your next race:" for upcoming. Only one line shown (active takes priority).
- Q: Should "missing start lists" messages use blue info or amber warning styling? → A: Amber warning — same visual treatment as "no matches" since missing data is a warning condition.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Racer Looks Up Their Personal Schedule (Priority: P1)

A racer attending a track cycling competition wants to know when they will race. They navigate to the schedule page for their competition and enter their name in the name input on the schedule page. The schedule highlights the events they are competing in and shows their predicted individual start times, including which heat they are in.

**Why this priority**: This is the core value proposition — without it, the feature has no purpose. A racer currently has to manually cross-reference start lists with the predicted schedule to figure out their race times.

**Independent Test**: Can be fully tested by entering a racer name on a competition schedule page and verifying that matching events are visually distinguished and show heat-specific timing information.

**Acceptance Scenarios**:

1. **Given** a racer is viewing a competition schedule, **When** they enter their name in the name input on the schedule page, **Then** events where they appear in a start list are visually highlighted in the schedule.
2. **Given** a racer has entered their name, **When** an event they are competing in has multiple heats, **Then** the schedule shows which heat they are in and their predicted start time for that specific heat.
3. **Given** a racer has entered their name, **When** the schedule auto-refreshes via polling, **Then** the racer name is preserved and highlighted events continue to be shown.
4. **Given** a racer enters a name that does not match any start list, **Then** no events are highlighted and a message indicates no matches were found.

---

### User Story 2 - Racer Checks Schedule on Mobile at the Venue (Priority: P2)

A racer at the velodrome checks their phone to see when they race next. The personalized schedule is easy to read on a small screen, with their events clearly standing out from the rest.

**Why this priority**: Most racers will use this feature on their phones at the venue. If the mobile experience is poor, the feature fails its primary real-world use case.

**Independent Test**: Can be tested by viewing a personalized schedule on a mobile viewport and verifying that highlighted events are clearly distinguishable and the name input is accessible.

**Acceptance Scenarios**:

1. **Given** a racer is viewing the schedule on a mobile device, **When** they have entered their name, **Then** their events are clearly distinguishable in the card layout without horizontal scrolling.
2. **Given** a racer is on mobile, **When** they need to enter or change their name, **Then** the name input is easily accessible without navigating away from the schedule.

---

### User Story 3 - Racer Shares Their Personalized Schedule Link (Priority: P3)

A racer wants to bookmark or share a link that shows their personalized schedule so they can return to it quickly or send it to a coach or family member. The racer name is encoded in the URL so it is not visible as plain text.

**Why this priority**: Convenience feature that reduces friction for repeat visits. Lower priority because the name input is lightweight enough to re-enter.

**Independent Test**: Can be tested by constructing a URL with an encoded racer name parameter, navigating to it, and verifying the schedule loads with that racer's events highlighted.

**Acceptance Scenarios**:

1. **Given** a racer has entered their name, **When** they copy the page URL, **Then** the URL contains the racer name in an encoded (non-plaintext) format and can be shared with others.
2. **Given** someone opens a shared personalized schedule link, **When** the page loads, **Then** the schedule shows with the racer's events highlighted without requiring additional input.

---

### User Story 4 - Returning Racer is Recognized (Priority: P3)

A racer who previously entered their name returns to the site for the same or a different competition. Their name is pre-filled from their previous visit, reducing friction. This also lays the groundwork for a future "palmares" feature where racers can see their history of timed competitions.

**Why this priority**: Quality-of-life improvement that supports the goal of the app remembering returning users. Sets the foundation for future personalization features including palmares.

**Independent Test**: Can be tested by entering a name, navigating away, then returning to verify the name is pre-filled and events are automatically highlighted.

**Acceptance Scenarios**:

1. **Given** a racer previously entered their name on the site, **When** they visit a new competition schedule, **Then** their name is pre-filled in the name input field and matching events are automatically highlighted without any additional interaction.
2. **Given** a racer has a remembered name, **When** they want to change it, **Then** they can easily clear and replace the pre-filled name.

---

### Edge Cases

- What happens when a racer's name appears in multiple events across different sessions?
  - All matching events across all sessions should be highlighted.
- What happens when two racers share the same name?
  - Both entries are matched. The system matches on name text, not a unique identifier. Note: for the future palmares feature, same-name racers would produce identical encoded URLs and cookies. A future disambiguator (e.g., bib number or team) may be needed for palmares, but is out of scope for this feature.
- What happens when the racer name is entered with different capitalization, spacing, or name order?
  - Name matching is case-insensitive, tolerant of minor whitespace differences, and accepts either "Firstname Lastname" or "Lastname Firstname" order.
- What happens when start list data is not yet available for an event?
  - Events without start list data cannot be matched; the racer is informed that some start lists are not yet published.
- What happens when a user with a stored cookie opens a shared link with a different name?
  - The URL parameter takes precedence: the schedule highlights the URL-provided name and the cookie is updated to match. This ensures shared links work reliably for all recipients.
- What happens when the racer clears their name?
  - The schedule returns to the default (unhighlighted) view and the stored cookie is cleared.
- What happens when an event the racer is in has already completed?
  - Completed events are still shown as matched but retain their completed visual styling (with racer match as a secondary indicator).
- What happens when an event has only one heat (e.g., a final)?
  - The event is highlighted as a match with a "Racing" badge. No heat-specific time line is shown (the event start time is the heat start time).
- What happens when the racer submits the form with an empty name?
  - Behaves identically to the "Clear" link — deletes cookie, redirects without `?r=`, schedule returns to default unhighlighted view.
- How are racer-matched events communicated to screen reader users?
  - `.racer-match` rows include `aria-label="Your event"`. Message elements (success, no-match, missing start lists) use `role="status"` for live region announcements.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST parse rider names from start list pages for each event, associating each rider with their heat assignment.
- **FR-002**: System MUST provide a name input on the schedule view page in a new `.racer-form-bar` row between the existing meta bar and the schedule container. The form is a flex row containing: a text input with placeholder "Your name to highlight your events" (pre-filled when name is set), a "Highlight" submit button, and a "Clear" text link (visible only when a name is active). On mobile, the form stacks full-width. A `<small>` hint below the input reads "Enter your full name as shown on the start list" (visible always, not only on error). The text input MUST include `aria-label="Racer name"` and `aria-describedby` referencing the hint element, ensuring the form is accessible to screen reader users.
- **FR-003**: System MUST match the entered racer name against parsed start list data using full-name, case-insensitive comparison with name-order normalization (e.g., "Sean Hall" and "Hall Sean" both match "HALL Sean"). Partial or substring matches are not supported.
- **FR-004**: System MUST visually highlight events where the racer is competing using a blue left border (`4px solid #1a73e8`) as the racer identity signal and a blue background tint (`#e8f0fe`) as the default row background. Active events MUST retain their amber background (`#fff3cd`) when racer-matched; completed events MUST retain reduced opacity (applied to cell content, not the row element, so the blue left border retains full contrast) and strikethrough. The blue border is always present on racer-matched rows regardless of event status, consistent across desktop and mobile viewports.
- **FR-005**: System MUST display a prominent inline badge after the event name for racer-matched events, using a solid blue pill style (`background: #1a73e8; color: #fff; font-weight: 700`). Multi-heat events display "Heat N" (the racer's assigned heat number). Single-heat events display "Racing" to indicate the racer is competing. Single-heat events show no heat-specific time line (the event start time is the heat start time).
- **FR-006**: System MUST calculate and display a predicted start time for the racer's specific heat within a multi-heat event. The heat time MUST appear on its own line below the duration value in the Est. Duration column, formatted as "Your heat: HH:MM" in blue text (`color: #1a53a0`). The event-level predicted start time in the Predicted Start column remains unchanged.
- **FR-007**: System MUST preserve the racer name across auto-refresh polling cycles so highlighted events persist.
- **FR-008**: System MUST include the racer name in the page URL in an encoded (non-plaintext) format upon form submission (not live during typing) so personalized schedules can be bookmarked and shared without exposing the name as readable text.
- **FR-009**: System MUST store the racer name in a browser cookie with a 1-year expiry (`Max-Age=31536000`) so returning users have their name pre-filled and automatically applied (events highlighted on page load) on subsequent visits across competitions. When a URL `?r=` parameter is present, it takes precedence over the cookie and the cookie is updated to match. *(Principle VIII tension acknowledged — see plan.md Complexity Tracking for justification.)*
- **FR-010**: System MUST display contextual messaging once above the session list in `_schedule_body.html` based on match state: (a) **Success**: when `racer_name` is set and `match_count > 0`, display "Found N event(s) for [name]" using blue info styling (`background: #dce8fc; color: #1a53a0`). Below the count line, display one status line based on the racer's nearest non-completed event: if the nearest matched event is **active** (in progress), display "Racing now: [event name], Heat N" (omit the time since the event is already underway and the predicted time is in the past); if the nearest matched event is **upcoming**, display "Your next race: [event name], Heat N at HH:MM". Single-heat events omit the "Heat N" portion. If all matched events are completed, omit the status line entirely. Only one status line is shown (active takes priority over upcoming). (b) **Missing start lists**: when `events_without_start_lists > 0`, display "N event(s) do not yet have start lists" using amber warning styling (`background: #fff3cd; color: #856404`) (shown alongside success or no-match messages). (c) **No matches**: when `racer_name` is set and `match_count == 0` and at least one event has a start list, display "No matching events found for [name]" using amber warning styling (`background: #fff3cd; color: #856404`). (d) **No data**: when `racer_name` is set and ALL events lack start lists (`events_without_start_lists == total_events`), display only "Start lists are not yet published — check back closer to the event" using amber warning styling (`background: #fff3cd; color: #856404`) (suppress the "no matches" message since there is nothing to search).
- **FR-011**: System MUST display the personalized schedule effectively on both desktop and mobile viewports.
- **FR-012**: System MUST allow the racer to clear their name via a "Clear" text link (`font-size: 0.82rem; color: #666`) in the form bar, which removes highlighting and clears the stored cookie. The clear link is hidden when no name is active.
- **FR-013**: System MUST remain fully functional and readable at browser zoom levels up to 200%, since users at the velodrome may not have their glasses and rely on browser zoom for readability.
- **FR-014**: System MUST preserve all existing action links (Results, Start List, Audit, Live) on event rows regardless of racer-match status. Racer highlighting MUST NOT remove, hide, or alter the visibility of action links.
- **FR-015**: When a racer name is active, sessions that contain at least one **pending** (non-completed) racer-matched event MUST be auto-opened (via the `open` attribute on `<details>`). Completed sessions where all racer events are finished collapse normally to reduce noise — the racer no longer needs quick access to those events.
- **FR-016**: After form submission, the redirect URL from `/settings/racer-name` MUST include a `#schedule-container` fragment so the browser scrolls past the header and form to the schedule content. This reduces scrolling on long schedules, especially on mobile.

### Key Entities

- **Rider Entry**: A racer's presence in a specific event's start list, including their name, heat assignment, and position within the heat.
- **Personalized Prediction**: A predicted start time for a specific heat within an event, derived from the event's predicted start time and the heat's sequential position.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A racer can enter their name and see their personalized schedule within 5 seconds of the schedule page loading.
- **SC-002**: 100% of events where the racer appears in a published start list are correctly identified and highlighted.
- **SC-003**: Heat-specific predicted start times are accurate to within the same margin as the overall event predictions (no additional error introduced by heat calculation).
- **SC-004**: The personalized schedule is fully usable on mobile devices with screen widths as small as 320px and at browser zoom levels up to 200%.
- **SC-005**: Shared personalized schedule links load correctly for any recipient without requiring them to re-enter the racer name.
- **SC-006**: The racer name input and matching adds no more than 1 second to the schedule load time.
- **SC-007**: A returning racer sees their previously entered name pre-filled and events automatically highlighted on page load, reducing interaction to zero for repeat visits (e.g., day 2 of a multi-day competition).

## UI Design Reference

Visual mockup: [`docs/ui-recommendations-mockup.html`](../../docs/ui-recommendations-mockup.html) — open in a browser to see all proposed UI elements with current vs. proposed side-by-side comparisons. Resize to ≤600px for mobile card layout.

**Design language summary:**

| Signal | Visual | Scope |
|--------|--------|-------|
| Racer identity | Blue left border `4px solid #1a73e8` | Always present on `.racer-match` rows |
| Default racer bg | Blue tint `#e8f0fe` | `.racer-match` rows (unless overridden by active) |
| Active status | Amber bg `#fff3cd` | Preserved on `.racer-match.active` rows |
| Completed status | Opacity `0.45` on cell content + line-through | Preserved on `.racer-match.status-completed` rows (border retains full contrast) |
| Heat badge | Solid blue pill `bg: #1a73e8; color: #fff`; "Heat N" (multi-heat) or "Racing" (single-heat) | Inline after event name |
| Heat time | "Your heat: HH:MM" in `color: #1a53a0` | Own line below duration in Est. Duration column |
| Success message | Blue info `bg: #dce8fc` | Once above session list (when matches > 0) |
| Next race line | "Your next race: [event], Heat N at HH:MM" in success message | Shown when nearest matched event is upcoming |
| Racing now line | "Racing now: [event], Heat N" in success message (time omitted — event already underway) | Shown when nearest matched event is active (takes priority over "Your next race") |
| No-match message | Amber warning `bg: #fff3cd` | Once above session list (only when start lists exist to search) |
| No-data message | Amber warning `bg: #fff3cd` | Once above session list (when ALL start lists missing) |
| Missing start lists | Amber warning `bg: #fff3cd` | Once above session list |
| Action buttons | `margin: 0 4px 4px 0` | Vertical spacing between stacking buttons |

## Assumptions

- Racer names in start lists are consistent enough for text matching (the tracktiming.live system uses a single name format per competition).
- Start list pages are already being fetched by the system; this feature extends parsing rather than requiring additional network requests.
- Heat durations within a multi-heat event are approximately equal, making sequential heat start time prediction reasonable.
- The "LASTNAME Firstname" format used by tracktiming.live is consistent across competitions.
- A browser cookie is an acceptable persistence mechanism for the racer name, following the existing pattern used for the learned-durations toggle.
- The racer name encoding in URLs (Base64) addresses cosmetic/optics concerns but is not a security measure — it prevents casual readability, not determined decoding.
- Same-name racers are an accepted limitation for this feature. The future palmares feature may require an additional disambiguator (e.g., bib number or team) to distinguish racers with identical names.
- A privacy policy page is not in scope for this feature but may be needed in the future given that the site stores racer names in cookies and encodes them in shareable URLs.
- When a returning user's cookie auto-applies on page load (without form submission), the URL does not contain the `?r=` parameter. Copying the URL in this state produces a non-personalized link. This is by design — the URL only updates on explicit form submission (FR-008). To share a personalized link, the racer must submit the form at least once.
- The `racer_name` cookie stores the name as plain text (not Base64 encoded) because it is httponly (not accessible to JavaScript) and only transmitted over HTTPS in production (secure flag). The URL Base64 encoding is a cosmetic measure for browser history and shared links, not a security boundary.
- The `/settings/racer-name` route accepts the racer name as a plaintext GET query parameter (`?name=...`), meaning the name appears in server access logs and CloudFront logs before the redirect encodes it. This is unavoidable under the GET-only constraint (CloudFront OAC) and is an accepted trade-off since racer names are public data on tracktiming.live.
