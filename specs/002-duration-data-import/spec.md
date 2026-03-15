# Feature Specification: Duration Data Import Scripts

**Feature Branch**: `002-duration-data-import`
**Created**: 2026-03-14
**Status**: Draft
**Input**: User description: "As a companion to the track-timing-predictor app, create one or more scripts that can parse data sources and turn them into a standard data structure used to strengthen the app's default predictions and widen its understanding of different events and categories."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Import Historical Results from tracktiming.live (Priority: P1)

A developer or operator wants to backfill the app's duration database by importing historical competition results from tracktiming.live. They run a script with a single competition ID, and the script fetches schedule and result data, extracts actual event durations and heat counts, and writes them into a standardized intermediate format. Unlike the app's current flat discipline-key approach, the import process decomposes each event name into structured category dimensions — discipline, gender, age group, round, and other modifiers — so that duration observations can be analyzed at a granular level (e.g., learning that junior pursuit events are shorter than elite ones). This data can then be loaded into the app's learning database to improve both total event duration defaults and per-heat duration estimates immediately rather than waiting for the app to observe live events over time. As a key secondary benefit, the import process surfaces event names that couldn't be fully categorized, enabling the developer to expand the app's event understanding. Multiple competitions can be processed by running the script repeatedly or via a simple shell loop.

**Why this priority**: This is the highest-value data source because it uses the same upstream API the app already consumes, delivering the most directly applicable duration observations. It enables the app to start with much better predictions from day one.

**Independent Test**: Can be fully tested by running the script against a known competition ID (e.g., a past event) and verifying the output file contains correctly parsed event durations and heat counts in the expected format, and that unrecognized event names are clearly reported.

**Acceptance Scenarios**:

1. **Given** a valid tracktiming.live competition ID for a completed competition, **When** the script is run with that ID, **Then** the script produces an output file containing one duration record per event with structured category information (discipline, gender, age group, round, and any modifiers), duration in minutes, heat count (when available), competition metadata, and session metadata.
2. **Given** a competition ID for a partially completed competition, **When** the script is run, **Then** completed events have observed durations and incomplete events are omitted or flagged as incomplete.
3. **Given** an invalid or nonexistent competition ID, **When** the script is run, **Then** no output file is created and a clear error message is reported.
4. **Given** a competition containing events with names that can't be fully categorized (unrecognized discipline, ambiguous components), **When** the script is run, **Then** those events are recorded with as much category information as could be extracted (e.g., gender and age group may still be parseable even if discipline is unknown), and the script produces a summary listing each distinct uncategorized event name with its frequency, average observed duration, and whether heats were present — giving the developer the information needed to add support for new event types with appropriate default values.
5. **Given** a competition containing French-language event names (e.g., from Quebec competitions), **When** the script is run, **Then** the event names are categorized correctly for commonly encountered French terms, and any unrecognized French terms appear in the uncategorized summary for manual review.

---

### User Story 2 - Load Imported Data into the App's Learning Database (Priority: P2)

A developer or operator has one or more standardized data files produced by the import script (User Story 1). They run a loader script that reads these files and writes the duration observations — including full structured category information — into the app's learning database. The learning database schema is extended to store and query by structured categories (discipline, gender, age group), enabling granular learned durations (e.g., returning a different average for junior pursuit vs. elite pursuit). After loading, the app returns improved averages based on the imported observations.

**Why this priority**: Without a loader, the import scripts produce files that must be manually processed. The loader completes the pipeline from raw data to improved predictions.

**Independent Test**: Can be tested by creating a sample standardized data file, running the loader against a test database, and verifying that `get_learned_duration()` returns the expected averages.

**Acceptance Scenarios**:

1. **Given** a valid standardized data file with duration records, **When** the loader script is run targeting the local database, **Then** all records are persisted and the app's learned averages reflect the imported data.
2. **Given** a valid standardized data file with duration records, **When** the loader script is run with the production database backend configured, **Then** all records are persisted and the app's learned averages reflect the imported data.
3. **Given** a standardized data file containing a discipline not currently in the app's keyword list, **When** the loader is run, **Then** the record is stored with its discipline key as-is and a warning is emitted noting the unrecognized discipline.
4. **Given** a data file that has already been loaded (duplicate records), **When** the loader is run again, **Then** duplicate records are skipped or handled idempotently so that averages are not skewed by double-counting.

---

### Edge Cases

- What happens when a tracktiming.live competition has sessions with no completed events? The import script skips that session and logs a warning.
- How does the system handle events that can't be fully categorized? The import extracts whatever category dimensions it can (gender, age group, etc.) and marks the unresolved dimensions. These events are included in the uncategorized summary (see Acceptance Scenario 4) with their partial category information to help the developer diagnose what's missing.
- What happens when the tracktiming.live API is unreachable or returns malformed data? The script retries once, then exits with a clear error message.
- What happens when a duration value is unreasonably large (e.g., > 120 minutes for a single event)? The import script flags it as an outlier with a warning but still includes it in the output for manual review. The loader script applies the same validation bounds the app uses (0.5× to 3× static default).
- How does the system handle omnium events (multi-discipline, multi-part)? Each omnium component (e.g., "Scratch Race / Omni I") is categorized by its actual discipline with an omnium part modifier. Both standard (I-IV) and non-standard (extended) omnium formats are supported.
- How does the system handle exhibition or novelty events (e.g., "Chariot Race", "Kids Race")? These are categorized with a distinct discipline designation so they don't pollute standard discipline averages, but their durations are still recorded.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST define a standardized intermediate data format for duration records that includes at minimum: structured category information (discipline, gender, age group, round, and any modifiers such as ride number or omnium part), duration in minutes, heat count (when available), duration source indicator, source identifier, competition ID, session ID, event position, and original event name.
- **FR-002**: The system MUST provide a script that fetches historical competition data from tracktiming.live for a single competition ID and produces output in the standardized format.
- **FR-003**: The import script MUST extract actual event durations and heat counts from result and start-list pages where available, using the same duration-source priority as the app (observed finish time > generated timestamp differences > heat count estimation). When both total duration and heat count are known, the per-heat duration can be derived to refine the app's per-heat duration estimates.
- **FR-012**: The import script MUST decompose event names into structured category dimensions (discipline, gender, age group, round, and modifiers) rather than mapping to a flat discipline key. This compositional approach MUST handle the combinatorial variety of event naming — including age brackets, masters categories, para classifications, compound groups (e.g., "Elite/Junior"), omnium parts, and round/ride designators.
- **FR-013**: The import script MUST handle commonly encountered French event terminology (e.g., discipline names, gender abbreviations) found in Quebec competition data. Unrecognized French terms MUST appear in the uncategorized summary rather than silently falling through to a default.
- **FR-005**: The system MUST provide a loader script that reads standardized data files and writes duration observations into the app's learning database.
- **FR-006**: The loader script MUST support the app's dual database backends based on the same environment variable configuration the app uses.
- **FR-014**: The learning database schema MUST be extended to store and query duration observations by structured category dimensions (discipline, gender, age group) so that the app can return granular learned averages rather than aggregating all observations for a discipline into a single average.
- **FR-007**: The loader script MUST handle duplicate records idempotently — re-importing the same data file should not skew learned averages.
- **FR-008**: Both scripts MUST validate input data and provide clear, actionable error messages for invalid inputs.
- **FR-009**: The import script MUST handle competitions with multiple sessions, producing records for all sessions in a single run.
- **FR-010**: The standardized data format MUST be human-readable to support inspection and debugging of the import pipeline.
- **FR-011**: The import script MUST include in the per-competition output file a summary of all event names that could not be fully categorized, including for each: the partial category information that was extracted, observed duration in minutes, and whether heats were detected. This summary serves as a permanent record and recommendation for expanding the system's event categorization and adding appropriate default duration and per-heat duration values.

### Key Entities

- **Duration Record**: A single observation of how long an event took. Contains structured category information (discipline, gender, age group, round, and modifiers like ride number or omnium part), duration in minutes, duration source indicator (how the duration was determined), heat count (optional — present when determinable from start-list or result pages), source identifier, competition ID, session ID, event position, original event name, and a timestamp of when the observation was recorded. When both duration and heat count are present, the per-heat duration (duration ÷ heat count, minus changeover) can be computed to improve per-heat estimates. The structured category enables granular analysis — e.g., comparing duration patterns across age groups or genders within the same discipline.
- **Event Category**: The structured decomposition of an event name into its component dimensions: discipline (the type of race), gender, age group, round (qualifying, final, repechage, etc.), ride number (for multi-ride rounds), and omnium part number (for multi-discipline omnium events). Some dimensions may be absent or unresolvable for a given event name.
- **Import Manifest**: Metadata about an import run — source, competition ID processed, record count, warnings, and errors. Embedded in or alongside the output file for traceability.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After importing data from 5 or more historical competitions, the structured category data covers the majority of discipline, gender, and age group combinations encountered, and the app's learned duration averages cover at least 80% of the disciplines defined in the app's keyword list.
- **SC-002**: Imported duration values for known disciplines fall within the validation bounds (0.5× to 3× the static default) at least 90% of the time, confirming data quality.
- **SC-003**: The full import-and-load pipeline for a single competition completes in under 2 minutes, making batch processing of many competitions practical.
- **SC-004**: A developer unfamiliar with the scripts can successfully import and load data for a competition by following the script's help text and any accompanying usage instructions, without needing to read the app's source code.

## Clarifications

### Session 2026-03-14

- Q: How should structured categories map to the learning DB — flatten to discipline key, retain only in files, or extend the schema? → A: Extend the learning DB schema to store and query by structured categories (discipline + gender + age group), enabling granular learned durations. No need to preserve backward compatibility with the flat discipline-key schema.
- Q: Should the import script accept multiple competition IDs or just one? → A: Single competition ID per run. Multiple competitions can be processed by running the script repeatedly or via a shell loop. This simplifies the script and eliminates batch orchestration concerns.
- Q: Where should the uncategorized event summary be output? → A: Included as a section within the per-competition output file, providing a permanent record tied to each competition.

## Assumptions

- The tracktiming.live API will continue to serve historical competition data for past events (the same API already used by the app).
- Event names from tracktiming.live follow recognizable patterns that can be compositionally decomposed, though some competitions (particularly in Quebec) use French terminology that must be handled.
- A per-competition output file (rather than a single combined file) is a reasonable default, enabling re-processing of individual competitions without re-fetching all data.
- Scripts will be invoked from the command line as standalone Python scripts (not as part of the FastAPI app's request lifecycle).
- The scripts are developer/operator tools, not end-user features — they do not need a web UI.
