import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.prompt_compiler import CardSpec, compile_card_prompt, default_templates
from tests.conftest import FAKE_KEY, art_png

MOCK = dict(provider="mock", model="mock-placeholder-v1")


def wait(c, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/jobs/{job_id}").json()
        if not d["active"]:
            return d
        time.sleep(0.03)
    raise AssertionError("job did not finish")


def mock_job(c, genre="Detective", scope="full"):
    r = c.post("/api/jobs", json=dict(genre=genre, number_of_variants=1, scope=scope, confirmed=False, **MOCK))
    assert r.status_code == 200, r.text
    return r.json()["job_id"], wait(c, r.json()["job_id"])


def card(cat=2):
    return CardSpec(collection="Movie Collection", genre="Detective", object_name="Magnifying Glass", category=cat)


@pytest.fixture
def c(settings):
    with TestClient(create_app(settings)) as client:
        yield client


# ------------------------------------------------------------------------- Prompt Library
def test_default_templates_reproduce_the_builtin_prompt_exactly():
    for cat in (1, 2, 3, 4):
        assert compile_card_prompt(card(cat), "retro", ["style"]).text == \
               compile_card_prompt(card(cat), "retro", ["style"], default_templates()).text


def test_library_is_seeded_with_version_one_defaults(c):
    items = c.get("/api/prompts").json()
    assert [i["name"] for i in items] == ["system_style", "category_1", "category_2", "category_3",
                                          "category_4", "image_closing", "repair"]
    assert all(i["version"] == 1 and i["is_default_text"] and i["versions"] == [1] for i in items)


def test_save_creates_new_active_version_and_restore_copies_forward(c):
    r = c.post("/api/prompts", json={"name": "category_1", "text": "Composition rule: A floating thing. Background rule: plain.",
                                     "author": "VD", "note": "simplify"})
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["author"] == "VD" and not r.json()["is_default_text"]
    assert [v["version"] for v in c.get("/api/prompts/category_1/versions").json()] == [2, 1]
    r = c.post("/api/prompts/category_1/restore", json={"version": 1})
    assert r.status_code == 200 and r.json()["version"] == 3 and r.json()["is_default_text"]
    assert r.json()["note"] == "restored from v1"
    assert c.post("/api/prompts/category_1/restore", json={"version": 99}).status_code == 404


def test_library_validation(c):
    assert c.post("/api/prompts", json={"name": "category_1", "text": "   "}).status_code == 422
    assert c.post("/api/prompts", json={"name": "repair", "text": "no placeholder here"}).status_code == 422
    assert c.post("/api/prompts", json={"name": "nope", "text": "x"}).status_code == 422
    assert c.post("/api/prompts", json={"name": "system_style", "text": "x" * 3001}).status_code == 422
    assert c.get("/api/prompts/nope/versions").status_code == 404
    assert [i["version"] for i in c.get("/api/prompts").json()] == [1] * 7  # nothing was saved


def test_edited_template_changes_generation_and_is_recorded_in_provenance(c):
    c.post("/api/prompts", json={"name": "category_1", "text": "Composition rule: ZZ-UNIQUE floating thing. Background rule: plain."})
    c.post("/api/prompts", json={"name": "image_closing", "text": "Keep it clean. QQ-CLOSING."})
    r = c.post("/api/generate-card", json=dict(genre="Fantasy", object_name="Wand", category=1, confirmed=False, **MOCK))
    assert r.status_code == 200
    p = r.json()["prompt"]
    assert "ZZ-UNIQUE" in p["effective"] and "QQ-CLOSING" in p["effective"]
    assert p["template_versions"]["category_1"] == 2 and p["template_versions"]["image_closing"] == 2
    assert p["template_versions"]["category_2"] == 1
    # the preview endpoint uses the same active templates
    pv = c.post("/api/prompt/preview", json=dict(genre="Fantasy", object_name="Wand", category=1, confirmed=False, **MOCK)).json()
    assert "ZZ-UNIQUE" in pv["effective_prompt"]


def test_custom_repair_template_and_malformed_template_fallback():
    t = compile_card_prompt(card(), "", None, {"repair": "FIX: {note} on {object_name}"}, "less shadow").text
    assert "FIX: less shadow on Magnifying Glass" in t
    bad = compile_card_prompt(card(), "", None, {"repair": "oops {nope} {note}"}, "less shadow").text
    assert "Regenerate the SAME concept" in bad  # malformed edit must not break generation


# ------------------------------------------------------------------------------- Templates
def test_templates_builtin_user_create_duplicate_and_delete(c):
    items = c.get("/api/templates").json()
    builtin = [i for i in items if i["kind"] == "builtin"]
    assert len(builtin) == 4 and {"Movie · Detective pilot"} <= {i["name"] for i in builtin}
    r = c.post("/api/templates", json={"name": "My sci-fi trio", "payload": {
        "genre": "Science Fiction", "number_of_variants": 3, "scope": "pilot",
        "category_distribution": {"mode": "custom", "counts": {"1": 3, "2": 2, "3": 3, "4": 2}}, "wishes": "retro"}})
    assert r.status_code == 200 and r.json()["kind"] == "user"
    assert c.post("/api/templates", json={"name": "My sci-fi trio", "payload": {"genre": "Fantasy"}}).status_code == 409
    assert c.delete(f"/api/templates/{r.json()['id']}").json() == {"deleted": True}
    assert c.delete(f"/api/templates/{builtin[0]['id']}").status_code == 409  # built-ins are protected
    assert c.delete("/api/templates/99999").json() == {"deleted": False}


def test_template_payload_validation_rejects_secrets_and_junk(c):
    bad = [{"api_key": "x"}, {"genre": "Fantasy", "gemini_api_key": "x"}, {"number_of_variants": 9},
           {"scope": "huge"}, {"wishes": "x" * 501}, {"category_distribution": {"mode": "weird"}}]
    for payload in bad:
        assert c.post("/api/templates", json={"name": "t", "payload": payload}).status_code == 422, payload
    assert c.post("/api/templates", json={"name": "", "payload": {}}).status_code == 422


# -------------------------------------------------------------------------------- Settings
def test_settings_never_expose_secrets(settings):
    from dataclasses import replace
    s = replace(settings, cloudflare_api_token="cf" "ut_SECRETSECRETSECRET12345", openai_api_key="s" "k-SECRETSECRETSECRET12345")
    with TestClient(create_app(s)) as c:
        r = c.get("/api/settings")
        text = r.text
        assert FAKE_KEY not in text and "SECRETSECRET" not in text
        d = r.json()
        assert d["providers"]["gemini"]["key_configured"] is True and d["providers"]["cloudflare"]["token_set"] is True
        assert d["providers"]["cloudflare"]["configured"] is False  # account id missing
        assert d["providers"]["openai"] == {"enabled": False, "paid_calls_enabled": False, "key_configured": True}
        assert d["limits"]["max_card_retries"] == 0 and d["limits"]["max_regenerations_per_card"] == 2
        assert d["storage"]["artifacts"].endswith("artifacts") and ":" not in d["storage"]["database"]
        assert d["spec_version"] == "1.7" and "never shown" in d["how_to_change"]


# ------------------------------------------------------------------------ Reference Compare
def upload(c, ref_type, seed=1):
    r = c.post("/api/references", files={"file": (f"r{seed}.png", art_png(300, seed), "image/png")}, data={"ref_type": ref_type})
    assert r.status_code == 200, r.text
    return r.json()["ref_id"]


def test_comparison_auto_match_manual_override_and_fallback(c):
    job_id, d = mock_job(c)
    cards = d["variants"][0]["cards"]
    cat1 = next(k for k in cards if k["category"] == 1)
    cat3 = next(k for k in cards if k["category"] == 3)

    empty = c.get(f"/api/jobs/{job_id}/cards/{cat1['card_id']}/comparison").json()
    assert empty["reference"] is None and "upload one" in empty["message"] and empty["image_url"]

    r1, rs = upload(c, "category_1", 1), upload(c, "style", 2)
    neg = upload(c, "negative", 3)
    a = c.get(f"/api/jobs/{job_id}/cards/{cat1['card_id']}/comparison").json()
    assert a["reference"]["ref_id"] == r1 and a["reference"]["mode"] == "auto"      # category-typed ref first
    b = c.get(f"/api/jobs/{job_id}/cards/{cat3['card_id']}/comparison").json()
    assert b["reference"]["ref_id"] == rs                                            # falls back to style, never 'negative'
    assert {x["ref_id"] for x in a["candidates"]} == {r1, rs, neg} and [x["suggested"] for x in a["candidates"] if x["ref_id"] == r1] == [True]

    r = c.patch(f"/api/jobs/{job_id}/cards/{cat3['card_id']}/comparison-reference", json={"ref_id": r1})
    assert r.status_code == 200 and r.json()["reference"] == {**r.json()["reference"], "ref_id": r1, "mode": "manual"}
    assert c.patch(f"/api/jobs/{job_id}/cards/{cat3['card_id']}/comparison-reference", json={"ref_id": "ref-0000000000"}).status_code == 404

    grid = c.get(f"/api/jobs/{job_id}/comparison").json()
    assert len(grid["pairs"]) == 10 and grid["reference_count"] == 3
    assert next(p for p in grid["pairs"] if p["card_id"] == cat3["card_id"])["reference"]["mode"] == "manual"

    assert c.delete(f"/api/references/{r1}").json()["deleted"]                        # deleted ref -> auto fallback
    after = c.get(f"/api/jobs/{job_id}/cards/{cat3['card_id']}/comparison").json()
    assert after["reference"]["ref_id"] == rs and after["reference"]["mode"] == "auto"

    r = c.patch(f"/api/jobs/{job_id}/cards/{cat3['card_id']}/comparison-reference", json={"ref_id": None})
    assert r.json()["reference"]["mode"] == "auto"
    assert c.get(f"/api/jobs/{job_id}/cards/nope/comparison").status_code == 404
    assert c.get("/api/jobs/job-nope/comparison").status_code == 404


def test_manual_match_survives_restart(settings):
    with TestClient(create_app(settings)) as c:
        job_id, d = mock_job(c)
        k = d["variants"][0]["cards"][0]
        upload(c, "style", 1)
        ref = upload(c, "composition", 2)
        c.patch(f"/api/jobs/{job_id}/cards/{k['card_id']}/comparison-reference", json={"ref_id": ref})
    with TestClient(create_app(settings)) as c2:  # "refresh" / restart
        got = c2.get(f"/api/jobs/{job_id}/cards/{k['card_id']}/comparison").json()
        assert got["reference"]["ref_id"] == ref and got["reference"]["mode"] == "manual"


def test_comparison_lists_versions_after_regeneration(c):
    job_id, d = mock_job(c)
    k = d["variants"][0]["cards"][0]
    assert c.post(f"/api/jobs/{job_id}/cards/{k['card_id']}/regenerate", json={"confirmed": False}).status_code == 200
    wait(c, job_id)
    cmp = c.get(f"/api/jobs/{job_id}/cards/{k['card_id']}/comparison").json()
    assert [v["version"] for v in cmp["versions"]] == [2, 1] and cmp["versions"][0]["is_current"]
