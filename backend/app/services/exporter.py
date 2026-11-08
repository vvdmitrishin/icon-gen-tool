"""Export (spec §10.6) and the Movie-collection workflow (Phase 6).

A variant export is a ZIP with README.txt, manifest.json, set-plan.json, QA-report.json,
contact-sheet.png and artwork/NN_slug_catN.png. A collection export bundles one approved variant per
genre. Exports never contain credentials; every PNG is listed with provider, model, prompt versions,
references, category and QA verdict.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .. import __version__
from ..config import Settings
from .db import Db
from .jobs import JobService
from .planning import supported_genres

SPEC_VERSION = "1.7"
LIMITATIONS = ("Technical QA checks structure only (format, size, aspect, blank, integrity, duplicates). "
               "Text-in-image, frames and style fidelity are not detected automatically; they need human art review.")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS exports (
  export_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL, summary_json TEXT NOT NULL,
  path TEXT NOT NULL, size INTEGER NOT NULL
);
"""


class ExportError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}


def slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "x"


def contact_sheet(images: list[tuple[bytes, str]], cols: int = 5, thumb: int = 256) -> bytes:
    rows = (len(images) + cols - 1) // cols
    cap, pad = 26, 10
    sheet = Image.new("RGB", (cols * (thumb + pad) + pad, rows * (thumb + cap + pad) + pad), (243, 244, 251))
    d = ImageDraw.Draw(sheet)
    for i, (png, label) in enumerate(images):
        x, y = pad + (i % cols) * (thumb + pad), pad + (i // cols) * (thumb + cap + pad)
        with Image.open(io.BytesIO(png)) as im:
            im = im.convert("RGB")
            im.thumbnail((thumb, thumb))
            sheet.paste(im, (x + (thumb - im.width) // 2, y + (thumb - im.height) // 2))
        d.text((x, y + thumb + 6), label[:38], fill=(60, 63, 102))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


class Exporter:
    def __init__(self, settings: Settings, jobs: JobService, db: Db) -> None:
        self.s, self.jobs, self.db = settings, jobs, db
        db.script(_SCHEMA)
        self.root = settings.data_root / "exports"

    # ------------------------------------------------------------------ one variant bundle
    def _variant_files(self, detail: dict[str, Any], variant: dict[str, Any], *, include_unapproved: bool,
                       allow_mock: bool) -> tuple[dict[str, bytes], dict[str, Any]]:
        store = self.jobs.gen.store
        vid = variant["variant_id"]
        cards = variant["cards"]
        blockers = []
        n_done = sum(1 for c in cards if c["status"] == "done")
        if n_done < len(cards) or not cards:
            blockers.append(f"variant {vid} is incomplete ({n_done}/{len(cards)} cards generated)")
        if variant["set_qa"]["status"] == "failed":
            blockers.append("set QA failed: " + "; ".join(c["name"] for c in variant["set_qa"]["checks"] if c["status"] == "fail"))
        if not variant["approved"] and not include_unapproved:
            blockers.append(f"variant {vid} is not approved (approve it, or export explicitly as unapproved)")
        if any(c["art_review"] == "rejected" for c in cards):
            blockers.append("a card is rejected in art review")
        metas = {c["card_id"]: store.metadata(c["artifact_id"]) for c in cards if c["artifact_id"]}
        mock = any((m or {}).get("is_mock") for m in metas.values())
        if mock and not allow_mock:
            blockers.append("the set contains MOCK placeholder images (pass allow_mock to export for testing)")
        if blockers:
            raise ExportError(400, "not_exportable", "Cannot export: " + "; ".join(blockers) + ".", {"blockers": blockers})

        files: dict[str, bytes] = {}
        manifest_cards, thumbs = [], []
        for c in sorted(cards, key=lambda x: x["position"]):
            meta = metas[c["card_id"]]
            png = store.image_path(c["artifact_id"]).read_bytes()  # type: ignore[union-attr]
            name = f"artwork/{c['position']:02d}_{slug(c['object_name'])}_cat{c['category']}.png"
            files[name] = png
            thumbs.append((png, f"{c['position']:02d} cat{c['category']} {c['object_name']}"))
            cur = next(v for v in c["versions"] if v["is_current"])
            manifest_cards.append({
                "position": c["position"], "card_id": c["card_id"], "file": name,
                "object": c["object_name"], "description": c["object_description"], "category": c["category"],
                "artifact_id": c["artifact_id"], "version": cur["version"], "regenerations": c["regen_count"],
                "sha256": hashlib.sha256(png).hexdigest(), "width": meta["image"]["width"], "height": meta["image"]["height"],
                "pixel_size": f"{meta['image']['width']}x{meta['image']['height']}",
                "native_width": meta["image"].get("native_width"), "native_height": meta["image"].get("native_height"),
                "provider": meta["provider"]["id"], "model": meta["provider"]["model"],
                "model_status": meta["provider"]["model_status"], "origin": meta["origin"], "is_mock": meta.get("is_mock", False),
                "prompt": {"effective": meta["prompt"]["effective"], "template_versions": meta["prompt"].get("template_versions", {}),
                           "style_bible_version": meta["prompt"]["style_bible_version"],
                           "category_rules_version": meta["prompt"]["category_rules_version"],
                           "free_wishes": meta["prompt"]["free_wishes"], "repair_note": meta["prompt"].get("repair_note", "")},
                "references": meta["references"], "reference_selection": c["reference_selection"],
                "estimated_cost_usd": meta["cost"]["estimated_usd"], "usage": meta["cost"]["usage_metadata"],
                "latency_ms": meta["timing"]["latency_ms"], "created_at": meta["created_at"],
                "technical_qa": c["qa_status"], "art_review": c["art_review"],
            })
        manifest = {
            "format": "playrix-collection-studio-export/1", "spec_version": SPEC_VERSION, "app_version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(), "collection": detail["collection"],
            "genre": detail["genre"], "job_id": detail["job_id"], "variant_id": vid, "variant_title": variant["title"],
            "concept": variant["concept"], "approved": variant["approved"], "approved_at": variant["approved_at"],
            "distribution": detail["distribution"], "seed": detail["seed"], "contains_mock_images": mock,
            "number_of_cards": len(cards), "gold_cards": detail.get("gold_cards", 0),
            "output_size": detail.get("output_size", 1024),
            "output_pixel_size": (f"{manifest_cards[0]['width']}x{manifest_cards[0]['height']}" if manifest_cards else None),
            "planning_mode": detail.get("planning_mode", "rules"),
            "estimated_total_cost_usd": round(sum(c["estimated_cost_usd"] for c in manifest_cards), 6),
            "cost_is_estimate": True, "limitations": LIMITATIONS, "cards": manifest_cards,
        }
        files["manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False).encode()
        files["set-plan.json"] = json.dumps({
            "genre": detail["genre"], "job_id": detail["job_id"], "variant": {k: variant[k] for k in ("variant_id", "title", "concept")},
            "distribution": detail["distribution"], "seed": detail["seed"], "request_settings": detail["request"],
            "cards": [{k: c[k] for k in ("position", "category", "object_name", "object_description")}
                      for c in sorted(cards, key=lambda x: x["position"])]}, indent=2, ensure_ascii=False).encode()
        files["QA-report.json"] = json.dumps({"set_qa": variant["set_qa"], "limitations": LIMITATIONS, "cards": [
            {"position": c["position"], "object": c["object_name"], "technical_qa": c["qa_status"],
             "checks": (c["qa"] or {}).get("checks", []), "art_review": c["art_review"], "review_note": c["review_note"]}
            for c in sorted(cards, key=lambda x: x["position"])]}, indent=2, ensure_ascii=False).encode()
        files["contact-sheet.png"] = contact_sheet(thumbs)
        files["README.txt"] = (
            f"{detail['collection']} - {detail['genre']} - Variant {vid} ({variant['title']})\n"
            f"Status: {'APPROVED' if variant['approved'] else 'NOT APPROVED (exported explicitly)'}\n"
            f"{'WARNING: contains MOCK placeholder images, not AI artwork.' + chr(10) if mock else ''}"
            f"\nartwork/            {len(cards)} PNGs, each exactly {manifest['output_pixel_size']} px (artwork only: no frames, stars or UI)\n"
            "manifest.json       provider, model, prompts, references, category, QA verdict per image\n"
            "set-plan.json       the planned set and request settings\n"
            "QA-report.json      technical + set QA results\n"
            "contact-sheet.png   overview\n\n" + LIMITATIONS + "\nCosts in the manifest are local estimates, not invoices.\n").encode()
        return files, manifest

    def _write(self, kind: str, filename: str, files: dict[str, bytes], summary: dict[str, Any]) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        eid = f"exp-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        path = self.root / f"{eid}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, data)
        summary = {**summary, "filename": filename, "files": len(files)}
        self.db.exec("INSERT INTO exports(export_id,created_at,kind,summary_json,path,size) VALUES(?,?,?,?,?,?)",
                     (eid, datetime.now(timezone.utc).isoformat(), kind, json.dumps(summary), path.name, path.stat().st_size))
        return self.get(eid)  # type: ignore[return-value]

    def export_variant(self, job_id: str, variant_id: str | None = None, *, include_unapproved: bool = False,
                       allow_mock: bool = False) -> dict[str, Any]:
        detail = self.jobs.detail(job_id)
        vid = variant_id or detail["approved_variant_id"]
        if not vid:
            raise ExportError(400, "no_variant", "No variant given and none is approved for this job.")
        variant = next((v for v in detail["variants"] if v["variant_id"] == vid), None)
        if variant is None:
            raise ExportError(404, "not_found", "Variant not found.")
        files, manifest = self._variant_files(detail, variant, include_unapproved=include_unapproved, allow_mock=allow_mock)
        filename = (f"{slug(detail['collection'], 20)}_{slug(detail['genre'], 20)}_Variant{vid}_"
                    f"{'Approved' if variant['approved'] else 'Unapproved'}.zip")
        return self._write("variant", filename, files, {
            "job_ids": [job_id], "genre": detail["genre"], "variant_id": vid, "approved": variant["approved"],
            "final_eligible": detail.get("final_eligible", True), "final_ineligible_reason": detail.get("final_ineligible_reason"),
            "contains_mock_images": manifest["contains_mock_images"]})

    # -------------------------------------------------------------------------- collection
    def overview(self, collection: str = "Movie Collection") -> dict[str, Any]:
        genres = [g["genre"] for g in supported_genres()]
        jobs = [j for j in self.jobs.list(500) if j["collection"] == collection]
        out, blockers_all = [], []
        for genre in genres:
            all_gj = [j for j in jobs if j["genre"] == genre]
            gj = [j for j in all_gj if j.get("final_eligible", True)]  # only full 10-card sets with 0 golden cards
            skipped = [{"job_id": j["job_id"], "reason": j.get("final_ineligible_reason")} for j in all_gj
                       if not j.get("final_eligible", True)]
            row: dict[str, Any] = {"genre": genre, "jobs": len(gj), "job_id": None, "status": None, "approved_variant_id": None,
                                   "generated": 0, "ready": False, "blockers": [], "contains_mock_images": False,
                                   "ineligible_jobs": skipped}
            pick = next((j for j in gj if j["approved_variant_id"]), gj[0] if gj else None)
            if pick is None:
                row["blockers"].append(
                    f"no final-eligible job yet ({len(skipped)} job(s) ignored: " + (skipped[0]["reason"] or "") + ")"
                    if skipped else "no job generated yet")
            else:
                d = self.jobs.detail(pick["job_id"])
                row.update(job_id=pick["job_id"], status=d["status"], approved_variant_id=d["approved_variant_id"])
                v = next((x for x in d["variants"] if x["variant_id"] == d["approved_variant_id"]), None)
                if v is None:
                    row["blockers"].append("no variant approved")
                else:
                    store = self.jobs.gen.store
                    row["generated"] = v["set_qa"]["generated"]
                    row["contains_mock_images"] = any((store.metadata(c["artifact_id"]) or {}).get("is_mock") for c in v["cards"] if c["artifact_id"])
                    if v["set_qa"]["generated"] < v["set_qa"]["total"]:
                        row["blockers"].append("approved variant is incomplete")
                    if v["set_qa"]["status"] == "failed":
                        row["blockers"].append("set QA failed")
                    if row["contains_mock_images"]:
                        row["blockers"].append("contains mock images")
                    row["set_qa"] = v["set_qa"]["status"]
            row["ready"] = not row["blockers"]
            blockers_all += [f"{genre}: {b}" for b in row["blockers"]]
            out.append(row)
        return {"collection": collection, "genres": out, "final_ready": all(r["ready"] for r in out),
                "blockers": blockers_all, "note": "Final deliverable: one approved, complete, non-mock 10-card standard set per genre. Golden cards are excluded: "
                "jobs with golden cards or fewer than 10 cards stay exportable as ordinary variant ZIPs but are not counted here."}

    def export_collection(self, collection: str = "Movie Collection", *, include_unapproved: bool = False,
                          allow_mock: bool = False, allow_partial: bool = False) -> dict[str, Any]:
        ov = self.overview(collection)
        files: dict[str, bytes] = {}
        summaries, problems = [], []
        for i, g in enumerate(ov["genres"], 1):
            if not g["job_id"]:
                problems.append(f"{g['genre']}: no job")
                continue
            detail = self.jobs.detail(g["job_id"])
            vid = g["approved_variant_id"] or next((v["variant_id"] for v in detail["variants"] if v["set_qa"]["generated"] == v["set_qa"]["total"]), None)
            variant = next((v for v in detail["variants"] if v["variant_id"] == vid), None)
            if variant is None:
                problems.append(f"{g['genre']}: no complete variant")
                continue
            try:
                f, manifest = self._variant_files(detail, variant, include_unapproved=include_unapproved, allow_mock=allow_mock)
            except ExportError as exc:
                problems.append(f"{g['genre']}: {exc.message}")
                continue
            for name, data in f.items():
                files[f"{i:02d}_{slug(g['genre'])}/{name}"] = data
            summaries.append({"genre": g["genre"], "job_id": g["job_id"], "variant_id": variant["variant_id"],
                              "approved": variant["approved"], "contains_mock_images": manifest["contains_mock_images"]})
        if problems and not allow_partial:
            raise ExportError(400, "collection_not_ready", "Collection is not ready: " + " | ".join(problems), {"blockers": problems})
        if not summaries:
            raise ExportError(400, "collection_not_ready", "Nothing to export.", {"blockers": problems})
        files["collection-manifest.json"] = json.dumps({
            "format": "playrix-collection-studio-collection-export/1", "spec_version": SPEC_VERSION, "app_version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(), "collection": collection, "sets": summaries,
            "missing_or_blocked": problems, "final_ready": ov["final_ready"], "limitations": LIMITATIONS}, indent=2).encode()
        files["README.txt"] = (f"{collection}: {len(summaries)} set(s), one folder per genre.\n"
                               + ("PARTIAL export: " + " | ".join(problems) + "\n" if problems else "")
                               + "Each folder = artwork/, manifest.json, set-plan.json, QA-report.json, contact-sheet.png.\n"
                               + LIMITATIONS + "\n").encode()
        return self._write("collection", f"{slug(collection, 30)}_final-bundle.zip", files,
                           {"collection": collection, "sets": summaries, "partial": bool(problems),
                            "contains_mock_images": any(s["contains_mock_images"] for s in summaries)})

    # ----------------------------------------------------------------------------- records
    def get(self, export_id: str) -> dict[str, Any] | None:
        r = self.db.one("SELECT * FROM exports WHERE export_id=?", (export_id,))
        if r is None:
            return None
        return {"export_id": r["export_id"], "created_at": r["created_at"], "kind": r["kind"], "size": r["size"],
                "download_url": f"/api/exports/{r['export_id']}/download", **json.loads(r["summary_json"])}

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT export_id FROM exports ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self.get(r["export_id"]) for r in rows]  # type: ignore[misc]

    def path(self, export_id: str) -> tuple[Path, str] | None:
        if not re.fullmatch(r"exp-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}", export_id):
            return None
        r = self.get(export_id)
        p = self.root / f"{export_id}.zip"
        return (p, r["filename"]) if r and p.exists() else None
