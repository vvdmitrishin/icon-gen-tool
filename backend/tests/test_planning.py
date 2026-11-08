import pytest

from app.services.category_distribution import assign_slots
from app.services.planning import (
    PlanningError,
    default_seed,
    plan_set,
    resolve_genre,
    supported_genres,
    validate_plan,
)

DEFAULT = {1: 2, 2: 2, 3: 3, 4: 3, 5: 0}
GENRES = ["Science Fiction", "Detective", "Fantasy"]


@pytest.mark.parametrize("genre", GENRES)
@pytest.mark.parametrize("variants", [1, 3, 5])
def test_plans_are_valid_for_every_genre_and_variant_count(genre, variants):
    plan = plan_set(genre, variants, DEFAULT)
    assert len(plan.variants) == variants
    assert validate_plan(plan, DEFAULT) == []
    for v in plan.variants:
        assert len(v.cards) == 10
        assert [c.category for c in v.cards] == assign_slots(DEFAULT)
        assert not any(c.borrowed for c in v.cards)  # default distribution never needs borrowing
    names = [c.object_name for v in plan.variants for c in v.cards]
    assert len(names) == len(set(names))  # unique across all alternatives
    assert len({v.title for v in plan.variants}) == variants  # distinct micro-themes, not recolours


def test_same_distribution_across_all_alternatives():
    plan = plan_set("Detective", 5, {1: 3, 2: 2, 3: 2, 4: 3, 5: 0})
    slots = {tuple(c.category for c in v.cards) for v in plan.variants}
    assert len(slots) == 1


def test_custom_extreme_distribution_still_valid_via_borrowing():
    counts = {1: 4, 2: 4, 3: 1, 4: 1, 5: 0}  # 8 simple objects needed, a micro-theme has 5
    plan = plan_set("Science Fiction", 1, counts)
    assert validate_plan(plan, counts) == []
    assert any(c.borrowed for v in plan.variants for c in v.cards)
    assert any("borrowed" in n for n in plan.notes)


def test_deterministic_for_same_seed_and_varies_with_seed():
    a = plan_set("Fantasy", 3, DEFAULT, seed=7).model_dump()
    b = plan_set("Fantasy", 3, DEFAULT, seed=7).model_dump()
    c = plan_set("Fantasy", 3, DEFAULT, seed=8).model_dump()
    assert a == b and a != c


def test_default_seed_is_stable():
    assert default_seed("Fantasy", 3) == default_seed("fantasy", 3)
    assert plan_set("Fantasy", 3, DEFAULT).seed == default_seed("Fantasy", 3)


def test_cat1_and_cat2_get_simple_objects_only():
    plan = plan_set("Science Fiction", 1, DEFAULT, seed=1)
    simple_names = {"Holographic Communicator", "Space Helmet", "Anti-Gravity Boots", "Robot Assistant",
                    "Glowing Alien Fruit", "Antenna Hat", "Hover Skateboard", "Translator Gadget",
                    "Glowing Beaker", "Plasma Globe", "Data Crystal", "Holographic Microscope",
                    "Wind-Up Robot Toy", "Gear Cluster", "Tiny Drone", "Oil Can",
                    "Space Snack Pack", "Satellite Dish Model", "Astronaut Glove", "Rocket Fuel Cell",
                    "Star Map Scroll", "Alien Pet Plush", "Hover Clipboard", "Robot Hand", "Zero-G Water Bubble"}
    for c in plan.variants[0].cards:
        if c.category in (1, 2):
            assert c.object_name in simple_names


def test_genre_aliases_and_unknown_genre():
    assert resolve_genre("sci-fi") == "Science Fiction"
    assert resolve_genre("  DETECTIVE ") == "Detective"
    with pytest.raises(PlanningError) as e:
        resolve_genre("Western")
    assert e.value.code == "unknown_genre" and "TextPlanner" in e.value.message


def test_variant_count_bounds():
    with pytest.raises(PlanningError):
        plan_set("Detective", 6, DEFAULT)
    with pytest.raises(PlanningError):
        plan_set("Detective", 0, DEFAULT)


def test_supported_genres_listing():
    g = {x["genre"]: x for x in supported_genres()}
    assert set(g) == set(GENRES) and all(x["max_variants"] == 5 for x in g.values())


def test_validator_catches_tampered_plans():
    plan = plan_set("Detective", 2, DEFAULT)
    plan.variants[0].cards[1].object_name = plan.variants[0].cards[0].object_name  # duplicate in variant
    assert any("duplicate" in i for i in validate_plan(plan, DEFAULT))

    plan = plan_set("Detective", 2, DEFAULT)
    plan.variants[1].cards[0].object_name = plan.variants[0].cards[0].object_name  # across variants
    assert any("appears in variants" in i for i in validate_plan(plan, DEFAULT))

    plan = plan_set("Detective", 1, DEFAULT)
    plan.variants[0].cards.pop()
    assert any("expected 10" in i for i in validate_plan(plan, DEFAULT))

    plan = plan_set("Detective", 1, DEFAULT)
    plan.variants[0].cards[0].category = 5  # a Cat.5 card is legal now, but the distribution must still match
    assert any("differ from requested" in i for i in validate_plan(plan, DEFAULT))
    plan.variants[0].cards[0].category = 9
    assert any("invalid category" in i for i in validate_plan(plan, DEFAULT))

    plan = plan_set("Detective", 1, DEFAULT)
    assert any("differ from requested" in i for i in validate_plan(plan, {1: 3, 2: 2, 3: 2, 4: 3, 5: 0}))


def test_pool_exhaustion_gives_actionable_error():
    with pytest.raises(PlanningError) as e:
        plan_set("Detective", 5, {1: 4, 2: 4, 3: 1, 4: 1, 5: 0})  # 40 simple objects needed, pool has 25
    assert e.value.code == "pool_exhausted" and "fewer variants" in e.value.message
