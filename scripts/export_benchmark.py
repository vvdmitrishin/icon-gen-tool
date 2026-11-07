"""Freeze a finished job as a local, read-only benchmark for future model comparisons.

Copies the original PNGs, their provenance sidecars, the reference images actually used, the job
record (plan, settings, events) and the cost-ledger rows into benchmarks/<name>/ with a
manifest.json (SHA-256 per file). benchmarks/ is git-ignored: it may hold confidential references.

  backend\\.venv\\Scripts\\python scripts\\export_benchmark.py --job job-... --name phase3_gemini_baseline
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import load_settings  # noqa: E402
from app.services.jobs import JobStore  # noqa: E402
from app.services.reference_manager import ReferenceManager  # noqa: E402
from app.services.storage import ArtifactStore  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--no-references", action="store_true", help="do not copy reference images")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    s = load_settings()
    store, jobs = ArtifactStore(s.artifact_root), JobStore(s.database_path)
    refs = ReferenceManager(s.reference_root, s.max_upload_bytes)
    job = jobs.get_job(args.job)
    if job is None:
        print("job not found")
        return 2
    out = ROOT / "benchmarks" / args.name
    if out.exists():
        print(f"{out} already exists; refusing to overwrite a frozen benchmark")
        return 3
    for d in ("images", "metadata", "references"):
        (out / d).mkdir(parents=True)

    ledger = []
    if s.ledger_path.exists():
        ledger = [json.loads(line) for line in s.ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    cards_out, used_refs, files = [], {}, []
    for c in jobs.cards(args.job):
        if c["status"] != "done" or not c["artifact_id"]:
            continue
        meta = store.metadata(c["artifact_id"])
        png = store.image_path(c["artifact_id"])
        stem = f"{c['variant_id']}{c['position']:02d}_cat{c['category']}_{c['object_name'].lower().replace(' ', '-')}"
        shutil.copy2(png, out / "images" / f"{stem}.png")
        (out / "metadata" / f"{stem}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        for r in meta["references"]:
            used_refs[r["ref_id"]] = r
        cards_out.append({
            "card_id": c["card_id"], "variant": c["variant_id"], "position": c["position"],
            "category": c["category"], "object_name": c["object_name"],
            "object_description": c["object_description"], "artifact_id": c["artifact_id"],
            "image": f"images/{stem}.png", "metadata": f"metadata/{stem}.json",
            "sha256": meta["image"]["sha256"], "width": meta["image"]["width"], "height": meta["image"]["height"],
            "provider": meta["provider"]["id"], "model": meta["provider"]["model"],
            "model_status": meta["provider"]["model_status"],
            "generation_parameters": {
                "aspect_ratio_requested": meta["image"]["aspect_ratio_requested"],
                **meta["provider"]["request_summary"],
                "retries_allowed": s.max_card_retries, "attempts": meta["timing"]["attempts"],
                "reference_selection": c["reference_selection"],
            },
            "prompt": {"effective": meta["prompt"]["effective"], "layers": meta["prompt"]["layers"],
                       "template_version": meta["prompt"]["template_version"],
                       "style_bible_version": meta["prompt"]["style_bible_version"],
                       "category_rules_version": meta["prompt"]["category_rules_version"],
                       "free_wishes": meta["prompt"]["free_wishes"]},
            "references": meta["references"],
            "cost": meta["cost"], "latency_ms": meta["timing"]["latency_ms"],
            "finish_reason": meta["provider"]["finish_reason"], "created_at": meta["created_at"],
            "review": meta["review"],
        })

    ref_out = []
    if not args.no_references:
        for rid, r in used_refs.items():
            src = refs.image_path(rid)
            if src:
                shutil.copy2(src, out / "references" / f"{rid}.png")
                m = refs.get(rid) or {}
                (out / "references" / f"{rid}.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
                ref_out.append({"ref_id": rid, "role": r["role"], "sha256": r["sha256"], "file": f"references/{rid}.png",
                                "original_name": m.get("original_name"), "size": [m.get("width"), m.get("height")]})

    art_ids = {c["artifact_id"] for c in cards_out}
    ledger_rows = [row for row in ledger if row.get("artifact_id") in art_ids]
    (out / "job.json").write_text(json.dumps({k: v for k, v in job.items() if k != "plan"} | {"plan": job["plan"]},
                                             indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "events.json").write_text(json.dumps(jobs.events(args.job), indent=2), encoding="utf-8")
    (out / "ledger.json").write_text(json.dumps(ledger_rows, indent=2), encoding="utf-8")

    manifest = {
        "benchmark": args.name, "created_at": datetime.now(timezone.utc).isoformat(), "note": args.note,
        "source_job": args.job, "genre": job["plan"]["genre"], "seed": job["plan"]["seed"],
        "pools_version": job["plan"]["pools_version"], "distribution": job["plan"]["distribution"],
        "request_settings": job["request"], "card_count": len(cards_out),
        "estimated_total_cost_usd": round(sum(c["cost"]["estimated_usd"] for c in cards_out), 6),
        "cost_is_estimate": True,
        "contains_confidential_reference_assets": bool(ref_out),
        "how_to_compare": "Re-run the SAME cards (object, category, prompt layers, reference, aspect ratio) "
                          "on another provider/model and place results beside images/ by card_id.",
        "cards": cards_out, "references": ref_out,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "README.md").write_text(
        f"# Benchmark: {args.name}\n\nFrozen from job `{args.job}` ({job['plan']['genre']}). "
        f"{len(cards_out)} images, original PNGs + provenance. Read-only; do not edit.\n\n"
        "- `manifest.json`: per-card model/provider, prompts, category, generation parameters, references, estimated cost, SHA-256\n"
        "- `images/`: original PNGs - `metadata/`: original sidecars - `references/`: references actually sent (may be confidential)\n"
        "- `job.json`, `events.json`, `ledger.json`: job record, event log, cost ledger rows\n\n"
        "Costs are local estimates, not invoices. **Git-ignored; contains confidential reference material - do not publish.**\n",
        encoding="utf-8")

    # per-file hashes + read-only protection
    for p in sorted(out.rglob("*")):
        if p.is_file():
            files.append({"path": str(p.relative_to(out)).replace("\\", "/"), "sha256": sha256(p)})
    (out / "SHA256SUMS.json").write_text(json.dumps(files, indent=2), encoding="utf-8")
    for p in out.rglob("*"):
        if p.is_file():
            os.chmod(p, stat.S_IREAD)
    print(f"benchmark written: {out} ({len(cards_out)} cards, {len(ref_out)} reference(s), {len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
