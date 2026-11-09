import asyncio
import json
import sqlite3
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import ArtRecorder, Recorder, art_png, gemini_ok_response

GEMINI = dict(provider="gemini", model="gemini-nano-banana-2.1")
MOCK = dict(provider="mock", model="mock-placeholder-v1")


def body(**kw):
    base = dict(genre="Detective", wishes="", number_of_variants=1, scope="full", confirmed=True, **GEMINI)
    base.update(kw)
    return base


def wait(c, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/jobs/{job_id}").json()
        if not d["active"]:
            return d
        time.sleep(0.03)
    raise AssertionError("job did not finish")


def make_job(c, **kw):
    r = c.post("/api/jobs", json=body(**kw))
    assert r.status_code == 200, r.text
    return r.json()["job_id"], wait(c, r.json()["job_id"])


def cards(d, vid="A"):
    return next(v for v in d["variants"] if v["variant_id"] == vid)["cards"]


@pytest.fixture
def rec():
    return ArtRecorder()


def client(settings, rec):
    return TestClient(create_app(settings, gemini_transport=rec.transport))


# ----------------------------------------------------------------------------------- QA
def test_clean_full_set_passes_technical_and_set_qa(settings, rec):
    with client(settings, rec) as c:
        job_id, d = make_job(c)
        assert d["status"] == "ready_for_review" and d["counts"]["done"] == 10
        assert all(k["qa_status"] == "passed" and k["art_review"] == "pending" for k in cards(d))
        v = d["variants"][0]
        assert v["set_qa"]["status"] == "passed" and v["set_qa"]["generated"] == 10
        ev = " ".join(e["message"] for e in c.get(f"/api/jobs/{job_id}/events").json())
        assert "Technical QA: 10 passed" in ev and "Set QA: A=passed" in ev
        rep = c.get(f"/api/jobs/{job_id}/qa").json()
        assert rep["technical"]["passed"] == 10 and rep["job_checks"][0]["status"] == "pass"
        assert "NOT detected" in rep["limitations"]
        assert c.post(f"/api/jobs/{job_id}/qa").json()["technical"]["passed"] == 10


def test_identical_images_fail_set_qa_and_block_approval(settings):
    same = Recorder(body=gemini_ok_response(image=art_png(seed=1)))
    with client(settings, same) as c:
        job_id, d = make_job(c)
        sq = d["variants"][0]["set_qa"]
        assert sq["status"] == "failed" and any(x["id"] == "SQ-05" and x["status"] == "fail" for x in sq["checks"])
        r = c.post(f"/api/jobs/{job_id}/variants/A/approve")
        assert r.status_code == 400 and r.json()["detail"]["code"] == "not_approvable"


# -------------------------------------------------------------------- review & approval
def test_review_and_variant_approval_flow(settings, rec):
    with client(settings, rec) as c:
        job_id, d = make_job(c)
        k = cards(d)
        r = c.post(f"/api/jobs/{job_id}/cards/{k[0]['card_id']}/review", json={"decision": "approved", "note": "great"})
        assert r.status_code == 200 and cards(r.json())[0]["art_review"] == "approved" and cards(r.json())[0]["review_note"] == "great"

        c.post(f"/api/jobs/{job_id}/cards/{k[1]['card_id']}/review", json={"decision": "rejected"})
        r = c.post(f"/api/jobs/{job_id}/variants/A/approve")
        assert r.status_code == 400 and "rejected" in r.json()["detail"]["message"]
        c.post(f"/api/jobs/{job_id}/cards/{k[1]['card_id']}/review", json={"decision": "pending"})

        r = c.post(f"/api/jobs/{job_id}/variants/A/approve")
        assert r.status_code == 200
        d = r.json()
        assert d["approved_variant_id"] == "A" and d["variants"][0]["approved"]
        assert all(x["art_review"] == "approved" for x in cards(d))  # pending cards marked approved

        d = c.post(f"/api/jobs/{job_id}/cards/{k[2]['card_id']}/review", json={"decision": "rejected"}).json()
        assert d["approved_variant_id"] is None  # rejecting a card withdraws the approval
        assert c.post(f"/api/jobs/{job_id}/variants/A/unapprove").status_code == 400


def test_only_one_variant_is_final_and_incomplete_cannot_be_approved(settings):
    with TestClient(create_app(settings)) as c:
        _, pilot = make_job(c, **MOCK, scope="pilot", confirmed=False)
        r = c.post(f"/api/jobs/{pilot['job_id']}/variants/A/approve")
        assert r.status_code == 400 and "only 4/10" in r.json()["detail"]["message"]

        job_id, _d = make_job(c, **MOCK, number_of_variants=2, confirmed=False)
        assert c.post(f"/api/jobs/{job_id}/variants/A/approve").json()["approved_variant_id"] == "A"
        d = c.post(f"/api/jobs/{job_id}/variants/B/approve").json()
        assert d["approved_variant_id"] == "B" and [v["approved"] for v in d["variants"]] == [False, True]
        assert c.post(f"/api/jobs/{job_id}/variants/Z/approve").status_code == 404
        assert c.post(f"/api/jobs/{job_id}/variants/B/unapprove").json()["approved_variant_id"] is None


def test_review_requires_an_image(settings):
    with TestClient(create_app(settings)) as c:
        job_id, d = make_job(c, **MOCK, scope="pilot", confirmed=False)
        planned = next(k for k in cards(d) if k["status"] == "planned")
        r = c.post(f"/api/jobs/{job_id}/cards/{planned['card_id']}/review", json={"decision": "approved"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "no_image"


# --------------------------------------------------------------------------- regeneration
def test_regeneration_adds_version_keeps_old_one_and_uses_repair_note(settings, rec):
    with client(settings, rec) as c:
        job_id, d = make_job(c)
        c.post(f"/api/jobs/{job_id}/variants/A/approve")
        target = cards(d)[4]
        old_artifact = target["artifact_id"]
        n_before = len(rec.requests)

        r = c.post(f"/api/jobs/{job_id}/cards/{target['card_id']}/regenerate", json={"confirmed": False})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "confirmation_required"
        assert len(rec.requests) == n_before

        note = "remove all text, simpler background"
        r = c.post(f"/api/jobs/{job_id}/cards/{target['card_id']}/regenerate", json={"confirmed": True, "note": note})
        assert r.status_code == 200
        d = wait(c, job_id)
        k = next(x for x in cards(d) if x["card_id"] == target["card_id"])
        assert len(rec.requests) == n_before + 1  # exactly ONE provider call; the other 9 cards untouched
        assert k["artifact_id"] != old_artifact and k["regen_count"] == 1 and k["regen_remaining"] == 1
        assert [v["version"] for v in k["versions"]] == [2, 1] and [v["is_current"] for v in k["versions"]] == [True, False]
        assert k["versions"][0]["note"] == note
        assert k["art_review"] == "pending"          # the new version needs a fresh review
        assert d["approved_variant_id"] is None      # content changed -> approval withdrawn
        assert c.get(f"/api/artifacts/{old_artifact}/image").status_code == 200  # old file immutable & kept
        sent = json.loads(rec.requests[-1].content)["contents"][0]["parts"][0]["text"]
        assert "Regenerate the SAME concept" in sent and note in sent
        meta = c.get(f"/api/artifacts/{k['artifact_id']}").json()
        assert meta["prompt"]["repair_note"] == note and meta["context"]["regeneration"] is True
        assert d["spent_estimated_usd"] == pytest.approx(0.737)


def test_regeneration_limit(settings, rec):
    with client(settings, rec) as c:
        job_id, d = make_job(c)
        cid = cards(d)[0]["card_id"]
        for _ in range(2):
            assert c.post(f"/api/jobs/{job_id}/cards/{cid}/regenerate", json={"confirmed": True}).status_code == 200
            wait(c, job_id)
        r = c.post(f"/api/jobs/{job_id}/cards/{cid}/regenerate", json={"confirmed": True})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "regen_limit"
        assert len(rec.requests) == 12


def test_regeneration_is_not_blocked_by_lifetime_spend(settings, rec):
    with client(settings, rec) as c:
        job_id, d = make_job(c)  # 10 x 0.067 = 0.67 spent
        r = c.post(f"/api/jobs/{job_id}/cards/{cards(d)[0]['card_id']}/regenerate", json={"confirmed": True})
        assert r.status_code == 200


def test_failed_regeneration_keeps_the_previous_image(settings, rec):
    with client(settings, rec) as c:
        job_id, d = make_job(c)
        k = cards(d)[2]
        rec.fail = True
        c.post(f"/api/jobs/{job_id}/cards/{k['card_id']}/regenerate", json={"confirmed": True})
        d = wait(c, job_id)
        k2 = next(x for x in cards(d) if x["card_id"] == k["card_id"])
        assert k2["status"] == "done" and k2["artifact_id"] == k["artifact_id"] and len(k2["versions"]) == 1
        assert "overloaded" in k2["error"] and k2["regen_count"] == 0
        assert d["status"] == "ready_for_review" and d["counts"]["failed"] == 0


def test_generate_a_single_planned_card_from_a_pilot(settings, rec):
    with client(settings, rec) as c:
        job_id, d = make_job(c, scope="pilot")
        planned = next(k for k in cards(d) if k["status"] == "planned")
        n = len(rec.requests)
        assert c.post(f"/api/jobs/{job_id}/cards/{planned['card_id']}/regenerate", json={"confirmed": True}).status_code == 200
        d = wait(c, job_id)
        k = next(x for x in cards(d) if x["card_id"] == planned["card_id"])
        assert k["status"] == "done" and k["regen_count"] == 0 and len(rec.requests) == n + 1
        assert d["counts"]["done"] == 5


def test_regenerate_rejected_while_job_active(settings):
    async def slow(request):
        await asyncio.sleep(0.3)
        return httpx.Response(200, json=gemini_ok_response())

    with TestClient(create_app(settings, gemini_transport=httpx.MockTransport(slow))) as c:
        job_id = c.post("/api/jobs", json=body(scope="pilot")).json()["job_id"]
        r = c.post(f"/api/jobs/{job_id}/cards/{job_id}-A-01/regenerate", json={"confirmed": True})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "job_active"
        c.post(f"/api/jobs/{job_id}/cancel")
        wait(c, job_id)


# ------------------------------------------------------------------------- replace a card
def test_replace_card_resets_it_and_keeps_history(settings, rec):
    with client(settings, rec) as c:
        job_id, d = make_job(c)
        c.post(f"/api/jobs/{job_id}/variants/A/approve")
        k = cards(d)[5]
        used = cards(d)[0]["object_name"]

        r = c.patch(f"/api/jobs/{job_id}/cards/{k['card_id']}", json={"object_name": used})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "duplicate_object"

        r = c.patch(f"/api/jobs/{job_id}/cards/{k['card_id']}",
                    json={"object_name": "Brass Periscope", "object_description": "a shiny periscope"})
        assert r.status_code == 200
        k2 = next(x for x in cards(r.json()) if x["card_id"] == k["card_id"])
        assert k2["status"] == "planned" and k2["object_name"] == "Brass Periscope" and k2["image_url"] is None
        assert k2["spec_version"] == 2 and all(not v["is_current"] for v in k2["versions"]) and len(k2["versions"]) == 1
        assert r.json()["approved_variant_id"] is None and r.json()["counts"]["planned"] == 1

        n = len(rec.requests)
        assert c.post(f"/api/jobs/{job_id}/cards/{k['card_id']}/regenerate", json={"confirmed": True}).status_code == 200
        d = wait(c, job_id)
        k3 = next(x for x in cards(d) if x["card_id"] == k["card_id"])
        assert k3["status"] == "done" and k3["object_name"] == "Brass Periscope" and len(rec.requests) == n + 1
        assert [v["version"] for v in k3["versions"]] == [2, 1]
        assert "Brass Periscope" in json.loads(rec.requests[-1].content)["contents"][0]["parts"][0]["text"]


# ------------------------------------------------------------------------------ migration
def test_phase3_database_is_migrated_and_backfilled(settings, rec):
    with client(settings, rec) as c:
        job_id, _d = make_job(c)
    db = sqlite3.connect(settings.database_path)
    db.executescript("DROP TABLE card_versions; DROP TABLE variant_approvals; DROP TABLE comparison_refs;"
                     "ALTER TABLE cards DROP COLUMN regen_count; ALTER TABLE cards DROP COLUMN pending_note;"
                     "ALTER TABLE cards DROP COLUMN spec_version;")
    db.commit()
    db.close()
    with client(settings, rec) as c2:  # startup migrates and backfills version 1 with QA
        d = c2.get(f"/api/jobs/{job_id}").json()
        assert all(len(x["versions"]) == 1 and x["qa_status"] == "passed" and x["regen_count"] == 0 for x in cards(d))
        assert d["variants"][0]["set_qa"]["status"] == "passed"
        assert c2.post(f"/api/jobs/{job_id}/variants/A/approve").status_code == 200
