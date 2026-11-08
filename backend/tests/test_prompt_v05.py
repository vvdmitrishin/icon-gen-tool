"""Prompt System v0.5 is the permanent production default: saved once as new versions, v1 kept for rollback, 5,000-char limit,
used by every new generation. Offline: Mock images and the temporary databases only."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.db import Db
from app.services.prompt_compiler import SESSION_EDITABLE, default_templates
from app.services.prompt_library import MAX_TEXT, V05_AUTHOR, LibraryError, PromptLibrary, v05_templates
from tests.test_free_text_sets import MOCK, wait

SEVEN = tuple(SESSION_EDITABLE)


def test_seven_v05_templates_are_present_verbatim_and_fit_the_limit():
    t = v05_templates()
    assert tuple(t) == SEVEN and MAX_TEXT == 5000
    assert t["system_style"].startswith("Create ONE original square illustration for a premium casual mobile-game collectible.")
    assert t["category_4"].startswith("CATEGORY 4 — LARGE NAMED HERO INSIDE A READABLE ENVIRONMENT.")
    assert t["category_5"].startswith("CATEGORY 5 — GOLDEN CHARACTER MINI-STORY.")
    assert t["image_closing"].startswith("FINAL QUALITY CHECK:")
    assert all(0 < len(v) <= MAX_TEXT for v in t.values())


def test_activation_keeps_v1_for_rollback_and_leaves_repair_alone(tmp_path):
    lib = PromptLibrary(Db(tmp_path / "x.sqlite3"))
    act = lib.active()
    for n in SEVEN:
        assert act[n]["version"] == 2 and act[n]["author"] == V05_AUTHOR and act[n]["text"] == v05_templates()[n]
        v1 = next(v for v in lib.versions(n) if v["version"] == 1)
        assert v1["text"] == default_templates()[n] and v1["author"] == "system"  # the old text is preserved
    assert act["repair"]["version"] == 1
    assert {i["name"]: i["is_default_text"] for i in lib.listing()} == {n: True for n in (*SEVEN, "repair")}  # shipped defaults, not "(edited)"


def test_activation_is_idempotent_and_never_overrides_producer_edits(tmp_path):
    p = tmp_path / "x.sqlite3"
    PromptLibrary(Db(p))
    lib = PromptLibrary(Db(p))  # a restart: nothing is saved twice
    assert all([v["version"] for v in lib.versions(n)] == [2, 1] for n in SEVEN)
    lib.restore("category_2", 1)  # producer rolls one template back to v1
    lib.save("category_3", "My own category 3 text.", "VD")
    lib2 = PromptLibrary(Db(p))
    a = lib2.active()
    assert a["category_2"]["text"] == default_templates()["category_2"]  # the rollback sticks across restarts
    assert a["category_3"]["text"] == "My own category 3 text." and a["category_3"]["version"] == 3
    assert [v["version"] for v in lib2.versions("category_2")] == [3, 2, 1]


def test_existing_database_with_producer_edit_is_not_overridden(tmp_path):
    db = Db(tmp_path / "old.sqlite3")
    lib = PromptLibrary.__new__(PromptLibrary)  # an old install: v1 seeded, producer edited one template, v0.5 not yet applied
    lib.db = db
    from app.services.prompt_library import _SCHEMA
    db.script(_SCHEMA)
    lib.seed()
    lib.save("category_1", "Producer's own category 1.", "VD")
    PromptLibrary(db)
    a = PromptLibrary(db).active()
    assert a["category_1"]["text"] == "Producer's own category 1." and a["category_1"]["version"] == 2
    assert a["category_2"]["version"] == 2 and a["category_2"]["author"] == V05_AUTHOR


def test_limit_is_5000_for_save_and_session_overrides(settings):
    with TestClient(create_app(settings)) as c:
        assert c.post("/api/prompts", json={"name": "system_style", "text": "x" * 5000, "note": "limit"}).status_code == 200
        r = c.post("/api/prompts", json={"name": "system_style", "text": "x" * 5001})
        assert r.status_code == 422 and "5000" in r.text
        assert c.post("/api/prompts/session-check", json={"overrides": {"category_1": "y" * 5000}}).status_code == 200
        bad = c.post("/api/prompts/session-check", json={"overrides": {"category_1": "y" * 5001}})
        assert bad.status_code == 422 and "5000" in bad.text
        assert c.get("/api/prompts").json()[0]["text"] == "x" * 5000  # saved intact, not truncated


def test_new_generation_uses_v05_exactly_once_and_old_snapshots_stay(settings):
    t = v05_templates()
    with TestClient(create_app(settings)) as c:
        for cat in (1, 2, 3, 4):
            r = c.post("/api/generate-card", json=dict(collection="Winter", genre="Forest", object_name="Pine Cone", category=cat, confirmed=False, **MOCK))
            assert r.status_code == 200, r.text
            p = r.json()["prompt"]
            eff = p["effective"]
            assert eff.count(t["system_style"]) == 1 and eff.count(t["image_closing"]) == 1
            assert [n for n in SEVEN if n.startswith("category_") and t[n] in eff] == [f"category_{cat}"]  # exactly one category module
            assert p["template_versions"][f"category_{cat}"] == 2 and p["template_versions"]["system_style"] == 2
            assert default_templates()["system_style"] not in eff  # the old v1 text is no longer used
        jr = c.post("/api/jobs", json=dict(collection="Winter", genre="Forest", number_of_cards=10, gold_cards=0, number_of_variants=1, confirmed=True, **MOCK))
        d = wait(c, jr.json()["job_id"])
        arts = [k["artifact_id"] for v in d["variants"] for k in v["cards"] if k["artifact_id"]]
        meta = c.get(f"/api/artifacts/{arts[0]}").json()["prompt"]
        assert meta["bundle"]["system_style"]["source"] == "library" and meta["bundle"]["system_style"]["version"] == 2
        # rolling back to v1 affects only NEW generations; the finished job keeps its own prompt snapshot
        assert c.post("/api/prompts/system_style/restore", json={"version": 1}).status_code == 200
        again = c.get(f"/api/artifacts/{arts[0]}").json()["prompt"]
        assert again["effective"] == meta["effective"]
        new = c.post("/api/generate-card", json=dict(collection="Winter", genre="Forest", object_name="Pine Cone", category=1, confirmed=False, **MOCK)).json()["prompt"]
        assert default_templates()["system_style"] in new["effective"]
