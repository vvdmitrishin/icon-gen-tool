import asyncio
import base64

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers.base import GenerationRequest, ProviderDisabled, ProviderError, ReferenceImage
from app.providers.cloudflare_image import CLOUDFLARE_MODEL_ID, CloudflareImageProvider
from app.providers.model_registry import ModelRegistry
from tests.conftest import png_bytes

TOKEN = "cfut_FAKE_TOKEN_1234567890abcdef1234567890"


class CfRecorder:
    def __init__(self, status=200, json_body=None, raw=None, ctype=None):
        self.status, self.raw, self.ctype = status, raw, ctype
        self.json_body = json_body if json_body is not None else {
            "result": {"image": base64.b64encode(png_bytes((64, 64), (10, 200, 10))).decode()}, "success": True}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._h)

    def _h(self, request):
        self.requests.append(request)
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw, headers={"content-type": self.ctype or "image/png"})
        return httpx.Response(self.status, json=self.json_body)


def prov(rec):
    return CloudflareImageProvider("acct123", TOKEN, transport=rec.transport)


def req(refs=()):
    return GenerationRequest(prompt="a toy robot", model_id=CLOUDFLARE_MODEL_ID, references=tuple(refs))


def run(c):
    return asyncio.run(c)


def test_request_is_multipart_with_prompt_size_and_reference():
    rec = CfRecorder()
    ref = ReferenceImage("ref-aaaaaaaaaa", png_bytes((8, 8)), "sha")
    res = run(prov(rec).generate(req([ref])))
    r = rec.requests[0]
    assert str(r.url) == f"https://api.cloudflare.com/client/v4/accounts/acct123/ai/run/{CLOUDFLARE_MODEL_ID}"
    assert r.headers["authorization"] == f"Bearer {TOKEN}"
    assert r.headers["content-type"].startswith("multipart/form-data")
    body = r.content
    for needle in (b'name="prompt"', b"a toy robot", b'name="width"', b"1024", b'name="input_image_0"'):
        assert needle in body
    assert res.png_bytes[:4] == b"\x89PNG" and res.provider_id == "cloudflare" and res.usage == {}


def test_text_only_has_no_image_part_but_is_still_multipart():
    rec = CfRecorder()
    run(prov(rec).generate(req()))
    assert b"input_image_0" not in rec.requests[0].content
    assert rec.requests[0].headers["content-type"].startswith("multipart/form-data")


def test_raw_image_response_accepted():
    rec = CfRecorder(raw=png_bytes((32, 32)), ctype="image/png")
    assert run(prov(rec).generate(req())).width == 32


def test_errors_are_sanitized_and_classified():
    rec = CfRecorder(status=401, json_body={"success": False, "errors": [{"message": f"bad token {TOKEN}"}]})
    with pytest.raises(ProviderError) as e:
        run(prov(rec).generate(req()))
    assert TOKEN not in e.value.message and "***" in e.value.message and not e.value.retryable
    rec = CfRecorder(status=429, json_body={"success": False, "errors": [{"message": "rate"}]})
    with pytest.raises(ProviderError) as e:
        run(prov(rec).generate(req()))
    assert e.value.retryable


def test_missing_image_and_success_false():
    with pytest.raises(ProviderError) as e:
        run(prov(CfRecorder(json_body={"success": True, "result": {}})).generate(req()))
    assert e.value.code == "no_image"
    with pytest.raises(ProviderError):
        run(prov(CfRecorder(json_body={"success": False, "errors": [{"message": "x"}]})).generate(req()))


def test_registry_status_depends_on_both_credentials(tmp_path):
    def cf(**kw):
        reg = ModelRegistry(Settings(data_root=tmp_path, **kw))
        return next(m for m in reg.models() if m.provider_id == "cloudflare"), reg

    m, reg = cf(cloudflare_api_token=TOKEN)  # token only (account id missing)
    assert not m.selectable and "CLOUDFLARE_ACCOUNT_ID" in m.disabled_reason
    with pytest.raises(ProviderDisabled):
        reg.provider("cloudflare")
    m, reg = cf(cloudflare_api_token=TOKEN, cloudflare_account_id="acct")
    assert m.selectable and m.status == "configured_app" and m.role == "fallback"  # never "live" before a real request
    assert reg.provider("cloudflare").provider_id == "cloudflare"


def test_set_job_runs_on_cloudflare_without_confirmation_or_cost(tmp_path):
    rec = CfRecorder()
    s = Settings(data_root=tmp_path / "d", artifact_root=tmp_path / "d" / "a",
                 cloudflare_api_token=TOKEN, cloudflare_account_id="acct")
    with TestClient(create_app(s, cloudflare_transport=rec.transport)) as c:
        r = c.post("/api/generate-card", json=dict(genre="Fantasy", object_name="Wand", category=1,
                   provider="cloudflare", model=CLOUDFLARE_MODEL_ID))
        assert r.status_code == 200, r.text
        assert r.json()["origin"].startswith("Live API · Cloudflare") and r.json()["cost"]["estimated_usd"] == 0
        assert c.get("/api/models").json()["budget"]["spent_estimated_usd"] == 0
    assert len(rec.requests) == 1
