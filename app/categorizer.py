"""Compositional event name categorizer.

Extracts structured dimensions from track cycling event names by
stripping matched components in a fixed order:
  1. Special events (Break, End of Session, Medal Ceremonies, Pause)
  2. Omnium part (/ Omni I through / Omni VII+)
  3. Ride number (Ride N)
  4. Round (most specific first)
  5. Classification (compound first, then singles)
  6. Gender (English + French)
  7. Discipline (bilingual keyword table)

After extraction, a post-extraction mapping step resolves distance-variant
discipline keys using (discipline, classification, gender).
"""

from __future__ import annotations

import re

from app.models import EventCategory


# --------------------------------------------------------------------------
# 1. Special events
# --------------------------------------------------------------------------

_SPECIAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^pause\b", re.IGNORECASE), "break_"),
    (re.compile(r"^break$", re.IGNORECASE), "break_"),
    (re.compile(r"^end of session$", re.IGNORECASE), "end_of_session"),
    (re.compile(r"^medal ceremon", re.IGNORECASE), "ceremony"),
    (re.compile(r"\bwarm-?up\b", re.IGNORECASE), "break_"),
]

# --------------------------------------------------------------------------
# 2. Omnium part: "/ Omni III" or "/ Omni 3"
# --------------------------------------------------------------------------

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
           "viii": 8, "ix": 9, "x": 10}

_OMNIUM_RE = re.compile(
    r"/\s*omni\s+([ivx]+|\d+)", re.IGNORECASE,
)

# --------------------------------------------------------------------------
# 3. Ride number: "Ride 1", "Ride 2"
# --------------------------------------------------------------------------

_RIDE_RE = re.compile(r"\bride\s+(\d+)\b", re.IGNORECASE)

# --------------------------------------------------------------------------
# 4. Round patterns — most specific first
# --------------------------------------------------------------------------

_ROUND_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b1/16\s+final\s+repechage\b", re.IGNORECASE), "sixteenth_final_repechage"),
    (re.compile(r"\b1/8\s+final\s+repechage\b", re.IGNORECASE), "eighth_final_repechage"),
    (re.compile(r"\b1/16\s+final\b", re.IGNORECASE), "sixteenth_final"),
    (re.compile(r"\b1/8\s+final\b", re.IGNORECASE), "eighth_final"),
    (re.compile(r"\b1/4\s+final\b", re.IGNORECASE), "quarter_final"),
    (re.compile(r"\b1/2\s+final\b", re.IGNORECASE), "semi_final"),
    (re.compile(r"\bqualifier\s+(\d+)\b", re.IGNORECASE), "qualifier_{0}"),
    (re.compile(r"(?<![Ss]print )\bqualifying\b", re.IGNORECASE), "qualifying"),
    (re.compile(r"\brepechage\b", re.IGNORECASE), "repechage"),
    (re.compile(r"\bround\s+(\d+)\b", re.IGNORECASE), "round_{0}"),
    (re.compile(r"\b1-6\s+final\b", re.IGNORECASE), "final_1_6"),
    (re.compile(r"\b7-12\s+final\b", re.IGNORECASE), "final_7_12"),
    (re.compile(r"\b5-8\s+final\b", re.IGNORECASE), "final_5_8"),
    (re.compile(r"\b9-12\s+final\b", re.IGNORECASE), "final_9_12"),
    (re.compile(r"\bbronze\s+final\b", re.IGNORECASE), "bronze_final"),
    (re.compile(r"\bnon\s+comp\b", re.IGNORECASE), "non_championship"),
    (re.compile(r"\bnon\s+championship\b", re.IGNORECASE), "non_championship"),
    (re.compile(r"\bfinal\b", re.IGNORECASE), "final"),
]

# --------------------------------------------------------------------------
# 5. Classification patterns — compound first, then singles
# --------------------------------------------------------------------------

_CLASSIFICATION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Compound groups
    (re.compile(r"\bjunior/maitre/elite\b", re.IGNORECASE), "junior_master_elite"),
    (re.compile(r"\bjunior/master/elite\b", re.IGNORECASE), "junior_master_elite"),
    (re.compile(r"\belite/junior\b", re.IGNORECASE), "elite_junior"),
    (re.compile(r"\bjunior/elite\b", re.IGNORECASE), "elite_junior"),
    (re.compile(r"\bu17/u15\b", re.IGNORECASE), "u15_u17"),
    (re.compile(r"\bu15/u17\b", re.IGNORECASE), "u15_u17"),
    (re.compile(r"\bu11\s*&\s*u13\b", re.IGNORECASE), "u11_u13"),
    (re.compile(r"\bu11/u13\b", re.IGNORECASE), "u11_u13"),
    (re.compile(r"\bu13/u11\b", re.IGNORECASE), "u11_u13"),
    (re.compile(r"\bmaitre\s+a[/-]b\b", re.IGNORECASE), "master_ab"),
    (re.compile(r"\bmaster\s+a/b\b", re.IGNORECASE), "master_ab"),
    (re.compile(r"\bmaitre\s+c[/-]d\b", re.IGNORECASE), "master_cd"),
    (re.compile(r"\bmaster\s+c/d\b", re.IGNORECASE), "master_cd"),
    # Para classifications (compound first)
    (re.compile(r"\bpara\s+c1-5\b", re.IGNORECASE), "para_c1_5"),
    (re.compile(r"\bpara\s+c5\b", re.IGNORECASE), "para_c5"),
    (re.compile(r"\bpara\s+c4\b", re.IGNORECASE), "para_c4"),
    (re.compile(r"\bpara\s+c3\b", re.IGNORECASE), "para_c3"),
    (re.compile(r"\bpara\s+c2\b", re.IGNORECASE), "para_c2"),
    (re.compile(r"\bpara\s+b\b", re.IGNORECASE), "para_b"),
    # Age bracket ranges: "55-64", "35+", etc.
    (re.compile(r"\b(\d{2,3})-(\d{2,3})\b"), "_age_bracket_range"),
    (re.compile(r"\b(\d{2,3})\+"), "_age_bracket_plus"),
    # License categories (Cat A-G, Cat 1-6, Cat 3A/3B)
    (re.compile(r"\bcat\s+3a/3b\b", re.IGNORECASE), "cat_3a_3b"),
    (re.compile(r"\bcat\s+3a\b", re.IGNORECASE), "cat_3a"),
    (re.compile(r"\bcat\s+3b\b", re.IGNORECASE), "cat_3b"),
    (re.compile(r"\bcat\s+([a-g])\b", re.IGNORECASE), "_cat_letter"),
    (re.compile(r"\bcat\s+(\d)\b", re.IGNORECASE), "_cat_number"),
    # Singles — most specific first
    (re.compile(r"\belite\b", re.IGNORECASE), "elite"),
    (re.compile(r"\bsenior\b", re.IGNORECASE), "senior"),
    (re.compile(r"\bjunior\b", re.IGNORECASE), "junior"),
    (re.compile(r"\bu17\b", re.IGNORECASE), "u17"),
    (re.compile(r"\bu15\b", re.IGNORECASE), "u15"),
    (re.compile(r"\bu13\b", re.IGNORECASE), "u13"),
    (re.compile(r"\bu11\b", re.IGNORECASE), "u11"),
    (re.compile(r"\bmaitre\s+([a-e])\b", re.IGNORECASE), "_master_letter"),
    (re.compile(r"\bmaster\s+([a-e])\b", re.IGNORECASE), "_master_letter"),
    (re.compile(r"\bmaitre\b", re.IGNORECASE), "master"),
    (re.compile(r"\bmaster\b", re.IGNORECASE), "master"),
    (re.compile(r"\bminime\b", re.IGNORECASE), "u15"),
    (re.compile(r"\bcadet\b", re.IGNORECASE), "u17"),
]

# --------------------------------------------------------------------------
# 6. Gender patterns
# --------------------------------------------------------------------------

_GENDER_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bwomen\b", re.IGNORECASE), "women"),
    (re.compile(r"\bfemmes\b", re.IGNORECASE), "women"),
    (re.compile(r"\bdames\b", re.IGNORECASE), "women"),
    (re.compile(r"\bhommes\b", re.IGNORECASE), "men"),
    (re.compile(r"\bmen\b", re.IGNORECASE), "men"),
    (re.compile(r"\bmixed\b", re.IGNORECASE), "open"),
    (re.compile(r"\bco-ed\b", re.IGNORECASE), "open"),
    # French single-letter abbreviations — only match as standalone word
    (re.compile(r"\bF\b"), "women"),
    (re.compile(r"\bH\b"), "men"),
]

# --------------------------------------------------------------------------
# 7. Discipline keyword table — bilingual, most specific first
# --------------------------------------------------------------------------

_DISCIPLINE_KEYWORDS: list[tuple[re.Pattern, str]] = [
    # Team events first
    (re.compile(r"poursuite\s+par\s+[eé]quipe", re.IGNORECASE), "team_pursuit"),
    (re.compile(r"\bteam\s+pursuit\b", re.IGNORECASE), "team_pursuit"),
    (re.compile(r"\bteam\s+sprint\b", re.IGNORECASE), "team_sprint"),
    # Mass start races
    (re.compile(r"\bmadison\b", re.IGNORECASE), "madison"),
    (re.compile(r"\bam[eé]ricaine\b", re.IGNORECASE), "madison"),
    (re.compile(r"\bmiss\s+and\s+out\b", re.IGNORECASE), "elimination_race"),
    (re.compile(r"\bsuper\s+sprint\s+elimination\b", re.IGNORECASE), "elimination_race"),
    (re.compile(r"\bcourse\s+[àa]\s+l['\u2019]?[eé]limination\b", re.IGNORECASE), "elimination_race"),
    (re.compile(r"\belimination\s+race\b", re.IGNORECASE), "elimination_race"),
    (re.compile(r"\bamerican\s+tempo\b", re.IGNORECASE), "tempo_race"),
    (re.compile(r"\bpoint\s+a\s+lap\b", re.IGNORECASE), "tempo_race"),
    (re.compile(r"\bcourse\s+tempo\b", re.IGNORECASE), "tempo_race"),
    (re.compile(r"\btempo\s+race\b", re.IGNORECASE), "tempo_race"),
    (re.compile(r"\bcourse\s+aux\s+points\b", re.IGNORECASE), "points_race"),
    (re.compile(r"\bpoints\s+race\b", re.IGNORECASE), "points_race"),
    (re.compile(r"\bcourse\s+scratch\b", re.IGNORECASE), "scratch_race"),
    (re.compile(r"\bscratch\s+race\b", re.IGNORECASE), "scratch_race"),
    (re.compile(r"\bkeirin\b", re.IGNORECASE), "keirin"),
    # Time trials — distance-specific first
    (re.compile(r"\b500m\s+(?:clm|time\s+trial)\b", re.IGNORECASE), "time_trial_500"),
    (re.compile(r"\b750m\s+(?:clm|time\s+trial)\b", re.IGNORECASE), "time_trial_750"),
    (re.compile(r"\bkilo\s+(?:clm|time\s+trial)\b", re.IGNORECASE), "time_trial_kilo"),
    (re.compile(r"\b1000m\s+(?:clm|time\s+trial)\b", re.IGNORECASE), "time_trial_kilo"),
    (re.compile(r"\bessai\s+chronom[eé]tr[eé]\b", re.IGNORECASE), "time_trial_generic"),
    (re.compile(r"\bclm\b", re.IGNORECASE), "time_trial_generic"),
    (re.compile(r"\btime\s+trial\b", re.IGNORECASE), "time_trial_generic"),
    # Sprint variants
    (re.compile(r"\bflying\s+200m\b", re.IGNORECASE), "sprint_qualifying"),
    (re.compile(r"\bflying\s+mile\b", re.IGNORECASE), "sprint_qualifying"),
    (re.compile(r"\bsprint\s+qualifying\b", re.IGNORECASE), "sprint_qualifying"),
    (re.compile(r"\bsprint\b", re.IGNORECASE), "sprint_match"),
    (re.compile(r"\b200m\b", re.IGNORECASE), "sprint_qualifying"),
    (re.compile(r"\bvitesse\b", re.IGNORECASE), "_vitesse"),
    # Pursuit (generic — distance resolved in post-extraction)
    (re.compile(r"\bpoursuite\b", re.IGNORECASE), "pursuit"),
    (re.compile(r"\bindividual\s+pursuit\b", re.IGNORECASE), "pursuit"),
    (re.compile(r"\bpursuit\b", re.IGNORECASE), "pursuit"),
    # Exhibition / novelty
    (re.compile(r"\bchariot\s+race\b", re.IGNORECASE), "exhibition"),
    (re.compile(r"\bwheel\s+race\b", re.IGNORECASE), "exhibition"),
    (re.compile(r"\bkids\s+race\b", re.IGNORECASE), "exhibition"),
    (re.compile(r"\blongest\s+lap\b", re.IGNORECASE), "exhibition"),
    # Omnium as discipline (when only "Omnium Qualifier 1" with no sub-discipline)
    (re.compile(r"\bomnium\b", re.IGNORECASE), "unknown"),
]

# --------------------------------------------------------------------------
# Distance-variant discipline resolution
# --------------------------------------------------------------------------

# Pursuit distance: (classification, gender) -> discipline key
# Elite/Senior men & women = 4k
# Junior men, Master A/B men = 3k
# All women (junior/U17/U15/master), U17 and younger, Master C+ men = 2k
# Fallback = 3k

_PURSUIT_4K_CLASSES = {"elite", "senior"}
_PURSUIT_3K_CLASSES = {"junior", "master_a", "master_b", "master_ab"}
_PURSUIT_2K_CLASSES = {"master_c", "master_d", "master_e", "master_cd", "master",
                       "u17", "u15", "u13", "u11", "u15_u17", "u11_u13",
                       "para_b", "para_c2", "para_c3", "para_c4", "para_c5", "para_c1_5"}
_PURSUIT_2K_COMPOUND_CLASSES = {"elite_junior", "junior_master_elite"}


def _resolve_pursuit_distance(classification: str | None, gender: str) -> str:
    if classification in _PURSUIT_4K_CLASSES:
        return "pursuit_4k"
    if classification in _PURSUIT_3K_CLASSES and gender == "men":
        return "pursuit_3k"
    if classification in _PURSUIT_2K_CLASSES:
        if gender == "women":
            return "pursuit_2k"
        if gender == "men":
            # Master C+ men = 2k, youth = 2k
            if classification and (classification.startswith("master") or
                                    classification.startswith("u") or
                                    classification.startswith("para")):
                return "pursuit_2k"
        # open/unknown gender for youth categories
        if classification and (classification.startswith("u") or
                               classification.startswith("para")):
            return "pursuit_2k"
        return "pursuit_2k"
    if classification in _PURSUIT_2K_COMPOUND_CLASSES:
        # Compound groups: use gender to disambiguate
        if gender == "women":
            return "pursuit_2k"
        return "pursuit_3k"
    # Age bracket classifications
    if classification and classification.startswith("age_"):
        if gender == "women":
            return "pursuit_2k"
        return "pursuit_3k"
    # No classification
    if gender == "women":
        return "pursuit_2k"
    if gender == "men":
        return "pursuit_4k"
    return "pursuit_3k"


def _strip(text: str, match: re.Match) -> str:
    """Remove a match from text and collapse whitespace."""
    return (text[:match.start()] + " " + text[match.end():]).strip()


def _clean(text: str) -> str:
    """Collapse multiple spaces and strip."""
    return re.sub(r"\s+", " ", text).strip()


def categorize_event(event_name: str) -> tuple[EventCategory, str]:
    """Categorize a track cycling event name into structured dimensions.

    Returns (EventCategory, unresolved_text) where unresolved_text is any
    text remaining after all extraction steps.
    """
    text = _clean(event_name)
    discipline: str | None = None
    classification: str | None = None
    gender: str = "open"
    round_: str | None = None
    ride_number: int | None = None
    omnium_part: int | None = None
    is_special = False

    # Step 1: Special events
    for pattern, disc_key in _SPECIAL_PATTERNS:
        if pattern.search(text):
            return EventCategory(
                discipline=disc_key,
                gender="open",
            ), ""

    # Step 2: Omnium part
    m = _OMNIUM_RE.search(text)
    if m:
        val = m.group(1).lower()
        omnium_part = _ROMAN.get(val) or int(val)
        text = _strip(text, m)

    # Step 3: Ride number
    m = _RIDE_RE.search(text)
    if m:
        ride_number = int(m.group(1))
        text = _strip(text, m)

    # Step 4: Round
    for pattern, key_template in _ROUND_PATTERNS:
        m = pattern.search(text)
        if m:
            if "{0}" in key_template:
                round_ = key_template.format(m.group(1))
            else:
                round_ = key_template
            text = _strip(text, m)
            break

    # Step 5: Classification
    for pattern, key in _CLASSIFICATION_PATTERNS:
        m = pattern.search(text)
        if m:
            if key == "_age_bracket_range":
                lo, hi = m.group(1), m.group(2)
                classification = f"age_{lo}_{hi}"
            elif key == "_age_bracket_plus":
                classification = f"age_{m.group(1)}_plus"
            elif key == "_cat_letter":
                classification = f"cat_{m.group(1).lower()}"
            elif key == "_cat_number":
                classification = f"cat_{m.group(1)}"
            elif key == "_master_letter":
                classification = f"master_{m.group(1).lower()}"
            else:
                classification = key
            text = _strip(text, m)
            break

    # Step 6: Gender
    for pattern, gen_key in _GENDER_PATTERNS:
        m = pattern.search(text)
        if m:
            gender = gen_key
            text = _strip(text, m)
            break

    # Handle "Open" as classification (e.g., "Open F Madison")
    m_open = re.search(r"\bopen\b", text, re.IGNORECASE)
    if m_open and classification is None:
        classification = "open"
        text = _strip(text, m_open)

    # Handle "Co-Ed" setting classification to open if not already set
    m_coed = re.search(r"\bco-ed\b", text, re.IGNORECASE)
    if m_coed and classification is None:
        classification = "open"
        text = _strip(text, m_coed)

    # Handle "Exhibition" as a prefix modifier
    m_exhibition = re.search(r"\bexhibition\b", text, re.IGNORECASE)
    if m_exhibition:
        text = _strip(text, m_exhibition)

    # Step 7: Discipline
    text = _clean(text)
    for pattern, disc_key in _DISCIPLINE_KEYWORDS:
        m = pattern.search(text)
        if m:
            if disc_key == "_vitesse":
                # French "Vitesse": qualifying if round is qualifying, else sprint_match
                discipline = "sprint_qualifying" if round_ == "qualifying" else "sprint_match"
            else:
                discipline = disc_key
            text = _strip(text, m)
            break

    if discipline is None:
        discipline = "unknown"

    # Post-extraction: set round=qualifying for sprint_qualifying when round not
    # already set and the discipline was matched via "sprint qualifying" keyword
    if discipline == "sprint_qualifying" and round_ is None:
        if re.search(r"\bsprint\s+qualifying\b", event_name, re.IGNORECASE):
            round_ = "qualifying"

    # Post-extraction: if "Qualifying" remains in residual and round is still None,
    # extract it now (handles "Team Sprint Qualifying" where the lookbehind prevented
    # round extraction in step 4)
    if round_ is None:
        m_q = re.search(r"\bqualifying\b", text, re.IGNORECASE)
        if m_q:
            round_ = "qualifying"
            text = _strip(text, m_q)

    # Post-extraction: strip "Open" from residual when already handled as classification
    m_open_resid = re.search(r"\bopen\b", text, re.IGNORECASE)
    if m_open_resid:
        text = _strip(text, m_open_resid)

    # Post-extraction: resolve distance-variant discipline keys
    if discipline == "pursuit":
        discipline = _resolve_pursuit_distance(classification, gender)

    # Clean up residual
    text = _clean(text)
    # Remove stray punctuation left over
    text = re.sub(r"^[\s\-/]+|[\s\-/]+$", "", text).strip()

    return EventCategory(
        discipline=discipline,
        classification=classification,
        gender=gender,
        round=round_,
        ride_number=ride_number,
        omnium_part=omnium_part,
    ), text
