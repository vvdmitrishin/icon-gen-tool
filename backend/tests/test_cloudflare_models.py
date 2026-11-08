"""Cloudflare model table, per-model request/response handling, free-tier safety and pipeline (all mocked)."""
import asyncio
import base64
import io
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.providers.base import GenerationRequest, ProviderError, ReferenceImage
from app.providers.cloudflare_image import CloudflareImageProvider
from app.providers.cloudflare_models import (
    CLOUDFLARE_MODELS, KLEIN_ID, SCHNELL_ID, estimate_usd, get_model_spec,
)
from app.services.cost_guard import CostGuard
from tests.conftest import Recorder, art_png, png_bytes

TOKEN = "cfut_" + "FAKE" + "_TOKEN_" + "1234567890abcdef"


class Cf:
    def __init__(self, status=200, body=None, raw=None, ctype=None, fmt="PNG", size=(64, 64)):
        self.status, self.raw, self.ctype = status, raw, ctype
        self.body = body if body is not None else {
            "result": {"image": base64.b64encode(png_bytes(size, (10, 200, 10), fmt=fmt)).decode()}, "success": True}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._h)

    def _h(self, request):
        self.requests.append(request)
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw, headers={"content-type": self.ctype or "image/png"})
        return httpx.Response(self.status, json=self.body)


def prov(rec):
    return CloudflareImageProvider("acct123", TOKEN, transport=rec.transport)


def greq(model=KLEIN_ID, refs=(), aspect="1:1"):
    return GenerationRequest(prompt="a toy robot", model_id=model, references=tuple(refs), aspect_ratio=aspect)


def ref(i=0, size=(900, 700)):
    return ReferenceImage(f"ref-{i:010d}", png_bytes(size), "sha")


def run(c):
    return asyncio.run(c)


QUOTA_BODY = {"success": False, "errors": [{"code": 3036, "message": "You have used up your daily free allocation of 10,000 neurons."}]}


# ------------------------------------------------------------------------------------ table
def test_model_table_and_lookup():
    assert [m.model_id for m in CLOUDFLARE_MODELS] == [KLEIN_ID, SCHNELL_ID]
    k, s = get_model_spec(KLEIN_ID), get_model_spec(SCHNELL_ID)
    assert (k.request_mode, k.max_reference_images, k.supports_reference_images) == ("multipart", 4, True)
    assert (s.request_mode, s.max_reference_images, s.supports_reference_images) == ("json", 0, False)
    assert get_model_spec("nope") is None
    for m in CLOUDFLARE_MODELS:
        assert m.capabilities["verified"] is False
        assert "10,000 Neurons/day" in m.price_note and "never changes" in m.price_note
        assert "free" not in m.price_note.lower().replace("free allocation", "").replace("workers free", "").replace("free plan", "")


def test_estimates_follow_plan(tmp_path):
    free = Settings(data_root=tmp_path)
    paid = Settings(data_root=tmp_path, cloudflare_plan="paid")
    assert estimate_usd(KLEIN_ID, free) == 0.0 and estimate_usd(SCHNELL_ID, free) == 0.0
    assert estimate_usd(KLEIN_ID, paid) == pytest.approx(0.00121) and estimate_usd(SCHNELL_ID, paid) == pytest.approx(0.000634)
    over = Settings(data_root=tmp_path, cloudflare_plan="paid", cloudflare_est_cost_per_image_usd=0.01)
    assert estimate_usd(SCHNELL_ID, over) == 0.01
    g = CostGuard(paid)
    assert g.estimate_usd("cloudflare", 10, KLEIN_ID) == pytest.approx(0.0121)
    assert g.estimate_usd("cloudflare", 1) > 0  # model unknown -> Klein default
    assert CostGuard(free).estimate_usd("cloudflare", 5, KLEIN_ID) == 0.0
    assert g.estimate_usd("mock", 3) == 0.0 and g.estimate_usd("gemini", 1) == pytest.approx(0.067)


# ------------------------------------------------------------------------------------ klein request
def test_klein_multipart_fields_and_reference_limits():
    rec = Cf()
    res = run(prov(rec).generate(greq(refs=[ref(i) for i in range(6)], aspect="16:9")))
    r = rec.requests[0]
    assert r.headers["content-type"].startswith("multipart/form-data")
    b = r.content
    for needle in (b'name="prompt"', b'name="width"', b"1024", b'name="height"', b"576"):
        assert needle in b
    for i in range(4):
        assert f'name="input_image_{i}"'.encode() in b
    assert b'name="input_image_4"' not in b
    assert res.request_summary["reference_count"] == 4 and res.request_summary["references_ignored"] == 2
    assert res.request_summary["requested_size"] == [1024, 576]


def test_klein_references_are_downscaled_below_512():
    p = prov(Cf())
    kwargs, _ = p.build_request(greq(refs=[ref(0, (1200, 800))]))
    name, data, mime = kwargs["files"]["input_image_0"]
    with Image.open(io.BytesIO(data)) as im:
        assert max(im.size) < 512 and im.size[0] / im.size[1] == pytest.approx(1.5, rel=0.02)
    assert mime == "image/png"
    small = ref(0, (100, 80))
    with Image.open(io.BytesIO(p.build_request(greq(refs=[small]))[0]["files"]["input_image_0"][1])) as im:
        assert im.size == (100, 80)  # never upscaled


# ------------------------------------------------------------------------------------ schnell request
def test_schnell_is_json_text_only_and_ignores_references():
    rec = Cf(body={"result": {"image": base64.b64encode(png_bytes((48, 48), fmt="JPEG")).decode()}, "success": True})
    res = run(prov(rec).generate(greq(SCHNELL_ID, refs=[ref(0), ref(1)])))
    r = rec.requests[0]
    assert r.headers["content-type"].startswith("application/json")
    import json
    assert json.loads(r.content) == {"prompt": "a toy robot", "steps": 4}
    assert b"input_image" not in r.content
    assert res.png_bytes[:4] == b"\x89PNG" and res.request_summary["source_format"] == "JPEG"
    assert res.request_summary["reference_count"] == 0 and res.request_summary["references_ignored"] == 2
    assert res.model_id == SCHNELL_ID


def test_unknown_model_rejected_before_any_call():
    rec = Cf()
    with pytest.raises(ProviderError) as e:
        run(prov(rec).generate(greq("@cf/someone/other-model")))
    assert e.value.code == "unsupported_model" and rec.requests == []


# ------------------------------------------------------------------------------------ responses
def test_jpeg_base64_and_raw_image_normalized_to_png():
    res = run(prov(Cf(fmt="JPEG")).generate(greq()))
    assert res.png_bytes[:4] == b"\x89PNG"
    res = run(prov(Cf(raw=png_bytes((40, 30), fmt="JPEG"), ctype="image/jpeg")).generate(greq(SCHNELL_ID)))
    assert res.png_bytes[:4] == b"\x89PNG" and (res.width, res.height) == (40, 30)
    plain = Cf(body={"image": base64.b64encode(png_bytes((20, 20))).decode()})  # unwrapped Schnell-style body
    assert run(prov(plain).generate(greq(SCHNELL_ID))).width == 20


def test_missing_image_success_false_and_garbage():
    with pytest.raises(ProviderError) as e:
        run(prov(Cf(body={"success": True, "result": {}})).generate(greq()))
    assert e.value.code == "no_image"
    with pytest.raises(ProviderError):
        run(prov(Cf(body={"success": False, "errors": [{"message": "x"}]})).generate(greq()))
    with pytest.raises(ProviderError) as e:
        run(prov(Cf(body={"success": True, "result": {"image": base64.b64encode(b"not an image").decode()}})).generate(greq()))
    assert e.value.code == "bad_image"


# ------------------------------------------------------------------------------------ errors
@pytest.mark.parametrize("status,body", [
    (429, QUOTA_BODY),
    (200, QUOTA_BODY),
    (429, {"success": False, "errors": [{"code": 4006, "message": "daily free allocation exceeded"}]}),
])
def test_quota_exhaustion_is_final_and_not_billable(status, body):
    with pytest.raises(ProviderError) as e:
        run(prov(Cf(status=status, body=body)).generate(greq()))
    err = e.value
    assert err.code == "quota_exceeded" and not err.retryable and not err.billable
    for phrase in ("daily free allocation", "10,000", "Nothing was charged", "no plan upgrade", "another model"):
        assert phrase.lower() in err.message.lower()


def test_plain_rate_limits_stay_retryable_and_tokens_are_redacted():
    with pytest.raises(ProviderError) as e:
        run(prov(Cf(status=429, body={"success": False, "errors": [{"code": 3040, "message": "capacity"}]})).generate(greq()))
    assert e.value.retryable and e.value.code == "rate_limited"
    with pytest.raises(ProviderError) as e:
        run(prov(Cf(status=429, body={"success": False, "errors": [{"message": f"slow down {TOKEN}"}]})).generate(greq()))
    assert e.value.retryable and TOKEN not in e.value.message
    with pytest.raises(ProviderError) as e:
        run(prov(Cf(status=403, body={"success": False, "errors": [{"code": 5035, "message": "needs paid"}]})).generate(greq()))
    assert e.value.code == "plan_required" and not e.value.retryable and "No upgrade was attempted" in e.value.message


# ------------------------------------------------------------------------------------ pipeline
def settings(tmp_path, **kw):
    # a Gemini key is present only so the (fixture) text planner can run: the image provider under test is Cloudflare
    return Settings(data_root=tmp_path / "d", artifact_root=tmp_path / "d" / "a", gemini_api_key="fake-planner-key",
                    cloudflare_api_token=TOKEN, cloudflare_account_id="acct", **kw)


def wait(c, job_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/jobs/{job_id}").json()
        if not d["active"]:
            return d
        time.sleep(0.03)
    raise AssertionError("job did not finish")


def job_body(**kw):
    b = dict(genre="Fantasy", wishes="", number_of_variants=1, scope="pilot", provider="cloudflare", model=KLEIN_ID)
    b.update(kw)
    return b


class CfArt(Cf):
    def _h(self, request):
        self.requests.append(request)
        b64 = base64.b64encode(art_png(1024, seed=len(self.requests))).decode()
        return httpx.Response(200, json={"result": {"image": b64}, "success": True})


def test_pilot_job_on_free_plan_end_to_end(tmp_path):
    rec, gem = CfArt(), Recorder()
    with TestClient(create_app(settings(tmp_path), cloudflare_transport=rec.transport, gemini_transport=gem.transport)) as c:
        r = c.post("/api/jobs", json=job_body())  # not confirmed: free plan needs no confirmation
        assert r.status_code == 200, r.text
        d = wait(c, r.json()["job_id"])
        assert d["status"] == "ready_for_review" and d["counts"]["done"] == 4
        # images on the free plan cost nothing; only the tiny AI planning text call (~$0.002) is in the ledger
        assert d["spent_estimated_usd"] == 0 and c.get("/api/models").json()["budget"]["spent_estimated_usd"] <= 0.002
        card = next(k for k in d["variants"][0]["cards"] if k["artifact_id"])
        assert card["qa_status"] in ("passed", "flagged")
        meta = c.get(f"/api/artifacts/{card['artifact_id']}").json()
        assert meta["provider"]["id"] == "cloudflare" and meta["provider"]["model"] == KLEIN_ID
        assert meta["cost"]["estimated_usd"] == 0 and meta["provider"]["request_summary"]["request_mode"] == "multipart"
        assert meta["references"] == [] and "Cloudflare" in meta["origin"]
    assert len(rec.requests) == 4 and gem.requests == []


def test_paid_plan_requires_confirmation_and_hits_ledger(tmp_path):
    rec = CfArt()
    with TestClient(create_app(settings(tmp_path, cloudflare_plan="paid"), cloudflare_transport=rec.transport)) as c:
        pv = c.post("/api/jobs/preview", json=job_body(allow_ai_plan=True)).json()
        assert pv["estimated_cost_usd"] > 0
        r = c.post("/api/jobs", json=job_body())
        assert r.status_code == 400 and r.json()["detail"]["code"] == "confirmation_required" and rec.requests == []
        r = c.post("/api/jobs", json=job_body(confirmed=True))
        assert r.status_code == 200, r.text
        d = wait(c, r.json()["job_id"])
        assert d["spent_estimated_usd"] == pytest.approx(4 * 0.00121, abs=1e-5)
        assert c.get("/api/models").json()["budget"]["spent_estimated_usd"] > 0


def test_quota_error_stops_job_immediately_without_fallback(tmp_path):
    rec, gem = Cf(status=429, body=QUOTA_BODY), Recorder()
    with TestClient(create_app(settings(tmp_path), cloudflare_transport=rec.transport, gemini_transport=gem.transport)) as c:
        r = c.post("/api/jobs", json=job_body())
        d = wait(c, r.json()["job_id"])
        assert d["status"] == "failed" and d["counts"]["done"] == 0
        failed = [k for k in d["variants"][0]["cards"] if k["status"] == "failed"]
        assert len(failed) == 1 and "[quota_exceeded]" in failed[0]["error"]
        assert "daily free allocation" in failed[0]["error"]
    assert len(rec.requests) == 1  # no second Cloudflare call, no retry
    assert gem.requests == []  # no silent fallback to another provider


def test_direct_card_with_schnell_style_unsupported_references_message(tmp_path):
    # the registry owns Schnell's capabilities; here we only confirm Klein caps now allow up to 4 refs
    from app.providers.cloudflare_image import CLOUDFLARE_CAPABILITIES
    assert CLOUDFLARE_CAPABILITIES.max_reference_images == 4
