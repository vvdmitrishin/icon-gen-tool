"""Metaphor Planner semantic diversity: the theme is a WORLD (not an object class), Free Wishes are a real brief, similarity is judged by idea.
Prompt/validation level only; no network."""
import pytest

from app.services.category_distribution import resolve_distribution
from app.services.planning import PlanningError
from app.services.semantic_planning import similar_concepts
from app.services.text_planner import _RESPONSE_SCHEMA, TextPlanner

COUNTS = resolve_distribution(None, number_of_cards=10, gold_cards=0)


def prompt(collection="Movie", set_name="Detective", wishes="", history=None):
    return TextPlanner.build_prompt(collection, set_name, 1, COUNTS, wishes, False, history)


def test_prompt_interprets_the_set_as_a_world_with_facets_but_no_quotas_or_pools():
    p = prompt()
    assert "complete thematic WORLD" in p and "theme_map" in p and "NOT required categories or quotas" in p
    for facet in ("environments", "everyday objects", "activities and events", "infrastructure", "creatures", "vignette"):
        assert facet in p
    assert "most stereotypical" in p and "aspect" in p
    # generalised logic: nothing theme-specific is hardcoded, the same text serves any Collection/Set
    assert "magnifying" not in p.lower() and "crime" not in p.lower() and "pipe" not in p.lower()
    other = prompt("Summer", "Seafaring")
    assert p.replace("'Movie'", "X").replace("'Detective'", "Y") == other.replace("'Summer'", "X").replace("'Seafaring'", "Y")


def test_free_wishes_are_a_priority_brief_placed_before_the_card_rules_and_history():
    p = prompt(wishes="crime scenes, urban locations, everyday objects", history=["Magnifying Glass - round lens"])
    assert "crime scenes, urban locations, everyday objects" in p and "outrank" in p and "Exclusions are strict" in p and "wishes_applied" in p
    assert p.index("PRODUCER'S FREE WISHES") < p.index("Choose subjects.") < p.index("ALREADY GENERATED")
    assert "PRODUCER'S FREE WISHES" not in prompt()  # no wishes -> no brief


def test_history_is_context_for_new_facets_and_similarity_is_by_idea():
    p = prompt(history=["Magnifying Glass - round lens"])
    assert "Do not repeat or paraphrase" in p and "DIFFERENT facets" in p and "by IDEA, not by name" in p


def test_focal_rule_still_one_subject_but_allows_focused_environments_and_vignettes():
    p = prompt()
    assert "FOCAL POINT" in p and "thumbnail" in p and "full game screen" in p and "small focused vignette" in p
    assert "Category 1 - a SIMPLE object floating" in p  # Categories 1-4 remain visual-complexity categories


def test_schema_asks_for_the_facets_before_the_cards():
    assert _RESPONSE_SCHEMA["propertyOrdering"][:2] == ["obvious_ideas", "theme_map"] and "theme_map" in _RESPONSE_SCHEMA["required"]
    card = _RESPONSE_SCHEMA["properties"]["variants"]["items"]["properties"]["cards"]["items"]
    assert card["propertyOrdering"].index("aspect") < card["propertyOrdering"].index("object_name")


def test_similar_concepts_flags_paraphrases_but_not_related_objects():
    assert similar_concepts([("Magnifying Glass", "a round magnifying glass lens on a brass handle"),
                             ("Magnifying Lens Glass", "a brass handle round magnifying lens glass")])
    ok = [("Bicycle", "a red road bicycle"), ("Bicycle Helmet", "a white helmet with straps"),
          ("Coffee Cup", "a paper cup of steaming coffee"), ("Crime Scene Tape", "yellow tape across a wet alley"),
          ("Street Lamp", "a tall street lamp glowing at night"), ("Desk Lamp", "a green banker's desk lamp")]
    assert similar_concepts(ok) == []


def _data(names_descs):
    cards = [dict(position=i + 1, category=c, object_name=n, object_description=d, rationale="r")
             for i, ((n, d), c) in enumerate(zip(names_descs, [1, 1, 2, 2, 3, 3, 3, 4, 4, 4]))]
    return cards


DIVERSE = [("Crime Scene Tape", "yellow tape across a wet alley"), ("Street Lamp", "a tall lamp glowing in fog"),
           ("Coffee Cup", "a paper cup of coffee on a ledge"), ("Evidence Marker", "a numbered cone on asphalt"),
           ("Fire Escape", "a zigzag iron stairway on a brick wall"), ("Payphone Booth", "a red booth at a corner"),
           ("Dumpster Alley", "a green dumpster with a cat on top"), ("Rooftop Chase", "a hat flying off a rooftop edge"),
           ("Night Diner Window", "a neon-lit diner window with a lone cup"), ("Bushes With Clue", "a glove hidden in garden bushes")]


def test_build_surfaces_facets_and_wishes_notes_and_rejects_paraphrases():
    data = {"theme_map": ["crime scenes", "urban places", "everyday objects"], "wishes_applied": "Used urban places and crime scenes.",
            "variants": [{"title": "t", "concept": "c", "cards": _data(DIVERSE)}]}
    plan = TextPlanner._build(data, "Detective", 1, COUNTS, 1, "m", wishes="crime scenes, urban places")
    assert any("Facets of the Set's world explored: crime scenes; urban places; everyday objects" in n for n in plan.notes)
    assert any(n.startswith("How your wishes were applied") for n in plan.notes)
    dup = list(DIVERSE)
    dup[1] = ("Crime-Scene Tape Roll", "tape in yellow across a wet alley")
    with pytest.raises(PlanningError) as e:
        TextPlanner._build({"variants": [{"title": "t", "concept": "c", "cards": _data(dup)}]}, "Detective", 1, COUNTS, 1, "m")
    assert e.value.code == "ai_plan_invalid" and "same idea" in e.value.message


def test_obvious_ideas_are_reported_and_cliche_use_is_noted():
    data = {"obvious_ideas": ["magnifying glass", "wooden pipe"], "theme_map": ["places"],
            "variants": [{"title": "t", "concept": "c", "cards": _data([("Brass Magnifying Glass", "a lens on a handle")] + DIVERSE[1:])}]}
    plan = TextPlanner._build(data, "Detective", 1, COUNTS, 1, "m")
    note = next(n for n in plan.notes if n.startswith("Most obvious ideas"))
    assert "magnifying glass; wooden pipe" in note and "Still used: Brass Magnifying Glass" in note
