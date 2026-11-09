"""Local cost tracking (spec §19.5). Estimates only — not provider billing.

Every successful (or possibly-billed) provider call, image OR text-planning, is appended once to an append-only JSONL ledger,
so the lifetime estimated spend survives restarts. There is NO cumulative spending cap: only the per-run size guard,
the paid-calls switch and the image-count limit remain. Provider quotas and billing limits still apply on the provider's side.

Ledger entries: {ts, entry_id, kind: "plan"|"image", job_id, artifact_id, provider, model, est_cost_usd, usage, outcome}.
Entries written before this tracking existed have no `kind`/`job_id`: `kind` is derived (outcome "ok:plan" = planning) and
image entries are attributed to a batch through their artifact_id by the caller. Nothing in the ledger is ever rewritten.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings
from ..providers.cloudflare_models import estimate_usd as cf_estimate_usd


class BudgetExceeded(Exception):
    def __init__(self, reason: str, code: str = "budget_exceeded") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True)
class BudgetSummary:
    paid_calls_enabled: bool
    job_budget_usd: float
    spent_estimated_usd: float  # lifetime = planning + generation
    planning_estimated_usd: float
    generation_estimated_usd: float
    max_card_retries: int
    note: str = (
        "Estimated cost from recorded usage, not provider billing. Provider billing is the source of truth and may lag; "
        "there is no application-side spending cap."
    )


class CostGuard:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ estimate
    def estimate_usd(self, provider_id: str, n_images: int = 1, model_id: str | None = None) -> float:
        if provider_id == "gemini":
            return round(self._s.gemini_est_cost_per_image_usd * n_images, 6)
        if provider_id == "cloudflare":
            # free plan -> 0 (free Neuron allocation applies); paid plan -> published-price estimate
            return round(cf_estimate_usd(model_id, self._s) * n_images, 6)
        return 0.0  # mock; openai is blocked before reaching here

    # --------------------------------------------------------------------- ledger
    def _entries(self) -> list[dict[str, Any]]:
        p: Path = self._s.ledger_path
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    @staticmethod
    def entry_kind(e: dict[str, Any]) -> str:
        """'plan' (text-model planning) or 'image'. Older entries have no `kind`; planning ones are marked by their outcome."""
        k = e.get("kind")
        if k in ("plan", "image"):
            return k
        return "plan" if str(e.get("outcome", "")).endswith(":plan") or (e.get("usage") or {}).get("kind") == "text_plan" else "image"

    def costed(self) -> list[dict[str, Any]]:
        """Billable entries only (attribution lines carry no cost)."""
        return [e for e in self._entries() if e.get("kind") != "attach" and float(e.get("est_cost_usd", 0) or 0) > 0]

    def lifetime(self) -> dict[str, float | int]:
        """Lifetime estimated spend; total is always planning + generation."""
        planning = generation = 0.0
        n_plan = n_img = 0
        for e in self.costed():
            c = float(e["est_cost_usd"])
            if self.entry_kind(e) == "plan":
                planning += c
                n_plan += 1
            else:
                generation += c
                n_img += 1
        planning, generation = round(planning, 6), round(generation, 6)
        return {"planning_usd": planning, "generation_usd": generation, "total_usd": round(planning + generation, 6),
                "planning_calls": n_plan, "image_calls": n_img}

    def spent_usd(self) -> float:
        return float(self.lifetime()["total_usd"])

    def attach(self, entry_id: str, job_id: str) -> None:
        """Attribute an already recorded entry (e.g. the planning call) to a batch. Appends a line; no cost is added."""
        with self._lock:
            self._s.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self._s.ledger_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"kind": "attach", "entry_id": entry_id, "job_id": job_id,
                                     "ts": datetime.now(timezone.utc).isoformat()}) + "\n")

    def attachments(self) -> dict[str, str]:
        return {e["entry_id"]: e["job_id"] for e in self._entries() if e.get("kind") == "attach" and e.get("entry_id")}

    def record(self, *, artifact_id: str | None, provider_id: str, model_id: str,
               est_cost_usd: float, usage: dict[str, Any], outcome: str,
               kind: str | None = None, job_id: str | None = None) -> str | None:
        if est_cost_usd <= 0:
            return None
        entry_id = uuid.uuid4().hex[:12]
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "entry_id": entry_id,
            "kind": kind or ("plan" if outcome.endswith(":plan") else "image"),
            "job_id": job_id,
            "artifact_id": artifact_id,
            "provider": provider_id,
            "model": model_id,
            "est_cost_usd": est_cost_usd,
            "usage": usage,
            "outcome": outcome,
        }
        with self._lock:
            self._s.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self._s.ledger_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        return entry_id

    def by_job(self, artifact_to_job: dict[str, str]) -> dict[str, dict[str, Any]]:
        """Per-batch estimated spend. Image entries: their own job_id, else the batch that owns the artifact. Planning entries:
        their own job_id or a later attachment. Entries that cannot be attributed reliably are NOT guessed (see `__unattributed__`)."""
        att = self.attachments()
        out: dict[str, dict[str, Any]] = {}
        un = {"planning_usd": 0.0, "generation_usd": 0.0, "planning_calls": 0, "image_calls": 0}
        for e in self.costed():
            kind, c = self.entry_kind(e), float(e["est_cost_usd"])
            jid = e.get("job_id") or att.get(e.get("entry_id") or "") or artifact_to_job.get(e.get("artifact_id") or "")
            if not jid:
                un["planning_usd" if kind == "plan" else "generation_usd"] += c
                un["planning_calls" if kind == "plan" else "image_calls"] += 1
                continue
            b = out.setdefault(jid, {"planning_usd": 0.0, "generation_usd": 0.0, "images_billed": 0})
            b["planning_usd" if kind == "plan" else "generation_usd"] += c
            if kind == "image":
                b["images_billed"] += 1
        for b in out.values():
            b["planning_usd"], b["generation_usd"] = round(b["planning_usd"], 6), round(b["generation_usd"], 6)
            b["total_usd"] = round(b["planning_usd"] + b["generation_usd"], 6)
        out["__unattributed__"] = {k: round(v, 6) if isinstance(v, float) else v for k, v in un.items()}
        return out

    # ---------------------------------------------------------------------- check
    def check(self, provider_id: str, n_images: int = 1, model_id: str | None = None) -> float:
        """Raise BudgetExceeded if the call must not run. Returns the estimate otherwise."""
        s = self._s
        if n_images > s.max_images_per_job:
            raise BudgetExceeded(
                f"Job asks for {n_images} images; MAX_IMAGES_PER_JOB is {s.max_images_per_job}.",
                "too_many_images",
            )
        est = self.estimate_usd(provider_id, n_images, model_id)
        if est <= 0:
            return est
        if not s.paid_image_calls_enabled:
            raise BudgetExceeded(
                "Paid image calls are disabled (PAID_IMAGE_CALLS_ENABLED=false).", "paid_disabled"
            )
        if est > s.job_budget_usd:
            raise BudgetExceeded(
                f"Estimated ${est:.3f} exceeds the per-job cap ${s.job_budget_usd:.2f}.",
                "job_cap",
            )
        return est  # no cumulative cap: lifetime spend is tracked, never blocked

    def summary(self) -> BudgetSummary:
        s = self._s
        lt = self.lifetime()
        return BudgetSummary(
            paid_calls_enabled=s.paid_image_calls_enabled,
            job_budget_usd=s.job_budget_usd,
            spent_estimated_usd=float(lt["total_usd"]),
            planning_estimated_usd=float(lt["planning_usd"]),
            generation_estimated_usd=float(lt["generation_usd"]),
            max_card_retries=s.max_card_retries,
        )
