# Feature Specification: Racer Palmares (Achievements)

**Feature Branch**: `004-racer-palmares`
**Created**: 2026-03-22
**Status**: Draft
**Input**: User description: "Create a palmares (achievements) feature that automatically saves timed event audit result links for identified racers, provides a profile page showing all competitions with audit links, supports sharing via r= param, and enables CSV export of individual audit result data."

## Clarifications

### Session 2026-03-22

- Q: How should two racers with the same name sharing a palmares be handled? → A: Accept collision as-is — same normalized name = shared palmares pool with no mitigation. Consistent with how rider matching already works in the schedule view and with the upstream tracktiming.live data model where names are the only identifier.
- Q: Should bulk CSV export (multiple events in one file) be included? → A: No. Keep per-event CSV export only, matching the original description. Bulk export removed from scope.
- Q: What should an unidentified visitor see on the palmares page? → A: Show a name input form directly on the palmares page. No competition required — entering a name sets the racer identity and immediately loads their palmares.
- Q: Should the palmares page show inline result metadata (time, placement) or just event names with audit links? → A: Event name + audit link only. Lightweight link directory; result details live on the external audit page.
- Q: Should the schedule view provide visual feedback when palmares entries are captured? → A: Yes. A subtle inline message appended to the existing "events found" racer info area (e.g., "3 of your timed events are in your palmares") with a link to the palmares page.
- Q: Should the CSV export endpoint have rate limiting to prevent abuse as a proxy to tracktiming.live? → A: No throttling. Rely on Lambda concurrency limits and external service resilience.
- Q: How should the shareable palmares URL be presented? → A: "Copy Link" button with visual confirmation ("Copied!"), accompanied by a short description explaining that the racer's event information will travel with the link.
- Q: Should the palmares page cross-link back to competition schedules in the app? → A: Yes. The competition name in each palmares group links to the app's schedule view for that competition (with the racer identifier preserved).
- Q: What should the palmares save message show when a returning racer re-views a competition, including during live refreshes? → A: Always show the total count of this competition's events in the palmares (e.g., "N of your timed events are in your palmares") with a link. The count increments naturally during live refreshes as new audit results become available. The wording must clarify these are timed events with audit results, since the count will not match the racer's total event count if they also participate in non-timed events.
- Q: How should the per-event CSV export be presented on the palmares page? → A: Small download icon (e.g., arrow-down) next to each event's audit link — compact and mobile-friendly.
- Q: What loading and error states should the CSV export show? → A: Download icon replaced with a small spinner while loading. On error, replaced with a warning icon and a tooltip/inline message explaining the failure.
- Q: What visual container should competition groups use on the palmares page? → A: Static card-based layout — one card per competition with no expand/collapse interactivity. Each card is a self-contained achievement block showing competition name, date, and event list. No interactive elements like the schedule page's collapsible sessions.
- Q: Should the palmares proactively check whether external audit links are still available? → A: No. Audit links always render normally. Broken links are the external site's responsibility. The CSV export error state (warning icon) is sufficient for the rare case.
- Q: Can racers remove entries from their palmares? → A: Yes. Per-competition removal via a small "remove" action on each competition card, available only when accessing via cookie (not shared links). A confirmation prompt is shown before deletion. Removes all events for that competition from the racer's palmares.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automatic Palmares Collection (Priority: P1)

A racer who has identified themselves (via the racer name highlight feature) views a competition schedule. As the page loads and events are matched to the racer, the system automatically saves references to all timed events in which the racer competed that have audit result links. The racer does not need to take any extra action — simply viewing a competition schedule while identified is enough to build their palmares over time.

A first-time visitor enters their name, views a competition, and their matched timed events begin accumulating immediately. A returning visitor (with name already in cookie) has new competitions added each time they check a schedule.

**Why this priority**: This is the foundational data-collection mechanism. Without it, there is nothing to display on the profile page or export. Every other story depends on palmares entries existing.

**Independent Test**: Can be tested by identifying as a racer, viewing a competition schedule with completed timed events, and verifying that the matched events with audit URLs are persisted. Delivers value because the data is being captured for future use.

**Acceptance Scenarios**:

1. **Given** a racer has set their name and views a competition schedule, **When** the schedule contains timed events where the racer appears on a start list and audit result links exist, **Then** those events are saved to the racer's palmares with competition context (competition name, date, event name, audit link).
2. **Given** a racer has previously viewed a competition, **When** they view the same competition again and new events have audit results, **Then** only the new events are added (no duplicates).
3. **Given** a racer views a competition schedule, **When** no events match the racer or no audit URLs exist yet, **Then** no palmares entries are created and no error is shown.
4. **Given** a racer views a schedule containing special events (Break, Warm-up, Medal Ceremonies, End of Session), **When** the system checks for palmares entries, **Then** special events are excluded from the palmares regardless of whether they have URLs.
5. **Given** a racer views a competition schedule and palmares entries exist or are newly saved, **When** the schedule page renders the racer info area, **Then** the message shows the total count of this competition's timed events in the racer's palmares with a link to the palmares page (e.g., "3 of your timed events are in your palmares"). The count increments naturally during live HTMX refreshes as new audit results become available.

---

### User Story 2 - Palmares Profile Page (Priority: P2)

A racer navigates to their palmares profile page to see a chronological record of all competitions in which they competed. The page groups events by competition (newest first), showing the competition name, date, and a list of timed events with direct links to the audit result pages on tracktiming.live.

A first-time visitor who hasn't yet viewed any competitions sees an empty state with a clear call-to-action to view a competition and set their name. A returning visitor with accumulated palmares sees their full history organized clearly.

**Why this priority**: This is the primary user-facing value of the feature — the racer's personal achievement record. It transforms scattered schedule views into a cohesive record.

**Independent Test**: Can be tested by navigating to the palmares page after having viewed at least one competition while identified. The page should show competition groupings with audit links. Delivers value as a standalone achievement record.

**Acceptance Scenarios**:

1. **Given** a racer has palmares entries from multiple competitions, **When** they visit the palmares page, **Then** they see competitions listed in reverse chronological order, each with its constituent timed events and clickable audit result links.
2. **Given** a racer is identified but has no palmares entries, **When** they visit the palmares page, **Then** they see a friendly empty state explaining that viewing competitions will populate their achievements.
3. **Given** a racer is not identified (no name set, no `r=` parameter), **When** they visit the palmares page, **Then** they see a name input form directly on the page. After entering their name, the racer identity is set and their palmares loads immediately without navigating away.
4. **Given** a racer has palmares entries, **When** they view the profile page on a mobile device, **Then** the layout is responsive and all audit links are accessible.
5. **Given** a racer is viewing their palmares via cookie (not a shared link), **When** they click the "remove" action on a competition card, **Then** a confirmation prompt is shown. If confirmed, all palmares entries for that competition are deleted and the card is removed from the page. If cancelled, no changes are made.
6. **Given** a recipient is viewing a shared palmares link (no cookie set), **When** they view the competition cards, **Then** no "remove" action is visible.

---

### User Story 3 - Shareable Palmares Link (Priority: P3)

A racer wants to share their achievement record with a coach, teammate, or friend. The palmares page provides a shareable URL that includes the racer identifier (`r=` parameter). When the recipient opens this link on their device, they see the racer's palmares profile page with all collected competitions and audit links — no prior app usage required.

**Why this priority**: Sharing is what gives the palmares social value. A private-only record is useful, but a shareable one enables racers to showcase achievements to coaches, parents, and teammates.

**Independent Test**: Can be tested by copying the shareable palmares link and opening it in an incognito/different browser. The recipient should see the racer's full palmares. Delivers value as a portable achievement record.

**Acceptance Scenarios**:

1. **Given** a racer is on their palmares page, **When** they look for a way to share, **Then** a "Copy Link" button is displayed alongside a short description explaining that the racer's event information will travel with the link. Clicking the button copies the shareable URL (including the `r=` parameter) to the clipboard and shows visual confirmation (e.g., "Copied!").
2. **Given** a recipient opens a shared palmares link on a device that has never used the app, **When** the page loads, **Then** the recipient sees the full palmares for the identified racer including all competition groupings and audit links.
3. **Given** a recipient opens a shared palmares link, **When** they view the page, **Then** they see the page in a read-only/visitor context (the racer name cookie is NOT set on the recipient's device from a shared link alone).
4. **Given** a racer shares their palmares link, **When** the racer later views more competitions and accumulates new entries, **Then** anyone re-opening the shared link sees the updated palmares.

---

### User Story 4 - CSV Export of Individual Audit Results (Priority: P4)

A racer viewing their palmares or a specific competition's events wants to export their individual audit result data as a CSV file. The export fetches the audit result page from tracktiming.live, extracts the tabular data, filters it to only the identified racer's rows, and returns a downloadable CSV file. The CSV columns match the table structure displayed on the tracktiming.live audit result page.

**Why this priority**: Export is a power-user feature that builds on the existing palmares collection. It has high value for racers tracking personal records or submitting results to coaches/federations, but requires the other stories to be useful.

**Independent Test**: Can be tested by triggering a CSV export for a specific event from the palmares page and verifying the downloaded file contains only the racer's data in the correct tabular format. Delivers value as a portable data extract.

**Acceptance Scenarios**:

1. **Given** a racer is viewing their palmares with events that have audit links, **When** they request a CSV export for a specific event, **Then** a CSV file is downloaded containing only the racer's rows from that event's audit result table, with columns matching the audit page table format.
2. **Given** a racer requests a CSV export, **When** the audit result page on tracktiming.live is unavailable or returns an error, **Then** the system displays a user-friendly error message indicating the external data source is temporarily unavailable.
3. **Given** a racer requests a CSV export for an event, **When** the racer's name does not appear in the audit result data, **Then** the system returns an empty CSV with headers only and a message indicating no matching data was found.
4. **Given** a racer clicks the download icon for a CSV export, **When** the export is in progress, **Then** the download icon is replaced with a spinner. On completion, the spinner reverts to the download icon and the file downloads. On failure, the spinner is replaced with a warning icon and a tooltip/inline message explains the error.

---

### Edge Cases

- **Audit page no longer available**: tracktiming.live may remove or archive old competition data. The palmares continues to display the saved event metadata and audit link normally — no proactive link health checking. If the racer clicks an audit link that has been removed, tracktiming.live handles the 404. If they attempt a CSV export for that event, the download icon shows a warning icon with an error explanation (per FR-012).
- **Racer name changes**: If a racer changes their name (e.g., corrects a typo or changes due to life event), old palmares entries stored under the previous name are not automatically transferred. The profile page shows entries matching the current identifier only.
- **Name collision (accepted)**: Two different racers sharing the same name will contribute to and see the same palmares pool. This is an explicit design decision: the app has no authentication, and the upstream tracktiming.live data model identifies racers by name only. This mirrors how rider matching already works in the schedule view. No disambiguation mechanism is provided, but racers can remove misattributed competition cards via the per-competition remove action (FR-015). Note: another person with the same name could also remove entries, which is an accepted consequence of name-based identity without authentication.
- **Concurrent device usage**: A racer viewing competitions on multiple devices (phone at the track, laptop at home) should see the same palmares on any device when using the same racer name, since data is stored server-side.
- **Competition viewed before racer identified**: If a racer views a competition schedule before entering their name, no palmares entries are created for that visit. The racer must re-view the competition after identification for entries to be saved.
- **Events without start lists**: If a racer is identified but an event does not yet have a start list (so no rider matching is possible), the event cannot be added to the palmares. It may be added on a subsequent schedule view once start lists are published.
- **Very large palmares (deferred)**: A racer who has competed in many competitions over multiple seasons may accumulate hundreds of entries. Pagination or lazy-loading by season/year is deferred to a future iteration. The current implementation targets up to 50 events across 10 competitions (per SC-002). Beyond this scale, page performance may degrade but remains functional.
- **Existing schedule link sharing unchanged**: The existing behavior of `/schedule/{id}?r=...` (showing the schedule with racer highlighting) must remain unchanged. Palmares sharing uses a distinct URL path.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST automatically save palmares entries (competition context + audit result link) when an identified racer views a competition schedule containing timed events where the racer appears on a start list and audit result links are available.
- **FR-002**: System MUST NOT create duplicate palmares entries when the same competition is viewed multiple times by the same racer.
- **FR-003**: System MUST exclude special events (Break, Warm-up, Medal Ceremonies, End of Session) from palmares collection.
- **FR-004**: System MUST provide a palmares profile page accessible via a dedicated URL that accepts the racer identifier through the same mechanism used by schedule views (URL parameter with cookie fallback).
- **FR-005**: System MUST display palmares entries grouped by competition in reverse chronological order using a static card-based layout (one card per competition, no expand/collapse interactivity), showing competition name (linked to the app's schedule view for that competition with racer identifier preserved), date, and a list of timed events with audit result links and download icons.
- **FR-006**: System MUST provide a "Copy Link" button on the palmares page that copies the shareable URL (including racer identifier) to the clipboard with visual confirmation, accompanied by a short description explaining that the racer's event information will travel with the link.
- **FR-007**: System MUST NOT set the racer name cookie on a recipient's device when they open a shared palmares link — viewing a shared link is read-only for the recipient.
- **FR-008**: System MUST provide CSV export of individual audit result data for specific events via a small download icon next to each event's audit link on the palmares page. While the export is in progress, the download icon is replaced with a spinner. The export fetches the audit result page from the external data source, filters to only the identified racer's rows, and matches the table column structure of the source page.
- **FR-009**: System MUST display a clear empty state on the palmares page when the racer has no entries, with guidance on how to populate it (view a competition while identified).
- **FR-010**: System MUST display a name input form when an unidentified visitor accesses the palmares page without a racer identifier. Submitting the form sets the racer identity and loads their palmares on the same page without requiring the user to manually navigate elsewhere.
- **FR-011**: System MUST store palmares data server-side to enable cross-device access and link sharing.
- **FR-012**: System MUST handle unavailable external audit pages gracefully during CSV export by replacing the download icon with a warning icon and displaying a tooltip or inline message explaining the failure. No system errors or unhandled exceptions should be shown to the user.
- **FR-013**: System MUST provide navigation to the palmares page from the schedule view when a racer is identified, and from the site's main navigation.
- **FR-014**: System MUST display the total count of this competition's timed events in the racer's palmares within the existing racer info area on the schedule page, with a link to the palmares page (e.g., "N of your timed events are in your palmares"). The count updates on each HTMX refresh as new audit results become available. The wording must distinguish timed events with audit results from the racer's total event count.
- **FR-015**: System MUST provide a per-competition "remove" action on each competition card on the palmares page, visible only to users accessing via racer name cookie (not via shared `r=` link). Clicking the action shows a confirmation prompt before deleting all palmares entries for that competition. The action is hidden for shared link viewers.

### Key Entities

- **Palmares Entry**: A record linking a racer to a specific timed event's audit result. Contains: racer identifier (normalized name), competition identifier, competition name, session name, event name, event position within the schedule, audit result URL (relative path), and the date the competition was held.
- **Palmares Collection**: The complete set of palmares entries for a given racer, organized by competition. Represents the racer's achievement history across all competitions they have viewed while identified.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An identified racer who views a competition schedule has all matching timed events with audit links saved to their palmares within the same page load — no separate action required.
- **SC-002**: The palmares profile page loads and displays all of a racer's collected competitions within 3 seconds for a typical palmares of up to 50 events across 10 competitions.
- **SC-003**: A shared palmares link opened on a new device (no cookies, no prior visits) displays the complete palmares within 3 seconds.
- **SC-004**: CSV export for a single event completes and initiates download within 5 seconds.
- **SC-005**: 100% of timed events where the racer appears on a start list and an audit link exists are captured — no matching events are silently dropped.
- **SC-006**: The palmares page is fully functional on mobile viewports (360px width and above) with all links and export actions accessible.
- **SC-007**: Returning racers see previously collected palmares entries from prior sessions without re-viewing those competitions.

## Assumptions

- **Server-side storage**: Palmares data is stored server-side (extending the existing database layer) to enable cross-device access and link sharing. This follows the existing dual-backend pattern (DynamoDB in production, SQLite for local development).
- **Racer identity = normalized name**: The racer identifier for palmares is the same normalized name used for rider matching in the schedule view. No separate account or login system is introduced.
- **Public data only**: All data stored in the palmares (racer name, competition/event names, audit URLs) is publicly available on tracktiming.live. No private or sensitive information is collected beyond what is already visible on the upstream source.
- **Audit page format is tabular**: The tracktiming.live audit result pages (`-AUDIT-R.htm`) contain HTML tables with structured result data that can be parsed and filtered by racer name.
- **Per-event CSV only**: CSV export is per-event (one audit result table at a time). Bulk export across multiple events is out of scope.
- **No authentication**: Consistent with the rest of the application, the palmares feature does not require login or authentication. Anyone with a racer's name (or a shared link) can view that racer's palmares. This is acceptable because all underlying data is publicly accessible on tracktiming.live.
- **GET-only routes**: All new palmares routes use GET methods, consistent with the existing CloudFront OAC + Lambda Function URL constraint documented in the architecture.
