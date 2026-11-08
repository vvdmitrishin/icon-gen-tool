"""Runtime settings, read from environment (and a git-ignored `.env`). Server-side only."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _float(v: str | None, default: float) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


def _int(v: str | None, default: int) -> int:
    try:
        return int(v) if v not in (None, "") else default
    except ValueError:
        return default


def _path(v: str | None, default: str) -> Path:
    p = Path(v or default)
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


@dataclass(frozen=True)
class Settings:
    default_provider: str = "gemini"
    default_model: str = "gemini-nano-banana-2.1"

    gemini_api_key: str = field(default="", repr=False)
    gemini_api_enabled: bool = True
    gemini_send_image_config: bool = True
    gemini_timeout_s: float = 120.0
    gemini_est_cost_per_image_usd: float = 0.067
    gemini_text_model: str = "gemini-2.5-flash"  # AI set planner (TextPlanner), one tiny text call per plan
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    paid_image_calls_enabled: bool = True
    job_budget_usd: float = 0.75
    total_budget_usd: float = 4.00
    max_card_retries: int = 0
    max_regenerations_per_card: int = 2
    max_parallel_generations: int = 1
    max_images_per_job: int = 50

    cloudflare_account_id: str = ""
    cloudflare_api_token: str = field(default="", repr=False)
    # "free" = Workers Free plan assumed (cost estimate 0, daily Neuron allocation applies);
    # "paid" = Workers Paid assumed (estimates apply and calls need explicit confirmation). Never auto-changed.
    cloudflare_plan: str = "free"
    cloudflare_est_cost_per_image_usd: float | None = None  # optional override of the built-in estimate

    openai_image_enabled: bool = False
    openai_paid_calls_enabled: bool = False
    openai_api_key: str = field(default="", repr=False)

    enable_mock_provider: bool = True

    data_root: Path = REPO_ROOT / "data"
    artifact_root: Path = REPO_ROOT / "data" / "artifacts"
    max_upload_bytes: int = 10 * 1024 * 1024
    max_refs_per_request: int = 5

    @property
    def reference_root(self) -> Path:
        return self.data_root / "references"

    @property
    def database_path(self) -> Path:
        return self.data_root / "collection_studio.sqlite3"

    @property
    def ledger_path(self) -> Path:
        return self.data_root / "ledger.jsonl"

    @property
    def cloudflare_configured(self) -> bool:
        return bool(self.cloudflare_account_id.strip()) and bool(self.cloudflare_api_token.strip())

    @property
    def gemini_configured(self) -> bool:
        return self.gemini_api_enabled and bool(self.gemini_api_key.strip())

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        g = e.get
        return cls(
            default_provider=g("DEFAULT_IMAGE_PROVIDER") or "gemini",
            default_model=g("DEFAULT_IMAGE_MODEL") or "gemini-nano-banana-2.1",
            gemini_api_key=g("GEMINI_API_KEY", "") or "",
            gemini_api_enabled=_bool(g("GEMINI_API_ENABLED"), True),
            gemini_text_model=(g("GEMINI_TEXT_MODEL") or "gemini-2.5-flash").strip(),
            gemini_send_image_config=_bool(g("GEMINI_SEND_IMAGE_CONFIG"), True),
            gemini_timeout_s=_float(g("GEMINI_TIMEOUT_SECONDS"), 120.0),
            gemini_est_cost_per_image_usd=_float(g("GEMINI_EST_COST_PER_IMAGE_USD"), 0.067),
            paid_image_calls_enabled=_bool(g("PAID_IMAGE_CALLS_ENABLED"), True),
            job_budget_usd=_float(g("GEMINI_JOB_BUDGET_USD"), 0.75),
            total_budget_usd=_float(g("GEMINI_TOTAL_BUDGET_USD"), 4.00),
            max_card_retries=_int(g("MAX_CARD_RETRIES"), 0),
            max_regenerations_per_card=_int(g("MAX_REGENERATIONS_PER_CARD"), 2),
            max_parallel_generations=_int(g("MAX_PARALLEL_GENERATIONS"), 1),
            max_images_per_job=_int(g("MAX_IMAGES_PER_JOB"), 50),
            cloudflare_account_id=g("CLOUDFLARE_ACCOUNT_ID", "") or "",
            cloudflare_api_token=g("CLOUDFLARE_API_TOKEN", "") or "",
            cloudflare_plan="paid" if (g("CLOUDFLARE_PLAN") or "free").strip().lower() == "paid" else "free",
            cloudflare_est_cost_per_image_usd=(_float(g("CLOUDFLARE_EST_COST_PER_IMAGE_USD"), -1.0)
                                               if g("CLOUDFLARE_EST_COST_PER_IMAGE_USD") else None),
            openai_image_enabled=_bool(g("OPENAI_IMAGE_ENABLED"), False),
            openai_paid_calls_enabled=_bool(g("OPENAI_PAID_CALLS_ENABLED"), False),
            openai_api_key=g("OPENAI_API_KEY", "") or "",
            enable_mock_provider=_bool(g("ENABLE_MOCK_PROVIDER"), True),
            data_root=_path(g("DATA_ROOT"), "./data"),
            artifact_root=_path(g("ARTIFACT_ROOT"), "./data/artifacts"),
            max_upload_bytes=_int(g("MAX_UPLOAD_BYTES"), 10 * 1024 * 1024),
            max_refs_per_request=_int(g("MAX_REFS_PER_REQUEST"), 5),
        )


def load_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env", override=False)
    return Settings.from_env()
