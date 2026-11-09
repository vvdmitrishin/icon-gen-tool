import hashlib
import io
import json
import re
import time
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import ArtRecorder, art_png

GEMINI = dict(provider="gemini", model="gemini-nano-banana-2.1")
MOCK = dict(provider="mock", model="mock-placeholder-v1")


def wait(c, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/jobs/{job_id}").json()
        if not d["active"]:
            return d
        time.sleep(0.03)
    raise AssertionError("job did not finish")


def job(c, genre="Detective", scope="full", confirmed=True, **prov):
    r = c.post("/api/jobs", json=dict(genre=genre, number_of_variants=1, scope=scope, confirmed=confirmed, **(prov or GEMINI)))
    assert r.status_code == 200, r.text
    return r.json()["job_id"], wait(c, r.json()["job_id"])


@pytest.fixture
def rec():
    return ArtRecorder()


@pytest.fixture
def big(settings):
    return replace(settings, total_budget_usd=10.0)


def zipfile_of(c, export):
    r = c.get(export["download_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(r.content)), r


# ------------------------------------------------------------------------------- variant export
def test_export_contains_every_spec_file_with_correct_manifest(big, rec):
    with TestClient(create_app(big, gemini_transport=rec.transport)) as c:
        job_id, _ = job(c)
        assert c.post(f"/api/jobs/{job_id}/variants/A/approve").status_code == 200
        r = c.post("/api/exports", json={"job_id": job_id})
        assert r.status_code == 200, r.text
        exp = r.json()
        assert exp["kind"] == "variant" and exp["approved"] is True and exp["contains_mock_images"] is False
        z, resp = zipfile_of(c, exp)
        names = z.namelist()
        for required in ("README.txt", "manifest.json", "set-plan.json", "QA-report.json", "contact-sheet.png"):
            assert required in names
        art = [n for n in names if n.startswith("artwork/")]
        assert len(art) == 10 and all(re.fullmatch(r"artwork/\d\d_[a-z0-9-]+_cat[1-4]\.png", n) for n in art)
        assert 'filename="' in resp.headers["content-disposition"] and "Approved.zip" in resp.headers["content-disposition"]

        m = json.loads(z.read("manifest.json"))
        assert m["approved"] and m["variant_id"] == "A" and m["genre"] == "Detective" and len(m["cards"]) == 10
        assert m["spec_version"] == "1.7" and m["cost_is_estimate"] and "Text-in-image" in m["limitations"]
        for card in m["cards"]:  # manifest hashes match the real files in the ZIP
            assert hashlib.sha256(z.read(card["file"])).hexdigest() == card["sha256"]
            assert card["provider"] == "gemini" and card["model"] == "gemini-nano-banana-2.1" and card["technical_qa"] == "passed"
            assert card["prompt"]["template_versions"]["category_1"] == 4 and card["art_review"] == "approved"
        assert sorted(x["category"] for x in m["cards"]) == [1, 1, 2, 2, 3, 3, 3, 4, 4, 4]
        # artwork is artwork-only and untouched; the contact sheet is a separate overview image
        assert z.read("contact-sheet.png")[:4] == b"\x89PNG"
        plan = json.loads(z.read("set-plan.json"))
        assert len(plan["cards"]) == 10 and plan["variant"]["variant_id"] == "A"
        everything = b"".join(z.read(n) for n in names if n.endswith((".json", ".txt")))
        assert b"AIza" not in everything and b"api_key" not in everything.lower()
        assert c.get("/api/exports").json()[0]["export_id"] == exp["export_id"]


def test_export_blocking_rules(big, rec):
    with TestClient(create_app(big, gemini_transport=rec.transport)) as c:
        job_id, _ = job(c)
        r = c.post("/api/exports", json={"job_id": job_id})                       # nothing approved, no variant named
        assert r.status_code == 400 and r.json()["detail"]["code"] == "no_variant"
        r = c.post("/api/exports", json={"job_id": job_id, "variant_id": "A"})    # named but unapproved
        assert r.status_code == 400 and "not approved" in r.json()["detail"]["message"]
        ok = c.post("/api/exports", json={"job_id": job_id, "variant_id": "A", "include_unapproved": True})
        assert ok.status_code == 200 and ok.json()["approved"] is False
        assert "Unapproved.zip" in c.get(ok.json()["download_url"]).headers["content-disposition"]
        z, _ = zipfile_of(c, ok.json())
        assert "NOT APPROVED" in z.read("README.txt").decode()
        assert c.post("/api/exports", json={"job_id": job_id, "variant_id": "Z", "include_unapproved": True}).status_code == 404
        assert c.post("/api/exports", json={"job_id": "job-nope"}).status_code == 404

        # a rejected card blocks export even when unapproved exports are allowed
        d = c.get(f"/api/jobs/{job_id}").json()
        c.post(f"/api/jobs/{job_id}/cards/{d['variants'][0]['cards'][0]['card_id']}/review", json={"decision": "rejected"})
        r = c.post("/api/exports", json={"job_id": job_id, "variant_id": "A", "include_unapproved": True})
        assert r.status_code == 400 and "rejected" in r.json()["detail"]["message"]


def test_incomplete_set_and_mock_images_are_blocked(big, rec):
    with TestClient(create_app(big, gemini_transport=rec.transport)) as c:
        pilot, _ = job(c, scope="pilot")
        r = c.post("/api/exports", json={"job_id": pilot, "variant_id": "A", "include_unapproved": True})
        assert r.status_code == 400 and "incomplete" in r.json()["detail"]["message"]
        m, _ = job(c, confirmed=False, **MOCK)
        c.post(f"/api/jobs/{m}/variants/A/approve")
        r = c.post("/api/exports", json={"job_id": m})
        assert r.status_code == 400 and "MOCK" in r.json()["detail"]["message"]
        ok = c.post("/api/exports", json={"job_id": m, "allow_mock": True})
        assert ok.status_code == 200 and ok.json()["contains_mock_images"] is True
        z, _ = zipfile_of(c, ok.json())
        assert json.loads(z.read("manifest.json"))["contains_mock_images"] is True and "MOCK" in z.read("README.txt").decode()


def test_download_endpoint_rejects_bad_ids(big, rec):
    with TestClient(create_app(big, gemini_transport=rec.transport)) as c:
        assert c.get("/api/exports/exp-nope/download").status_code == 404
        assert c.get("/api/exports/..%2F..%2Fsecret/download").status_code in (404, 400)
        assert c.get("/api/exports/exp-20260101-000000-abcd/download").status_code == 404


# ----------------------------------------------------------------------- collection workflow
def test_collection_overview_and_final_bundle(big, rec):
    with TestClient(create_app(big, gemini_transport=rec.transport)) as c:
        ov = c.get("/api/collection/overview").json()
        assert [g["genre"] for g in ov["genres"]] == ["Science Fiction", "Detective", "Fantasy"]
        assert not ov["final_ready"] and all("no job generated yet" in g["blockers"] for g in ov["genres"])
        assert c.post("/api/collection/export", json={}).json()["detail"]["code"] == "collection_not_ready"

        ids = {}
        for genre in ("Science Fiction", "Detective"):
            ids[genre], _ = job(c, genre=genre)
            c.post(f"/api/jobs/{ids[genre]}/variants/A/approve")
        ov = c.get("/api/collection/overview").json()
        assert not ov["final_ready"] and [g["ready"] for g in ov["genres"]] == [True, True, False]
        r = c.post("/api/collection/export", json={})
        assert r.status_code == 400 and "Fantasy" in r.json()["detail"]["message"]
        part = c.post("/api/collection/export", json={"allow_partial": True})
        assert part.status_code == 200 and part.json()["partial"] is True

        ids["Fantasy"], _ = job(c, genre="Fantasy")
        assert c.get("/api/collection/overview").json()["final_ready"] is False  # not approved yet
        c.post(f"/api/jobs/{ids['Fantasy']}/variants/A/approve")
        ov = c.get("/api/collection/overview").json()
        assert ov["final_ready"] and ov["blockers"] == [] and all(g["generated"] == 10 for g in ov["genres"])

        exp = c.post("/api/collection/export", json={}).json()
        assert exp["kind"] == "collection" and not exp["partial"] and len(exp["sets"]) == 3
        z, resp = zipfile_of(c, exp)
        top = {n.split("/")[0] for n in z.namelist() if "/" in n}
        assert top == {"01_science-fiction", "02_detective", "03_fantasy"}
        for folder in top:
            assert len([n for n in z.namelist() if n.startswith(f"{folder}/artwork/")]) == 10
            assert json.loads(z.read(f"{folder}/manifest.json"))["approved"] is True
        cm = json.loads(z.read("collection-manifest.json"))
        assert cm["final_ready"] is True and len(cm["sets"]) == 3 and cm["missing_or_blocked"] == []
        assert "final-bundle.zip" in resp.headers["content-disposition"]


def test_mock_sets_never_count_as_final_ready(big):
    with TestClient(create_app(big)) as c:
        for genre in ("Science Fiction", "Detective", "Fantasy"):
            j, _ = job(c, genre=genre, confirmed=False, **MOCK)
            c.post(f"/api/jobs/{j}/variants/A/approve")
        ov = c.get("/api/collection/overview").json()
        assert not ov["final_ready"] and all("contains mock images" in g["blockers"] for g in ov["genres"])
        assert c.post("/api/collection/export", json={}).status_code == 400
        assert c.post("/api/collection/export", json={"allow_mock": True}).status_code == 200


# --------------------------------------------------------------------------------- batch create
def test_batch_creates_one_job_per_genre_under_one_confirmation(big, rec):
    with TestClient(create_app(big, gemini_transport=rec.transport)) as c:
        req = dict(genres=["Science Fiction", "Detective"], number_of_variants=1, scope="pilot", **GEMINI)
        r = c.post("/api/collection/batch", json={**req, "confirmed": False})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "confirmation_required"
        assert r.json()["detail"]["images"] == 8 and c.get("/api/jobs").json() == [] and rec.requests == []
        r = c.post("/api/collection/batch", json={**req, "confirmed": True})
        assert r.status_code == 200 and [j["genre"] for j in r.json()] == ["Science Fiction", "Detective"]
        for j in r.json():
            assert wait(c, j["job_id"])["counts"]["done"] == 4
        assert len(rec.requests) == 8


def test_batch_respects_caps_and_validation(settings, rec):
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        three = dict(genres=["Science Fiction", "Detective", "Fantasy"], number_of_variants=1, scope="pilot", confirmed=True, **GEMINI)
        r = c.post("/api/collection/batch", json=three)  # 12 images = $0.804 > per-run cap $0.75
        assert r.status_code == 402 and r.json()["detail"]["code"] == "job_cap"
        assert c.get("/api/jobs").json() == [] and rec.requests == []
        assert c.post("/api/collection/batch", json={**three, "genres": ["Detective", "detective"]}).status_code == 400
        assert c.post("/api/collection/batch", json={**three, "genres": []}).status_code == 400
        free = c.post("/api/collection/batch", json=dict(genres=["Science Fiction", "Detective", "Fantasy"], number_of_variants=1,
                                                         scope="pilot", confirmed=False, **MOCK))
        assert free.status_code == 200 and len(free.json()) == 3


# ----------------------------------------------------------------------------------- imports
def test_import_sample_is_labelled_honestly(settings):
    with TestClient(create_app(settings)) as c:
        files = {"file": ("sample.png", art_png(400, 3), "image/png")}
        r = c.post("/api/imports", files=files, data={"provider_label": "Z-Image", "model_label": "turbo", "prompt_note": "manual run", "object_name": "Helmet"})
        assert r.status_code == 200
        m = r.json()
        assert m["origin"] == "Imported · Z-Image turbo" and m["is_imported"] and not m["is_mock"] and m["cost"]["estimated_usd"] == 0
        assert m["provider"]["id"] == "imported" and m["provider"]["model_status"] == "manual_import" and m["card"]["object_name"] == "Helmet"
        assert c.get(m["image"]["url"]).content[:4] == b"\x89PNG"
        assert c.get("/api/artifacts").json()[0]["artifact_id"] == m["artifact_id"]
        assert c.post("/api/imports", files=files, data={"provider_label": "", "model_label": "x"}).status_code in (400, 422)
        bad = c.post("/api/imports", files={"file": ("x.png", b"not an image", "image/png")}, data={"provider_label": "a", "model_label": "b"})
        assert bad.status_code == 400 and bad.json()["detail"]["code"] == "not_an_image"
        assert c.post("/api/imports", files=files).status_code == 422  # labels are mandatory
        assert c.get("/api/models").json()["budget"]["spent_estimated_usd"] == 0
    with TestClient(create_app(settings)) as c2:  # an imported image must never mark a model "live in app"
        nano = next(x for x in c2.get("/api/models").json()["models"] if x["model_id"] == "gemini-nano-banana-2.1")
        assert nano["status"] == "external_rest_verified"
