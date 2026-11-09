"""Unified AI planning guarantees, session-only prompt overrides, immutable per-job prompt snapshots, shared Set context in
prompts, and prompt transparency (view / preview). Offline only: fixture planner, Mock image provider, no paid call."""
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import FAKE_KEY
from tests.test_free_text_sets import MOCK, TextRecorder, ai_plan_json, wait

CARD = dict(collection="Winter", genre="Forest", object_name="Red Fox", object_description="a fox in the snow", category=3, **MOCK)
SESSION_STYLE = "SESSION STYLE: moody lantern-lit watercolor look."
SESSION_CAT1 = "Composition rule: SESSION CAT1 floating. Background rule: pure white."


def job(**kw):
    b = dict(collection="Winter", genre="Forest", number_of_variants=1, number_of_cards=10, gold_cards=0, confirmed=True, **MOCK)
    b.update(kw)
    return b


def run(c, **kw):
    r = c.post("/api/jobs", json=job(**kw))
    assert r.status_code == 200, r.text
    return wait(c, r.json()["job_id"])


def meta(c, artifact_id):
    return c.get(f"/api/artifacts/{artifact_id}").json()


def done_cards(d):
    return [k for v in d["variants"] for k in v["cards"] if k["status"] == "done"]


# ------------------------------------------------------------------------------------- unified planning
def test_no_curated_pools_remain_in_the_production_code(settings):
    from app.services import planning
    assert not any(hasattr(planning, n) for n in ("plan_set", "curated_genre_for", "match_curated_genre", "resolve_genre", "_pools"))
    assert not (Path(__file__).resolve().parents[1] / "app" / "config" / "object_pools.yaml").exists()


@pytest.mark.parametrize("coll,genre", [("Movies", "Science Fiction"), ("Movie Collection", "Detective"), ("Movies", "Fantasy"),
                                        ("Winter", "Forest"), ("Summer", "Music Festival")])
def test_every_collection_and_set_goes_through_the_same_ai_planner_with_wishes(settings, coll, genre):
    rec = TextRecorder(ai_plan_json(1, {1: 2, 2: 2, 3: 3, 4: 3}))
    wishes = "cozy mood, no buildings"
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        d = run(c, collection=coll, genre=genre, wishes=wishes, allow_ai_plan=True)
        assert d["planning_mode"] == "ai" and len(rec.requests) == 1
        prompt = json.loads(rec.requests[0].content)["contents"][0]["parts"][0]["text"]
        assert f"'{coll}'" in prompt and f"'{genre}'" in prompt and wishes in prompt  # identical mechanism, wishes always sent


def test_missing_text_planner_credentials_fail_clearly_before_any_image_job(tmp_path):
    s = Settings(data_root=tmp_path / "d", artifact_root=tmp_path / "d" / "a")  # no Gemini key: the text planner cannot run
    with TestClient(create_app(s)) as c:
        r = c.post("/api/jobs", json=job())
        assert r.status_code == 400 and r.json()["detail"]["code"] == "gemini_key_missing"
        assert "Settings" in r.json()["detail"]["message"] and "API Connections" in r.json()["detail"]["message"]
        assert c.get("/api/jobs").json() == [] and c.get("/api/artifacts").json() == []  # nothing was generated


def test_planner_failure_never_falls_back_to_curated_subjects(settings):
    rec = TextRecorder(None, status=500)
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs", json=job(collection="Movies", genre="Science Fiction"))
        assert r.status_code == 502 and r.json()["detail"]["code"] == "ai_plan_failed"
        assert c.get("/api/jobs").json() == [] and c.get("/api/artifacts").json() == []


# ------------------------------------------------------------------------------------- session prompt overrides
def test_override_changes_the_compiled_prompt_and_reset_restores_default(settings):
    with TestClient(create_app(settings)) as c:
        base = c.post("/api/prompt/preview", json=CARD).json()
        assert SESSION_STYLE not in base["effective_prompt"] and base["session_overrides"] == []
        over = c.post("/api/prompt/preview", json={**CARD, "prompt_overrides": {"system_style": SESSION_STYLE}}).json()
        assert SESSION_STYLE in over["effective_prompt"] and over["session_overrides"] == ["system_style"]
        assert over["bundle"]["system_style"]["source"] == "session_override" and over["bundle"]["category_3"]["source"] == "library"
        assert "Mandatory rules (always apply" in over["effective_prompt"]  # protected block when an override is active
        reset = c.post("/api/prompt/preview", json=CARD).json()  # "reset" = the browser stops sending the override
        assert reset["effective_prompt"] == base["effective_prompt"] and reset["session_overrides"] == []
        # nothing permanent changed: still version 1 of every template
        assert [i["version"] for i in c.get("/api/prompts").json()] == [4, 3, 3, 4, 4, 3, 4, 1] and all(i["is_default_text"] for i in c.get("/api/prompts").json())


def test_override_validation_and_mandatory_warnings(settings):
    with TestClient(create_app(settings)) as c:
        for bad in ({"repair": "x {note}"}, {"nope": "x"}, {"system_style": "   "}, {"system_style": "x" * 5001}):
            r = c.post("/api/prompts/session-check", json={"overrides": bad})
            assert r.status_code == 422, bad
        ok = c.post("/api/prompts/session-check", json={"overrides": {"image_closing": "Add a golden frame and a title text. Show 4 characters."}}).json()
        assert ok["ok"] and len(ok["warnings"]) >= 1 and any("mandatory" in w for w in ok["warnings"])
        ok5 = c.post("/api/prompts/session-check", json={"overrides": {"category_5": "Show five characters laughing."}}).json()
        assert any("2 characters" in w for w in ok5["warnings"])
        # even a hostile override cannot remove the mandatory rules from the final prompt
        p = c.post("/api/prompt/preview", json={**CARD, "category": 5, "prompt_overrides": {"image_closing": "Add a frame with text."}}).json()
        assert p["effective_prompt"].rstrip().endswith("A golden card shows at most 2 characters.")
        assert "no text, lettering, labels, captions or watermark" in p["effective_prompt"]


def test_job_applies_overrides_to_every_card_and_records_provenance(settings):
    with TestClient(create_app(settings)) as c:
        d = run(c, prompt_overrides={"system_style": SESSION_STYLE, "category_1": SESSION_CAT1})
        assert d["prompt_overrides"] == ["category_1", "system_style"] and "prompt_bundle" in d
        assert "prompt_bundle" not in d["request"]  # snapshot texts are not shipped in every poll
        cards = done_cards(d)
        assert len(cards) == 10
        for k in cards:
            p = meta(c, k["artifact_id"])["prompt"]
            assert SESSION_STYLE in p["effective"]  # affects the REAL request text, not just the UI
            assert p["session_overrides"] == ["category_1", "system_style"]
            assert p["bundle"]["system_style"] == {"source": "session_override", "version": None, "base_version": 4}
            assert p["bundle"]["category_2"] == {"source": "library", "version": 3, "base_version": 3}
            assert p["effective_texts"]["system_style"] == SESSION_STYLE and p["effective_texts"]["image_closing"]
            assert p["effective_texts"]["category_rule"] == (SESSION_CAT1 if k["category"] == 1 else p["effective_texts"]["category_rule"])
            assert (SESSION_CAT1 in p["effective"]) == (k["category"] == 1)
            assert p["mandatory_block_appended"] is True
        assert [e for e in c.get(f"/api/jobs/{d['job_id']}/events").json() if "SESSION overrides" in e["message"]]
        assert [i["version"] for i in c.get("/api/prompts").json()] == [4, 3, 3, 4, 4, 3, 4, 1]  # no permanent version was created


def test_snapshot_is_immutable_for_the_whole_job_even_if_the_library_changes_while_it_runs(settings):
    app = create_app(settings)
    jobs = app.state.jobs
    with TestClient(app) as c:
        import asyncio
        held: list = []
        real_schedule = jobs._schedule
        # the job exists and is queued, but has not started generating (remember the app's event loop to start it later)
        jobs._schedule = lambda jid: held.append((jid, asyncio.get_running_loop()))
        r = c.post("/api/jobs", json=job(prompt_overrides={"system_style": SESSION_STYLE}))
        assert r.status_code == 200 and held
        jid, loop = held[0]
        # the Producer now edits the PERMANENT library while the batch is waiting / running
        assert c.post("/api/prompts", json={"name": "image_closing", "text": "NEW PERMANENT CLOSING v2.", "note": "mid-job"}).status_code == 200
        assert c.post("/api/prompts", json={"name": "category_2", "text": "Composition rule: NEW CAT2. Background rule: z."}).status_code == 200
        loop.call_soon_threadsafe(real_schedule, jid)
        d = wait(c, jid)
        for k in done_cards(d):
            p = meta(c, k["artifact_id"])["prompt"]
            assert "NEW PERMANENT CLOSING" not in p["effective"] and "NEW CAT2" not in p["effective"]  # snapshot, not live
            assert SESSION_STYLE in p["effective"] and p["bundle"]["image_closing"]["version"] == 4
        # a regeneration of a card of that job still uses the job's original snapshot
        card = done_cards(d)[0]
        before = meta(c, card["artifact_id"])["prompt"]["effective"]
        jobs._schedule = real_schedule
        rr = c.post(f"/api/jobs/{jid}/cards/{card['card_id']}/regenerate", json={"confirmed": True, "note": "cleaner"})
        assert rr.status_code == 200, rr.text
        d2 = wait(c, jid)
        card2 = next(k for v in d2["variants"] for k in v["cards"] if k["card_id"] == card["card_id"])
        assert card2["artifact_id"] != card["artifact_id"]
        p2 = meta(c, card2["artifact_id"])["prompt"]
        assert "NEW PERMANENT CLOSING" not in p2["effective"] and SESSION_STYLE in p2["effective"] and "cleaner" in p2["effective"]
        assert meta(c, card["artifact_id"])["prompt"]["effective"] == before  # the historical image's prompt is untouched
        # a NEW job picks up the new permanent versions
        d3 = run(c, genre="Mountains")
        p3 = meta(c, done_cards(d3)[0]["artifact_id"])["prompt"]
        assert "NEW PERMANENT CLOSING v2." in p3["effective"] and p3["bundle"]["image_closing"]["version"] == 5  # v1 built-in, v2 = v0.5, v3 = v0.6, v4 = v0.7, v5 = this save


def test_variant_title_concept_and_rationale_reach_every_image_prompt(settings):
    with TestClient(create_app(settings)) as c:
        d = run(c, number_of_variants=2)
        for v in d["variants"]:
            for k in v["cards"]:
                p = meta(c, k["artifact_id"])["prompt"]
                assert f"Set variant: {v['title']} - " in p["effective"] and v["concept"][:30] in p["effective"]
                assert "Why it belongs to the Set: clearly part of Forest." in p["effective"]  # concise rationale
                assert "never replaces this subject or the rules" in p["effective"]
                assert p["planning_context"]["variant_title"] == v["title"] and p["planning_context"]["set"] == "Forest"
                assert k["object_name"] in p["effective"] and f"Category {k['category']}" in p["effective"]  # subject + rules remain


# ------------------------------------------------------------------------------------- transparency
def test_planned_prompt_preview_matches_what_real_generation_sends(settings):
    with TestClient(create_app(settings)) as c:
        body = job(prompt_overrides={"system_style": SESSION_STYLE}, allow_ai_plan=True, number_of_variants=2)
        n_art, spent = len(c.get("/api/artifacts").json()), c.get("/api/models").json()["budget"]["spent_estimated_usd"]
        pv = c.post("/api/jobs/preview-prompts", json=body)
        assert pv.status_code == 200, pv.text
        pv = pv.json()
        assert pv["session_overrides"] == ["system_style"] and set(pv["variants"]) == {"A", "B"}
        assert len(c.get("/api/artifacts").json()) == n_art  # no image generated, no provider call
        d = run(c, prompt_overrides={"system_style": SESSION_STYLE}, number_of_variants=2)
        for v in d["variants"]:
            for k in v["cards"]:
                real = meta(c, k["artifact_id"])["prompt"]["effective"]
                planned = next(r for r in pv["variants"][v["variant_id"]] if r["position"] == k["position"])
                assert planned["object_name"] == k["object_name"] and planned["prompt"] == real  # same compiler, same inputs


def test_job_prompts_endpoint_shows_stored_and_planned_prompts_without_generating(settings):
    with TestClient(create_app(settings)) as c:
        d = run(c, scope="pilot", prompt_overrides={"system_style": SESSION_STYLE})  # pilot: only some cards are generated
        n_art, spent = len(c.get("/api/artifacts").json()), c.get("/api/models").json()["budget"]["spent_estimated_usd"]
        r = c.get(f"/api/jobs/{d['job_id']}/prompts").json()
        assert r["snapshot"] is True and r["bundle"]["system_style"]["source"] == "session_override"
        sources = {x["source"] for x in r["prompts"]}
        assert sources == {"generated", "compiled"}
        for x in r["prompts"]:
            assert SESSION_STYLE in x["prompt"] and x["model"] == "mock-placeholder-v1" and "bundle" in x
            if x["source"] == "generated":
                assert x["prompt"] == meta(c, x["artifact_id"])["prompt"]["effective"]  # the immutable stored prompt
        only_a = c.get(f"/api/jobs/{d['job_id']}/prompts", params={"variant_id": "A"}).json()["prompts"]
        assert len(only_a) == 10
        assert len(c.get("/api/artifacts").json()) == n_art
        assert c.get("/api/models").json()["budget"]["spent_estimated_usd"] == spent  # viewing prompts costs nothing


def test_overrides_in_the_job_request_are_validated_before_anything_runs(settings):
    with TestClient(create_app(settings)) as c:
        r = c.post("/api/jobs", json=job(prompt_overrides={"category_9": "x"}))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_template"
        assert c.get("/api/jobs").json() == []


def test_historical_job_without_a_snapshot_still_loads_and_exports_unchanged_prompts(settings):
    """Jobs created before snapshots existed have no prompt_bundle: they load, and their stored image prompts are untouched."""
    app = create_app(settings)
    with TestClient(app) as c:
        d = run(c)
        card = done_cards(d)[0]
        before = meta(c, card["artifact_id"])["prompt"]["effective"]
        job_row = app.state.jobs.store.get_job(d["job_id"])
        rq = {k: v for k, v in job_row["request"].items() if k != "prompt_bundle"}
        app.state.jobs.store._exec("UPDATE jobs SET request_json=? WHERE job_id=?", (json.dumps(rq), d["job_id"]))
        old = c.get(f"/api/jobs/{d['job_id']}").json()
        assert old["prompt_bundle"] == {} and old["prompt_overrides"] == []
        p = c.get(f"/api/jobs/{d['job_id']}/prompts").json()
        assert p["snapshot"] is False and any(x["prompt"] == before for x in p["prompts"])
        assert meta(c, card["artifact_id"])["prompt"]["effective"] == before


# ------------------------------------------------------------------------------------- references with a real provider adapter
def test_klein_receives_only_applicable_reference_images(tmp_path):
    from tests.test_cloudflare_models import CfArt, job_body, settings as cf_settings
    srv = CfArt()
    with TestClient(create_app(cf_settings(tmp_path), cloudflare_transport=srv.transport)) as c:
        up = c.post("/api/references", files={"file": ("r.png", open_png(), "image/png")}, data={"ref_type": "category_1"}).json()
        r = c.post("/api/jobs", json=job_body(reference_ids=[up["ref_id"]], reference_policy_accepted=True, confirmed=True,
                                              collection="Winter", genre="Forest"))
        assert r.status_code == 200, r.text
        d = wait(c, r.json()["job_id"])
        with_ref = sum(b"input_image_0" in q.content for q in srv.requests)
        cat1_cards = sum(1 for k in done_cards(d) if k["category"] == 1)
        assert with_ref == cat1_cards and len(srv.requests) == len(done_cards(d))  # only category 1 cards got the image


def open_png():
    from tests.conftest import art_png
    return art_png(300, 2)
