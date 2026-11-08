"""Reference image upload/validation/storage (spec §6.5, §19.3).

Validation is by decoding with Pillow (file signature), not by trusting MIME or extension.
Stored files use generated IDs only; user filenames are never used as paths.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..providers.base import ReferenceImage

REF_TYPES = ("style", "category_1", "category_2", "category_3", "category_4", "category_5",
             "composition", "negative")
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
MAX_DIMENSION = 8192
MAX_STORED_LONG_SIDE = 2048
THUMB_SIZE = 256
_REF_RE = re.compile(r"^ref-[0-9a-f]{10}$")


class ReferenceError_(Exception):
    def __init__(self, message: str, code: str = "invalid_reference") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def is_valid_ref_id(v: str) -> bool:
    return bool(_REF_RE.match(v))


def _safe_name(name: str) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    return re.sub(r"[^\w.\- ]", "_", base)[:80] or "upload"


class ReferenceManager:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root
        self.max_bytes = max_bytes

    def _p(self, ref_id: str, suffix: str) -> Path:
        if not is_valid_ref_id(ref_id):
            raise ReferenceError_("invalid reference id", "invalid_id")
        return self.root / f"{ref_id}{suffix}"

    def save_upload(self, filename: str, data: bytes, ref_type: str) -> dict[str, Any]:
        if ref_type not in REF_TYPES:
            raise ReferenceError_(f"ref_type must be one of {', '.join(REF_TYPES)}", "bad_type")
        if not data:
            raise ReferenceError_("empty file", "empty")
        if len(data) > self.max_bytes:
            raise ReferenceError_(
                f"file is larger than {self.max_bytes // (1024 * 1024)} MB", "too_large"
            )
        try:
            with Image.open(io.BytesIO(data)) as probe:
                fmt = probe.format
                w, h = probe.size
            if fmt not in ALLOWED_FORMATS:
                raise ReferenceError_("only PNG, JPEG and WebP images are accepted", "bad_format")
            if max(w, h) > MAX_DIMENSION:
                raise ReferenceError_(f"image side exceeds {MAX_DIMENSION}px", "too_big_dims")
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
                if max(im.size) > MAX_STORED_LONG_SIDE:
                    im.thumbnail((MAX_STORED_LONG_SIDE, MAX_STORED_LONG_SIDE))
                norm = io.BytesIO()
                im.save(norm, format="PNG")  # re-encode: strips EXIF/metadata
                width, height = im.size
                thumb = im.copy()
                thumb.thumbnail((THUMB_SIZE, THUMB_SIZE))
                tbuf = io.BytesIO()
                thumb.save(tbuf, format="PNG")
        except ReferenceError_:
            raise
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
            raise ReferenceError_("file is not a valid image", "not_an_image") from None

        png = norm.getvalue()
        sha = hashlib.sha256(png).hexdigest()
        for existing in self.list():
            if existing["sha256"] == sha and existing["ref_type"] == ref_type:
                return existing  # dedupe identical re-uploads

        ref_id = f"ref-{secrets.token_hex(5)}"
        meta = {
            "ref_id": ref_id,
            "ref_type": ref_type,
            "original_name": _safe_name(filename),
            "sha256": sha,
            "width": width,
            "height": height,
            "bytes": len(png),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        self._p(ref_id, ".png").write_bytes(png)
        self._p(ref_id, "_thumb.png").write_bytes(tbuf.getvalue())
        self._p(ref_id, ".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta

    def get(self, ref_id: str) -> dict[str, Any] | None:
        p = self._p(ref_id, ".json")
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        out = []
        for f in sorted(self.root.glob("ref-*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(out, key=lambda m: m["created_at"])

    def delete(self, ref_id: str) -> bool:
        existed = self.get(ref_id) is not None
        for suffix in (".png", "_thumb.png", ".json"):
            self._p(ref_id, suffix).unlink(missing_ok=True)
        return existed

    def image_path(self, ref_id: str, thumb: bool = False) -> Path | None:
        p = self._p(ref_id, "_thumb.png" if thumb else ".png")
        return p if p.exists() else None

    def load(self, ref_id: str) -> ReferenceImage:
        meta = self.get(ref_id)
        path = self.image_path(ref_id)
        if meta is None or path is None:
            raise ReferenceError_(f"reference {ref_id} not found", "not_found")
        return ReferenceImage(
            ref_id=ref_id,
            png_bytes=path.read_bytes(),
            sha256=meta["sha256"],
            role=meta["ref_type"],
        )
