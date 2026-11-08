"""MINIMAL live smoke test for the Cloudflare Workers AI models (FLUX.2 Klein 4B, FLUX.1 Schnell).

Sends ONE image request per run through the same code path the app uses (prompt compiler -> adapter ->
PNG + provenance), so it proves real API compatibility. Run only after you approved a live call.
It uses the credentials you saved in Settings > API Connections (Windows Credential Manager) or, for
development, the .env file. No secret is ever printed.

Free-tier note: the Workers Free plan has a daily allocation of 10,000 Neurons; one image uses roughly
60-110 Neurons. If the allocation is used up the call fails with a clear quota message; this script never
changes your plan and never falls back to another provider.

  backend\\.venv\\Scripts\\python scripts\\smoke_cloudflare.py --model klein            # dry run (prints what it would do)
  backend\\.venv\\Scripts\\python scripts\\smoke_cloudflare.py --model klein --yes      # ONE text-to-image request
  backend\\.venv\\Scripts\\python scripts\\smoke_cloudflare.py --model klein --yes --ref some.png --ref-type style
  backend\\.venv\\Scripts\\python scripts\\smoke_cloudflare.py --model schnell --yes     # text-only model (no references)
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
from app.providers.cloudflare_models import KLEIN_ID, SCHNELL_ID  # noqa: E402
from app.providers.model_registry import ModelRegistry  # noqa: E402
from app.services.cost_guard import CostGuard  # noqa: E402
from app.services.credentials import KeyringCredentialStore  # noqa: E402
from app.services.generation import GenerateCardRequest, GenerationFailure, GenerationService  # noqa: E402
from app.services.reference_manager import ReferenceManager  # noqa: E402
from app.services.storage import ArtifactStore  # noqa: E402

MODELS = {"klein": KLEIN_ID, "schnell": SCHNELL_ID}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=sorted(MODELS), required=True)
    ap.add_argument("--yes", action="store_true", help="really send ONE request to Cloudflare")
    ap.add_argument("--ref", type=Path, help="optional reference image (klein only)")
    ap.add_argument("--ref-type", default="style")
    ap.add_argument("--object", default="Retro Space Helmet")
    ap.add_argument("--genre", default="Science Fiction")
    ap.add_argument("--category", type=int, default=2, choices=[1, 2, 3, 4])
    args = ap.parse_args()

    s = load_settings()
    registry = ModelRegistry(s, credentials=KeyringCredentialStore())
    model_id = MODELS[args.model]
    info = next(m for m in registry.models() if m.provider_id == "cloudflare" and m.model_id == model_id)
    print(f"model={model_id} selectable={info.selectable} status={info.status} plan={s.cloudflare_plan}")
    if not info.selectable:
        print("Not usable yet:", info.disabled_reason)
        return 2
    if args.ref and args.model == "schnell":
        print("FLUX.1 Schnell does not accept reference images; drop --ref.")
        return 2
    if not args.yes:
        print("Dry run only. Re-run with --yes to send ONE request (free-tier Neurons are used; nothing else is changed).")
        return 0

    refs = ReferenceManager(s.reference_root, s.max_upload_bytes)
    ref_ids: list[str] = []
    if args.ref:
        ref_ids.append(refs.save_upload(args.ref.name, args.ref.read_bytes(), args.ref_type)["ref_id"])
    svc = GenerationService(s, registry, CostGuard(s), ArtifactStore(s.artifact_root), refs)
    req = GenerateCardRequest(genre=args.genre, object_name=args.object, category=args.category, provider="cloudflare",
                              model=model_id, reference_ids=ref_ids, reference_policy_accepted=bool(ref_ids),
                              confirmed=s.cloudflare_plan == "paid")
    try:
        meta = await svc.generate(req)
    except GenerationFailure as exc:
        print(f"FAILED [{exc.code}] {exc.message}")
        return 1
    print("SUCCESS:", ROOT / "data" / "artifacts" / meta["image"]["file"])
    print(json.dumps({"origin": meta["origin"], "size": [meta["image"]["width"], meta["image"]["height"]],
                      "latency_ms": meta["timing"]["latency_ms"], "references_sent": len(meta["references"]),
                      "estimated_usd": meta["cost"]["estimated_usd"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
