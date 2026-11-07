"""OpenAI GPT Image adapter — SCAFFOLD ONLY, disabled by design (spec §12.3.1).

This module intentionally contains no network code and imports no HTTP client. It exists so the
registry, UI, settings and tests can reference the provider and its capabilities. Enabling it
requires an explicit future decision: user-provided key + billing + a real implementation.
"""
from __future__ import annotations

from .base import (
    GenerationRequest,
    GenerationResult,
    ImageProvider,
    ProviderCapabilities,
    ProviderDisabled,
)

OPENAI_CAPABILITIES = ProviderCapabilities(
    supports_reference_images=True,
    supports_image_editing=True,
    supports_transparent_background=True,
    supports_svg_output=True,
    max_reference_images=16,
    aspect_ratios=("1:1", "3:2", "2:3"),
    cost_type="per_image_token_billing",
)

OPENAI_MODELS: tuple[tuple[str, str], ...] = (
    ("gpt-image-2.5-flare", "GPT Image Flare"),
    ("gpt-image-2.5-sunburst", "GPT Image Sunburst"),
)

DISABLED_REASON = (
    "OpenAI GPT Image is planned but not configured: no API key, no billing, and no paid "
    "calls are allowed until you explicitly enable it."
)


class OpenAIImageProvider(ImageProvider):
    provider_id = "openai"

    def __init__(self, *, enabled: bool = False, paid_calls_enabled: bool = False) -> None:
        self.enabled = enabled
        self.paid_calls_enabled = paid_calls_enabled

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        if not (self.enabled and self.paid_calls_enabled):
            raise ProviderDisabled(DISABLED_REASON)
        raise ProviderDisabled(
            "OpenAI adapter is a scaffold without network code; implement it before enabling."
        )
