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
