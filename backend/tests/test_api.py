import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import FAKE_KEY, Recorder, gemini_ok_response, png_bytes


def payload(**kw):
    base = dict(collection="Movie Collection", genre="Science Fiction", object_name="Space Helmet",
                category=2, wishes="retro", provider="gemini", model="gemini-nano-banana-2.1",
                reference_ids=[], reference_policy_accepted=False, aspect_ratio="1:1",
                confirmed=True)
    base.update(kw)
    return base


@pytest.fixture
def rec():
    return Recorder()


@pytest.fixture
def client(settings, rec):
    return TestClient(create_app(settings, gemini_transport=rec.transport))


def test_health_and_models(client):
    assert client.get("/api/health").json()["status"] == "ok"
    data = client.get("/api/models").json()
    ids = {(m["provider_id"], m["model_id"]): m for m in data["models"]}
    assert ids[("openai", "gpt-image-2.5-flare")]["selectable"] is False
    assert ids[("gemini", "gemini-nano-banana-2.1")]["selectable"] is True
    assert data["budget"]["max_card_retries"] == 0


def test_end_to_end_text_only_with_provenance(client, rec, settings):
    r = client.post("/api/generate-card", json=payload())
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["origin"] == "Live API · Gemini Nano Banana 2.1" and not meta["is_mock"]
    assert meta["image"]["width"] == 1024 and meta["image"]["native_width"] == 64 and meta["prompt"]["layers"]["C_free_wishes"]
    assert meta["cost"]["is_estimate"] and meta["review"]["art_review"] == "pending"
    img = client.get(meta["image"]["url"])
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    # sidecar JSON persisted next to the PNG
    sidecar = json.loads((settings.artifact_root / f"{meta['artifact_id']}.json").read_text())
    assert sidecar["artifact_id"] == meta["artifact_id"]
    assert FAKE_KEY not in json.dumps(sidecar)
    assert len(rec.requests) == 1  # exactly one provider call, no hidden retries
    assert client.get("/api/artifacts").json()[0]["artifact_id"] == meta["artifact_id"]


def test_reference_generation_end_to_end(client, rec):
    up = client.post("/api/references", files={"file": ("ref.png", png_bytes(), "image/png")},
                     data={"ref_type": "category_1"})
    assert up.status_code == 200
    rid = up.json()["ref_id"]
    assert client.get(f"/api/references/{rid}/thumbnail").status_code == 200

    # reference without accepting the policy is refused (no provider call)
    r = client.post("/api/generate-card", json=payload(reference_ids=[rid]))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "reference_policy_required"
    assert rec.requests == []

    r = client.post("/api/generate-card",
                    json=payload(reference_ids=[rid], reference_policy_accepted=True))
    assert r.status_code == 200
    assert r.json()["references"][0]["ref_id"] == rid
    parts = rec.last_json()["contents"][0]["parts"]
    assert "inlineData" in parts[1] and "Category 1 example" in parts[0]["text"]


def test_confirmation_required_for_paid_call(client, rec):
    r = client.post("/api/generate-card", json=payload(confirmed=False))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "confirmation_required"
    assert rec.requests == []


def test_disabled_providers_rejected_without_network(client, rec):
    for prov, model in (("openai", "gpt-image-2.5-flare"),
                        ("cloudflare", "@cf/black-forest-labs/flux-2-klein-4b"),
                        ("gemini", "gemini-3-pro-image")):
        r = client.post("/api/generate-card", json=payload(provider=prov, model=model))
        assert r.status_code == 400 and r.json()["detail"]["code"] == "model_unavailable"
    assert rec.requests == []


def test_category_5_and_bad_category_rejected(client):
    assert client.post("/api/generate-card", json=payload(category=5)).status_code == 422


def test_there_is_no_total_spending_cap(settings, rec):
    c = TestClient(create_app(settings, gemini_transport=rec.transport))
    for _ in range(40):  # 40 x 0.067 = 2.68: far beyond the old $2 test cap and the old $5 production cap path
        assert c.post("/api/generate-card", json=payload()).status_code == 200
    assert len(rec.requests) == 40
    b = c.get("/api/models").json()["budget"]
    assert b["spent_estimated_usd"] == pytest.approx(40 * 0.067) and "total_budget_usd" not in b and "remaining_estimated_usd" not in b


def test_provider_error_no_retry_by_default_and_message_surfaced(settings):
    rec = Recorder(status=500, body={"error": {"message": "backend overloaded"}})
    c = TestClient(create_app(settings, gemini_transport=rec.transport))
    r = c.post("/api/generate-card", json=payload())
    assert r.status_code == 502 and "overloaded" in r.json()["detail"]["message"]
    assert len(rec.requests) == 1  # MAX_CARD_RETRIES=0 → one attempt only
    assert c.get("/api/models").json()["budget"]["spent_estimated_usd"] == 0  # not billable


def test_retry_only_when_explicitly_enabled(settings):
    from dataclasses import replace
    rec = Recorder(status=503, body={"error": {"message": "unavailable"}})
    c = TestClient(create_app(replace(settings, max_card_retries=1),
                              gemini_transport=rec.transport))
    assert c.post("/api/generate-card", json=payload()).status_code == 502
    assert len(rec.requests) == 2


def test_blocked_200_is_charged_in_ledger(settings):
    rec = Recorder(body={"candidates": [{"content": {"parts": [{"text": "no"}]},
                                         "finishReason": "STOP"}],
                         "usageMetadata": {"promptTokenCount": 9}})
    c = TestClient(create_app(settings, gemini_transport=rec.transport))
    assert c.post("/api/generate-card", json=payload()).status_code == 502
    assert c.get("/api/models").json()["budget"]["spent_estimated_usd"] == pytest.approx(0.067)


def test_prompt_preview_is_free(client, rec):
    r = client.post("/api/prompt/preview", json=payload(confirmed=False))
    assert r.status_code == 200
    d = r.json()
    assert d["estimated_cost_usd"] == pytest.approx(0.067) and d["blocked"] is None
    assert "Space Helmet" in d["effective_prompt"]
    assert rec.requests == []


def test_artifact_id_traversal_rejected(client):
    assert client.get("/api/artifacts/..%2F..%2Fsecret/image").status_code in (404, 400)


def test_mock_provider_works_offline(client, rec):
    r = client.post("/api/generate-card",
                    json=payload(provider="mock", model="mock-placeholder-v1", confirmed=False))
    assert r.status_code == 200 and r.json()["is_mock"] is True
    assert rec.requests == []


def test_availability_endpoint(client, rec):
    rec.body = {"models": [{"name": "models/gemini-3.1-flash-image",
                            "supportedGenerationMethods": ["generateContent"]}]}
    r = client.post("/api/models/check-availability")
    assert r.status_code == 200 and r.json()["available"]["gemini-3.1-flash-image"] is True
