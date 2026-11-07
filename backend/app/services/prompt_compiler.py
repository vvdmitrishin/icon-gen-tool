"""Prompt Compiler: Layer A (Style Bible) + Layer B (settings/CardSpec) + Layer C (Free Wishes)
→ effective image prompt (spec §0.4, §11.3). Precedence A > B > C: wishes never override rules.

The Prompt Library (versioned templates) can override the editable text blocks; with no library the
built-in defaults below produce exactly the same prompt as before.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml
from pydantic import BaseModel, Field, field_validator

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
TEMPLATE_VERSION = "image_base_v0"
MAX_WISHES = 500
TEMPLATE_NAMES = ("system_style", "category_1", "category_2", "category_3", "category_4",
                  "image_closing", "repair")

_ROLE_LABEL = {
    "style": "general style reference",
    "category_1": "Category 1 example (simple floating object)",
    "category_2": "Category 2 example (simple object on a surface)",
    "category_3": "Category 3 example (object on a realistic surface)",
    "category_4": "Category 4 example (object in an environment)",
    "composition": "composition reference",
    "negative": "NEGATIVE example (what to avoid)",
}

DEFAULT_CLOSING = (
    "The object must be readable at small size, central and unobstructed. "
    "No text, no labels, no stars, no border, no collection window, no watermark. "
    "One coherent illustration."
)
DEFAULT_REPAIR = (
    "Regenerate the SAME concept with these corrections: {note} "
    "Keep: {object_name}, the set art direction and the approved style reference. "
    "Do not add: frame, labels, stars, extra foreground items."
)


class CardSpec(BaseModel):
    collection: str = Field(min_length=1, max_length=80)
    genre: str = Field(min_length=1, max_length=80)
    object_name: str = Field(min_length=1, max_length=80)
    object_description: str = Field(default="", max_length=300)
    category: Literal[1, 2, 3, 4]

    @field_validator("category", mode="before")
    @classmethod
    def _no_golden(cls, v: Any) -> Any:
        if v == 5:
            raise ValueError("Category 5 (golden) is not generated in the Movie demo")
        return v


@dataclass
class CompiledPrompt:
    text: str
    template_version: str
    style_bible_version: str
    category_rules_version: str
    layers: dict[str, str]
    warnings: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def _style_bible() -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / "style_bible.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _category_rules() -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / "category_rules.yaml").read_text(encoding="utf-8"))


def default_templates() -> dict[str, str]:
    """Built-in text of every editable Prompt Library template (seed for version 1)."""
    sb, cr = _style_bible(), _category_rules()
    out = {
        "system_style": (
            f"Style: {sb['style_description']}. "
            + " ".join(f"{k.replace('_', ' ').capitalize()}: {v}." for k, v in sb["visual_rules"].items())
            + " Avoid: " + "; ".join(sb["negative_rules"]) + "."
        ),
        "image_closing": DEFAULT_CLOSING,
        "repair": DEFAULT_REPAIR,
    }
    for n in (1, 2, 3, 4):
        c = cr["categories"][n]
        out[f"category_{n}"] = f"Composition rule: {c['composition']} Background rule: {c['background']}"
    return out


_CONFLICT_PATTERNS = [
    (re.compile(r"\bgold(en)?\b", re.I),
     "Golden cards are disabled for this run; 'golden' wishes are ignored."),
    (re.compile(r"\b(frame|border|badge|stars?)\b", re.I),
     "Artwork-only rule: frames, borders, stars and badges are never drawn; those wishes are ignored."),
    (re.compile(r"\b(text|label|caption|logo|watermark)\b", re.I),
     "Artwork-only rule: no text, labels, logos or watermarks; those wishes are ignored."),
]


def check_wishes(wishes: str) -> list[str]:
    warnings = []
    if len(wishes) > MAX_WISHES:
        warnings.append(f"Free Wishes longer than {MAX_WISHES} characters will be truncated.")
    for pattern, msg in _CONFLICT_PATTERNS:
        for m in pattern.finditer(wishes):
            lead = wishes[max(0, m.start() - 16): m.start()].lower()
            if re.search(r"\b(no|avoid|without|never|not)\s+(\w+\s+)?$", lead):
                continue  # explicitly negated ("no frames") is fine
            warnings.append(msg)
            break
    return warnings


def compile_card_prompt(
    card: CardSpec,
    wishes: str = "",
    reference_roles: list[str] | None = None,
    templates: Mapping[str, str] | None = None,
    repair_note: str | None = None,
) -> CompiledPrompt:
    sb = _style_bible()
    cr = _category_rules()
    defaults = default_templates()
    tpl = {**defaults, **{k: v for k, v in (templates or {}).items() if k in defaults and v.strip()}}
    cat = cr["categories"][card.category]
    wishes = (wishes or "").strip()[:MAX_WISHES]
    warnings = check_wishes(wishes)

    layer_a = tpl["system_style"]
    obj = card.object_name + (f" — {card.object_description}" if card.object_description else "")
    layer_b = (
        f"Task: Generate ONE standalone square collectible-card ARTWORK (not a card frame). "
        f"Object: {obj}. Collection: {card.collection}. Set theme: {card.genre}. "
        f"Category {card.category} ({cat['name']}). {tpl[f'category_{card.category}']}"
    )
    layer_c = f"Producer wishes (creative guidance only, never overrides the rules above): {wishes}" if wishes else ""

    refs_block = ""
    if reference_roles:
        lines = [f"Reference image {i + 1}: {_ROLE_LABEL.get(r, 'reference')}"
                 for i, r in enumerate(reference_roles)]
        refs_block = (
            "Visual target: match the rendering style, material treatment, lighting, color richness "
            "and level of detail of the attached reference image(s), without copying their exact "
            "objects or composition. " + "; ".join(lines) + "."
        )

    repair_block = ""
    if repair_note and repair_note.strip():
        note = repair_note.strip()[:300]
        try:
            repair_block = tpl["repair"].format(note=note, object_name=card.object_name)
        except (KeyError, IndexError, ValueError):  # a malformed edited template must not break generation
            repair_block = defaults["repair"].format(note=note, object_name=card.object_name)

    text = "\n\n".join(p for p in (layer_b, layer_a, layer_c, refs_block, repair_block, tpl["image_closing"]) if p)
    return CompiledPrompt(
        text=text,
        template_version=TEMPLATE_VERSION,
        style_bible_version=sb["version"],
        category_rules_version=cr["version"],
        layers={"A_style_bible": layer_a, "B_settings": layer_b, "C_free_wishes": layer_c},
        warnings=warnings,
    )
