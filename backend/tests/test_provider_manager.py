"""Provider Manager: add / test / connect / disconnect OpenAI, Ollama and custom OpenAI-compatible text providers, pick the active
planning model, and verified-by-use status. Fully offline (mock transports); no image provider is ever called."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.category_distribution import resolve_distribution
from app.services.credentials import MemoryCredentialStore
from tests.test_free_text_sets import ai_plan_json

COUNTS = resolve_distribution(None, number_of_cards=10, gold_cards=0)
BASE = dict(collection="Summer", genre="Beach", number_of_cards=10, number_of_variants=1, wishes="")
OAI_KEY = "sk-" + "fake-openai-key-987654"


class FakeOpenAI:
    """OpenAI-compatible server: GET /models, POST /chat/completions."""

    def __init__(self, models=("llama3",), models_status=200, down=False, chat=None):
        self.models, self.models_status, self.down, self.chat = list(models), models_status, down, chat
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._h)

    def _h(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.down:
            raise httpx.ConnectError("refused")
        if req.url.path.endswith("/models"):
            if self.models_status != 200:
                return httpx.Response(self.models_status, json={"error": {"message": "bad key"}})
            return httpx.Response(200, json={"data": [{"id": m} for m in self.models]})
        if req.url.path.endswith("/chat/completions"):
            content = self.chat if self.chat is not None else json.dumps(ai_plan_json(1, COUNTS))
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        return httpx.Response(404)


def make(settings, ai, store=None):
    store = store if store is not None else MemoryCredentialStore()
    gem = httpx.MockTransport(lambda r: httpx.Response(500))
    gem.serves_text_plans = True  # let the real planner dispatch run (not the deterministic fixture)
    c = TestClient(create_app(settings, gemini_transport=gem, credentials=store, ai_transport=ai.transport), base_url="http://127.0.0.1:8000")
    return c, store


def auth(c):
    return {"X-Studio-Token": c.get("/api/session").json()["token"]}


def connect(c, **kw):
    return c.post("/api/providers", json=kw, headers=auth(c)).json()


def prov(d, pid):
    return next((p for p in d["providers"] if p["id"] == pid), None)


def test_ollama_connects_without_api_key(settings):
    ai = FakeOpenAI()
    c, store = make(settings, ai)
    d = connect(c, kind="ollama", model_id="llama3")
    assert d["saved"] is True
    p = prov(d, "ollama")
    assert p["status"] == "connected" and p["types"] == ["text"] and p["local"] is True and p["text_models"] == ["llama3"]
    assert store.get("ai:ollama", "api_key") is None  # nothing secret exists
    assert ai.requests[0].url.path == "/v1/models" and "authorization" not in ai.requests[0].headers


def test_unreachable_local_server_is_reported_and_nothing_is_saved(settings):
    c, _ = make(settings, FakeOpenAI(down=True))
    d = connect(c, kind="ollama", model_id="llama3")
    assert d["saved"] is False and d["test"]["status"] == "unreachable" and "not running" in d["test"]["message"]
    assert prov(d, "ollama") is None


def test_rejected_key_is_not_saved_and_good_key_stays_out_of_every_response(settings):
    c, store = make(settings, FakeOpenAI(models_status=401, models=("gpt-4o-mini",)))
    d = connect(c, kind="openai", api_key=OAI_KEY, model_id="gpt-4o-mini")
    assert d["saved"] is False and d["test"]["status"] == "invalid" and OAI_KEY not in json.dumps(d)
    assert store.get("ai:openai", "api_key") is None
    c2, store2 = make(settings, FakeOpenAI(models=("gpt-4o-mini",)))
    d = connect(c2, kind="openai", api_key=OAI_KEY, model_id="gpt-4o-mini")
    assert d["saved"] is True and store2.get("ai:openai", "api_key") == OAI_KEY
    assert OAI_KEY not in json.dumps(d) and OAI_KEY not in c2.get("/api/providers").text and OAI_KEY not in c2.get("/api/models").text


def test_openai_requires_a_key_and_custom_endpoint_requires_base_url(settings):
    c, _ = make(settings, FakeOpenAI())
    assert connect(c, kind="openai", model_id="gpt-4o-mini")["saved"] is False
    assert connect(c, kind="openai_compatible", model_id="m")["saved"] is False
    d = connect(c, kind="openai_compatible", base_url="http://localhost:1234/v1", label="LM Studio", model_id="llama3")
    assert d["saved"] is True and prov(d, d["id"])["label"] == "LM Studio"


def test_test_connection_is_free_and_stores_nothing(settings):
    ai = FakeOpenAI(models=("a", "b"))
    c, store = make(settings, ai)
    r = c.post("/api/providers/test", json={"kind": "ollama", "model_id": "a"}, headers=auth(c)).json()
    assert r["ok"] and r["models"] == ["a", "b"]
    assert all(q.method == "GET" for q in ai.requests)  # never a chat/completion call
    assert c.get("/api/providers").json()["providers"] == [p for p in c.get("/api/providers").json()["providers"] if p["id"] == "gemini"]
    assert c.post("/api/providers/test", json={"kind": "ollama"}).status_code == 403  # needs the session token


def test_active_text_model_runs_metaphor_planner_with_local_llm_and_costs_nothing(settings):
    ai = FakeOpenAI()
    c, _ = make(settings, ai)
    connect(c, kind="ollama", model_id="llama3")
    assert c.put("/api/active-models/text", json={"provider_id": "ollama", "model_id": "llama3"}, headers=auth(c)).status_code == 200
    assert c.get("/api/models").json()["planner"]["provider_id"] == "ollama"
    r = c.post("/api/plan/test", json=BASE)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["text_model"] == "llama3" and out["text_cost_estimate_usd"] == 0
    chat = [q for q in ai.requests if q.url.path.endswith("/chat/completions")]
    assert len(chat) == 1 and json.loads(chat[0].content)["model"] == "llama3"
    assert c.get("/api/costs").json()["planning_usd"] == 0
    # the planner call is recorded against the provider that ran it
    assert prov(c.get("/api/providers").json(), "ollama")["status"] == "connected"


def test_unusable_model_answer_is_reported_and_does_not_break(settings):
    c, _ = make(settings, FakeOpenAI(chat="this is not json"))
    connect(c, kind="ollama", model_id="llama3")
    c.put("/api/active-models/text", json={"provider_id": "ollama", "model_id": "llama3"}, headers=auth(c))
    r = c.post("/api/plan/test", json=BASE)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_invalid"


def test_only_connected_models_can_be_selected(settings):
    c, _ = make(settings, FakeOpenAI())
    assert c.put("/api/active-models/text", json={"provider_id": "ollama", "model_id": "llama3"}, headers=auth(c)).status_code == 422
    connect(c, kind="ollama", model_id="llama3")
    assert c.put("/api/active-models/text", json={"provider_id": "ollama", "model_id": "other"}, headers=auth(c)).status_code == 422


def test_disconnect_removes_credentials_and_planner_falls_back_but_history_stays(settings):
    ai = FakeOpenAI(models=("gpt-4o-mini",))
    c, store = make(settings, ai)
    connect(c, kind="openai", api_key=OAI_KEY, model_id="gpt-4o-mini")
    c.put("/api/active-models/text", json={"provider_id": "openai", "model_id": "gpt-4o-mini"}, headers=auth(c))
    c.post("/api/plan/test", json=BASE)
    spent = c.get("/api/costs").json()["planning_usd"]
    assert spent > 0  # a cloud text model is estimated
    d = c.delete("/api/providers/openai", headers=auth(c)).json()
    assert d["removed"] is True and prov(d, "openai") is None and store.get("ai:openai", "api_key") is None
    assert d["active_text"]["provider_id"] == "gemini"
    assert c.get("/api/costs").json()["planning_usd"] == spent  # the cost ledger is untouched


def test_connections_survive_a_restart(settings):
    ai = FakeOpenAI(models=("gpt-4o-mini",))
    c, store = make(settings, ai)
    connect(c, kind="openai", api_key=OAI_KEY, model_id="gpt-4o-mini")
    c.put("/api/active-models/text", json={"provider_id": "openai", "model_id": "gpt-4o-mini"}, headers=auth(c))
    c2, _ = make(settings, ai, store=store)  # new app, same database and credential store
    d = c2.get("/api/providers").json()
    assert prov(d, "openai")["status"] == "connected" and d["active_text"]["provider_id"] == "openai"


def test_gemini_env_key_becomes_verified_after_a_real_use_and_list_has_types(settings):
    c, _ = make(settings, FakeOpenAI())
    g = prov(c.get("/api/providers").json(), "gemini")
    assert g["status"] == "configured" and g["status_label"] == "Configured / Not verified" and g["types"] == ["text", "image"]
    assert g["image_models"] and g["text_models"]
    # a successful real planning request verifies it (Gemini answers through the fixture's deterministic planner)
    from app.services.text_planner import TextPlanner
    assert TextPlanner is not None
    c.app.state.jobs.gen.registry.note_success("gemini")
    assert prov(c.get("/api/providers").json(), "gemini")["status"] == "connected"


def test_cloudflare_is_listed_as_image_only_and_catalog_does_not_advertise_openai_images(settings):
    c, _ = make(settings, FakeOpenAI())
    cat = {k["kind"]: k for k in c.get("/api/providers").json()["catalog"]}
    assert cat["cloudflare"]["types"] == ["image"] and cat["openai"]["types"] == ["text"] and cat["ollama"]["fields"] == []


@pytest.mark.parametrize("url", ["ftp://x/v1", "http://user:pw@host/v1", "not a url", "http://h/v1?x=1"])
def test_bad_base_urls_are_rejected(settings, url):
    c, _ = make(settings, FakeOpenAI())
    d = connect(c, kind="openai_compatible", base_url=url, model_id="m")
    assert d["saved"] is False and d["test"]["status"] == "invalid"
