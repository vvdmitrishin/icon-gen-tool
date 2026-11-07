import pytest

from app.services.reference_manager import ReferenceError_, ReferenceManager
from tests.conftest import png_bytes


@pytest.fixture
def mgr(tmp_path):
    return ReferenceManager(tmp_path / "refs", max_bytes=200_000)


def test_upload_roundtrip_and_thumbnail(mgr):
    meta = mgr.save_upload("../../evil/name.png", png_bytes((300, 200)), "category_2")
    assert meta["ref_id"].startswith("ref-") and meta["original_name"] == "name.png"
    assert meta["width"] == 300 and mgr.image_path(meta["ref_id"], thumb=True)
    assert mgr.load(meta["ref_id"]).role == "category_2"


def test_jpeg_is_reencoded_to_png(mgr):
    meta = mgr.save_upload("a.jpg", png_bytes((50, 50), fmt="JPEG"), "style")
    assert mgr.load(meta["ref_id"]).png_bytes[:4] == b"\x89PNG"


def test_identical_upload_is_deduplicated(mgr):
    a = mgr.save_upload("a.png", png_bytes(), "style")
    b = mgr.save_upload("b.png", png_bytes(), "style")
    assert a["ref_id"] == b["ref_id"] and len(mgr.list()) == 1


@pytest.mark.parametrize("data,code", [
    (b"", "empty"),
    (b"not an image at all", "not_an_image"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "not_an_image"),
])
def test_rejects_bad_files(mgr, data, code):
    with pytest.raises(ReferenceError_) as e:
        mgr.save_upload("x.png", data, "style")
    assert e.value.code == code


def test_rejects_oversize_and_bad_type(mgr):
    with pytest.raises(ReferenceError_) as e:
        mgr.save_upload("x.png", b"0" * 300_000, "style")
    assert e.value.code == "too_large"
    with pytest.raises(ReferenceError_) as e:
        mgr.save_upload("x.png", png_bytes(), "bogus")
    assert e.value.code == "bad_type"


def test_path_traversal_ids_rejected(mgr):
    with pytest.raises(ReferenceError_):
        mgr.image_path("../../etc/passwd")


def test_delete(mgr):
    m = mgr.save_upload("a.png", png_bytes(), "style")
    assert mgr.delete(m["ref_id"]) is True
    assert mgr.get(m["ref_id"]) is None
