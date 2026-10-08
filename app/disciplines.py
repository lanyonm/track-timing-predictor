import logging
import re

logger = logging.getLogger(__name__)

# Discipline detection and default duration estimates for track cycling events.
# Durations are in minutes and include the event itself plus changeover time.

# Keyword matching: most specific phrases must come before less specific ones.
DISCIPLINE_KEYWORDS: list[tuple[str, str]] = [
    ("poursuite par équipe", "team_pursuit"),  # French: team pursuit
    ("team pursuit", "team_pursuit"),
    ("team sprint", "team_sprint"),
    ("madison", "madison"),
    ("flying mile", "scratch_race"),
    ("scratch race", "scratch_race"),
    ("omnium qualifier", "points_race"),
    ("course aux points", "points_race"),  # French: points race
    ("points race", "points_race"),
    ("miss and out", "elimination_race"),
    ("elimination race", "elimination_race"),
    ("american tempo", "tempo_race"),
    ("point a lap", "tempo_race"),
    ("course tempo", "tempo_race"),  # French: tempo race
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
    ("women individual pursuit", "pursuit_2k"),  # junior women, U17/U15 women, master women
    ("women pursuit", "pursuit_2k"),
    ("2000m individual pursuit", "pursuit_2k"),
    ("2000m pursuit", "pursuit_2k"),
    ("individual pursuit", "pursuit_3k"),  # men (U17/U15/unmatched masters) fallback
    ("pursuit", "pursuit_3k"),  # generic fallback
    ("poursuite", "pursuit_3k"),  # French: generic pursuit fallback
    ("500m time trial", "time_trial_500"),
    ("500m clm", "time_trial_500"),  # French: 500m contre-la-montre
    ("750m time trial", "time_trial_750"),
    ("750m clm", "time_trial_750"),  # French: 750m contre-la-montre
    ("kilo time trial", "time_trial_kilo"),
    ("kilo clm", "time_trial_kilo"),  # French: kilo contre-la-montre
    ("1000m time trial", "time_trial_kilo"),
    ("1000m clm", "time_trial_kilo"),  # French: 1000m contre-la-montre
    ("time trial", "time_trial_generic"),
    ("clm", "time_trial_generic"),  # French: generic contre-la-montre
    ("flying 200m", "sprint_qualifying"),
    ("sprint qualifying", "sprint_qualifying"),
    ("vitesse qualifying", "sprint_qualifying"),  # French: sprint qualifying
    ("sprint", "sprint_match"),
    ("vitesse", "sprint_match"),  # French: sprint match
    ("200m", "sprint_qualifying"),  # bare 200m = sprint qualifying
    ("medal ceremonies", "ceremony"),
    ("medal ceremony", "ceremony"),
    ("pause", "break_"),  # French: break
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
    "pursuit_4k": 15.0,  # 2 heats × 7.5 min per heat
    "pursuit_3k": 11.0,  # 2 heats × 5.5 min per heat
    "pursuit_2k": 9.0,  # 2 heats × 4.5 min per heat
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
    "time_trial_500": 20.0,  # 500m: 2:20/rider
    "time_trial_750": 22.0,  # 750m: 2:40/rider
    "time_trial_kilo": 22.0,  # 1000m: ~7 riders × 3:00
    "time_trial_generic": 20.0,
    # Non-race:
    "ceremony": 20.0,
    "break_": 10.0,
    "end_of_session": 0.0,
    "unknown": 10.0,
}

# Medal ceremony length from the podiums it awards (app/ceremonies.py forecasts the count).
# Least-squares fit to the three mid-session ceremonies at EventId 26037 that a following
# result timestamp brackets: 4, 8 and 11 podiums took ~23, ~46 and ~45 min
# (docs/medal-ceremony-durations.md). The flat "ceremony" default stays for unforecast ones.
CEREMONY_BASE_MINUTES = 13.0
CEREMONY_PER_PODIUM_MINUTES = 3.3

SPECIAL_EVENT_NAMES = {"break", "pause", "end of session", "medal ceremonies", "medal ceremony"}

# Per-heat durations in minutes for use when heat count is known from a start list.
# One "heat" = one sequential time slot (e.g. one keirin race, one pursuit pair,
# one sprint qualifier ride). Does NOT include the inter-event changeover; that is
# added separately via get_changeover().
PER_HEAT_DURATIONS: dict[str, float] = {
    # Sprint qualifying: one 200m TT ride per heat (~1:15 ride + ~15 s gap)
    "sprint_qualifying": 1.25,
    # Sprint match: one 2-rider match per heat (~3:00 + recovery)
    "sprint_match": 3.0,
    # Timed events below are rounded medians of Generated-timestamp gap / heat count across
    # 25022-26037 (docs/timed-event-durations.md); each includes ~1.5-2.5 min between heats.
    # Individual pursuit: 2 riders race simultaneously per heat
    "pursuit_4k": 7.5,
    "pursuit_3k": 5.5,
    "pursuit_2k": 4.5,
    # Team pursuit: 2 teams race simultaneously per heat
    "team_pursuit": 6.75,
    # Team sprint: 2 teams per heat
    "team_sprint": 3.0,
    # Mass start races are almost always 1 heat; per-heat ≈ full race duration
    "scratch_race": 12.0,
    # Assumes 60 laps on a 250m track at ~50 km/h average speed
    "points_race": 20.0,
    # Assumes ~15 riders on a 250m track at ~50 km/h average speed
    "elimination_race": 8.0,
    # Assumes 30 laps on a 250m track at ~50 km/h average speed
    "tempo_race": 10.0,
    # Madison: typical 15-20km race ~20-27 min + 2 min changeover
    "madison": 24.0,
    # Keirin: one heat of ~6 riders (~4:30 race + recovery between heats)
    "keirin": 4.5,
    # Time trials: one rider per heat; per-rider time + small gap between starts
    "time_trial_500": 2.33,  # 500m: ~2:20/rider
    "time_trial_750": 2.67,  # 750m: ~2:40/rider
    "time_trial_kilo": 3.0,  # 1000m: measured ~3:05/rider
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
    logger.warning("Unrecognized discipline, falling back to default duration", extra={"event_name": event_name})
    return "unknown"


# Individual pursuit page URLs encode the distance ridden, e.g. W4044-IP-3000-Q-0-R.htm.
# Team pursuit uses -TP-, so it never matches.
_PURSUIT_URL_DISTANCE = re.compile(r"-IP-(2000|3000|4000)-")
_PURSUIT_BY_METRES = {"2000": "pursuit_2k", "3000": "pursuit_3k", "4000": "pursuit_4k"}


def pursuit_discipline_from_urls(*urls: str | None) -> str | None:
    """Return the pursuit distance key from the first event URL that encodes one.

    Event names often omit the distance (e.g. masters "40-44 Women Pursuit"), and
    the name-based keywords guess wrong for many categories, so the URL wins
    whenever the event has any link.
    """
    for url in urls:
        if url and (m := _PURSUIT_URL_DISTANCE.search(url)):
            return _PURSUIT_BY_METRES[m.group(1)]
    return None


def get_default_duration(discipline: str) -> float:
    return DEFAULT_DURATIONS.get(discipline, DEFAULT_DURATIONS["unknown"])


def get_per_heat_duration(discipline: str) -> float:
    return PER_HEAT_DURATIONS.get(discipline, DEFAULT_DURATIONS.get(discipline, DEFAULT_DURATIONS["unknown"]))
