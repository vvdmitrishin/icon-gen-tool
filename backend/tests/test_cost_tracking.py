"""Lifetime cost tracking: planning (text) + generation (image) = total, per-batch costs, persistence, no double counting.
Offline: the Gemini text transport is mocked, nothing is sent to Google."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.category_distribution import resolve_distribution
from tests.conftest import plan_cost
from tests.test_free_text_sets import MOCK, TextRecorder, ai_plan_json, wait


def test_planning_batch_and_lifetime_costs_add_up_without_double_counting(settings):
    counts = resolve_distribution(None, number_of_cards=10, gold_cards=0)
    rec = TextRecorder(ai_plan_json(1, counts))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        c.post("/api/plan/test", json=dict(collection="Winter", genre="Forest", number_of_cards=10, number_of_variants=1))  # standalone plan
        r = c.post("/api/jobs", json=dict(collection="Summer", genre="Beach", number_of_cards=10, gold_cards=0, number_of_variants=1,
                                          confirmed=True, **MOCK))
        job_id = r.json()["job_id"]
        wait(c, job_id)
        cost = c.get("/api/costs").json()
        assert cost["cost_is_estimate"] and cost["planning_usd"] == pytest.approx(2 * plan_cost(1, 10))  # both text calls count
        assert cost["generation_usd"] == 0 and cost["total_usd"] == pytest.approx(cost["planning_usd"] + cost["generation_usd"])
        # the batch owns ITS planning call, not the standalone one
        row = next(j for j in c.get("/api/jobs").json() if j["job_id"] == job_id)
        assert row["costs"]["planning_usd"] == pytest.approx(plan_cost(1, 10)) and row["costs"]["planning_known"] is True
        assert row["costs"]["total_usd"] == pytest.approx(row["costs"]["planning_usd"] + row["costs"]["generation_usd"])
        assert row["provider"] == "mock" and row["collection"] == "Summer"
        d = c.get(f"/api/jobs/{job_id}").json()
        assert d["costs"] == row["costs"]
        # viewing / refreshing never adds anything
        for _ in range(3):
            c.get("/api/jobs"); c.get(f"/api/jobs/{job_id}"); c.get("/api/costs"); c.get("/api/models")
        assert c.get("/api/costs").json()["total_usd"] == pytest.approx(cost["total_usd"])
        un = cost["unattributed"]
        assert un["planning_usd"] == pytest.approx(plan_cost(1, 10)) and un["planning_calls"] == 1  # the standalone planning call
        assert c.get("/api/models").json()["budget"]["planning_estimated_usd"] == pytest.approx(cost["planning_usd"])


def test_lifetime_survives_a_restart_and_a_new_collection(settings):
    counts = resolve_distribution(None, number_of_cards=10, gold_cards=0)
    rec = TextRecorder(ai_plan_json(1, counts))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        c.post("/api/plan/test", json=dict(collection="Winter", genre="Forest", number_of_cards=10, number_of_variants=1))
        first = c.get("/api/costs").json()["total_usd"]
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c2:  # restart: same ledger
        assert c2.get("/api/costs").json()["total_usd"] == pytest.approx(first)
        assert first > 0
        c2.post("/api/plan/test", json=dict(collection="Autumn", genre="Park", number_of_cards=10, number_of_variants=1))  # another Collection
        assert c2.get("/api/costs").json()["total_usd"] == pytest.approx(2 * first)
