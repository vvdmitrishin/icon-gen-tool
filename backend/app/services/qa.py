"""Deterministic technical QA and set-level QA (spec §13, Stage 6 and 9).

Only structural facts are checked here. It deliberately does NOT judge style, and it cannot detect
rendered text or frames (that needs OCR/vision, which the spec keeps out of the MVP): those stay
with the human art review.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

MIN_SIDE_FAIL = 256
MIN_SIDE_WARN = 512


def _chk(cid: str, name: str, status: str, detail: str) -> dict[str, str]:
    return {"id": cid, "name": name, "status": status, "detail": detail}


def _verdict(checks: list[dict[str, str]]) -> str:
    if any(c["status"] == "fail" for c in checks):
        return "failed"
    if any(c["status"] == "warn" for c in checks):
        return "flagged"
    return "passed"


def qa_card(png_path: Path, meta: dict[str, Any], expected_aspect: str = "1:1") -> dict[str, Any]:
    """Per-image technical QA. Returns {status: passed|flagged|failed, checks: [...]}."""
    checks: list[dict[str, str]] = []
    try:
        data = png_path.read_bytes()
    except OSError as exc:
        return {"status": "failed", "checks": [_chk("TQ-01", "File readable", "fail", f"cannot read file: {exc.__class__.__name__}")]}

    try:
        with Image.open(png_path) as im:
            im.load()
            w, h = im.size
            gray = im.convert("L")
        checks.append(_chk("TQ-01", "File readable", "pass", f"{w}×{h} decoded"))
    except Exception as exc:
        return {"status": "failed", "checks": [_chk("TQ-01", "File readable", "fail", f"cannot decode image: {exc.__class__.__name__}")]}

    checks.append(_chk("TQ-02", "PNG format", "pass" if data[:8] == b"\x89PNG\r\n\x1a\n" else "fail",
                       "PNG signature present" if data[:8] == b"\x89PNG\r\n\x1a\n" else "file is not a PNG"))

    side = min(w, h)
    # the producer may deliberately ask for small icons (24..1024px): thresholds never exceed the requested size
    requested = int(meta.get("image", {}).get("output_size_requested") or 0) or None
    fail_below = min(MIN_SIDE_FAIL, requested) if requested else MIN_SIDE_FAIL
    warn_below = min(MIN_SIDE_WARN, requested) if requested else MIN_SIDE_WARN
    checks.append(_chk("TQ-03", "Resolution", "fail" if side < fail_below else "warn" if side < warn_below else "pass",
                       f"shorter side {side}px (warn < {warn_below}, fail < {fail_below}"
                       + (f"; requested output {requested}px)" if requested else ")")))

    try:
        aw, ah = (int(x) for x in expected_aspect.split(":"))
        diff = abs((w / h) - (aw / ah)) / (aw / ah)
        checks.append(_chk("TQ-04", "Aspect ratio", "pass" if diff <= 0.02 else "warn" if diff <= 0.10 else "fail",
                           f"{w}:{h} vs requested {expected_aspect} (deviation {diff * 100:.1f}%)"))
    except (ValueError, ZeroDivisionError):
        checks.append(_chk("TQ-04", "Aspect ratio", "warn", f"unparseable requested ratio '{expected_aspect}'"))

    std = ImageStat.Stat(gray).stddev[0]
    checks.append(_chk("TQ-05", "Not blank", "fail" if std < 3 else "warn" if std < 12 else "pass",
                       f"luminance spread {std:.1f} (fail < 3, warn < 12)"))

    missing = [k for k, ok in {
        "artifact_id": bool(meta.get("artifact_id")),
        "provider.id": bool(meta.get("provider", {}).get("id")),
        "provider.model": bool(meta.get("provider", {}).get("model")),
        "prompt.effective": bool(meta.get("prompt", {}).get("effective")),
        "card.object_name": bool(meta.get("card", {}).get("object_name")),
        "image.sha256": bool(meta.get("image", {}).get("sha256")),
    }.items() if not ok]
    checks.append(_chk("TQ-06", "Metadata complete", "fail" if missing else "pass",
                       f"missing: {', '.join(missing)}" if missing else "all provenance fields present"))

    sha = hashlib.sha256(data).hexdigest()
    ok = sha == meta.get("image", {}).get("sha256")
    checks.append(_chk("TQ-07", "File integrity", "pass" if ok else "fail",
                       "SHA-256 matches provenance" if ok else "SHA-256 differs from provenance (file changed?)"))

    kb = len(data) / 1024
    min_kb = 5 if side >= MIN_SIDE_FAIL else 0.05  # a legitimate 24px icon is only a few hundred bytes
    checks.append(_chk("TQ-08", "File size", "pass" if min_kb <= kb <= 20480 else "warn", f"{kb:.1f} KB"))

    if meta.get("is_mock"):
        checks.append(_chk("TQ-09", "Real artwork", "warn", "mock placeholder - not produced by an AI model"))
    return {"status": _verdict(checks), "checks": checks}


def qa_variant(variant_id: str, cards: list[dict[str, Any]], distribution: dict[int, int],
               expected_total: int | None = None) -> dict[str, Any]:
    """Set-level QA for one variant. `cards` rows need: card_id, category, object_name, status,
    sha256, width, height, qa_status, art_review."""
    checks: list[dict[str, str]] = []
    done = [c for c in cards if c["status"] == "done" and c.get("sha256")]
    planned_total = expected_total or sum(distribution.get(k, 0) for k in (1, 2, 3, 4, 5)) or len(cards)
    checks.append(_chk("SQ-01", "Set complete", "pass" if len(done) == planned_total else "warn",
                       f"{len(done)}/{planned_total} cards generated"))

    got = {k: sum(1 for c in cards if c["category"] == k) for k in (1, 2, 3, 4, 5)}
    want = {k: distribution.get(k, 0) for k in (1, 2, 3, 4, 5)}
    checks.append(_chk("SQ-02", "Category distribution (HR-08)", "pass" if got == want else "fail",
                       f"planned {list(got.values())} vs requested {list(want.values())} (Cat.1-5)"))

    names = [c["object_name"].strip().lower() for c in cards]
    checks.append(_chk("SQ-03", "Unique objects (HR-02)", "pass" if len(set(names)) == len(names) else "fail",
                       "all names unique" if len(set(names)) == len(names) else "duplicate object names"))

    golden = [c for c in cards if c["category"] == 5]
    gold_want = distribution.get(5, 0)
    checks.append(_chk("SQ-04", "Golden cards match plan (HR-03/04)", "pass" if len(golden) == gold_want else "fail",
                       f"{len(golden)} golden card(s) planned, {gold_want} requested"))

    hashes: dict[str, list[str]] = {}
    for c in done:
        hashes.setdefault(c["sha256"], []).append(c["card_id"])
    dups = [v for v in hashes.values() if len(v) > 1]
    checks.append(_chk("SQ-05", "No duplicate artwork (HR-09)", "fail" if dups else "pass",
                       f"{len(dups)} duplicate file hash group(s)" if dups else "all file hashes distinct"))

    qa = [c.get("qa_status", "not_run") for c in done]
    if "failed" in qa:
        checks.append(_chk("SQ-06", "Card technical QA", "fail", f"{qa.count('failed')} card(s) failed technical QA"))
    elif "flagged" in qa or "not_run" in qa:
        checks.append(_chk("SQ-06", "Card technical QA", "warn",
                           f"{qa.count('flagged')} flagged, {qa.count('not_run')} not run"))
    else:
        checks.append(_chk("SQ-06", "Card technical QA", "pass", f"{qa.count('passed')} card(s) passed"))

    dims = {(c["width"], c["height"]) for c in done if c.get("width")}
    checks.append(_chk("SQ-07", "Consistent dimensions", "pass" if len(dims) <= 1 else "warn",
                       "uniform size" if len(dims) <= 1 else f"{len(dims)} different sizes"))

    rejected = [c for c in done if c.get("art_review") == "rejected"]
    checks.append(_chk("SQ-08", "No rejected cards", "fail" if rejected else "pass",
                       f"{len(rejected)} card(s) rejected in art review" if rejected else "none rejected"))

    review = {k: sum(1 for c in done if c.get("art_review") == k) for k in ("approved", "pending", "rejected")}
    return {"variant_id": variant_id, "status": _verdict(checks), "checks": checks,
            "generated": len(done), "total": len(cards), "art_review": review}
