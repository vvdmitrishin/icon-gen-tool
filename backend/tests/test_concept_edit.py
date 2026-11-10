"""Metaphor Planner manual concept editing: a producer override of Subject and/or Description. No AI call, no image generation
(Mock image provider + a mocked text transport that must see NO extra request)."""
import copy

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_free_text_sets import ai_plan_json, wait
from tests.test_metaphor_planner import BASE, COUNTS, MOCK, Seq, _cards, _job, cards_of


def _prompt(c, artifact_id):
    return c.get(f"/api/artifacts/{artifact_id}").json()["prompt"]["effective"]


def test_edited_curated_plan_reaches_the_image_prompt_and_generate(settings):
    seq = Seq(ai_plan_json(1, COUNTS))
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        plan = c.post("/api/plan/test", json=dict(BASE, number_of_variants=1)).json()["plan"]
        before = cards_of(plan)
        edited = copy.deepcopy(plan)  # what the planner table does on Save: only these fields of ONE card change
        tgt = cards_of(edited)[("A", 2)]
        tgt["object_name"], tgt["object_description"] = "Tide Pool Crab", "a small orange crab waving from a bright tide pool"
        calls = len(seq.requests)
        r = c.post("/api/jobs", json=dict(BASE, number_of_variants=1, allow_ai_plan=True, confirmed=True, plan=edited, **MOCK))
        assert r.status_code == 200, r.text
        d = wait(c, r.json()["job_id"])
        assert len(seq.requests) == calls  # no planning call: the edited plan is used as is
        cards = {(v["variant_id"], k["position"]): k for v in d["variants"] for k in v["cards"]}
        assert cards[("A", 2)]["object_name"] == "Tide Pool Crab" and cards[("A", 2)]["category"] == before[("A", 2)]["category"]
        for key, k in before.items():  # every other concept and category is exactly as planned
            if key != ("A", 2):
                assert cards[key]["object_name"] == k["object_name"] and cards[key]["category"] == k["category"]
        p = _prompt(c, cards[("A", 2)]["artifact_id"])
        assert "Tide Pool Crab" in p and "small orange crab waving" in p and before[("A", 2)]["object_name"] not in p


@pytest.mark.parametrize("patch", [
    dict(object_name="Tide Pool Crab"),
    dict(object_description="a freshly described object on a sunny beach"),
    dict(object_name="Tide Pool Crab", object_description="a small orange crab waving"),
])
def test_edit_keeps_existing_image_and_metadata_and_regeneration_uses_the_new_text(settings, patch):
    seq = Seq(ai_plan_json(1, COUNTS))
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        job_id, d = _job(c)
        before = _cards(d)
        tgt = next(k for k in before.values() if k["position"] == 3)
        old_art = tgt["artifact_id"]
        old_prompt = _prompt(c, old_art)
        calls, costs = len(seq.requests), c.get("/api/costs").json()
        r = c.patch(f"/api/jobs/{job_id}/cards/{tgt['card_id']}/concept", json=patch)
        assert r.status_code == 200, r.text
        after = _cards(r.json())
        a = after[tgt["card_id"]]
        assert a["object_name"] == patch.get("object_name", tgt["object_name"])  # unchanged field keeps its value
        assert a["object_description"] == patch.get("object_description", tgt["object_description"])
        # everything else about the card is untouched: image, versions, status, category, position, regeneration count
        for f in ("artifact_id", "versions", "status", "category", "position", "variant_id"):
            assert a.get(f) == tgt.get(f), f
        for cid, k in before.items():
            if cid != tgt["card_id"]:
                assert after[cid] == k
        assert _prompt(c, old_art) == old_prompt  # history/provenance keeps the concept the old image was made from
        assert len(seq.requests) == calls and c.get("/api/costs").json() == costs  # no AI call, no cost
        stored = next(pc for pc in c.app.state.jobs.store.get_job(job_id)["plan"]["variants"][0]["cards"] if pc["position"] == 3)
        assert stored["object_name"] == a["object_name"] and stored["object_description"] == a["object_description"]
        # an explicit regeneration then uses the edited text, and the old version stays
        g = c.post(f"/api/jobs/{job_id}/cards/{tgt['card_id']}/regenerate", json={"confirmed": True})
        assert g.status_code == 200, g.text
        end = _cards(wait(c, job_id))[tgt["card_id"]]
        assert end["artifact_id"] != old_art and len(end["versions"]) == len(tgt["versions"]) + 1
        np = _prompt(c, end["artifact_id"])
        assert a["object_name"] in np and a["object_description"] in np
        if "object_name" in patch:
            assert tgt["object_name"] not in np


def test_edit_validation_and_noop(settings):
    seq = Seq(ai_plan_json(1, COUNTS))
    with TestClient(create_app(settings, gemini_transport=seq.transport)) as c:
        job_id, d = _job(c)
        before = _cards(d)
        tgt = next(k for k in before.values() if k["position"] == 3)
        other = next(k for k in before.values() if k["position"] == 4)
        url = f"/api/jobs/{job_id}/cards/{tgt['card_id']}/concept"
        assert c.patch(url, json={}).status_code == 422                                    # nothing to edit
        assert c.patch(url, json=dict(object_name="   ")).json()["detail"]["code"] == "empty_concept"
        assert c.patch(url, json=dict(object_description="")).json()["detail"]["code"] == "empty_concept"
        assert c.patch(url, json=dict(object_name=other["object_name"].upper())).json()["detail"]["code"] == "duplicate_object"
        assert c.patch(f"/api/jobs/{job_id}/cards/nope/concept", json=dict(object_name="X")).status_code == 404
        assert _cards(c.get(f"/api/jobs/{job_id}").json()) == before                       # every refusal changed nothing
        same = c.patch(url, json=dict(object_name=tgt["object_name"], object_description=tgt["object_description"]))
        assert same.status_code == 200 and _cards(same.json()) == before                   # saving unchanged text is a no-op
        assert len(seq.requests) == 1                                                       # only the original planning call
