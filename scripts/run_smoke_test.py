"""Offline end-to-end smoke test of the whole workflow with the free MOCK provider.

Uses a throw-away data folder and never calls a paid API. It walks: plan -> generate 10 cards ->
QA -> review -> regenerate one card -> approve -> export ZIP -> verify manifest hashes ->
collection overview -> prompt library.   (Live Gemini: scripts/smoke_gemini.py --yes)

  backend\\.venv\\Scripts\\python scripts\\run_smoke_test.py
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
# console-safe output on Windows (cp1252)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

MOCK = {"provider": "mock", "model": "mock-placeholder-v1"}
results: list[tuple[bool, str]] = []


def step(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")


def wait(c: TestClient, job_id: str, timeout: float = 90) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/jobs/{job_id}").json()
        if not d["active"]:
            return d
        time.sleep(0.05)
    raise TimeoutError("job did not finish")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        s = Settings(data_root=data, artifact_root=data / "artifacts")
        with TestClient(create_app(s)) as c:
            step(c.get("/api/health").json()["status"] == "ok", "backend health")
            step(len(c.get("/api/prompts").json()) == 8, "prompt library seeded (8 templates)")

            r = c.post("/api/jobs", json={"genre": "Detective", "number_of_variants": 1, "scope": "full", **MOCK})
            step(r.status_code == 200, "create a full 10-card job (mock, free)", r.text[:80] if r.status_code != 200 else "")
            job_id = r.json()["job_id"]
            d = wait(c, job_id)
            cards = d["variants"][0]["cards"]
            step(d["status"] == "ready_for_review" and d["counts"]["done"] == 10, "10 cards generated", d["status"])
            step(sorted(k["category"] for k in cards) == [1, 1, 2, 2, 3, 3, 3, 4, 4, 4], "category distribution 2/2/3/3")
            step(all(k["qa_status"] in ("flagged", "passed") for k in cards), "technical QA ran on every card (mock images are flagged)")
            step(d["variants"][0]["set_qa"]["status"] != "failed", "set QA not failed", d["variants"][0]["set_qa"]["status"])

            rv = c.post(f"/api/jobs/{job_id}/cards/{cards[0]['card_id']}/review", json={"decision": "approved"})
            step(rv.status_code == 200, "approve a card in art review")
            rg = c.post(f"/api/jobs/{job_id}/cards/{cards[1]['card_id']}/regenerate", json={"confirmed": False, "note": "simpler"})
            d = wait(c, job_id)
            k2 = next(k for k in d["variants"][0]["cards"] if k["card_id"] == cards[1]["card_id"])
            step(rg.status_code == 200 and len(k2["versions"]) == 2, "regenerate one card (new version, old kept)")

            ap = c.post(f"/api/jobs/{job_id}/variants/A/approve")
            step(ap.status_code == 200 and ap.json()["approved_variant_id"] == "A", "approve the variant")

            blocked = c.post("/api/exports", json={"job_id": job_id})
            step(blocked.status_code == 400, "export of MOCK images is refused by default")
            ex = c.post("/api/exports", json={"job_id": job_id, "allow_mock": True})
            step(ex.status_code == 200, "export with allow_mock (testing only)")
            z = zipfile.ZipFile(io.BytesIO(c.get(ex.json()["download_url"]).content))
            names = z.namelist()
            step(all(n in names for n in ("README.txt", "manifest.json", "set-plan.json", "QA-report.json", "contact-sheet.png"))
                 and len([n for n in names if n.startswith("artwork/")]) == 10, "ZIP has the spec files + 10 artworks")
            m = json.loads(z.read("manifest.json"))
            step(all(hashlib.sha256(z.read(k["file"])).hexdigest() == k["sha256"] for k in m["cards"]), "manifest hashes match the files")

            ov = c.get("/api/collection/overview").json()
            step(len(ov["genres"]) == 3 and not ov["final_ready"], "collection overview (mock set is never final-ready)")
            step(c.get("/api/settings").status_code == 200, "settings endpoint")
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} steps passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
