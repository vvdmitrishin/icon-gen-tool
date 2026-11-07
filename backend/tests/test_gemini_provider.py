import asyncio
import base64
import io

import pytest
from PIL import Image

from app.providers.base import GenerationRequest, ProviderError, ReferenceImage
from app.providers.gemini_image import GeminiImageProvider
from tests.conftest import FAKE_KEY, Recorder, gemini_ok_response, png_bytes


def provider(rec, **kw):
    return GeminiImageProvider(FAKE_KEY, transport=rec.transport, **kw)


def req(refs=(), aspect="1:1"):
    return GenerationRequest(prompt="draw a helmet", model_id="gemini-nano-banana-2.1",
                             references=tuple(refs), aspect_ratio=aspect)


def run(coro):
    return asyncio.run(coro)


def test_text_only_request_shape_and_png_result():
    rec = Recorder()
    res = run(provider(rec).generate(req()))
    r = rec.requests[-1]
    assert str(r.url).endswith("/models/gemini-nano-banana-2.1:generateContent")
    assert r.headers["x-goog-api-key"] == FAKE_KEY
    assert FAKE_KEY not in str(r.url)  # key never in URL
    body = rec.last_json()
    assert body["contents"][0]["parts"] == [{"text": "draw a helmet"}]
    assert "responseFormat" not in str(body)  # known-400 field from spec v1.6
    assert body["generationConfig"]["imageConfig"] == {"aspectRatio": "1:1"}
    assert res.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert (res.width, res.height) == (64, 48)
    assert res.usage["candidatesTokenCount"] == 1290
    assert res.text_notes == ["Here is your image."]


def test_reference_sent_as_inline_data():
    rec = Recorder()
    ref_png = png_bytes((10, 10), (0, 255, 0))
    ref = ReferenceImage("ref-aaaaaaaaaa", ref_png, "sha")
    run(provider(rec).generate(req([ref])))
    parts = rec.last_json()["contents"][0]["parts"]
    assert len(parts) == 2
    assert parts[1]["inlineData"]["mimeType"] == "image/png"
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == ref_png


def test_image_config_can_be_disabled():
    rec = Recorder()
    run(provider(rec, send_image_config=False).generate(req()))
    assert "imageConfig" not in rec.last_json()["generationConfig"]


def test_jpeg_response_is_normalized_to_png():
    rec = Recorder(body=gemini_ok_response(image=png_bytes(fmt="JPEG"), fmt="JPEG"))
    res = run(provider(rec).generate(req()))
    assert res.png_bytes[:4] == b"\x89PNG"
    Image.open(io.BytesIO(res.png_bytes)).verify()


def test_http_error_is_sanitized_and_not_billable():
    rec = Recorder(status=400, body={"error": {"message": f"bad key {FAKE_KEY}"}})
    with pytest.raises(ProviderError) as e:
        run(provider(rec).generate(req()))
    assert e.value.http_status == 400 and not e.value.billable
    assert FAKE_KEY not in e.value.message and "***" in e.value.message


def test_429_is_retryable():
    rec = Recorder(status=429, body={"error": {"message": "quota"}})
    with pytest.raises(ProviderError) as e:
        run(provider(rec).generate(req()))
    assert e.value.retryable


def test_200_without_image_is_billable_error():
    body = {"candidates": [{"content": {"parts": [{"text": "I can't draw that."}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 50}}
    rec = Recorder(body=body)
    with pytest.raises(ProviderError) as e:
        run(provider(rec).generate(req()))
    assert e.value.code == "no_image" and e.value.billable
    assert e.value.usage == {"promptTokenCount": 50}


def test_prompt_block_reason_reported():
    rec = Recorder(body={"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(ProviderError) as e:
        run(provider(rec).generate(req()))
    assert "SAFETY" in e.value.message


def test_list_models_parses_and_strips_prefix():
    rec = Recorder(body={"models": [
        {"name": "models/gemini-nano-banana-2.1",
         "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
    ]})
    out = run(provider(rec).list_models())
    assert out["gemini-nano-banana-2.1"] == ["generateContent"]
    assert rec.requests[-1].headers["x-goog-api-key"] == FAKE_KEY
