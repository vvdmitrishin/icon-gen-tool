"""Universal ImageProvider registry + model catalogue with honest per-model status
(spec §12.2, §12.3.1). Visibility in the UI never implies the model is callable."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import Settings
from .base import (
    ImageProvider,
    ModelStatus,
    ProviderCapabilities,
    ProviderDisabled,
    ProviderError,
)
from .cloudflare_image import (
    CLOUDFLARE_CAPABILITIES,
    CLOUDFLARE_MODEL_ID,
    CloudflareImageProvider,
)
from .gemini_image import GEMINI_CAPABILITIES, GeminiImageProvider
from .mock_image import MOCK_CAPABILITIES, MOCK_MODEL_ID, MockImageProvider
from .openai_image import (
    DISABLED_REASON as OPENAI_DISABLED_REASON,
    OPENAI_CAPABILITIES,
    OPENAI_MODELS,
    OpenAIImageProvider,
)

GEMINI_MODELS: tuple[tuple[str, str, ModelStatus], ...] = (
    ("gemini-nano-banana-2.1", "Nano Banana 2.1", ModelStatus.EXTERNAL_REST_VERIFIED),
    ("gemini-3.1-flash-image", "Nano Banana 2", ModelStatus.DOCUMENTED),
    ("gemini-3.1-flash-lite-image", "Nano Banana 2 Lite", ModelStatus.DOCUMENTED),
    ("gemini-3-pro-image", "Nano Banana Pro", ModelStatus.DOCUMENTED),
)


@dataclass
class ModelInfo:
    provider_id: str
    provider_label: str
    model_id: str
    label: str
    role: str  # primary | fallback | planned | test
    status: str
    selectable: bool
    disabled_reason: str | None
    capabilities: dict[str, Any]
    est_cost_per_image_usd: float | None
    origin_badge: str
    availability_checked: bool = False
    notes: list[str] = field(default_factory=list)


class ModelRegistry:
    def __init__(
        self,
        settings: Settings,
        *,
        gemini_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._s = settings
        self._gemini_transport = gemini_transport
        self._availability: dict[str, bool] = {}
        self._live: set[tuple[str, str]] = set()
        self.availability_checked_at: str | None = None
        self.availability_error: str | None = None
        self._providers: dict[str, ImageProvider] = {}

    # --------------------------------------------------------------- providers
    def provider(self, provider_id: str) -> ImageProvider:
        if provider_id in self._providers:
            return self._providers[provider_id]
        s = self._s
        if provider_id == "gemini":
            if not s.gemini_configured:
                raise ProviderDisabled("Gemini is not configured: set GEMINI_API_KEY in your local .env.")
            p: ImageProvider = GeminiImageProvider(
                s.gemini_api_key,
                base_url=s.gemini_base_url,
                timeout_s=s.gemini_timeout_s,
                send_image_config=s.gemini_send_image_config,
                transport=self._gemini_transport,
            )
        elif provider_id == "openai":
            p = OpenAIImageProvider(
                enabled=s.openai_image_enabled, paid_calls_enabled=s.openai_paid_calls_enabled
            )
        elif provider_id == "cloudflare":
            p = CloudflareImageProvider()
        elif provider_id == "mock" and s.enable_mock_provider:
            p = MockImageProvider()
        else:
            raise ProviderDisabled(f"Unknown or disabled provider '{provider_id}'.")
        self._providers[provider_id] = p
        return p

    # ------------------------------------------------------------------ status
    def mark_live(self, provider_id: str, model_id: str) -> None:
        self._live.add((provider_id, model_id))

    def seed_live_from(self, artifact_metadata: list[dict[str, Any]]) -> None:
        for m in artifact_metadata:
            prov = m.get("provider", {})
            if prov.get("id") and prov.get("id") != "mock" and prov.get("model"):
                self.mark_live(prov["id"], prov["model"])

    def _gemini_info(self) -> list[ModelInfo]:
        s = self._s
        out = []
        for model_id, label, base_status in GEMINI_MODELS:
            reason = None
            status = base_status
            if not s.gemini_configured:
                status = ModelStatus.DISABLED_NO_KEY if s.gemini_api_enabled else ModelStatus.DISABLED_BY_FLAG
                reason = (
                    "GEMINI_API_ENABLED=false." if not s.gemini_api_enabled
                    else "GEMINI_API_KEY is not set in your local .env."
                )
                selectable = False
            else:
                avail = self._availability.get(model_id)
                if base_status == ModelStatus.EXTERNAL_REST_VERIFIED:
                    selectable = avail is not False
                    if avail is False:
                        reason = "Not listed by the Gemini API for this key (availability check)."
                elif avail is True:
                    status, selectable = ModelStatus.AVAILABLE_IN_ACCOUNT, True
                else:
                    selectable = False
                    reason = (
                        "Not listed by the Gemini API for this key (availability check)."
                        if avail is False
                        else "Documented only — run the availability check to unlock."
                    )
                if ("gemini", model_id) in self._live and selectable:
                    status = ModelStatus.APP_LIVE
            out.append(
                ModelInfo(
                    provider_id="gemini",
                    provider_label="Google Gemini",
                    model_id=model_id,
                    label=label,
                    role="primary",
                    status=status.value,
                    selectable=selectable,
                    disabled_reason=reason,
                    capabilities=asdict(GEMINI_CAPABILITIES),
                    est_cost_per_image_usd=s.gemini_est_cost_per_image_usd,
                    origin_badge=f"Live API · Gemini {label}",
                    availability_checked=model_id in self._availability,
                )
            )
        return out

    def models(self) -> list[ModelInfo]:
        s = self._s
        out = self._gemini_info()
        out.append(
            ModelInfo(
                provider_id="cloudflare",
                provider_label="Cloudflare Workers AI",
                model_id=CLOUDFLARE_MODEL_ID,
                label="FLUX.2 Klein 4B",
                role="fallback",
                status=ModelStatus.ADAPTER_PENDING.value,
                selectable=False,
                disabled_reason="Verified in an external REST test; app adapter arrives in Phase 2.",
                capabilities=asdict(CLOUDFLARE_CAPABILITIES),
                est_cost_per_image_usd=0.0,
                origin_badge="Live API · Cloudflare FLUX.2 Klein 4B",
            )
        )
        for model_id, label in OPENAI_MODELS:
            flagged = not s.openai_image_enabled
            out.append(
                ModelInfo(
                    provider_id="openai",
                    provider_label="OpenAI GPT Image",
                    model_id=model_id,
                    label=label,
                    role="planned",
                    status=(ModelStatus.DISABLED_BY_FLAG if flagged else ModelStatus.DISABLED_NO_KEY).value,
                    selectable=False,
                    disabled_reason=OPENAI_DISABLED_REASON,
                    capabilities=asdict(OPENAI_CAPABILITIES),
                    est_cost_per_image_usd=None,
                    origin_badge="Planned · OpenAI GPT Image (disabled)",
                )
            )
        if s.enable_mock_provider:
            out.append(
                ModelInfo(
                    provider_id="mock",
                    provider_label="Mock (offline)",
                    model_id=MOCK_MODEL_ID,
                    label="Placeholder generator",
                    role="test",
                    status=ModelStatus.MOCK.value,
                    selectable=True,
                    disabled_reason=None,
                    capabilities=asdict(MOCK_CAPABILITIES),
                    est_cost_per_image_usd=0.0,
                    origin_badge="Mock · no AI model called",
                )
            )
        return out

    def resolve(self, provider_id: str, model_id: str) -> ModelInfo:
        for m in self.models():
            if m.provider_id == provider_id and m.model_id == model_id:
                if not m.selectable:
                    raise ProviderDisabled(m.disabled_reason or "Model is not selectable.")
                return m
        raise ProviderDisabled(f"Unknown model '{provider_id}/{model_id}'.")

    # ------------------------------------------------------ availability check
    async def check_gemini_availability(self) -> dict[str, Any]:
        """Free ListModels call (no image billed)."""
        provider = self.provider("gemini")
        assert isinstance(provider, GeminiImageProvider)
        try:
            listed = await provider.list_models()
        except ProviderError as exc:
            self.availability_error = exc.message
            raise
        self.availability_error = None
        for model_id, _label, _st in GEMINI_MODELS:
            methods = listed.get(model_id)
            self._availability[model_id] = bool(methods) and (
                "generateContent" in methods or "predict" in methods
            )
        self.availability_checked_at = datetime.now(timezone.utc).isoformat()
        return {
            "checked_at": self.availability_checked_at,
            "available": {k: v for k, v in self._availability.items()},
            "image_like_models_listed": sorted(
                m for m in listed if "image" in m or "banana" in m
            ),
        }
