"""B1: the delivered PNG has EXACTLY output_size x output_size px; the provider original is retained."""
import hashlib
import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.services.generation import deliver_size
from app.services.qa import qa_card
from tests.conftest import art_png, png_bytes

MOCK = dict(provider="mock", model="mock-placeholder-v1")


def wait(c, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/jobs/{job_id}").json()
        if not d["active"]:
            return d
        time.sleep(0.03)
    raise AssertionError("job did not finish")


def one_card(c, size, cards=1):
    jid = c.post("/api/jobs", json=dict(genre="Detective", number_of_variants=1, number_of_cards=cards,
                                        output_size=size, confirmed=True, **MOCK)).json()["job_id"]
    return jid, wait(c, jid)


@pytest.mark.parametrize("size", [1024, 768, 512, 24, 64])
def test_delivered_png_is_exact_and_original_retained(settings, size):
    with TestClient(create_app(settings)) as c:
        jid, d = one_card(c, size)
        card = d["variants"][0]["cards"][0]
        meta = c.get(f"/api/artifacts/{card['artifact_id']}").json()
        img = c.get(card["image_url"]).content
        with Image.open(io.BytesIO(img)) as im:
            assert im.size == (size, size)
        im_meta = meta["image"]
        assert (im_meta["width"], im_meta["height"]) == (size, size)
        assert im_meta["output_size_requested"] == size and im_meta["native_width"] == 512
        assert hashlib.sha256(img).hexdigest() == im_meta["sha256"]
        orig = c.app.state.__dict__ if False else None  # noqa: F841
        orig_path = settings.artifact_root / im_meta["original_file"]
        assert orig_path.exists() and im_meta["original_file"] == f"{card['artifact_id']}.original.png"
        data = orig_path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == im_meta["original_sha256"]
        with Image.open(io.BytesIO(data)) as o:
            assert o.size == (512, 512)
        tq = {ch["id"]: ch["status"] for ch in card["qa"]["checks"]}
        assert tq["TQ-10"] == "pass" and card["qa_status"] != "failed"
        assert "output_size_note" not in json.dumps(d)  # never surfaced to the UI via the job


def test_deliver_size_square_crop_and_non_square_aspect():
    wide = png_bytes((300, 200))
    out, w, h, note = deliver_size(wide, 300, 200, 64)
    assert (w, h) == (64, 64) and "centre-cropped" in note
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (64, 64)
    out, w, h, _ = deliver_size(wide, 300, 200, 128, square=False)
    assert (w, h) == (128, round(200 * 128 / 300))
    out, w, h, _ = deliver_size(png_bytes((200, 300)), 200, 300, 600, square=False)
    assert h == 600 and w == 400
    same = png_bytes((64, 64))
    assert deliver_size(same, 64, 64, 64)[0] == same


def test_qa_flags_size_mismatch(tmp_path):
    data = art_png(64)
    meta = {"artifact_id": "art-x", "provider": {"id": "g", "model": "m"}, "prompt": {"effective": "p"},
            "card": {"object_name": "o"}, "image": {"sha256": hashlib.sha256(data).hexdigest(), "output_size_requested": 128}}
    p = tmp_path / "a.png"
    p.write_bytes(data)
    r = qa_card(p, meta, "1:1")
    st = {c["id"]: c["status"] for c in r["checks"]}
    assert st["TQ-10"] == "fail" and r["status"] == "failed"
    meta["image"]["output_size_requested"] = 64
    r = qa_card(p, meta, "1:1")
    assert {c["id"]: c["status"] for c in r["checks"]}["TQ-10"] == "pass"


def test_exported_zip_images_have_requested_size(settings):
    from dataclasses import replace
    with TestClient(create_app(settings)) as c:
        jid, d = one_card(c, 64, cards=3)
        assert c.post(f"/api/jobs/{jid}/variants/A/approve").status_code == 200
        r = c.post("/api/exports", json={"job_id": jid, "allow_mock": True})
        assert r.status_code == 200, r.text
        z = zipfile.ZipFile(io.BytesIO(c.get(r.json()["download_url"]).content))
        art = [n for n in z.namelist() if n.startswith("artwork/")]
        assert len(art) == 3
        for n in art:
            with Image.open(io.BytesIO(z.read(n))) as im:
                assert im.size == (64, 64)
        m = json.loads(z.read("manifest.json"))
        assert m["output_pixel_size"] == "64x64" and all(x["pixel_size"] == "64x64" for x in m["cards"])
        assert "64x64" in z.read("README.txt").decode()
