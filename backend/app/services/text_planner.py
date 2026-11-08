"""TextPlanner: AI set planner for custom themes (any Collection/Set that the curated RulesPlanner does not cover).

ONE Gemini TEXT call (structured JSON output) analyses the meaning of Collection + Set and returns, per variant, a
micro-theme and the cards (object name + description per category slot). The answer is validated strictly (pydantic +
the same validate_plan as the rules planner); an invalid answer raises PlanningError - there is NO automatic retry, so a
plan never costs more than one tiny text call. Identical inputs (preview then create) reuse the cached plan.
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from pydantic import BaseModel, Field, ValidationError

from ..providers.model_registry import ModelRegistry
from .category_distribution import ALL_CATEGORIES, GOLDEN_CATEGORY, assign_slots
from .planning import VARIANT_IDS, PlanCard, PlanningError, PlanVariant, SetPlan, default_seed, validate_plan

PLAN_COST_ESTIMATE_USD = 0.002  # rough cost of one small Gemini text call (local estimate, not an invoice)
CACHE_SIZE = 32

CATEGORY_MEANINGS = {
    1: "Category 1 - a SIMPLE object floating in the air, no small details, no ground",
    2: "Category 2 - a simple object standing on a simple surface",
    3: "Category 3 - an object on a realistic surface in a volumetric scene",
    4: "Category 4 - an object inside a recognizable environment that tells a small story",
    5: "Category 5 - GOLDEN card: the collection's KEY CHARACTERS (at most 2 characters) in an ironic, emotional situation",
}

_SYSTEM = (
    "You are the set planner of a casual mobile game's collectible-card art pipeline. Each collection has several "
    "sets; each set is a group of cards, one illustrated object (or, for golden cards, one scene with key characters) "
    "per card. You plan WHICH objects/scenes are illustrated; you do not draw. Be concrete, visual and family-friendly: "
    "no weapons, no violence, no real brands, no existing franchise names or copyrighted characters, nothing that needs "
    "lettering in the image. Write all names and descriptions in English."
)

_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
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
                            "properties": {
                                "position": {"type": "INTEGER"},
                                "category": {"type": "INTEGER"},
                                "object_name": {"type": "STRING"},
                                "object_description": {"type": "STRING"},
                            },
                            "required": ["position", "category", "object_name", "object_description"],
                        },
                    },
                },
                "required": ["title", "concept", "cards"],
            },
        }
    },
    "required": ["variants"],
}


class _AICard(BaseModel):
    position: int = 0
    category: int
    object_name: str = Field(min_length=1, max_length=80)
    object_description: str = Field(min_length=1)


class _AIVariant(BaseModel):
    title: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    cards: list[_AICard]


class _AIResponse(BaseModel):
    variants: list[_AIVariant]


@dataclass
class PlannerResult:
    plan: SetPlan
    cached: bool
    cost_usd: float  # 0 when the plan came from the cache (no new call)
    model: str


class TextPlanner:
    def __init__(self, registry: ModelRegistry, *, cache_size: int = CACHE_SIZE) -> None:
        self.registry = registry
        self._cache: OrderedDict[tuple, SetPlan] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()
        self.calls = 0  # number of real Gemini text calls made (for tests / diagnostics)

    # ------------------------------------------------------------------ prompt
    @staticmethod
    def build_prompt(collection: str, set_name: str, variants: int, counts: dict[int, int], wishes: str) -> str:
        total = sum(counts.get(c, 0) for c in ALL_CATEGORIES)
        slots = assign_slots(counts)
        dist = "\n".join(f"- {CATEGORY_MEANINGS[c]}: exactly {counts.get(c, 0)} card(s)" for c in ALL_CATEGORIES
                         if counts.get(c, 0) > 0)
        lines = [
            f"COLLECTION (broad theme, free text): {collection!r}",
            f"SET (narrower theme inside the collection, free text): {set_name!r}",
            "First analyse what the Collection and the Set mean together (for example Collection 'Movies' -> film themes, "
            "Collection 'Transport' -> vehicles; Set 'Sci-Fi' or 'Detective' narrows it down) and keep every object on that theme.",
            f"Create {variants} DISTINCT alternative variant(s) of the set. Each variant has its own micro-theme: a short "
            f"`title` and a one-sentence `concept`. Each variant has exactly {total} cards.",
            "Per variant the cards must follow this category distribution EXACTLY:",
            dist,
            f"Card order inside a variant: positions 1..{total} follow these category slots: {slots}.",
            "Every `object_name` must be unique across ALL variants (no repeats anywhere). `object_name` max 80 chars; "
            "`object_description` one short visual sentence (max 200 chars).",
        ]
        if counts.get(GOLDEN_CATEGORY, 0):
            lines.append(
                "GOLDEN cards (Category 5): the most valuable cards, with images of the collection's KEY CHARACTERS. "
                "The characters play out an ironic, emotional situation. AT MOST 2 characters on one golden card. "
                "Name = the characters + situation, description = the visual scene. Characters must be original designs."
            )
        if wishes.strip():
            lines.append(f"Producer wishes (guidance only, never break the rules above): {wishes.strip()[:500]}")
        lines.append("Return JSON only, matching the response schema.")
        return "\n".join(lines)

    # ------------------------------------------------------------------- cache
    @staticmethod
    def cache_key(collection: str, set_name: str, variants: int, counts: dict[int, int], seed: int | None,
                  wishes: str) -> tuple:
        return (collection.strip().lower(), set_name.strip().lower(), variants,
                tuple(counts.get(c, 0) for c in ALL_CATEGORIES), seed, wishes.strip())

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
             seed: int | None = None, wishes: str = "", pre_call: Callable[[], None] | None = None) -> PlannerResult:
        s = self.registry.effective
        model = s.gemini_text_model
        key = self.cache_key(collection, set_name, variants, counts, seed, wishes)
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
        data = self._call(s, model, self.build_prompt(collection, set_name, variants, counts, wishes), seed)
        plan = self._build(data, set_name, variants, counts, seed, model)
        self._put(key, plan)
        return PlannerResult(plan.model_copy(deep=True), False, PLAN_COST_ESTIMATE_USD, model)

    # --------------------------------------------------------------- http call
    def _call(self, s: Any, model: str, prompt: str, seed: int | None) -> dict[str, Any]:
        url = f"{s.gemini_base_url.rstrip('/')}/models/{model}:generateContent"
        gen_cfg: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
            "temperature": 0.9,
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
               model: str) -> SetPlan:
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
                         rationale=f"{v.title.strip()}: AI-planned for Cat. {c.category}")
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
        if issues:
            raise PlanningError("The AI plan breaks the rules: " + "; ".join(issues[:4]), "ai_plan_invalid")
        return plan
