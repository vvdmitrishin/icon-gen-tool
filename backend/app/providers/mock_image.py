"""Deterministic offline mock provider: no network, no cost. Output is visibly marked MOCK so it
can never be mistaken for live-generated artwork (spec §27.1)."""
from __future__ import annotations

import hashlib
import io
import time

from PIL import Image, ImageDraw

from .base import (
    GenerationRequest,
    GenerationResult,
    ImageProvider,
    ProviderCapabilities,
)

MOCK_CAPABILITIES = ProviderCapabilities(
    supports_reference_images=True,
    max_reference_images=14,
    aspect_ratios=("1:1",),
    cost_type="free",
)
MOCK_MODEL_ID = "mock-placeholder-v1"


class MockImageProvider(ImageProvider):
    provider_id = "mock"

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        digest = hashlib.sha256(req.prompt.encode()).digest()
        c1 = tuple(int(b) // 2 + 100 for b in digest[0:3])
        c2 = tuple(int(b) // 2 + 100 for b in digest[3:6])
        size = 512
        img = Image.new("RGB", (size, size), c1)
        d = ImageDraw.Draw(img)
        for y in range(size):  # vertical gradient
            t = y / size
            d.line([(0, y), (size, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
        d.ellipse([120, 120, 392, 392], fill=(250, 250, 250), outline=(60, 60, 60), width=6)
        d.text((196, 245), "MOCK", fill=(60, 60, 60))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return GenerationResult(
            png_bytes=buf.getvalue(),
            width=size,
            height=size,
            provider_id=self.provider_id,
            model_id=req.model_id,
            latency_ms=int((time.perf_counter() - started) * 1000),
            text_notes=["Mock output - no AI model was called."],
            request_summary={"reference_count": len(req.references), "mock": True},
        )
