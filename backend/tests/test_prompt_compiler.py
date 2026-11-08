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
    assert "without copying" in c.text


def test_no_reference_block_without_refs():
    assert "Visual target" not in compile_card_prompt(card()).text


def test_wishes_conflicts_warn_but_never_change_rules():
    c = compile_card_prompt(card(), "make it with a fancy frame and a text label")
    assert len(c.warnings) >= 2
    assert "no collection window" in c.text  # rules unchanged


def test_negated_wishes_do_not_warn():
    assert check_wishes("no frames, avoid text, no weapons") == []


def test_wishes_truncated_to_500():
    c = compile_card_prompt(card(), "x" * 900)
    assert len(c.layers["C_free_wishes"]) < 700
    assert any("truncated" in w for w in check_wishes("x" * 900))
