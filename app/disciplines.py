# Discipline detection and default duration estimates for track cycling events.
# Durations are in minutes and include the event itself plus changeover time.

# Keyword matching: most specific phrases must come before less specific ones.
DISCIPLINE_KEYWORDS: list[tuple[str, str]] = [
    ("team pursuit", "team_pursuit"),
    ("team sprint", "team_sprint"),
    ("madison", "madison"),
    ("scratch race", "scratch_race"),
    ("points race", "points_race"),
    ("elimination race", "elimination_race"),
    ("tempo race", "tempo_race"),
    ("keirin", "keirin"),
    # Pursuit: distance varies by category; detect most-specific first.
    # Elite men & women = 4000m; junior men & masters A/B men = 3000m;
    # junior women, U17/U15, masters C/D/E+ men, master women = 2000m.
    ("elite men individual pursuit", "pursuit_4k"),
    ("elite men pursuit", "pursuit_4k"),
    ("elite women individual pursuit", "pursuit_4k"),
    ("elite women pursuit", "pursuit_4k"),
    ("4000m individual pursuit", "pursuit_4k"),
    ("4000m pursuit", "pursuit_4k"),
    ("junior men individual pursuit", "pursuit_3k"),
    ("junior men pursuit", "pursuit_3k"),
    ("master a men individual pursuit", "pursuit_3k"),
    ("master a men pursuit", "pursuit_3k"),
    ("master b men individual pursuit", "pursuit_3k"),
    ("master b men pursuit", "pursuit_3k"),
    ("3000m individual pursuit", "pursuit_3k"),
    ("3000m pursuit", "pursuit_3k"),
    ("master c men individual pursuit", "pursuit_2k"),
    ("master c men pursuit", "pursuit_2k"),
    ("master d men individual pursuit", "pursuit_2k"),
    ("master d men pursuit", "pursuit_2k"),
    ("master e men individual pursuit", "pursuit_2k"),
    ("master e men pursuit", "pursuit_2k"),
    ("women individual pursuit", "pursuit_2k"),   # junior women, U17/U15 women, master women
    ("women pursuit", "pursuit_2k"),
    ("2000m individual pursuit", "pursuit_2k"),
    ("2000m pursuit", "pursuit_2k"),
    ("individual pursuit", "pursuit_3k"),         # men (U17/U15/unmatched masters) fallback
    ("pursuit", "pursuit_3k"),                    # generic fallback
    ("500m time trial", "time_trial_500"),
    ("750m time trial", "time_trial_750"),
    ("kilo time trial", "time_trial_kilo"),
    ("1000m time trial", "time_trial_kilo"),
    ("time trial", "time_trial_generic"),
    ("sprint qualifying", "sprint_qualifying"),
    ("sprint", "sprint_match"),
    ("medal ceremonies", "ceremony"),
    ("medal ceremony", "ceremony"),
    ("break", "break_"),
    ("end of session", "end_of_session"),
]

# Default durations in minutes per event row in the schedule.
# These cover the full event including all heats/rides and changeover time
# for that category (e.g., "Elite Men Sprint Qualifying" = all qualifying
# rides for that category, not a single ride).
#
# Reference race times used for calibration (from event schedule sheet):
#   200m TT: 1:15/rider  sprint match: 3:00  keirin: 4:30
#   500m TT: 2:20/rider  750m TT: 2:40/rider  1000m TT: 2:30/rider
#   break: 10:00  medals: 20:00  team sprint: 2:40/ride
#   madison 15k W: 25:30  madison 15k M: 21:00
DEFAULT_DURATIONS: dict[str, float] = {
    # Sprint qualifying (200m TT): ~8 riders × 1:15 + changeovers ≈ 10 min
    "sprint_qualifying": 10.0,
    # One round of sprint matches (e.g. 1/8 final = 4 pairs × ~3 min): ~12 min
    "sprint_match": 12.0,
    # Pursuit: one schedule slot (qualifying or final) for one category.
    # Two riders race simultaneously per heat; times below cover ~2-heat finals.
    "pursuit_4k": 12.0,   # elite men 4km: ~4:15/ride × 2 heats + gaps
    "pursuit_3k": 9.0,    # elite women / junior men 3km: ~3:25/ride × 2 heats + gaps
    "pursuit_2k": 6.0,    # junior/U17 women 2km: ~2:20/ride × 2 heats + gaps
    # Team pursuit: qualifying or final, ~2-3 rides
    "team_pursuit": 10.0,
    # Team sprint: ~4 rides × 2:40/ride per category
    "team_sprint": 10.0,
    # Mass start races; race time varies by distance, +2 min changeover:
    "scratch_race": 12.0,
    # Assumes 60 laps on a 250m track at ~50 km/h average speed
    "points_race": 20.0,
    # Assumes ~15 riders on a 250m track at ~50 km/h average speed
    "elimination_race": 8.0,
    # Assumes 30 laps on a 250m track at ~50 km/h average speed
    "tempo_race": 10.0,
    # Madison: typical 15-20km race ~20-27 min + 2 min changeover
    "madison": 22.0,
    # Keirin: 4:30 race + 2:00 changeover
    "keirin": 6.5,
    # Time trials (one category, sequential starts): per-rider time × ~8 riders
    "time_trial_500": 20.0,   # 500m: 2:20/rider
    "time_trial_750": 22.0,   # 750m: 2:40/rider
    "time_trial_kilo": 22.0,  # 1000m: 2:30/rider
    "time_trial_generic": 20.0,
    # Non-race:
    "ceremony": 20.0,
    "break_": 10.0,
    "end_of_session": 0.0,
    "unknown": 10.0,
}

SPECIAL_EVENT_NAMES = {"break", "end of session", "medal ceremonies", "medal ceremony"}

# Per-heat durations in minutes for use when heat count is known from a start list.
# One "heat" = one sequential time slot (e.g. one keirin race, one pursuit pair,
# one sprint qualifier ride). Does NOT include the inter-event changeover; that is
# added separately via get_changeover().
PER_HEAT_DURATIONS: dict[str, float] = {
    # Sprint qualifying: one 200m TT ride per heat (~1:15 ride + ~15 s gap)
    "sprint_qualifying": 1.5,
    # Sprint match: one 2-rider match per heat (~3:00 + recovery)
    "sprint_match": 3.5,
    # Individual pursuit: 2 riders race simultaneously per heat
    "pursuit_4k": 5.0,    # elite men 4km: ~4:15/ride per heat
    "pursuit_3k": 4.0,    # elite women / junior men 3km: ~3:25/ride per heat
    "pursuit_2k": 3.0,    # junior/U17 women 2km: ~2:20/ride per heat
    # Team pursuit: 2 teams race simultaneously per heat (~4:30-5 min/ride)
    "team_pursuit": 5.0,
    # Team sprint: 2 teams per heat (~2:40 ride + setup)
    "team_sprint": 3.5,
    # Mass start races are almost always 1 heat; per-heat ≈ full race duration
    "scratch_race": 12.0,
    # Assumes 60 laps on a 250m track at ~50 km/h average speed
    "points_race": 20.0,
    # Assumes ~15 riders on a 250m track at ~50 km/h average speed
    "elimination_race": 8.0,
    # Assumes 30 laps on a 250m track at ~50 km/h average speed
    "tempo_race": 10.0,
    # Madison: typical 15-20km race ~20-27 min + 2 min changeover
    "madison": 22.0,
    # Keirin: one heat of ~6 riders (~4:30 race + recovery between heats)
    "keirin": 5.0,
    # Time trials: one rider per heat; per-rider time + small gap between starts
    "time_trial_500": 2.5,    # 500m: ~2:20/rider
    "time_trial_750": 3.0,    # 750m: ~2:40/rider
    "time_trial_kilo": 3.0,   # 1000m: ~2:30/rider
    "time_trial_generic": 3.0,
}

# Minutes to add to a result-page Finish Time to account for changeover between events.
# Only applicable to disciplines where "Finish Time" appears in result pages (mass start races).
CHANGEOVER_MINUTES: dict[str, float] = {
    "scratch_race": 2.0,
    "points_race": 2.0,
    "elimination_race": 2.0,
    "tempo_race": 2.0,
    "madison": 2.0,
    "keirin": 2.0,
}


def get_changeover(discipline: str) -> float:
    return CHANGEOVER_MINUTES.get(discipline, 0.0)


def detect_discipline(event_name: str) -> str:
    """Return a normalized discipline key for the given event name."""
    lower = event_name.lower()
    for keyword, key in DISCIPLINE_KEYWORDS:
        if keyword in lower:
            return key
    return "unknown"


def get_default_duration(discipline: str) -> float:
    return DEFAULT_DURATIONS.get(discipline, DEFAULT_DURATIONS["unknown"])


def get_per_heat_duration(discipline: str) -> float:
    return PER_HEAT_DURATIONS.get(discipline, DEFAULT_DURATIONS.get(discipline, DEFAULT_DURATIONS["unknown"]))
