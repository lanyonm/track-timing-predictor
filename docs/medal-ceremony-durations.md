# Medal Ceremony Durations

This document explains how the predictor estimates medal ceremony slots: `CEREMONY_BASE_MINUTES + podiums × CEREMONY_PER_PODIUM_MINUTES` (13 + 3.3 per podium, in `app/disciplines.py`), with the podium count forecast by `app/ceremonies.py`. A ceremony without a forecast keeps the flat `DEFAULT_DURATIONS["ceremony"]` (20 min).

The data is from EventId 26037 (2026 UCI Masters Track World Championships, London), days 1–3.

## What a ceremony awards

Each `CEREMONY-N-R.htm` page lists the podiums awarded. Every ceremony awarded the finals held since the previous ceremony:

| Ceremony | Podiums | Medals | Finals awarded |
|---|---|---|---|
| 1 (Mon PM, mid-session) | 8 | 18 | 35-49 Women Points (3 categories), 50+ Women Points (5 categories) |
| 2 (Mon PM, end) | 7 | 21 | 40-44 Men Sprint, 55-64 Men TP, 65-74 Men TS, 4 pursuits |
| 3 (Tue PM, mid-session) | 4 | 12 | 35-39 Men Sprint, 35-39 Men Points, 2 scratch races |
| 4 (Tue PM, end) | 10 | 29 | 40-44 Men Points (moved from ceremony 3), 2 TP, 7 pursuits |
| 5 (Wed PM, mid-session) | 11 | 31 | 2 sprints, 8 time trials, 75+ Men TS |
| 6 (Wed PM, end) | 6 | 18 | 2 points races, 2 scratch races, 2 pursuits |

The forecast rules that follow from this:

- A sprint Final is awarded after its last scheduled ride (some Finals list only Rides 1–2).
- Team events are one podium whatever their age range (55-64 Men TP).
- Combined-age bunch races are one podium per category entered. Their start lists carry a Category column (`W5054` … `W7074`); before the start list is posted, the Rider List gives the categories entered with that event code. With neither, the forecast counts the five-year bands in the name (one for an open band such as `50+`).
- Rounds (`1/2 Final`, `1/4 Final`, `1/8 Final`) and placement finals (`40-44 Men Sprint 5-8 Final` at 25022, `Keirin 7-12 Final`) award nothing. A range starting at 1 (`Keirin 1-6 Final`) is a medal final.

The only miss is ceremony 3: the 40-44 Men Points Race result was regenerated about an hour later (presumably a protest) and its podium moved to ceremony 4.

Ceremonies are forecast only when every final in their window has a masters age band (`NN-NN Men`, `NN+ Women`). Other competitions name finals differently (`U17 Women Pursuit Final`, French omnium parts) and keep the flat default.

## How long they took

There is no timestamp for a ceremony's end. The ceremony page is generated seconds after the previous result (16:24:36 → 16:24:42 for ceremony 1), so it marks the start. The end comes from the next result timestamp minus the races run in between: a bunch race's Finish Time plus ~7.7 min overhead (median of back-to-back bunch races, range 3.7–8.6), or measured per-heat times for other events. The predictor therefore never uses the Generated gap before a ceremony, nor the one after it, which includes the ceremony and would count it twice.

| Ceremony | Podiums | Start | Next anchor | Duration |
|---|---|---|---|---|
| 1 | 8 | 16:24:42 | 55-64 Men TP Final audit 17:31:36, after 4 sprint matches and 2 TP heats | ~44–49 min |
| 3 | 4 | ~19:21 (60-64 Men Scratch result) | 65-74 Men TP Final audit 19:57:12, after 2 heats | ~22–24 min |
| 5 | 11 | 17:29:47 | 45-49 Men Points result 18:47:53, 26.2 min race + ~7.7 overhead | ~43–48 min |

Ceremonies 2, 4 and 6 end their sessions, so nothing brackets them; they don't delay anything that day either.

Podiums explain duration better than medals (ceremony 1 had fewer medals than 5 but took as long). A least-squares fit to (4, 23), (8, 46), (11, 45) gives 13 + 3.3 × podiums: 26, 39 and 49 min, within ~8 min of each measurement. The flat 20 min was 3–26 min short. Three samples is thin; refit when more ceremonies are bracketed.
