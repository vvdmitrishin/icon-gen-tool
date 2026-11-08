"""Variable set size (1..10), golden cards (Cat.5), planner routing, AI TextPlanner (mocked transport) and
output-size delivery. No test here makes a live network call."""
import json
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.services.category_distribution import (
    CategoryDistribution,
    DistributionError,
    assign_slots,
    balanced_counts,
    resolve_distribution,
    validate_counts,
)
from app.services.planning import PlanningError, curated_genre_for, plan_set, validate_plan
from app.services.prompt_compiler import CardSpec, compile_card_prompt
from tests.conftest import FAKE_KEY

MOCK = dict(provider="mock", model="mock-placeholder-v1")


# ------------------------------------------------------------------------------------ distribution
@pytest.mark.parametrize("n,expected", [
    (0, [0, 0, 0, 0]), (1, [1, 0, 0, 0]), (2, [1, 1, 0, 0]), (3, [1, 1, 1, 0]), (4, [1, 1, 1, 1]),
    (10, [2, 2, 3, 3]),
])
def test_balanced_counts(n, expected):
    assert list(balanced_counts(n).values()) == expected


@pytest.mark.parametrize("n", range(0, 11))
def test_balanced_counts_always_sum_and_min_one_from_four(n):
    b = balanced_counts(n)
    assert sum(b.values()) == n
    if n >= 4:
        assert all(v >= 1 for v in b.values())


@pytest.mark.parametrize("cards,gold", [(1, 0), (3, 0), (10, 0), (10, 2), (5, 5), (10, 10), (6, 1)])
def test_resolve_distribution_totals(cards, gold):
    d = resolve_distribution(None, number_of_cards=cards, gold_cards=gold)
    assert sum(d.values()) == cards and d[5] == gold
    slots = assign_slots(d)
    assert len(slots) == cards and slots == sorted(slots)  # Cat.1..4 ascending, golden LAST
    assert slots.count(5) == gold


def test_auto_ten_with_two_gold():
    d = resolve_distribution(None, number_of_cards=10, gold_cards=2)
    assert d[5] == 2 and sum(d[c] for c in (1, 2, 3, 4)) == 8 and all(d[c] >= 1 for c in (1, 2, 3, 4))
    assert assign_slots(d)[-2:] == [5, 5]


@pytest.mark.parametrize("counts,cards,gold,code", [
    ({1: 2, 2: 2, 3: 2, 4: 2}, 10, 0, "bad_total"),
    ({1: 0, 2: 3, 3: 3, 4: 2}, 8, 0, "category_empty"),      # standard >= 4: every category needs a card
    ({1: 3, 2: 0, 3: 0, 4: 0}, 3, 0, None),                  # 1..3 standard cards: zeros are fine
    ({1: 1, 2: 1, 3: 1, 4: 1}, 5, 1, None),
    ({1: 2, 2: 0, 3: 0, 4: 0}, 3, 0, "bad_total"),
    ({}, 3, 3, None),                                        # all gold
    ({1: 1}, 3, 3, "bad_total"),
    ({1: 1}, 3, 4, "bad_gold_count"),                        # gold > cards
    ({1: 1}, 11, 0, "bad_number_of_cards"),
    ({7: 1}, 3, 2, "unknown_category"),
])
def test_validate_counts_rules(counts, cards, gold, code):
    if code is None:
        assert validate_counts(counts, number_of_cards=cards, gold_cards=gold)[5] == gold
    else:
        with pytest.raises(DistributionError) as e:
            validate_counts(counts, number_of_cards=cards, gold_cards=gold)
        assert e.value.code == code


def test_custom_ignores_cat5_key():
    d = resolve_distribution(CategoryDistribution(mode="custom", counts={1: 2, 2: 2, 3: 2, 4: 2, 5: 9}),
                             number_of_cards=10, gold_cards=2)
    assert d == {1: 2, 2: 2, 3: 2, 4: 2, 5: 2}


# ----------------------------------------------------------------------------------------- prompts
def test_gold_prompt_has_character_rules_and_no_lock_error():
    p = compile_card_prompt(CardSpec(collection="Movies", genre="Sci-Fi", object_name="Robot and Alien on a date",
                                     object_description="awkward soup", category=5))
    low = p.layers["B_settings"].lower()
    assert "max 2 characters" in low and "ironic" in low and "key characters" in low
    assert "no text" in p.text.lower()
    assert "Category 5" in p.layers["B_settings"]


def test_single_card_endpoint_still_refuses_cat5(settings):
    c = TestClient(create_app(settings))
    r = c.post("/api/generate-card", json=dict(genre="Sci-Fi", object_name="x", category=5, **MOCK))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_card"


# ------------------------------------------------------------------------------------ rules planner
@pytest.mark.parametrize("cards,gold", [(1, 0), (3, 0), (10, 0), (10, 2), (4, 4), (10, 10)])
def test_rules_planner_variable_size_and_gold(cards, gold):
    counts = resolve_distribution(None, number_of_cards=cards, gold_cards=gold)
    plan = plan_set("Detective", 1, counts)
    assert len(plan.variants[0].cards) == cards
    assert sum(1 for c in plan.variants[0].cards if c.category == 5) == gold
    assert validate_plan(plan, counts) == []
    assert [c.category for c in plan.variants[0].cards][-gold:] == [5] * gold if gold else True


def test_rules_planner_gold_unique_across_variants_and_pool_exhausted():
    counts = resolve_distribution(None, number_of_cards=10, gold_cards=4)
    plan = plan_set("Fantasy", 3, counts)
    names = [c.object_name for v in plan.variants for c in v.cards if c.category == 5]
    assert len(names) == 12 and len(set(names)) == 12
    assert validate_plan(plan, counts) == []
    all_gold = resolve_distribution(None, number_of_cards=10, gold_cards=10)
    with pytest.raises(PlanningError) as e:
        plan_set("Science Fiction", 2, all_gold)  # 20 gold scenes needed, 16 in the pool
    assert e.value.code == "pool_exhausted" and "golden" in e.value.message


def test_curated_routing_helper():
    assert curated_genre_for("Movie Collection", "Science Fiction") == "Science Fiction"
    assert curated_genre_for("Movies", "Sci-Fi") == "Science Fiction"
    assert curated_genre_for("Кіно", "noir") == "Detective"
    assert curated_genre_for("Cinema classics", "dark fantasy") == "Fantasy"
    assert curated_genre_for("Transport", "Sci-Fi") is None
    assert curated_genre_for("Movies", "Western") is None


# ------------------------------------------------------------------------------------- API plumbing
def wait(c, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/jobs/{job_id}").json()
        if not d["active"]:
            return d
        time.sleep(0.03)
    raise AssertionError("job did not finish")


def ai_plan_json(variants, counts, prefix="Item"):
    out = []
    for vi in range(variants):
        cards, n = [], 0
        for cat in (1, 2, 3, 4, 5):
            for _ in range(counts.get(cat, 0)):
                n += 1
                cards.append(dict(position=n, category=cat, object_name=f"{prefix} {vi}-{n}",
                                  object_description=f"a vivid {prefix} number {vi}-{n}"))
                if cat == 5:
                    cards[-1]["characters"] = 1 + n % 2  # golden cards carry 1 or 2 characters
        out.append(dict(title=f"Theme {vi}", concept=f"Concept {vi}", cards=cards))
    return {"variants": out}


class TextRecorder:
    """Gemini transport that answers the text-plan call and records every request."""

    def __init__(self, plan_body=None, text=None, status=200):
        self.requests: list[httpx.Request] = []
        self.plan_body, self.text, self.status = plan_body, text, status
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.requests.append(request)
        text = self.text if self.text is not None else json.dumps(self.plan_body)
        return httpx.Response(self.status, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def job_body(**kw):
    base = dict(collection="Transport", genre="Cars", number_of_variants=2, number_of_cards=6, gold_cards=1,
                allow_ai_plan=True, confirmed=True, **MOCK)
    base.update(kw)
    return base


def test_custom_theme_requires_explicit_ai_permission(settings):
    rec = TextRecorder(ai_plan_json(1, {}))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        for coll, st in (("Transport", "Cars"), ("Movies", "Western")):
            r = c.post("/api/jobs/preview", json=job_body(collection=coll, genre=st, allow_ai_plan=False))
            assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_required"
        assert rec.requests == []


def test_movie_scifi_uses_rules_planner_for_free(settings):
    rec = TextRecorder({})
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=job_body(collection="Movies", genre="Sci-Fi", allow_ai_plan=False,
                                                      number_of_cards=10, gold_cards=2, number_of_variants=1))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["planning_mode"] == "rules" and d["plan_cost_estimate_usd"] == 0
        assert d["number_of_cards"] == 10 and d["gold_cards"] == 2 and d["output_size"] == 1024
        assert d["images_total"] == 10 and d["distribution"]["5"] == 2
        assert rec.requests == []


def test_ai_plan_preview_and_create_make_one_call(settings):
    counts = resolve_distribution(None, number_of_cards=6, gold_cards=1)
    rec = TextRecorder(ai_plan_json(2, counts))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        p = c.post("/api/jobs/preview", json=job_body())
        assert p.status_code == 200, p.text
        d = p.json()
        assert d["planning_mode"] == "ai" and d["plan_cost_estimate_usd"] == 0.002
        assert d["images_total"] == 12 and len(d["plan"]["variants"]) == 2
        body = rec.requests[0].content.decode()
        req = json.loads(body)
        assert req["generationConfig"]["responseMimeType"] == "application/json"
        assert "responseSchema" in req["generationConfig"]
        assert "gemini-2.5-flash:generateContent" in str(rec.requests[0].url)
        assert FAKE_KEY not in body  # key travels in the header only
        r = c.post("/api/jobs", json=job_body())
        assert r.status_code == 200, r.text
        assert len(rec.requests) == 1  # preview + create reused the same plan
        j = wait(c, r.json()["job_id"])
        assert j["status"] == "ready_for_review" and j["counts"]["done"] == 12
        assert j["planning_mode"] == "ai" and j["number_of_cards"] == 6 and j["gold_cards"] == 1
        assert [x["category"] for x in j["variants"][0]["cards"]] == [1, 2, 3, 4, 4, 5] or j["variants"][0]["cards"][-1]["category"] == 5
        assert all(v["set_qa"]["total"] == 6 for v in j["variants"])
        sq = {ch["id"]: ch["status"] for ch in j["variants"][0]["set_qa"]["checks"]}
        assert sq["SQ-01"] == "pass" and sq["SQ-02"] == "pass" and sq["SQ-04"] == "pass"
        # honest cost accounting: exactly one text call in the ledger
        lines = [json.loads(x) for x in settings.ledger_path.read_text().splitlines() if x.strip()]
        assert [e["outcome"] for e in lines] == ["ok:plan"] and lines[0]["est_cost_usd"] == 0.002


def test_shuffle_seed_makes_a_new_call(settings):
    counts = resolve_distribution(None, number_of_cards=6, gold_cards=1)
    rec = TextRecorder(ai_plan_json(2, counts))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        assert c.post("/api/jobs/preview", json=job_body()).status_code == 200
        assert c.post("/api/jobs/preview", json=job_body(seed=7)).status_code == 200
        assert c.post("/api/jobs/preview", json=job_body(seed=7)).status_code == 200
        assert len(rec.requests) == 2


@pytest.mark.parametrize("text,code", [
    ("not json at all", "ai_plan_invalid"),
    (json.dumps({"variants": []}), "ai_plan_invalid"),
    (json.dumps({"nope": 1}), "ai_plan_invalid"),
])
def test_ai_plan_invalid_output(settings, text, code):
    rec = TextRecorder(text=text)
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=job_body())
        assert r.status_code == 422 and r.json()["detail"]["code"] == code
        assert len(rec.requests) == 1  # no automatic retry


def test_ai_plan_wrong_distribution_or_duplicates_rejected(settings):
    counts = resolve_distribution(None, number_of_cards=6, gold_cards=1)
    bad = ai_plan_json(2, counts)
    bad["variants"][0]["cards"][-1]["category"] = 1  # golden card missing, one extra Cat.1
    rec = TextRecorder(bad)
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=job_body())
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_invalid"
    dup = ai_plan_json(2, counts)
    dup["variants"][1]["cards"][0]["object_name"] = dup["variants"][0]["cards"][0]["object_name"]
    rec = TextRecorder(dup)
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=job_body())
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_invalid"


def test_ai_plan_without_key(settings):
    rec = TextRecorder({})
    with TestClient(create_app(replace(settings, gemini_api_key=""), gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=job_body())
        assert r.status_code == 400 and r.json()["detail"]["code"] == "gemini_key_missing"
        assert rec.requests == []


def test_ai_plan_http_error_is_clear(settings):
    rec = TextRecorder(text="{}", status=503)
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=job_body())
        assert r.status_code == 502 and r.json()["detail"]["code"] == "ai_plan_failed"
        assert FAKE_KEY not in r.text


def test_batch_with_three_curated_genres_and_new_fields(settings):
    with TestClient(create_app(settings)) as c:
        r = c.post("/api/collection/batch", json=dict(
            genres=["Science Fiction", "Detective", "Fantasy"], collection="Movie Collection", number_of_variants=1,
            number_of_cards=5, gold_cards=1, output_size=128, confirmed=False, **MOCK))
        assert r.status_code == 200, r.text
        jobs = r.json()
        assert len(jobs) == 3 and all(j["number_of_cards"] == 5 and j["gold_cards"] == 1 for j in jobs)
        for j in jobs:
            d = wait(c, j["job_id"])
            assert d["counts"]["done"] == 5 and d["output_size"] == 128 and d["planning_mode"] == "rules"


def test_job_detail_defaults_for_old_jobs(settings):
    with TestClient(create_app(settings)) as c:
        jid = c.post("/api/jobs", json=dict(genre="Fantasy", number_of_variants=1, confirmed=True, **MOCK)).json()["job_id"]
        d = wait(c, jid)
        assert (d["number_of_cards"], d["gold_cards"], d["output_size"], d["planning_mode"]) == (10, 0, 1024, "rules")
        assert d["scope"] == "full" and d["counts"]["done"] == 10
        row = [j for j in c.get("/api/jobs").json() if j["job_id"] == jid][0]
        assert row["number_of_cards"] == 10 and row["output_size"] == 1024


def test_presets_accept_new_keys(settings):
    with TestClient(create_app(settings)) as c:
        ok = c.post("/api/templates", json=dict(name="Free text", payload=dict(
            collection="Transport", genre="Cars", number_of_cards=6, gold_cards=1, output_size=64)))
        assert ok.status_code == 200, ok.text
        bad = c.post("/api/templates", json=dict(name="Bad size", payload=dict(output_size=100)))
        assert bad.status_code in (400, 422)


# ----------------------------------------------------------------------------------------- sizes
def _image_for(c, card):
    r = c.get(f"/api/artifacts/{card['artifact_id']}")
    assert r.status_code == 200
    img = c.get(card["image_url"])
    return r.json(), img.content


@pytest.mark.parametrize("size", [64, 24])
def test_output_size_downscales_and_qa_accepts_small_icons(settings, size):
    import io
    with TestClient(create_app(settings)) as c:
        jid = c.post("/api/jobs", json=dict(genre="Detective", number_of_variants=1, number_of_cards=3,
                                            output_size=size, confirmed=True, **MOCK)).json()["job_id"]
        d = wait(c, jid)
        assert d["counts"]["done"] == 3
        for card in d["variants"][0]["cards"]:
            meta, png = _image_for(c, card)
            with Image.open(io.BytesIO(png)) as im:
                assert im.size == (size, size)
            im_meta = meta["image"]
            assert (im_meta["width"], im_meta["height"]) == (size, size)
            assert im_meta["native_width"] == 512 and im_meta["output_size_requested"] == size
            tq = {ch["id"]: ch["status"] for ch in card["qa"]["checks"]}
            assert tq["TQ-03"] == "pass" and tq["TQ-07"] == "pass" and tq["TQ-08"] == "pass"
            assert card["qa_status"] != "failed"


def test_bad_output_size_is_422(settings):
    with TestClient(create_app(settings)) as c:
        for path in ("/api/jobs/preview", "/api/jobs"):
            r = c.post(path, json=dict(genre="Detective", output_size=100, confirmed=True, **MOCK))
            assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_output_size"
        r = c.post("/api/generate-card", json=dict(genre="Detective", object_name="Lamp", category=1,
                                                   output_size=100, confirmed=True, **MOCK))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_output_size"


def test_single_card_generate_accepts_output_size(settings):
    import io
    with TestClient(create_app(settings)) as c:
        r = c.post("/api/generate-card", json=dict(genre="Detective", object_name="Lamp", category=2,
                                                   output_size=96, confirmed=True, **MOCK))
        assert r.status_code == 200, r.text
        assert (r.json()["image"]["width"], r.json()["image"]["height"]) == (96, 96)
        with Image.open(io.BytesIO(c.get(r.json()["image"]["url"]).content)) as im:
            assert im.size == (96, 96)
