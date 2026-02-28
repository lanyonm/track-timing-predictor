# Discipline detection and default duration estimates for track cycling events.
# Durations are in minutes and include the event itself plus changeover time.

# Keyword matching: most specific phrases must come before less specific ones.
DISCIPLINE_KEYWORDS: list[tuple[str, str]] = [
    ("team pursuit", "team_pursuit"),
    ("team sprint", "team_sprint"),
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
DEFAULT_DURATIONS: dict[str, float] = {
    # One category's sprint qualifying (200m TT): ~6-10 riders × 45s + changeovers
    "sprint_qualifying": 8.0,
    # One round of sprint matches (e.g. 1/8 final = 4 pairs × ~3 min): ~12-15 min
    "sprint_match": 12.0,
    # Pursuit qualifying or final for one category: 2 simultaneous rides × 5 min
    "pursuit": 8.0,
    # Team pursuit: qualifying or final, ~2 rides: 10 min
    "team_pursuit": 10.0,
    # Team sprint: qualifying + final: 10 min
    "team_sprint": 10.0,
    # Bunch races (one category):
    "scratch_race": 12.0,
    "points_race": 20.0,
    "elimination_race": 15.0,
    "tempo_race": 15.0,
    # Keirin: heat or final including possible restart
    "keirin": 10.0,
    # Time trials (one category, sequential starts):
    "time_trial_500": 12.0,
    "time_trial_750": 15.0,
    "time_trial_kilo": 18.0,
    "time_trial_generic": 15.0,
    # Non-race:
    "ceremony": 5.0,
    "break_": 15.0,
    "end_of_session": 0.0,
    "unknown": 10.0,
}

SPECIAL_EVENT_NAMES = {"break", "end of session", "medal ceremonies", "medal ceremony"}


def detect_discipline(event_name: str) -> str:
    """Return a normalized discipline key for the given event name."""
    lower = event_name.lower()
    for keyword, key in DISCIPLINE_KEYWORDS:
        if keyword in lower:
            return key
    return "unknown"


def get_default_duration(discipline: str) -> float:
    return DEFAULT_DURATIONS.get(discipline, DEFAULT_DURATIONS["unknown"])
