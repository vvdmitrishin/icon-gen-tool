"""Planner quality tuning: focal point, Category 4 hero subject, Set meaning, subject variety.
Offline: prompt content plus the existing validation only; no LLM is called."""
import pytest

from app.services.category_distribution import resolve_distribution
from app.services.planning import PlanningError
from app.services.text_planner import CATEGORY_MEANINGS, TextPlanner

STD = resolve_distribution(None, number_of_cards=10, gold_cards=0)
GRAND = resolve_distribution(None, number_of_cards=6, gold_cards=6, set_type="grand")


def prompt(counts=STD, grand=False, history=None, wishes=""):
    return TextPlanner.build_prompt("Technology", "Two-Wheeled Vehicles", 2, counts, wishes, grand, history)


# ---------------------------------------------------------------- standard set
def test_category_definitions_1_to_3_unchanged_and_4_names_a_hero_subject():
    assert CATEGORY_MEANINGS[1] == "Category 1 - a SIMPLE object floating in the air, no small details, no ground"
    assert CATEGORY_MEANINGS[2] == "Category 2 - a simple object standing on a simple surface"
    assert CATEGORY_MEANINGS[3].startswith("Category 3 - one collectible subject on a context-appropriate physical surface")
    c4 = CATEGORY_MEANINGS[4]
    assert "hero subject" in c4 and "environment" in c4 and "dominant" in c4


def test_standard_prompt_asks_for_focal_point_and_category4_hero_not_location():
    p = prompt()
    assert "FOCAL POINT" in p and "thumbnail" in p and "crowds" in p
    assert "name the hero subject, not the place" in p
    assert "richer environment" in p  # Category 4 is not simplified into Category 3
    assert "composition principles, not fixed subjects" in p


def test_set_meaning_separates_core_subjects_from_accessories_without_hardcoding():
    p = prompt()
    assert "SET MEANING" in p and "related gear" in p and "genuinely contradicts" in p
    assert "rigid technical rule" in p and "replace any that contradicts" in p
    assert prompt().count("Two-Wheeled Vehicles") >= 2  # generic wording; the Set name only comes from the request
    other = TextPlanner.build_prompt("Food", "Italian Desserts", 1, STD, "")
    assert "SET MEANING" in other and "Italian Desserts" in other  # same instructions for ANY Collection/Set


def test_variety_is_a_preference_not_a_quota():
    p = prompt(history=["Carbon Road Bike - sleek"])
    assert "VARIETY" in p and "silhouette" in p and "preference, not a quota" in p
    assert "never pick an obscure or weak subject" in p
    assert "ALREADY GENERATED" in p and "Carbon Road Bike" in p  # cross-batch history still passed


def test_distribution_slots_and_planner_contract_unchanged():
    p = prompt()
    assert "exactly 2 card(s)" in p and "exactly 3 card(s)" in p and "Create 2 DISTINCT" in p
    assert "category slots: [1, 1, 2, 2, 3, 3, 3, 4, 4, 4]" in p


def test_wishes_still_present_and_strict():
    p = prompt(wishes="no buildings")
    assert "Exclusions are strict" in p and "no buildings" in p


def _plan(cards, counts=STD):
    data = {"variants": [{"title": "t", "concept": "c", "cards": cards}]}
    return TextPlanner._build(data, "Two-Wheeled Vehicles", 1, counts, 1, "m")


def _std_cards(names):
    cats = [1, 1, 2, 2, 3, 3, 3, 4, 4, 4]
    return [dict(position=i + 1, category=c, object_name=n, object_description=f"{n} illustration", rationale="fits")
            for i, (c, n) in enumerate(zip(cats, names))]


NAMES = ["Road Bicycle", "Motorcycle", "Bicycle Helmet", "Tire Pump", "Chain Lock", "Kick Scooter", "Repair Toolkit",
         "Detective's Antique Writing Desk", "Forensic Evidence Microscope", "Cycling Track Pedestal"]


def test_related_accessories_and_hero_subjects_are_accepted():
    plan = _plan(_std_cards(NAMES))
    assert [c.category for c in plan.variants[0].cards].count(4) == 3 and not any(c.category == 5 for c in plan.variants[0].cards)


def test_exact_and_synonymous_duplicates_still_rejected():
    dup = list(NAMES)
    dup[1] = "Road Bicycle"
    with pytest.raises(PlanningError):
        _plan(_std_cards(dup))
    near = list(NAMES)
    near[4] = "Road Bicycles"
    with pytest.raises(PlanningError):
        _plan(_std_cards(near))


# ------------------------------------------------------------------- grand set
def test_grand_prompt_keeps_characters_and_has_no_object_only_focal_rule():
    p = prompt(counts=GRAND, grand=True)
    assert "FOCAL POINT (Categories 1-4)" not in p  # standard-card rule is NOT applied to character scenes
    assert "EXACTLY 1 or 2 characters" in p and "never more" in p
    assert "ONE character or ONE interaction between TWO characters" in p
    assert "irony, humour and storytelling" in p
    assert "no Category 1-4 cards" in p and "Characters must be original designs" in p


def test_grand_plan_validation_unchanged():
    cards = [dict(position=i + 1, category=5, object_name=f"Scene {chr(65 + i)} fox", object_description=["two rivals share umbrella", "chef burns toast", "pilot hugs cactus", "sleepy guard snores", "twins swap hats", "clown stuck ladder"][i],
                  characters=1 + i % 2, rationale="finale") for i in range(6)]
    data = {"variants": [{"title": "t", "concept": "c", "cards": cards}]}
    plan = TextPlanner._build(data, "S", 1, GRAND, 1, "m")
    assert all(c.category == 5 and c.characters in (1, 2) for c in plan.variants[0].cards)
    cards[0]["characters"] = 3
    with pytest.raises(PlanningError):
        TextPlanner._build({"variants": [{"title": "t", "concept": "c", "cards": cards}]}, "S", 1, GRAND, 1, "m")


def test_mixed_set_applies_focal_rule_to_standard_cards_only():
    mixed = resolve_distribution(None, number_of_cards=10, gold_cards=2)
    p = prompt(counts=mixed)
    assert "FOCAL POINT (Categories 1-4)" in p and "GOLDEN cards (Category 5)" in p and "AT MOST 2 characters" in p
