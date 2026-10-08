# Feature Specification: Rider List Fallback Matching

**Feature Branch**: `006-rider-list-matching`
**Created**: 2026-10-06
**Status**: Draft
**Input**: User description: "Rider List fallback matching for racer events. Some competitions (e.g. EventId 26037) require rider sign-on before each session, so start lists are not published until just before each session. These competitions publish a Rider List document listing every rider's category and entered events. Use it to highlight a racer's events when no start list exists yet. Deliberately narrow: only build for what live data from 26037 covers."

## Clarifications

### Session 2026-10-06 (pre-spec design discussion)

- Q: How should rounds after the first be handled, given a rider entered in a discipline only rides later rounds if they advance? → A: Mark every round, but label later rounds "if advancing" so the racer sees their full possible day while the uncertainty is explicit.
- Q: What happens when a rider's category or an event name doesn't fit the masters age-band format seen in 26037? → A: No fallback match. Scope is limited to formats seen in captured live data; other formats will be added when fixtures exist.
- Q: Does the Rider List change during a competition? → A: Treat it as immutable. Start lists are absent because sign-on happens before each session, not because entries change. A changed list would be published under a new file name.
- Q: Is matching costly when every future event lacks a start list? → A: No. Matching is a per-event comparison against one rider's entry; the only real cost is downloading the Rider List once per competition per running app instance.
- Q: Can a Rider List match show which heat the racer is in? → A: No. The Rider List has no heat information and never will.

### Session 2026-10-06 (/speckit.clarify)

- Q: Should a start list that downloads but yields 0 riders supersede the Rider List? → A: No. A start list with 0 parsed riders is treated as absent: the Rider List fallback applies and the event counts toward "events without start lists".
- Q: Should sprint "Ride 3" (only ridden if tied 1–1) get its own label? → A: No. All later-round rides, including Ride 3, use "If advancing".

### Session 2026-10-08 (post-implementation review)

- Q: A rider rides only one of several numbered qualifiers (Scratch Race Qualifier 1 and 2). How should that show? → A: Both stay "Entered", but the next-race time isn't definite: it's the time to be ready by, shown as "Your next race: … Qualifier 1 (or a later qualifier), be ready by HH:MM".
- Q: Are an empty start list and no start list different? → A: No, they are equivalent.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See my events before start lists are posted (Priority: P1)

A masters racer at a competition that requires per-session sign-on enters their name on the schedule view days before racing. No start lists exist for upcoming sessions, but the competition has published a Rider List showing the racer's category (e.g. M6064) and the disciplines they entered (e.g. Sprint, Team Sprint, Time Trial). The schedule highlights the events those entries correspond to — such as "55-64 Men Team Sprint Qualifying" and "60-64 Men Sprint Qualifying" — with an "Entered" badge, so the racer can plan their days.

**Why this priority**: Without this, the racer-name feature is useless at these competitions for nearly their entire duration: every event shows "do not yet have start lists" and nothing is highlighted.

**Independent Test**: Load the 26037 schedule with a racer name present in its Rider List and no start lists for future sessions; verify the racer's first-round events are highlighted with an "Entered" badge and other age bands/genders/disciplines are not.

**Acceptance Scenarios**:

1. **Given** a competition with a Rider List and a racer named ABERS Brian (M6064, entered S, TS, TT), **When** the racer views the schedule and "60-64 Men Sprint Qualifying" has no start list, **Then** that event shows an "Entered" badge and the racer row tint.
2. **Given** the same racer, **When** the schedule contains "65-74 Men Team Sprint Qualifying" or "55-59 Men Sprint Qualifying", **Then** those events are not highlighted (age band does not contain M6064).
3. **Given** the same racer, **When** the schedule contains "60-64 Men Pursuit Qualifying", **Then** it is not highlighted (racer is not entered in IP).
4. **Given** an event that is the only one for its age band, gender and discipline in the competition (e.g. "65-69 Men 500m Time Trial Final"), **When** a matching racer views the schedule, **Then** it shows "Entered".
5. **Given** an event that has a start list with at least one rider, **When** the racer views the schedule, **Then** matching uses only the start list exactly as today, whether or not the racer appears on it.
6. **Given** an event whose start list yields 0 riders, **When** the racer views the schedule, **Then** it is matched from the Rider List and counts as an event without a start list.
7. **Given** a special event (Break, Medal Ceremonies, End of Session), **When** the racer views the schedule, **Then** it is never matched from the Rider List.

---

### User Story 2 - See later rounds I might ride (Priority: P2)

A sprinter entered in Sprint sees their Qualifying ride as "Entered" and the 1/8, 1/4, 1/2 and Final rides for their age band labelled "If advancing", so they know the latest point in the day they could still be racing.

**Why this priority**: Builds on P1; helpful for planning, but the certain events matter most.

**Independent Test**: Load 26037 with a sprinter's name; verify Sprint 1/4 Final, 1/2 Final and Final rides for their band show "If advancing" without the row tint, and Qualifying shows "Entered".

**Acceptance Scenarios**:

1. **Given** a racer entered in S in band 35-39 Men, **When** they view the schedule, **Then** "35-39 Men Sprint Qualifying" shows "Entered" and "35-39 Men Sprint 1/4 Final Ride 1" shows "If advancing".
2. **Given** a racer entered in SCR in band 55-59 Men, **When** the competition has "55-59 Men Scratch Race Qualifier 1/2" and "55-59 Men Scratch Race Final", **Then** the qualifiers show "Entered" and the final shows "If advancing".
3. **Given** a racer entered in IP, **When** the competition has both a Qualifying and a Final for their band, **Then** the Final shows "If advancing".
4. **Given** an "If advancing" event, **When** it is rendered, **Then** it has no row tint and its accessible label is "Your event, if advancing".

---

### User Story 3 - Clear status messages (Priority: P3)

The banners above the schedule tell the racer that matches came from the Rider List, which category and entries were used (so a wrong-person match is obvious), and how many matches are tentative. "Your next race" names the next pending match and says "(if advancing)" when that match is tentative.

**Why this priority**: The current warnings ("N events do not yet have start lists", "Start lists are not yet published") would be misleading once Rider List matches are shown.

**Independent Test**: With Rider List matches present, verify the info line replaces both start-list warnings, the found-count includes the tentative count, and next race shows the qualifier.

**Acceptance Scenarios**:

1. **Given** Rider List matches exist, **When** the schedule renders, **Then** an info line reads "Events without start lists matched from the Rider List (M6064: S, TS, TT). Start lists replace these once posted." and neither start-list warning is shown.
2. **Given** 12 matches of which 7 are tentative, **When** the schedule renders, **Then** the count line reads "Found 12 events for "<name>" (7 if advancing)".
3. **Given** the next pending match is tentative, **When** the schedule renders, **Then** next race reads e.g. "Your next race: 35-39 Men Sprint 1/4 Final Ride 1 (if advancing) at 14:20", with no heat.
4. **Given** a Rider List exists but the racer isn't in it, or their category isn't a masters age band, **When** the schedule renders, **Then** the existing start-list warnings are shown unchanged.

---

### Edge Cases

- Competition has no Event Documents block or no "Rider List" entry: behaviour is exactly as today.
- Rider List download or parse fails: no Rider List matches, no error shown to the user; existing warnings appear.
- Rider category is not `[MW]` followed by four digits (lo-hi) or two digits (lo+): no Rider List matches for that racer.
- Event name has no age band (`NN-NN` or `NN+`) or no Men/Women: never matched from the Rider List.
- Rider band overlaps more than one event band (W6569 entered in TS with both "55+ Women Team Sprint" and "65+ Women Team Sprint" scheduled): both match.
- Unknown event code in the Rider List: ignored.
- Start list downloads but yields 0 riders (empty template or unrecognised layout): treated as no start list; Rider List matching applies.
- Start list appears for an event mid-competition: that event switches to start-list matching on the next load/refresh (including no match if the racer isn't on it).
- Completed events without start-list riders are matched by the same rules as upcoming ones.
- Two riders with the same normalized name: same behaviour as start lists today (first match wins).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST locate the Rider List document from the competition's Event Documents block when present.
- **FR-002**: System MUST read each rider's name, category and entered event codes from the Rider List.
- **FR-003**: System MUST obtain the Rider List only when a racer name is set and at least one non-special event lacks start-list riders, and MUST fetch it concurrently with the other upstream requests for the same view.
- **FR-004**: System MUST retain a parsed Rider List for the life of the running app instance, keyed by its document address, and MUST NOT re-download it while retained.
- **FR-005**: System MUST treat any failure to obtain or read the Rider List as "no Rider List" without surfacing an error.
- **FR-006**: System MUST identify the racer in the Rider List using the same name matching as start lists.
- **FR-007**: System MUST derive the racer's age band and gender only from categories of the form `M`/`W` + four digits (band lo–hi) or `M`/`W` + two digits (band lo and over).
- **FR-008**: System MUST match a non-special event from the Rider List only when the event has no start-list riders (no start list, a failed download, or a start list yielding 0 riders), its name contains an age band (`NN-NN` or `NN+`) that contains the racer's band, its gender matches the racer's, and its discipline corresponds to one of the racer's entered codes.
- **FR-009**: Code-to-discipline correspondence MUST be: S → sprint; TT → time trial (kilo, 750m, 500m); IP → individual pursuit; TP → team pursuit; TS → team sprint; SCR → scratch race; PTS → points race. Other codes MUST be ignored.
- **FR-010**: A Rider List match MUST be "entered" when the event is a Qualifying round, a numbered Qualifier, or the only event the rider matches for that entered event type (Rider List code, so sprint qualifying and sprint match rounds share one group, and overlapping bands such as 55+ and 65+ share one group); otherwise it MUST be "if advancing". This includes sprint "Ride 3", which gets no separate label.
- **FR-011**: Rider List matches MUST NOT carry heat information or a per-heat start time.
- **FR-012**: Rider List matches MUST count toward the found-events count, the next race selection and session auto-open, and MUST NOT create palmares entries.
- **FR-013**: The schedule MUST show an "Entered" badge with the racer row tint for entered matches, and an "If advancing" badge (visually weaker) with no row tint for tentative matches. Start-list match badges are unchanged.
- **FR-014**: When any Rider List match exists, the schedule MUST replace the start-list warnings with an info line naming the category and entered codes used.
- **FR-015**: The found-events line MUST append "(M if advancing)" when M tentative matches exist; the next race line MUST append "(if advancing)" when the next match is tentative.
- **FR-016**: When the rider matches more than one numbered qualifier for an entered code, those matches MUST be flagged as parallel qualifiers. When the next match is one, the next race line MUST read "Your next race: {event} (or a later qualifier), be ready by HH:MM" ("Racing now: {event} (or a later qualifier)" when active), since the rider rides only one of them.

### Key Entities

- **Rider List entry**: one rider in a competition's Rider List — name, category (e.g. M6064), set of entered event codes.
- **Age band**: an inclusive age range with optional open upper bound, plus gender; derived from a rider category or an event name.
- **Rider match**: the existing per-event racer match, extended with its source (start list or Rider List) and whether it is tentative ("if advancing"); heat is absent for Rider List matches.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For EventId 26037, a racer in the Rider List with a masters category sees every first-round event in their band/gender/disciplines highlighted, with zero highlighted events outside their band, gender or entered disciplines (verified against the captured fixture).
- **SC-002**: Repeat views and live refreshes of the same competition do not re-download the Rider List while the app instance is running.
- **SC-003**: Schedule views for competitions without a Rider List, or whose racer's category is not a masters age band, render identically to today; existing test assertions keep passing (test mocks may be adjusted to route the new Rider List fetch).
- **SC-004**: A Rider List outage never produces an error page; the schedule renders with the existing start-list warnings.

## Assumptions

- Scope is limited to formats observed in EventId 26037 (masters age-band categories, the seven event codes listed, English event names with "Men"/"Women"). Other competitions' formats are out of scope until captured as fixtures.
- The Rider List file does not change during a competition; a revised list would use a new file name.
- The Rider List's inline flag images are irrelevant and may be discarded before reading.
- A "TT" entry covers whichever time-trial distance the racer's band rides (kilo, 750m or 500m); the band and gender select the specific event.
- Tests use captured 26037 fixtures: the initial schedule layout and the Rider List with flag images removed.
- Documentation (CLAUDE.md, README.md) is updated in the same commit as the code, per project rules.
