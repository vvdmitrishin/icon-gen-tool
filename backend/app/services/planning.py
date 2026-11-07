"""RulesPlanner (MVP set planner, spec §8.4, §9 Stage 2-3, §15).

Deterministic and seeded. It assigns objects from curated per-genre pools to category slots according
to the Card Category Distribution (spec §3.4) BEFORE any image is generated, and produces 1-5 distinct
variants (one micro-theme each). It does NOT interpret free-form wishes: those reach the image prompt
only (Layer C). Genres outside the curated pools need a real TextPlanner, which is not configured.
"""
from __future__ import annotations

import random
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .category_distribution import CARDS_PER_SET, STANDARD_CATEGORIES, assign_slots

POOLS_PATH = Path(__file__).resolve().parent.parent / "config" / "object_pools.yaml"
MAX_VARIANTS = 5
VARIANT_IDS = "ABCDE"


class PlanningError(Exception):
    def __init__(self, message: str, code: str = "planning_error") -> None:
        super().__init__(message)
        self.message, self.code = message, code


class PlanCard(BaseModel):
    card_id: str = ""
    position: int = Field(ge=1, le=CARDS_PER_SET)
    category: int
    object_name: str
    object_description: str
    rationale: str
    borrowed: bool = False  # object came from another micro-theme because the own pool ran short


class PlanVariant(BaseModel):
    variant_id: str
    index: int
    title: str
    concept: str
    cards: list[PlanCard]


class SetPlan(BaseModel):
    genre: str
    seed: int
    planning_mode: str = "rules"
    distribution: dict[int, int]
    pools_version: str
    variants: list[PlanVariant]
    notes: list[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def _pools() -> dict[str, Any]:
    return yaml.safe_load(POOLS_PATH.read_text(encoding="utf-8"))


def supported_genres() -> list[dict[str, Any]]:
    return [
        {"genre": g, "microthemes": [m["title"] for m in d["microthemes"]], "max_variants": len(d["microthemes"])}
        for g, d in _pools()["genres"].items()
    ]


def resolve_genre(name: str) -> str:
    key = (name or "").strip().lower()
    for genre, data in _pools()["genres"].items():
        if key == genre.lower() or key in [a.lower() for a in data.get("aliases", [])]:
            return genre
    raise PlanningError(
        f"The rules planner only knows curated genres: {', '.join(_pools()['genres'])}. "
        f"'{name}' needs a TextPlanner, which is not configured.",
        "unknown_genre",
    )


def default_seed(genre: str, variants: int) -> int:
    """Stable seed so the preview equals the created job until the producer shuffles."""
    return zlib.crc32(f"{genre.lower()}|{variants}".encode()) & 0x7FFFFFFF


def _entry(raw: str) -> tuple[str, str]:
    name, _, desc = raw.partition(" | ")
    return name.strip(), desc.strip()


# group preference order per category slot
_PREFS = {1: ("simple",), 2: ("simple",), 3: ("scene3", "simple", "scene4"), 4: ("scene4", "scene3", "simple")}


def plan_set(genre_name: str, variants: int, counts: dict[int, int], seed: int | None = None) -> SetPlan:
    genre = resolve_genre(genre_name)
    pools = _pools()["genres"][genre]["microthemes"]
    if not 1 <= variants <= min(MAX_VARIANTS, len(pools)):
        raise PlanningError(
            f"This genre supports 1-{min(MAX_VARIANTS, len(pools))} distinct variants.", "bad_variant_count"
        )
    slots = assign_slots(counts)
    seed = default_seed(genre, variants) if seed is None else seed
    rng = random.Random(seed)
    themes = rng.sample(pools, variants)
    used_global: set[str] = set()
    built: list[PlanVariant] = []

    for vi, theme in enumerate(themes):
        used_local: set[str] = set()

        def candidates(group: str, source: dict[str, Any]) -> list[tuple[str, str]]:
            items = [_entry(r) for r in source.get(group, [])]
            rng.shuffle(items)
            return [e for e in items if e[0] not in used_global and e[0] not in used_local]

        cards: list[PlanCard] = []
        for pos, cat in enumerate(slots, 1):
            picked, borrowed = None, False
            for group in _PREFS[cat]:
                c = candidates(group, theme)
                if c:
                    picked = c[0]
                    break
            if picked is None:  # borrow from other micro-themes of the same genre
                for other in pools:
                    if other is theme:
                        continue
                    for group in _PREFS[cat]:
                        c = candidates(group, other)
                        if c:
                            picked, borrowed = c[0], True
                            break
                    if picked:
                        break
            if picked is None:
                raise PlanningError(
                    f"The curated object pool for '{genre}' is too small for this distribution with {variants} variant(s). "
                    "Use fewer variants or move cards from Cat. 1-2 (simple objects) to Cat. 3-4.",
                    "pool_exhausted",
                )
            name, desc = picked
            used_local.add(name)
            used_global.add(name)
            cards.append(
                PlanCard(
                    position=pos, category=cat, object_name=name, object_description=desc,
                    borrowed=borrowed,
                    rationale=f"{theme['title']}: {'borrowed ' if borrowed else ''}fits Cat. {cat} composition",
                )
            )
        built.append(PlanVariant(variant_id=VARIANT_IDS[vi], index=vi, title=theme["title"],
                                 concept=theme["concept"], cards=cards))

    notes = ["Rules planner: objects come from curated pools; free wishes affect image prompts only."]
    if any(c.borrowed for v in built for c in v.cards):
        notes.append("Some objects were borrowed from other micro-themes because the distribution needs more of one kind.")
    return SetPlan(genre=genre, seed=seed, distribution=dict(counts), pools_version=_pools()["version"],
                   variants=built, notes=notes)


def validate_plan(plan: SetPlan, counts: dict[int, int]) -> list[str]:
    """PLAN_QA (spec Stage 3): deterministic hard rules. Empty list = valid."""
    issues: list[str] = []
    seen_global: dict[str, str] = {}
    for v in plan.variants:
        if len(v.cards) != CARDS_PER_SET:
            issues.append(f"Variant {v.variant_id}: expected {CARDS_PER_SET} cards, got {len(v.cards)}.")
        names = [c.object_name.strip().lower() for c in v.cards]
        if len(set(names)) != len(names):
            issues.append(f"Variant {v.variant_id}: duplicate object names.")
        got = {c: 0 for c in STANDARD_CATEGORIES}
        for c in v.cards:
            if c.category == 5:
                issues.append(f"Variant {v.variant_id}: golden Category 5 is not allowed.")
            elif c.category in got:
                got[c.category] += 1
            else:
                issues.append(f"Variant {v.variant_id}: invalid category {c.category}.")
            if not (c.object_name.strip() and c.object_description.strip()):
                issues.append(f"Variant {v.variant_id} card {c.position}: missing name or description.")
            prev = seen_global.get(c.object_name.strip().lower())
            if prev and prev != v.variant_id:
                issues.append(f"'{c.object_name}' appears in variants {prev} and {v.variant_id}.")
            seen_global[c.object_name.strip().lower()] = v.variant_id
        want = {c: counts.get(c, 0) for c in STANDARD_CATEGORIES}
        if got != want:
            issues.append(f"Variant {v.variant_id}: category counts {got} differ from requested {want}.")
    return issues
