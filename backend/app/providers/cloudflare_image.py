"""Cloudflare Workers AI / FLUX.2 Klein 4B — verified free fallback, adapter lands in Phase 2.

Registered now so the registry/UI show it honestly as `adapter_pending`; calling it raises.
"""
from __future__ import annotations

from .base import (
    GenerationRequest,
    GenerationResult,
    ImageProvider,
    ProviderCapabilities,
    ProviderDisabled,
)

CLOUDFLARE_MODEL_ID = "@cf/black-forest-labs/flux-2-klein-4b"

CLOUDFLARE_CAPABILITIES = ProviderCapabilities(
    supports_reference_images=True,  # input_image_0 verified in external REST smoke test
    max_reference_images=1,
    aspect_ratios=("1:1",),
    cost_type="free_tier",
)


class CloudflareImageProvider(ImageProvider):
    provider_id = "cloudflare"

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        raise ProviderDisabled(
            "Cloudflare FLUX.2 Klein 4B adapter is scheduled for Phase 2 and is not implemented yet."
        )
