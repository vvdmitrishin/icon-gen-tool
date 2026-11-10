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
from pydantic import BaseModel, Field

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
TEMPLATE_VERSION = "image_base_v0"
MAX_WISHES = 1500  # Producer's Free Wishes: one limit for the UI, API, templates and every prompt
TEMPLATE_NAMES = ("system_style", "category_1", "category_2", "category_3", "category_4", "category_5",
                  "image_closing", "repair")
# Templates the Producer may override for the current browser session (the repair template stays permanent-only).
SESSION_EDITABLE = ("system_style", "category_1", "category_2", "category_3", "category_4", "category_5", "image_closing")

_ROLE_LABEL = {
    "style": "general style reference",
    "category_1": "Category 1 example (simple floating object)",
    "category_2": "Category 2 example (simple object on a surface)",
    "category_3": "Category 3 example (object on a context-appropriate surface)",
    "category_4": "Category 4 example (object in an environment)",
    "category_5": "Category 5 example (golden card, key characters)",
    "character": "character reference (the key characters of the collection)",
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
    category: Literal[1, 2, 3, 4, 5]  # 5 = golden card (key characters)
    # shared Set context from the planner (art direction for the whole variant; never overrides subject or category rules)
    variant_title: str = Field(default="", max_length=120)
    variant_concept: str = Field(default="", max_length=400)
    rationale: str = Field(default="", max_length=300)


@dataclass
class CompiledPrompt:
    text: str
    template_version: str
    style_bible_version: str
    category_rules_version: str
    layers: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    bundle: dict[str, dict[str, Any]] = field(default_factory=dict)  # name -> {text, source, version, base_version}
    protected: bool = False  # the mandatory-rules block was appended because a session override is active


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
    for n in (1, 2, 3, 4, 5):
        c = cr["categories"][n]
        out[f"category_{n}"] = f"Composition rule: {c['composition']} Background rule: {c['background']}"
    return out


_CONFLICT_PATTERNS = [
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


MANDATORY_RULES = (
    "Mandatory rules (always apply, whatever the style or category text says): artwork only, one coherent illustration; "
    "no text, lettering, labels, captions or watermark; no card frame, border, stars, rarity badges or UI elements."
)
GOLD_RULE = " A golden card shows at most 2 characters."

_OVERRIDE_CONFLICTS = [
    (re.compile(r"\b(?:add|include|show|draw|write|put|with)\s+(?:\w+\s+){0,2}?(?:text|lettering|captions?|labels?|logo|watermark|title)\b", re.I),
     "asks for text/labels in the image"),
    (re.compile(r"\b(?:add|include|show|draw|with)\s+(?:\w+\s+){0,2}?(?:frames?|borders?|stars?|badges?|rarity)\b", re.I),
     "asks for a frame, border, stars or badges"),
]
_MANY_CHARS = re.compile(r"\b(?:[3-9]|\d{2,}|three|four|five|six|several|many|crowd|group of)\s+(?:\w+\s+){0,2}(?:characters?|people|persons|figures)\b", re.I)


def check_overrides(overrides: Mapping[str, str]) -> list[str]:
    """Warnings for session overrides that contradict mandatory rules. The rules are still enforced: the compiler appends
    the MANDATORY block whenever an override is active, and counts/structure are validated server-side, not in prompt text."""
    out: list[str] = []
    for name, text in overrides.items():
        for pattern, what in _OVERRIDE_CONFLICTS:
            if pattern.search(text or ""):
                out.append(f"'{name}' {what}. The mandatory artwork-only rules stay in force and are appended to every prompt.")
                break
        if name in ("category_5", "system_style", "image_closing") and _MANY_CHARS.search(text or ""):
            out.append(f"'{name}' mentions more than 2 characters. A golden card keeps its maximum of 2 characters (enforced by the planner).")
    return out


def _set_context(card: "CardSpec") -> str:
    """Short shared art direction of the variant, so every card of a batch feels like one set."""
    parts = []
    title, concept = card.variant_title.strip(), card.variant_concept.strip()
    if title or concept:
        parts.append(f" Set variant: {title}" + (f" - {concept[:200]}" if concept else "")
                     + " (shared art direction only; it never replaces this subject or the rules).")
    why = card.rationale.strip()
    if why and "AI-planned for Cat" not in why:
        parts.append(f" Why it belongs to the Set: {why[:140]}.")
    return "".join(parts)


def compile_card_prompt(
    card: CardSpec,
    wishes: str = "",
    reference_roles: list[str] | None = None,
    templates: Mapping[str, str] | None = None,
    repair_note: str | None = None,
    protected: bool = False,
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
    if card.category == 5:
        layer_b = (
            f"Task: Generate ONE standalone square collectible-card ARTWORK (not a card frame): a GOLDEN card. "
            f"Scene: {obj}. Collection: {card.collection}. Set theme: {card.genre}.{_set_context(card)} "
            f"Category 5 ({cat['name']}). {tpl['category_5']} "
            "Rules: show the collection's KEY CHARACTERS (original designs) in an ironic, emotional situation; "
            "max 2 characters on this golden card; no text, lettering, speech bubbles or frames."
        )
    else:
        layer_b = (
            f"Task: Generate ONE standalone square collectible-card ARTWORK (not a card frame). "
            f"Object: {obj}. Collection: {card.collection}. Set theme: {card.genre}.{_set_context(card)} "
            f"Category {card.category} ({cat['name']}). {tpl[f'category_{card.category}']}"
        )
    layer_c = f"Producer wishes (creative guidance only, never overrides the rules above): {wishes}" if wishes else ""

    refs_block = ""
    if reference_roles:
        lines = [f"Reference image {i + 1}: {_ROLE_LABEL.get(r, 'reference')}"
                 for i, r in enumerate(reference_roles)]
        refs_block = (
            "Reference guidance (technical, not content): the attached image(s), together with their category, show how a "
            "planned concept is turned into a collectible illustration at that category's complexity: how a thematic idea "
            "becomes a recognizable visual metaphor, the abstraction and simplification, composition, object prominence and "
            "readability, the balance of object and scene, stylized geometry and proportions, rendering and material "
            "treatment, soft shading and lighting principles, overall colorfulness and consistency across a collection. "
            "Learn those principles; do not copy the references literally: not their objects or subjects, exact color "
            "combinations, backgrounds, locations or settings, character designs or scenes, nor the sheet layout, its "
            "margins and dark canvas. The Collection, Set and requested subject decide WHAT is drawn; the Style Bible, "
            "category rule and references guide HOW. Create an original interpretation of THIS subject and choose its "
            "colors, lighting and rendering details yourself, keeping the artistic identity the references demonstrate. "
            "References never override the requested subject, the Collection and Set, the style direction or the category "
            "rule above. " + "; ".join(lines) + "."
            + (" Images labelled NEGATIVE show what to AVOID: never reproduce them." if "negative" in reference_roles else "")
        )

    repair_block = ""
    if repair_note and repair_note.strip():
        note = repair_note.strip()[:300]
        try:
            repair_block = tpl["repair"].format(note=note, object_name=card.object_name)
        except (KeyError, IndexError, ValueError):  # a malformed edited template must not break generation
            repair_block = defaults["repair"].format(note=note, object_name=card.object_name)

    mandatory = (MANDATORY_RULES + (GOLD_RULE if card.category == 5 else "")) if protected else ""
    text = "\n\n".join(p for p in (layer_b, layer_a, layer_c, refs_block, repair_block, tpl["image_closing"], mandatory) if p)
    return CompiledPrompt(
        text=text,
        template_version=TEMPLATE_VERSION,
        style_bible_version=sb["version"],
        category_rules_version=cr["version"],
        layers={"A_style_bible": layer_a, "B_settings": layer_b, "C_free_wishes": layer_c},
        warnings=warnings,
        protected=protected,
    )
