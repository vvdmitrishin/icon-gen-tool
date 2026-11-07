"""Card Category Distribution (spec v1.7 §3.4).

Modes: `auto_balanced` (configurable default, initially [2,2,3,3] — our design assumption, not a
Playrix-mandated ratio) and `custom` (producer-set counts). The same rules apply in UI, API and planner:
sum(Cat.1-4) == 10, each of Cat.1-4 >= 1, Cat.5 locked to 0 for the Movie demo. Distribution is
independent of rarity/stars.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .prompt_compiler import _style_bible

CARDS_PER_SET = 10
STANDARD_CATEGORIES = (1, 2, 3, 4)
GOLDEN_CATEGORY = 5


class DistributionError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message, self.code = message, code


class CategoryDistribution(BaseModel):
    mode: Literal["auto_balanced", "custom"] = "auto_balanced"
    counts: dict[int, int] | None = Field(default=None)


def default_counts() -> dict[int, int]:
    raw = _style_bible()["category_distribution_default"]
    return {int(k): int(v) for k, v in raw.items()}


def validate_counts(counts: dict[int, int], *, total: int = CARDS_PER_SET) -> dict[int, int]:
    """Return normalized counts for Cat.1-5, or raise DistributionError."""
    unknown = sorted(k for k in counts if k not in (*STANDARD_CATEGORIES, GOLDEN_CATEGORY))
    if unknown:
        raise DistributionError(f"Unknown categories: {unknown}. Use 1-4 (and 5 = 0).", "unknown_category")
    norm = {c: int(counts.get(c, 0)) for c in (*STANDARD_CATEGORIES, GOLDEN_CATEGORY)}
    if any(v < 0 for v in norm.values()):
        raise DistributionError("Counts cannot be negative.", "negative")
    if norm[GOLDEN_CATEGORY] != 0:
        raise DistributionError(
            "Category 5 (Golden) is locked to 0 for the Movie Collection pilot.", "golden_locked"
        )
    missing = [c for c in STANDARD_CATEGORIES if norm[c] < 1]
    if missing:
        raise DistributionError(
            f"Each of Categories 1-4 needs at least 1 card (missing: {missing}).", "category_empty"
        )
    s = sum(norm[c] for c in STANDARD_CATEGORIES)
    if s != total:
        raise DistributionError(f"Counts must total {total} cards (currently {s}).", "bad_total")
    return norm


def resolve_distribution(dist: CategoryDistribution | None = None) -> dict[int, int]:
    """Auto (Balanced) → configured default; Custom → validated producer counts."""
    dist = dist or CategoryDistribution()
    if dist.mode == "auto_balanced":
        return validate_counts(default_counts())
    if not dist.counts:
        raise DistributionError("Custom mode needs counts for Categories 1-4.", "category_empty")
    return validate_counts(dist.counts)


def assign_slots(counts: dict[int, int]) -> list[int]:
    """Ordered category slot per card (visual progression: simple → environment).
    e.g. [2,2,3,3] → [1,1,2,2,3,3,3,4,4,4]. The same slots apply to every alternative set."""
    norm = validate_counts(counts)
    return [c for c in STANDARD_CATEGORIES for _ in range(norm[c])]
