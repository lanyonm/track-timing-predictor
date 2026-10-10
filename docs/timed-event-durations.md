# Timed Event Durations

This document explains how the predictor estimates schedule slot durations for individual pursuits, team pursuit, team sprint and time trials.

## How These Events Work

**Individual pursuit**: Two riders start simultaneously on opposite sides of the track and race until one catches the other or both complete the distance. Heats are sequential — the track is occupied by one pair at a time. A "slot" on the schedule covers all heats for one category (e.g. all gold/silver/bronze rides for Elite Men).

**Time trial**: Riders start one at a time at regular intervals. The slot covers all riders in one category back-to-back.

---

## Pursuit

### Distance by Category

All pursuits at the national/provincial level follow standard UCI distances. These are what the name-based classifiers assume when an event has no links yet; once it has any URL, the distance comes from the URL's `-IP-<metres>-` token. Real competitions don't always follow the table: at 26008 and 26009 Junior Women rode 3 km and U17 Men 2 km, and at 26037 (masters worlds, age-group names) men and women 35–49 rode 3 km and 50+ rode 2 km. For an age-band name (`60-64 Men Pursuit`) with no URL yet, the predictor uses that split rather than the table (`disciplines.pursuit_discipline_from_band`).

| Category | Distance |
|---|---|
| Elite Men / Elite Women | 4 km |
| Junior Men / Junior Women | 3 km |
| Master A Men / Master B Men | 3 km |
| Master C Men / Master D Men | 2 km |
| Master Women (all grades) | 2 km |
| U17 Men / U17 Women | 2 km |
| U15 Men / U15 Women | 2 km |

### Observed Race Times (per heat, slower rider)

The heat ends when the slower of the two riders finishes. The data below shows the range of heat times observed across gold, silver, and bronze rides.

| Category | Distance | Heats | Fastest Heat | Slowest Heat | Source |
|---|---|---|---|---|---|
| Elite Men | 4 km | 2 | 4:34 | 4:52 | E26008 |
| Elite Women | 4 km | 2 | 5:18 | 5:39 | E26008 |
| Junior Men | 3 km | 4 | 3:46 | 4:04 | E26008 |
| Junior Women | 3 km | 3 | 3:56 | 4:27 | E26008 |
| Master A Men | 3 km | 1 | 3:33 | 3:48 | E26008 |
| Master B Men | 3 km | 4 | 4:04 | 4:15 | E26008 |
| Master A Women | 2 km | 1 | 2:14 | 2:33 | E26008 |
| Master C Men | 2 km | 2 | 2:30 | 2:38 | E26008 |
| Master D Men | 2 km | 2 | 2:42 | 2:53 | E26008 |
| U17 Men | 2 km | 5 | 2:30 | 2:37 | E26008 |
| U17 Women | 2 km | 3 | 2:42 | 2:57 | E26008 |
| U15 Men | 2 km | 2 | 3:08 | 3:14 | E26008 |
| U15 Women | 2 km | 2 | 3:06 | 3:17 | E26008 |

Notes:
- Heat count depends on the number of entrants and the competition format (qualifying + medal rounds vs. medal rounds only).
- U17 Men had 5 heats, suggesting a qualifying round was held on the same day within the same schedule slot.
- Master A Men and Master A Women had only 1 heat each, consistent with a small field.

### Slot Duration Calculation

When the predictor knows the heat count from a start list:

```
slot = heat_count × per_heat_duration
```

(No changeover is added for pursuits; the per-heat duration already includes the setup time between heats.)

Per-heat durations come from the measured data in [Measured Per-Heat Durations](#measured-per-heat-durations). The race times above are only part of each slot: rider setup, the start and the clear-down between heats add about 1.5–2.5 min per heat.

| Distance | Per-Heat Duration |
|---|---|
| 4 km | 7.5 min |
| 3 km | 6.25 min |
| 2 km | 5.0 min (4.5 for a masters age band under 70, see [Masters Per-Heat Durations](#masters-per-heat-durations)) |

Before the start list is posted, a masters pursuit qualifying round takes its heat count from the Rider List: ⌈entrants ÷ 2⌉, counting riders in the event's age band with the `IP` code (`rider_list.estimate_heats`). At 26037 this matched the start list for 6 of 11 qualifying rounds and was one heat high for the rest, from non-starters (e.g. 8 entrants, 3 heats for 80+ Men). Team qualifying rounds aren't sized this way. The Rider List's `TP`/`TS` codes undercount team riders, since teams are made up after entries close: of the 24 riders in 26037's 55-64 Men team pursuit qualifying, 13 had `TP`, one was an M6569 riding down, and the team column holds the nation, not the team. ⌈TP entrants ÷ 4⌉ would give 4 heats against 6 ridden. Instead, 26037's team qualifying rounds are sized from the organiser's team entry list (fullgascycling.co.uk/team-events, captured into `app/data/fields/26037.json` by `tools.import_fullgas_teams`; `fields.heats_from_fields`): one heat per team, counting teams whose age band lies inside the event's. This matched all 8 team qualifying rounds ridden by 2026-10-09; on 2026-10-10, 35-44 Women Team Pursuit rode 3 heats against 2 listed teams (the page is updated weekly). Competitions without a field-size file keep the default.

A final that follows a qualifying round of the same name (`45-49 Men Pursuit Qualifying` → `45-49 Men Pursuit Final`) is ridden for bronze and gold, so it counts 2 heats before its start list is posted (`predictor.infer_heats`). At 26037, 18 of 21 such finals (pursuit, team pursuit, team sprint) had 2 heats; the other 3, with small fields, had 1, so a team final with two or fewer teams in the field-size file counts 1 heat. A final with no qualifying round (regional `Pursuit Final`s, where every rider rides) isn't sized by name.

Otherwise, when heat count is unknown, the default duration covers an assumed 2-heat final (the median heat count at 2 km and 4 km):

| Discipline Key | Default Duration | Assumed Basis |
|---|---|---|
| `pursuit_4k` | 15.0 min | 2 heats × 7.5 min |
| `pursuit_3k` | 12.5 min | 2 heats × 6.25 min |
| `pursuit_2k` | 10.0 min | 2 heats × 5.0 min |

---

## Team Pursuit and Team Sprint

Both use the same `heat_count × per_heat_duration` formula. Finals race two teams per heat (bronze, gold); qualifying can be one team per heat (26037's 55-64 Men team pursuit: 6 teams, 6 heats). Per-heat durations are 6.75 min for `team_pursuit` and 3.0 min for `team_sprint`, from the measured data below; masters team sprints with an age band in the name use 3.5 min ([Masters Per-Heat Durations](#masters-per-heat-durations)). The flat defaults (10.0 min each) match the measured whole-event medians (10.4 and 9.4 min) and are unchanged.

---

## Time Trials

### Distance and Event Type by Category

Each rider does one timed effort from a standing start (kilo) or flying start (500m / 750m). Riders go **two per heat**, one on each side of the track: every competition with measured slots (25022, 26002, 26008, 26009, 26037) has about half as many heats as riders (e.g. 18 riders in 9 heats, 25 in 13). The start-list heat count is the number of these pairs.

| Category | Distance | Type |
|---|---|---|
| Elite Men / Elite Women | 1000 m | Kilo (standing start) |
| Junior Men / Junior Women | 1000 m | Kilo (standing start) |
| Master A Men | 1000 m | Kilo (standing start) |
| Master B Men | 750 m | Flying start |
| Master C Men / Master D Men | 500 m | Flying start |
| Master Women (all grades) | 500 m | Flying start |
| U17 Men / U17 Women | 500 m | Flying start |
| U15 Men / U15 Women | 500 m | Flying start |
| U13 / U11 | 500 m | Flying start |

### Reference Times per Heat

Each heat's slot covers both riders' efforts (run at once) plus clearing the track and the next pair setting up.

| Distance | Typical Race Time | Slot per Heat |
|---|---|---|
| 500 m | ~40–55 s | ~2:20 |
| 750 m | ~55–65 s | ~2:40 |
| 1000 m | ~1:00–1:20 | ~3:00 |

### Slot Duration Calculation

When the heat count is known from the start list:

```
slot = heat_count × per_heat_duration
```

| Discipline Key | Per-Heat Duration | Measured (Generated gap ÷ heats) |
|---|---|---|
| `time_trial_500` | 2.33 min (2.75 for a masters age band 70+) | median 2.55 (n = 12): 1.65–3.40 at 25022, 26002, 26008, 26009; 3.28–3.71 at 26037 |
| `time_trial_750` | 2.67 min | 2.21, 2.95 (26037), 5.25 (26002, likely includes a break) |
| `time_trial_kilo` | 3.00 min | median 3.10 (n = 7), 2.65–3.92 |

The constants are unchanged. The 500 m median is pulled up by 26037 (masters worlds), whose three clean measurements run about 3.3 min per heat; the other competitions' median is 2.31. Two of those three (75-79 and 80-84 Men) are 70+ events, which now use 2.75 (below). Two 26037 measurements are excluded: 90+ Men (1 rider, 15.0 min, includes a break) and 40-44 Women 750 m (6.37 min per heat).

Before the start list is posted, a masters time trial takes its heat count from the Rider List: ⌈entrants ÷ 2⌉, counting riders in the event's age band with the `TT` code. At 26037 this matched the start list for all 8 completed women's TTs and 3 of 6 men's; the other men's were 1–2 heats high from non-starters.

When the heat count is unknown, the default assumes 7–8 heats (14–17 riders):

| Discipline Key | Default Duration | Assumed Basis |
|---|---|---|
| `time_trial_500` | 20.0 min | ~8.5 heats × 2:20 |
| `time_trial_750` | 22.0 min | ~8 heats × 2:40 |
| `time_trial_kilo` | 22.0 min | ~7 heats × 3:00 |

---

## Measured Per-Heat Durations

Per-heat duration = an event's Generated-timestamp gap (`generated_diff` observations in the extracted reports) ÷ its start-list heat count. Observations derived from heat counts are excluded, since they're computed from the model itself. Data: 25022, 25026, 25027, 25028, 25031, 26001, 26002, 26008, 26009, 26010 and 26037, extracted 2026-10-06 with URL-derived pursuit distances. Values are minutes; cells show the median per competition (n = events).

| Discipline | 25022 | 25028 | 26002 | 26008 | 26009 | 26037 | All (median) | Previous | Now |
|---|---|---|---|---|---|---|---|---|---|
| `pursuit_2k` | 4.19 (5) | | 5.20 (5) | 4.54 (2) | 4.72 (3) | | 4.72 (15) | 3.0 | 5.0 |
| `pursuit_3k` | 4.92 (1) | | 1.54 (1) | 5.71 (1) | 5.39 (1) | 6.20 (1) | 5.39 (5) | 4.0 | 6.25 |
| `pursuit_4k` | 8.05 (3) | | 7.88 (3) | 6.80 (1) | | | 7.88 (7) | 5.0 | 7.5 |
| `team_pursuit` | 5.93 (5) | | | 6.43 (5) | 8.07 (2) | 7.27 (1) | 6.90 (13) | 5.0 | 6.75 |
| `team_sprint` | 4.50 (4) | 2.20 (1) | | 2.73 (4) | | | 3.01 (9) | 2.67 | 3.0 |
| `time_trial_kilo` | | | 3.13 (2) | 3.26 (2) | 2.83 (3) | | 3.10 (7) | 2.5 | 3.0 |
| `time_trial_500` | 2.30 (5) | | 2.60 (1) | 3.02 (2) | 2.16 (1) | | 2.31 (9) | 2.33 | 2.33 |

New values are the all-competition median rounded to a quarter-minute. They round down for `pursuit_2k`, `pursuit_4k` and `team_pursuit`, where single slower meets (26002 pursuits, 26009 team pursuit) pull the median up, so no one competition sets the value. The 26002 `pursuit_3k` value (1.54) is an outlier: a 3-heat event whose Generated gap was 4.6 min.

`pursuit_2k` and `pursuit_3k` were later raised to 5.0 and 6.25 to fit masters data. At 26037 (masters worlds) over days 1–3, where the column above covers only day 1, 2 km qualifying rounds measured 4.45–7.64 min per heat (median 5.31, n = 7) and 3 km 5.90–6.85 (median 6.30, n = 4), against 4.5 and 5.5.

The same method confirmed the existing values for `sprint_qualifying` (median 1.36 vs 1.25), `sprint_match` (3.00 vs 3.0) and `keirin` (4.53 vs 4.5), so those are unchanged.

**Fixed per-event overhead: not added.** Rounds with only a few heats run long per heat at 26037 (a 5-heat sprint qualifying at 2.94 min per heat). Across the other competitions, sprint qualifying with 4–16 heats stays at 1.1–1.5 min per heat with no visible trend by heat count, so the data doesn't yet support a fixed term.

## Masters Per-Heat Durations

Masters events whose name carries an age band (`60-64 Women Pursuit Qualifying`, `75+ Men Team Sprint`; `rider_list.event_band`) can override the per-heat constant by the band's youngest age (`MASTERS_PER_HEAT_DURATIONS`, read by `disciplines.get_per_heat_duration(discipline, band)`). Unbanded events and disciplines without an override use the constants above. In the live app the override applies wherever the per-heat value does: the heat-count estimate, the expected duration that bounds a Generated gap, the active-heat counter and a racer's heat start. The import tools (`tools.extract_competition`, `tools.load_durations`) keep the unbanded constants: their heat-count records feed per-discipline learned averages, which shouldn't depend on a competition's age mix, and the unbanded bounds reject three break-inflated team sprints (22023 35-44 Men Final, 25022 35+ Men Final, 25032 45-54 Women Qualifying) that the masters 3.5 would let in.

Data: `generated_diff` observations with a start-list heat count at the masters worlds 22023, 25032 and 26037 and the masters nationals 25022 (the overrides apply to any banded name, so 25022's faster officiating counts too). Per heat = Generated gap ÷ heats. Measurements outside 0.5×–2.0× the current constant are dropped as break-inflated or out of order, the same bounds the live predictor puts on a Generated gap: 26037 90+ Men 500 m (1 heat, 14.97 min), 26037 40-44 Women 750 m (6.37), 25032 70-74 Men 2 km pursuit qualifying (1.73), and the team sprints 22023 35-44 Men Final (6.37), 25032 45-54 Women Qualifying (6.37), 25022 35+ Men Final (6.57) and 26037 55+ Women Final (8.87, 1 heat).

Decision rule: an override for a (discipline, age group) needs n ≥ 5, a quarter-minute-rounded median at least 0.25 min from the constant, and leave-one-competition-out error (fit on three competitions, whole-slot mean absolute error on the fourth) no worse than the constant's. Age groups are under 70 and 70+ (the youngest age in the band), except team events, which are combined-age (`35-44`, `55+`, `75+`) with a single 70+ measurement each, so they're one masters group.

| Discipline | Group | n | Median | Constant | LOCO MAE: constant → override | Decision |
|---|---|---|---|---|---|---|
| `pursuit_2k` | under 70 | 22 | 4.48 | 5.0 | 1.91 → 1.87 | **4.5** |
| `pursuit_2k` | 70+ | 12 | 5.00 | 5.0 | | no change: matches |
| `pursuit_3k` | under 70 (all 35–49) | 10 | 6.20 | 6.25 | | no change: within 0.25 |
| `time_trial_500` | under 70 | 12 | 2.51 | 2.33 | | no change: within 0.25 (masters worlds alone 2.67; 25022 1.65–2.50) |
| `time_trial_500` | 70+ | 7 | 2.75 | 2.33 | 4.45 → 3.67 | **2.75** |
| `time_trial_750` | under 70 | 3 | 2.95 | 2.67 | | no change: n < 5 |
| `time_trial_kilo` | | 0 | | 3.0 | | no change: no clean masters measurements |
| `team_sprint` | all bands | 15 | 3.39 | 3.0 | 1.78 → 1.27 | **3.5** |
| `team_pursuit` | all bands | 12 | 7.50 | 6.75 | 2.63 → 2.84 | no change: worse out of sample |

Team pursuit passes the first two tests but not the third. Worlds slots mostly run 7.3–10.2 min per heat and 25022's multi-heat finals 5.5–5.7, so the gap is the competition, not the age band, and a masters value fitted to one misses the other. The 2 km pursuit override helps 22023, 25022 and 25032 and costs 26037 (1.66 → 2.53 min), whose qualifying rounds set the 5.0 constant; on balance it's neutral-to-better.

**Masters worlds pace (500 m TT).** 26037's slow 500 m heats (3.28, 3.34) are 75-79 and 80-84 Men; with the 70+ override they're predicted at 2.75 instead of 2.33. Its third (60-64 Women, 3.71) and 22023's 70-74 Men (3.88) remain slow outliers. Under 70, the worlds run ~2.67 per heat against 1.65–2.50 at the 25022 nationals and 2.31 at non-masters events, so some of the gap is the event, not the age. That residual is too small and inconsistent to justify a masters-event multiplier; a per-competition calibration like the bunch changeover remains the option if it matters. The unbanded 500 m (2.33, measured 2.31) and kilo (3.0, measured 3.10) constants stay.

## Caveats and Future Work

- **Heat count is the key variable**: The per-heat/per-rider mechanism produces much more accurate estimates than the flat default when start lists are available. Ensuring the start list URL is parsed correctly is more impactful than tuning the default durations.
- **U15 pursuit distance**: U15 rides 2 km, matching U17 — but observed race times at E26008 were ~30–40 s slower per heat than U17 Men (3:08 vs 2:30), likely reflecting younger riders. Future data may justify a separate `pursuit_2k_u15` tier.
- **Format variation**: Some competitions run qualifying and medal rounds in separate schedule slots; others combine them. A qualifying round adds 2–4 heats per category that would otherwise not appear.
