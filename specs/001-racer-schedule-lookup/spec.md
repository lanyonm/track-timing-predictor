# Feature Specification: Racer Schedule Lookup

**Feature Branch**: `001-racer-schedule-lookup`
**Created**: 2026-03-14
**Status**: Draft
**Input**: User description: "Individual racer predicted race time lookup — allow a racer to enter their name and see personalized predicted start times for events they are competing in."

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

**Independent Test**: Can be tested by entering a name, navigating away, then returning to verify the name is pre-filled.

**Acceptance Scenarios**:

1. **Given** a racer previously entered their name on the site, **When** they visit a new competition schedule, **Then** their name is pre-filled in the name input field.
2. **Given** a racer has a remembered name, **When** they want to change it, **Then** they can easily clear and replace the pre-filled name.

---

### Edge Cases

- What happens when a racer's name appears in multiple events across different sessions?
  - All matching events across all sessions should be highlighted.
- What happens when two racers share the same name?
  - Both entries are matched. The system matches on name text, not a unique identifier. Note: for the future palmares feature, same-name racers would produce identical encoded URLs and cookies. A future disambiguator (e.g., bib number or team) may be needed for palmares, but is out of scope for this feature.
- What happens when the racer name is entered with different capitalization or spacing?
  - Name matching should be case-insensitive and tolerant of minor whitespace differences.
- What happens when start list data is not yet available for an event?
  - Events without start list data cannot be matched; the racer is informed that some start lists are not yet published.
- What happens when the racer clears their name?
  - The schedule returns to the default (unhighlighted) view and the stored cookie is cleared.
- What happens when an event the racer is in has already completed?
  - Completed events are still shown as matched but retain their completed visual styling (with racer match as a secondary indicator).
- What happens when an event has only one heat (e.g., a final)?
  - The event is highlighted as a match but no heat detail is shown (heat info is only relevant for multi-heat events).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST parse rider names from start list pages for each event, associating each rider with their heat assignment.
- **FR-002**: System MUST provide a name input on the schedule view page where the racer can enter their name.
- **FR-003**: System MUST match the entered racer name against parsed start list data using case-insensitive comparison.
- **FR-004**: System MUST visually highlight events where the racer is competing within the full schedule view, so the racer retains context of surrounding events.
- **FR-005**: System MUST display the racer's specific heat number for multi-heat events where the racer is competing.
- **FR-006**: System MUST calculate and display a predicted start time for the racer's specific heat within a multi-heat event.
- **FR-007**: System MUST preserve the racer name across auto-refresh polling cycles so highlighted events persist.
- **FR-008**: System MUST include the racer name in the page URL in an encoded (non-plaintext) format so personalized schedules can be bookmarked and shared without exposing the name as readable text.
- **FR-009**: System MUST store the racer name in a browser cookie so returning users have their name pre-filled on subsequent visits across competitions.
- **FR-010**: System MUST indicate when start list data is unavailable for some events, so the racer understands their schedule may be incomplete.
- **FR-011**: System MUST display the personalized schedule effectively on both desktop and mobile viewports.
- **FR-012**: System MUST allow the racer to clear their name, which removes highlighting and clears the stored cookie.
- **FR-013**: System MUST remain fully functional and readable at browser zoom levels up to 200%, since users at the velodrome may not have their glasses and rely on browser zoom for readability.

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
- **SC-007**: A returning racer sees their previously entered name pre-filled, reducing input time to zero for repeat visits.

## Assumptions

- Racer names in start lists are consistent enough for text matching (the tracktiming.live system uses a single name format per competition).
- Start list pages are already being fetched by the system; this feature extends parsing rather than requiring additional network requests.
- Heat durations within a multi-heat event are approximately equal, making sequential heat start time prediction reasonable.
- The "LASTNAME Firstname" format used by tracktiming.live is consistent across competitions.
- A browser cookie is an acceptable persistence mechanism for the racer name, following the existing pattern used for the learned-durations toggle.
- The racer name encoding in URLs (Base64) addresses cosmetic/optics concerns but is not a security measure — it prevents casual readability, not determined decoding.
- Same-name racers are an accepted limitation for this feature. The future palmares feature may require an additional disambiguator (e.g., bib number or team) to distinguish racers with identical names.
