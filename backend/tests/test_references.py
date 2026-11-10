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


# ----------------------------------------------------------------------- curated default references
def _defaults_dir(tmp_path, count=5):
    d = tmp_path / "defaults"
    d.mkdir()
    for n in range(1, count + 1):
        (d / f"Ref_category_{n}.png").write_bytes(png_bytes((120, 80), color=(10 * n, 40, 90)))
    return d


def test_seed_defaults_imports_five_category_references_once(mgr, tmp_path):
    d = _defaults_dir(tmp_path)
    assert mgr.seed_defaults(d) == [f"Ref_category_{n}.png" for n in range(1, 6)]
    assert sorted(m["ref_type"] for m in mgr.list()) == [f"category_{n}" for n in range(1, 6)]
    assert mgr.seed_defaults(d) == [] and len(mgr.list()) == 5  # idempotent: no duplicates on restart


def test_seeded_reference_deleted_by_producer_is_not_resurrected(mgr, tmp_path):
    d = _defaults_dir(tmp_path)
    mgr.seed_defaults(d)
    victim = next(m for m in mgr.list() if m["ref_type"] == "category_2")
    mgr.delete(victim["ref_id"])
    mgr.seed_defaults(d)
    assert [m["ref_type"] for m in mgr.list()].count("category_2") == 0 and len(mgr.list()) == 4


def test_producer_upload_is_kept_and_changed_default_replaces_its_old_copy(mgr, tmp_path):
    d = _defaults_dir(tmp_path, count=1)
    mgr.seed_defaults(d)
    mine = mgr.save_upload("mine.png", png_bytes((70, 70), color=(1, 2, 3)), "category_1")
    (d / "Ref_category_1.png").write_bytes(png_bytes((120, 80), color=(250, 250, 0)))  # curated file replaced
    assert mgr.seed_defaults(d) == ["Ref_category_1.png"]
    ids = {m["ref_id"] for m in mgr.list()}
    assert mine["ref_id"] in ids and len(ids) == 2


def test_seed_defaults_tolerates_missing_or_broken_sources(mgr, tmp_path):
    assert mgr.seed_defaults(None) == [] and mgr.seed_defaults(tmp_path / "nope") == []
    d = tmp_path / "bad"
    d.mkdir()
    (d / "Ref_category_1.png").write_bytes(b"not an image")
    assert mgr.seed_defaults(d) == [] and mgr.list() == []


def test_app_seeds_defaults_from_settings_and_routes_by_category(tmp_path):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.jobs import select_references
    from tests.conftest import FAKE_KEY
    from app.config import Settings

    s = Settings(gemini_api_key=FAKE_KEY, data_root=tmp_path / "data", artifact_root=tmp_path / "data" / "artifacts",
                 default_references_dir=_defaults_dir(tmp_path))
    with TestClient(create_app(s)) as c:
        metas = c.get("/api/references").json()
    assert len(metas) == 5
    by_type = {m["ref_type"]: m["ref_id"] for m in metas}
    for cat in range(1, 6):  # each card receives only its own category reference
        ids, mode = select_references(metas, cat, 5)
        assert ids == [by_type[f"category_{cat}"]] and mode == "category_match"
    assert replace(s, default_references_dir=None).default_references_dir is None


def test_repository_ships_the_curated_default_references():
    from app.config import REPO_ROOT

    for n in range(1, 6):
        assert (REPO_ROOT / "specification" / f"Ref_category_{n}.png").is_file()
