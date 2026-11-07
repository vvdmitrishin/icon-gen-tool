"""Real, PAID Gemini call. Skipped unless RUN_LIVE=1 and GEMINI_API_KEY is set. Costs ~1 image."""
import asyncio
import os

import pytest

from app.config import load_settings
from app.providers.base import GenerationRequest
from app.providers.gemini_image import GeminiImageProvider

pytestmark = pytest.mark.live


@pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to spend real money")
def test_live_single_image():
    s = load_settings()
    if not s.gemini_configured:
        pytest.skip("GEMINI_API_KEY not configured")
    p = GeminiImageProvider(s.gemini_api_key, send_image_config=s.gemini_send_image_config)
    res = asyncio.run(p.generate(GenerationRequest(
        prompt="A single glossy red apple icon on a plain yellow background, casual mobile game style, no text.",
        model_id=s.default_model)))
    assert res.png_bytes[:4] == b"\x89PNG" and res.width > 0
