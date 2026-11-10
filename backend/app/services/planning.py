"""Plan data model, shared validation and the Movie Collection genre list.

There is ONE planning mechanism: the AI semantic planner (text_planner.TextPlanner). The former curated object pools and
the deterministic rules planner were removed from the production path; deterministic plans now exist only as test fixtures
(backend/tests/conftest.py). Plans are validated here (validate_plan) before any image is generated.
"""
from __future__ import annotations

import zlib
from typing import Any

from pydantic import BaseModel, Field

from .category_distribution import ALL_CATEGORIES, CARDS_PER_SET, GOLDEN_CATEGORY

MAX_VARIANTS = 5
VARIANT_IDS = "ABCDE"


MAX_GOLD_CHARACTERS = 2  # Playrix requirement: at most 2 characters per golden card


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
    characters: int | None = None  # golden cards planned by AI: number of characters in the scene (1 or 2)
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


# The three Movie Collection deliverable genres (Playrix assignment). They are only names: used as form suggestions and by the
# Movie Collection export workflow. They carry no object pools and do not select a different planner.
MOVIE_DELIVERABLE_GENRES = ("Science Fiction", "Detective", "Fantasy")


def supported_genres() -> list[dict[str, Any]]:
    return [{"genre": g, "max_variants": MAX_VARIANTS} for g in MOVIE_DELIVERABLE_GENRES]


def default_seed(genre: str, variants: int) -> int:
    """Stable seed so the preview equals the created job until the producer shuffles."""
    return zlib.crc32(f"{genre.lower()}|{variants}".encode()) & 0x7FFFFFFF


def validate_plan(plan: SetPlan, counts: dict[int, int]) -> list[str]:
    """PLAN_QA (spec Stage 3): deterministic hard rules. Empty list = valid.
    `counts` = resolved {1..5: n}; the expected cards per variant is its sum."""
    issues: list[str] = []
    seen_global: dict[str, str] = {}
    total = sum(counts.get(c, 0) for c in ALL_CATEGORIES)
    for v in plan.variants:
        if len(v.cards) != total:
            issues.append(f"Variant {v.variant_id}: expected {total} cards, got {len(v.cards)}.")
        names = [c.object_name.strip().lower() for c in v.cards]
        if len(set(names)) != len(names):
            issues.append(f"Variant {v.variant_id}: duplicate object names.")
        got = {c: 0 for c in ALL_CATEGORIES}
        for c in v.cards:
            if c.category in got:
                got[c.category] += 1
            else:
                issues.append(f"Variant {v.variant_id}: invalid category {c.category}.")
            if c.characters is not None and not 1 <= c.characters <= MAX_GOLD_CHARACTERS:
                issues.append(f"Variant {v.variant_id} card {c.position}: {c.characters} characters; a golden card has 1-{MAX_GOLD_CHARACTERS}.")
            if c.category == GOLDEN_CATEGORY and plan.planning_mode == "ai" and c.characters is None:
                issues.append(f"Variant {v.variant_id} card {c.position}: golden card without a character count (1-{MAX_GOLD_CHARACTERS}).")
            if not (c.object_name.strip() and c.object_description.strip()):
                issues.append(f"Variant {v.variant_id} card {c.position}: missing name or description.")
            prev = seen_global.get(c.object_name.strip().lower())
            if prev and prev != v.variant_id:
                issues.append(f"'{c.object_name}' appears in variants {prev} and {v.variant_id}.")
            seen_global[c.object_name.strip().lower()] = v.variant_id
        want = {c: counts.get(c, 0) for c in ALL_CATEGORIES}
        if got != want:
            issues.append(f"Variant {v.variant_id}: category counts {got} differ from requested {want}.")
    return issues
