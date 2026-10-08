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

Before the start list is posted, a masters sprint qualifying round takes its rider count from the Rider List: riders in the event's age band with the `S` code (`rider_list.estimate_heats`). At 26037 this was exact or one rider high (9/9, 6/5, 21/20, 20/20 entrants/riders).

Otherwise, when rider count is unknown, the default assumes ~8 riders:

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

When heat count is unknown, the round name gives the pairs where it's fixed (`disciplines.sprint_round_pairs`): a 1/2 Final or Final has 2 (6 min), a 1/4 Final 4 (12 min). Their start lists only appear once the previous round is done, so this covers day-ahead views. Other rounds (1/8 Finals: 4 heats at 26008 with byes, 8 at 26037) and placement finals (`5-8 Final`, one race) use the default:

| Discipline Key | Default Duration | Assumed Basis |
|---|---|---|
| `sprint_match` | 12.0 min | ~4 matches × 3 min |

### Best-of-3 Rides (Ride 1, Ride 2, Ride 3)

Masters championships (26037) schedule each best-of-3 ride as its own row: `55-59 Men Sprint 1/4 Final Ride 1`, `Ride 2`, `Ride 3`. All three rows share one start list and one result page (`M5559-S-4-R1-S.htm`, `M5559-S-4-R1-R.htm`), and the start list's heat count is the number of pairs. Every pair rides Rides 1 and 2, so those use the normal heat-count estimate.

Ride 3 is the decider, ridden only by pairs tied 1–1. In completed 26037 rounds (days 1–3) 4 of 32 pairs needed one, so a Ride 3 is estimated as:

```
slot = heat_count × SPRINT_DECIDER_RATE × SPRINT_DECIDER_MINUTES   (0.12 × 4.25)
```

(2.04 min for a 4-pair round, 1.02 for 2 pairs; without a start list, the pairs come from the round name.) Expected value rather than "one match" because overestimating pushes every later event's predicted start too late.

Once Ride 2 is posted, the shared result page shows which pairs are tied, and the Ride 3 uses the exact count: `deciders × SPRINT_DECIDER_MINUTES`, shown as **N heats** (0 when none). `parser.parse_sprint_deciders` reads it: each pair's header row (`Heat N`, or `Final 3-4`/`Final 1-2` on a Final) carries a 200m time per ride ridden, and each rider row a `Winner` (or a gap, or `REL` for a relegated rider) per ride. A pair needs a decider if it rode one or each rider won once. Upstream drops a Ride 3 row from the schedule when no pair needs it.

`SPRINT_DECIDER_MINUTES` (4.25) is the median of the one-decider Ride 3 slots at 26037: 2.6, 3.4, 4.23, 4.4 and 7.3 min. That's more than a match's 3.0-minute share of a full round, since a lone decider carries the whole changeover. Its 0.5×–2× Generated-gap window (2.1–8.5 min) covers all five.

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
