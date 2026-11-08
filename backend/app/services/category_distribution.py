"""Card Category Distribution (spec v1.7 §3.4, extended for variable set size + golden cards).

A set has `number_of_cards` (1..10, 10 = golden standard) cards, of which `gold_cards` (0..10) are golden
(Category 5, the collection's KEY CHARACTERS). The remaining `standard = number_of_cards - gold_cards` cards are
spread over Categories 1-4.

Modes: `auto_balanced` (proportions of the configurable default [2,2,3,3] - our design assumption, not a
Playrix-mandated ratio) and `custom` (producer-set Cat.1-4 counts; a Cat.5 key is ignored because the golden
count always comes from `gold_cards`).

Rules (same in UI, API and planner):
  * sum(Cat.1-4) == standard count;
  * if standard >= 4 every one of Cat.1-4 needs >= 1 card;
  * if standard is 1..3 categories may be 0, and Auto picks the FIRST categories in order 1, 2, 3
    (1 card -> Cat.1; 2 -> Cat.1+2; 3 -> Cat.1+2+3);
  * standard == 0 is allowed (an all-golden set).
Distribution is independent of rarity/stars. Slot order: Cat.1..4 ascending, golden (Cat.5) cards LAST.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .prompt_compiler import _style_bible

SET_STANDARD = "standard"
SET_GRAND = "grand"  # Grand Set (All Golden): the collection's finale set, every card is golden (Cat.5), Cat.1-4 = 0
SetType = Literal["standard", "grand"]
GRAND_NOTE = "Grand Set (All Golden): finale set, every card is a golden Category 5 card; an explicit exception to the four-category coverage rule."

CARDS_PER_SET = 10  # default and maximum
MIN_CARDS = 1
STANDARD_CATEGORIES = (1, 2, 3, 4)
GOLDEN_CATEGORY = 5
ALL_CATEGORIES = (*STANDARD_CATEGORIES, GOLDEN_CATEGORY)


class DistributionError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message, self.code = message, code


class CategoryDistribution(BaseModel):
    mode: Literal["auto_balanced", "custom"] = "auto_balanced"
    counts: dict[int, int] | None = Field(default=None)
    # only used by POST /api/category-distribution/validate (jobs carry these as top-level fields)
    number_of_cards: int = Field(default=CARDS_PER_SET, ge=MIN_CARDS, le=CARDS_PER_SET)
    gold_cards: int = Field(default=0, ge=0, le=CARDS_PER_SET)
    set_type: Literal["standard", "grand"] = SET_STANDARD


def default_counts() -> dict[int, int]:
    raw = _style_bible()["category_distribution_default"]
    return {int(k): int(v) for k, v in raw.items()}


def check_sizes(number_of_cards: int, gold_cards: int) -> int:
    """Validate set size / golden count and return the standard (Cat.1-4) card count."""
    if not MIN_CARDS <= number_of_cards <= CARDS_PER_SET:
        raise DistributionError(f"Number of cards must be {MIN_CARDS}-{CARDS_PER_SET}.", "bad_number_of_cards")
    if not 0 <= gold_cards <= CARDS_PER_SET:
        raise DistributionError(f"Golden cards must be 0-{CARDS_PER_SET}.", "bad_gold_count")
    if gold_cards > number_of_cards:
        raise DistributionError(
            f"Golden cards ({gold_cards}) cannot exceed the number of cards ({number_of_cards}).", "bad_gold_count"
        )
    return number_of_cards - gold_cards


def balanced_counts(standard: int) -> dict[int, int]:
    """Auto (Balanced) split of `standard` cards over Cat.1-4 (largest remainder toward the default proportions).
    10 -> [2,2,3,3]; 1..3 -> first categories in order 1,2,3; n>=4 -> min 1 each."""
    if standard <= 0:
        return {c: 0 for c in STANDARD_CATEGORIES}
    if standard < 4:
        return {c: (1 if c <= standard else 0) for c in STANDARD_CATEGORIES}
    base = default_counts()
    weights = {c: base[c] for c in STANDARD_CATEGORIES}
    wsum = sum(weights.values()) or 1
    exact = {c: standard * weights[c] / wsum for c in STANDARD_CATEGORIES}
    counts = {c: max(1, int(exact[c])) for c in STANDARD_CATEGORIES}
    diff = standard - sum(counts.values())
    if diff > 0:
        order = sorted(STANDARD_CATEGORIES, key=lambda c: (exact[c] - int(exact[c]), c), reverse=True)
        for i in range(diff):
            counts[order[i % 4]] += 1
    while diff < 0:
        c = max((c for c in STANDARD_CATEGORIES if counts[c] > 1), key=lambda c: (counts[c], c))
        counts[c] -= 1
        diff += 1
    return counts


def validate_counts(counts: dict[int, int], *, number_of_cards: int = CARDS_PER_SET,
                    gold_cards: int = 0) -> dict[int, int]:
    """Return normalized counts for Cat.1-5 (Cat.5 == gold_cards), or raise DistributionError.
    `counts` carries Cat.1-4; a Cat.5 key is accepted but ignored."""
    standard = check_sizes(number_of_cards, gold_cards)
    unknown = sorted(k for k in counts if k not in ALL_CATEGORIES)
    if unknown:
        raise DistributionError(f"Unknown categories: {unknown}. Use 1-4 (golden count comes from gold_cards).",
                                "unknown_category")
    norm = {c: int(counts.get(c, 0)) for c in STANDARD_CATEGORIES}
    if any(v < 0 for v in norm.values()):
        raise DistributionError("Counts cannot be negative.", "negative")
    if standard >= 4:
        missing = [c for c in STANDARD_CATEGORIES if norm[c] < 1]
        if missing:
            raise DistributionError(
                f"Each of Categories 1-4 needs at least 1 card (missing: {missing}).", "category_empty"
            )
    s = sum(norm.values())
    if s != standard:
        raise DistributionError(
            f"Categories 1-4 must total {standard} cards ({number_of_cards} cards - {gold_cards} golden); currently {s}.",
            "bad_total",
        )
    norm[GOLDEN_CATEGORY] = gold_cards
    return norm


def grand_counts(number_of_cards: int) -> dict[int, int]:
    """Grand Set distribution: Cat.1-4 = 0, every card golden."""
    check_sizes(number_of_cards, number_of_cards)
    return {**{c: 0 for c in STANDARD_CATEGORIES}, GOLDEN_CATEGORY: number_of_cards}


def resolve_distribution(dist: CategoryDistribution | None = None, *, number_of_cards: int | None = None,
                         gold_cards: int | None = None, set_type: str = SET_STANDARD) -> dict[int, int]:
    """Auto (Balanced) -> proportional split; Custom -> validated producer counts. Returns {1..5: n}.
    Grand Set -> Cat.1-4 = 0 and gold = number_of_cards, whatever the distribution mode says."""
    dist = dist or CategoryDistribution()
    if set_type == SET_GRAND:
        return grand_counts(number_of_cards if number_of_cards is not None else dist.number_of_cards)
    n = number_of_cards if number_of_cards is not None else dist.number_of_cards
    g = gold_cards if gold_cards is not None else dist.gold_cards
    standard = check_sizes(n, g)
    if dist.mode == "auto_balanced":
        return validate_counts(balanced_counts(standard), number_of_cards=n, gold_cards=g)
    if not dist.counts:
        raise DistributionError("Custom mode needs counts for Categories 1-4.", "category_empty")
    return validate_counts(dist.counts, number_of_cards=n, gold_cards=g)


def assign_slots(counts: dict[int, int]) -> list[int]:
    """Ordered category slot per card (visual progression: simple -> environment, golden last).
    e.g. [2,2,3,3] -> [1,1,2,2,3,3,3,4,4,4]; with 2 golden -> ... + [5,5]. The same slots apply to every alternative set."""
    gold = int(counts.get(GOLDEN_CATEGORY, 0))
    standard = sum(int(counts.get(c, 0)) for c in STANDARD_CATEGORIES)
    norm = validate_counts(counts, number_of_cards=standard + gold, gold_cards=gold)
    return [c for c in ALL_CATEGORIES for _ in range(norm[c])]


def compliance(counts: dict[int, int], set_type: str = SET_STANDARD) -> tuple[bool, str | None]:
    """Playrix-style coverage: compliant == at least 4 STANDARD cards and every one of Cat.1-4 has >= 1 card.
    Non-compliant configurations stay allowed; this only reports (never alters) them.
    A Grand Set is the explicit exception: it reports compliant with a 'Grand Set' note."""
    if set_type == SET_GRAND:
        return True, GRAND_NOTE
    std = {c: int(counts.get(c, 0)) for c in STANDARD_CATEGORIES}
    total = sum(std.values())
    if total < 4:
        return False, "Custom configuration: fewer than 4 standard cards cannot cover all four categories."
    empty = [c for c in STANDARD_CATEGORIES if std[c] < 1]
    if empty:
        names = " and ".join(f"Category {c}" for c in empty)
        return False, f"Custom configuration: {names} has no cards." if len(empty) == 1 else             f"Custom configuration: {names} have no cards."
    return True, None


def final_eligibility(number_of_cards: int, gold_cards: int, set_type: str = SET_STANDARD) -> tuple[bool, str | None]:
    """Movie Collection final bundle takes only full 10-card standard sets (no golden cards)."""
    if set_type == SET_GRAND:
        return False, "A Grand Set (All Golden) is a separate finale set; it is not part of the Movie Collection final bundle."
    if gold_cards > 0:
        return False, "Golden cards are excluded from the Movie Collection final bundle (needs a set with 0 golden cards)."
    if number_of_cards != CARDS_PER_SET:
        return False, f"The final bundle needs full {CARDS_PER_SET}-card standard sets; this set has {number_of_cards}."
    return True, None
