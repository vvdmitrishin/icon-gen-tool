"""Staged live Gemini smoke test through the same service layer the app uses
(prompt compiler → cost guard → provider → PNG + metadata + ledger).

Spends real money (~1 image per run), so it refuses to run without --yes.

  backend\\.venv\\Scripts\\python scripts\\smoke_gemini.py --yes
  backend\\.venv\\Scripts\\python scripts\\smoke_gemini.py --yes --ref path\\to\\reference.png --ref-type category_1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
# console-safe output on Windows (cp1252)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import load_settings  # noqa: E402
from app.providers.model_registry import ModelRegistry  # noqa: E402
from app.services.cost_guard import CostGuard  # noqa: E402
from app.services.generation import GenerateCardRequest, GenerationFailure, GenerationService  # noqa: E402
from app.services.reference_manager import ReferenceManager  # noqa: E402
from app.services.storage import ArtifactStore  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="confirm that this call may cost money")
    ap.add_argument("--ref", type=Path, help="optional reference image to send")
    ap.add_argument("--ref-type", default="style")
    ap.add_argument("--object", default="Retro Space Helmet")
    ap.add_argument("--genre", default="Science Fiction")
    ap.add_argument("--category", type=int, default=2, choices=[1, 2, 3, 4])
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    s = load_settings()
    if not s.gemini_configured:
        print("GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in (never commit it).")
        return 2
    registry = ModelRegistry(s)
    guard = CostGuard(s)
    refs = ReferenceManager(s.reference_root, s.max_upload_bytes)
    svc = GenerationService(s, registry, guard, ArtifactStore(s.artifact_root), refs)

    model = args.model or s.default_model
    ref_ids: list[str] = []
    if args.ref:
        ref_ids.append(refs.save_upload(args.ref.name, args.ref.read_bytes(), args.ref_type)["ref_id"])

    req = GenerateCardRequest(
        genre=args.genre, object_name=args.object, category=args.category,
        provider="gemini", model=model, reference_ids=ref_ids,
        reference_policy_accepted=bool(ref_ids), confirmed=args.yes,
    )
    pv = svc.preview(req)
    print(f"model={model} refs={len(ref_ids)} estimate=${pv['estimated_cost_usd']:.3f} "
          f"lifetime est. spent=${pv['budget']['spent_estimated_usd']:.3f}")
    if not args.yes:
        print("Dry run only. Re-run with --yes to spend the estimated amount.")
        return 0
    try:
        meta = await svc.generate(req)
    except GenerationFailure as exc:
        print(f"FAILED [{exc.code}] {exc.message}")
        return 1
    print("SUCCESS:", ROOT / "data" / "artifacts" / meta["image"]["file"])
    print(json.dumps({k: meta[k] for k in ("artifact_id", "origin", "timing", "cost")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
