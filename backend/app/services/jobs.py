"""Set-generation jobs: SQLite store, state machine and a sequential background runner
(spec §10.4, §17 Phase 3, §19.4-19.5).

Guarantees:
  * one card = one provider call; strictly sequential; automatic retries are off unless configured;
  * explicit cost confirmation and a per-run + cumulative cap before anything is spent;
  * a circuit breaker stops the job after 2 consecutive provider failures;
  * state lives in SQLite, so a restart never loses finished cards; interrupted jobs are NOT
    auto-resumed (that could spend money without a user action) - the user presses Continue.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import Settings
from ..providers.base import ProviderDisabled
from .category_distribution import CategoryDistribution, DistributionError, resolve_distribution
from .cost_guard import BudgetExceeded, CostGuard
from .generation import GenerateCardRequest, GenerationFailure, GenerationService
from .planning import PlanningError, SetPlan, default_seed, plan_set, validate_plan
from .reference_manager import ReferenceError_, ReferenceManager

ACTIVE_STATES = {"created", "planning", "plan_qa", "queued", "generating", "cancel_requested"}
MAX_CONSECUTIVE_FAILURES = 2
UNIVERSAL_REF_TYPES = {"style", "composition", "negative"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobFailure(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}


class JobCreateRequest(BaseModel):
    collection: str = Field(default="Movie Collection", min_length=1, max_length=80)
    genre: str = Field(min_length=1, max_length=80)
    wishes: str = Field(default="", max_length=2000)
    number_of_variants: int = Field(default=1, ge=1, le=5)
    category_distribution: CategoryDistribution | None = None
    provider: str
    model: str
    reference_ids: list[str] = Field(default_factory=list)
    reference_policy_accepted: bool = False
    aspect_ratio: Literal["1:1", "3:4", "4:3", "9:16", "16:9"] = "1:1"
    scope: Literal["pilot", "full"] = "pilot"
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    confirmed: bool = False


class ContinueRequest(BaseModel):
    confirmed: bool = False
    retry_failed: bool = False


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
  reference_selection TEXT, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cards_job ON cards(job_id);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, ts TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_job ON events(job_id, id);
"""


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        return c

    def _exec(self, sql: str, args: tuple = ()) -> None:
        with self._lock, self._conn() as c:
            c.execute(sql, args)

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock, self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

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
        self._exec("UPDATE jobs SET status=?, error=?, updated_at=? WHERE job_id=?",
                   (status, error, now(), job_id))

    def set_scope(self, job_id: str, scope: str) -> None:
        self._exec("UPDATE jobs SET scope=?, updated_at=? WHERE job_id=?", (scope, now(), job_id))

    def set_cancel(self, job_id: str, value: bool) -> None:
        self._exec("UPDATE jobs SET cancel_requested=?, updated_at=? WHERE job_id=?",
                   (1 if value else 0, now(), job_id))

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
        return self._all("SELECT id,ts,level,message FROM events WHERE job_id=? AND id>? ORDER BY id",
                         (job_id, after))

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

    def recover_interrupted(self) -> list[str]:
        marks = ",".join("?" for _ in ACTIVE_STATES)
        ids = [r["job_id"] for r in self._all(f"SELECT job_id FROM jobs WHERE status IN ({marks})",
                                              tuple(ACTIVE_STATES))]
        for jid in ids:
            self._exec("UPDATE cards SET status='failed', error=? WHERE job_id=? AND status='generating'",
                       ("Interrupted during the provider call; it may have been billed. Not retried automatically.", jid))
            self._exec("UPDATE cards SET status='planned' WHERE job_id=? AND status='queued'", (jid,))
            self.set_status(jid, "interrupted", "Server restarted while the job was active.")
            self.set_cancel(jid, False)
            self.add_event(jid, "Server restarted: job marked interrupted. Finished cards are kept; "
                                "press Continue to generate the rest (nothing was resumed automatically).",
                           "warn")
        return ids


# ----------------------------------------------------------------------------------------- helpers
def pilot_card_ids(cards: list[dict[str, Any]]) -> set[str]:
    """First card of each category per variant (4 per variant)."""
    seen: set[tuple[str, int]] = set()
    out: set[str] = set()
    for c in sorted(cards, key=lambda x: (x["variant_id"], x["position"])):
        key = (c["variant_id"], c["category"])
        if key not in seen:
            seen.add(key)
            out.add(c["card_id"])
    return out


def select_references(metas: list[dict[str, Any]], category: int, cap: int) -> tuple[list[str], str]:
    """Category-typed refs for this card + universal (style/composition/negative) refs; if nothing
    matches, fall back to every selected ref so the card is still style-guided."""
    matched = [m for m in metas if m["ref_type"] == f"category_{category}" or m["ref_type"] in UNIVERSAL_REF_TYPES]
    mode = "category_match"
    if not matched and metas:
        matched, mode = list(metas), "fallback_all"
    if not metas:
        mode = "none"
    return [m["ref_id"] for m in matched][:cap], mode


# ----------------------------------------------------------------------------------------- service
class JobService:
    def __init__(self, settings: Settings, gen: GenerationService, guard: CostGuard,
                 refs: ReferenceManager, store: JobStore) -> None:
        self.s, self.gen, self.guard, self.refs, self.store = settings, gen, guard, refs, store
        self._worker_lock: asyncio.Lock | None = None
        self._tasks: set[asyncio.Task] = set()

    # -- planning ------------------------------------------------------------------------------
    def _plan(self, req: JobCreateRequest) -> tuple[SetPlan, dict[int, int], Any, list[dict[str, Any]]]:
        try:
            model = self.gen.registry.resolve(req.provider, req.model)
        except ProviderDisabled as exc:
            raise JobFailure(400, "model_unavailable", exc.message) from exc
        try:
            counts = resolve_distribution(req.category_distribution)
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
        try:
            plan = plan_set(req.genre, req.number_of_variants, counts, req.seed)
        except PlanningError as exc:
            raise JobFailure(422, exc.code, exc.message) from exc
        issues = validate_plan(plan, counts)
        if issues:
            raise JobFailure(422, "plan_invalid", "; ".join(issues))
        return plan, counts, model, metas

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

    def _scope_cards(self, plan: SetPlan, scope: str) -> list[dict[str, Any]]:
        return self._scope_cards_for(self._cards_flat(plan, "preview"), scope)

    def _estimate(self, provider_id: str, images: int) -> float:
        return self.guard.estimate_usd(provider_id, images)

    def preview(self, req: JobCreateRequest) -> dict[str, Any]:
        plan, counts, model, _ = self._plan(req)
        n = len(self._scope_cards(plan, req.scope))
        est = self._estimate(model.provider_id, n)
        blocked = None
        try:
            self.guard.check(model.provider_id, n)
        except BudgetExceeded as exc:
            blocked = {"code": exc.code, "message": exc.reason}
        warnings = []
        if req.number_of_variants < 3:
            warnings.append("The final deliverable needs 3-5 alternative sets per genre; fewer are used here "
                            "for a budget-controlled pilot.")
        return {
            "plan": plan.model_dump(mode="json"),
            "distribution": {str(k): v for k, v in counts.items()},
            "scope": req.scope,
            "images_in_scope": n,
            "images_total": req.number_of_variants * 10,
            "estimated_cost_usd": est,
            "estimated_cost_full_usd": self._estimate(model.provider_id, req.number_of_variants * 10),
            "cost_is_estimate": True,
            "budget": self.guard.summary().__dict__,
            "blocked": blocked,
            "warnings": warnings,
        }

    # -- lifecycle -----------------------------------------------------------------------------
    async def create(self, req: JobCreateRequest) -> dict[str, Any]:
        plan, counts, model, _metas = self._plan(req)
        job_id = f"job-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        flat = self._cards_flat(plan, job_id)
        scope_ids = {c["card_id"] for c in self._scope_cards_for(flat, req.scope)}
        est = self._estimate(model.provider_id, len(scope_ids))
        self._gate(model.provider_id, len(scope_ids), est, req.confirmed)

        for c in flat:
            c["status"] = "queued" if c["card_id"] in scope_ids else "planned"
        self.store.create_job(job_id, "created", req.model_dump(mode="json"),
                              plan.model_dump(mode="json"), req.scope, flat)
        self.store.add_event(job_id, f"Job created: {req.genre}, {req.number_of_variants} variant(s), "
                                     f"scope={req.scope} ({len(scope_ids)} images, est ${est:.3f}).")
        self.store.set_status(job_id, "planning")
        self.store.add_event(job_id, f"Planned with rules planner (seed {plan.seed}, distribution "
                                     f"{[counts[c] for c in (1, 2, 3, 4)]}).")
        self.store.set_status(job_id, "plan_qa")
        self.store.add_event(job_id, "Plan QA passed: 10 cards/variant, unique objects, distribution matched, no Cat. 5.")
        self.store.set_status(job_id, "queued")
        self._schedule(job_id)
        return self.detail(job_id)

    @staticmethod
    def _scope_cards_for(flat: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
        if scope == "full":
            return flat
        pilot = pilot_card_ids(flat)
        return [c for c in flat if c["card_id"] in pilot]

    def _gate(self, provider_id: str, images: int, est: float, confirmed: bool) -> None:
        if est > 0 and not confirmed:
            raise JobFailure(400, "confirmation_required",
                             f"This run is estimated at ${est:.3f} for {images} image(s). Confirm to proceed.",
                             {"estimated_cost_usd": est, "images": images})
        try:
            self.guard.check(provider_id, images)
        except BudgetExceeded as exc:
            raise JobFailure(402, exc.code, exc.reason) from exc

    async def continue_job(self, job_id: str, req: ContinueRequest) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise JobFailure(404, "not_found", "Job not found.")
        if job["status"] in ACTIVE_STATES:
            raise JobFailure(409, "job_active", "The job is still running.")
        wanted = {"planned", "failed"} if req.retry_failed else {"planned"}
        cards = [c for c in self.store.cards(job_id) if c["status"] in wanted]
        if not cards:
            raise JobFailure(400, "nothing_to_do", "No pending cards to generate.")
        provider = job["request"]["provider"]
        est = self._estimate(provider, len(cards))
        self._gate(provider, len(cards), est, req.confirmed)
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

    # -- runner --------------------------------------------------------------------------------
    async def _run(self, job_id: str) -> None:
        assert self._worker_lock is not None
        async with self._worker_lock:
            try:
                await self._run_locked(job_id)
            except Exception as exc:  # never leave a job stuck in 'generating'
                self.store._exec("UPDATE cards SET status='planned' WHERE job_id=? AND status IN ('queued','generating')",
                                 (job_id,))
                self.store.set_status(job_id, "failed", f"Internal error: {type(exc).__name__}: {exc}")
                self.store.add_event(job_id, f"Internal error: {type(exc).__name__}: {exc}", "error")

    async def _run_locked(self, job_id: str) -> None:
        store = self.store
        job = store.get_job(job_id)
        if job is None:
            return
        rq = job["request"]
        if store.cancel_requested(job_id):
            self._finish(job_id, "cancelled", None, ran=0, done=0)
            return
        store.set_status(job_id, "generating")
        queued = [c for c in store.cards(job_id) if c["status"] == "queued"]
        store.add_event(job_id, f"Generating {len(queued)} card(s) sequentially, retries={self.s.max_card_retries}.")
        metas = [m for m in (self.refs.get(r) for r in rq["reference_ids"]) if m]
        model = self.gen.registry.resolve(rq["provider"], rq["model"])
        cap = min(model.capabilities.get("max_reference_images", 0), self.s.max_refs_per_request)
        done = failed = consecutive = 0
        stop: str | None = None

        for card in queued:
            if store.cancel_requested(job_id):
                stop = "cancelled"
                break
            ref_ids, ref_mode = select_references(metas, card["category"], cap)
            store.update_card(card["card_id"], status="generating", attempts=card["attempts"] + 1,
                              reference_selection=ref_mode, error=None)
            req = GenerateCardRequest(
                collection=rq["collection"], genre=job["plan"]["genre"], object_name=card["object_name"],
                object_description=card["object_description"], category=card["category"],
                wishes=rq["wishes"], provider=rq["provider"], model=rq["model"], reference_ids=ref_ids,
                reference_policy_accepted=rq["reference_policy_accepted"], aspect_ratio=rq["aspect_ratio"],
                confirmed=True,
                context={"job_id": job_id, "variant_id": card["variant_id"], "position": card["position"],
                         "card_id": card["card_id"], "reference_selection": ref_mode},
            )
            try:
                meta = await self.gen.generate(req)
            except GenerationFailure as exc:
                if exc.status == 402:  # budget guard: stop before spending more
                    store.update_card(card["card_id"], status="planned", error=exc.message)
                    store.add_event(job_id, f"Stopped by budget guard: {exc.message}", "warn")
                    stop = "budget"
                    break
                failed += 1
                consecutive += 1
                store.update_card(card["card_id"], status="failed", error=f"[{exc.code}] {exc.message}")
                store.add_event(job_id, f"Card {card['card_id']} failed: [{exc.code}] {exc.message}", "error")
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    store.add_event(job_id, f"Circuit breaker: {consecutive} consecutive failures; stopping.", "error")
                    stop = "provider_errors"
                    break
                continue
            consecutive = 0
            done += 1
            store.update_card(card["card_id"], status="done", artifact_id=meta["artifact_id"])
            store.add_spent(job_id, float(meta["cost"]["estimated_usd"]))
            store.add_event(job_id, f"Card {card['variant_id']}{card['position']:02d} '{card['object_name']}' done "
                                    f"({done}/{len(queued)}).")

        # anything we did not get to goes back to 'planned' so Continue can pick it up
        store._exec("UPDATE cards SET status='planned' WHERE job_id=? AND status='queued'", (job_id,))
        self._finish(job_id, stop, None, ran=len(queued), done=done, failed=failed)

    def _finish(self, job_id: str, stop: str | None, err: str | None, *, ran: int, done: int, failed: int = 0) -> None:
        if stop == "cancelled":
            status = "cancelled"
        elif stop in ("budget", "provider_errors") or failed:
            status = "partial_success" if done else "failed"
        else:
            status = "ready_for_review"
        self.store.set_cancel(job_id, False)
        self.store.set_status(job_id, status, err)
        pending = sum(1 for c in self.store.cards(job_id) if c["status"] == "planned")
        self.store.add_event(job_id, f"Run finished: {status} ({done} generated, {failed} failed, "
                                     f"{pending} still planned).")

    # -- views ---------------------------------------------------------------------------------
    def detail(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise JobFailure(404, "not_found", "Job not found.")
        cards = self.store.cards(job_id)
        by_variant: dict[str, list[dict[str, Any]]] = {}
        for c in cards:
            by_variant.setdefault(c["variant_id"], []).append({
                "card_id": c["card_id"], "position": c["position"], "category": c["category"],
                "object_name": c["object_name"], "object_description": c["object_description"],
                "status": c["status"], "artifact_id": c["artifact_id"], "error": c["error"],
                "reference_selection": c["reference_selection"],
                "image_url": f"/api/artifacts/{c['artifact_id']}/image" if c["artifact_id"] else None,
            })
        counts = {s: 0 for s in ("planned", "queued", "generating", "done", "failed")}
        for c in cards:
            counts[c["status"]] = counts.get(c["status"], 0) + 1
        provider = job["request"]["provider"]
        pending = counts["planned"]
        return {
            "job_id": job_id, "status": job["status"], "error": job["error"], "scope": job["scope"],
            "created_at": job["created_at"], "updated_at": job["updated_at"],
            "genre": job["plan"]["genre"], "request": job["request"],
            "seed": job["plan"]["seed"], "distribution": job["plan"]["distribution"],
            "notes": job["plan"]["notes"],
            "variants": [{"variant_id": v["variant_id"], "title": v["title"], "concept": v["concept"],
                          "cards": by_variant.get(v["variant_id"], [])} for v in job["plan"]["variants"]],
            "counts": {**counts, "total": len(cards)},
            "spent_estimated_usd": round(job["spent_est"], 6),
            "pending_estimate_usd": self._estimate(provider, pending),
            "active": job["status"] in ACTIVE_STATES,
            "can_continue": job["status"] not in ACTIVE_STATES and (pending > 0 or counts["failed"] > 0),
            "budget": self.guard.summary().__dict__,
        }

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        out = []
        for j in self.store.list_jobs(limit):
            rq = json.loads(j.pop("request_json"))
            j["genre"], j["variants"] = rq["genre"], rq["number_of_variants"]
            out.append(j)
        return out
