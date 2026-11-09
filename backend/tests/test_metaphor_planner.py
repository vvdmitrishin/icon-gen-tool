"""Metaphor Planner: selective concept regeneration and generating from the curated plan.
Offline: the Gemini TEXT transport is mocked; there is no image provider call anywhere in these tests."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.category_distribution import resolve_distribution
from tests.test_free_text_sets import MOCK, ai_plan_json, wait

COUNTS = resolve_distribution(None, number_of_cards=10, gold_cards=0)
BASE = dict(collection="Summer", genre="Beach", number_of_cards=10, number_of_variants=2, wishes="no buildings")


class Seq:
    """Text transport answering successive calls from a list of JSON bodies (the last one repeats)."""

    def __init__(self, *bodies):
        self.bodies, self.requests = list(bodies), []
        self.transport = httpx.MockTransport(self._handle)
        self.transport.serves_text_plans = True

    def _handle(self, request):
        self.requests.append(request)
        body = self.bodies[min(len(self.requests), len(self.bodies)) - 1]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]})


def cards_of(plan):
    return {(v["variant_id"], c["position"]): c for v in plan["variants"] for c in v["cards"]}


def repl(*items):
    return {"replacements": [dict(variant_id=v, position=p, object_name=n, object_description=f"a bright {n}", rationale="fits the Set")
                             for v, p, n in items]}


def test_regenerate_selected_changes_only_the_selected_concepts(settings):
    seq = Seq(ai_plan_json(2, COUNTS), repl(("A", 3, "Sandcastle Flag"), ("B", 7, "Surfboard Rack")))
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        plan = c.post("/api/plan/test", json=BASE).json()["plan"]
        before_cost = c.get("/api/costs").json()
        r = c.post("/api/plan/regenerate", json=dict(BASE, plan=plan, selected=[dict(variant_id="A", position=3), dict(variant_id="B", position=7)]))
        assert r.status_code == 200, r.text
        out = r.json()
        old, new = cards_of(plan), cards_of(out["plan"])
        changed = {k for k in old if old[k] != new[k]}
        assert changed == {("A", 3), ("B", 7)}  # unselected concepts are identical
        for k in changed:  # number, order and category preserved; only the concept changed
            assert new[k]["position"] == old[k]["position"] and new[k]["category"] == old[k]["category"]
            assert new[k]["object_name"] != old[k]["object_name"]
        assert new[("A", 3)]["object_name"] == "Sandcastle Flag"
        assert [c["position"] for c in out["plan"]["variants"][0]["cards"]] == list(range(1, 11))
        assert out["plan"]["distribution"] == plan["distribution"] and out["image_provider_called"] is False
        assert out["regenerated"] == [dict(variant_id="A", position=3), dict(variant_id="B", position=7)]
        # context: Collection, Set, wishes, the KEEP cards and the selected categories all reach the model; text model only
        sent = json.loads(seq.requests[1].content)["contents"][0]["parts"][0]["text"]
        assert "Summer" in sent and "Beach" in sent and "no buildings" in sent
        assert sent.count("[KEEP]") == 18 and sent.count("[REPLACE]") == 2 and "Item 0-1" in sent and "Item 1-10" in sent
        assert "Category 2 - a simple object standing" in sent  # category of A3 / meaning of the selected slot
        assert "generateContent" in str(seq.requests[1].url) and "gemini-3.5-flash-lite" in str(seq.requests[1].url)
        assert "replacements" in json.dumps(json.loads(seq.requests[1].content)["generationConfig"]["responseSchema"])
        assert all("gemini-3.5-flash-lite" in str(q.url) for q in seq.requests)  # no image model was ever called
        # the extra text call is recorded as planning cost, once
        after = c.get("/api/costs").json()
        assert after["planning_calls"] == before_cost["planning_calls"] + 1 and after["generation_usd"] == 0
        assert after["planning_usd"] > before_cost["planning_usd"]
        assert after["total_usd"] == pytest.approx(after["planning_usd"] + after["generation_usd"])


def test_regeneration_that_duplicates_an_existing_concept_changes_nothing(settings):
    seq = Seq(ai_plan_json(1, COUNTS), repl(("A", 2, "Item 0-5")))  # repeats a KEEP card
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        plan = c.post("/api/plan/test", json=dict(BASE, number_of_variants=1)).json()["plan"]
        r = c.post("/api/plan/regenerate", json=dict(BASE, number_of_variants=1, plan=plan, selected=[dict(variant_id="A", position=2)]))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_invalid"


def test_selection_and_plan_are_validated(settings):
    seq = Seq(ai_plan_json(1, COUNTS))
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        plan = c.post("/api/plan/test", json=dict(BASE, number_of_variants=1)).json()["plan"]
        calls = len(seq.requests)
        body = dict(BASE, number_of_variants=1, plan=plan)
        assert c.post("/api/plan/regenerate", json=dict(body, selected=[])).status_code == 422
        assert c.post("/api/plan/regenerate", json=dict(body, selected=[dict(variant_id="Z", position=1)])).status_code == 422
        r = c.post("/api/plan/regenerate", json=dict(body, genre="Forest", selected=[dict(variant_id="A", position=1)]))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "plan_mismatch"  # plan belongs to another Set
        assert len(seq.requests) == calls  # nothing was sent to the model


def test_generate_uses_the_curated_plan_without_planning_again(settings):
    seq = Seq(ai_plan_json(2, COUNTS), repl(("A", 3, "Sandcastle Flag")))
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        plan = c.post("/api/plan/test", json=BASE).json()["plan"]
        plan = c.post("/api/plan/regenerate", json=dict(BASE, plan=plan, selected=[dict(variant_id="A", position=3)])).json()["plan"]
        calls = len(seq.requests)
        r = c.post("/api/jobs", json=dict(BASE, allow_ai_plan=True, confirmed=True, plan=plan, **MOCK))
        assert r.status_code == 200, r.text
        d = wait(c, r.json()["job_id"])
        assert len(seq.requests) == calls  # no new planning call: the curated plan was used as is
        names = {(v["variant_id"], k["position"]): k["object_name"] for v in d["variants"] for k in v["cards"]}
        assert names == {k: v["object_name"] for k, v in cards_of(plan).items()} and names[("A", 3)] == "Sandcastle Flag"
        assert "plan" not in d["request"] and d["request"]["curated_plan"] is True
        # a stale plan (different Set) is refused instead of being silently replaced
        bad = c.post("/api/jobs", json=dict(BASE, genre="Forest", allow_ai_plan=True, confirmed=True, plan=plan, **MOCK))
        assert bad.status_code == 409 and bad.json()["detail"]["code"] == "plan_mismatch"


def test_one_click_generation_still_plans_automatically_when_there_is_no_plan(settings):
    seq = Seq(ai_plan_json(1, COUNTS))
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        r = c.post("/api/jobs", json=dict(BASE, number_of_variants=1, allow_ai_plan=True, confirmed=True, **MOCK))
        assert r.status_code == 200
        wait(c, r.json()["job_id"])
        assert len(seq.requests) == 1  # the automatic planning call


@pytest.mark.parametrize("raw", ["A", "a", "Variant A", "VARIANT A", "variant-a", " Variant  A "])
def test_equivalent_variant_identifiers_are_accepted(settings, raw):
    seq = Seq(ai_plan_json(1, COUNTS), {"replacements": [dict(variant_id=raw, position=3, object_name="Sandcastle Flag",
                                                              object_description="a flag", rationale="fits")]})
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        plan = c.post("/api/plan/test", json=dict(BASE, number_of_variants=1)).json()["plan"]
        r = c.post("/api/plan/regenerate", json=dict(BASE, number_of_variants=1, plan=plan, selected=[dict(variant_id="A", position=3)]))
        assert r.status_code == 200, r.text
        assert cards_of(r.json()["plan"])[("A", 3)]["object_name"] == "Sandcastle Flag"


@pytest.mark.parametrize("variant,position", [("B", 3), ("Variant B", 3), ("A", 4), ("Z", 3), ("", 3)])
def test_normalization_does_not_weaken_validation(settings, variant, position):
    seq = Seq(ai_plan_json(1, COUNTS), {"replacements": [dict(variant_id=variant or "?", position=position, object_name="Sandcastle Flag",
                                                              object_description="a flag", rationale="x")]})
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        plan = c.post("/api/plan/test", json=dict(BASE, number_of_variants=1)).json()["plan"]
        before = c.get("/api/costs").json()
        r = c.post("/api/plan/regenerate", json=dict(BASE, number_of_variants=1, plan=plan, selected=[dict(variant_id="A", position=3)]))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_invalid"
        after = c.get("/api/costs").json()  # the call was made, so its estimated text cost is recorded once (not hidden)
        assert after["planning_calls"] == before["planning_calls"] + 1 and after["total_usd"] == pytest.approx(after["planning_usd"] + after["generation_usd"])


# ------------------------------------------------------------------ replacing ONE card of a generated job
def _job(c, seq_unused=None):
    r = c.post("/api/jobs", json=dict(BASE, number_of_variants=1, allow_ai_plan=True, confirmed=True, **MOCK))
    return r.json()["job_id"], wait(c, r.json()["job_id"])


def _cards(d):
    return {k["card_id"]: k for v in d["variants"] for k in v["cards"]}


def test_suggest_then_replace_then_generate_one_card_keeps_everything_else(settings, app_factory=None):
    seq = Seq(ai_plan_json(1, COUNTS), repl(("Variant A", 3, "Sandcastle Flag")))
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        job_id, d = _job(c)
        before = _cards(d)
        target = next(k for k in before.values() if k["position"] == 3)
        cost0 = c.get("/api/costs").json()
        row0 = next(j for j in c.get("/api/jobs").json() if j["job_id"] == job_id)["costs"]
        # 1) Gemini proposes a concept: read-only, text only, costed as planning of THIS batch
        r = c.post(f"/api/jobs/{job_id}/cards/{target['card_id']}/suggest-replacement")
        assert r.status_code == 200, r.text
        sug = r.json()
        assert sug["suggestion"]["object_name"] == "Sandcastle Flag" and sug["changed"] is False and sug["image_provider_called"] is False
        assert _cards(c.get(f"/api/jobs/{job_id}").json()) == before  # nothing changed yet
        sent = json.loads(seq.requests[-1].content)["contents"][0]["parts"][0]["text"]
        assert sent.count("[KEEP]") == 9 and sent.count("[REPLACE]") == 1 and "Summer" in sent and "Beach" in sent
        cost1 = c.get("/api/costs").json()
        assert cost1["planning_calls"] == cost0["planning_calls"] + 1 and cost1["generation_usd"] == cost0["generation_usd"]
        row1 = next(j for j in c.get("/api/jobs").json() if j["job_id"] == job_id)["costs"]
        assert row1["planning_usd"] == pytest.approx(row0["planning_usd"] + sug["text_cost_estimate_usd"]) and row1["generation_usd"] == row0["generation_usd"]
        assert cost1["unattributed"] == cost0["unattributed"]
        # 2) approve it: the existing Replace object (with the suggestion's rationale)
        s = sug["suggestion"]
        p = c.patch(f"/api/jobs/{job_id}/cards/{target['card_id']}",
                    json=dict(object_name=s["object_name"], object_description=s["object_description"], rationale=s["rationale"], characters=s["characters"]))
        assert p.status_code == 200, p.text
        mid = _cards(p.json())
        assert mid[target["card_id"]]["status"] == "planned" and mid[target["card_id"]]["object_name"] == "Sandcastle Flag"
        for cid, k in before.items():  # every other card and image is untouched
            if cid != target["card_id"]:
                assert mid[cid]["object_name"] == k["object_name"] and mid[cid]["artifact_id"] == k["artifact_id"] and mid[cid]["versions"] == k["versions"]
        old_vers = [v["version"] for v in before[target["card_id"]]["versions"]]
        assert [v["version"] for v in mid[target["card_id"]]["versions"]] == old_vers  # the old image stays in the history ...
        assert not any(v["is_current"] for v in mid[target["card_id"]]["versions"])  # ... but is no longer current
        # the stored plan follows: no stale subject or rationale reaches the image prompt
        plan_card = next(pc for pc in c.app.state.jobs.store.get_job(job_id)["plan"]["variants"][0]["cards"] if pc["position"] == 3)
        assert plan_card["object_name"] == "Sandcastle Flag" and plan_card["rationale"] == "fits the Set"
        # 3) generate the image of just that card (Mock image provider: free); a new version, no other card regenerated
        g = c.post(f"/api/jobs/{job_id}/cards/{target['card_id']}/regenerate", json={"confirmed": True})
        assert g.status_code == 200, g.text
        end = _cards(wait(c, job_id))
        assert end[target["card_id"]]["status"] == "done" and end[target["card_id"]]["artifact_id"] != before[target["card_id"]]["artifact_id"]
        assert len(end[target["card_id"]]["versions"]) == len(old_vers) + 1
        for cid, k in before.items():
            if cid != target["card_id"]:
                assert end[cid]["artifact_id"] == k["artifact_id"]


def test_manual_replacement_clears_the_old_subjects_rationale(settings):
    seq = Seq(ai_plan_json(1, COUNTS))
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        job_id, d = _job(c)
        target = next(k for k in _cards(d).values() if k["position"] == 2)
        before = next(pc for pc in c.app.state.jobs.store.get_job(job_id)["plan"]["variants"][0]["cards"] if pc["position"] == 2)
        p = c.patch(f"/api/jobs/{job_id}/cards/{target['card_id']}", json=dict(object_name="Beach Umbrella", object_description="a striped umbrella"))
        assert p.status_code == 200
        after = next(pc for pc in c.app.state.jobs.store.get_job(job_id)["plan"]["variants"][0]["cards"] if pc["position"] == 2)
        assert after["object_name"] == "Beach Umbrella" and after["rationale"].startswith("Beach Umbrella") and after["rationale"] != before["rationale"]
        assert c.patch(f"/api/jobs/{job_id}/cards/{target['card_id']}", json=dict(object_name="Item 0-5")).status_code == 422  # duplicate still refused


def test_suggestion_failures_change_nothing(settings):
    seq = Seq(ai_plan_json(1, COUNTS), repl(("A", 3, "Item 0-5")))  # repeats another card
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        job_id, d = _job(c)
        before = _cards(d)
        target = next(k for k in before.values() if k["position"] == 3)
        r = c.post(f"/api/jobs/{job_id}/cards/{target['card_id']}/suggest-replacement")
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_invalid"
        assert _cards(c.get(f"/api/jobs/{job_id}").json()) == before
        assert c.post(f"/api/jobs/{job_id}/cards/nope/suggest-replacement").status_code == 404


def test_ordinary_plan_with_unusable_reply_is_costed_once_but_provider_errors_are_not(settings):
    bad = ai_plan_json(1, COUNTS)
    bad["variants"][0]["cards"][1]["object_name"] = bad["variants"][0]["cards"][0]["object_name"]  # duplicate -> ai_plan_invalid
    seq = Seq(bad)
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        before = c.get("/api/costs").json()
        r = c.post("/api/plan/test", json=dict(BASE, number_of_variants=1))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_invalid"
        a = c.get("/api/costs").json()
        assert a["planning_calls"] == before["planning_calls"] + 1 and a["planning_usd"] > before["planning_usd"]
        assert a["total_usd"] == pytest.approx(a["planning_usd"] + a["generation_usd"])
    err = httpx.MockTransport(lambda req: httpx.Response(500, json={"error": {"message": "boom"}}))
    err.serves_text_plans = True
    with TestClient(create_app(settings, gemini_transport=err)) as c:
        n = c.get("/api/costs").json()["planning_calls"]
        assert c.post("/api/plan/test", json=dict(BASE, number_of_variants=1)).status_code == 502
        assert c.get("/api/costs").json()["planning_calls"] == n  # billing status unknown: no estimated cost invented
