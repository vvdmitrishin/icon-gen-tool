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
        job_budget_usd=0.50,
        total_budget_usd=1.00,
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
