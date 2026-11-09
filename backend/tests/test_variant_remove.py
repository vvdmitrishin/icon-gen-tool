"""Skipping (removing) a variant that has no generated image yet. Offline, Mock images."""
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_free_text_sets import MOCK, wait


def _cancelled_job_with_ungenerated_variants(c, app):
    r = c.post("/api/jobs", json=dict(collection="Movie", genre="Comedy", number_of_cards=10, gold_cards=0, number_of_variants=3, confirmed=True, **MOCK))
    jid = r.json()["job_id"]
    d = wait(c, jid)
    store = app.state.jobs.store
    for v in d["variants"][1:]:  # simulate a cancelled run: variants B and C were never generated
        for k in v["cards"]:
            store.update_card(k["card_id"], status="planned", artifact_id=None)
    store.set_status(jid, "cancelled")
    return jid


def test_remove_ungenerated_variants_but_never_generated_ones(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        jid = _cancelled_job_with_ungenerated_variants(c, app)
        before = c.get(f"/api/jobs/{jid}").json()
        assert before["counts"]["planned"] == 20 and before["can_continue"] and len(before["variants"]) == 3
        assert c.delete(f"/api/jobs/{jid}/variants/Z").status_code == 404
        # remove B: its cards disappear, the rest stays, the estimate shrinks, A is untouched
        r = c.delete(f"/api/jobs/{jid}/variants/B")
        assert r.status_code == 200, r.text
        d = r.json()
        assert [v["variant_id"] for v in d["variants"]] == ["A", "C"] and d["counts"]["total"] == 20 and d["counts"]["planned"] == 10
        assert d["request"]["number_of_variants"] == 2 and d["can_continue"]
        assert all(k["image_url"] for k in d["variants"][0]["cards"])
        assert any("Variant B removed" in e["message"] for e in c.get(f"/api/jobs/{jid}/events").json())
        assert next(j for j in c.get("/api/jobs").json() if j["job_id"] == jid)["variants"] == 2
        # removing C leaves only A: nothing left to continue, so the job is reviewable again
        r = c.delete(f"/api/jobs/{jid}/variants/C")
        assert r.status_code == 200 and r.json()["status"] == "ready_for_review" and not r.json()["can_continue"]
        # the last variant can never be removed
        assert c.delete(f"/api/jobs/{jid}/variants/A").status_code == 409


def test_variant_with_generated_images_can_be_removed_but_files_stay(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        jid = c.post("/api/jobs", json=dict(collection="Movie", genre="Comedy", number_of_cards=10, gold_cards=0, number_of_variants=2, confirmed=True, **MOCK)).json()["job_id"]
        d = wait(c, jid)
        arts = [k["artifact_id"] for k in d["variants"][0]["cards"]]
        assert all(arts) and c.post(f"/api/jobs/{jid}/variants/A/approve", json={"mark_cards_approved": True}).status_code == 200
        r = c.delete(f"/api/jobs/{jid}/variants/A")  # an approved variant with all 10 images
        assert r.status_code == 200, r.text
        d2 = r.json()
        assert [v["variant_id"] for v in d2["variants"]] == ["B"] and d2["counts"]["total"] == 10 and d2["approved_variant_id"] is None
        assert all(c.get(f"/api/artifacts/{a}/image").status_code == 200 for a in arts)  # the files stay in History
        assert any("Variant A removed" in e["message"] and "10 with generated images" in e["message"] for e in c.get(f"/api/jobs/{jid}/events").json())


def test_cannot_remove_while_the_job_is_running(settings):
    app = create_app(settings)
    jobs = app.state.jobs
    with TestClient(app) as c:
        held = []
        jobs._schedule = lambda jid: held.append(jid)  # stays queued = active
        jid = c.post("/api/jobs", json=dict(collection="Movie", genre="Comedy", number_of_cards=10, gold_cards=0, number_of_variants=2, confirmed=True, **MOCK)).json()["job_id"]
        r = c.delete(f"/api/jobs/{jid}/variants/B")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "job_active"
