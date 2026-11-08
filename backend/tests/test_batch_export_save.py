"""Export ZIPs can be copied into the user's Downloads folder by the local server (the in-app browser may not finish downloads)."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_free_text_sets import MOCK, wait


def test_save_export_to_downloads(settings, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    with TestClient(create_app(settings)) as c:
        jid = c.post("/api/jobs", json=dict(collection="Summer", genre="Beach", number_of_cards=2, gold_cards=0, number_of_variants=1, confirmed=True, **MOCK)).json()["job_id"]
        d = wait(c, jid)
        items = [dict(job_id=jid, card_id=k["card_id"], artifact_id=k["artifact_id"]) for k in d["variants"][0]["cards"]]
        rec = c.post("/api/exports/selection", json=dict(collection="Summer", genre="Beach", set_type="standard", items=items)).json()
        r = c.post(f"/api/exports/{rec['export_id']}/save-to-downloads")
        assert r.status_code == 200
        saved = Path(r.json()["path"])
        assert saved.parent == home / "Downloads" and saved.stat().st_size == rec["size"]
        r2 = c.post(f"/api/exports/{rec['export_id']}/save-to-downloads").json()  # never overwrites
        assert Path(r2["path"]) != saved and Path(r2["path"]).exists()
        assert c.post("/api/exports/exp-nope/save-to-downloads").status_code == 404
