"""Import a manually created sample image with honest provenance (spec §0.3.1, §6.8 origin badges).

Imported images are labelled `Imported · <model>` and never pass as pipeline output: no provider call,
no cost, `is_imported: true`. Useful for comparing other models against the benchmark.
"""
from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from typing import Any

from PIL import Image, UnidentifiedImageError

from .. import __version__
from .storage import ArtifactStore, new_artifact_id


class ImportError_(Exception):
    def __init__(self, message: str, code: str = "invalid_import") -> None:
        super().__init__(message)
        self.message, self.code = message, code


def import_sample(store: ArtifactStore, data: bytes, *, provider_label: str, model_label: str,
                  prompt_note: str, object_name: str, max_bytes: int) -> dict[str, Any]:
    provider_label, model_label = provider_label.strip()[:60], model_label.strip()[:60]
    if not provider_label or not model_label:
        raise ImportError_("Provider and model labels are required so the origin stays honest.", "label_required")
    if not data or len(data) > max_bytes:
        raise ImportError_("Empty or too large image.", "bad_size")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP"):
                raise ImportError_("Only PNG, JPEG and WebP images are accepted.", "bad_format")
            im.load()
            w, h = im.size
            buf = io.BytesIO()
            im.convert("RGBA" if "A" in im.getbands() else "RGB").save(buf, format="PNG")
    except ImportError_:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ImportError_("File is not a valid image.", "not_an_image") from None
    png = buf.getvalue()
    aid = new_artifact_id()
    name = (object_name.strip() or "Imported sample")[:80]
    meta = {
        "schema_version": 1, "artifact_id": aid, "created_at": datetime.now(timezone.utc).isoformat(),
        "app_version": __version__, "origin": f"Imported · {provider_label} {model_label}",
        "is_mock": False, "is_imported": True,
        "image": {"file": f"{aid}.png", "url": f"/api/artifacts/{aid}/image", "width": w, "height": h,
                  "bytes": len(png), "sha256": hashlib.sha256(png).hexdigest(), "aspect_ratio_requested": "n/a"},
        "card": {"collection": "Imported", "genre": "Imported", "object_name": name, "object_description": "", "category": 0},
        "context": {}, "provider": {"id": "imported", "model": model_label, "label": f"{provider_label} {model_label}",
                                    "model_status": "manual_import", "finish_reason": None, "model_notes": [], "request_summary": {}},
        "prompt": {"effective": prompt_note.strip()[:2000], "layers": {}, "template_version": "n/a", "style_bible_version": "n/a",
                   "category_rules_version": "n/a", "template_versions": {}, "repair_note": "", "free_wishes": "", "warnings": []},
        "references": [], "reference_policy_accepted": False,
        "cost": {"estimated_usd": 0.0, "is_estimate": True, "usage_metadata": {}, "note": "Imported manually; no generation cost recorded."},
        "timing": {"latency_ms": 0, "attempts": 0}, "review": {"technical_qa": "not_run", "art_review": "pending"},
    }
    store.save(aid, png, meta)
    return meta
