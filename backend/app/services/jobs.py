"""Set-generation jobs: SQLite store, state machine and a sequential background runner
(spec §10.4, §17 Phases 3-4, §19.4-19.5).

Guarantees:
  * one card = one provider call; strictly sequential; automatic retries are off unless configured;
  * explicit cost confirmation and a per-run + cumulative cap before anything is spent;
  * a circuit breaker stops the job after 2 consecutive provider failures;
  * state lives in SQLite, so a restart never loses finished cards; interrupted jobs are NOT
    auto-resumed (that could spend money without a user action) - the user presses Continue;
  * every generated image is an immutable card *version*; regeneration adds a version and never
    deletes the previous one (spec ADR-009, §19.4).
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel, Field, model_validator

from ..config import Settings
from ..providers.base import ProviderDisabled
from .category_distribution import (
    CARDS_PER_SET,
    SET_GRAND,
    SET_STANDARD,
    CategoryDistribution,
    DistributionError,
    compliance,
    final_eligibility,
    resolve_distribution,
)
from .cost_guard import BudgetExceeded, CostGuard
from .generation import GenerateCardRequest, GenerationFailure, GenerationService
from .planning import PlanningError, SetPlan, validate_plan
from .qa import qa_card, qa_variant
from .reference_manager import ReferenceError_, ReferenceManager
from .prompt_compiler import MAX_REPAIR_NOTE, MAX_WISHES, check_overrides
from .prompt_library import LibraryError
from .semantic_planning import history_summary, theme_key
from .text_planner import TextPlanner, estimate_plan_cost_usd

ACTIVE_STATES = {"created", "planning", "plan_qa", "queued", "generating", "image_qa", "set_qa",
                 "cancel_requested"}
MAX_CONSECUTIVE_FAILURES = 2
UNIVERSAL_REF_TYPES = {"style", "composition", "negative"}
ALLOWED_OUTPUT_SIZES = (24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024)
_PLAN_ERROR_STATUS = {"gemini_key_missing": 400, "ai_plan_failed": 502}
_PLAN_COST_SINK: contextvars.ContextVar = contextvars.ContextVar("plan_cost_sink", default=None)  # ledger ids of the planning calls of one create()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobFailure(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}


class JobCreateRequest(BaseModel):
    collection: str = Field(default="Movie Collection", min_length=1, max_length=80)
    genre: str = Field(min_length=1, max_length=80)
    wishes: str = Field(default="", max_length=MAX_WISHES)
    number_of_variants: int = Field(default=1, ge=1, le=5)
    category_distribution: CategoryDistribution | None = None  # Custom mode: counts for Cat.1-4
    number_of_cards: int = Field(default=CARDS_PER_SET, ge=1, le=CARDS_PER_SET)  # cards per set, 10 = golden standard
    gold_cards: int = Field(default=0, ge=0, le=CARDS_PER_SET)  # Category 5 cards inside the set (<= number_of_cards)
    output_size: int = 1024  # icon side in px, one of ALLOWED_OUTPUT_SIZES (422 bad_output_size otherwise)
    allow_ai_plan: bool = False  # explicit opt-in for the AI (Gemini text) planner on custom themes
    provider: str
    model: str
    reference_ids: list[str] = Field(default_factory=list)
    reference_policy_accepted: bool = False
    aspect_ratio: Literal["1:1", "3:4", "4:3", "9:16", "16:9"] = "1:1"
    scope: Literal["pilot", "full"] = "full"
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    confirmed: bool = False
    set_type: Literal["standard", "grand"] = SET_STANDARD  # grand = Grand Set (All Golden), finale set
    # Session-only prompt overrides from the browser tab (template name -> text). Resolved ONCE at job creation into an
    # immutable server-side snapshot (request["prompt_bundle"]); nothing is written to the permanent Prompt Library.
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    # A plan already curated in the Metaphor Planner. When present it is used AS IS (validated, never re-planned, no text call);
    # when absent the automatic AI planning is unchanged.
    plan: SetPlan | None = None

    @model_validator(mode="after")
    def _grand_set_rules(self) -> "JobCreateRequest":
        if self.set_type == SET_GRAND:  # Gold = Number of Cards, Cat.1-4 = 0 (the server enforces it, not only the UI)
            self.gold_cards = self.number_of_cards
            self.category_distribution = None
        return self


class CardRef(BaseModel):
    variant_id: str = Field(min_length=1, max_length=2)
    position: int = Field(ge=1, le=CARDS_PER_SET)


class PlanRegenerateRequest(BaseModel):
    """Metaphor Planner: replace the concepts of the selected cards only (text model only, no images)."""
    collection: str = Field(default="Movie Collection", min_length=1, max_length=80)
    genre: str = Field(min_length=1, max_length=80)
    wishes: str = Field(default="", max_length=MAX_WISHES)
    number_of_variants: int = Field(default=1, ge=1, le=5)
    number_of_cards: int = Field(default=CARDS_PER_SET, ge=1, le=CARDS_PER_SET)
    gold_cards: int = Field(default=0, ge=0, le=CARDS_PER_SET)
    category_distribution: CategoryDistribution | None = None
    set_type: Literal["standard", "grand"] = SET_STANDARD
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    plan: SetPlan
    selected: list[CardRef] = Field(min_length=1, max_length=CARDS_PER_SET * 5)

    @model_validator(mode="after")
    def _grand_set_rules(self) -> "PlanRegenerateRequest":
        if self.set_type == SET_GRAND:
            self.gold_cards = self.number_of_cards
            self.category_distribution = None
        return self


class PlanOnlyRequest(BaseModel):
    """Planner-only test: the real semantic planner, no image provider, no job, no images."""
    collection: str = Field(default="Movie Collection", min_length=1, max_length=80)
    genre: str = Field(min_length=1, max_length=80)  # = Set
    wishes: str = Field(default="", max_length=MAX_WISHES)
    number_of_variants: int = Field(default=1, ge=1, le=5)
    number_of_cards: int = Field(default=CARDS_PER_SET, ge=1, le=CARDS_PER_SET)
    gold_cards: int = Field(default=0, ge=0, le=CARDS_PER_SET)
    category_distribution: CategoryDistribution | None = None
    set_type: Literal["standard", "grand"] = SET_STANDARD
    allow_ai_plan: bool = True  # the Producer pressed the explicit "Plan only" button
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def _grand_set_rules(self) -> "PlanOnlyRequest":
        if self.set_type == SET_GRAND:
            self.gold_cards = self.number_of_cards
            self.category_distribution = None
        return self


class ContinueRequest(BaseModel):
    confirmed: bool = False
    retry_failed: bool = False


class RegenerateRequest(BaseModel):
    confirmed: bool = False
    note: str = Field(default="", max_length=MAX_REPAIR_NOTE)


class ReviewRequest(BaseModel):
    decision: Literal["approved", "rejected", "pending"]
    note: str = Field(default="", max_length=300)


class ApproveVariantRequest(BaseModel):
    mark_cards_approved: bool = True


class PatchCardRequest(BaseModel):
    object_name: str = Field(min_length=1, max_length=80)
    object_description: str = Field(default="", max_length=300)
    # from a Gemini suggestion (optional): why the new subject fits the Set, and the golden character count
    rationale: str = Field(default="", max_length=300)
    characters: int | None = Field(default=None, ge=1, le=2)


# ----------------------------------------------------------------------------------------- store
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  request_json TEXT NOT NULL, plan_json TEXT NOT NULL, scope TEXT NOT NULL, error TEXT,
  spent_est REAL NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cards (
  card_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, variant_id TEXT NOT NULL, position INTEGER NOT NULL,
  category INTEGER NOT NULL, object_name TEXT NOT NULL, object_description TEXT NOT NULL,
  status TEXT NOT NULL, artifact_id TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  reference_selection TEXT, updated_at TEXT NOT NULL,
  regen_count INTEGER NOT NULL DEFAULT 0, pending_note TEXT, spec_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS cards_job ON cards(job_id);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, ts TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_job ON events(job_id, id);
CREATE TABLE IF NOT EXISTS card_versions (
  version_id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT NOT NULL, version INTEGER NOT NULL,
  artifact_id TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT,
  sha256 TEXT, width INTEGER, height INTEGER,
  qa_status TEXT NOT NULL DEFAULT 'not_run', qa_json TEXT,
  art_review TEXT NOT NULL DEFAULT 'pending', review_note TEXT, reviewed_at TEXT,
  is_current INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS versions_card ON card_versions(card_id);
CREATE TABLE IF NOT EXISTS variant_approvals (
  job_id TEXT NOT NULL, variant_id TEXT NOT NULL, approved_at TEXT NOT NULL, approved_by TEXT NOT NULL,
  PRIMARY KEY(job_id, variant_id)
);
CREATE TABLE IF NOT EXISTS comparison_refs (
  card_id TEXT PRIMARY KEY, ref_id TEXT, selection_mode TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._conn() as c:
            c.executescript(_SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(cards)")}  # migrate Phase 3 databases
            for name, ddl in (("regen_count", "INTEGER NOT NULL DEFAULT 0"), ("pending_note", "TEXT"),
                              ("spec_version", "INTEGER NOT NULL DEFAULT 1")):
                if name not in cols:
                    c.execute(f"ALTER TABLE cards ADD COLUMN {name} {ddl}")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Commit on success, roll back on error, ALWAYS close (sqlite3's own `with` does not close)."""
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def _exec(self, sql: str, args: tuple = ()) -> int:
        with self._lock, self._conn() as c:
            return c.execute(sql, args).lastrowid or 0

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock, self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    # -- jobs / cards ------------------------------------------------------------------------
    def create_job(self, job_id: str, status: str, request: dict, plan: dict, scope: str,
                   cards: list[dict[str, Any]]) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO jobs(job_id,status,created_at,updated_at,request_json,plan_json,scope)"
                      " VALUES(?,?,?,?,?,?,?)",
                      (job_id, status, now(), now(), json.dumps(request), json.dumps(plan), scope))
            c.executemany(
                "INSERT INTO cards(card_id,job_id,variant_id,position,category,object_name,"
                "object_description,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                [(k["card_id"], job_id, k["variant_id"], k["position"], k["category"], k["object_name"],
                  k["object_description"], k.get("status", "planned"), now()) for k in cards])

    def set_status(self, job_id: str, status: str, error: str | None = None) -> None:
        self._exec("UPDATE jobs SET status=?, error=?, updated_at=? WHERE job_id=?", (status, error, now(), job_id))

    def set_scope(self, job_id: str, scope: str) -> None:
        self._exec("UPDATE jobs SET scope=?, updated_at=? WHERE job_id=?", (scope, now(), job_id))

    def set_cancel(self, job_id: str, value: bool) -> None:
        self._exec("UPDATE jobs SET cancel_requested=?, updated_at=? WHERE job_id=?", (1 if value else 0, now(), job_id))

    def cancel_requested(self, job_id: str) -> bool:
        r = self._all("SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,))
        return bool(r and r[0]["cancel_requested"])

    def add_spent(self, job_id: str, usd: float) -> None:
        self._exec("UPDATE jobs SET spent_est=spent_est+?, updated_at=? WHERE job_id=?", (usd, now(), job_id))

    def update_card(self, card_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE cards SET {cols}, updated_at=? WHERE card_id=?", (*fields.values(), now(), card_id))

    def add_event(self, job_id: str, message: str, level: str = "info") -> None:
        self._exec("INSERT INTO events(job_id,ts,level,message) VALUES(?,?,?,?)", (job_id, now(), level, message))

    def events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]:
        return self._all("SELECT id,ts,level,message FROM events WHERE job_id=? AND id>? ORDER BY id", (job_id, after))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        r = self._all("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        if not r:
            return None
        j = r[0]
        j["request"] = json.loads(j.pop("request_json"))
        j["plan"] = json.loads(j.pop("plan_json"))
        return j

    def list_jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        return self._all("SELECT job_id,status,created_at,updated_at,scope,spent_est,request_json "
                         "FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))

    def cards(self, job_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM cards WHERE job_id=? ORDER BY variant_id, position", (job_id,))

    def card(self, job_id: str, card_id: str) -> dict[str, Any] | None:
        r = self._all("SELECT * FROM cards WHERE job_id=? AND card_id=?", (job_id, card_id))
        return r[0] if r else None

    # -- versions / review / approval --------------------------------------------------------
    def add_version(self, card_id: str, artifact_id: str, note: str | None, sha256: str, width: int,
                    height: int) -> dict[str, Any]:
        cur = self._all("SELECT MAX(version) AS v FROM card_versions WHERE card_id=?", (card_id,))
        version = (cur[0]["v"] or 0) + 1
        self._exec("UPDATE card_versions SET is_current=0 WHERE card_id=?", (card_id,))
        vid = self._exec(
            "INSERT INTO card_versions(card_id,version,artifact_id,created_at,note,sha256,width,height,is_current)"
            " VALUES(?,?,?,?,?,?,?,?,1)", (card_id, version, artifact_id, now(), note, sha256, width, height))
        return {"version_id": vid, "version": version}

    def versions_for_job(self, job_id: str) -> dict[str, list[dict[str, Any]]]:
        rows = self._all("SELECT v.* FROM card_versions v JOIN cards c ON c.card_id=v.card_id "
                         "WHERE c.job_id=? ORDER BY v.card_id, v.version DESC", (job_id,))
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r["card_id"], []).append(r)
        return out

    def current_version(self, card_id: str) -> dict[str, Any] | None:
        r = self._all("SELECT * FROM card_versions WHERE card_id=? AND is_current=1", (card_id,))
        return r[0] if r else None

    def update_version_qa(self, version_id: int, status: str, qa: dict[str, Any]) -> None:
        self._exec("UPDATE card_versions SET qa_status=?, qa_json=? WHERE version_id=?",
                   (status, json.dumps(qa), version_id))

    def set_version_review(self, version_id: int, decision: str, note: str) -> None:
        self._exec("UPDATE card_versions SET art_review=?, review_note=?, reviewed_at=? WHERE version_id=?",
                   (decision, note, now(), version_id))

    def set_plan(self, job_id: str, plan: dict[str, Any]) -> None:
        self._exec("UPDATE jobs SET plan_json=?, updated_at=? WHERE job_id=?", (json.dumps(plan), now(), job_id))

    def retire_versions(self, card_id: str) -> None:
        self._exec("UPDATE card_versions SET is_current=0 WHERE card_id=?", (card_id,))

    def approvals(self, job_id: str) -> dict[str, dict[str, Any]]:
        return {r["variant_id"]: r for r in self._all("SELECT * FROM variant_approvals WHERE job_id=?", (job_id,))}

    def approve_variant(self, job_id: str, variant_id: str, by: str) -> None:
        self._exec("DELETE FROM variant_approvals WHERE job_id=?", (job_id,))  # one final variant per job
        self._exec("INSERT INTO variant_approvals(job_id,variant_id,approved_at,approved_by) VALUES(?,?,?,?)",
                   (job_id, variant_id, now(), by))

    def remove_variant(self, job_id: str, variant_id: str, plan: dict[str, Any], request: dict[str, Any]) -> None:
        """Drop a variant from a job: its cards, versions, approval and comparison links go and the stored plan/request no longer list it.
        The image FILES stay in the artifact store (History), so nothing on disk is destroyed."""
        ids = [c["card_id"] for c in self.cards(job_id) if c["variant_id"] == variant_id]
        for cid in ids:
            self._exec("DELETE FROM card_versions WHERE card_id=?", (cid,))
            self._exec("DELETE FROM comparison_refs WHERE card_id=?", (cid,))
        self._exec("DELETE FROM variant_approvals WHERE job_id=? AND variant_id=?", (job_id, variant_id))
        self._exec("DELETE FROM cards WHERE job_id=? AND variant_id=?", (job_id, variant_id))
        self._exec("UPDATE jobs SET plan_json=?, request_json=?, updated_at=? WHERE job_id=?",
                   (json.dumps(plan), json.dumps(request), now(), job_id))

    def clear_approval(self, job_id: str, variant_id: str | None = None) -> bool:
        had = bool(self._all("SELECT 1 FROM variant_approvals WHERE job_id=?" + (" AND variant_id=?" if variant_id else ""),
                             (job_id, variant_id) if variant_id else (job_id,)))
        if variant_id:
            self._exec("DELETE FROM variant_approvals WHERE job_id=? AND variant_id=?", (job_id, variant_id))
        else:
            self._exec("DELETE FROM variant_approvals WHERE job_id=?", (job_id,))
        return had

    # -- comparison reference matches (spec §10.1 ArtworkReferenceMatch) -----------------------
    def comparison_ref(self, card_id: str) -> dict[str, Any] | None:
        r = self._all("SELECT * FROM comparison_refs WHERE card_id=?", (card_id,))
        return r[0] if r else None

    def comparison_refs_for_job(self, job_id: str) -> dict[str, dict[str, Any]]:
        rows = self._all("SELECT r.* FROM comparison_refs r JOIN cards c ON c.card_id=r.card_id WHERE c.job_id=?", (job_id,))
        return {r["card_id"]: r for r in rows}

    def set_comparison_ref(self, card_id: str, ref_id: str | None, mode: str) -> None:
        self._exec("INSERT INTO comparison_refs(card_id,ref_id,selection_mode,updated_at) VALUES(?,?,?,?) "
                   "ON CONFLICT(card_id) DO UPDATE SET ref_id=excluded.ref_id, selection_mode=excluded.selection_mode, "
                   "updated_at=excluded.updated_at", (card_id, ref_id, mode, now()))

    def clear_comparison_ref(self, card_id: str) -> None:
        self._exec("DELETE FROM comparison_refs WHERE card_id=?", (card_id,))

    def recover_interrupted(self) -> list[str]:
        marks = ",".join("?" for _ in ACTIVE_STATES)
        ids = [r["job_id"] for r in self._all(f"SELECT job_id FROM jobs WHERE status IN ({marks})", tuple(ACTIVE_STATES))]
        for jid in ids:
            # a card that was mid-call may have been billed: failed if it never had an image, else keep it
            self._exec("UPDATE cards SET status='failed', error=? WHERE job_id=? AND status='generating' AND artifact_id IS NULL",
                       ("Interrupted during the provider call; it may have been billed. Not retried automatically.", jid))
            self._exec("UPDATE cards SET status='done', error=? WHERE job_id=? AND status='generating' AND artifact_id IS NOT NULL",
                       ("Regeneration interrupted; the previous version is kept (the call may have been billed).", jid))
            self._exec("UPDATE cards SET status='planned' WHERE job_id=? AND status='queued' AND artifact_id IS NULL", (jid,))
            self._exec("UPDATE cards SET status='done' WHERE job_id=? AND status='queued' AND artifact_id IS NOT NULL", (jid,))
            self.set_status(jid, "interrupted", "Server restarted while the job was active.")
            self.set_cancel(jid, False)
            self.add_event(jid, "Server restarted: job marked interrupted. Finished cards are kept; "
                                "press Continue to generate the rest (nothing was resumed automatically).", "warn")
        return ids


# ----------------------------------------------------------------------------------------- helpers
def pilot_card_ids(cards: list[dict[str, Any]]) -> set[str]:
    """First card of each category per variant (Cat.5 golden counts as its own category). Legacy pilot scope."""
    seen: set[tuple[str, int]] = set()
    out: set[str] = set()
    for c in sorted(cards, key=lambda x: (x["variant_id"], x["position"])):
        key = (c["variant_id"], c["category"])
        if key not in seen:
            seen.add(key)
            out.add(c["card_id"])
    return out


def select_references(metas: list[dict[str, Any]], category: int, cap: int) -> tuple[list[str], str]:
    """Pick ONLY the references that apply to this card (no 'send everything' fallback):
      - category_N reference -> only cards of category N;
      - character reference  -> only golden (category 5) cards;
      - style / composition / negative -> every card of the set (negative ones are labelled NEGATIVE in the prompt).
    Under the provider cap the most specific come first. Nothing applicable -> no images (text rules only).
    -> (ref_ids, mode) with mode: none | none_applicable | global_only | category_match."""
    def of(*types: str) -> list[dict[str, Any]]:
        return [m for m in metas if m["ref_type"] in types]
    own = of(f"category_{category}")
    character = of("character") if category == 5 else []
    ordered = own + character + of("style") + of("composition") + of("negative")
    if not metas:
        mode = "none"
    elif not ordered:
        mode = "none_applicable"
    else:
        mode = "category_match" if (own or character) else "global_only"
    return [m["ref_id"] for m in ordered][:cap], mode


# ----------------------------------------------------------------------------------------- service
class JobService:
    def __init__(self, settings: Settings, gen: GenerationService, guard: CostGuard,
                 refs: ReferenceManager, store: JobStore, text_planner: TextPlanner | None = None) -> None:
        self.s, self.gen, self.guard, self.refs, self.store = settings, gen, guard, refs, store
        self.text_planner = text_planner or TextPlanner(gen.registry)
        self._worker_lock: asyncio.Lock | None = None
        self._last_text_model: str | None = None
        self._last_cached = False
        self._tasks: set[asyncio.Task] = set()
        self._backfill()

    # -- migration helpers ---------------------------------------------------------------------
    def _backfill(self) -> None:
        """Phase 3 databases have done cards without version rows: create v1 and compute QA."""
        for j in self.store.list_jobs(500):
            versions = self.store.versions_for_job(j["job_id"])
            for c in self.store.cards(j["job_id"]):
                if c["artifact_id"] and c["card_id"] not in versions:
                    meta = self.gen.store.metadata(c["artifact_id"]) or {}
                    img = meta.get("image", {})
                    v = self.store.add_version(c["card_id"], c["artifact_id"], None, img.get("sha256", ""),
                                               img.get("width", 0), img.get("height", 0))
                    self._qa_version(j["job_id"], c, v["version_id"], c["artifact_id"])

    def _qa_version(self, job_id: str, card: dict[str, Any], version_id: int, artifact_id: str) -> str:
        meta = self.gen.store.metadata(artifact_id)
        path = self.gen.store.image_path(artifact_id)
        job = self.store.get_job(job_id)
        aspect = job["request"].get("aspect_ratio", "1:1") if job else "1:1"
        if meta is None or path is None:
            qa = {"status": "failed", "checks": [{"id": "TQ-01", "name": "File readable", "status": "fail",
                                                  "detail": "artifact files are missing"}]}
        else:
            qa = qa_card(path, meta, aspect)
        self.store.update_version_qa(version_id, qa["status"], qa)
        return qa["status"]

    # -- planning ------------------------------------------------------------------------------
    def _plan(self, req: JobCreateRequest) -> tuple[SetPlan, dict[int, int], Any, list[dict[str, Any]]]:
        # Creating a job IS the Producer's Generate action: it authorises the (tiny) automatic AI planning call. Only the free
        # preview needs an explicit allow_ai_plan. Image-generation cost confirmation (_gate) is unchanged.
        plan, counts, model, metas, _ = self._plan_ex(req.model_copy(update={"allow_ai_plan": True}))
        return plan, counts, model, metas

    def _plan_cost(self, req: Any) -> float:
        """Estimated cost of ONE planning call: same text model and formula for Plan Only and Generate."""
        return estimate_plan_cost_usd(self.gen.registry.text_target().model, req.number_of_variants, req.number_of_cards, self.gen.registry.text_target().free)

    def _check_plan_budget(self, cost: float) -> None:
        if not self.s.paid_image_calls_enabled:
            raise JobFailure(402, "paid_disabled", "Paid calls are disabled (PAID_IMAGE_CALLS_ENABLED=false); "
                                                   "the AI planner is a paid Gemini text call.")

    def _plan_ex(self, req: JobCreateRequest) -> tuple[SetPlan, dict[int, int], Any, list[dict[str, Any]], float]:
        """-> (plan, resolved {1..5} distribution, model, reference metas, plan_cost_estimate_usd)."""
        try:
            model = self.gen.registry.resolve(req.provider, req.model)
        except ProviderDisabled as exc:
            raise JobFailure(400, "model_unavailable", exc.message) from exc
        if req.output_size not in ALLOWED_OUTPUT_SIZES:
            raise JobFailure(422, "bad_output_size",
                             f"Output size must be one of {', '.join(str(x) for x in ALLOWED_OUTPUT_SIZES)} px.")
        self._resolve_bundle(req.prompt_overrides)  # reject malformed session overrides before anything is planned or spent
        try:
            counts = resolve_distribution(req.category_distribution, number_of_cards=req.number_of_cards,
                                          gold_cards=req.gold_cards, set_type=req.set_type)
        except DistributionError as exc:
            raise JobFailure(422, exc.code, exc.message) from exc
        if len(req.reference_ids) != len(set(req.reference_ids)):
            raise JobFailure(400, "duplicate_references", "Duplicate reference ids.")
        if len(req.reference_ids) > self.s.max_refs_per_request:
            raise JobFailure(400, "too_many_references", f"At most {self.s.max_refs_per_request} references.")
        metas: list[dict[str, Any]] = []
        for rid in req.reference_ids:
            try:
                m = self.refs.get(rid)
            except ReferenceError_ as exc:
                raise JobFailure(400, exc.code, exc.message) from exc
            if m is None:
                raise JobFailure(400, "not_found", f"reference {rid} not found")
            metas.append(m)
        if metas and model.provider_id != "mock" and not req.reference_policy_accepted:
            raise JobFailure(400, "reference_policy_required",
                             "Confirm that you may send these reference images to the external AI provider.")
        plan, cost, _history = self._run_planner(req, counts)
        issues = validate_plan(plan, counts)
        if issues:
            raise JobFailure(422, "plan_invalid", "; ".join(issues))
        return plan, counts, model, metas, cost

    # -- prompt bundle / card requests ----------------------------------------------------------
    def _resolve_bundle(self, overrides: dict[str, str] | None) -> dict[str, dict[str, Any]]:
        lib = self.gen.prompts
        if lib is None:
            return {}
        try:
            return lib.resolve_bundle(overrides)
        except LibraryError as exc:
            raise JobFailure(422, exc.code, exc.message) from exc

    @staticmethod
    def _plan_context(plan: dict[str, Any], variant_id: str, position: int) -> dict[str, str]:
        """Variant title / concept and the card's short rationale from the stored plan (shared Set context)."""
        v = next((x for x in plan.get("variants", []) if x.get("variant_id") == variant_id), {})
        c = next((x for x in v.get("cards", []) if x.get("position") == position), {})
        return {"variant_title": v.get("title", ""), "variant_concept": v.get("concept", ""), "rationale": c.get("rationale", "")}

    def _card_request(self, rq: dict[str, Any], plan: dict[str, Any], card: dict[str, Any], ref_ids: list[str],
                      ref_mode: str, bundle: dict[str, Any] | None, *, note: str = "", is_regen: bool = False,
                      job_id: str = "") -> GenerateCardRequest:
        """THE one way a card becomes a generation request. Real generation and 'Preview Prompts' both use it, so the
        previewed prompt is compiled by the same code path with the same inputs as the real one."""
        ctx = self._plan_context(plan, card["variant_id"], card["position"])
        return GenerateCardRequest(
            collection=rq["collection"], genre=plan["genre"], object_name=card["object_name"],
            object_description=card["object_description"], category=card["category"],
            wishes=rq["wishes"], provider=rq["provider"], model=rq["model"], reference_ids=ref_ids,
            reference_policy_accepted=rq["reference_policy_accepted"], aspect_ratio=rq["aspect_ratio"],
            output_size=rq.get("output_size", 1024), confirmed=True, repair_note=note,
            prompt_bundle=bundle, **ctx,
            context={"job_id": job_id, "variant_id": card["variant_id"], "position": card["position"],
                     "card_id": card.get("card_id", ""), "reference_selection": ref_mode,
                     "regeneration": is_regen, "spec_version": card.get("spec_version", 1)},
        )

    def _prompt_row(self, req: GenerateCardRequest, card: dict[str, Any], ref_mode: str) -> dict[str, Any]:
        model, compiled, refs = self.gen.render_prompt(req)
        return {"position": card["position"], "category": card["category"], "variant_id": card["variant_id"],
                "object_name": card["object_name"], "card_id": card.get("card_id"), "source": "compiled",
                "prompt": compiled.text, "warnings": compiled.warnings, "model": model.model_id, "provider": model.provider_id,
                "references": [{"ref_id": r.ref_id, "role": r.role} for r in refs], "reference_selection": ref_mode,
                "bundle": {n: {k: v for k, v in b.items() if k != "text"} for n, b in compiled.bundle.items()},
                "session_overrides": sorted(n for n, b in compiled.bundle.items() if b["source"] == "session_override")}

    def preview_prompts(self, req: JobCreateRequest) -> dict[str, Any]:
        """Compiled prompts of every card of the PLANNED set (cached plan), before any image is generated. Free: no image
        provider is called. Uses the same card-request builder and compiler as real generation."""
        plan, counts, model, metas, _cost = self._plan_ex(req)
        bundle = self._resolve_bundle(req.prompt_overrides)
        cap = min(model.capabilities.get("max_reference_images", 0), self.s.max_refs_per_request)
        plan_json = plan.model_dump(mode="json")
        rq = req.model_dump(mode="json")
        variants: dict[str, list[dict[str, Any]]] = {}
        for v in plan.variants:
            rows = []
            for c in v.cards:
                card = {"variant_id": v.variant_id, "position": c.position, "category": c.category, "object_name": c.object_name,
                        "object_description": c.object_description}
                ref_ids, mode = select_references(metas, c.category, cap)
                if metas and cap == 0:
                    ref_ids, mode = [], "unsupported_by_model"
                rows.append(self._prompt_row(self._card_request(rq, plan_json, card, ref_ids, mode, bundle), card, mode))
            variants[v.variant_id] = rows
        return {"variants": variants, "planning_mode": plan.planning_mode, "set_type": req.set_type,
                "session_overrides": sorted(n for n, b in bundle.items() if b["source"] == "session_override"),
                "warnings": check_overrides({n: b["text"] for n, b in bundle.items() if b["source"] == "session_override"})}

    def job_prompts(self, job_id: str, variant_id: str | None = None) -> dict[str, Any]:
        """Prompts of an existing job: the exact stored prompt for generated images (immutable), the compiled preview (from the
        job's snapshot) for cards that are still planned. Never calls an image provider."""
        job = self.store.get_job(job_id)
        if job is None:
            raise JobFailure(404, "not_found", "Job not found.")
        rq = job["request"]
        bundle = rq.get("prompt_bundle") or None
        model = self.gen.registry.resolve(rq["provider"], rq["model"])
        cap = min(model.capabilities.get("max_reference_images", 0), self.s.max_refs_per_request)
        metas = [m for m in (self.refs.get(r) for r in rq["reference_ids"]) if m]
        out: list[dict[str, Any]] = []
        for card in self.store.cards(job_id):
            if variant_id and card["variant_id"] != variant_id:
                continue
            meta = self.gen.store.metadata(card["artifact_id"]) if card["artifact_id"] else None
            if meta:
                p = meta["prompt"]
                out.append({"position": card["position"], "category": card["category"], "variant_id": card["variant_id"],
                            "object_name": card["object_name"], "card_id": card["card_id"], "source": "generated",
                            "artifact_id": card["artifact_id"], "prompt": p["effective"], "warnings": p.get("warnings", []),
                            "model": meta["provider"]["model"], "provider": meta["provider"]["id"],
                            "references": [{"ref_id": r["ref_id"], "role": r["role"]} for r in meta.get("references", [])],
                            "reference_selection": meta.get("context", {}).get("reference_selection"),
                            "bundle": p.get("bundle", {}), "session_overrides": p.get("session_overrides", [])})
                continue
            ref_ids, mode = select_references(metas, card["category"], cap)
            if metas and cap == 0:
                ref_ids, mode = [], "unsupported_by_model"
            out.append(self._prompt_row(self._card_request(rq, job["plan"], card, ref_ids, mode, bundle, job_id=job_id), card, mode))
        return {"job_id": job_id, "prompts": out, "snapshot": bool(bundle),
                "bundle": {n: {k: v for k, v in b.items() if k != "text"} for n, b in (bundle or {}).items()}}

    # -- semantic planning ---------------------------------------------------------------------
    def previous_subjects(self, collection: str, genre: str, set_type: str = SET_STANDARD) -> list[str]:
        """Compact summary of subjects planned in earlier batches of the SAME Collection + Set + Set type (never of other
        themes), newest first, deduplicated and capped so the planning context cannot grow without bound."""
        want = theme_key(collection, genre, set_type)
        entries: list[tuple[str, str]] = []
        for j in self.store.list_jobs(200):  # newest first
            rq = json.loads(j["request_json"])
            if theme_key(rq.get("collection", ""), rq.get("genre", ""), rq.get("set_type", SET_STANDARD)) != want:
                continue
            for c in sorted(self.store.cards(j["job_id"]), key=lambda x: (x["variant_id"], x["position"])):
                entries.append((c["object_name"], c["object_description"]))
        return history_summary(entries)

    def _run_planner(self, req: Any, counts: dict[int, int]) -> tuple[SetPlan, float, list[str]]:
        """The ONE planning path: the semantic AI planner for every Collection / Set (Movie presets included).
        No curated pools and no silent fallback: if the text planner cannot run, the error is raised BEFORE any image job
        exists. Returns (plan, text_cost_estimate_usd, history_lines_passed_to_the_planner)."""
        if not req.allow_ai_plan:
            raise JobFailure(
                422, "ai_plan_required",
                f"'{req.collection}' / '{req.genre}' is planned by AI (a Gemini text model, a tiny cost, about "
                f"${self._plan_cost(req):.3f} for this set). Generate does this automatically; a free preview needs allow_ai_plan=true.",
                {"plan_cost_estimate_usd": self._plan_cost(req)})
        cost = self._plan_cost(req)
        given = getattr(req, "plan", None)
        if given is not None:  # a plan curated in the Metaphor Planner: use it as is, no text call, no new cost
            if given.genre.strip().lower() != req.genre.strip().lower() or len(given.variants) != req.number_of_variants:
                raise JobFailure(409, "plan_mismatch", "The curated plan no longer matches this Set / variant count. "
                                                       "Run Plan Metaphors again.")
            return given.model_copy(deep=True), 0.0, []
        try:
            history = self.previous_subjects(req.collection, req.genre, req.set_type)
            res = self.text_planner.plan(req.collection, req.genre, req.number_of_variants, counts,
                                         seed=req.seed, wishes=req.wishes, pre_call=lambda: self._check_plan_budget(cost),
                                         grand=req.set_type == SET_GRAND, history=history)
        except PlanningError as exc:
            if exc.code == "ai_plan_invalid":  # the model answered (the request completed) but the plan is unusable: estimated text cost
                self.guard.record(artifact_id=None, provider_id=self.gen.registry.text_target().provider_id, model_id=self.gen.registry.text_target().model,
                                  est_cost_usd=cost, usage={"kind": "text_plan"}, outcome="failed:ai_plan_invalid:plan", kind="plan")
            # transport/HTTP errors (ai_plan_failed) are NOT recorded: whether the provider billed them is unknown
            raise JobFailure(_PLAN_ERROR_STATUS.get(exc.code, 422), exc.code, exc.message) from exc
        if not res.cached:  # honest accounting: a real text call was made (recorded once; cached plans cost nothing)
            entry_id = self.guard.record(artifact_id=None, provider_id=self.gen.registry.text_target().provider_id, model_id=res.model,
                                         est_cost_usd=res.cost_usd, usage={"kind": "text_plan"}, outcome="ok:plan", kind="plan")
            sink = _PLAN_COST_SINK.get()
            if sink is not None and entry_id:
                sink.append(entry_id)  # create() attaches it to the batch it plans, so it is not counted twice
        self._last_text_model, self._last_cached = res.model, res.cached
        return res.plan, cost, history

    def plan_only(self, req: PlanOnlyRequest) -> dict[str, Any]:
        """Planner-only test: runs the real planner and returns the plan. Never resolves or calls an image model,
        never creates a job; a real text call is recorded in the normal budget ledger."""
        try:
            counts = resolve_distribution(req.category_distribution, number_of_cards=req.number_of_cards,
                                          gold_cards=req.gold_cards, set_type=req.set_type)
        except DistributionError as exc:
            raise JobFailure(422, exc.code, exc.message) from exc
        self._last_text_model, self._last_cached = None, False
        plan, cost, history = self._run_planner(req, counts)
        issues = validate_plan(plan, counts)
        if issues:
            raise JobFailure(422, "plan_invalid", "; ".join(issues))
        return {
            "plan": plan.model_dump(mode="json"),
            "distribution": {str(k): v for k, v in counts.items()},
            "set_type": req.set_type,
            "planning_mode": plan.planning_mode,
            "text_model": self._last_text_model if plan.planning_mode == "ai" else "rules (curated pools, no LLM)",
            "cached": bool(plan.planning_mode == "ai" and self._last_cached),
            "text_cost_estimate_usd": 0.0 if self._last_cached else round(cost, 6),
            "cost_is_estimate": True,
            "history_subjects_used": len(history),
            "history": history,
            "image_provider_called": False,
            "budget": self.guard.summary().__dict__,
        }

    def plan_regenerate(self, req: PlanRegenerateRequest) -> dict[str, Any]:
        """Metaphor Planner 'Regenerate Selected': one text call that replaces only the selected concepts. Unselected cards,
        card numbers, order, categories and distribution are unchanged. Never resolves an image model; the text cost is recorded."""
        try:
            counts = resolve_distribution(req.category_distribution, number_of_cards=req.number_of_cards,
                                          gold_cards=req.gold_cards, set_type=req.set_type)
        except DistributionError as exc:
            raise JobFailure(422, exc.code, exc.message) from exc
        if req.plan.genre.strip().lower() != req.genre.strip().lower() or len(req.plan.variants) != req.number_of_variants:
            raise JobFailure(409, "plan_mismatch", "The plan no longer matches this Set / variant count. Run Plan Metaphors again.")
        issues = validate_plan(req.plan, counts)
        if issues:
            raise JobFailure(422, "plan_invalid", "; ".join(issues))
        selected = [(r.variant_id.strip().upper(), r.position) for r in req.selected]
        cost = estimate_plan_cost_usd(self.gen.registry.text_target().model, 1, len(selected), self.gen.registry.text_target().free)
        try:
            history = self.previous_subjects(req.collection, req.genre, req.set_type)
            res = self.text_planner.regenerate_cards(req.collection, req.genre, req.plan, selected, wishes=req.wishes, seed=req.seed,
                                                     pre_call=lambda: self._check_plan_budget(cost),
                                                     grand=req.set_type == SET_GRAND, history=history)
        except PlanningError as exc:
            if exc.code == "ai_plan_invalid":  # the model WAS called and answered unusably: still an (estimated) text cost
                self.guard.record(artifact_id=None, provider_id=self.gen.registry.text_target().provider_id, model_id=self.gen.registry.text_target().model,
                                  est_cost_usd=cost, usage={"kind": "text_plan", "regenerated_cards": len(selected)},
                                  outcome="failed:ai_plan_invalid:plan", kind="plan")
            raise JobFailure(_PLAN_ERROR_STATUS.get(exc.code, 422), exc.code, exc.message) from exc
        self.guard.record(artifact_id=None, provider_id=self.gen.registry.text_target().provider_id, model_id=res.model, est_cost_usd=res.cost_usd,
                          usage={"kind": "text_plan", "regenerated_cards": len(selected)}, outcome="ok:plan", kind="plan")
        return {
            "plan": res.plan.model_dump(mode="json"),
            "distribution": {str(k): v for k, v in counts.items()},
            "set_type": req.set_type, "planning_mode": res.plan.planning_mode, "text_model": res.model, "cached": False,
            "text_cost_estimate_usd": round(res.cost_usd, 6), "cost_is_estimate": True,
            "history_subjects_used": len(history), "history": history, "image_provider_called": False,
            "regenerated": [{"variant_id": v, "position": p} for v, p in selected],
            "budget": self.guard.summary().__dict__,
        }

    @staticmethod
    def _cards_flat(plan: SetPlan, job_id: str) -> list[dict[str, Any]]:
        out = []
        for v in plan.variants:
            for c in v.cards:
                c.card_id = f"{job_id}-{v.variant_id}-{c.position:02d}"
                out.append({"card_id": c.card_id, "variant_id": v.variant_id, "position": c.position,
                            "category": c.category, "object_name": c.object_name,
                            "object_description": c.object_description})
        return out

    @staticmethod
    def _scope_cards_for(flat: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
        if scope == "full":
            return flat
        pilot = pilot_card_ids(flat)
        return [c for c in flat if c["card_id"] in pilot]

    def _scope_cards(self, plan: SetPlan, scope: str) -> list[dict[str, Any]]:
        return self._scope_cards_for(self._cards_flat(plan, "preview"), scope)

    def _estimate(self, provider_id: str, images: int, model_id: str | None = None) -> float:
        return self.guard.estimate_usd(provider_id, images, model_id)

    def preview(self, req: JobCreateRequest) -> dict[str, Any]:
        plan, counts, model, _, plan_cost = self._plan_ex(req)
        n = len(self._scope_cards(plan, req.scope))
        total = req.number_of_variants * req.number_of_cards
        est = self._estimate(model.provider_id, n, model.model_id)
        blocked = None
        try:
            self.guard.check(model.provider_id, n, **({"model_id": model.model_id} if model.provider_id == "cloudflare" else {}))
        except BudgetExceeded as exc:
            blocked = {"code": exc.code, "message": exc.reason}
        warnings = []
        if req.number_of_variants < 3:
            warnings.append("The final deliverable needs 3-5 alternative sets per genre; fewer are used here "
                            "for a budget-controlled pilot.")
        return {
            "plan": plan.model_dump(mode="json"),
            "distribution": {str(k): v for k, v in counts.items()},
            "set_type": req.set_type,
            "compliant": compliance(counts, req.set_type)[0], "compliance_note": compliance(counts, req.set_type)[1],
            "final_eligible": final_eligibility(req.number_of_cards, req.gold_cards, req.set_type)[0],
            "final_ineligible_reason": final_eligibility(req.number_of_cards, req.gold_cards, req.set_type)[1],
            "scope": req.scope,
            "number_of_cards": req.number_of_cards,
            "gold_cards": req.gold_cards,
            "output_size": req.output_size,
            "planning_mode": plan.planning_mode,
            "plan_cost_estimate_usd": plan_cost,
            "images_in_scope": n,
            "images_total": total,
            "estimated_cost_usd": est,
            "estimated_cost_full_usd": self._estimate(model.provider_id, total, model.model_id),
            "cost_is_estimate": True,
            "budget": self.guard.summary().__dict__,
            "blocked": blocked,
            "warnings": warnings,
        }

    # -- lifecycle -----------------------------------------------------------------------------
    async def create(self, req: JobCreateRequest, *, skip_gate: bool = False) -> dict[str, Any]:
        plan_entries: list[str] = []
        _PLAN_COST_SINK.set(plan_entries)  # to_thread copies the context but shares this list
        plan, counts, model, _metas = await asyncio.to_thread(self._plan, req)
        job_id = f"job-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        flat = self._cards_flat(plan, job_id)
        scope_ids = {c["card_id"] for c in self._scope_cards_for(flat, req.scope)}
        est = self._estimate(model.provider_id, len(scope_ids), model.model_id)
        if not skip_gate:
            self._gate(model.provider_id, len(scope_ids), est, req.confirmed, model.model_id)

        for c in flat:
            c["status"] = "queued" if c["card_id"] in scope_ids else "planned"
        # Immutable prompt snapshot: permanent library versions + the browser's session overrides, resolved NOW and bound to
        # the job. Every card of every variant (and later regenerations) uses exactly these texts, even if the library or the
        # browser session changes while the job runs, and the snapshot survives a browser restart (it lives in the database).
        bundle = self._resolve_bundle(req.prompt_overrides)
        request_json = req.model_dump(mode="json", exclude={"plan"})  # the plan itself lives in plan_json
        request_json["curated_plan"] = req.plan is not None
        request_json["prompt_overrides"] = sorted(req.prompt_overrides)  # names only; the texts live in the snapshot
        request_json["prompt_bundle"] = bundle
        self.store.create_job(job_id, "created", request_json, plan.model_dump(mode="json"), req.scope, flat)
        for entry_id in plan_entries:
            self.guard.attach(entry_id, job_id)  # planning cost belongs to this batch (lifetime total unchanged)
        self.store.add_event(job_id, f"Job created: {req.genre}, {req.number_of_variants} variant(s), "
                                     f"scope={req.scope} ({len(scope_ids)} images, est ${est:.3f}).")
        self.store.set_status(job_id, "planning")
        planner = ("curated Metaphor Planner plan (no new text call)" if req.plan is not None
                   else "AI planner (Gemini text)" if plan.planning_mode == "ai" else "legacy planner")
        session_names = sorted(n for n, b in bundle.items() if b["source"] == "session_override")
        if session_names:
            self.store.add_event(job_id, "Prompt snapshot uses SESSION overrides for: " + ", ".join(session_names)
                                 + " (the permanent Prompt Library is unchanged).", "warn")
        self.store.add_event(job_id, f"Planned with {planner} (seed {plan.seed}, distribution "
                                     f"{[counts[c] for c in (1, 2, 3, 4, 5)]} = Cat.1-4 + golden).")
        self.store.set_status(job_id, "plan_qa")
        self.store.add_event(job_id, f"Plan QA passed: {req.number_of_cards} cards/variant ({req.gold_cards} golden), "
                                     "unique objects, distribution matched.")
        self.store.set_status(job_id, "queued")
        self._schedule(job_id)
        return self.detail(job_id)

    async def create_batch(self, base: JobCreateRequest, genres: list[str]) -> list[dict[str, Any]]:
        """Create one job per genre under a SINGLE confirmation and a combined budget check."""
        if not 1 <= len(genres) <= 3 or len(set(g.lower() for g in genres)) != len(genres):
            raise JobFailure(400, "bad_genres", "Choose 1-3 distinct genres.")
        planned = [await asyncio.to_thread(self._plan, base.model_copy(update={"genre": g})) for g in genres]
        model = planned[0][2]
        images = sum(len(self._scope_cards(p[0], base.scope)) for p in planned)
        est = self._estimate(model.provider_id, images, model.model_id)
        self._gate(model.provider_id, images, est, base.confirmed, model.model_id)
        return [await self.create(base.model_copy(update={"genre": g}), skip_gate=True) for g in genres]

    def _gate(self, provider_id: str, images: int, est: float, confirmed: bool, model_id: str | None = None) -> None:
        if est > 0 and not confirmed:
            raise JobFailure(400, "confirmation_required",
                             f"This run is estimated at ${est:.3f} for {images} image(s). Confirm to proceed.",
                             {"estimated_cost_usd": est, "images": images})
        try:
            self.guard.check(provider_id, images, **({"model_id": model_id} if provider_id == "cloudflare" else {}))
        except BudgetExceeded as exc:
            raise JobFailure(402, exc.code, exc.reason) from exc

    def _job_for_edit(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise JobFailure(404, "not_found", "Job not found.")
        if job["status"] in ACTIVE_STATES:
            raise JobFailure(409, "job_active", "The job is still running; wait for it to finish or cancel it.")
        return job

    async def continue_job(self, job_id: str, req: ContinueRequest) -> dict[str, Any]:
        job = self._job_for_edit(job_id)
        wanted = {"planned", "failed"} if req.retry_failed else {"planned"}
        cards = [c for c in self.store.cards(job_id) if c["status"] in wanted]
        if not cards:
            raise JobFailure(400, "nothing_to_do", "No pending cards to generate.")
        provider, mid = job["request"]["provider"], job["request"]["model"]
        est = self._estimate(provider, len(cards), mid)
        self._gate(provider, len(cards), est, req.confirmed, mid)
        for c in cards:
            self.store.update_card(c["card_id"], status="queued", error=None)
        self.store.set_scope(job_id, "full")
        self.store.set_cancel(job_id, False)
        self.store.set_status(job_id, "queued")
        self.store.add_event(job_id, f"Continue: {len(cards)} card(s) queued (est ${est:.3f}).")
        self._schedule(job_id)
        return self.detail(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise JobFailure(404, "not_found", "Job not found.")
        if job["status"] not in ACTIVE_STATES:
            raise JobFailure(409, "not_active", "The job is not running.")
        self.store.set_cancel(job_id, True)
        self.store.set_status(job_id, "cancel_requested")
        self.store.add_event(job_id, "Cancel requested: the in-flight provider call (if any) will still finish; "
                                     "no further cards will be started.", "warn")
        return self.detail(job_id)

    # -- Phase 4: review, approval, regeneration, card edits -----------------------------------
    def _card_or_404(self, job_id: str, card_id: str) -> dict[str, Any]:
        card = self.store.card(job_id, card_id)
        if card is None:
            raise JobFailure(404, "not_found", "Card not found.")
        return card

    def review_card(self, job_id: str, card_id: str, req: ReviewRequest) -> dict[str, Any]:
        job = self._job_for_edit(job_id)
        card = self._card_or_404(job_id, card_id)
        cur = self.store.current_version(card_id)
        if cur is None or card["status"] != "done":
            raise JobFailure(400, "no_image", "This card has no generated image to review.")
        self.store.set_version_review(cur["version_id"], req.decision, req.note)
        self.store.add_event(job_id, f"Art review: card {card_id.split('-')[-2]}{card['position']:02d} "
                                     f"'{card['object_name']}' → {req.decision}.")
        if req.decision == "rejected" and self.store.clear_approval(job_id, card["variant_id"]):
            self.store.add_event(job_id, f"Variant {card['variant_id']} approval withdrawn: a card was rejected.", "warn")
        _ = job
        return self.detail(job_id)

    def approve_variant(self, job_id: str, variant_id: str, req: ApproveVariantRequest) -> dict[str, Any]:
        self._job_for_edit(job_id)
        d = self.detail(job_id)
        variant = next((v for v in d["variants"] if v["variant_id"] == variant_id), None)
        if variant is None:
            raise JobFailure(404, "not_found", "Variant not found.")
        blockers = []
        if variant["set_qa"]["generated"] < variant["set_qa"]["total"]:
            blockers.append(f"only {variant['set_qa']['generated']}/{variant['set_qa']['total']} cards are generated")
        if variant["set_qa"]["status"] == "failed":
            blockers.append("set QA failed: " + "; ".join(c["name"] for c in variant["set_qa"]["checks"] if c["status"] == "fail"))
        if any(c["art_review"] == "rejected" for c in variant["cards"]):
            blockers.append("a card is rejected in art review")
        if blockers:
            raise JobFailure(400, "not_approvable", "Cannot approve this variant: " + "; ".join(blockers) + ".")
        if req.mark_cards_approved:
            for c in variant["cards"]:
                cur = self.store.current_version(c["card_id"])
                if cur and cur["art_review"] != "approved":
                    self.store.set_version_review(cur["version_id"], "approved", cur["review_note"] or "")
        self.store.approve_variant(job_id, variant_id, "Local User")
        self.store.add_event(job_id, f"Variant {variant_id} approved as the final set for {d['genre']}.")
        return self.detail(job_id)

    def remove_variant(self, job_id: str, variant_id: str) -> dict[str, Any]:
        """Skip/delete a variant (also one with generated images, on the Producer's explicit request): Continue no longer spends on it."""
        job = self._job_for_edit(job_id)
        plan, rq = job["plan"], job["request"]
        if variant_id not in [v["variant_id"] for v in plan["variants"]]:
            raise JobFailure(404, "not_found", "Variant not found.")
        if len(plan["variants"]) <= 1:
            raise JobFailure(409, "last_variant", "This is the only variant of the job; it cannot be removed.")
        cards = [c for c in self.store.cards(job_id) if c["variant_id"] == variant_id]
        plan["variants"] = [v for v in plan["variants"] if v["variant_id"] != variant_id]
        rq["number_of_variants"] = len(plan["variants"])
        self.store.remove_variant(job_id, variant_id, plan, rq)
        imgs = sum(1 for c in cards if c["artifact_id"])
        self.store.add_event(job_id, f"Variant {variant_id} removed ({len(cards)} card(s), {imgs} with generated images; image files stay in History).", "warn")
        rest = self.store.cards(job_id)
        if job["status"] in ("cancelled", "partial_success", "failed") and rest and all(c["status"] == "done" for c in rest):
            self.store.set_status(job_id, "ready_for_review")  # nothing left to continue
        return self.detail(job_id)

    def unapprove_variant(self, job_id: str, variant_id: str) -> dict[str, Any]:
        self._job_for_edit(job_id)
        if not self.store.clear_approval(job_id, variant_id):
            raise JobFailure(400, "not_approved", "This variant is not approved.")
        self.store.add_event(job_id, f"Variant {variant_id} approval withdrawn.", "warn")
        return self.detail(job_id)

    async def regenerate_card(self, job_id: str, card_id: str, req: RegenerateRequest) -> dict[str, Any]:
        job = self._job_for_edit(job_id)
        card = self._card_or_404(job_id, card_id)
        if card["status"] not in ("done", "failed", "planned"):
            raise JobFailure(409, "card_busy", "The card is already queued or generating.")
        if card["artifact_id"] and card["regen_count"] >= self.s.max_regenerations_per_card:
            raise JobFailure(400, "regen_limit", f"This card already used its {self.s.max_regenerations_per_card} "
                                                 "regenerations (MAX_REGENERATIONS_PER_CARD).")
        provider, mid = job["request"]["provider"], job["request"]["model"]
        est = self._estimate(provider, 1, mid)
        self._gate(provider, 1, est, req.confirmed, mid)
        self.store.update_card(card_id, status="queued", pending_note=req.note.strip() or None, error=None)
        self.store.set_cancel(job_id, False)
        self.store.set_status(job_id, "queued")
        kind = "Regenerate" if card["artifact_id"] else "Generate"
        self.store.add_event(job_id, f"{kind} card {card['variant_id']}{card['position']:02d} '{card['object_name']}' "
                                     f"(est ${est:.3f}){' with note' if req.note.strip() else ''}.")
        self._schedule(job_id)
        return self.detail(job_id)

    def patch_card(self, job_id: str, card_id: str, req: PatchCardRequest) -> dict[str, Any]:
        """Replace the object of a card (spec Quick Action 'Replace a card'). The card goes back to
        'planned'; earlier images stay in its version history and are no longer current."""
        job = self._job_for_edit(job_id)
        card = self._card_or_404(job_id, card_id)
        if card["status"] in ("queued", "generating"):
            raise JobFailure(409, "card_busy", "The card is queued or generating.")
        name = req.object_name.strip()
        clash = [c for c in self.store.cards(job_id) if c["card_id"] != card_id and c["object_name"].strip().lower() == name.lower()]
        if clash:
            raise JobFailure(422, "duplicate_object", f"'{name}' is already used in this job.")
        self.store.retire_versions(card_id)
        self.store.update_card(card_id, object_name=name, object_description=req.object_description.strip() or name,
                               status="planned", artifact_id=None, error=None, regen_count=0, pending_note=None,
                               spec_version=card["spec_version"] + 1)
        self.store.clear_comparison_ref(card_id)
        self.store.clear_approval(job_id, card["variant_id"])
        plan = job["plan"]  # the stored plan feeds the image prompt (rationale) and the Set Plans view: never leave the old subject in it
        for v in plan.get("variants", []):
            if v.get("variant_id") == card["variant_id"]:
                for pc in v.get("cards", []):
                    if pc.get("position") == card["position"]:
                        pc["object_name"], pc["object_description"] = name, req.object_description.strip() or name
                        # never keep the old subject's reason: use the suggestion's, else a neutral one that names the NEW subject
                        pc["rationale"] = (req.rationale.strip() or f"{name}: subject chosen by the Producer for this Set")[:140]
                        if pc.get("category") == 5 and req.characters is not None:
                            pc["characters"] = req.characters
        self.store.set_plan(job_id, plan)
        self.store.add_event(job_id, f"Card {card['variant_id']}{card['position']:02d} replaced: "
                                     f"'{card['object_name']}' → '{name}' (previous images kept in history).")
        return self.detail(job_id)

    def suggest_replacement(self, job_id: str, card_id: str) -> dict[str, Any]:
        """Metaphor Planner for ONE card of an existing job: ask the text model for a replacement concept, using every other card of
        the job as context. Read-only: nothing is changed and no image is generated; the Producer reviews it, then uses
        'Replace object' and 'Generate this card'. The text call is recorded as Planning cost of this batch."""
        job = self._job_for_edit(job_id)
        card = self._card_or_404(job_id, card_id)
        if card["status"] in ("queued", "generating"):
            raise JobFailure(409, "card_busy", "The card is queued or generating.")
        rq = job["request"]
        try:
            plan = SetPlan.model_validate(job["plan"])
        except Exception as exc:  # pragma: no cover - a stored plan is always valid
            raise JobFailure(422, "plan_invalid", "The stored plan of this job cannot be read.") from exc
        live = {c["card_id"]: c for c in self.store.cards(job_id)}
        for v in plan.variants:  # the cards table is the truth (cards may have been replaced since planning)
            for pc in v.cards:
                c = next((x for x in live.values() if x["variant_id"] == v.variant_id and x["position"] == pc.position), None)
                if c:
                    pc.object_name, pc.object_description = c["object_name"], c["object_description"]
        sel = [(card["variant_id"], card["position"])]
        cost = estimate_plan_cost_usd(self.gen.registry.text_target().model, 1, 1, self.gen.registry.text_target().free)
        grand = rq.get("set_type", SET_STANDARD) == SET_GRAND
        try:
            res = self.text_planner.regenerate_cards(rq["collection"], plan.genre, plan, sel, wishes=rq.get("wishes", ""),
                                                     pre_call=lambda: self._check_plan_budget(cost), grand=grand)
        except PlanningError as exc:
            if exc.code == "ai_plan_invalid":  # the model was called and answered unusably: still an estimated text cost
                self.guard.record(artifact_id=None, provider_id=self.gen.registry.text_target().provider_id, model_id=self.gen.registry.text_target().model,
                                  est_cost_usd=cost, usage={"kind": "text_plan", "suggest_card": card_id},
                                  outcome="failed:ai_plan_invalid:plan", kind="plan", job_id=job_id)
            raise JobFailure(_PLAN_ERROR_STATUS.get(exc.code, 422), exc.code, exc.message) from exc
        self.guard.record(artifact_id=None, provider_id=self.gen.registry.text_target().provider_id, model_id=res.model, est_cost_usd=res.cost_usd,
                          usage={"kind": "text_plan", "suggest_card": card_id}, outcome="ok:plan", kind="plan", job_id=job_id)
        new = next(c for v in res.plan.variants if v.variant_id == card["variant_id"] for c in v.cards if c.position == card["position"])
        self.store.add_event(job_id, f"Suggested a replacement for card {card['variant_id']}{card['position']:02d} "
                                     f"('{card['object_name']}' → '{new.object_name}'); nothing was changed.")
        return {"suggestion": {"object_name": new.object_name, "object_description": new.object_description,
                               "rationale": new.rationale, "characters": new.characters},
                "text_model": res.model, "text_cost_estimate_usd": round(res.cost_usd, 6), "cost_is_estimate": True,
                "image_provider_called": False, "changed": False}

    def schedule_waiting(self, job_id: str) -> None:
        self._schedule(job_id)

    # -- QA ------------------------------------------------------------------------------------
    def _qa_missing(self, job_id: str) -> None:
        for c in self.store.cards(job_id):
            cur = self.store.current_version(c["card_id"])
            if cur and cur["qa_status"] == "not_run":
                self._qa_version(job_id, c, cur["version_id"], cur["artifact_id"])

    def run_qa(self, job_id: str) -> dict[str, Any]:
        """Recompute technical QA for every current image, then return the full report."""
        job = self.store.get_job(job_id)
        if job is None:
            raise JobFailure(404, "not_found", "Job not found.")
        for c in self.store.cards(job_id):
            cur = self.store.current_version(c["card_id"])
            if cur:
                self._qa_version(job_id, c, cur["version_id"], cur["artifact_id"])
        self.store.add_event(job_id, "Technical QA re-run for all current images.")
        return self.qa_report(job_id)

    def qa_report(self, job_id: str) -> dict[str, Any]:
        d = self.detail(job_id)
        cards = {c["card_id"]: {"status": c["qa_status"], "checks": (c["qa"] or {}).get("checks", []),
                                "art_review": c["art_review"], "object_name": c["object_name"],
                                "category": c["category"]}
                 for v in d["variants"] for c in v["cards"] if c["status"] == "done"}
        counts = {k: sum(1 for x in cards.values() if x["status"] == k) for k in ("passed", "flagged", "failed", "not_run")}
        return {
            "job_id": job_id, "genre": d["genre"], "generated_at": now(),
            "limitations": "Structural checks only. Text-in-image, frames and style fidelity are NOT detected "
                           "automatically; they require human art review.",
            "technical": counts, "cards": cards,
            "variants": {v["variant_id"]: v["set_qa"] for v in d["variants"]},
            "job_checks": [{"id": "HR-10", "name": "Spend recorded", "status": "pass",
                            "detail": f"estimated image spend ${d['spent_estimated_usd']:.3f} (estimate; no application spending cap)"}],
            "approved_variant_id": d["approved_variant_id"],
        }

    # -- scheduling / runner ------------------------------------------------------------------
    def _schedule(self, job_id: str) -> None:
        loop = asyncio.get_running_loop()
        if self._worker_lock is None:
            self._worker_lock = asyncio.Lock()
        task = loop.create_task(self._run(job_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        """Test helper: wait until all scheduled runs finish."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _run(self, job_id: str) -> None:
        assert self._worker_lock is not None
        async with self._worker_lock:
            try:
                await self._run_locked(job_id)
            except Exception as exc:  # never leave a job stuck in 'generating'
                self.store._exec("UPDATE cards SET status='planned' WHERE job_id=? AND status IN ('queued','generating') "
                                 "AND artifact_id IS NULL", (job_id,))
                self.store._exec("UPDATE cards SET status='done' WHERE job_id=? AND status IN ('queued','generating') "
                                 "AND artifact_id IS NOT NULL", (job_id,))
                self.store.set_status(job_id, "failed", f"Internal error: {type(exc).__name__}: {exc}")
                self.store.add_event(job_id, f"Internal error: {type(exc).__name__}: {exc}", "error")

    async def _run_locked(self, job_id: str) -> None:
        store = self.store
        job = store.get_job(job_id)
        if job is None:
            return
        rq = job["request"]
        if store.cancel_requested(job_id):
            self._finish(job_id, "cancelled")
            return
        store.set_status(job_id, "generating")
        queued = [c for c in store.cards(job_id) if c["status"] == "queued"]
        store.add_event(job_id, f"Generating {len(queued)} card(s) sequentially, retries={self.s.max_card_retries}.")
        metas = [m for m in (self.refs.get(r) for r in rq["reference_ids"]) if m]
        model = self.gen.registry.resolve(rq["provider"], rq["model"])
        cap = min(model.capabilities.get("max_reference_images", 0), self.s.max_refs_per_request)
        done = 0
        consecutive = 0
        stop: str | None = None

        for card in queued:
            if store.cancel_requested(job_id):
                stop = "cancelled"
                break
            is_regen = card["artifact_id"] is not None
            note = card["pending_note"] or ""
            ref_ids, ref_mode = select_references(metas, card["category"], cap)
            if metas and cap == 0:  # model takes no reference images: none are sent, and provenance says so
                ref_ids, ref_mode = [], "unsupported_by_model"
            store.update_card(card["card_id"], status="generating", attempts=card["attempts"] + 1,
                              reference_selection=ref_mode, error=None)
            req = self._card_request(rq, job["plan"], card, ref_ids, ref_mode, rq.get("prompt_bundle") or None,
                                     note=note, is_regen=is_regen, job_id=job_id)
            try:
                meta = await self.gen.generate(req, allow_golden=True)
            except GenerationFailure as exc:
                if exc.status == 402:  # budget guard: stop before spending more
                    store.update_card(card["card_id"], status="done" if is_regen else "planned", error=exc.message,
                                      pending_note=None)
                    store.add_event(job_id, f"Stopped by budget guard: {exc.message}", "warn")
                    stop = "budget"
                    break
                consecutive += 1
                quota = exc.code == "quota_exceeded"  # daily free allocation used up: stop at once, no fallback
                msg = f"[{exc.code}] {exc.message}"
                # a failed regeneration keeps the previous good image current
                store.update_card(card["card_id"], status="done" if is_regen else "failed", error=msg, pending_note=None)
                store.add_event(job_id, f"Card {card['card_id']} {'regeneration ' if is_regen else ''}failed: {msg}", "error")
                if quota or consecutive >= MAX_CONSECUTIVE_FAILURES:
                    store.add_event(job_id, ("Provider quota exhausted; stopping. No other provider was tried and no plan change was attempted." if quota else f"Circuit breaker: {consecutive} consecutive failures; stopping."), "error")
                    stop = "provider_errors"
                    break
                continue
            consecutive = 0
            done += 1
            img = meta["image"]
            v = store.add_version(card["card_id"], meta["artifact_id"], note or None, img["sha256"], img["width"], img["height"])
            self._qa_version(job_id, card, v["version_id"], meta["artifact_id"])
            store.update_card(card["card_id"], status="done", artifact_id=meta["artifact_id"], pending_note=None,
                              regen_count=card["regen_count"] + (1 if is_regen else 0), error=None)
            store.add_spent(job_id, float(meta["cost"]["estimated_usd"]))
            if is_regen and store.clear_approval(job_id, card["variant_id"]):
                store.add_event(job_id, f"Variant {card['variant_id']} approval withdrawn: a card was regenerated.", "warn")
            store.add_event(job_id, f"Card {card['variant_id']}{card['position']:02d} '{card['object_name']}' "
                                    f"{'regenerated (v' + str(v['version']) + ')' if is_regen else 'done'} ({done}/{len(queued)}).")

        # anything we did not get to goes back to its resting state so Continue can pick it up
        store._exec("UPDATE cards SET status='planned' WHERE job_id=? AND status='queued' AND artifact_id IS NULL", (job_id,))
        store._exec("UPDATE cards SET status='done' WHERE job_id=? AND status='queued' AND artifact_id IS NOT NULL", (job_id,))
        self._finish(job_id, stop)

    def _finish(self, job_id: str, stop: str | None) -> None:
        store = self.store
        store.set_status(job_id, "image_qa")
        self._qa_missing(job_id)
        store.set_status(job_id, "set_qa")
        d = self.detail(job_id)
        tech = {k: sum(1 for v in d["variants"] for c in v["cards"] if c["status"] == "done" and c["qa_status"] == k)
                for k in ("passed", "flagged", "failed", "not_run")}
        store.add_event(job_id, f"Technical QA: {tech['passed']} passed, {tech['flagged']} flagged, {tech['failed']} failed. "
                                f"Set QA: " + ", ".join(f"{v['variant_id']}={v['set_qa']['status']}" for v in d["variants"]) + ".")
        counts = d["counts"]
        if stop == "cancelled":
            status = "cancelled"
        elif stop in ("budget", "provider_errors") or counts["failed"]:
            status = "partial_success" if counts["done"] else "failed"
        else:
            status = "ready_for_review"
        store.set_cancel(job_id, False)
        store.set_status(job_id, status)
        store.add_event(job_id, f"Run finished: {status} ({counts['done']} generated, {counts['failed']} failed, "
                                f"{counts['planned']} still planned).")

    # -- views ---------------------------------------------------------------------------------
    def detail(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise JobFailure(404, "not_found", "Job not found.")
        cards = self.store.cards(job_id)
        versions = self.store.versions_for_job(job_id)
        approvals = self.store.approvals(job_id)
        distribution = {int(k): v for k, v in job["plan"]["distribution"].items()}
        max_regen = self.s.max_regenerations_per_card
        by_variant: dict[str, list[dict[str, Any]]] = {}
        qa_rows: dict[str, list[dict[str, Any]]] = {}
        for c in cards:
            vers = versions.get(c["card_id"], [])
            cur = next((v for v in vers if v["is_current"]), None)
            qa = json.loads(cur["qa_json"]) if cur and cur["qa_json"] else None
            by_variant.setdefault(c["variant_id"], []).append({
                "card_id": c["card_id"], "position": c["position"], "category": c["category"],
                "object_name": c["object_name"], "object_description": c["object_description"],
                "status": c["status"], "artifact_id": c["artifact_id"], "error": c["error"],
                "reference_selection": c["reference_selection"],
                "image_url": f"/api/artifacts/{c['artifact_id']}/image" if c["artifact_id"] else None,
                "qa_status": cur["qa_status"] if cur else "not_run", "qa": qa,
                "art_review": cur["art_review"] if cur else None,
                "review_note": cur["review_note"] if cur else None,
                "regen_count": c["regen_count"], "regen_remaining": max(0, max_regen - c["regen_count"]),
                "spec_version": c["spec_version"],
                "versions": [{"version": v["version"], "artifact_id": v["artifact_id"],
                              "image_url": f"/api/artifacts/{v['artifact_id']}/image", "created_at": v["created_at"],
                              "note": v["note"], "qa_status": v["qa_status"], "art_review": v["art_review"],
                              "is_current": bool(v["is_current"])} for v in vers],
            })
            qa_rows.setdefault(c["variant_id"], []).append({
                "card_id": c["card_id"], "category": c["category"], "object_name": c["object_name"],
                "status": c["status"], "sha256": cur["sha256"] if cur else None,
                "width": cur["width"] if cur else None, "height": cur["height"] if cur else None,
                "qa_status": cur["qa_status"] if cur else "not_run", "art_review": cur["art_review"] if cur else None})
        counts = {s: 0 for s in ("planned", "queued", "generating", "done", "failed")}
        for c in cards:
            counts[c["status"]] = counts.get(c["status"], 0) + 1
        provider = job["request"]["provider"]
        pending = counts["planned"]
        variants = []
        for v in job["plan"]["variants"]:
            vid = v["variant_id"]
            ap = approvals.get(vid)
            variants.append({"variant_id": vid, "title": v["title"], "concept": v["concept"],
                             "cards": by_variant.get(vid, []),
                             "approved": ap is not None, "approved_at": ap["approved_at"] if ap else None,
                             "set_qa": qa_variant(vid, qa_rows.get(vid, []), distribution)})
        rq = job["request"]
        return {
            "job_id": job_id, "status": job["status"], "error": job["error"], "scope": job["scope"],
            "created_at": job["created_at"], "updated_at": job["updated_at"],
            "genre": job["plan"]["genre"], "collection": job["request"]["collection"],
            "request": {k: v for k, v in job["request"].items() if k != "prompt_bundle"},
            "prompt_bundle": {n: {k: v for k, v in b.items() if k != "text"} for n, b in (job["request"].get("prompt_bundle") or {}).items()},
            "prompt_overrides": sorted(n for n, b in (job["request"].get("prompt_bundle") or {}).items() if b.get("source") == "session_override"),
            "seed": job["plan"]["seed"], "distribution": job["plan"]["distribution"],
            "number_of_cards": rq.get("number_of_cards") or sum(distribution.values()) or CARDS_PER_SET,
            "gold_cards": rq.get("gold_cards", distribution.get(5, 0)),
            "set_type": rq.get("set_type", SET_STANDARD),
            "compliant": compliance(distribution, rq.get("set_type", SET_STANDARD))[0],
            "compliance_note": compliance(distribution, rq.get("set_type", SET_STANDARD))[1],
            "final_eligible": final_eligibility(
                rq.get("number_of_cards") or sum(distribution.values()) or CARDS_PER_SET,
                rq.get("gold_cards", distribution.get(5, 0)), rq.get("set_type", SET_STANDARD))[0],
            "final_ineligible_reason": final_eligibility(
                rq.get("number_of_cards") or sum(distribution.values()) or CARDS_PER_SET,
                rq.get("gold_cards", distribution.get(5, 0)), rq.get("set_type", SET_STANDARD))[1],
            "output_size": rq.get("output_size", 1024),
            "planning_mode": job["plan"].get("planning_mode", "rules"),
            "notes": job["plan"]["notes"], "variants": variants,
            "counts": {**counts, "total": len(cards)},
            "spent_estimated_usd": round(job["spent_est"], 6),
            "costs": self.batch_costs().get(job_id) or self._no_costs(),
            "pending_estimate_usd": self._estimate(provider, pending, job["request"].get("model")),
            "active": job["status"] in ACTIVE_STATES,
            "can_continue": job["status"] not in ACTIVE_STATES and (pending > 0 or counts["failed"] > 0),
            "approved_variant_id": next(iter(approvals), None),
            "max_regenerations_per_card": max_regen,
            "budget": self.guard.summary().__dict__,
        }

    # -- cost tracking (estimates from the ledger; never provider billing) ------------------------------------------
    @staticmethod
    def _no_costs() -> dict[str, Any]:
        return {"planning_usd": 0.0, "generation_usd": 0.0, "total_usd": 0.0, "images_billed": 0, "planning_known": False}

    def batch_costs(self) -> dict[str, dict[str, Any]]:
        """Estimated planning / generation / total per batch (job). Image entries written before per-batch tracking are attributed
        through their artifact; planning calls without a recorded batch are NOT guessed and stay in the lifetime total only."""
        a2j = {r["a"]: r["j"] for r in self.store._all(
            "SELECT cv.artifact_id AS a, c.job_id AS j FROM card_versions cv JOIN cards c ON c.card_id=cv.card_id")}
        a2j.update({r["artifact_id"]: r["job_id"] for r in self.store._all(
            "SELECT artifact_id, job_id FROM cards WHERE artifact_id IS NOT NULL")})
        per = self.guard.by_job(a2j)
        att = set(self.guard.attachments().values())
        out = {}
        for jid, b in per.items():
            if jid != "__unattributed__":
                out[jid] = {**b, "planning_known": jid in att or any(
                    e.get("job_id") == jid and self.guard.entry_kind(e) == "plan" for e in self.guard.costed())}
        out["__unattributed__"] = per["__unattributed__"]
        return out

    def cost_report(self) -> dict[str, Any]:
        """Lifetime counters (planning + generation = total) plus an honest completeness note."""
        lt = self.guard.lifetime()
        un = self.batch_costs()["__unattributed__"]
        return {**lt, "cost_is_estimate": True, "unattributed": un,
                "incomplete_note": ("Recorded estimates only. Spending from before cost tracking was recorded, from other tools, or from "
                                    "provider-side retries is not included, so this may be lower than your provider bill.")}

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        out = []
        costs = self.batch_costs()
        for j in self.store.list_jobs(limit):
            rq = json.loads(j.pop("request_json"))
            j["genre"], j["variants"], j["collection"] = rq["genre"], rq["number_of_variants"], rq["collection"]
            j["number_of_cards"] = rq.get("number_of_cards", CARDS_PER_SET)
            j["gold_cards"] = rq.get("gold_cards", 0)
            j["set_type"] = rq.get("set_type", SET_STANDARD)
            j["final_eligible"], j["final_ineligible_reason"] = final_eligibility(
                j["number_of_cards"], j["gold_cards"], j["set_type"])
            j["output_size"] = rq.get("output_size", 1024)
            j["provider"], j["model"] = rq.get("provider"), rq.get("model")
            j["costs"] = costs.get(j["job_id"]) or self._no_costs()
            j["approved_variant_id"] = next(iter(self.store.approvals(j["job_id"])), None)
            out.append(j)
        return out
