"""Grand Set (All Golden): server-side rules, AI-planner routing and prompt (mocked Gemini transport only)."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.category_distribution import compliance, final_eligibility, grand_counts, resolve_distribution
from app.services.jobs import JobCreateRequest
from tests.test_free_text_sets import MOCK, TextRecorder, ai_plan_json, job_body, wait


def grand_body(**kw):
    base = dict(collection="Movies", genre="Sci-Fi", number_of_variants=1, number_of_cards=4, gold_cards=0,
                set_type="grand")
    base.update(kw)
    return job_body(**base)


def test_request_forces_gold_and_ignores_distribution():
    r = JobCreateRequest(genre="x", provider="mock", model="m", number_of_cards=7, gold_cards=2, set_type="grand",
                         category_distribution={"mode": "custom", "counts": {1: 5, 2: 1, 3: 1, 4: 0}})
    assert r.gold_cards == 7 and r.category_distribution is None


@pytest.mark.parametrize("n", range(1, 11))
def test_grand_counts(n):
    assert resolve_distribution(None, number_of_cards=n, set_type="grand") == {1: 0, 2: 0, 3: 0, 4: 0, 5: n}
    assert grand_counts(n)[5] == n


def test_grand_compliance_and_eligibility():
    ok, note = compliance(grand_counts(10), "grand")
    assert ok and "Grand Set" in note
    assert compliance(grand_counts(10))[0] is False  # same counts without the flag stay non-compliant
    assert final_eligibility(10, 10, "grand")[0] is False
    assert compliance(resolve_distribution(None, number_of_cards=10, gold_cards=0))[0] is True  # Standard unchanged


def test_standard_default_unchanged():
    r = JobCreateRequest(genre="x", provider="mock", model="m", number_of_cards=10, gold_cards=2)
    assert r.set_type == "standard" and r.gold_cards == 2


def test_grand_preview_uses_ai_planner_even_for_curated_genre(settings):
    counts = grand_counts(4)
    rec = TextRecorder(ai_plan_json(1, counts))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=grand_body())
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["planning_mode"] == "ai" and d["set_type"] == "grand"
        assert d["gold_cards"] == 4 and d["distribution"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 4}
        assert d["compliant"] is True and "Grand Set" in d["compliance_note"]
        assert d["final_eligible"] is False
        assert all(card["category"] == 5 for card in d["plan"]["variants"][0]["cards"])
        prompt = json.loads(rec.requests[0].content)["contents"][0]["parts"][0]["text"]
        assert "GRAND SET" in prompt and "FINALE" in prompt and "EXACTLY 1 or 2 characters" in prompt
        assert "DIFFERENT" in prompt and "consistent" in prompt


def test_grand_requires_explicit_ai_permission(settings):
    rec = TextRecorder(ai_plan_json(1, grand_counts(4)))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=grand_body(allow_ai_plan=False))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_required"
        assert rec.requests == []


def test_grand_job_runs_and_standard_prompt_has_no_grand_text(settings):
    rec = TextRecorder(ai_plan_json(1, grand_counts(3)))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs", json=grand_body(number_of_cards=3))
        assert r.status_code in (200, 201), r.text
        d = wait(c, r.json()["job_id"])
        assert d["set_type"] == "grand" and d["gold_cards"] == 3 and d["final_eligible"] is False
        assert {card["category"] for v in d["variants"] for card in v["cards"]} == {5}
        assert any(j["set_type"] == "grand" for j in c.get("/api/jobs").json())
    rec2 = TextRecorder(ai_plan_json(1, resolve_distribution(None, number_of_cards=6, gold_cards=1)))
    with TestClient(create_app(settings, gemini_transport=rec2.transport)) as c:
        r = c.post("/api/jobs/preview", json=job_body(number_of_variants=1))
        assert r.json()["set_type"] == "standard"
        assert "GRAND SET" not in json.loads(rec2.requests[0].content)["contents"][0]["parts"][0]["text"]


def test_validate_endpoint_grand(settings):
    with TestClient(create_app(settings)) as c:
        d = c.post("/api/category-distribution/validate",
                   json=dict(mode="custom", number_of_cards=5, gold_cards=0, set_type="grand")).json()
        assert d["counts"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 5} and d["compliant"] is True


# ------------------------------------------------------------------ character count (max 2 per golden card)
@pytest.mark.parametrize("bad", [None, 0, 3, 5])
def test_ai_plan_rejects_bad_character_count_before_any_image(settings, bad):
    plan = ai_plan_json(1, grand_counts(3))
    if bad is None:
        del plan["variants"][0]["cards"][1]["characters"]
    else:
        plan["variants"][0]["cards"][1]["characters"] = bad
    rec = TextRecorder(plan)
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        before = len(c.get("/api/jobs").json())
        r = c.post("/api/jobs", json=grand_body(number_of_cards=3))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_invalid"
        assert len(c.get("/api/jobs").json()) == before  # no job, so no image generation


def test_ai_plan_keeps_characters_and_schema_asks_for_them(settings):
    rec = TextRecorder(ai_plan_json(1, grand_counts(4)))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        d = c.post("/api/jobs/preview", json=grand_body()).json()
        assert [x["characters"] for x in d["plan"]["variants"][0]["cards"]] == [2, 1, 2, 1]
        body = json.loads(rec.requests[0].content)
        props = body["generationConfig"]["responseSchema"]["properties"]["variants"]["items"]["properties"]["cards"]["items"]["properties"]
        assert "characters" in props
        assert "characters` to 1 or 2" in body["contents"][0]["parts"][0]["text"]


def test_every_request_model_forces_all_golden_and_no_standard_categories_for_grand():
    from app.services.jobs import PlanOnlyRequest, PlanRegenerateRequest
    from tests.conftest import make_plan

    stale_ui = dict(genre="x", number_of_cards=8, gold_cards=0, set_type="grand",
                    category_distribution=dict(mode="custom", counts={"1": 3, "2": 3, "3": 1, "4": 1}))
    for r in (JobCreateRequest(provider="mock", model="m", **stale_ui), PlanOnlyRequest(**stale_ui),
              PlanRegenerateRequest(plan=make_plan(1, grand_counts(8), "x"), selected=[dict(variant_id="A", position=1)], **stale_ui)):
        assert r.gold_cards == 8 and r.category_distribution is None  # regardless of what the UI sent
        assert resolve_distribution(r.category_distribution, number_of_cards=r.number_of_cards, gold_cards=r.gold_cards, set_type="grand") == {1: 0, 2: 0, 3: 0, 4: 0, 5: 8}
