"""Cloudflare FLUX.2 Klein 4B + FLUX.1 Schnell wired into the registry, selector metadata and job pipeline (offline)."""
import base64
import io
import json
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.providers.model_registry import ModelRegistry
from tests.conftest import art_png

KLEIN = "@cf/black-forest-labs/flux-2-klein-4b"
SCHNELL = "@cf/black-forest-labs/flux-1-schnell"
FAKE_TOKEN = "fake" + "-cf-token-1234567890"


def jpeg_b64(seed=1) -> str:
    buf = io.BytesIO()
    Image.open(io.BytesIO(art_png(512, seed))).convert("RGB").save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


class CfServer:
    def __init__(self, status=200, body=None):
        self.requests: list[httpx.Request] = []
        self.status, self.body = status, body
        self.transport = httpx.MockTransport(self._h)

    def _h(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.body is not None:
            return httpx.Response(self.status, json=self.body)
        return httpx.Response(200, json={"success": True, "result": {"image": jpeg_b64(len(self.requests))}})


def configured(tmp_path, **kw):
    return Settings(data_root=tmp_path / "d", artifact_root=tmp_path / "d" / "a", cloudflare_account_id="acct",
                    cloudflare_api_token=FAKE_TOKEN, **kw)


def wait(c, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/jobs/{job_id}").json()
        if not d["active"]:
            return d
        time.sleep(0.03)
    raise AssertionError("job did not finish")


def models_by_id(settings):
    return {m.model_id: m for m in ModelRegistry(settings).models() if m.provider_id == "cloudflare" and m.model_id in (KLEIN, SCHNELL)}


def test_both_models_are_listed_and_disabled_without_credentials(tmp_path):
    cf = models_by_id(Settings(data_root=tmp_path))
    assert set(cf) == {KLEIN, SCHNELL}
    for m in cf.values():
        assert not m.selectable and m.status == "disabled_no_key" and "Settings > API Connections" in m.disabled_reason
        assert "Account ID (CLOUDFLARE_ACCOUNT_ID)" in m.disabled_reason and "API token (CLOUDFLARE_API_TOKEN)" in m.disabled_reason


def test_configured_models_are_selectable_but_never_claimed_live_before_a_real_request(tmp_path):
    cf = models_by_id(configured(tmp_path))
    for m in cf.values():
        assert m.selectable and m.status == "configured_app" and m.role == "fallback"   # not app_live / not 'verified'
        assert m.capabilities["verified"] is False and m.runtime == "cloud_api"
        assert "Workers Free allocation" in m.price_note and "never changes your Cloudflare plan" in m.price_note
        assert "Plan assumed by this app: free" in m.price_note and m.est_cost_per_image_usd == 0.0
        assert "unconditionally free" not in m.price_note.lower()


def test_klein_and_schnell_capabilities_in_the_selector(tmp_path):
    cf = models_by_id(configured(tmp_path))
    k, s = cf[KLEIN].capabilities, cf[SCHNELL].capabilities
    assert k["text_to_image"] is True and k["supports_reference_images"] is True and k["max_reference_images"] == 4
    assert {"1:1", "3:4", "4:3", "9:16", "16:9"} <= set(k["aspect_ratios"])
    assert s["text_to_image"] is True and s["image_editing"] is False
    assert s["supports_reference_images"] is False and s["max_reference_images"] == 0 and s["aspect_ratios"] == ["1:1"]
    assert cf[KLEIN].label == "FLUX.2 Klein 4B" and cf[SCHNELL].label == "FLUX.1 Schnell"
    assert cf[KLEIN].model_id != cf[KLEIN].label


def test_paid_plan_shows_an_estimate_and_free_plan_shows_zero(tmp_path):
    free = models_by_id(configured(tmp_path))
    paid = models_by_id(configured(tmp_path, cloudflare_plan="paid"))
    for mid in (KLEIN, SCHNELL):
        assert free[mid].est_cost_per_image_usd == 0.0 and paid[mid].est_cost_per_image_usd > 0
        assert "Plan assumed by this app: paid" in paid[mid].price_note
    assert paid[KLEIN].est_cost_per_image_usd > paid[SCHNELL].est_cost_per_image_usd


@pytest.mark.parametrize("model", [KLEIN, SCHNELL])
def test_single_card_generation_on_both_models_marks_them_live_only_after_success(tmp_path, model):
    srv = CfServer()
    with TestClient(create_app(configured(tmp_path), cloudflare_transport=srv.transport)) as c:
        before = next(m for m in c.get("/api/models").json()["models"] if m["model_id"] == model)
        assert before["status"] == "configured_app"
        r = c.post("/api/generate-card", json=dict(genre="Fantasy", object_name="Wand", category=1, provider="cloudflare", model=model))
        assert r.status_code == 200, r.text
        meta = r.json()
        assert meta["provider"]["id"] == "cloudflare" and meta["provider"]["model"] == model and meta["cost"]["estimated_usd"] == 0
        assert c.get(meta["image"]["url"]).content[:4] == b"\x89PNG"      # JPEG output normalized to PNG
        after = next(m for m in c.get("/api/models").json()["models"] if m["model_id"] == model)
        assert after["status"] == "app_live"
        assert c.get("/api/models").json()["budget"]["spent_estimated_usd"] == 0
    assert len(srv.requests) == 1 and FAKE_TOKEN not in str(srv.requests[0].url)


def test_schnell_rejects_direct_reference_requests_and_klein_accepts_them(tmp_path):
    srv = CfServer()
    with TestClient(create_app(configured(tmp_path), cloudflare_transport=srv.transport)) as c:
        up = c.post("/api/references", files={"file": ("r.png", art_png(300, 2), "image/png")}, data={"ref_type": "style"}).json()
        base = dict(genre="Fantasy", object_name="Wand", category=1, provider="cloudflare", reference_ids=[up["ref_id"]], reference_policy_accepted=True)
        r = c.post("/api/generate-card", json={**base, "model": SCHNELL})
        assert r.status_code == 400 and "at most 0 reference" in r.json()["detail"]["message"] and srv.requests == []
        assert c.post("/api/generate-card", json={**base, "model": KLEIN}).status_code == 200
        assert b"input_image_0" in srv.requests[0].content


def test_schnell_job_degrades_references_and_records_it_in_provenance(tmp_path):
    srv = CfServer()
    with TestClient(create_app(configured(tmp_path), cloudflare_transport=srv.transport)) as c:
        up = c.post("/api/references", files={"file": ("r.png", art_png(300, 2), "image/png")}, data={"ref_type": "style"}).json()
        r = c.post("/api/jobs", json=dict(genre="Detective", number_of_variants=1, scope="pilot", provider="cloudflare",
                                           model=SCHNELL, reference_ids=[up["ref_id"]], reference_policy_accepted=True))
        assert r.status_code == 200, r.text
        d = wait(c, r.json()["job_id"])
        assert d["status"] == "ready_for_review" and d["counts"]["done"] == 4 and d["spent_estimated_usd"] == 0
        for card in (k for k in d["variants"][0]["cards"] if k["status"] == "done"):
            assert card["reference_selection"] == "unsupported_by_model" and card["qa_status"] in ("passed", "flagged")
            meta = c.get(f"/api/artifacts/{card['artifact_id']}").json()
            assert meta["references"] == [] and meta["provider"]["model"] == SCHNELL
        for req in srv.requests:  # JSON body, no reference field, no multipart
            body = json.loads(req.content)
            assert set(body) <= {"prompt", "steps", "seed"} and req.headers["content-type"].startswith("application/json")
    assert len(srv.requests) == 4


def test_quota_error_stops_the_job_and_never_falls_back_to_gemini(tmp_path):
    quota = CfServer(status=429, body={"success": False, "errors": [{"code": 3036, "message": "you have used up your daily free allocation of 10,000 neurons"}]})
    gemini_hits = []
    gem = httpx.MockTransport(lambda r: (gemini_hits.append(r), httpx.Response(500))[1])
    s = replace(configured(tmp_path), gemini_api_key="fake" + "-gemini-key")
    with TestClient(create_app(s, cloudflare_transport=quota.transport, gemini_transport=gem)) as c:
        r = c.post("/api/jobs", json=dict(genre="Fantasy", number_of_variants=1, scope="pilot", provider="cloudflare", model=KLEIN))
        d = wait(c, r.json()["job_id"])
        assert d["status"] == "failed" and d["counts"]["done"] == 0
        failed = [k for k in d["variants"][0]["cards"] if k["status"] == "failed"]
        assert failed and "quota_exceeded" in failed[0]["error"] and "daily free allocation" in failed[0]["error"]
        events = " ".join(e["message"] for e in c.get(f"/api/jobs/{d['job_id']}/events").json())
        assert "No other provider was tried" in events
    assert len(quota.requests) == 1 and gemini_hits == []        # one Cloudflare call, zero Gemini calls
