import pytest
from pydantic import ValidationError

from app.services.prompt_compiler import CardSpec, check_wishes, compile_card_prompt


def card(**kw):
    base = dict(collection="Movie Collection", genre="Science Fiction",
                object_name="Space Helmet", category=2)
    base.update(kw)
    return CardSpec(**base)


def test_compiles_three_layers_and_category_rules():
    c = compile_card_prompt(card(), "retro-futuristic, lighthearted")
    assert "Space Helmet" in c.text and "Science Fiction" in c.text
    assert "Category 2" in c.text and "placed on a simple surface" in c.text
    assert "retro-futuristic" in c.layers["C_free_wishes"]
    assert "No text, no labels, no stars, no border" in c.text
    assert c.template_version == "image_base_v0"


def test_category_5_is_accepted_and_bad_category_rejected():
    assert card(category=5).category == 5
    with pytest.raises(ValidationError):
        card(category=6)


@pytest.mark.parametrize("cat", [1, 2, 3, 4])
def test_each_category_has_distinct_rule(cat):
    texts = {compile_card_prompt(card(category=c)).layers["B_settings"] for c in (1, 2, 3, 4)}
    assert len(texts) == 4
    assert f"Category {cat}" in compile_card_prompt(card(category=cat)).text


def test_reference_roles_are_described():
    c = compile_card_prompt(card(), "", ["category_2", "negative"])
    assert "Reference image 1: Category 2 example" in c.text
    assert "NEGATIVE example" in c.text
    assert "Reference guidance (technical, not content)" in c.text
    assert "never override the requested subject" in c.text
    assert "dark canvas" in c.text and "visual metaphor" in c.text


def test_no_reference_block_without_refs():
    assert "Reference guidance" not in compile_card_prompt(card()).text


def test_wishes_conflicts_warn_but_never_change_rules():
    c = compile_card_prompt(card(), "make it with a fancy frame and a text label")
    assert len(c.warnings) >= 2
    assert "no collection window" in c.text  # rules unchanged


def test_negated_wishes_do_not_warn():
    assert check_wishes("no frames, avoid text, no weapons") == []


def test_wishes_limit_is_1500_and_full_text_reaches_the_prompt():
    text = "a" * 1499 + "Z"
    c = compile_card_prompt(card(), text)
    assert text in c.layers["C_free_wishes"] and check_wishes(text) == []  # a 1,500-char brief is used whole
    c = compile_card_prompt(card(), "x" * 2200)
    assert "x" * 1500 in c.layers["C_free_wishes"] and "x" * 1501 not in c.layers["C_free_wishes"]
    assert any("truncated" in w and "1500" in w for w in check_wishes("x" * 1501))


def test_long_wishes_reach_planner_regeneration_and_api_validation(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.services.prompt_compiler import MAX_WISHES
    from app.services.text_planner import TextPlanner
    from tests.test_metaphor_planner import COUNTS, cards_of  # noqa: F401
    from tests.conftest import make_plan

    long = "retro neon mood, " * 85  # ~1,450 characters
    long = long.strip()[:1500]
    assert MAX_WISHES == 1500 and len(long) > 500
    plan = make_plan(1, COUNTS)
    assert long in TextPlanner.build_prompt("Movie", "Detective", 1, COUNTS, long)  # Metaphor Planner
    assert long in TextPlanner.build_regen_prompt("Movie", "Detective", plan, [("A", 1)], long)  # selective regeneration
    with TestClient(create_app(settings)) as c:
        base = dict(collection="Movie", genre="Detective", number_of_cards=10, number_of_variants=1)
        assert c.post("/api/plan/test", json=dict(base, wishes="w" * 1500)).status_code == 200
        assert c.post("/api/plan/test", json=dict(base, wishes="w" * 1501)).status_code == 422
        assert c.post("/api/templates", json={"name": "long", "payload": {"wishes": "w" * 1500}}).status_code in (200, 201)


def test_planner_prompt_keeps_broad_themes_wide_and_bright():
    from app.services.text_planner import BROAD_THEME_RULE, TextPlanner
    p = TextPlanner.build_prompt("Movie", "Comedy", 1, {1: 2, 2: 2, 3: 3, 4: 3}, "")
    assert BROAD_THEME_RULE in p
    assert "no quotas" in p and "not the time of day or lighting" in p


def test_repair_note_limit_is_1000_in_prompt_and_api():
    from app.services.prompt_compiler import MAX_REPAIR_NOTE
    assert MAX_REPAIR_NOTE == 1000
    c = compile_card_prompt(card(), "", None, None, "r" * 1500)
    assert "r" * 1000 in c.text and "r" * 1001 not in c.text
    from app.services.jobs import RegenerateRequest
    from pydantic import ValidationError
    import pytest
    assert RegenerateRequest(note="n" * 1000).note
    with pytest.raises(ValidationError):
        RegenerateRequest(note="n" * 1001)
