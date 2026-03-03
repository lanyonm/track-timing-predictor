# Sprint Durations

This document explains how the predictor estimates schedule slot durations for sprint events: the flying 200m qualifying, match sprint rounds, and keirin.

## How These Events Work

**Sprint qualifying (flying 200m)**: Riders complete a rolling lap to build speed and are then timed over the final 200m. Each rider goes individually. One schedule slot covers all riders in one category.

**Match sprints**: Two riders race head-to-head over 3 laps (750m). Matches are best-of-two or best-of-three rides. A schedule slot covers one complete round for one category (e.g. all 1/4 final matches for Elite Men). Rounds progress from the 1/8 final (largest fields only) through the 1/4 final, 1/2 final, and final (gold + bronze rides).

**Keirin**: Several riders, typically six, but as few as four and as many as nine, follow a motorized pacer (or "derny") for the first 3 laps, with speed gradually increasing from 30 km/h to 50 km/h (18–31 mph). The pacer pulls off with 2.5 laps to go, initiating an all-out sprint to the finish. When there are more racers in a category than can be safely run in a single heat, multiple heats are run to determine the major and minor finals.

---

## Sprint Qualifying (Flying 200m)

### Structure

Each rider completes one timed 200m effort. The total distance ridden is 3.5 laps (875m on a 250m track): approximately 3 laps of rolling build-up plus the timed 200m section. Time between riders includes rolling off the track, the next rider entering, and completing the warm-up lap.

### Observed Data

| Category | Riders | Fastest 200m | Slowest 200m | Source |
|---|---|---|---|---|
| Elite Men | ~12 | 10.4 s | ~13 s | E26008 |
| Elite/Junior Women | ~7 | ~11 s | ~13 s | E26008 |
| Junior Men | ~5 | ~11 s | ~12 s | E26008 |
| Master A/B Men | ~6 | ~10.5 s | ~12 s | E26008 |
| Master C/D Men | ~9 | 11.9 s | 14.4 s | E26008 |
| U17 Men | 5 | 11.2 s | 12.2 s | E26008 |
| U17 Women | 5 | 12.9 s | 14.6 s | E26008 |

### Slot Duration Calculation

When rider count is known from the start list:

```
slot = rider_count × per_rider_duration
```

The per-rider slot time includes the timed 200m effort plus the rolling build-up lap and transition to the next rider. Standard reference: **~1:15 per rider** across all categories (the raw 200m time is 10–15 seconds; the majority of the slot is the rolling approach and reset).

| Discipline Key | Per-Rider Duration |
|---|---|
| `sprint_qualifying` | 1.25 min (~1:15) |

When rider count is unknown, the default assumes ~8 riders:

| Discipline Key | Default Duration | Assumed Basis |
|---|---|---|
| `sprint_qualifying` | 10.0 min | 8 riders × 1:15 ≈ 10 min |

---

## Match Sprints

### Round Structure

Sprint rounds are scheduled as individual rows on the schedule — one row per round per category. Each round is a set of head-to-head matches; the winner advances. The number of matches per round depends on the number of riders.

| Round | Typical Heats (Matches) | Notes |
|---|---|---|
| 1/8 Final | up to 4 | Held only for larger fields (10+ riders); bye heats take no time |
| 1/4 Final | up to 4 | Bye heats take no time |
| 1/2 Final | 2 | 2 semi-final matches |
| Final | 2 | Gold final + bronze final |

Matches are never run concurrently — each heat occupies the full track. Byes are recorded as heats in the result book but consume no schedule time.

From E26008:
- 1/4 Final heats per category: U17 Women 2, U17 Men 4, Master C/D Men 2, Master A/B Men 2, Junior Men 2, Elite/Junior Women 1, Elite Men 2
- 1/2 Final: uniformly 2 heats across all categories
- 1/8 Final (Master C/D Men): 4 heats (some were byes); (Elite Men): 1 heat (remaining were byes)

### Match Duration

Each 3-lap (750m) ride takes ~40–50 seconds at racing speeds. A best-of-two or best-of-three match (including the standing restart between rides) typically runs **2–4 minutes** per match.

A complete round (e.g. 1/4 final with 2–4 matches) typically fills **8–15 minutes** as a schedule slot.

### Slot Duration Calculation

When heat count is known from the start list:

```
slot = heat_count × per_heat_duration
```

| Discipline Key | Per-Heat Duration |
|---|---|
| `sprint_match` | 3.0 min |

When heat count is unknown:

| Discipline Key | Default Duration | Assumed Basis |
|---|---|---|
| `sprint_match` | 12.0 min | ~4 matches × 3 min |

---

## Keirin

### Structure

A keirin is a mass-start sprint race for 6–8 riders. Riders pace behind a motorized derny for several laps to build speed; the derny pulls off with approximately 600–700m remaining and riders sprint to the finish. Rounds typically have multiple heats run sequentially on the same schedule row (e.g. two heats for a semi-final, six heats for a round-of-48).

### Slot Duration Calculation

When heat count is known from the start list:

```
slot = heat_count × per_heat_duration + changeover
```

| Discipline Key | Per-Heat Duration | Changeover |
|---|---|---|
| `keirin` | 4.5 min | 2.0 min |

The per-heat duration covers the full keirin race (~4:30) plus the short recovery and reset between heats.

When heat count is unknown, the default assumes approximately one complete round:

| Discipline Key | Default Duration | Assumed Basis |
|---|---|---|
| `keirin` | 6.5 min | 1 heat × 4:30 race + 2:00 changeover |

### Observed Data

| Round | Typical Heats | Notes |
|---|---|---|
| Round 1 / Repechage | 2–6 | Depends on field size |
| 1/2 Final | 2 | Two semi-final heats |
| 7–12 Final | 1 | Consolation final |
| 1–6 Final | 1 | Gold final |

---

## Caveats and Future Work

- **Best-of-three vs. best-of-two**: When a match goes three rides, a round takes significantly longer. The per-heat duration of 3 min implicitly averages over this.
- **Larger sprint fields**: At national championships or World Cup events, elite sprint fields may have 16–24 riders, adding a second round of 1/8 finals and more matches per round.
- **U15 sprints**: Not observed in this dataset. U15 riders typically race sprint qualifying only (no match sprint bracket); their qualifying event is included under `sprint_qualifying`.
