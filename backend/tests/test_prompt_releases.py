"""Prompt System releases (v0.5 -> v0.6) are the permanent production defaults: saved once as new versions, older versions kept for
rollback, 5,000-char limit, used by every new generation on Gemini and Cloudflare. Offline: Mock images / mocked transports only."""
import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.config import Settings
from app.services.db import Db
from app.services.prompt_compiler import SESSION_EDITABLE, default_templates
from app.services.prompt_library import (
    MAX_TEXT, V05_AUTHOR, V06_AUTHOR, V07_AUTHOR, V08_AUTHOR, V09_AUTHOR, _SCHEMA, PromptLibrary, v05_templates, v06_templates, v07_templates, v08_templates,
    v09_templates)
from tests.conftest import FAKE_KEY, Recorder
from tests.test_free_text_sets import MOCK, wait

SEVEN = tuple(SESSION_EDITABLE)
CHANGED = ('system_style', 'category_3', 'category_4', 'image_closing')  # templates v0.7 changed (and v0.8 changes again)
V09 = ('category_3', 'category_4')  # the only templates v0.9 changes
V08 = ('system_style', 'category_1', 'category_2', 'category_3', 'category_4', 'image_closing')  # the only templates v0.8 changes


def active_text(n):
    return v09_templates()[n] if n in V09 else v08_templates()[n] if n in V08 else v06_templates()[n]


def active_version(n):
    return 6 if n in V09 else 5 if n in CHANGED else 4 if n in V08 else 3  # category_5 stays at v0.6 (v3)


def test_seven_v06_templates_are_present_verbatim_and_fit_the_limit():
    t = v06_templates()
    assert tuple(t) == SEVEN and MAX_TEXT == 5000
    assert t["system_style"].startswith("STYLE CORE — CASUAL MOBILE COLLECTIBLE ART.")
    assert t["category_1"].startswith("CATEGORY 1 — SINGLE COLLECTIBLE OBJECT, FLOATING.")
    assert t["category_4"].startswith("CATEGORY 4 — MAIN COLLECTIBLE IN A RECOGNIZABLE STYLIZED ENVIRONMENT.")
    assert t["category_5"].startswith("CATEGORY 5 — GOLDEN CARD, EXPRESSIVE CHARACTER MOMENT.")
    assert t["image_closing"].startswith("FINAL CHECKS.") and t["image_closing"].endswith("one final artwork image for the card.")
    assert all(0 < len(v) <= MAX_TEXT for v in t.values())


def test_fresh_library_has_v07_on_changed_templates_and_all_older_versions_kept(tmp_path):
    lib = PromptLibrary(Db(tmp_path / "x.sqlite3"))
    act = lib.active()
    for n in SEVEN:
        vs = {v["version"]: v for v in lib.versions(n)}
        assert act[n]["version"] == active_version(n) and act[n]["text"] == active_text(n)
        assert sorted(vs) == list(range(1, active_version(n) + 1))
        assert act[n]["author"] == (V09_AUTHOR if n in V09 else V08_AUTHOR if n in V08 else V06_AUTHOR)
        assert vs[1]["text"] == default_templates()[n] and vs[1]["author"] == "system"
        assert vs[2]["text"] == v05_templates()[n] and vs[2]["author"] == V05_AUTHOR
        assert vs[3]["text"] == v06_templates()[n] and vs[3]["author"] == V06_AUTHOR  # v0.6 stays restorable
        if n in CHANGED:
            assert vs[4]["text"] == v07_templates()[n] and vs[4]["author"] == V07_AUTHOR  # v0.7 stays restorable
            assert vs[5]["text"] == v08_templates()[n]
            if n in V09:
                assert vs[6]["text"] == v09_templates()[n] and vs[6]["author"] == V09_AUTHOR  # v0.9 = the active version
        elif n in V08:  # category_1 / category_2 were never changed by v0.7: v0.8 is their v4
            assert vs[4]["text"] == v08_templates()[n]
    assert act["repair"]["version"] == 1
    assert all(i["is_default_text"] for i in lib.listing())  # shipped defaults, not "(edited)"


def test_existing_v2_database_is_upgraded_once_to_v0_6_then_v0_7(tmp_path):
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
        assert [v["version"] for v in PromptLibrary(db).versions(n)] == list(range(active_version(n), 0, -1))
        assert PromptLibrary(db).active()[n]["text"] == active_text(n)


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
    assert a["category_3"]["version"] == 6 and a["category_3"]["author"] == V09_AUTHOR  # untouched templates move on through v0.7 and v0.8 to v0.9
    assert a["category_1"]["version"] == 3 and a["category_2"]["version"] == 3 and a["category_5"]["version"] == 3  # edited / rolled back / unchanged
    again = PromptLibrary(db).active()  # and a later restart changes nothing
    assert {n: again[n]["version"] for n in SEVEN} == {n: a[n]["version"] for n in SEVEN}


def test_templates_stay_editable_and_old_versions_restorable(settings):
    with TestClient(create_app(settings)) as c:
        items = {i["name"]: i for i in c.get("/api/prompts").json()}
        assert all(items[n]["version"] == active_version(n) for n in SEVEN)
        assert items["category_3"]["versions"] == [6, 5, 4, 3, 2, 1]
        r = c.post("/api/prompts", json={"name": "category_3", "text": "My tweak.", "author": "VD", "note": "tweak"})
        assert r.status_code == 200 and r.json()["version"] == 7
        r = c.post("/api/prompts/category_3/restore", json={"version": 2})  # back to v0.5
        assert r.status_code == 200 and r.json()["text"] == v05_templates()["category_3"]
        r = c.post("/api/prompts/category_3/restore", json={"version": 3})  # back to v0.6
        assert r.json()["text"] == v06_templates()["category_3"]
        r = c.post("/api/prompts/category_3/restore", json={"version": 4})  # forward to v0.7
        assert r.json()["text"] == v07_templates()["category_3"]
        r = c.post("/api/prompts/category_3/restore", json={"version": 5})  # forward to v0.8
        assert r.json()["text"] == v08_templates()["category_3"]
        r = c.post("/api/prompts/category_3/restore", json={"version": 6})  # forward to v0.9
        assert r.json()["text"] == v09_templates()["category_3"]
        assert c.post("/api/prompts", json={"name": "system_style", "text": "x" * 5000}).status_code == 200
        assert c.post("/api/prompts", json={"name": "system_style", "text": "x" * 5001}).status_code == 422
        assert c.post("/api/prompts/session-check", json={"overrides": {"category_1": "y" * 5001}}).status_code == 422


def test_new_generation_uses_v08_exactly_once_and_old_snapshots_stay(settings):
    t = {n: active_text(n) for n in SEVEN}
    with TestClient(create_app(settings)) as c:
        for cat in (1, 2, 3, 4):
            r = c.post("/api/generate-card", json=dict(collection="Winter", genre="Forest", object_name="Pine Cone", category=cat, confirmed=False, **MOCK))
            assert r.status_code == 200, r.text
            p = r.json()["prompt"]
            eff = p["effective"]
            assert eff.count(t["system_style"]) == 1 and eff.count(t["image_closing"]) == 1
            assert [n for n in SEVEN if n.startswith("category_") and t[n] in eff] == [f"category_{cat}"]  # exactly one category module
            assert p["template_versions"][f"category_{cat}"] == active_version(f"category_{cat}") and p["template_versions"]["system_style"] == 5
            assert v07_templates()["system_style"] not in eff and v06_templates()["system_style"] not in eff and v05_templates()["system_style"] not in eff and default_templates()["system_style"] not in eff
        jr = c.post("/api/jobs", json=dict(collection="Winter", genre="Forest", number_of_cards=10, gold_cards=1, number_of_variants=1, confirmed=True, **MOCK))
        d = wait(c, jr.json()["job_id"])
        cards = [k for v in d["variants"] for k in v["cards"] if k["artifact_id"]]
        gold = next(k for k in cards if k["category"] == 5)
        meta = c.get(f"/api/artifacts/{gold['artifact_id']}").json()["prompt"]
        assert t["category_5"] in meta["effective"] and meta["effective"].count(t["system_style"]) == 1
        assert meta["bundle"]["system_style"]["source"] == "library" and meta["bundle"]["system_style"]["version"] == 5
        assert c.post("/api/prompts/system_style/restore", json={"version": 1}).status_code == 200  # rollback affects NEW generations only
        assert c.get(f"/api/artifacts/{gold['artifact_id']}").json()["prompt"]["effective"] == meta["effective"]
        new = c.post("/api/generate-card", json=dict(collection="Winter", genre="Forest", object_name="Pine Cone", category=1, confirmed=False, **MOCK)).json()["prompt"]
        assert default_templates()["system_style"] in new["effective"]


def test_gemini_and_cloudflare_requests_carry_the_active_v08_prompt(tmp_path):
    from tests.test_cloudflare_models import KLEIN_ID, Cf, settings as cf_settings

    t = {n: active_text(n) for n in SEVEN}
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
            assert resp["prompt"]["template_versions"]["system_style"] == 5 and resp["prompt"]["effective"].count(t["system_style"]) == 1


def test_v07_changes_exactly_four_templates_and_keeps_the_rules():
    t = v07_templates()
    assert tuple(t) == CHANGED and all(0 < len(v) <= MAX_TEXT for v in t.values())
    for n in ("category_1", "category_2", "category_5"):
        assert n not in t  # untouched
    assert "60–75%" in t["system_style"] and "readable text" in t["system_style"]
    assert "iconic" in t["category_3"] and "iconic" in t["category_4"]
    assert "No readable text of any kind" in t["image_closing"] and t["image_closing"].endswith("one final artwork image for the card.")
    assert t["category_4"].startswith("CATEGORY 4 —") and t["category_3"].startswith("CATEGORY 3 —")
    assert "If reference images are attached" in t["system_style"] and "authoritative visual specification" in t["system_style"]  # reference handling unchanged


def test_v08_installs_six_templates_verbatim_and_leaves_category_5_and_repair(tmp_path):
    t = v08_templates()
    assert tuple(t) == V08 and all(0 < len(v) <= MAX_TEXT for v in t.values())
    assert t["system_style"].startswith("VISUAL ART DIRECTION — COHESIVE CASUAL MOBILE-GAME COLLECTIBLES")
    assert t["category_2"].startswith("CATEGORY 2 — ONE OBJECT, HORIZONTAL SUPPORT + VERTICAL BACKDROP")
    assert t["category_3"].startswith("CATEGORY 3 — OBJECT ON A CONTEXTUALLY APPROPRIATE PHYSICAL SURFACE")
    assert t["image_closing"].startswith("FINAL ARTWORK COMPLIANCE CHECK") and t["image_closing"].endswith("a card mockup.")
    lib = PromptLibrary(Db(tmp_path / "x.sqlite3"))
    a = lib.active()
    assert all(a[n]["text"] == t[n] for n in V08 if n not in V09)  # character-for-character (category_3/4 have moved on to v0.9)
    assert a["category_5"]["text"] == v06_templates()["category_5"] and a["category_5"]["version"] == 3
    assert a["repair"]["version"] == 1 and a["repair"]["text"] == default_templates()["repair"]


def test_compiled_prompt_has_corrected_category_3_and_4_labels(settings):
    with TestClient(create_app(settings)) as c:
        for cat, label in ((3, "Collectible on a context-appropriate physical surface"), (4, "Dominant collectible inside a simplified stylized contextual environment")):
            eff = c.post("/api/generate-card", json=dict(collection="Summer", genre="Beach", object_name="Lifeguard Tower", category=cat, confirmed=False, **MOCK)).json()["prompt"]["effective"]
            assert f"Category {cat} ({label}" in eff and "realistic surface" not in eff and "realistic environment" not in eff
            assert eff.count(v08_templates()["system_style"]) == 1 and eff.count(v08_templates()["image_closing"]) == 1
            assert eff.count(active_text(f"category_{cat}")) == 1


def test_v09_separates_grounded_category_3_from_environment_category_4(tmp_path):
    from app.services.prompt_compiler import CardSpec, compile_card_prompt
    t = {n: active_text(n) for n in SEVEN}
    c3 = compile_card_prompt(CardSpec(collection="C", genre="G", object_name="Mug Tower", category=3), "", None, t).text
    c4 = compile_card_prompt(CardSpec(collection="C", genre="G", object_name="Bus", category=4), "", None, t).text
    assert "SIMPLE BACKGROUND" in c3 and "must not become Category 4" in c3 and "materially convincing" in c3
    assert "no wall-and-baseboard construction, doorways, windows, furniture" in c3 and "at most a few soft" not in c3.lower()
    assert "DISTINCTION FROM CATEGORY 3" in c4 and "integrates the subject into a recognizable setting" in c4
    assert "SIMPLE BACKGROUND" not in c4 and "DISTINCTION FROM CATEGORY 3" not in c3
    assert set(v09_templates()) == {"category_3", "category_4"} and all(len(v) <= MAX_TEXT for v in v09_templates().values())
    assert v09_templates()["category_3"] != v08_templates()["category_3"] and active_text("category_1") == v08_templates()["category_1"]
