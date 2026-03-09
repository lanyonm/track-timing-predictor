# Mass Start Race Duration Estimation

This document explains how the predictor estimates durations for mass start track cycling disciplines: **scratch race**, **tempo race**, **elimination race**, **points race**, and **madison**.

## The Problem

Unlike timed events (pursuits, time trials, sprints), mass start races have no fixed duration — they run until a set lap count is completed. That lap count varies significantly by rider category, meaning a single flat default per discipline produces poor predictions. An elite men's scratch race at 7.5 km takes nearly twice as long as a U11/U13 race at 3 km.

## Approach

The predictor detects the rider category from the event name (e.g. "Elite/Junior Men Scratch Race / Omni I") and maps it to a **distance tier**, each with its own default duration. If no category-specific keyword is matched, a generic fallback is used.

The learning mechanism (SQLite) accumulates observed durations per discipline key as events complete. Once ≥ 3 samples exist for a key, the learned average overrides the built-in default.

## Data Source

Defaults are derived from finish times collected across multiple events. Race durations cover the period from the starting gun to the last rider crossing the finish line. A 2-minute changeover (setup / warm-down) is added to obtain the schedule slot estimate.

Each data row below includes a source event tag (e.g. `[E26008]`) for traceability.

---

## Scratch Race

| Category | Distance | Finish Time | Source | Slot (+ 2 min) |
|---|---|---|---|---|
| Elite/Junior Men | 7.5 km | 8:59 | E26008 | |
| Elite/Junior Women | 7.5 km | 9:49 | E26008 | |
| Master A/B Men | 7.5 km | 9:57 | E26008 | |
| Master C Men | 7.5 km | 10:06 | E26008 | |
| **Long tier average** | **7.5 km** | **9:43** | | **~12 min** |
| U17/U15 Women | 4 km | 6:08 | E26008 | |
| U17 Men | 5 km | 6:55 | E26008 | |
| Master Women | 5 km | 7:51 | E26008 | |
| Master D Men | 5 km | 6:49 | E26008 | |
| **Medium tier average** | **4–5 km** | **6:56** | | **~9 min** |
| U11 & U13 | 3 km | 4:54 | E26008 | |
| **Short tier** | **3 km** | **4:54** | | **~7 min** |

Note: U15 Men (5 km) did not have a finish time in the result book for E26008; their result was combined with U17 Men.

---

## Tempo Race

| Category | Distance | Finish Time | Source | Slot (+ 2 min) |
|---|---|---|---|---|
| Elite/Junior Men | 7.5 km | 8:25 | E26008 | |
| Elite/Junior Women | 7.5 km | 9:46 | E26008 | |
| Master A/B Men | 7.5 km | 9:28 | E26008 | |
| Master C Men | 7.5 km | 9:50 | E26008 | |
| **Long tier average** | **7.5 km** | **9:22** | | **~11 min** |
| U17/U15 Women | 4 km | 5:54 | E26008 | |
| U17 Men | 5 km | 6:14 | E26008 | |
| U15 Men | 5 km | 6:59 | E26008 | |
| Master D Men | 5 km | 6:40 | E26008 | |
| Master Women | 5 km | 7:19 | E26008 | |
| **Medium tier average** | **4–5 km** | **6:37** | | **~9 min** |
| U11 & U13 | ~3.75 km* | 3:51 | E26008 | |
| **Short tier** | **3 km** | **3:51** | | **~6 min** |

*The U11/U13 tempo race was scheduled for 3 km (12 laps) but the results sheet shows 3.75 km (15 laps), suggesting a last-minute distance change.

---

## Elimination Race

Elimination races have no fixed distance — they run until one rider remains (or a defined number of riders). Duration is primarily determined by the number of starters. Elite/Junior combined fields tend to be larger than age-category fields.

| Category | # Riders | Finish Time | Source | Slot (+ 2 min) |
|---|---|---|---|---|
| Elite/Junior Women | 13 | 8:00 | E26008 | |
| Elite/Junior Men | 14 | 8:08 | E26008 | |
| **Elite tier average** | | **8:04** | | **~10 min** |
| U11 & U13 | 9 | 6:14 | E26008 | |
| Master C Men | 10 | 6:01 | E26008 | |
| U17/U15 Women | 8 | 5:08 | E26008 | |
| U15 Men | 8 | 5:10 | E26008 | |
| Master Women | ~8 | 5:08 | E26008 | |
| Master A/B Men | 11 | 5:10 | E26008 | |
| U17 Men | 8 | 4:24 | E26008 | |
| Master D Men | 7 | 4:01 | E26008 | |
| **Standard tier average** | | **5:10** | | **~7 min** |

---

## Points Race

Points races award sprint points every N laps; the distance varies widely by category.

| Category | Distance | Finish Time | Source | Slot (+ 2 min) |
|---|---|---|---|---|
| Elite/Junior Women | 15 km | 20:25 | E26008 | |
| Elite/Junior Men | 15 km | 19:18 | E26008 | |
| Master A/B Men | 15 km | 19:20 | E26008 | |
| **Long tier average** | **15 km** | **19:41** | | **~22 min** |
| U17/U15 Women | 7.5 km | 12:52 | E26008 | |
| U17 Men | 10 km | 13:32 | E26008 | |
| Master C Men | 10 km | 13:15 | E26008 | |
| Master D Men | 10 km | 13:52 | E26008 | |
| U15 Men | 10 km | 15:07 | E26008 | |
| Master Women | 10 km | 15:51 | E26008 | |
| **Standard tier average** | **7.5–10 km** | **14:05** | | **~16 min** |
| U11 & U13 | 4 km | 6:12 | E26008 | |
| **Short tier** | **4 km** | **6:12** | | **~8 min** |

---

## Madison

The Madison is a team event where pairs of riders alternate laps via a hand-sling. Points are awarded for sprints every 10 laps. Race distances vary widely by category and event level, making duration prediction less consistent than for other mass start disciplines.

### Observed Data

| Category | Distance | Finish Time | Source | Slot (+ 2 min) |
|---|---|---|---|---|
| Elite/Junior Men | 15 km | 21:00 | E26008 (ref) | ~23 min |
| Elite/Junior Women | 15 km | 25:30 | E26008 (ref) | ~28 min |
| Elite Men | ~20 km | ~34:18 | E26002 | ~36 min |
| Elite Women | ~15 km | ~24:30 | E26002 | ~26 min |

*E26008 times are from the schedule reference sheet; E26002 finish times are back-calculated from observed slot durations (obs − 2 min changeover).*

### Default Duration

Madison uses a single flat default (no distance tiers) since category and distance information is not reliably present in event names:

| Discipline Key | Default Duration | Assumed Basis |
|---|---|---|
| `madison` | 22.0 min | ~15–20 km race + 2 min changeover |

The learning mechanism will improve this as observed durations accumulate per venue/level.

---

## Discipline Key Mapping

The predictor uses keyword-phrase matching on the lowercased event name. Keywords are evaluated in order; the first match wins. The resulting discipline key is used to look up the default duration and accumulate learned durations in SQLite.

| Tier | Discipline Keys |
|---|---|
| Scratch long | `scratch_race_long` |
| Scratch medium | `scratch_race_medium` |
| Scratch short | `scratch_race_short` |
| Scratch (fallback) | `scratch_race` |
| Tempo long | `tempo_race_long` |
| Tempo medium | `tempo_race_medium` |
| Tempo short | `tempo_race_short` |
| Tempo (fallback) | `tempo_race` |
| Elimination elite | `elimination_race_elite` |
| Elimination (fallback) | `elimination_race` |
| Points long | `points_race_long` |
| Points standard | `points_race_standard` |
| Points short | `points_race_short` |
| Points (fallback) | `points_race` |

---

## Caveats and Future Work

- **Limited sample**: Finish times will be expanded as more events are researched. The learning mechanism will further improve predictions as live events are observed. The tier averages and slot estimates will be updated as additional data is added.
- **Rider count matters for elimination**: Duration scales with the number of starters. A future improvement could parse the start list page for rider count and scale accordingly.
- **Distance in event names**: tracktiming.live schedule event names do not include distance (e.g. "Elite/Junior Men Scratch Race / Omni I"). If a future data source includes distance, speed-based calculation could replace the tier lookup.
