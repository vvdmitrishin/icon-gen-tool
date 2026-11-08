"""API Connections: credential store, effective settings, endpoints, protection, no-leak guarantees (all offline)."""
import dataclasses
import logging
import sqlite3
import sys
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.credentials import (
    KeyringCredentialStore,
    MemoryCredentialStore,
    NullCredentialStore,
    effective_settings,
)
from app.services.security import (
    RateLimiter,
    is_allowed_host,
    is_allowed_origin,
    token_matches,
)
from tests.conftest import gemini_ok_response

G_KEY = "fake" + "-gemini-key-123"
CF_TOKEN = "fake" + "-cf-token-456"
CF_ACCT = "0123456789abcdef0123456789abcdef"
KLEIN = "@cf/black-forest-labs/flux-2-klein-4b"
SCHNELL = "@cf/black-forest-labs/flux-1-schnell"
LOCAL = "http://127.0.0.1:8000"


class FakeGemini:
    """ListModels + generateContent router with scripted status."""

    def __init__(self, list_status=200):
        self.list_status = list_status
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._h)

    def _h(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.url.path.endswith("/models"):
            if self.list_status != 200:
                return httpx.Response(self.list_status, json={"error": {"message": "API key not valid"}})
            return httpx.Response(200, json={"models": [
                {"name": "models/gemini-nano-banana-2.1", "supportedGenerationMethods": ["generateContent"]}]})
        return httpx.Response(200, json=gemini_ok_response())

    def paths(self):
        return [r.url.path for r in self.requests]


class FakeCloudflare:
    def __init__(self, status=200, names=(KLEIN, SCHNELL), exc=None):
        self.status, self.names, self.exc = status, names, exc
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._h)

    def _h(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.exc:
            raise self.exc
        if self.status != 200:
            return httpx.Response(self.status, json={"success": False, "errors": [{"code": 1, "message": "nope"}]})
        return httpx.Response(200, json={"success": True, "result": [{"name": n} for n in self.names]})


@pytest.fixture
def empty_settings(settings):
    return dataclasses.replace(settings, gemini_api_key="")


def make(settings, store=None, gem=None, cf=None):
    store = store if store is not None else MemoryCredentialStore()
    app = create_app(settings, gemini_transport=(gem or FakeGemini()).transport,
                     cloudflare_transport=(cf or FakeCloudflare()).transport, credentials=store)
    c = TestClient(app, base_url=LOCAL)
    return c, store


def auth(c):
    return {"X-Studio-Token": c.get("/api/session").json()["token"]}


# ------------------------------------------------------------------ stores
def test_null_and_memory_stores():
    n = NullCredentialStore()
    assert not n.available() and n.get("gemini", "api_key") is None
    n.delete("gemini", "api_key")
    with pytest.raises(RuntimeError):
        n.set("gemini", "api_key", "x")
    m = MemoryCredentialStore()
    m.set("gemini", "api_key", G_KEY)
    assert m.available() and m.get("gemini", "api_key") == G_KEY
    m.delete("gemini", "api_key")
    m.delete("gemini", "api_key")  # idempotent
    assert m.get("gemini", "api_key") is None


def test_create_app_defaults_to_null_store(settings):
    c = TestClient(create_app(settings), base_url=LOCAL)
    d = c.get("/api/connections").json()
    assert d["secure_storage"] == {"available": False, "backend": "none"}
    r = c.put("/api/connections/gemini", json={"values": {"api_key": G_KEY}}, headers=auth(c))
    assert r.status_code == 503 and G_KEY not in r.text


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager only")
def test_keyring_store_real_roundtrip_windows():
    store = KeyringCredentialStore(service="AICollectionStudio-test-" + uuid.uuid4().hex)
    if not store.available():
        pytest.skip("no usable keyring backend")
    try:
        assert store.get("gemini", "api_key") is None
        store.set("gemini", "api_key", G_KEY)
        assert store.get("gemini", "api_key") == G_KEY
        store.delete("gemini", "api_key")
        assert store.get("gemini", "api_key") is None
    finally:
        store.delete("gemini", "api_key")


# ------------------------------------------------------- effective settings
def test_effective_precedence_store_over_env_over_none(settings, empty_settings):
    m = MemoryCredentialStore()
    assert effective_settings(empty_settings, m).gemini_api_key == ""
    env = dataclasses.replace(empty_settings, gemini_api_key="env" + "-key")
    assert effective_settings(env, m).gemini_api_key == "env-key"
    m.set("gemini", "api_key", G_KEY)
    assert effective_settings(env, m).gemini_api_key == G_KEY
    m.delete("gemini", "api_key")
    assert effective_settings(env, m).gemini_api_key == "env-key"  # fallback after delete
    m.set("cloudflare", "account_id", CF_ACCT)
    m.set("cloudflare", "api_token", CF_TOKEN)
    e = effective_settings(env, m)
    assert e.cloudflare_configured and e.cloudflare_api_token == CF_TOKEN


def test_settings_repr_has_no_secret(empty_settings):
    s = effective_settings(empty_settings, _filled())
    assert G_KEY not in repr(s) and CF_TOKEN not in repr(s)


def _filled():
    m = MemoryCredentialStore()
    m.set("gemini", "api_key", G_KEY)
    m.set("cloudflare", "account_id", CF_ACCT)
    m.set("cloudflare", "api_token", CF_TOKEN)
    return m


# ------------------------------------------------------- security unit tests
@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1", True), ("127.0.0.1:8000", True), ("localhost:5173", True), ("LOCALHOST", True), ("[::1]:8000", True),
    ("evil.com", False), ("127.0.0.1.evil.com", False), ("localhost.evil.com:8000", False), ("", False), (None, False),
    ("192.168.1.5:8000", False), ("0.0.0.0", False),
])
def test_allowed_host(host, ok):
    assert is_allowed_host(host) is ok


@pytest.mark.parametrize("origin,ok", [
    (None, True), ("http://127.0.0.1:5173", True), ("http://localhost:5174", True), ("http://[::1]:8000", True),
    ("http://127.0.0.1", True), ("https://127.0.0.1:5173", False), ("http://evil.com", False),
    ("http://localhost.evil.com", False), ("null", False), ("http://user@localhost", False),
    ("http://localhost:abc", False), ("", False),
])
def test_allowed_origin(origin, ok):
    assert is_allowed_origin(origin) is ok


def test_token_and_rate_limiter():
    assert token_matches("abc", "abc") and not token_matches("abc", "abd") and not token_matches("abc", None)
    t = [0.0]
    rl = RateLimiter(limit=2, window_s=10, clock=lambda: t[0])
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a") and rl.allow("b")
    t[0] = 11
    assert rl.allow("a")


# ------------------------------------------------------------- GET flows
def test_connections_initial_state_first_run(empty_settings):
    c, _ = make(empty_settings)
    d = c.get("/api/connections").json()
    assert d["secure_storage"] == {"available": True, "backend": "memory"}
    assert d["first_run"] is True
    g, cf = d["providers"]
    assert (g["id"], cf["id"]) == ("gemini", "cloudflare")
    assert g["status"] == "not_configured" and g["source"] == "none" and not g["configured"]
    assert g["fields"] == [{"name": "api_key", "label": "Gemini API key", "secret": True, "stored": False, "from_env": False}]
    assert [f["name"] for f in cf["fields"]] == ["account_id", "api_token"]
    assert g["help"]["get_credentials_url"].startswith("https://") and g["help"]["steps"]


def test_env_configured_reports_env_source_not_first_run(settings):
    c, _ = make(settings)  # settings carries a fake env gemini key
    d = c.get("/api/connections").json()
    g = d["providers"][0]
    assert d["first_run"] is False and g["source"] == "env" and g["status"] == "unverified"
    assert g["fields"][0]["from_env"] is True and g["fields"][0]["stored"] is False
    assert c.get("/api/health").json()["gemini_configured"] is True


# ------------------------------------------------------------ PUT / DELETE
def test_put_gemini_save_and_test_connected(empty_settings):
    gem = FakeGemini()
    c, store = make(empty_settings, gem=gem)
    assert c.get("/api/health").json()["gemini_configured"] is False
    r = c.put("/api/connections/gemini", json={"values": {"api_key": f"  {G_KEY} "}, "test": True}, headers=auth(c))
    assert r.status_code == 200, r.text
    body = r.json()
    assert G_KEY not in r.text
    assert store.get("gemini", "api_key") == G_KEY  # trimmed
    assert body["status"] == "connected" and body["source"] == "credential_manager" and body["last_tested_at"]
    assert body["test"]["ok"] is True and body["test"]["details"]["image_models_listed"] == ["gemini-nano-banana-2.1"]
    assert body["fields"][0]["stored"] is True
    assert all(p.endswith("/models") for p in gem.paths())  # metadata only, never generateContent
    assert c.get("/api/health").json()["gemini_configured"] is True  # effective without restart
    assert c.get("/api/settings").json()["providers"]["gemini"]["key_configured"] is True
    sel = {(m["provider_id"], m["model_id"]): m for m in c.get("/api/models").json()["models"]}
    assert sel[("gemini", "gemini-nano-banana-2.1")]["selectable"] is True


def test_put_without_test_is_unverified_and_status_persisted(empty_settings):
    c, _ = make(empty_settings)
    r = c.put("/api/connections/gemini", json={"values": {"api_key": G_KEY}}, headers=auth(c))
    assert r.json()["status"] == "unverified" and "test" not in r.json()
    t = c.post("/api/connections/gemini/test", headers=auth(c)).json()
    assert t["ok"] and t["status"] == "connected"
    row = sqlite3.connect(empty_settings.database_path).execute("SELECT provider,status,message FROM connection_status").fetchall()
    assert row == [("gemini", "connected", t["message"])]
    assert G_KEY not in repr(row)
    assert c.get("/api/connections").json()["providers"][0]["status"] == "connected"
    # changing the credential resets the verified status
    c.put("/api/connections/gemini", json={"values": {"api_key": G_KEY + "2"}}, headers=auth(c))
    assert c.get("/api/connections").json()["providers"][0]["status"] == "unverified"


def test_delete_falls_back_to_env(settings):
    c, store = make(settings)
    store.set("gemini", "api_key", G_KEY)
    assert c.get("/api/connections").json()["providers"][0]["source"] == "credential_manager"
    r = c.delete("/api/connections/gemini", headers=auth(c))
    assert r.status_code == 200 and r.json()["source"] == "env" and store.get("gemini", "api_key") is None


def test_delete_without_env_becomes_not_configured(empty_settings):
    c, store = make(empty_settings)
    store.set("gemini", "api_key", G_KEY)
    r = c.delete("/api/connections/gemini", headers=auth(c)).json()
    assert r["status"] == "not_configured" and r["source"] == "none"
    assert c.get("/api/connections").json()["first_run"] is True


@pytest.mark.parametrize("values,code", [
    ({}, "missing_field"),
    ({"api_key": "   "}, "empty_value"),
    ({"api_key": "a b"}, "invalid_characters"),
    ({"api_key": "ab\x00cd"}, "invalid_characters"),
    ({"api_key": "x" * 600}, "too_long"),
    ({"api_key": 12345}, "invalid_value"),
    ({"api_key": G_KEY, "other": "z"}, "unknown_field"),
])
def test_put_validation_errors_store_nothing(empty_settings, values, code):
    c, store = make(empty_settings)
    r = c.put("/api/connections/gemini", json={"values": values}, headers=auth(c))
    assert r.status_code == 422 and r.json()["detail"]["code"] == code
    assert store.get("gemini", "api_key") is None
    assert G_KEY not in r.text


def test_cloudflare_requires_both_and_validates_account(empty_settings):
    c, store = make(empty_settings)
    h = auth(c)
    r = c.put("/api/connections/cloudflare", json={"values": {"api_token": CF_TOKEN}}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "missing_field" and store.get("cloudflare", "api_token") is None
    r = c.put("/api/connections/cloudflare", json={"values": {"account_id": "../etc", "api_token": CF_TOKEN}}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_account_id" and CF_TOKEN not in r.text
    r = c.put("/api/connections/cloudflare", json={"values": {"account_id": CF_ACCT, "api_token": CF_TOKEN}, "test": True}, headers=h)
    assert r.status_code == 200 and CF_TOKEN not in r.text and CF_ACCT not in r.text
    assert r.json()["status"] == "connected" and r.json()["configured"] is True
    assert r.json()["test"]["details"]["models"] == {KLEIN: True, SCHNELL: True}
    # a later partial update keeps the other stored field
    r = c.put("/api/connections/cloudflare", json={"values": {"api_token": CF_TOKEN + "x"}}, headers=h)
    assert r.status_code == 200 and store.get("cloudflare", "account_id") == CF_ACCT


def test_unknown_provider_404(empty_settings):
    c, _ = make(empty_settings)
    h = auth(c)
    assert c.put("/api/connections/nope", json={"values": {}}, headers=h).status_code == 404
    assert c.delete("/api/connections/nope", headers=h).status_code == 404
    assert c.post("/api/connections/nope/test", headers=h).status_code == 404


# ------------------------------------------------------------ status mapping
@pytest.mark.parametrize("status,expected", [(400, "invalid"), (403, "invalid"), (429, "unreachable"), (503, "unreachable")])
def test_gemini_status_mapping(empty_settings, status, expected):
    c, store = make(empty_settings, gem=FakeGemini(list_status=status))
    store.set("gemini", "api_key", G_KEY)
    r = c.post("/api/connections/gemini/test", headers=auth(c))
    assert r.status_code == 200 and r.json()["status"] == expected and r.json()["ok"] is False
    assert G_KEY not in r.text
    assert c.get("/api/connections").json()["providers"][0]["status"] == expected


def test_gemini_network_error_unreachable(empty_settings):
    def boom(req):
        raise httpx.ConnectError("down " + G_KEY)
    app = create_app(empty_settings, gemini_transport=httpx.MockTransport(boom), credentials=_filled())
    c = TestClient(app, base_url=LOCAL)
    r = c.post("/api/connections/gemini/test", headers=auth(c))
    assert r.json()["status"] == "unreachable" and G_KEY not in r.text


@pytest.mark.parametrize("cf,status,ok", [
    (FakeCloudflare(200), "connected", True),
    (FakeCloudflare(200, names=(SCHNELL,)), "connected", True),
    (FakeCloudflare(401), "invalid", False),
    (FakeCloudflare(403), "invalid", False),
    (FakeCloudflare(404), "invalid", False),
    (FakeCloudflare(429), "unreachable", False),
    (FakeCloudflare(500), "unreachable", False),
    (FakeCloudflare(418), "unreachable", False),
    (FakeCloudflare(exc=httpx.ConnectTimeout("t")), "unreachable", False),
    (FakeCloudflare(exc=httpx.ConnectError("c")), "unreachable", False),
])
def test_cloudflare_status_mapping(empty_settings, cf, status, ok):
    c, _ = make(empty_settings, store=_filled(), cf=cf)
    r = c.post("/api/connections/cloudflare/test", headers=auth(c))
    assert r.status_code == 200
    j = r.json()
    assert (j["status"], j["ok"]) == (status, ok) and CF_TOKEN not in r.text
    req = cf.requests[0]
    assert req.method == "GET" and req.url.path == f"/client/v4/accounts/{CF_ACCT}/ai/models/search"
    assert req.headers["authorization"] == f"Bearer {CF_TOKEN}"
    assert all("/ai/run/" not in str(r.url) for r in cf.requests)  # never inference


def test_cloudflare_missing_klein_noted(empty_settings):
    c, _ = make(empty_settings, store=_filled(), cf=FakeCloudflare(200, names=(SCHNELL,)))
    j = c.post("/api/connections/cloudflare/test", headers=auth(c)).json()
    assert j["details"]["models"] == {KLEIN: False, SCHNELL: True} and "not listed" in j["message"]


def test_test_when_not_configured(empty_settings):
    c, _ = make(empty_settings)
    j = c.post("/api/connections/cloudflare/test", headers=auth(c)).json()
    assert j["ok"] is False and j["status"] == "not_configured"


def test_test_endpoint_rate_limited(empty_settings):
    c, _ = make(empty_settings, store=_filled())
    h = auth(c)
    codes = [c.post("/api/connections/gemini/test", headers=h).status_code for _ in range(8)]
    assert codes[:6] == [200] * 6 and codes[6:] == [429, 429]
    assert c.post("/api/connections/cloudflare/test", headers=h).status_code == 200  # per provider


# ------------------------------------------------------------- protection
def test_foreign_host_rejected_everywhere(empty_settings):
    c, store = make(empty_settings)
    tok = auth(c)
    for method, path, kw in [
        ("get", "/api/session", {}), ("get", "/api/connections", {}),
        ("put", "/api/connections/gemini", {"json": {"values": {"api_key": G_KEY}}}),
        ("delete", "/api/connections/gemini", {}), ("post", "/api/connections/gemini/test", {}),
    ]:
        r = getattr(c, method)(path, headers={**tok, "Host": "evil.example.com"}, **kw)
        assert r.status_code == 403, (method, path)
    assert store.get("gemini", "api_key") is None


def test_foreign_origin_rejected(empty_settings):
    c, store = make(empty_settings)
    tok = auth(c)
    bad = {**tok, "Origin": "http://evil.example.com"}
    assert c.get("/api/session", headers={"Origin": "https://evil.example.com"}).status_code == 403
    assert c.get("/api/connections", headers={"Origin": "http://evil.example.com"}).status_code == 403
    r = c.put("/api/connections/gemini", json={"values": {"api_key": G_KEY}}, headers=bad)
    assert r.status_code == 403 and store.get("gemini", "api_key") is None
    ok = c.put("/api/connections/gemini", json={"values": {"api_key": G_KEY}}, headers={**tok, "Origin": "http://localhost:5174"})
    assert ok.status_code == 200


def test_missing_or_wrong_token_rejected(empty_settings):
    c, store = make(empty_settings)
    body = {"values": {"api_key": G_KEY}}
    for h in ({}, {"X-Studio-Token": "wrong"}):
        assert c.put("/api/connections/gemini", json=body, headers=h).status_code == 403
        assert c.delete("/api/connections/gemini", headers=h).status_code == 403
        assert c.post("/api/connections/gemini/test", headers=h).status_code == 403
    assert store.get("gemini", "api_key") is None
    store.set("gemini", "api_key", G_KEY)
    assert c.delete("/api/connections/gemini", headers={}).status_code == 403
    assert store.get("gemini", "api_key") == G_KEY  # delete did not happen
    # tokens are per process/app
    c2, _ = make(empty_settings)
    assert auth(c)["X-Studio-Token"] != auth(c2)["X-Studio-Token"]


# ----------------------------------------------------------------- no leaks
def test_validation_error_does_not_echo_input(empty_settings):
    c, store = make(empty_settings)
    r = c.put("/api/connections/gemini", json={"values": G_KEY, "test": "maybe-" + G_KEY}, headers=auth(c))
    assert r.status_code == 422
    assert G_KEY not in r.text
    d = r.json()["detail"]
    assert isinstance(d, list) and all("msg" in e and "loc" in e and "input" not in e and "ctx" not in e for e in d)
    # other endpoints keep the same sanitized shape
    r2 = c.post("/api/wishes/check", json=[G_KEY])
    assert r2.status_code == 422 and G_KEY not in r2.text


def test_no_secret_in_logs_or_responses(empty_settings, caplog):
    caplog.set_level(logging.DEBUG)
    gem = FakeGemini(list_status=403)
    c, store = make(empty_settings, gem=gem)
    h = auth(c)
    texts = []
    for r in (
        c.put("/api/connections/gemini", json={"values": {"api_key": G_KEY}, "test": True}, headers=h),
        c.put("/api/connections/gemini", json={"values": {"api_key": G_KEY + " bad"}}, headers=h),
        c.put("/api/connections/gemini", json={"values": G_KEY}, headers=h),
        c.get("/api/connections"), c.get("/api/settings"), c.get("/api/health"), c.get("/api/models"),
        c.post("/api/models/check-availability"),
        c.put("/api/connections/cloudflare", json={"values": {"account_id": CF_ACCT, "api_token": CF_TOKEN}, "test": True}, headers=h),
        c.get("/api/connections"),
    ):
        texts.append(r.text)
    blob = "\n".join(texts) + caplog.text + repr(c.app.state.__dict__)
    for secret in (G_KEY, CF_TOKEN, CF_ACCT):
        assert secret not in blob, secret[:6]
    db = sqlite3.connect(empty_settings.database_path).iterdump()
    assert not any(G_KEY in line or CF_TOKEN in line for line in db)


# ---------------------------------------------- existing generation still works
def test_generation_works_after_saving_key_through_api(empty_settings):
    gem = FakeGemini()
    c, _ = make(empty_settings, gem=gem)
    payload = dict(collection="Movie Collection", genre="Science Fiction", object_name="Space Helmet", category=2,
                   wishes="retro", provider="gemini", model="gemini-nano-banana-2.1", reference_ids=[],
                   reference_policy_accepted=False, aspect_ratio="1:1", confirmed=True)
    r = c.post("/api/generate-card", json=payload)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "model_unavailable"  # no key yet
    assert c.put("/api/connections/gemini", json={"values": {"api_key": G_KEY}}, headers=auth(c)).status_code == 200
    r = c.post("/api/generate-card", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["origin"] == "Live API · Gemini Nano Banana 2.1" and not r.json()["is_mock"]
    gen = [q for q in gem.requests if q.url.path.endswith(":generateContent")]
    assert len(gen) == 1 and gen[0].headers["x-goog-api-key"] == G_KEY
    # removing the key disables Gemini again at once
    c.delete("/api/connections/gemini", headers=auth(c))
    assert c.post("/api/generate-card", json=payload).status_code == 400
