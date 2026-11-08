"""Stabilization: category compliance matrix, final-bundle eligibility, AI planning for arbitrary themes
(mocked Gemini transport only; no live calls)."""
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.category_distribution import (
    CategoryDistribution,
    DistributionError,
    assign_slots,
    compliance,
    final_eligibility,
    resolve_distribution,
)
from tests.conftest import make_plan
from tests.test_free_text_sets import MOCK, TextRecorder, ai_plan_json, job_body, wait

SUMMER = ["Beach Ball", "Surfboard", "Sun Hat", "Swim Goggles", "Sand Volleyball Net", "Paddle Board",
          "Sunscreen Bottle", "Snorkel Mask", "Frisbee Disc", "Water Polo Cap", "Beach Tennis Racket",
          "Kite Board", "Lifeguard Whistle", "Cooler Box", "Flip Flops", "Sand Castle Bucket"]


# ------------------------------------------------------------------ B2 matrix
@pytest.mark.parametrize("n", range(1, 11))
def test_distribution_matrix_all_n_g(n):
    for g in range(0, n + 1):
        d = resolve_distribution(None, number_of_cards=n, gold_cards=g)
        std = n - g
        assert all(v >= 0 for v in d.values()), (n, g)
        assert d[5] == g and sum(d.values()) == n, (n, g, d)
        assert len(assign_slots(d)) == n
        ok, note = compliance(d)
        assert ok == (std >= 4 and all(d[c] >= 1 for c in (1, 2, 3, 4))), (n, g)
        assert (note is None) == ok
        if std >= 4:  # custom counts are used exactly as given (never altered)
            custom = {1: std - 3, 2: 1, 3: 1, 4: 1}
            got = resolve_distribution(CategoryDistribution(mode="custom", counts=custom, number_of_cards=n, gold_cards=g))
            assert {c: got[c] for c in (1, 2, 3, 4)} == custom and got[5] == g
        elif std > 0:  # allowed, but flagged non-compliant
            custom = {1: 0, 2: std, 3: 0, 4: 0}
            got = resolve_distribution(CategoryDistribution(mode="custom", counts=custom, number_of_cards=n, gold_cards=g))
            assert {c: got[c] for c in (1, 2, 3, 4)} == custom
            assert compliance(got)[0] is False
        with pytest.raises(DistributionError):  # wrong sums are rejected, never silently fixed
            resolve_distribution(CategoryDistribution(mode="custom", counts={1: 20, 2: 1, 3: 1, 4: 1}, number_of_cards=n, gold_cards=g))
        plan = make_plan(1, d)  # planning: exactly N cards, the requested number of them golden
        cards = plan.variants[0].cards
        assert len(cards) == n and sum(1 for c in cards if c.category == 5) == g


def test_compliance_notes_and_eligibility():
    assert compliance({1: 1, 2: 1, 3: 0, 4: 0, 5: 0}) == (
        False, "Custom configuration: fewer than 4 standard cards cannot cover all four categories.")
    assert compliance({1: 2, 2: 1, 3: 0, 4: 1}) == (False, "Custom configuration: Category 3 has no cards.")
    assert compliance({1: 1, 2: 1, 3: 1, 4: 1})[0] is True
    assert final_eligibility(10, 0)[0] and not final_eligibility(10, 2)[0] and not final_eligibility(8, 0)[0]


def test_api_exposes_compliance_and_echoes_counts(settings):
    with TestClient(create_app(settings)) as c:
        r = c.post("/api/category-distribution/validate",
                   json=dict(mode="auto_balanced", number_of_cards=3, gold_cards=0)).json()
        assert r["compliant"] is False and "fewer than 4" in r["compliance_note"]
        assert r["counts"] == {"1": 1, "2": 1, "3": 1, "4": 0, "5": 0}
        r = c.post("/api/category-distribution/validate",
                   json=dict(mode="custom", counts={"1": 7, "2": 1, "3": 1, "4": 1}, number_of_cards=10)).json()
        assert r["compliant"] is True and r["compliance_note"] is None and r["counts"]["1"] == 7
        p = c.post("/api/jobs/preview", json=dict(genre="Detective", number_of_cards=5, gold_cards=2, allow_ai_plan=True, **MOCK)).json()
        assert p["compliant"] is False and p["distribution"]["5"] == 2 and p["final_eligible"] is False
        assert p["final_ineligible_reason"]
        jid = c.post("/api/jobs", json=dict(genre="Detective", number_of_variants=1, number_of_cards=4,
                                            confirmed=True, **MOCK)).json()["job_id"]
        d = wait(c, jid)
        assert d["compliant"] is True and d["final_eligible"] is False
        row = [j for j in c.get("/api/jobs").json() if j["job_id"] == jid][0]
        assert row["final_eligible"] is False and "10-card" in row["final_ineligible_reason"]


def test_final_bundle_ignores_golden_and_short_jobs(settings):
    with TestClient(create_app(replace(settings, total_budget_usd=10.0))) as c:
        jid = c.post("/api/jobs", json=dict(genre="Detective", number_of_variants=1, number_of_cards=10, gold_cards=2,
                                            confirmed=True, **MOCK)).json()["job_id"]
        d = wait(c, jid)
        assert d["final_eligible"] is False
        c.post(f"/api/jobs/{jid}/variants/A/approve")
        ov = c.get("/api/collection/overview").json()
        det = next(g for g in ov["genres"] if g["genre"] == "Detective")
        assert det["job_id"] is None and det["ready"] is False and det["ineligible_jobs"][0]["job_id"] == jid
        assert any("ignored" in b for b in det["blockers"])
        e = c.post("/api/exports", json={"job_id": jid, "variant_id": "A", "include_unapproved": True, "allow_mock": True})
        assert e.status_code == 200 and e.json()["final_eligible"] is False
        r = c.post("/api/collection/export", json={"allow_mock": True})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "collection_not_ready"


# ------------------------------------------------------------------ B3
def ai_body(**kw):
    return job_body(collection="Summer", genre="Sports", number_of_variants=2, number_of_cards=6, gold_cards=1,
                    wishes="bright, sunny colours", **kw)


def summer_plan(counts, variants=2):
    plan = ai_plan_json(variants, counts)
    i = 0
    for v in plan["variants"]:
        for card in v["cards"]:
            card["object_name"] = SUMMER[i]
            i += 1
    return plan


def test_ai_request_body_and_summer_sports_objects(settings):
    counts = resolve_distribution(None, number_of_cards=6, gold_cards=1)
    rec = TextRecorder(summer_plan(counts))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        before = len(c.get("/api/jobs").json())
        p = c.post("/api/jobs/preview", json=ai_body())
        assert p.status_code == 200, p.text
        d = p.json()
        assert d["planning_mode"] == "ai" and d["plan_cost_estimate_usd"] == 0.002
        names = {card["object_name"] for v in d["plan"]["variants"] for card in v["cards"]}
        assert names <= set(SUMMER)
        assert len(c.get("/api/jobs").json()) == before  # preview creates no job
        assert len(rec.requests) == 1 and ":generateContent" in str(rec.requests[0].url)  # one text call only
        body = json.loads(rec.requests[0].content)
        prompt = body["contents"][0]["parts"][0]["text"]
        sys_text = body["systemInstruction"]["parts"][0]["text"]
        assert "'Summer'" in prompt and "'Sports'" in prompt and "bright, sunny colours" in prompt
        assert "Create 2 DISTINCT" in prompt and "exactly 6 cards" in prompt
        for cat, n in counts.items():
            if n:
                assert f"Category {cat}" in prompt and f"exactly {n} card(s)" in prompt
        assert "SIMPLE object floating" in prompt and "KEY CHARACTERS" in prompt
        low = (prompt + sys_text).lower()
        assert "semantic boundary" in low and "lettering" in low and "producer's free wishes" in low
        assert d["estimated_cost_usd"] == 0 and d["plan_cost_estimate_usd"] == 0.002  # planning cost kept separate
        j = c.post("/api/jobs", json=ai_body())
        assert j.status_code == 200 and len(rec.requests) == 1  # cached plan reused
        det = wait(c, j.json()["job_id"])
        assert det["counts"]["done"] == 12
        assert {x["object_name"] for v in det["variants"] for x in v["cards"]} <= set(SUMMER)


@pytest.mark.parametrize("coll,st", [("Summer", "Sports"), ("Movie Collection", "Comedy"), ("Movies", "Romance")])
def test_never_falls_back_to_curated_pool(settings, coll, st):
    rec = TextRecorder({})
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=job_body(collection=coll, genre=st, allow_ai_plan=False))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_required"  # the free preview never calls the AI
        assert rec.requests == []
        # Generate plans with the AI; when the plan is unusable no job and no image is created, and nothing curated replaces it
        r = c.post("/api/jobs", json=job_body(collection=coll, genre=st, allow_ai_plan=False))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_invalid"
        assert c.get("/api/jobs").json() == []


def test_paid_disabled_blocks_ai_plan_and_ledger_is_separate(settings):
    counts = resolve_distribution(None, number_of_cards=6, gold_cards=1)
    rec = TextRecorder(summer_plan(counts))
    with TestClient(create_app(replace(settings, paid_image_calls_enabled=False), gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=ai_body())
        assert r.status_code == 402 and r.json()["detail"]["code"] == "paid_disabled"
        assert rec.requests == []
    rec = TextRecorder(summer_plan(counts))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        assert c.post("/api/jobs/preview", json=ai_body()).status_code == 200
        lines = [json.loads(x) for x in settings.ledger_path.read_text().splitlines() if x.strip()]
        assert [e["outcome"] for e in lines] == ["ok:plan"]
