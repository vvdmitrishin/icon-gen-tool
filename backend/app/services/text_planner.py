"""TextPlanner: semantic AI set planner for ANY Collection / Set (the curated RulesPlanner only covers the Movie presets).

ONE Gemini TEXT call (structured JSON output) analyses the meaning of Collection + Set and returns, per variant, a
micro-theme and the cards (object name + description per category slot). The answer is validated strictly (pydantic +
the same validate_plan as the rules planner); an invalid answer raises PlanningError - there is NO automatic retry, so a
plan never costs more than one tiny text call. Identical inputs (preview then create) reuse the cached plan.
"""
from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from pydantic import BaseModel, Field, ValidationError

from ..providers.model_registry import ModelRegistry
from .category_distribution import ALL_CATEGORIES, GOLDEN_CATEGORY, assign_slots
from .semantic_planning import forbidden_terms, near_duplicates, similar_concepts, similar_name, violates
from .planning import VARIANT_IDS, PlanCard, PlanningError, PlanVariant, SetPlan, default_seed, validate_plan

# Gemini API list prices (standard tier, USD per 1M tokens: input, output INCLUDING thinking tokens), checked 2026-11 against
# https://ai.google.dev/gemini-api/docs/pricing . A local estimate for the budget guard and the UI, not an invoice.
TEXT_PRICE_PER_M: dict[str, tuple[float, float]] = {"gemini-3.5-flash-lite": (0.30, 2.50)}
DEFAULT_TEXT_PRICE_PER_M = (0.30, 2.50)  # unknown / overridden model: assume the same list price
PLAN_INPUT_TOKENS = 2100          # system text + instructions + wishes + headroom for up to ~48 history lines
PLAN_OUTPUT_TOKENS_PER_CARD = 110  # name + description + rationale (+ characters) as JSON
PLAN_THINKING_HEADROOM_TOKENS = 300  # thinking tokens are billed as output (3.5 Flash-Lite thinks "minimal" by default)


def plan_cost_params(model: str) -> dict[str, Any]:
    """Pricing constants of one planning call: cost = base_usd + per_card_usd * variants * cards_per_variant."""
    in_p, out_p = TEXT_PRICE_PER_M.get(model, DEFAULT_TEXT_PRICE_PER_M)
    return {"text_model": model, "input_usd_per_m": in_p, "output_usd_per_m": out_p,
            "base_usd": round((PLAN_INPUT_TOKENS * in_p + PLAN_THINKING_HEADROOM_TOKENS * out_p) / 1e6, 6),
            "per_card_usd": round(PLAN_OUTPUT_TOKENS_PER_CARD * out_p / 1e6, 6)}


def estimate_plan_cost_usd(model: str, variants: int, cards_per_variant: int) -> float:
    """Local estimate of one AI planning call for this model and set size (same formula in Plan Only and Generate)."""
    p = plan_cost_params(model)
    return round(p["base_usd"] + p["per_card_usd"] * max(1, variants) * max(1, cards_per_variant), 5)


CACHE_SIZE = 32

CATEGORY_MEANINGS = {
    1: "Category 1 - a SIMPLE object floating in the air, no small details, no ground",
    2: "Category 2 - a simple object standing on a simple surface",
    3: "Category 3 - one collectible subject on a context-appropriate physical surface, with only minimal surroundings",
    4: "Category 4 - ONE recognizable hero subject (an object, a structure or a contained place-like feature that names the "
       "card) inside a simplified recognizable environment that tells a small story; the environment gives context and mood, "
       "the hero subject stays visually dominant",
    5: "Category 5 - GOLDEN card: the collection's KEY CHARACTERS (at most 2 characters) in an ironic, emotional situation",
}

_SYSTEM = (
    "You are the semantic set planner of a casual mobile game's collectible-card art pipeline. A COLLECTION is a broad "
    "theme (Winter, Summer, Technology, Movies); a SET is a specific sub-theme inside it (Forest, Music Festival, "
    "Two-Wheeled Vehicles, Science Fiction). The SET is the semantic boundary: every card must meaningfully belong to "
    "the Set, not merely to the broader Collection. Each card is one illustrated subject. You plan WHICH subjects are "
    "illustrated; you do not draw. Be concrete, visual and family-friendly: no weapons, no violence, no real brands, no "
    "existing franchise names or copyrighted characters, nothing that needs lettering in the image. The cards of a set must "
    "together represent the whole WORLD of the Set, not ten variations of its most stereotypical objects. "
    "Write all names and descriptions in English."
)

_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "propertyOrdering": ["obvious_ideas", "theme_map", "wishes_applied", "variants"],  # clichés and facets are written BEFORE the cards
    "properties": {
        "obvious_ideas": {"type": "ARRAY", "items": {"type": "STRING"}},  # the 6 most stereotypical subjects (to be limited)
        "theme_map": {"type": "ARRAY", "items": {"type": "STRING"}},  # 6-10 distinct facets of the Set's world
        "wishes_applied": {"type": "STRING"},  # one sentence: how the Producer's wishes were used
        "variants": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "concept": {"type": "STRING"},
                    "cards": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "propertyOrdering": ["position", "category", "aspect", "object_name", "object_description",
                                                 "characters", "rationale"],
                            "properties": {
                                "position": {"type": "INTEGER"},
                                "category": {"type": "INTEGER"},
                                "aspect": {"type": "STRING"},  # which facet of theme_map this card expresses
                                "object_name": {"type": "STRING"},
                                "object_description": {"type": "STRING"},
                                "characters": {"type": "INTEGER"},  # golden cards only: 1 or 2
                                "rationale": {"type": "STRING"},  # one short phrase: why it belongs to THIS Set
                            },
                            "required": ["position", "category", "object_name", "object_description"],
                        },
                    },
                },
                "required": ["title", "concept", "cards"],
            },
        }
    },
    "required": ["theme_map", "variants"],
}


class _AICard(BaseModel):
    position: int = 0
    category: int
    aspect: str = ""  # facet of the Set's world the card expresses (prompting aid; not stored)
    object_name: str = Field(min_length=1, max_length=80)
    object_description: str = Field(min_length=1)
    characters: int | None = None  # required for golden cards (validated in _build / validate_plan)
    rationale: str = ""  # optional short "why it belongs to this Set" (passed concisely into the image prompt)


_VARIANT_ID_RE = re.compile(r"(?:VARIANT\s*[:#_-]?\s*)?([A-E])")


def normalize_variant_id(raw: Any) -> str:
    """'A', 'a', 'Variant A', 'VARIANT A', 'variant-a' -> 'A'. Anything else is returned upper-cased and unchanged, so it still
    fails the strict match against the selected cards (card numbers and categories are never normalized or guessed)."""
    s = str(raw).strip().strip("\"'` .").upper()
    m = _VARIANT_ID_RE.fullmatch(s)
    return m.group(1) if m else s


class _AIRegenCard(BaseModel):
    variant_id: str = Field(min_length=1)
    position: int
    object_name: str = Field(min_length=1, max_length=80)
    object_description: str = Field(min_length=1)
    characters: int | None = None
    rationale: str = ""


class _AIRegen(BaseModel):
    replacements: list[_AIRegenCard]


_REGEN_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {"replacements": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "variant_id": {"type": "STRING"}, "position": {"type": "INTEGER"}, "object_name": {"type": "STRING"},
        "object_description": {"type": "STRING"}, "characters": {"type": "INTEGER"}, "rationale": {"type": "STRING"}},
        "required": ["variant_id", "position", "object_name", "object_description"]}}},
    "required": ["replacements"],
}


class _AIVariant(BaseModel):
    title: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    cards: list[_AICard]


class _AIResponse(BaseModel):
    obvious_ideas: list[str] = Field(default_factory=list)
    theme_map: list[str] = Field(default_factory=list)
    wishes_applied: str = ""
    variants: list[_AIVariant]


@dataclass
class PlannerResult:
    plan: SetPlan
    cached: bool
    cost_usd: float  # 0 when the plan came from the cache (no new call)
    model: str


def _wish_lines(wishes: str) -> list[str]:
    """The Producer's Free Wishes as a REAL brief: placed right after the theme interpretation, outranking default instincts about
    which objects are 'typical'. Only the fixed rules (Set boundary, categories, safety, no lettering) may override a wish."""
    if not wishes.strip():
        return []
    return [
        "PRODUCER'S FREE WISHES (they outrank your default instincts about which objects are typical for the Set): "
        f"{wishes.strip()[:500]}\n"
        "Treat every content request in them (focus, places, kinds of things, mood) as a real requirement: the finished set must "
        "visibly reflect it, and `theme_map` should contain the facets they ask for. Exclusions are strict. Only the fixed rules "
        "(the Set boundary, the category rules, safety, no lettering) may override a wish. Say in `wishes_applied`, in one short "
        "sentence, how you applied them."]


class TextPlanner:
    def __init__(self, registry: ModelRegistry, *, cache_size: int = CACHE_SIZE,
                 backend: Callable[[Any, str, str, int | None], dict[str, Any]] | None = None) -> None:
        """`backend(settings, model, prompt, seed) -> parsed JSON dict` is the only provider-specific piece (default:
        Gemini text). A Cloudflare or other text LLM can be plugged in here without touching the planning logic."""
        self.registry = registry
        self._backend = backend or self._call
        self._cache: OrderedDict[tuple, SetPlan] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()
        self.calls = 0  # number of real Gemini text calls made (for tests / diagnostics)

    # ------------------------------------------------------------------ prompt
    @staticmethod
    def build_prompt(collection: str, set_name: str, variants: int, counts: dict[int, int], wishes: str,
                     grand: bool = False, history: list[str] | None = None) -> str:
        total = sum(counts.get(c, 0) for c in ALL_CATEGORIES)
        slots = assign_slots(counts)
        dist = "\n".join(f"- {CATEGORY_MEANINGS[c]}: exactly {counts.get(c, 0)} card(s)" for c in ALL_CATEGORIES
                         if counts.get(c, 0) > 0)
        lines = [
            f"COLLECTION (broad theme): {collection!r}",
            f"SET (specific sub-theme, the semantic boundary): {set_name!r}",
            "THEME WORLD: treat the Collection and the Set as a complete thematic WORLD, not as a class of objects. Think about "
            "everything that belongs to that world: its signature objects and equipment, recognisable environments and "
            "locations, everyday objects connected with it, activities and events, infrastructure and surroundings, characters, "
            "creatures and memorable situations, and visual-storytelling elements. These are directions to consider, NOT "
            "required categories or quotas: use only the facets that genuinely fit THIS Set and add any others the Set suggests. "
            "Work in this order. (1) In `obvious_ideas`, name the 6 most stereotypical objects that most people think of first for "
            "this Set: the clichés. (2) In `theme_map`, write 6-10 short, clearly DIFFERENT facets of the world. (3) Choose the cards. "
            "At most TWO cards of the whole set may use an item from `obvious_ideas`, and only when it is truly iconic and shown in a "
            "fresh way; every other card must come from other facets and be something a visitor would not list first.",
            *_wish_lines(wishes),
            "Choose subjects. Every subject must clearly belong to THIS Set, not merely to the broad Collection (no generic items "
            "that fit the Collection but not the Set). Each subject must be distinct and instantly recognisable. Do NOT fill the "
            "set with the first, most stereotypical objects people associate with the Set: at most a few truly iconic signature "
            "items belong in it, the rest should come from other facets of the world. Put the facet each card expresses into its "
            "`aspect` (use the `theme_map` wording) and spread the cards across the facets; two cards may share a facet only when "
            "the Set is narrow. A subject may be an object, a structure or recognisable place-like feature, a creature or "
            "character, or a small vignette or situation, as its category allows. Categories 3-4 are the natural home for places, "
            "environments, events and situations: when the world of the Set (or the Producer's wishes) includes them, use those "
            "richer categories for them instead of filling every slot with a single loose object.",
            "Choose subjects that are good standalone illustrations. Avoid subjects whose look depends on text or "
            "lettering (signs, posters, book covers, labels, scoreboards, banknotes, flags with writing).",
            "SET MEANING: when the Set name states a clear defining requirement, every subject must respect it. Separate the "
            "core subject kind (which must have the defining property: Two-Wheeled Vehicles -> a bicycle, motorcycle or "
            "two-wheeled scooter, not a trike or a car) from related gear, equipment, activities and infrastructure "
            "(helmets, pumps, locks, repair tools, bike lanes), which are welcome even though they lack the property. Only "
            "a subject that genuinely contradicts the Set's meaning is wrong. Do not turn a broad or metaphorical Set name "
            "into a rigid technical rule. Before answering, re-read every subject against the Set and replace any that "
            "contradicts it.",
            "VARIETY: prefer subjects that differ in identity, function, silhouette and role within the Set, and that differ "
            "from earlier subjects. Judge similarity by IDEA, not by name: two names for the same thing, a close paraphrase, or two "
            "subjects that play the same role in the Set count as repeats. Avoid two close relatives in one variant (e.g. two helmets) when a relevant, "
            "visually strong alternative exists. This is a preference, not a quota: legitimate related objects are allowed "
            "when they are the best fit, and never pick an obscure or weak subject just to be different.",
            f"Create {variants} DISTINCT alternative variant(s) of the set. Each variant has its own micro-theme: a short "
            f"`title` and a one-sentence `concept`. Each variant has exactly {total} cards.",
            "Per variant the cards must follow this category distribution EXACTLY (the category only controls how the "
            "picture is composed - floating object, object on a surface, scene...; the subject still has to fit the Set):",
            dist,
            f"Card order inside a variant: positions 1..{total} follow these category slots: {slots}.",
            "Every `object_name` must be unique across ALL variants (no repeats anywhere). `object_name` max 80 chars; "
            "`object_description` one short visual sentence (max 200 chars); `rationale` a short phrase (max 100 chars) saying "
            "why this subject belongs to THIS Set.",
        ]
        if not grand and any(counts.get(c, 0) for c in (1, 2, 3, 4)):
            lines.append(
                "FOCAL POINT (Categories 1-4): each card needs ONE clearly identifiable primary subject that is a recognizable "
                "collectible subject (an object, a structure or contained place-like feature, a creature, or for Categories 3-4 a "
                "small focused vignette or environmental composition), readable as a standalone illustration even at thumbnail "
                "size. Supporting objects and surroundings are fine but must not hide or compete with it, and it must never "
                "become a full game screen or a wide landscape. Avoid generic crowds, silhouettes of crowds, empty "
                "environments, abstract compositions and piles of unrelated objects; pick one central subject that "
                "expresses the theme instead. A character may be the subject only where the category and collection "
                "rules allow it. For Category 4, name the hero subject, not the place: not 'Harbour Market Square' but "
                "'Fishmonger's Wooden Stall' placed in a bustling harbour; keep the richer environment, "
                "atmosphere and story of Category 4. (These are composition principles, not fixed subjects.)"
            )
        if history:
            lines.append(
                "ALREADY GENERATED in earlier batches of this same Collection + Set (subject - hint):\n"
                + "\n".join(f"- {h}" for h in history)
                + "\nThese ideas are already used, so they show which facets of the world are covered. Do not repeat or paraphrase "
                "them: move to DIFFERENT facets and ideas. Reuse a thematically valid idea only if the Set really has no fresh facet left."
            )
        if grand:
            lines.append(
                "GRAND SET (All Golden): this is the FINALE of the collection - the culmination of its theme. Every card is "
                "a GOLDEN card (Category 5) and the set has no Category 1-4 cards. Each card is a story scene with the "
                "collection's characters expressing an ironic, emotional situation. EXACTLY 1 or 2 characters per card, "
                "never more; set `characters` to 1 or 2 on every card. The focal point of a card is ONE character or ONE interaction between "
                "TWO characters; do not reduce it to a single object, keep the emotion, irony, humour and storytelling. The scenes must be clearly DIFFERENT from each other (different situation, setting and "
                "emotion) but share one consistent visual style and mood so they read as one finale set. Name = the "
                "characters + situation, description = the visual scene. Characters must be original designs."
            )
        elif counts.get(GOLDEN_CATEGORY, 0):
            lines.append(
                "GOLDEN cards (Category 5): the most valuable cards, with images of the collection's KEY CHARACTERS. "
                "The characters play out an ironic, emotional situation. AT MOST 2 characters on one golden card: "
                "set `characters` to 1 or 2 on every golden card. "
                "Name = the characters + situation, description = the visual scene. Characters must be original designs."
            )
        lines.append("Return JSON only, matching the response schema.")
        return "\n".join(lines)

    # ------------------------------------------------------------------- cache
    @staticmethod
    def cache_key(collection: str, set_name: str, variants: int, counts: dict[int, int], seed: int | None,
                  wishes: str, grand: bool = False, history: tuple[str, ...] = ()) -> tuple:
        return (collection.strip().lower(), set_name.strip().lower(), variants,
                tuple(counts.get(c, 0) for c in ALL_CATEGORIES), seed, wishes.strip(), grand, tuple(history))

    def _get(self, key: tuple) -> SetPlan | None:
        with self._lock:
            plan = self._cache.get(key)
            if plan is not None:
                self._cache.move_to_end(key)
                return plan.model_copy(deep=True)
        return None

    def _put(self, key: tuple, plan: SetPlan) -> None:
        with self._lock:
            self._cache[key] = plan.model_copy(deep=True)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

    # -------------------------------------------------------------------- plan
    def plan(self, collection: str, set_name: str, variants: int, counts: dict[int, int], *,
             seed: int | None = None, wishes: str = "", pre_call: Callable[[], None] | None = None,
             grand: bool = False, history: list[str] | None = None) -> PlannerResult:
        s = self.registry.effective
        model = s.gemini_text_model
        history = list(history or [])
        key = self.cache_key(collection, set_name, variants, counts, seed, wishes, grand, tuple(history))
        hit = self._get(key)
        if hit is not None:
            return PlannerResult(hit, True, 0.0, model)
        if not 1 <= variants <= 5:
            raise PlanningError("An AI plan supports 1-5 variants.", "bad_variant_count")
        if not s.gemini_configured:
            raise PlanningError(
                "A Gemini API key is needed to plan a custom theme with AI. Add it in Settings > API Connections.",
                "gemini_key_missing",
            )
        if pre_call is not None:
            pre_call()  # e.g. budget guard; runs only when a real call is about to be made
        data = self._backend(s, model, self.build_prompt(collection, set_name, variants, counts, wishes, grand, history), seed)
        plan = self._build(data, set_name, variants, counts, seed, model, wishes=wishes, history_used=len(history))
        self._put(key, plan)
        cost = estimate_plan_cost_usd(model, variants, sum(counts.get(c, 0) for c in ALL_CATEGORIES))
        return PlannerResult(plan.model_copy(deep=True), False, cost, model)


    # ------------------------------------------------------- selective regeneration (Metaphor Planner)
    @staticmethod
    def build_regen_prompt(collection: str, set_name: str, plan: SetPlan, selected: list[tuple[str, int]], wishes: str,
                           grand: bool = False, history: list[str] | None = None) -> str:
        """Context-aware prompt that asks for replacements of ONLY the selected cards. It carries the Collection, Set,
        wishes, every existing concept (kept and replaced), the categories of the selected cards and the planning rules."""
        sel = set(selected)
        lines = [
            f"COLLECTION (broad theme): {collection!r}",
            f"SET (specific sub-theme, the semantic boundary): {set_name!r}",
            "TASK: a Producer curates an existing plan. Replace ONLY the cards marked REPLACE with new, better subjects. "
            "The cards marked KEEP stay exactly as they are; they are context so that your replacements do not repeat them.",
        ]
        for v in plan.variants:
            lines.append(f"VARIANT {v.variant_id} - {v.title}: {v.concept}")
            for c in v.cards:
                tag = "REPLACE" if (v.variant_id, c.position) in sel else "KEEP"
                extra = f" ({c.characters} character(s))" if c.characters else ""
                lines.append(f"- {v.variant_id}{c.position} [{tag}] Category {c.category}: {c.object_name} - {c.object_description}{extra}")
        lines.append("Cards to replace, each with its FIXED category (the category only controls how the picture is composed; "
                     "keep each replacement suitable for it, and the subject must still fit the Set):")
        for v in plan.variants:
            for c in v.cards:
                if (v.variant_id, c.position) in sel:
                    lines.append(f"- {v.variant_id}{c.position}: {CATEGORY_MEANINGS.get(c.category, 'Category ' + str(c.category))}; "
                                 f"current subject '{c.object_name}' must NOT be proposed again")
        lines += [
            "Rules for every replacement: it must clearly belong to THIS Set (not merely to the broad Collection), be a "
            "distinct, instantly recognisable subject, and differ in identity, function, silhouette and role from ALL the other "
            "cards above (kept or replaced) and from the card it replaces. Never return a synonym, near-duplicate or close "
            "relative of an existing subject when a relevant, visually strong alternative exists; this is a preference for "
            "variety, not a quota, and never pick an obscure subject just to be different. Choose good standalone illustrations "
            "with one clear primary subject; avoid subjects that depend on text or lettering. Be concrete, visual and "
            "family-friendly: no weapons, no violence, no real brands, no franchise names. Respect the Set's defining meaning "
            "without turning a broad or metaphorical name into a rigid technical rule. Keep each variant's micro-theme in mind.",
            "`object_name` max 80 chars; `object_description` one short visual sentence (max 200 chars); `rationale` a short "
            "phrase (max 100 chars) saying why it belongs to THIS Set. Write in English.",
        ]
        if grand or any(c.category == GOLDEN_CATEGORY for v in plan.variants for c in v.cards if (v.variant_id, c.position) in sel):
            lines.append("GOLDEN cards (Category 5): the collection's KEY CHARACTERS in an ironic, emotional situation; EXACTLY 1 or 2 "
                         "characters (set `characters` to 1 or 2), original designs; name = the characters + situation, description = "
                         "the visual scene; the scene must differ clearly from the other golden cards.")
        if history:
            lines.append("ALREADY GENERATED in earlier batches of this same Collection + Set (subject - hint):\n"
                         + "\n".join(f"- {h}" for h in history) + "\nPrefer fresh subjects.")
        if wishes.strip():
            lines.append("Producer's free wishes (exclusions are strict; never let them break the Set boundary, the category rules or the "
                         f"safety rules): {wishes.strip()[:500]}")
        lines.append(f"Return JSON only: `replacements` with exactly {len(selected)} item(s), one per REPLACE card, using its "
                     "`variant_id` and `position` exactly as listed. Do not return the KEEP cards.")
        return "\n".join(lines)

    def regenerate_cards(self, collection: str, set_name: str, plan: SetPlan, selected: list[tuple[str, int]], *,
                         wishes: str = "", seed: int | None = None, pre_call: Callable[[], None] | None = None,
                         grand: bool = False, history: list[str] | None = None) -> PlannerResult:
        """Replace ONLY the selected cards' subject/description (and rationale / golden character count). Card numbers, order,
        categories and every other card stay byte-for-byte as they were. One text call; no cache, no image provider."""
        s = self.registry.effective
        model = s.gemini_text_model
        if not selected or len(set(selected)) != len(selected):
            raise PlanningError("Select at least one card (each at most once).", "bad_selection")
        index = {(v.variant_id, c.position): c for v in plan.variants for c in v.cards}
        missing = [k for k in selected if k not in index]
        if missing:
            raise PlanningError(f"Selected card(s) not in the plan: {missing}.", "bad_selection")
        if not s.gemini_configured:
            raise PlanningError("A Gemini API key is needed to regenerate concepts with AI. Add it in Settings > API Connections.",
                                "gemini_key_missing")
        if pre_call is not None:
            pre_call()
        prompt = self.build_regen_prompt(collection, set_name, plan, selected, wishes, grand, list(history or []))
        if self._backend == self._call:  # default Gemini backend: use the replacement schema
            data = self._call(s, model, prompt, seed, _REGEN_SCHEMA)
        else:  # custom/test backend keeps its 4-argument contract
            data = self._backend(s, model, prompt, seed)
        try:
            parsed = _AIRegen.model_validate(data)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise PlanningError(f"The AI replacements are malformed ({'.'.join(str(x) for x in first.get('loc', ()))}: "
                                f"{first.get('msg')}). Nothing was changed.", "ai_plan_invalid") from None
        got = {(normalize_variant_id(r.variant_id), r.position): r for r in parsed.replacements}
        if len(parsed.replacements) != len(selected) or set(got) != set(selected):
            raise PlanningError(f"The AI returned replacements for {sorted(got)}, expected {sorted(selected)}. Nothing was changed.",
                                "ai_plan_invalid")
        new_plan = plan.model_copy(deep=True)
        issues: list[str] = []
        for v in new_plan.variants:
            for c in v.cards:
                r = got.get((v.variant_id, c.position))
                if r is None:
                    continue  # unselected: untouched
                old = c.object_name.strip().lower()
                c.object_name = r.object_name.strip()
                c.object_description = r.object_description.strip()[:300]
                c.rationale = (r.rationale.strip()[:140] or f"{v.title.strip()}: AI-planned for Cat. {c.category}")
                c.characters = r.characters if c.category == GOLDEN_CATEGORY else None
                if c.object_name.lower() == old:
                    issues.append(f"{v.variant_id}{c.position} repeated its old subject '{c.object_name}'")
        issues += validate_plan(new_plan, {k: v for k, v in new_plan.distribution.items()})
        every = [c.object_name for v in new_plan.variants for c in v.cards]
        for a, b in near_duplicates(every)[:2]:
            issues.append(f"near-duplicate subjects '{a}' and '{b}'")
        banned = forbidden_terms(wishes)
        for v in new_plan.variants:
            for c in v.cards:
                if (v.variant_id, c.position) in got:
                    hit = violates(f"{c.object_name} {c.object_description}", banned)
                    if hit:
                        issues.append(f"'{c.object_name}' breaks the wish to avoid '{hit}'")
        if issues:
            raise PlanningError("The AI replacements break the rules, so nothing was changed: " + "; ".join(issues[:4]), "ai_plan_invalid")
        new_plan.notes = [n for n in new_plan.notes if not n.startswith("Metaphor Planner:")] + [
            f"Metaphor Planner: {len(selected)} concept(s) were regenerated from the other cards' context; the rest are unchanged."]
        cost = estimate_plan_cost_usd(model, 1, len(selected))
        return PlannerResult(new_plan, False, cost, model)

    # --------------------------------------------------------------- http call
    def _call(self, s: Any, model: str, prompt: str, seed: int | None, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{s.gemini_base_url.rstrip('/')}/models/{model}:generateContent"
        gen_cfg: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseSchema": schema or _RESPONSE_SCHEMA,
            # no temperature/top_p/top_k: Gemini 3.x models deprecate them (ignored, may be rejected); `seed` keeps variety reproducible
        }
        if seed is not None:
            gen_cfg["seed"] = int(seed) % (2**31 - 1)
        body = {
            "systemInstruction": {"parts": [{"text": _SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen_cfg,
        }
        headers = {"x-goog-api-key": s.gemini_api_key, "content-type": "application/json"}
        transport = getattr(self.registry, "_gemini_transport", None)
        if transport is not None and not isinstance(transport, httpx.BaseTransport):
            raise PlanningError("The configured HTTP transport cannot be used for the text planner.", "ai_plan_failed")

        def redact(text: str) -> str:
            return text.replace(s.gemini_api_key, "***") if s.gemini_api_key else text

        self.calls += 1
        try:
            with httpx.Client(timeout=min(s.gemini_timeout_s, 90.0), transport=transport) as client:
                resp = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise PlanningError(redact(f"The AI planner call failed ({type(exc).__name__}). No plan was created."),
                                "ai_plan_failed") from None
        if resp.status_code != 200:
            try:
                msg = resp.json().get("error", {}).get("message", "")
            except Exception:
                msg = resp.text[:200]
            raise PlanningError(redact(f"Gemini text API error {resp.status_code}: {msg or 'no details'}"),
                                "ai_plan_failed")
        try:
            payload = resp.json()
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError):
            raise PlanningError("The AI planner did not return valid JSON. No plan was created; try again.",
                                "ai_plan_invalid") from None
        if not isinstance(data, dict):
            raise PlanningError("The AI planner returned an unexpected structure.", "ai_plan_invalid")
        return data

    # ------------------------------------------------------------- validation
    @staticmethod
    def _build(data: dict[str, Any], set_name: str, variants: int, counts: dict[int, int], seed: int | None,
               model: str, wishes: str = "", history_used: int = 0) -> SetPlan:
        try:
            parsed = _AIResponse.model_validate(data)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(x) for x in first.get("loc", ()))
            raise PlanningError(f"The AI plan is malformed ({where}: {first.get('msg')}). No plan was created.",
                                "ai_plan_invalid") from None
        if len(parsed.variants) != variants:
            raise PlanningError(f"The AI plan has {len(parsed.variants)} variant(s), expected {variants}.",
                                "ai_plan_invalid")
        slots = assign_slots(counts)
        built: list[PlanVariant] = []
        for vi, v in enumerate(parsed.variants):
            ordered = sorted(v.cards, key=lambda c: (c.category, c.position))  # slots: Cat.1..4 then golden last
            if len(ordered) != len(slots):
                raise PlanningError(
                    f"AI variant {VARIANT_IDS[vi]} has {len(ordered)} card(s), expected {len(slots)}.", "ai_plan_invalid")
            cards = [
                PlanCard(position=pos, category=c.category, object_name=c.object_name.strip(),
                         object_description=c.object_description.strip()[:300],
                         characters=c.characters if c.category == GOLDEN_CATEGORY else None,
                         rationale=(c.rationale.strip()[:140] or f"{v.title.strip()}: AI-planned for Cat. {c.category}"))
                for pos, c in enumerate(ordered, 1)
            ]
            built.append(PlanVariant(variant_id=VARIANT_IDS[vi], index=vi, title=v.title.strip()[:80],
                                     concept=v.concept.strip()[:300], cards=cards))
        plan = SetPlan(
            genre=set_name.strip(), seed=default_seed(set_name, variants) if seed is None else seed, planning_mode="ai", distribution=dict(counts),
            pools_version="ai", variants=built,
            notes=[f"AI planner ({model}): one Gemini text call analysed the Collection and Set; objects are "
                   "AI-suggested, not from curated pools. Check them before generating images."],
        )
        issues = validate_plan(plan, counts)
        # semantic checks that need no second model call: near-duplicate subjects and Producer exclusions
        every = [c.object_name for v in plan.variants for c in v.cards]
        for a, b in near_duplicates(every)[:2]:
            issues.append(f"near-duplicate subjects '{a}' and '{b}'")
        for a, b in similar_concepts([(c.object_name, c.object_description) for v in plan.variants for c in v.cards])[:2]:
            issues.append(f"same idea under different names: '{a}' and '{b}'")
        banned = forbidden_terms(wishes)
        for v in plan.variants:
            for c in v.cards:
                hit = violates(f"{c.object_name} {c.object_description}", banned)
                if hit:
                    issues.append(f"'{c.object_name}' breaks the wish to avoid '{hit}'")
                    break
        if issues:
            raise PlanningError("The AI plan breaks the rules: " + "; ".join(issues[:4]), "ai_plan_invalid")
        cliches = [c.strip() for c in parsed.obvious_ideas if c and c.strip()][:8]
        if cliches:
            used = [c.object_name for v in plan.variants for c in v.cards
                    if any(similar_name(c.object_name, k) for k in cliches)]
            plan.notes.append("Most obvious ideas deliberately limited: " + "; ".join(cliches[:6]) + "."
                              + (f" Still used: {', '.join(used)}." if used else " None of them was used."))
        facets = [f.strip() for f in parsed.theme_map if f and f.strip()][:10]
        if facets:
            plan.notes.append("Facets of the Set's world explored: " + "; ".join(facets) + ".")
        if wishes.strip() and parsed.wishes_applied.strip():
            plan.notes.append("How your wishes were applied: " + parsed.wishes_applied.strip()[:240])
        if history_used:
            plan.notes.append(f"Earlier batches of this Collection/Set were passed to the planner ({history_used} subjects) "
                              "so that it prefers fresh subjects.")
        return plan
