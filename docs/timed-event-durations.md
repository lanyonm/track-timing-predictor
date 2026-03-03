# Timed Event Durations

This document explains how the predictor estimates schedule slot durations for individual pursuits and time trials.

## How These Events Work

**Individual pursuit**: Two riders start simultaneously on opposite sides of the track and race until one catches the other or both complete the distance. Heats are sequential — the track is occupied by one pair at a time. A "slot" on the schedule covers all heats for one category (e.g. all gold/silver/bronze rides for Elite Men).

**Time trial**: Riders start one at a time at regular intervals. The slot covers all riders in one category back-to-back.

---

## Pursuit

### Distance by Category

All pursuits at the national/provincial level follow standard UCI distances:

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

Per-heat durations (race time + ~30 s transition):

| Distance | Per-Heat Duration |
|---|---|
| 4 km | 5.0 min |
| 3 km | 4.0 min |
| 2 km | 3.0 min |

When heat count is unknown, the default duration covers an assumed ~2-heat final:

| Discipline Key | Default Duration | Assumed Basis |
|---|---|---|
| `pursuit_4k` | 12.0 min | 2 heats × 5 min + 2 min buffer |
| `pursuit_3k` | 9.0 min | 2 heats × 4 min + 1 min buffer |
| `pursuit_2k` | 6.0 min | 2 heats × 3 min |

---

## Time Trials

### Distance and Event Type by Category

Time trials are sequential — each rider does one timed effort from a standing start (kilo) or flying start (500m / 750m).

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

### Reference Times per Rider

Each rider's slot includes the ride itself plus rolling off the track and the next rider rolling on.

| Distance | Typical Race Time | Slot per Rider |
|---|---|---|
| 500 m | ~40–55 s | ~2:20 |
| 750 m | ~55–65 s | ~2:40 |
| 1000 m | ~1:00–1:20 | ~2:30 |

Rider counts at E26008 ranged from 1 (Master A Women) to ~7 (Elite Men kilo), reflecting typical entry sizes at a regional championship. National and international events may have larger fields and correspondingly longer slots.

### Slot Duration Calculation

When heat count (= rider count) is known from the start list:

```
slot = rider_count × per_rider_duration
```

Per-rider durations used:

| Discipline Key | Per-Rider Duration |
|---|---|
| `time_trial_500` | 2.33 min (~2:20) |
| `time_trial_750` | 2.67 min (~2:40) |
| `time_trial_kilo` | 2.50 min (~2:30) |

When rider count is unknown, the default assumes ~8 riders:

| Discipline Key | Default Duration | Assumed Basis |
|---|---|---|
| `time_trial_500` | 20.0 min | 8 riders × 2:20 ≈ 19 min |
| `time_trial_750` | 22.0 min | 8 riders × 2:40 ≈ 21 min |
| `time_trial_kilo` | 22.0 min | 8 riders × 2:30 ≈ 20 min |

---

## Caveats and Future Work

- **Heat count is the key variable**: The per-heat/per-rider mechanism produces much more accurate estimates than the flat default when start lists are available. Ensuring the start list URL is parsed correctly is more impactful than tuning the default durations.
- **U15 pursuit distance**: U15 rides 2 km, matching U17 — but observed race times at E26008 were ~30–40 s slower per heat than U17 Men (3:08 vs 2:30), likely reflecting younger riders. Future data may justify a separate `pursuit_2k_u15` tier.
- **Format variation**: Some competitions run qualifying and medal rounds in separate schedule slots; others combine them. A qualifying round adds 2–4 heats per category that would otherwise not appear.
