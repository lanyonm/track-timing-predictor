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
    ("individual pursuit", "pursuit"),
    ("pursuit", "pursuit"),
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
    # Pursuit qualifying or final for one category: 2 simultaneous rides × ~5-8 min
    "pursuit": 8.0,
    # Team pursuit: qualifying or final, ~2-3 rides
    "team_pursuit": 10.0,
    # Team sprint: ~4 rides × 2:40/ride per category
    "team_sprint": 10.0,
    # Bunch races (one category); race time varies by distance, +2 min changeover:
    "scratch_race": 12.0,
    "points_race": 20.0,
    "elimination_race": 15.0,
    "tempo_race": 20.0,
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

# Minutes to add to a result-page Finish Time to account for changeover between events.
# Only applicable to disciplines where "Finish Time" appears in result pages (bunch races).
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
