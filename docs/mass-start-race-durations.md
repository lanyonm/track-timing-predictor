# Mass Start Race Duration Estimation

This document explains how the predictor estimates durations for mass start track cycling disciplines: **scratch race**, **tempo race**, **elimination race**, **points race**, and **madison**.

## The Problem

Unlike timed events (pursuits, time trials, sprints), mass start races have no fixed duration — they run until a set lap count is completed. That lap count varies significantly by rider category, meaning a single flat default per discipline produces poor predictions. An elite men's scratch race at 7.5 km takes nearly twice as long as a U11/U13 race at 3 km.

## Approach

The predictor detects the rider category from the event name (e.g. "Elite/Junior Men Scratch Race / Omni I") and maps it to a **distance tier**, each with its own default duration. If no category-specific keyword is matched, a generic fallback is used.

The learning mechanism (SQLite) accumulates observed durations per discipline key as events complete. Once ≥ 3 samples exist for a key, the learned average overrides the built-in default.

## Data Source

Defaults are derived from finish times collected across multiple events. Race durations cover the period from the starting gun to the last rider crossing the finish line. A 2-minute changeover (setup / warm-down) is added to obtain the schedule slot estimate. The live predictor swaps that 2 minutes for the competition's calibrated changeover (see [Changeover](#changeover)); the "Slot (+ 2 min)" columns below are the static values that learned durations and `tools/` use.

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

## Points and Scratch Races: Duration from Distance

Every points and scratch race start list titles the race with its distance and laps (`50+ Women Points Race Final - 10km - 40 Laps`, sometimes with `- Sprint Every 5 Laps` after), in the same form at 26002, 26008, 26009, 26010 and 26037. Once the start list is posted, the slot is

```
slot = km / pace × 60 + changeover   (pace = disciplines.bunch_race_kmh(band); BUNCH_RACE_KMH = 46 without a band)
```

46 km/h is the median Finish Time speed of both disciplines across 26002–26037: 32 points races (35–52.5 km/h) and 38 scratch races (36–54.5 km/h). Speed depends on the field: elite men 50–53 km/h, masters men 45–50, elite and masters women 41–46, youth and some masters women 35–41. A single speed is within ~5 min on every measured race (slower fields come out short, the safe direction); the flat defaults were off by up to 17 min (points, 20) and 9 min (scratch, 12). At 26037 it's within 1.7 min of all 10 points races (7.5–30 km).

### Masters Pace by Age and Gender

Before the start list is posted, a competition with a committed supplement takes the distance from the organiser's published schedule (`supplements.scheduled_distances`; 26037's fullgascycling.co.uk schedule matched the start-list distance for all 26 races with both). The schedule gives no distance for the 75-79 and 80+ Men Points Race Finals, so they take the tech guide's 10 km for Male 75+ (`TECH_GUIDE_DISTANCES` in `tools/import_fullgas_26037.py`). The UI marks it **~N km est.**

Masters events whose name carries an age band (`50-54 Men`, `35-49 Women`, `80+ Men`; `rider_list.event_band`) use a pace for their gender and the band's youngest age (`MASTERS_BUNCH_RACE_KMH`). A combined-age race is paced by its youngest riders, so `35-49 Women` takes the under-50 value and `65+ Men` the under-70 one. Names without a band (26008's `ME`, `MU17`, `Master A Men`, elite and junior events) keep 46 km/h.

| Group (youngest age in band) | km/h | n | Median (range) |
|---|---|---|---|
| Men under 70 | 48.0 | 59 | 48.1 (39.5–54.5) |
| Men 70–74 | 41.5 | 5 | 41.5 (39.8–43.4) |
| Men 75+ | 36.0 | 6 | 35.8 (34.9–39.1) |
| Women under 50 | 43.5 | 5 | 43.5 (42.1–47.2) |
| Women 50+ | 41.0 | 7 | 41.1 (38.2–45.8) |

Data: 82 points and scratch races at the masters worlds 22023 (2022), 25032 (2025) and 26037 (2026). Ride time is the result-page Finish Time; km is the start list's title distance (`parser.parse_race_distance_km`). Values are group medians rounded to 0.5 km/h.

Cut points were chosen by leave-one-competition-out error over the three worlds (fit the medians on two, predict ride time on the third). Men's speeds by five-year band are flat from 35 to 64 (47.6–48.8 km/h medians), dip a little at 65–69 (46.3, n = 7) and drop at 70 and again at 75. Women split at 50.

| Groups | Mean absolute error (min) |
|---|---|
| Flat 46 km/h | 0.83 |
| Men <70 / 70–74 / 75+, women <50 / 50+ (chosen) | 0.39 (22023 0.39, 25032 0.38, 26037 0.39) |
| Men <65 / 65–69 / 70–74 / 75+, women <50 / 50+ | 0.39 |
| Men <70 / 70+, women <50 / 50+ | 0.42 |
| Men <65 / 65–69 / 70+, women <50 / 50+ | 0.41 |
| Men <70 / 70–74 / 75+, women one group | 0.39 |

A separate 65–69 group adds nothing out of sample, so it's folded into under 70. On 17 races at 25022 (a masters nationals with the same band naming, not used in the fit) the table gives 0.85 min against 1.01 for a flat 46.

The unbanded default stays 46. Those competitions mix elite men (50–53 km/h) with youth and women (35–46), and masters men under 70 at 48 km/h aren't a reason to speed up the rest.

Start lists appear about an hour before the race, so earlier views still use the defaults. Tempo races carry a distance too but haven't been measured, so they keep their default.

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

## Changeover

The changeover is everything in a bunch race's slot that isn't racing: clearing the previous race, staging the field, the neutral lap, and publishing results. It's measured as Generated gap minus Finish Time, where the Generated gap is the time between the previous event's result and this race's result.

Measured across 68 bunch races at 26002, 26008, 26009, 26010 and 26037 (in minutes):

| | n | Median |
|---|---|---|
| 26037 (masters worlds) | 13 | 8.2 |
| 26002 | 6 | 4.2 |
| 26008 | 27 | 3.4 |
| 26009 | 21 | 2.6 |
| 26010 | 1 | 5.0 |
| After another bunch race | 58 | 3.2 |
| After a sprint or timed event | 10 | 11.3 |

The static 2 minutes is close for national omnium sessions, where bunch races run back to back, and far short at a championship. So the live predictor calibrates it per competition (`predictor.bunch_changeover`):

- Samples: a scratch, points, elimination, tempo or madison race with a Finish Time, straight after another of those with its own result page, with an overhead between 0 and 20 min (outside that, result pages were uploaded out of order or regenerated). A race after a sprint or timed event also includes staging the field, so it's left out.
- With 3 or more samples the changeover is their median; before that, 3 min (the all-competition median after a bunch race).
- It's added to Finish Times for completed races, to distance estimates, and to defaults and learned averages in place of their static 2 minutes. Keirin keeps its static 2 minutes; it has no Finish Time to calibrate from.

On the full results, the calibration settles at 7.95 min for 26037, 3.38 for 26008 and 2.5 for 26009; 26002 has too few back-to-back bunch races and keeps 3.

The learning database still records Finish Time + the static 2 minutes, so learned averages stay comparable across competitions; the predictor shifts them like the defaults.

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

## Caveats

- **Limited sample**: the tier averages and slot estimates come from the finish times listed above. Once results are posted, observed Finish Times replace these estimates for live predictions.
- **Rider count is not used**: elimination race duration scales with the number of starters, but the estimate does not use the start list's rider count.
- **Distance before the start list**: schedule event names don't include distance (e.g. "Elite/Junior Men Scratch Race / Omni I"). Points and scratch races switch to a speed-based estimate once the start list (whose title has the distance) is posted, about an hour ahead; until then, and for other bunch races, the tier defaults apply.
