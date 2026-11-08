"""The AI text planner model (gemini-3.5-flash-lite): default, request configuration, pricing, and one configuration for Plan Only and
Generate. Offline only: the Gemini text transport is mocked, nothing is sent to Google."""
import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.category_distribution import resolve_distribution
from app.services.text_planner import DEFAULT_TEXT_PRICE_PER_M, estimate_plan_cost_usd, plan_cost_params
from tests.conftest import plan_cost
from tests.test_free_text_sets import MOCK, TextRecorder, ai_plan_json, wait

MODEL = "gemini-3.5-flash-lite"


def test_default_text_model_and_env_override():
    assert Settings().gemini_text_model == MODEL
    assert Settings.from_env({}).gemini_text_model == MODEL
    assert Settings.from_env({"GEMINI_TEXT_MODEL": " other-model "}).gemini_text_model == "other-model"  # explicit override still works


def test_pricing_uses_the_models_real_list_price():
    p = plan_cost_params(MODEL)
    assert (p["input_usd_per_m"], p["output_usd_per_m"]) == (0.30, 2.50)  # $0.30 in / $2.50 out (thinking included) per 1M tokens
    assert p["base_usd"] == round((2100 * 0.30 + 300 * 2.50) / 1e6, 6) and p["per_card_usd"] == round(110 * 2.50 / 1e6, 6)
    assert estimate_plan_cost_usd(MODEL, 1, 10) == round(p["base_usd"] + 10 * p["per_card_usd"], 5) == 0.00413
    assert estimate_plan_cost_usd(MODEL, 5, 10) > estimate_plan_cost_usd(MODEL, 1, 10) > estimate_plan_cost_usd(MODEL, 1, 1)  # grows with set size
    assert plan_cost_params("some-unknown-model")["input_usd_per_m"] == DEFAULT_TEXT_PRICE_PER_M[0]  # unknown model: same list price


def test_models_endpoint_exposes_the_same_pricing_to_the_ui(settings):
    with TestClient(create_app(settings)) as c:
        planner = c.get("/api/models").json()["planner"]
        assert planner == plan_cost_params(MODEL)
        assert round(planner["base_usd"] + planner["per_card_usd"] * 1 * 10, 5) == plan_cost(1, 10)


def _config(req):
    body = json.loads(req.content)
    return {"model": str(req.url).split("/models/")[1].split(":")[0], "method": str(req.url).rsplit(":", 1)[1],
            "gen_keys": sorted(body["generationConfig"]), "mime": body["generationConfig"]["responseMimeType"],
            "schema": body["generationConfig"]["responseSchema"], "system": body["systemInstruction"]}


def test_plan_only_and_generate_use_the_same_model_request_and_cost(settings):
    counts = resolve_distribution(None, number_of_cards=10, gold_cards=0)
    rec = TextRecorder(ai_plan_json(1, counts))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        po = c.post("/api/plan/test", json=dict(collection="Winter", genre="Forest", number_of_cards=10, number_of_variants=1)).json()
        r = c.post("/api/jobs", json=dict(collection="Summer", genre="Beach", number_of_cards=10, gold_cards=0, number_of_variants=1,
                                          confirmed=True, **MOCK))
        assert r.status_code == 200, r.text
        wait(c, r.json()["job_id"])
        assert len(rec.requests) == 2
        a, b = _config(rec.requests[0]), _config(rec.requests[1])
        assert a == b  # identical model, REST method, response configuration (JSON mode + schema) and system instruction
        assert a["model"] == MODEL and a["method"] == "generateContent" and a["mime"] == "application/json"
        assert "temperature" not in a["gen_keys"] and "responseSchema" in a["gen_keys"]  # deprecated on Gemini 3.x
        assert {"rationale", "characters"} <= set(a["schema"]["properties"]["variants"]["items"]["properties"]["cards"]["items"]["properties"])
        assert po["text_model"] == MODEL and po["text_cost_estimate_usd"] == plan_cost(1, 10)
        ledger = [json.loads(x) for x in settings.ledger_path.read_text().splitlines() if x.strip()]
        assert [e["model"] for e in ledger] == [MODEL, MODEL] and [e["est_cost_usd"] for e in ledger] == [plan_cost(1, 10)] * 2
        assert all(settings.gemini_api_key not in str(rq.url) for rq in rec.requests)  # the key travels in the header only
