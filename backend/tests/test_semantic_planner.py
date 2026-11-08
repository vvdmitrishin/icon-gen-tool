"""Semantic Collection -> Set -> Cards planning, cross-batch history, Plan Only and Export Selected.
Mocked Gemini text transport only: no paid call, no image provider except the offline Mock."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.category_distribution import resolve_distribution
from app.services.semantic_planning import forbidden_terms, history_summary, near_duplicates, theme_key, violates
from tests.test_free_text_sets import MOCK, TextRecorder, job_body, wait

WINTER_FOREST = ["Snowy Pine Branch", "Red Fox in the Snow", "Frozen Wild Berries", "Barn Owl on a Birch", "Deer Antlers",
                 "Woodland Mushroom Cluster", "Animal Tracks in Snow", "Frozen Forest Stream", "Pinecone with Frost", "Hare in a Snowdrift"]
BIKES = ["Road Bicycle", "Cruiser Motorcycle", "Electric Scooter", "Cycling Helmet", "Bike Lock", "Tire Pump",
         "Repair Tool Kit", "Bike Lane Marking", "Handlebar Bell", "Charging Dock"]
FESTIVAL = ["Electric Guitar", "DJ Turntable", "Stage Light Rig", "Festival Wristband", "Water Refill Station", "Signed Poster Tube",
            "Souvenir T-Shirt Stand", "Drum Kit", "Crowd Banner Flag", "Singer with Microphone"]
SCIFI = ["Ray Gun Prop", "Rocket Ship Model", "Alien Specimen Jar", "Robot Companion", "Space Helmet", "Moon Rover",
         "Hologram Projector", "Time Machine Console", "Star Map", "Cryo Pod"]


def plan_body(names, variants=1, counts=None):
    counts = counts or resolve_distribution(None, number_of_cards=len(names), gold_cards=0)
    slots = [c for c in (1, 2, 3, 4, 5) for _ in range(counts.get(c, 0))]
    out = []
    for vi in range(variants):
        cards = []
        for pos, (cat, name) in enumerate(zip(slots, names), 1):
            card = dict(position=pos, category=cat, object_name=f"{name}" if vi == 0 else f"{name} {vi}",
                        object_description=f"a vivid illustration of {name.lower()}")
            if cat == 5:
                card["characters"] = 2
            cards.append(card)
        out.append(dict(title=f"Theme {vi}", concept=f"Concept {vi}", cards=cards))
    return {"variants": out}


def body(collection, genre, **kw):
    b = dict(collection=collection, genre=genre, number_of_variants=1, number_of_cards=10, gold_cards=0,
             allow_ai_plan=True, confirmed=True, **MOCK)
    b.update(kw)
    return b


def prompt_of(req):
    return json.loads(req.content)["contents"][0]["parts"][0]["text"]


# ------------------------------------------------------------------------------ unit helpers
def test_near_duplicates():
    assert near_duplicates(["Bicycle", "Bicycles"]) == [("Bicycle", "Bicycles")]
    assert near_duplicates(["Red Fox", "The Fox Red"]) != []
    assert near_duplicates(["Bicycle", "Bicycle Helmet", "Bike Lock"]) == []  # different objects
    assert near_duplicates(["Item 0-1", "Item 0-2", "Item 1-10"]) == []


def test_forbidden_terms_from_wishes():
    t = forbidden_terms("Magical atmosphere, no buildings")
    assert {"building", "cabin", "house"} <= t
    assert violates("Cozy log cabin", t) == "cabin" and violates("Red fox", t) is None
    assert "weapon" in forbidden_terms("Make it cozy. Without weapons or violence")
    assert forbidden_terms("bright and sunny") == set()


def test_history_summary_is_deduplicated_and_capped():
    h = history_summary([("Bicycle", "a red bike"), ("Bicycles", "again"), ("Helmet", "safety gear")] + [(f"Thing {i}", "x") for i in range(100)], cap=10)
    assert len(h) == 10 and h[0].startswith("Bicycle - a red bike") and not any(x.startswith("Bicycles") for x in h)
    assert theme_key("Winter", "Forest") != theme_key("Winter", "Beach") != theme_key("Winter", "Forest", "grand")


# ------------------------------------------------------------------------------ scenarios A-E
def test_A_winter_forest_prompt_and_plan(settings):
    rec = TextRecorder(plan_body(WINTER_FOREST))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=body("Winter", "Forest"))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["planning_mode"] == "ai"
        names = [x["object_name"] for x in d["plan"]["variants"][0]["cards"]]
        assert names == WINTER_FOREST
        p = prompt_of(rec.requests[0]).lower()
        assert "'winter'" in p and "'forest'" in p and "semantic boundary" in p
        assert "do not add objects that only fit the broad collection" in p
        assert "not fixed quotas" in p and "primary object" not in p and "accessor" not in p.replace("accessories, infrastructure", "")  # no rigid subject quotas


def test_B_wishes_exclude_buildings(settings):
    wishes = "Magical atmosphere, no buildings"
    ok = TextRecorder(plan_body(WINTER_FOREST))
    with TestClient(create_app(settings, gemini_transport=ok.transport)) as c:
        r = c.post("/api/jobs/preview", json=body("Winter", "Forest", wishes=wishes))
        assert r.status_code == 200, r.text
        p = prompt_of(ok.requests[0])
        assert wishes in p and "Exclusions are strict" in p
    bad_names = WINTER_FOREST[:-1] + ["Cozy Log Cabin"]
    bad = TextRecorder(plan_body(bad_names))
    with TestClient(create_app(settings, gemini_transport=bad.transport)) as c:
        r = c.post("/api/jobs/preview", json=body("Winter", "Forest", wishes=wishes))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_invalid"
        assert "cabin" in r.json()["detail"]["message"].lower()
        assert not c.get("/api/jobs").json()  # nothing was created


def test_C_two_wheeled_vehicles_diverse_and_duplicate_batch_rejected(settings):
    rec = TextRecorder(plan_body(BIKES))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=body("Technology", "Two-Wheeled Vehicles"))
        assert r.status_code == 200, r.text
        names = {x["object_name"] for x in r.json()["plan"]["variants"][0]["cards"]}
        assert {"Cycling Helmet", "Bike Lock", "Tire Pump"} <= names and len(names) == 10
    dup = TextRecorder(plan_body(["Bicycle", "Bicycles"] + BIKES[2:]))
    with TestClient(create_app(settings, gemini_transport=dup.transport)) as c:
        r = c.post("/api/jobs/preview", json=body("Technology", "Two-Wheeled Vehicles"))
        assert r.status_code == 422 and "near-duplicate" in r.json()["detail"]["message"]


def test_D_summer_music_festival(settings):
    rec = TextRecorder(plan_body(FESTIVAL))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=body("Summer", "Music Festival", wishes="loud and joyful"))
        assert r.status_code == 200, r.text
        assert "'Music Festival'" in prompt_of(rec.requests[0])
        assert len({x["object_name"] for x in r.json()["plan"]["variants"][0]["cards"]}) == 10


@pytest.mark.parametrize("genre", ["Science Fiction", "Detective", "Fantasy", "Western"])
def test_E_every_movie_set_uses_the_same_ai_planner(settings, genre):
    rec = TextRecorder(plan_body(SCIFI))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/jobs/preview", json=body("Movies", genre, allow_ai_plan=False))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ai_plan_required" and rec.requests == []
        r = c.post("/api/jobs/preview", json=body("Movies", genre))
        assert r.status_code == 200 and r.json()["planning_mode"] == "ai" and len(rec.requests) == 1
        assert f"'{genre}'" in prompt_of(rec.requests[0])  # the Set reaches the semantic planner, whatever its name


# ------------------------------------------------------------------------------ cross-batch history
def _run(c, **kw):
    r = c.post("/api/jobs", json=body(**kw))
    assert r.status_code in (200, 201), r.text
    return wait(c, r.json()["job_id"])


def test_cross_batch_history_is_passed_and_isolated(settings):
    first = plan_body(WINTER_FOREST)
    rec = TextRecorder(first)
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        _run(c, collection="Winter", genre="Forest")
        _run(c, collection="Summer", genre="Beach", number_of_cards=10)  # unrelated theme
        assert "ALREADY GENERATED" not in prompt_of(rec.requests[0])
        rec.plan_body = plan_body(["Snow Hare", "Ice Fishing Hole", "Snow Leopard Cub", "Winter Berries Wreath", "Birch Bark Scroll",
                                   "Frost Fern", "Elk Silhouette", "Snowshoe", "Icicle Cluster", "Moss Covered Log"])
        d2 = _run(c, collection="Winter", genre="Forest")
        p2 = prompt_of(rec.requests[-1])
        assert "ALREADY GENERATED" in p2 and "Snowy Pine Branch" in p2 and "Red Fox in the Snow" in p2
        assert "Prefer FRESH subjects" in p2
        assert any("Earlier batches" in n for n in d2["notes"])
        # unrelated Collection/Set subjects never leak in: Summer/Beach has the same names here, so use a distinct third theme
        rec.plan_body = plan_body(BIKES)
        _run(c, collection="Technology", genre="Two-Wheeled Vehicles")
        assert "Snowy Pine Branch" not in prompt_of(rec.requests[-1]) and "ALREADY GENERATED" not in prompt_of(rec.requests[-1])
        # other Set of the same Collection and Set type are separate histories
        rec.plan_body = plan_body(WINTER_FOREST[::-1])
        _run(c, collection="Winter", genre="Mountains")
        assert "ALREADY GENERATED" not in prompt_of(rec.requests[-1])


def test_history_is_compact(settings):
    from app.services.jobs import JobService
    assert JobService.previous_subjects.__doc__ and "capped" in JobService.previous_subjects.__doc__
    assert len(history_summary([(f"Subject {i}", "d") for i in range(500)])) <= 48


# ------------------------------------------------------------------------------ Plan Only
def test_plan_only_never_calls_an_image_provider_and_reports_model_and_cost(settings):
    rec = TextRecorder(plan_body(WINTER_FOREST))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        before = c.get("/api/health").json() if False else None
        r = c.post("/api/plan/test", json=dict(collection="Winter", genre="Forest", wishes="cozy", number_of_cards=10))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["image_provider_called"] is False and d["planning_mode"] == "ai" and d["text_model"]
        assert d["text_cost_estimate_usd"] == 0.002 and d["cached"] is False
        cards = d["plan"]["variants"][0]["cards"]
        assert [x["object_name"] for x in cards] == WINTER_FOREST and all("category" in x and x["object_description"] for x in cards)
        assert len(rec.requests) == 1 and ":generateContent" in str(rec.requests[0].url)
        assert "image" not in str(rec.requests[0].url).split("/models/")[1]  # a text model, never an image model
        assert c.get("/api/jobs").json() == []  # no job, no images
        again = c.post("/api/plan/test", json=dict(collection="Winter", genre="Forest", wishes="cozy", number_of_cards=10)).json()
        assert again["cached"] is True and again["text_cost_estimate_usd"] == 0.0 and len(rec.requests) == 1
        rec.plan_body = plan_body(FESTIVAL)
        other = c.post("/api/plan/test", json=dict(collection="Summer", genre="Music Festival", number_of_cards=10)).json()
        assert other["plan"]["variants"][0]["cards"][0]["object_name"] == "Electric Guitar" and len(rec.requests) == 2
        assert other["budget"]["spent_estimated_usd"] >= d["budget"]["spent_estimated_usd"] + 0.002 - 1e-9


def test_plan_only_grand_and_errors(settings):
    from tests.test_grand_set import grand_counts
    rec = TextRecorder(plan_body(["Fox Sulks At Sunset", "Owl Wins Bingo", "Bear Hugs Empty Honey Pot"], counts=grand_counts(3)))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        r = c.post("/api/plan/test", json=dict(collection="Winter", genre="Forest", number_of_cards=3, set_type="grand"))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["distribution"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 3} and "GRAND SET" in prompt_of(rec.requests[0])
        assert all(x["characters"] in (1, 2) for x in d["plan"]["variants"][0]["cards"])
        bad = c.post("/api/plan/test", json=dict(collection="Winter", genre="", number_of_cards=3))
        assert bad.status_code == 422


def test_plan_only_pluggable_text_backend(settings):
    """The planner's model call is a seam: any text LLM backend returning the same JSON can be compared."""
    from app.services.text_planner import TextPlanner
    seen = {}
    def fake_backend(s, model, prompt, seed):
        seen["prompt"] = prompt
        return plan_body(BIKES)
    app = create_app(settings)
    jobs = app.state.jobs
    jobs.text_planner = TextPlanner(jobs.gen.registry, backend=fake_backend)
    with TestClient(app) as c:
        r = c.post("/api/plan/test", json=dict(collection="Technology", genre="Two-Wheeled Vehicles", number_of_cards=10))
        assert r.status_code == 200, r.text
        assert "Two-Wheeled Vehicles" in seen["prompt"]


# ------------------------------------------------------------------------------ Standard / Grand regression
def test_standard_distribution_and_grand_rules_unchanged(settings):
    rec = TextRecorder(plan_body(WINTER_FOREST))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        d = c.post("/api/jobs/preview", json=body("Winter", "Forest")).json()
        assert d["distribution"] == {"1": 2, "2": 2, "3": 3, "4": 3, "5": 0} and d["set_type"] == "standard"
        assert [x["category"] for x in d["plan"]["variants"][0]["cards"]] == [1, 1, 2, 2, 3, 3, 3, 4, 4, 4]


# ------------------------------------------------------------------------------ Export Selected
def _selection(job_detail, positions, variant=0):
    cards = job_detail["variants"][variant]["cards"]
    return [dict(job_id=job_detail["job_id"], card_id=cards[p]["card_id"], artifact_id=cards[p]["artifact_id"]) for p in positions]


def test_export_selection_from_several_jobs_and_warnings(settings):
    rec = TextRecorder(plan_body(WINTER_FOREST))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        j1 = _run(c, collection="Winter", genre="Forest")
        rec.plan_body = plan_body([f"Fresh {n}" for n in WINTER_FOREST])
        j2 = _run(c, collection="Winter", genre="Forest")
        items = _selection(j1, [0, 3, 9]) + _selection(j2, [1, 5])
        r = c.post("/api/exports/selection", json=dict(collection="Winter", genre="Forest", set_type="standard", items=items))
        assert r.status_code == 200, r.text
        rec_ = r.json()
        assert rec_["kind"] == "selection" and rec_["count"] == 5 and set(rec_["job_ids"]) == {j1["job_id"], j2["job_id"]}
        assert any("5 image" in w and "10 cards" in w for w in rec_["warnings"])  # not a standard 10-card set
        assert rec_["standard_ten_card_set"] is False and any("MOCK" in w for w in rec_["warnings"])
        z = zipfile.ZipFile(io.BytesIO(c.get(rec_["download_url"]).content))
        names = z.namelist()
        assert sum(n.startswith("artwork/") for n in names) == 5 and {"manifest.json", "selection-report.json", "README.txt"} <= set(names)
        m = json.loads(z.read("manifest.json"))
        assert {i["batch"] for i in m["images"]} == {1, 2}
        assert {i["artifact_id"] for i in m["images"]} == {x["artifact_id"] for x in items}
        for img in m["images"]:
            assert img["job_id"] and img["card_id"] and img["variant_id"] and img["model"] and img["category"]
        first = z.read(m["images"][0]["file"])
        assert first[:8] == b"\x89PNG\r\n\x1a\n"  # the stored file, byte for byte
        assert m["report"]["category_counts"]


def test_export_selection_ten_card_standard_set_and_errors(settings):
    rec = TextRecorder(plan_body(WINTER_FOREST))
    with TestClient(create_app(settings, gemini_transport=rec.transport)) as c:
        j = _run(c, collection="Winter", genre="Forest")
        r = c.post("/api/exports/selection", json=dict(collection="Winter", genre="Forest", items=_selection(j, range(10))))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["standard_ten_card_set"] is True  # 10 cards, no golden, all four categories covered
        assert not any("10 cards" in w for w in d["warnings"])
        assert c.post("/api/exports/selection", json=dict(items=[])).status_code == 400
        bad = c.post("/api/exports/selection", json=dict(items=[dict(job_id=j["job_id"], card_id="nope", artifact_id="x")]))
        assert bad.status_code == 404
        # the same image selected twice is exported once
        one = _selection(j, [2])
        assert c.post("/api/exports/selection", json=dict(items=one + one)).json()["count"] == 1
