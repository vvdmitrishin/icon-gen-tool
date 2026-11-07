"""Best-effort local cost guard (spec §19.5). Estimates only — not a Google-side hard limit.

Every successful (or possibly-billed) provider call is appended to a JSONL ledger so the
cumulative estimated spend survives restarts. Generation is refused before the call if the
estimate would break any cap.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings


class BudgetExceeded(Exception):
    def __init__(self, reason: str, code: str = "budget_exceeded") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True)
class BudgetSummary:
    paid_calls_enabled: bool
    job_budget_usd: float
    total_budget_usd: float
    spent_estimated_usd: float
    remaining_estimated_usd: float
    max_card_retries: int
    note: str = (
        "Local estimate only. Google Billing is the source of truth and may lag; "
        "this guard is not a hard limit on Google's side."
    )


class CostGuard:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ estimate
    def estimate_usd(self, provider_id: str, n_images: int = 1) -> float:
        if provider_id == "gemini":
            return round(self._s.gemini_est_cost_per_image_usd * n_images, 6)
        return 0.0  # mock / cloudflare free tier; openai is blocked before reaching here

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

    def spent_usd(self) -> float:
        return round(sum(float(e.get("est_cost_usd", 0)) for e in self._entries()), 6)

    def record(self, *, artifact_id: str | None, provider_id: str, model_id: str,
               est_cost_usd: float, usage: dict[str, Any], outcome: str) -> None:
        if est_cost_usd <= 0:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
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

    # ---------------------------------------------------------------------- check
    def check(self, provider_id: str, n_images: int = 1) -> float:
        """Raise BudgetExceeded if the call must not run. Returns the estimate otherwise."""
        s = self._s
        if n_images > s.max_images_per_job:
            raise BudgetExceeded(
                f"Job asks for {n_images} images; MAX_IMAGES_PER_JOB is {s.max_images_per_job}.",
                "too_many_images",
            )
        est = self.estimate_usd(provider_id, n_images)
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
        spent = self.spent_usd()
        if spent + est > s.total_budget_usd:
            raise BudgetExceeded(
                f"Estimated ${est:.3f} on top of ${spent:.3f} already spent would exceed the "
                f"total cap ${s.total_budget_usd:.2f}.",
                "total_cap",
            )
        return est

    def summary(self) -> BudgetSummary:
        s = self._s
        spent = self.spent_usd()
        return BudgetSummary(
            paid_calls_enabled=s.paid_image_calls_enabled,
            job_budget_usd=s.job_budget_usd,
            total_budget_usd=s.total_budget_usd,
            spent_estimated_usd=spent,
            remaining_estimated_usd=round(max(s.total_budget_usd - spent, 0.0), 6),
            max_card_retries=s.max_card_retries,
        )
