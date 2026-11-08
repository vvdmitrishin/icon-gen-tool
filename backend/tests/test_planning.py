"""Plan validation, seeds and the Movie Collection genre list. Plans come from the deterministic fixture
(tests/conftest.py); there are no curated object pools any more."""
import pytest

from app.services.planning import MOVIE_DELIVERABLE_GENRES, default_seed, supported_genres, validate_plan
from tests.conftest import make_plan

DEFAULT = {1: 2, 2: 2, 3: 3, 4: 3, 5: 0}


@pytest.mark.parametrize("variants", [1, 3, 5])
@pytest.mark.parametrize("counts", [DEFAULT, {1: 3, 2: 2, 3: 2, 4: 3, 5: 0}, {1: 1, 2: 1, 3: 1, 4: 1, 5: 2}, {1: 0, 2: 0, 3: 0, 4: 0, 5: 4}])
def test_fixture_plans_are_valid_for_any_distribution_and_variant_count(variants, counts):
    plan = make_plan(variants, counts)
    assert validate_plan(plan, counts) == [] and len(plan.variants) == variants
    assert all(len(v.cards) == sum(counts.values()) for v in plan.variants)
    assert plan.planning_mode == "ai"


def test_same_distribution_across_all_alternatives():
    plan = make_plan(5, DEFAULT)
    dists = {tuple(sum(1 for c in v.cards if c.category == k) for k in (1, 2, 3, 4, 5)) for v in plan.variants}
    assert dists == {(2, 2, 3, 3, 0)}


def test_default_seed_is_stable():
    assert default_seed("Fantasy", 3) == default_seed("Fantasy", 3) != default_seed("Fantasy", 4)


def test_supported_genres_are_only_the_movie_deliverable_names():
    g = {x["genre"]: x for x in supported_genres()}
    assert set(g) == set(MOVIE_DELIVERABLE_GENRES) == {"Science Fiction", "Detective", "Fantasy"}
    assert all("microthemes" not in x for x in g.values())  # no object pools


def test_validator_catches_tampered_plans():
    plan = make_plan(2, DEFAULT)
    plan.variants[0].cards[1].object_name = plan.variants[0].cards[0].object_name  # duplicate in variant
    assert any("duplicate" in i for i in validate_plan(plan, DEFAULT))

    plan = make_plan(2, DEFAULT)
    plan.variants[1].cards[0].object_name = plan.variants[0].cards[0].object_name  # across variants
    assert any("appears in variants" in i for i in validate_plan(plan, DEFAULT))

    plan = make_plan(1, DEFAULT)
    plan.variants[0].cards.pop()
    assert any("expected 10" in i for i in validate_plan(plan, DEFAULT))

    plan = make_plan(1, DEFAULT)
    plan.variants[0].cards[0].category = 5  # a Cat.5 card is legal now, but the distribution must still match
    assert any("differ from requested" in i for i in validate_plan(plan, DEFAULT))
    plan.variants[0].cards[0].category = 9
    assert any("invalid category" in i for i in validate_plan(plan, DEFAULT))

    plan = make_plan(1, DEFAULT)
    assert any("differ from requested" in i for i in validate_plan(plan, {1: 3, 2: 2, 3: 2, 4: 3, 5: 0}))
