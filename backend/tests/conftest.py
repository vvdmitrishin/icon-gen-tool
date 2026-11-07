import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app.config import Settings

FAKE_KEY = "AIzaSy-FAKE-TEST-KEY-1234567890"


def png_bytes(size=(64, 48), color=(200, 30, 30), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        gemini_api_key=FAKE_KEY,
        data_root=tmp_path / "data",
        artifact_root=tmp_path / "data" / "artifacts",
        job_budget_usd=0.75,
        total_budget_usd=2.00,
    )


def gemini_ok_response(image=None, text="Here is your image.", usage=None, fmt="PNG"):
    image = image or png_bytes(fmt=fmt)
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": text},
                        {"inlineData": {"mimeType": "image/png",
                                        "data": base64.b64encode(image).decode()}},
                    ]
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": usage or {"promptTokenCount": 120, "candidatesTokenCount": 1290},
    }


class Recorder:
    """httpx MockTransport that records requests and replays a scripted response."""

    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body if body is not None else gemini_ok_response()
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    def last_json(self):
        return json.loads(self.requests[-1].content)


def art_png(size: int = 512, seed: int = 0) -> bytes:
    """A non-blank, seed-distinct test image (gradient + ellipse) so QA and duplicate checks are realistic."""
    from PIL import ImageDraw

    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    c1 = ((seed * 53) % 200 + 30, (seed * 97) % 200 + 30, (seed * 31) % 200 + 30)
    c2 = (255 - c1[0], 255 - c1[1], 255 - c1[2])
    for y in range(size):
        t = y / size
        d.line([(0, y), (size, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
    d.ellipse([size // 4, size // 4, 3 * size // 4, 3 * size // 4], fill=(250, 250, 250), outline=(20, 20, 20), width=5)
    rng = __import__("random").Random(seed)  # speckle so the PNG is realistically large (>5 KB)
    for _ in range(size * 6):
        x, y = rng.randrange(size), rng.randrange(size)
        d.point((x, y), fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class ArtRecorder(Recorder):
    """Like Recorder but every call returns a different valid image; `fail` switches to a 500."""

    def __init__(self):
        super().__init__()
        self.fail = False

    def _handle(self, request):
        self.requests.append(request)
        if self.fail:
            return httpx.Response(500, json={"error": {"message": "backend overloaded"}})
        return httpx.Response(200, json=gemini_ok_response(image=art_png(seed=len(self.requests))))
