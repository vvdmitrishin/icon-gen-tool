"""Model Selector availability follows the SAME backend connection state as Settings > API Connections
(vault > .env precedence, no restart needed, real reason when unavailable). Offline only: free metadata endpoints, no image calls."""
import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.credentials import MemoryCredentialStore
from tests.test_connections import (
    CF_ACCT, KLEIN, SCHNELL, FakeCloudflare, FakeGemini, auth, make,
)

ENV_TOKEN = "fake" + "-env-token-789"


@pytest.fixture
def base(settings):
    return dataclasses.replace(settings, gemini_api_key="fake-gemini-key-123", cloudflare_api_token=ENV_TOKEN,
                               cloudflare_account_id="")


def models(c):
    return {(m["provider_id"], m["model_id"]): m for m in c.get("/api/models").json()["models"]}


def test_vault_account_id_plus_env_token_makes_klein_selectable_and_gemini_stays_default(base):
    cf = FakeCloudflare()
    c, store = make(base, cf=cf)
    m = models(c)
    assert m[("cloudflare", KLEIN)]["selectable"] is False  # account id missing: honest reason
    assert "Account ID" in m[("cloudflare", KLEIN)]["disabled_reason"]
    r = c.put("/api/connections/cloudflare", json={"values": {"account_id": CF_ACCT}, "test": True}, headers=auth(c))
    assert r.status_code == 200 and r.json()["status"] == "connected"
    body = c.get("/api/models").json()  # no restart, no extra call
    m = {(x["provider_id"], x["model_id"]): x for x in body["models"]}
    assert m[("cloudflare", KLEIN)]["selectable"] is True and m[("cloudflare", SCHNELL)]["selectable"] is True
    assert m[("cloudflare", KLEIN)]["disabled_reason"] is None
    assert m[("cloudflare", KLEIN)]["connection_status"] == "connected"
    # the default stays Gemini Nano Banana 2.1: connecting another provider never switches it
    assert body["defaults"]["provider"] == "gemini" and body["defaults"]["model"] == "gemini-nano-banana-2.1"
    assert m[("gemini", "gemini-nano-banana-2.1")]["selectable"] is True


def test_connections_and_models_agree_for_every_state(base):
    c, store = make(base)
    h = auth(c)
    for step in ("none", "account", "removed"):
        if step == "account":
            c.put("/api/connections/cloudflare", json={"values": {"account_id": CF_ACCT}}, headers=h)
        if step == "removed":
            c.delete("/api/connections/cloudflare", headers=h)
        conn = {p["id"]: p for p in c.get("/api/connections").json()["providers"]}
        m = models(c)
        for prov in ("gemini", "cloudflare"):
            sel = m[(prov, "gemini-nano-banana-2.1" if prov == "gemini" else KLEIN)]["selectable"]
            assert sel == conn[prov]["configured"], (step, prov)


def test_removing_credentials_updates_availability_without_restart(base):
    c, store = make(base)
    h = auth(c)
    c.put("/api/connections/cloudflare", json={"values": {"account_id": CF_ACCT}, "test": True}, headers=h)
    assert models(c)[("cloudflare", KLEIN)]["selectable"] is True
    c.delete("/api/connections/cloudflare", headers=h)  # vault value removed; .env token alone is not enough
    m = models(c)[("cloudflare", KLEIN)]
    assert m["selectable"] is False and "Account ID" in m["disabled_reason"]


def test_rejected_credentials_make_models_unavailable_with_the_real_reason(base):
    c, store = make(base, cf=FakeCloudflare(status=403))
    h = auth(c)
    r = c.put("/api/connections/cloudflare", json={"values": {"account_id": CF_ACCT}, "test": True}, headers=h)
    assert r.json()["status"] == "invalid"
    m = models(c)[("cloudflare", KLEIN)]
    assert m["selectable"] is False and m["status"] == "connection_failed"
    assert "Access denied" in m["disabled_reason"] and m["connection_status"] == "invalid"
    assert ENV_TOKEN not in str(m) and CF_ACCT not in str(m)


def test_rejected_gemini_key_blocks_default_model_with_reason(base):
    c, store = make(base, gem=FakeGemini(list_status=403))
    assert models(c)[("gemini", "gemini-nano-banana-2.1")]["selectable"] is True  # untested key stays usable
    r = c.post("/api/connections/gemini/test", headers=auth(c))
    assert r.json()["status"] == "invalid"
    m = models(c)[("gemini", "gemini-nano-banana-2.1")]
    assert m["selectable"] is False and m["status"] == "connection_failed" and "rejected" in m["disabled_reason"]


def test_network_trouble_does_not_disable_a_model(base):
    import httpx
    c, store = make(base, cf=FakeCloudflare(exc=httpx.ConnectError("down")))
    c.put("/api/connections/cloudflare", json={"values": {"account_id": CF_ACCT}, "test": True}, headers=auth(c))
    m = models(c)[("cloudflare", KLEIN)]
    assert m["connection_status"] == "unreachable" and m["selectable"] is True


def test_connection_state_survives_backend_restart(base):
    store = MemoryCredentialStore()
    c, _ = make(base, store=store, cf=FakeCloudflare())
    c.put("/api/connections/cloudflare", json={"values": {"account_id": CF_ACCT}, "test": True}, headers=auth(c))
    c2, _ = make(base, store=store)  # new app instance = restart (same vault, same database)
    m = models(c2)[("cloudflare", KLEIN)]
    assert m["selectable"] is True and m["connection_status"] == "connected"
