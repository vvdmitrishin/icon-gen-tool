"""Filesystem artifact store: <id>.png + <id>.json sidecar (immutable, atomic writes)."""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{5,63}$")


def new_artifact_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"art-{ts}-{secrets.token_hex(3)}"


def is_valid_id(value: str) -> bool:
    return bool(_ID_RE.match(value))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _paths(self, artifact_id: str) -> tuple[Path, Path]:
        if not is_valid_id(artifact_id):
            raise ValueError("invalid artifact id")
        return self.root / f"{artifact_id}.png", self.root / f"{artifact_id}.json"

    def save(self, artifact_id: str, png: bytes, metadata: dict[str, Any], original: bytes | None = None) -> None:
        png_path, json_path = self._paths(artifact_id)
        if png_path.exists():
            raise FileExistsError("artifact versions are immutable")
        if original is not None:  # the untouched provider image, kept next to the delivered file
            _atomic_write(self.root / f"{artifact_id}.original.png", original)
        _atomic_write(png_path, png)
        _atomic_write(json_path, json.dumps(metadata, indent=2, ensure_ascii=False).encode())

    def image_path(self, artifact_id: str) -> Path | None:
        p, _ = self._paths(artifact_id)
        return p if p.exists() else None

    def metadata(self, artifact_id: str) -> dict[str, Any] | None:
        _, j = self._paths(artifact_id)
        return json.loads(j.read_text(encoding="utf-8")) if j.exists() else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        files = sorted(self.root.glob("art-*.json"), reverse=True)[:limit]
        out = []
        for f in files:
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return out
