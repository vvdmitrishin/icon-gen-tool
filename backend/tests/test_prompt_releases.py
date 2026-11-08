"""Prompt System releases (v0.5 -> v0.6) are the permanent production defaults: saved once as new versions, older versions kept for
rollback, 5,000-char limit, used by every new generation on Gemini and Cloudflare. Offline: Mock images / mocked transports only."""
import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.config import Settings
from app.services.db import Db
from app.services.prompt_compiler import SESSION_EDITABLE, default_templates
from app.services.prompt_library import (
    MAX_TEXT, V05_AUTHOR, V06_AUTHOR, _SCHEMA, PromptLibrary, v05_templates, v06_templates)
from tests.conftest import FAKE_KEY, Recorder
from tests.test_free_text_sets import MOCK, wait

SEVEN = tuple(SESSION_EDITABLE)


def test_seven_v06_templates_are_present_verbatim_and_fit_the_limit():
    t = v06_templates()
    assert tuple(t) == SEVEN and MAX_TEXT == 5000
    assert t["system_style"].startswith("STYLE CORE — CASUAL MOBILE COLLECTIBLE ART.")
    assert t["category_1"].startswith("CATEGORY 1 — SINGLE COLLECTIBLE OBJECT, FLOATING.")
    assert t["category_4"].startswith("CATEGORY 4 — MAIN COLLECTIBLE IN A RECOGNIZABLE STYLIZED ENVIRONMENT.")
    assert t["category_5"].startswith("CATEGORY 5 — GOLDEN CARD, EXPRESSIVE CHARACTER MOMENT.")
    assert t["image_closing"].startswith("FINAL CHECKS.") and t["image_closing"].endswith("one final artwork image for the card.")
    assert all(0 < len(v) <= MAX_TEXT for v in t.values())


def test_fresh_library_is_v3_with_v1_and_v2_kept_for_rollback(tmp_path):
    lib = PromptLibrary(Db(tmp_path / "x.sqlite3"))
    act = lib.active()
    for n in SEVEN:
        assert act[n]["version"] == 3 and act[n]["author"] == V06_AUTHOR and act[n]["text"] == v06_templates()[n]
        vs = {v["version"]: v for v in lib.versions(n)}
        assert sorted(vs) == [1, 2, 3]
        assert vs[1]["text"] == default_templates()[n] and vs[1]["author"] == "system"
        assert vs[2]["text"] == v05_templates()[n] and vs[2]["author"] == V05_AUTHOR
    assert act["repair"]["version"] == 1
    assert all(i["is_default_text"] for i in lib.listing())  # shipped defaults, not "(edited)"


def test_existing_v2_database_is_upgraded_once_to_v3(tmp_path):
    db = Db(tmp_path / "old.sqlite3")  # the state of a live install: v1 + v0.5 (v2) active
    db.script(_SCHEMA)
    lib = PromptLibrary.__new__(PromptLibrary)
    lib.db = db
    lib.seed()
    for n, t in v05_templates().items():
        lib.save(n, t, V05_AUTHOR, "v0.5")
    assert all(lib.active()[n]["version"] == 2 for n in SEVEN)
    PromptLibrary(db)
    PromptLibrary(db)  # restarts: nothing is saved twice
    for n in SEVEN:
        assert [v["version"] for v in PromptLibrary(db).versions(n)] == [3, 2, 1]
        assert PromptLibrary(db).active()[n]["text"] == v06_templates()[n]


def test_producer_edits_and_rollbacks_are_never_overridden(tmp_path):
    db = Db(tmp_path / "x.sqlite3")
    db.script(_SCHEMA)
    lib = PromptLibrary.__new__(PromptLibrary)
    lib.db = db
    lib.seed()
    for n, t in v05_templates().items():
        lib.save(n, t, V05_AUTHOR, "v0.5")
    lib.save("category_1", "Producer's own category 1.", "VD")  # edited on top of v0.5
    lib.restore("category_2", 1)  # deliberately rolled back to v1
    a = PromptLibrary(db).active()
    assert a["category_1"]["text"] == "Producer's own category 1." and a["category_1"]["version"] == 3
    assert a["category_2"]["text"] == default_templates()["category_2"] and a["category_2"]["version"] == 3
    assert a["category_3"]["version"] == 3 and a["category_3"]["author"] == V06_AUTHOR
    again = PromptLibrary(db).active()  # and a later restart changes nothing
    assert {n: again[n]["version"] for n in SEVEN} == {n: a[n]["version"] for n in SEVEN}


def test_templates_stay_editable_and_old_versions_restorable(settings):
    with TestClient(create_app(settings)) as c:
        items = {i["name"]: i for i in c.get("/api/prompts").json()}
        assert all(items[n]["version"] == 3 and items[n]["versions"] == [3, 2, 1] for n in SEVEN)
        r = c.post("/api/prompts", json={"name": "category_3", "text": "My tweak.", "author": "VD", "note": "tweak"})
        assert r.status_code == 200 and r.json()["version"] == 4
        r = c.post("/api/prompts/category_3/restore", json={"version": 2})  # back to v0.5
        assert r.status_code == 200 and r.json()["text"] == v05_templates()["category_3"]
        r = c.post("/api/prompts/category_3/restore", json={"version": 3})  # forward to v0.6
        assert r.json()["text"] == v06_templates()["category_3"]
        assert c.post("/api/prompts", json={"name": "system_style", "text": "x" * 5000}).status_code == 200
        assert c.post("/api/prompts", json={"name": "system_style", "text": "x" * 5001}).status_code == 422
        assert c.post("/api/prompts/session-check", json={"overrides": {"category_1": "y" * 5001}}).status_code == 422


def test_new_generation_uses_v06_exactly_once_and_old_snapshots_stay(settings):
    t = v06_templates()
    with TestClient(create_app(settings)) as c:
        for cat in (1, 2, 3, 4):
            r = c.post("/api/generate-card", json=dict(collection="Winter", genre="Forest", object_name="Pine Cone", category=cat, confirmed=False, **MOCK))
            assert r.status_code == 200, r.text
            p = r.json()["prompt"]
            eff = p["effective"]
            assert eff.count(t["system_style"]) == 1 and eff.count(t["image_closing"]) == 1
            assert [n for n in SEVEN if n.startswith("category_") and t[n] in eff] == [f"category_{cat}"]  # exactly one category module
            assert p["template_versions"][f"category_{cat}"] == 3 and p["template_versions"]["system_style"] == 3
            assert v05_templates()["system_style"] not in eff and default_templates()["system_style"] not in eff
        jr = c.post("/api/jobs", json=dict(collection="Winter", genre="Forest", number_of_cards=10, gold_cards=1, number_of_variants=1, confirmed=True, **MOCK))
        d = wait(c, jr.json()["job_id"])
        cards = [k for v in d["variants"] for k in v["cards"] if k["artifact_id"]]
        gold = next(k for k in cards if k["category"] == 5)
        meta = c.get(f"/api/artifacts/{gold['artifact_id']}").json()["prompt"]
        assert t["category_5"] in meta["effective"] and meta["effective"].count(t["system_style"]) == 1
        assert meta["bundle"]["system_style"]["source"] == "library" and meta["bundle"]["system_style"]["version"] == 3
        assert c.post("/api/prompts/system_style/restore", json={"version": 1}).status_code == 200  # rollback affects NEW generations only
        assert c.get(f"/api/artifacts/{gold['artifact_id']}").json()["prompt"]["effective"] == meta["effective"]
        new = c.post("/api/generate-card", json=dict(collection="Winter", genre="Forest", object_name="Pine Cone", category=1, confirmed=False, **MOCK)).json()["prompt"]
        assert default_templates()["system_style"] in new["effective"]


def test_gemini_and_cloudflare_requests_carry_the_active_v06_prompt(tmp_path):
    from tests.test_cloudflare_models import KLEIN_ID, Cf, settings as cf_settings

    t = v06_templates()
    gem, cf = Recorder(), Cf()
    s = cf_settings(tmp_path)
    with TestClient(create_app(s, gemini_transport=gem.transport, cloudflare_transport=cf.transport)) as c:
        base = dict(collection="Winter", genre="Forest", object_name="Pine Cone", object_description="a snowy pine cone", category=3, confirmed=True)
        g = c.post("/api/generate-card", json=dict(base, provider="gemini", model="gemini-nano-banana-2.1"))
        k = c.post("/api/generate-card", json=dict(base, provider="cloudflare", model=KLEIN_ID))
        assert g.status_code == 200 and k.status_code == 200, (g.text, k.text)
        sent_gemini = gem.last_json()["contents"][0]["parts"][0]["text"]
        sent_cf = cf.requests[0].content.decode("utf-8", "ignore")
        for sent in (sent_gemini, sent_cf):  # the provider request itself, not only the stored provenance
            assert t["system_style"] in sent and t["image_closing"] in sent and t["category_3"] in sent
            assert not any(t[n] in sent for n in ("category_1", "category_2", "category_4", "category_5"))
        for resp in (g.json(), k.json()):
            assert resp["prompt"]["template_versions"]["system_style"] == 3 and resp["prompt"]["effective"].count(t["system_style"]) == 1
