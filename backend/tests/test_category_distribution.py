import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.category_distribution import (
    CategoryDistribution,
    DistributionError,
    assign_slots,
    compliance,
    default_counts,
    resolve_distribution,
    validate_counts,
)
from app.services.prompt_compiler import CardSpec, compile_card_prompt


def test_default_is_2_2_3_3_with_golden_zero():
    assert default_counts() == {1: 2, 2: 2, 3: 3, 4: 3, 5: 0}


def test_auto_balanced_resolves_to_default():
    assert resolve_distribution(CategoryDistribution()) == {1: 2, 2: 2, 3: 3, 4: 3, 5: 0}
    assert resolve_distribution(None) == {1: 2, 2: 2, 3: 3, 4: 3, 5: 0}


def test_auto_ignores_supplied_counts():
    d = CategoryDistribution(mode="auto_balanced", counts={1: 7, 2: 1, 3: 1, 4: 1})
    assert resolve_distribution(d)[1] == 2


def test_custom_valid():
    out = resolve_distribution(CategoryDistribution(mode="custom", counts={1: 1, 2: 4, 3: 3, 4: 2}))
    assert out == {1: 1, 2: 4, 3: 3, 4: 2, 5: 0}


def test_a_category_may_have_zero_cards_and_is_only_flagged():
    out = resolve_distribution(CategoryDistribution(mode="custom", counts={1: 0, 2: 4, 3: 3, 4: 3}))
    assert out == {1: 0, 2: 4, 3: 3, 4: 3, 5: 0}
    ok, note = compliance(out)
    assert ok is False and "Category 1" in (note or "")
    assert resolve_distribution(CategoryDistribution(mode="custom", counts={1: 0, 2: 10, 3: 0, 4: 0}))[2] == 10


@pytest.mark.parametrize("counts,code", [
    ({1: 2, 2: 2, 3: 3, 4: 2}, "bad_total"),          # sums to 9
    ({1: 3, 2: 3, 3: 3, 4: 3}, "bad_total"),          # sums to 12
    ({1: 2, 2: 2, 3: 3}, "bad_total"),               # missing category: sums to 7
    ({1: -1, 2: 4, 3: 4, 4: 3}, "negative"),
    ({1: 2, 2: 2, 3: 3, 4: 3, 6: 0}, "unknown_category"),
])
def test_invalid_distributions_rejected(counts, code):
    with pytest.raises(DistributionError) as e:
        validate_counts(counts)
    assert e.value.code == code


def test_custom_without_counts_rejected():
    with pytest.raises(DistributionError):
        resolve_distribution(CategoryDistribution(mode="custom"))


def test_assign_slots_orders_by_category_and_totals_ten():
    slots = assign_slots({1: 2, 2: 2, 3: 3, 4: 3})
    assert slots == [1, 1, 2, 2, 3, 3, 3, 4, 4, 4]
    assert len(assign_slots({1: 1, 2: 1, 3: 1, 4: 7})) == 10


def test_same_distribution_gives_identical_slots_for_every_alternative():
    counts = resolve_distribution(CategoryDistribution(mode="custom", counts={1: 3, 2: 2, 3: 2, 4: 3}))
    alts = [assign_slots(counts) for _ in range(5)]  # 3-5 alternatives share one distribution
    assert all(a == alts[0] for a in alts)


# ---- category-specific prompt logic (spec §3.4) -------------------------------------------------
def prompt(cat):
    return compile_card_prompt(
        CardSpec(collection="Movie Collection", genre="Detective", object_name="Magnifying Glass",
                 category=cat)
    ).layers["B_settings"].lower()


def test_cat1_prompt_floating_no_small_details_simple_background():
    p = prompt(1)
    for needle in ("no small details", "floating", "flat color", "gradient", "repeating pattern", "rays"):
        assert needle in p


def test_cat2_prompt_simple_surface_and_vertical_background():
    p = prompt(2)
    assert "simple surface" in p and "vertical background" in p and "minimal" in p


def test_cat3_prompt_realistic_surface_volumetric_centered():
    p = prompt(3)
    assert "more realistic surface" in p and "volumetric" in p and "centered and readable" in p


def test_cat4_prompt_realistic_environment_object_central_focus():
    p = prompt(4)
    assert "realistic environment" in p and "central focus" in p


def test_golden_category_is_accepted_by_card_spec():
    assert CardSpec(collection="c", genre="g", object_name="o", category=5).category == 5


# ---- API ----------------------------------------------------------------------------------------
@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


def test_models_endpoint_exposes_default_distribution(client):
    d = client.get("/api/models").json()["defaults"]["category_distribution"]
    assert d == {"1": 2, "2": 2, "3": 3, "4": 3, "5": 0}


def test_validate_endpoint_auto_and_custom(client):
    r = client.post("/api/category-distribution/validate", json={"mode": "auto_balanced"})
    assert r.status_code == 200
    assert r.json()["slots"] == [1, 1, 2, 2, 3, 3, 3, 4, 4, 4]
    assert "design assumption" in r.json()["note"]
    r = client.post("/api/category-distribution/validate",
                    json={"mode": "custom", "counts": {"1": 1, "2": 3, "3": 3, "4": 3}})
    assert r.status_code == 200 and r.json()["counts"]["2"] == 3


def test_validate_endpoint_rejects_bad_custom(client):
    r = client.post("/api/category-distribution/validate",
                    json={"mode": "custom", "counts": {"1": 5, "2": 5, "3": 0, "4": 1}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_total"
    r = client.post("/api/category-distribution/validate",  # only Category 2: allowed, just flagged non-compliant
                    json={"mode": "custom", "counts": {"1": 0, "2": 10, "3": 0, "4": 0}})
    assert r.status_code == 200 and r.json()["counts"]["2"] == 10 and r.json()["compliant"] is False
    # a Cat.5 key is ignored in Custom mode: the golden count comes from gold_cards
    r = client.post("/api/category-distribution/validate",
                    json={"mode": "custom", "counts": {"1": 2, "2": 2, "3": 3, "4": 3, "5": 1}})
    assert r.status_code == 200 and r.json()["counts"]["5"] == 0
