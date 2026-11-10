import asyncio
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.cost_guard import BudgetExceeded
from app.services.jobs import JobStore, select_references
from tests.conftest import Recorder, gemini_ok_response, png_bytes

GEMINI = dict(provider="gemini", model="gemini-nano-banana-2.1")
MOCK = dict(provider="mock", model="mock-placeholder-v1")


def body(**kw):
    base = dict(genre="Science Fiction", wishes="retro", number_of_variants=1, scope="pilot", confirmed=True, **GEMINI)
    base.update(kw)
    return base


def wait(c, job_id, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/jobs/{job_id}").json()
        if not d["active"]:
            return d
        time.sleep(0.03)
    raise AssertionError("job did not finish")


@pytest.fixture
def rec():
    return Recorder()


def client(settings, rec):
    return TestClient(create_app(settings, gemini_transport=rec.transport))


# ----------------------------------------------------------------------------- lifecycle
def test_preview_is_free_and_describes_scope(settings, rec):
    with client(settings, rec) as c:
        assert c.post("/api/jobs/preview", json=body()).json()["detail"]["code"] == "ai_plan_required"  # free preview never calls the AI
        r = c.post("/api/jobs/preview", json=body(allow_ai_plan=True))
        assert r.status_code == 200
        d = r.json()
        assert len(d["plan"]["variants"]) == 1 and len(d["plan"]["variants"][0]["cards"]) == 10
        assert d["images_in_scope"] == 4 and d["images_total"] == 10
        assert d["estimated_cost_usd"] == pytest.approx(0.268)
        assert d["estimated_cost_full_usd"] == pytest.approx(0.67)
        assert any("3-5 alternative" in w for w in d["warnings"])
        assert d["distribution"] == {"1": 2, "2": 2, "3": 3, "4": 3, "5": 0}
        assert rec.requests == []


def test_pilot_then_continue_to_full_set(settings, rec):
    with client(settings, rec) as c:
        r = c.post("/api/jobs", json=body(confirmed=False))
        assert r.status_code == 400 and r.json()["detail"]["code"] == "confirmation_required"
        assert rec.requests == []

        r = c.post("/api/jobs", json=body())
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        d = wait(c, job_id)
        assert d["status"] == "ready_for_review"
        assert d["counts"]["done"] == 4 and d["counts"]["planned"] == 6 and d["counts"]["total"] == 10
        assert len(rec.requests) == 4  # pilot = 4 provider calls, one per category
        assert d["spent_estimated_usd"] == pytest.approx(0.268)
        done_cats = sorted(card["category"] for card in d["variants"][0]["cards"] if card["status"] == "done")
        assert done_cats == [1, 2, 3, 4]
        assert d["can_continue"] and d["pending_estimate_usd"] == pytest.approx(0.402)

        art = c.get(f"/api/artifacts/{next(k['artifact_id'] for k in d['variants'][0]['cards'] if k['artifact_id'])}").json()
        assert art["context"]["job_id"] == job_id and art["context"]["variant_id"] == "A"

        # continue needs its own confirmation
        r = c.post(f"/api/jobs/{job_id}/continue", json={"confirmed": False})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "confirmation_required"
        assert c.post(f"/api/jobs/{job_id}/continue", json={"confirmed": True}).status_code == 200
        d = wait(c, job_id)
        assert d["status"] == "ready_for_review" and d["counts"]["done"] == 10 and not d["can_continue"]
        assert len(rec.requests) == 10
        assert d["spent_estimated_usd"] == pytest.approx(0.67)
        assert c.post(f"/api/jobs/{job_id}/continue", json={"confirmed": True}).status_code == 400

        events = c.get(f"/api/jobs/{job_id}/events").json()
        assert any("Plan QA passed" in e["message"] for e in events)
        assert c.get(f"/api/jobs/{job_id}/events", params={"after": events[-1]["id"]}).json() == []
        assert len(c.get(f"/api/jobs/{job_id}/variants").json()) == 1
        assert c.get("/api/jobs").json()[0]["job_id"] == job_id


def test_mock_provider_full_set_is_free(settings, rec):
    with client(settings, rec) as c:
        r = c.post("/api/jobs", json=dict(body(**MOCK), scope="full", confirmed=False, number_of_variants=2))
        assert r.status_code == 200
        d = wait(c, r.json()["job_id"])
        assert d["status"] == "ready_for_review" and d["counts"]["done"] == 20
        assert d["spent_estimated_usd"] == 0 and rec.requests == []
        assert {v["variant_id"] for v in d["variants"]} == {"A", "B"}


def test_job_validation_errors(settings, rec):
    with client(settings, rec) as c:
        assert c.post("/api/jobs/preview", json=body(genre="Western")).json()["detail"]["code"] == "ai_plan_required"
        r = c.post("/api/jobs", json=body(provider="openai", model="gpt-image-2.5-flare"))
        assert r.status_code == 400 and r.json()["detail"]["code"] == "model_unavailable"
        r = c.post("/api/jobs", json=body(category_distribution={"mode": "custom", "counts": {"1": 5, "2": 5, "3": 1, "4": 0}}))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_total"
        assert c.post("/api/jobs", json=body(number_of_variants=6)).status_code == 422
        assert c.post("/api/jobs", json=body(reference_ids=["ref-0000000000"])).status_code == 400
        assert rec.requests == []


def test_custom_distribution_is_applied_to_the_plan(settings, rec):
    with client(settings, rec) as c:
        r = c.post("/api/jobs", json=dict(body(**MOCK), scope="full", confirmed=False,
                   category_distribution={"mode": "custom", "counts": {"1": 3, "2": 3, "3": 2, "4": 2}}))
        d = wait(c, r.json()["job_id"])
        cats = [k["category"] for k in d["variants"][0]["cards"]]
        assert cats == [1, 1, 1, 2, 2, 2, 3, 3, 4, 4]


# ------------------------------------------------------------------------- budget / safety
def test_no_spending_cap_blocks_a_run(settings, rec):
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs", json=body(number_of_variants=3))  # 30 images, ~$2: far above the former $0.75 per-run cap
        assert r.status_code == 200, r.text


def test_budget_guard_mid_job_stops_and_keeps_finished_cards(settings, rec):
    app = create_app(settings, gemini_transport=rec.transport)
    jobs = app.state.jobs
    real = jobs.guard.check
    calls = {"n": 0}

    def flaky(provider_id, n_images=1):
        calls["n"] += 1
        if calls["n"] == 4:  # gate=1, card1=2, card2=3, card3 -> 4 raises
            raise BudgetExceeded("simulated cap", "total_cap")
        return real(provider_id, n_images)

    jobs.guard.check = flaky
    with TestClient(app) as c:
        job_id = c.post("/api/jobs", json=body()).json()["job_id"]
        d = wait(c, job_id)
    assert d["status"] == "partial_success"
    assert d["counts"]["done"] == 2 and d["counts"]["failed"] == 0 and d["counts"]["planned"] == 8
    assert len(rec.requests) == 2
    assert d["can_continue"]


def test_circuit_breaker_after_two_consecutive_failures(settings):
    rec = Recorder(status=500, body={"error": {"message": "overloaded"}})
    with client(settings, rec) as c:
        d = wait(c, c.post("/api/jobs", json=body()).json()["job_id"])
        assert d["status"] == "failed"
        assert len(rec.requests) == 2  # stopped after 2, no retries (MAX_CARD_RETRIES=0)
        assert d["counts"]["failed"] == 2 and d["counts"]["planned"] == 8
        assert d["spent_estimated_usd"] == 0  # failed non-billable calls cost nothing
        ev = " ".join(e["message"] for e in c.get(f"/api/jobs/{d['job_id']}/events").json())
        assert "Circuit breaker" in ev and "overloaded" in ev
        assert d["can_continue"]


def test_cancel_stops_further_cards(settings):
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.25)
        return httpx.Response(200, json=gemini_ok_response())

    app = create_app(settings, gemini_transport=httpx.MockTransport(slow))
    with TestClient(app) as c:
        job_id = c.post("/api/jobs", json=body()).json()["job_id"]
        time.sleep(0.1)
        r = c.post(f"/api/jobs/{job_id}/cancel")
        assert r.status_code == 200 and r.json()["status"] == "cancel_requested"
        d = wait(c, job_id)
        assert d["status"] == "cancelled"
        assert d["counts"]["done"] < 4 and d["counts"]["planned"] >= 7
        assert c.post(f"/api/jobs/{job_id}/cancel").status_code == 409


# ------------------------------------------------------------------------------ persistence
def test_state_survives_restart_and_interrupted_jobs_are_not_auto_resumed(settings, rec):
    with client(settings, rec) as c:
        job_id = c.post("/api/jobs", json=body()).json()["job_id"]
        wait(c, job_id)
    n_calls = len(rec.requests)

    # simulate a crash mid-run: job + cards left in active states
    store = JobStore(settings.database_path)
    store.set_status(job_id, "generating")
    cards = store.cards(job_id)
    planned = [k for k in cards if k["status"] == "planned"]
    store.update_card(planned[0]["card_id"], status="generating")
    store.update_card(planned[1]["card_id"], status="queued")

    with client(settings, rec) as c2:  # "restart"
        d = c2.get(f"/api/jobs/{job_id}").json()
        assert d["status"] == "interrupted" and not d["active"]
        assert d["counts"]["done"] == 4  # finished cards kept
        assert d["counts"]["failed"] == 1 and d["counts"]["queued"] == 0
        failed = next(k for v in d["variants"] for k in v["cards"] if k["status"] == "failed")
        assert "may have been billed" in failed["error"]
        assert len(rec.requests) == n_calls  # nothing auto-resumed, nothing spent
        warn = [e for e in c2.get(f"/api/jobs/{job_id}/events").json() if e["level"] == "warn"]
        assert warn and "Continue" in warn[-1]["message"]

        r = c2.post(f"/api/jobs/{job_id}/continue", json={"confirmed": True, "retry_failed": True})
        assert r.status_code == 200
        d = wait(c2, job_id)
        assert d["counts"]["done"] == 10 and d["counts"]["failed"] == 0


# ------------------------------------------------------------------------------- references
def test_select_references_unit():
    metas = [{"ref_id": "r1", "ref_type": "category_1"}, {"ref_id": "r2", "ref_type": "style"},
             {"ref_id": "r3", "ref_type": "category_3"}]
    assert select_references(metas, 1, 5) == (["r1", "r2"], "category_match")
    assert select_references(metas, 3, 5) == (["r3", "r2"], "category_match")  # the matching category comes first
    assert select_references(metas, 4, 5) == (["r2"], "global_only")  # NOT the category 1 / 3 references
    assert select_references([metas[0]], 4, 5) == ([], "none_applicable")  # nothing applies: text rules only, no images
    assert select_references([], 2, 5) == ([], "none")
    assert select_references(metas, 1, 1) == (["r1"], "category_match")


def test_select_references_scopes_character_composition_negative():
    metas = [{"ref_id": "st", "ref_type": "style"}, {"ref_id": "ch", "ref_type": "character"},
             {"ref_id": "co", "ref_type": "composition"}, {"ref_id": "ng", "ref_type": "negative"},
             {"ref_id": "g5", "ref_type": "category_5"}, {"ref_id": "c2", "ref_type": "category_2"}]
    assert select_references(metas, 2, 9) == (["c2", "st", "co", "ng"], "category_match")  # no character / golden refs
    assert select_references(metas, 5, 9) == (["g5", "ch", "st", "co", "ng"], "category_match")  # characters only on golden cards
    assert select_references(metas, 5, 2) == (["g5", "ch"], "category_match")  # provider cap keeps the most specific first
    assert select_references([metas[1]], 3, 5) == ([], "none_applicable")  # a character-only upload never reaches a normal card
    assert select_references([metas[3]], 3, 5) == (["ng"], "global_only")


def test_reference_guided_job_sends_only_applicable_refs_per_card_and_requires_policy(settings, rec):
    with client(settings, rec) as c:
        up = c.post("/api/references", files={"file": ("r.png", png_bytes(), "image/png")},
                    data={"ref_type": "category_1"}).json()
        rid = up["ref_id"]
        r = c.post("/api/jobs", json=body(reference_ids=[rid]))
        assert r.status_code == 400 and r.json()["detail"]["code"] == "reference_policy_required"
        assert rec.requests == []

        job_id = c.post("/api/jobs", json=body(reference_ids=[rid], reference_policy_accepted=True)).json()["job_id"]
        d = wait(c, job_id)
        assert d["status"] == "ready_for_review"
        import json
        sent = [len(json.loads(req.content)["contents"][0]["parts"]) for req in rec.requests]
        cards = [k for k in d["variants"][0]["cards"] if k["status"] == "done"]
        assert len(sent) == len(cards)
        for k in cards:
            meta = c.get(f"/api/artifacts/{k['artifact_id']}").json()
            if k["category"] == 1:
                assert k["reference_selection"] == "category_match" and [r["ref_id"] for r in meta["references"]] == [rid]
            else:  # the category 1 reference is NEVER sent to other categories any more
                assert k["reference_selection"] == "none_applicable" and meta["references"] == []
        assert sorted(sent) == sorted([2 if k["category"] == 1 else 1 for k in cards])  # 1 part = text only


def test_planner_genres_endpoint(settings, rec):
    with client(settings, rec) as c:
        g = c.get("/api/planner/genres").json()
        assert {x["genre"] for x in g} == {"Science Fiction", "Detective", "Fantasy"}
