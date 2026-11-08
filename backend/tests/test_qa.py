import hashlib
import io

from PIL import Image

from app.services.qa import qa_card, qa_variant
from tests.conftest import art_png, png_bytes


def meta_for(data: bytes, **kw):
    m = {"artifact_id": "art-x", "provider": {"id": "gemini", "model": "m"}, "prompt": {"effective": "p"},
         "card": {"object_name": "o"}, "image": {"sha256": hashlib.sha256(data).hexdigest()}}
    m.update(kw)
    return m


def run(tmp_path, data, meta=None, aspect="1:1"):
    p = tmp_path / "a.png"
    p.write_bytes(data)
    return qa_card(p, meta_for(data) if meta is None else meta, aspect)


def by_id(r):
    return {c["id"]: c["status"] for c in r["checks"]}


def test_clean_image_passes_every_check(tmp_path):
    r = run(tmp_path, art_png(1024))
    assert r["status"] == "passed" and set(by_id(r).values()) == {"pass"}


def test_blank_image_fails(tmp_path):
    r = run(tmp_path, png_bytes((600, 600), (200, 200, 200)))
    assert r["status"] == "failed" and by_id(r)["TQ-05"] == "fail"


def test_resolution_thresholds(tmp_path):
    assert by_id(run(tmp_path, art_png(100)))["TQ-03"] == "fail"
    assert by_id(run(tmp_path, art_png(400)))["TQ-03"] == "warn"
    assert by_id(run(tmp_path, art_png(512)))["TQ-03"] == "pass"


def test_aspect_ratio_checks(tmp_path):
    buf = io.BytesIO()
    Image.open(io.BytesIO(art_png(600))).resize((600, 400)).save(buf, format="PNG")
    assert by_id(run(tmp_path, buf.getvalue()))["TQ-04"] == "fail"       # 3:2 vs requested 1:1
    assert by_id(run(tmp_path, buf.getvalue(), aspect="3:2"))["TQ-04"] == "pass"
    assert by_id(run(tmp_path, art_png(600), aspect="garbage"))["TQ-04"] == "warn"


def test_tampered_file_and_missing_metadata_fail(tmp_path):
    good = art_png(600)
    meta = meta_for(good)
    other = art_png(600, seed=5)
    r = run(tmp_path, other, meta)
    assert by_id(r)["TQ-07"] == "fail" and r["status"] == "failed"
    r = run(tmp_path, good, meta_for(good, prompt={}))
    assert by_id(r)["TQ-06"] == "fail"


def test_unreadable_and_non_png(tmp_path):
    assert qa_card(tmp_path / "missing.png", {}, "1:1")["status"] == "failed"
    assert run(tmp_path, b"not an image at all", {})["status"] == "failed"
    buf = io.BytesIO()
    Image.open(io.BytesIO(art_png(600))).save(buf, format="JPEG")
    assert by_id(run(tmp_path, buf.getvalue(), meta_for(buf.getvalue())))["TQ-02"] == "fail"


def test_mock_is_flagged_not_failed(tmp_path):
    d = art_png(600)
    r = run(tmp_path, d, meta_for(d, is_mock=True))
    assert r["status"] == "flagged" and by_id(r)["TQ-09"] == "warn"


def card(i, cat, name=None, sha=None, status="done", qa="passed", review="pending", w=1024):
    return {"card_id": f"c{i}", "category": cat, "object_name": name or f"obj{i}", "status": status,
            "sha256": sha if sha is not None else f"h{i}", "width": w, "height": w, "qa_status": qa, "art_review": review}


DIST = {1: 2, 2: 2, 3: 3, 4: 3}
CATS = [1, 1, 2, 2, 3, 3, 3, 4, 4, 4]


def test_set_qa_clean_complete_set():
    r = qa_variant("A", [card(i, c) for i, c in enumerate(CATS)], DIST)
    assert r["status"] == "passed" and r["generated"] == 10 and all(c["status"] == "pass" for c in r["checks"])


def test_set_qa_detects_each_problem():
    cs = [card(i, c) for i, c in enumerate(CATS)]
    cs[3]["status"], cs[3]["sha256"] = "planned", None
    r = qa_variant("A", cs, DIST)
    assert r["status"] == "flagged" and {c["id"]: c["status"] for c in r["checks"]}["SQ-01"] == "warn"

    bad = [card(i, c) for i, c in enumerate([1, 1, 1, 2, 3, 3, 3, 4, 4, 4])]
    assert {c["id"]: c["status"] for c in qa_variant("A", bad, DIST)["checks"]}["SQ-02"] == "fail"

    dup = [card(i, c) for i, c in enumerate(CATS)]
    dup[1]["sha256"] = dup[0]["sha256"]
    assert {c["id"]: c["status"] for c in qa_variant("A", dup, DIST)["checks"]}["SQ-05"] == "fail"

    names = [card(i, c, name="Same") for i, c in enumerate(CATS)]
    assert {c["id"]: c["status"] for c in qa_variant("A", names, DIST)["checks"]}["SQ-03"] == "fail"

    gold = [card(i, c) for i, c in enumerate(CATS)]
    gold[0]["category"] = 5
    assert {c["id"]: c["status"] for c in qa_variant("A", gold, DIST)["checks"]}["SQ-04"] == "fail"  # unplanned golden

    rej = [card(i, c) for i, c in enumerate(CATS)]
    rej[2]["art_review"] = "rejected"
    r = qa_variant("A", rej, DIST)
    assert r["status"] == "failed" and r["art_review"]["rejected"] == 1

    qf = [card(i, c) for i, c in enumerate(CATS)]
    qf[0]["qa_status"] = "failed"
    assert {c["id"]: c["status"] for c in qa_variant("A", qf, DIST)["checks"]}["SQ-06"] == "fail"

    sizes = [card(i, c, w=1024 if i else 512) for i, c in enumerate(CATS)]
    assert {c["id"]: c["status"] for c in qa_variant("A", sizes, DIST)["checks"]}["SQ-07"] == "warn"
