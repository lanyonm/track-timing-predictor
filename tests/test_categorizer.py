"""Tests for the compositional event name categorizer."""

import pytest
from app.categorizer import categorize_event


class TestEnglishDisciplines:
    """Test discipline detection for English event names."""

    def test_sprint_qualifying(self):
        cat, _ = categorize_event("U17 Women Sprint Qualifying")
        assert cat.discipline == "sprint_qualifying"

    def test_sprint_match(self):
        cat, _ = categorize_event("Elite Men Sprint 1/2 Final Ride 1")
        assert cat.discipline == "sprint_match"

    def test_team_pursuit(self):
        cat, _ = categorize_event("Elite Men Team Pursuit Final")
        assert cat.discipline == "team_pursuit"

    def test_team_sprint(self):
        cat, _ = categorize_event("Co-Ed Team Sprint Final")
        assert cat.discipline == "team_sprint"

    def test_scratch_race(self):
        cat, _ = categorize_event("Elite/Junior Women Scratch Race  / Omni I")
        assert cat.discipline == "scratch_race"

    def test_points_race(self):
        cat, _ = categorize_event("Elite Men Points Race Final")
        assert cat.discipline == "points_race"

    def test_elimination_race(self):
        cat, _ = categorize_event("Para C1-5 Elimination Race Final")
        assert cat.discipline == "elimination_race"

    def test_tempo_race(self):
        cat, _ = categorize_event("Junior Men Tempo Race Final")
        assert cat.discipline == "tempo_race"

    def test_keirin(self):
        cat, _ = categorize_event("U11 & U13 Keirin 1-6 Final")
        assert cat.discipline == "keirin"

    def test_madison(self):
        cat, _ = categorize_event("Open F Madison Final")
        assert cat.discipline == "madison"

    def test_time_trial_500(self):
        cat, _ = categorize_event("Women 500m Time Trial Final")
        assert cat.discipline == "time_trial_500"

    def test_time_trial_750(self):
        cat, _ = categorize_event("Men 750m Time Trial Final")
        assert cat.discipline == "time_trial_750"

    def test_time_trial_kilo(self):
        cat, _ = categorize_event("Men Kilo Time Trial Final")
        assert cat.discipline == "time_trial_kilo"

    def test_time_trial_1000m(self):
        cat, _ = categorize_event("Men 1000m Time Trial Final")
        assert cat.discipline == "time_trial_kilo"

    def test_flying_200m(self):
        cat, _ = categorize_event("Para C4 Men Flying 200m Final")
        assert cat.discipline == "sprint_qualifying"

    def test_miss_and_out(self):
        cat, _ = categorize_event("Women Miss And Out Final")
        assert cat.discipline == "elimination_race"

    def test_american_tempo(self):
        cat, _ = categorize_event("Men American Tempo Final")
        assert cat.discipline == "tempo_race"

    def test_point_a_lap(self):
        cat, _ = categorize_event("Junior Men Point A Lap Final")
        assert cat.discipline == "tempo_race"

    def test_super_sprint_elimination(self):
        cat, _ = categorize_event("Men Super Sprint Elimination Final")
        assert cat.discipline == "elimination_race"


class TestFrenchDisciplines:
    """Test French discipline names per research.md section 2."""

    def test_vitesse_qualifying(self):
        cat, _ = categorize_event("Junior/Elite F Vitesse Qualifying")
        assert cat.discipline == "sprint_qualifying"

    def test_vitesse_final(self):
        cat, _ = categorize_event("Senior H Vitesse Final Ride 1 / Omni III")
        assert cat.discipline == "sprint_match"

    def test_poursuite_par_equipe(self):
        cat, _ = categorize_event("Elite H Poursuite par équipe Final")
        assert cat.discipline == "team_pursuit"

    def test_poursuite_individual(self):
        cat, _ = categorize_event("Senior H Poursuite Final")
        assert cat.discipline == "pursuit_4k"  # senior men = 4k

    def test_course_aux_points(self):
        cat, _ = categorize_event("U15/U17 H Course Aux Points Final  / Omni V")
        assert cat.discipline == "points_race"

    def test_course_elimination(self):
        cat, _ = categorize_event("Senior F Course à l'élimination Final")
        assert cat.discipline == "elimination_race"

    def test_course_tempo(self):
        cat, _ = categorize_event("Junior F Course Tempo Final")
        assert cat.discipline == "tempo_race"

    def test_course_scratch(self):
        cat, _ = categorize_event("Elite H Course Scratch Final")
        assert cat.discipline == "scratch_race"

    def test_americaine(self):
        cat, _ = categorize_event("Senior Américaine Final")
        assert cat.discipline == "madison"

    def test_clm(self):
        cat, _ = categorize_event("Maitre A Kilo CLM Final  / Omni III")
        assert cat.discipline == "time_trial_kilo"

    def test_200m_french_sprint(self):
        cat, _ = categorize_event("U15 F 200m Final  / Omni I")
        assert cat.discipline == "sprint_qualifying"

    def test_french_tempo_race(self):
        cat, _ = categorize_event("Junior/Maitre/Elite F Tempo Race Final  / Omni II")
        assert cat.discipline == "tempo_race"


class TestCompoundClassifications:
    """Test compound classification groups."""

    def test_elite_junior(self):
        cat, _ = categorize_event("Elite/Junior Women Scratch Race  / Omni I")
        assert cat.classification == "elite_junior"

    def test_junior_elite(self):
        cat, _ = categorize_event("Junior/Elite F Vitesse Qualifying")
        assert cat.classification == "elite_junior"

    def test_junior_master_elite(self):
        cat, _ = categorize_event("Junior/Maitre/Elite F Tempo Race Final  / Omni II")
        assert cat.classification == "junior_master_elite"

    def test_u11_u13(self):
        cat, _ = categorize_event("U11 & U13 Keirin 1-6 Final")
        assert cat.classification == "u11_u13"

    def test_u15_u17(self):
        cat, _ = categorize_event("U15/U17 H Course Aux Points Final  / Omni V")
        assert cat.classification == "u15_u17"

    def test_master_ab(self):
        cat, _ = categorize_event("Master A/B Men Sprint Final")
        assert cat.classification == "master_ab"

    def test_master_cd(self):
        cat, _ = categorize_event("Master C/D Men Sprint 1/8 Final")
        assert cat.classification == "master_cd"

    def test_maitre_cd(self):
        cat, _ = categorize_event("Maitre C-D H Vitesse Final")
        assert cat.classification == "master_cd"


class TestParaClassifications:
    """Test para-cycling classifications."""

    def test_para_c1_5(self):
        cat, _ = categorize_event("Para C1-5 Elimination Race Final")
        assert cat.classification == "para_c1_5"

    def test_para_c4(self):
        cat, _ = categorize_event("Para C4 Men Flying 200m Final")
        assert cat.classification == "para_c4"

    def test_para_b_mixed(self):
        cat, _ = categorize_event("Para B Mixed Team Sprint Final")
        assert cat.classification == "para_b"
        assert cat.gender == "open"


class TestAgeBrackets:
    """Test age bracket range classifications."""

    def test_35_39(self):
        cat, _ = categorize_event("35-39 Women Pursuit Final")
        assert cat.classification == "age_35_39"

    def test_80_plus(self):
        cat, _ = categorize_event("80+ Men Sprint Qualifying")
        assert cat.classification == "age_80_plus"

    def test_55_64(self):
        cat, _ = categorize_event("55-64 Women Sprint 1/2 Final Ride 1")
        assert cat.classification == "age_55_64"


class TestOmniumParts:
    """Test omnium part extraction."""

    def test_omni_i(self):
        cat, _ = categorize_event("Elite/Junior Women Scratch Race  / Omni I")
        assert cat.omnium_part == 1

    def test_omni_iii(self):
        cat, _ = categorize_event("Maitre A Kilo CLM Final  / Omni III")
        assert cat.omnium_part == 3

    def test_omni_v(self):
        cat, _ = categorize_event("U15/U17 H Course Aux Points Final  / Omni V")
        assert cat.omnium_part == 5

    def test_omni_vii(self):
        cat, _ = categorize_event("Senior H Keirin Final / Omni VII")
        assert cat.omnium_part == 7

    def test_no_omnium(self):
        cat, _ = categorize_event("Elite Men Sprint Final")
        assert cat.omnium_part is None


class TestRoundExtraction:
    """Test round detection."""

    def test_qualifying(self):
        cat, _ = categorize_event("U17 Women Sprint Qualifying")
        assert cat.round == "qualifying"

    def test_final(self):
        cat, _ = categorize_event("Elite Men Sprint Final")
        assert cat.round == "final"

    def test_semi_final(self):
        cat, _ = categorize_event("Elite Men Sprint 1/2 Final Ride 1")
        assert cat.round == "semi_final"

    def test_quarter_final(self):
        cat, _ = categorize_event("Women Sprint 1/4 Final")
        assert cat.round == "quarter_final"

    def test_eighth_final(self):
        cat, _ = categorize_event("Master C/D Men Sprint 1/8 Final")
        assert cat.round == "eighth_final"

    def test_sixteenth_final(self):
        cat, _ = categorize_event("Women Sprint 1/16 Final")
        assert cat.round == "sixteenth_final"

    def test_sixteenth_final_repechage(self):
        cat, _ = categorize_event("Men Sprint 1/16 Final Repechage")
        assert cat.round == "sixteenth_final_repechage"

    def test_final_1_6(self):
        cat, _ = categorize_event("U11 & U13 Keirin 1-6 Final")
        assert cat.round == "final_1_6"

    def test_final_7_12(self):
        cat, _ = categorize_event("Women Sprint 7-12 Final")
        assert cat.round == "final_7_12"

    def test_final_9_12(self):
        cat, _ = categorize_event("Women Sprint 9-12 Final")
        assert cat.round == "final_9_12"

    def test_bronze_final(self):
        cat, _ = categorize_event("Men Sprint Bronze Final")
        assert cat.round == "bronze_final"

    def test_qualifier_1(self):
        cat, _ = categorize_event("Men Omnium Qualifier 1")
        assert cat.round == "qualifier_1"


class TestRideExtraction:
    """Test ride number extraction."""

    def test_ride_1(self):
        cat, _ = categorize_event("Elite Men Sprint 1/2 Final Ride 1")
        assert cat.ride_number == 1

    def test_ride_2(self):
        cat, _ = categorize_event("Elite Men Sprint Final Ride 2")
        assert cat.ride_number == 2

    def test_no_ride(self):
        cat, _ = categorize_event("Elite Men Sprint Final")
        assert cat.ride_number is None


class TestGenderDetection:
    """Test gender detection including French abbreviations."""

    def test_women(self):
        cat, _ = categorize_event("U17 Women Sprint Qualifying")
        assert cat.gender == "women"

    def test_men(self):
        cat, _ = categorize_event("Elite Men Sprint Final")
        assert cat.gender == "men"

    def test_french_f(self):
        cat, _ = categorize_event("U15 F 200m Final  / Omni I")
        assert cat.gender == "women"

    def test_french_h(self):
        cat, _ = categorize_event("U15/U17 H Course Aux Points Final  / Omni V")
        assert cat.gender == "men"

    def test_mixed(self):
        cat, _ = categorize_event("Para B Mixed Team Sprint Final")
        assert cat.gender == "open"

    def test_co_ed(self):
        cat, _ = categorize_event("Co-Ed Team Sprint Final")
        assert cat.gender == "open"

    def test_open_default(self):
        cat, _ = categorize_event("Para C1-5 Elimination Race Final")
        assert cat.gender == "open"


class TestSpecialEvents:
    """Test special event detection."""

    def test_break(self):
        cat, residual = categorize_event("Break")
        assert cat.discipline == "break_"
        assert residual == ""

    def test_end_of_session(self):
        cat, residual = categorize_event("End of Session")
        assert cat.discipline == "end_of_session"

    def test_medal_ceremonies(self):
        cat, residual = categorize_event("Medal Ceremonies")
        assert cat.discipline == "ceremony"

    def test_medal_ceremony(self):
        cat, residual = categorize_event("Medal Ceremony")
        assert cat.discipline == "ceremony"

    def test_pause_french(self):
        cat, residual = categorize_event("Pause - Reprise à 12h30")
        assert cat.discipline == "break_"

    def test_warmup(self):
        cat, residual = categorize_event("Madison Warm-up")
        assert cat.discipline == "break_"
        assert residual == ""

    def test_warmup_no_hyphen(self):
        cat, residual = categorize_event("Sprint Warmup")
        assert cat.discipline == "break_"


class TestExhibitionEvents:
    """Test exhibition/novelty event detection."""

    def test_kids_race(self):
        cat, _ = categorize_event("Kids Race")
        assert cat.discipline == "exhibition"

    def test_chariot_race(self):
        cat, _ = categorize_event("Junior Open Chariot Race Final")
        assert cat.discipline == "exhibition"

    def test_wheel_race(self):
        cat, _ = categorize_event("Wheel Race")
        assert cat.discipline == "exhibition"

    def test_longest_lap(self):
        cat, _ = categorize_event("Longest Lap")
        assert cat.discipline == "exhibition"


class TestDistanceVariantResolution:
    """Test pursuit and time trial distance-variant key resolution."""

    def test_elite_men_pursuit_4k(self):
        cat, _ = categorize_event("Elite Men Individual Pursuit Final")
        assert cat.discipline == "pursuit_4k"

    def test_elite_women_pursuit_4k(self):
        cat, _ = categorize_event("Elite Women Individual Pursuit Final")
        assert cat.discipline == "pursuit_4k"

    def test_junior_women_pursuit_2k(self):
        cat, _ = categorize_event("Junior Women Pursuit Final")
        assert cat.discipline == "pursuit_2k"

    def test_junior_men_pursuit_3k(self):
        cat, _ = categorize_event("Junior Men Pursuit Final")
        assert cat.discipline == "pursuit_3k"

    def test_u17_pursuit_2k(self):
        cat, _ = categorize_event("U17 Women Pursuit Final")
        assert cat.discipline == "pursuit_2k"

    def test_age_bracket_women_pursuit_2k(self):
        cat, _ = categorize_event("35-39 Women Pursuit Final")
        assert cat.discipline == "pursuit_2k"

    def test_generic_fallback(self):
        cat, _ = categorize_event("Pursuit Final")
        assert cat.discipline == "pursuit_3k"  # no classification, open gender -> 3k fallback

    def test_senior_men_pursuit_4k(self):
        cat, _ = categorize_event("Senior H Poursuite Final")
        assert cat.discipline == "pursuit_4k"


class TestUnresolvedResidual:
    """Test that unresolved text is returned."""

    def test_clean_parse(self):
        _, residual = categorize_event("Elite Men Sprint Final")
        assert residual == ""

    def test_exhibition_prefix_stripped(self):
        cat, residual = categorize_event("Exhibition Flying 200m Final")
        assert cat.discipline == "sprint_qualifying"
        assert cat.round == "final"


class TestFullTestCasesFromPipeline:
    """Validate all 27+ test cases from data-pipeline.md section 2.8."""

    @pytest.mark.parametrize("event_name,expected_disc,expected_gender,expected_class,expected_round,expected_ride,expected_omni", [
        ("U17 Women Sprint Qualifying", "sprint_qualifying", "women", "u17", "qualifying", None, None),
        ("Master C/D Men Sprint 1/8 Final", "sprint_match", "men", "master_cd", "eighth_final", None, None),
        ("Elite/Junior Women Scratch Race  / Omni I", "scratch_race", "women", "elite_junior", None, None, 1),
        ("U11 & U13 Keirin 1-6 Final", "keirin", "open", "u11_u13", "final_1_6", None, None),
        ("Elite Men Sprint 1/2 Final Ride 1", "sprint_match", "men", "elite", "semi_final", 1, None),
        ("Para C4 Men Flying 200m Final", "sprint_qualifying", "men", "para_c4", "final", None, None),
        ("Open F Madison Final", "madison", "women", "open", "final", None, None),
        ("Open H Madison Final", "madison", "men", "open", "final", None, None),
        ("Co-Ed Team Sprint Final", "team_sprint", "open", None, "final", None, None),
        ("35-39 Women Pursuit Final", "pursuit_2k", "women", "age_35_39", "final", None, None),
        ("80+ Men Sprint Qualifying", "sprint_qualifying", "men", "age_80_plus", "qualifying", None, None),
        ("55-64 Women Sprint 1/2 Final Ride 1", "sprint_match", "women", "age_55_64", "semi_final", 1, None),
        ("Para C1-5 Elimination Race Final", "elimination_race", "open", "para_c1_5", "final", None, None),
        ("Para B Mixed Team Sprint Final", "team_sprint", "open", "para_b", "final", None, None),
        ("Junior/Elite F Vitesse Qualifying", "sprint_qualifying", "women", "elite_junior", "qualifying", None, None),
        ("U15 F 200m Final  / Omni I", "sprint_qualifying", "women", "u15", "final", None, 1),
        ("Maitre A Kilo CLM Final  / Omni III", "time_trial_kilo", "open", "master_a", "final", None, 3),
        ("Senior H Vitesse Final Ride 1 / Omni III", "sprint_match", "men", "senior", "final", 1, 3),
        ("Junior/Maitre/Elite F Tempo Race Final  / Omni II", "tempo_race", "women", "junior_master_elite", "final", None, 2),
        ("U15/U17 H Course Aux Points Final  / Omni V", "points_race", "men", "u15_u17", "final", None, 5),
        ("Women Miss And Out Final", "elimination_race", "women", None, "final", None, None),
        ("Men American Tempo Final", "tempo_race", "men", None, "final", None, None),
        ("Junior Men Point A Lap Final", "tempo_race", "men", "junior", "final", None, None),
        ("Women Sprint 1/16 Final", "sprint_match", "women", None, "sixteenth_final", None, None),
        ("Men Sprint 1/16 Final Repechage", "sprint_match", "men", None, "sixteenth_final_repechage", None, None),
        ("Women Sprint 9-12 Final", "sprint_match", "women", None, "final_9_12", None, None),
        ("Exhibition Flying 200m Final", "sprint_qualifying", "open", None, "final", None, None),
        ("Men Omnium Qualifier 1", "unknown", "men", None, "qualifier_1", None, None),
        ("Kids Race", "exhibition", "open", None, None, None, None),
        ("Junior Open Chariot Race Final", "exhibition", "open", "junior", "final", None, None),
    ])
    def test_pipeline_cases(self, event_name, expected_disc, expected_gender, expected_class, expected_round, expected_ride, expected_omni):
        cat, _ = categorize_event(event_name)
        assert cat.discipline == expected_disc, f"discipline mismatch for '{event_name}'"
        assert cat.gender == expected_gender, f"gender mismatch for '{event_name}'"
        assert cat.classification == expected_class, f"classification mismatch for '{event_name}'"
        assert cat.round == expected_round, f"round mismatch for '{event_name}'"
        assert cat.ride_number == expected_ride, f"ride_number mismatch for '{event_name}'"
        assert cat.omnium_part == expected_omni, f"omnium_part mismatch for '{event_name}'"
