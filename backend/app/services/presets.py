"""Templates screen: saved request presets (spec §6.2 'Templates'). Presets hold only structural
choices (genre, variants, scope, distribution, wishes); never credentials or provider keys."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .db import Db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS presets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
  payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
"""
ALLOWED_KEYS = {"genre", "collection", "number_of_variants", "number_of_cards", "gold_cards", "output_size", "scope",
                "category_distribution", "wishes", "aspect_ratio"}
OUTPUT_SIZES = (24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024)

BUILTIN = {
    "Movie · Science Fiction pilot": {"genre": "Science Fiction", "number_of_variants": 1, "scope": "pilot",
                                      "category_distribution": {"mode": "auto_balanced"}, "wishes": ""},
    "Movie · Detective pilot": {"genre": "Detective", "number_of_variants": 1, "scope": "pilot",
                                "category_distribution": {"mode": "auto_balanced"}, "wishes": ""},
    "Movie · Fantasy pilot": {"genre": "Fantasy", "number_of_variants": 1, "scope": "pilot",
                              "category_distribution": {"mode": "auto_balanced"}, "wishes": ""},
    "Movie · final deliverable (3 variants, full)": {"genre": "Science Fiction", "number_of_variants": 3, "scope": "full",
                                                     "category_distribution": {"mode": "auto_balanced"}, "wishes": ""},
}


class PresetError(ValueError):
    def __init__(self, message: str, code: str = "invalid_preset") -> None:
        super().__init__(message)
        self.message, self.code = message, code


def validate_payload(p: dict[str, Any]) -> dict[str, Any]:
    extra = set(p) - ALLOWED_KEYS
    if extra:
        raise PresetError(f"Unsupported preset field(s): {', '.join(sorted(extra))}.")
    out = dict(p)
    if "number_of_variants" in out and not (isinstance(out["number_of_variants"], int) and 1 <= out["number_of_variants"] <= 5):
        raise PresetError("number_of_variants must be 1-5.")
    for key, lo, hi in (("number_of_cards", 1, 10), ("gold_cards", 0, 10)):
        if key in out and not (isinstance(out[key], int) and not isinstance(out[key], bool) and lo <= out[key] <= hi):
            raise PresetError(f"{key} must be {lo}-{hi}.")
    if "output_size" in out and out["output_size"] not in OUTPUT_SIZES:
        raise PresetError("output_size must be one of " + ", ".join(str(x) for x in OUTPUT_SIZES) + ".")
    if "collection" in out and not (isinstance(out["collection"], str) and 1 <= len(out["collection"].strip()) <= 80):
        raise PresetError("collection must be text of 1-80 characters.")
    if "scope" in out and out["scope"] not in ("pilot", "full"):
        raise PresetError("scope must be 'pilot' or 'full'.")
    if "wishes" in out and (not isinstance(out["wishes"], str) or len(out["wishes"]) > 500):
        raise PresetError("wishes must be text up to 500 characters.")
    d = out.get("category_distribution")
    if d is not None and not (isinstance(d, dict) and d.get("mode") in ("auto_balanced", "custom")):
        raise PresetError("category_distribution.mode must be auto_balanced or custom.")
    return out


class PresetStore:
    def __init__(self, db: Db) -> None:
        self.db = db
        db.script(_SCHEMA)
        for name, payload in BUILTIN.items():
            if not db.one("SELECT 1 AS x FROM presets WHERE name=?", (name,)):
                db.exec("INSERT INTO presets(name,kind,payload_json,created_at) VALUES(?,?,?,?)",
                        (name, "builtin", json.dumps(payload), datetime.now(timezone.utc).isoformat()))

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT * FROM presets ORDER BY kind, name")
        return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "payload": json.loads(r["payload_json"]),
                 "created_at": r["created_at"]} for r in rows]

    def create(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        name = (name or "").strip()
        if not 1 <= len(name) <= 60:
            raise PresetError("Name must be 1-60 characters.")
        payload = validate_payload(payload)
        if self.db.one("SELECT 1 AS x FROM presets WHERE name=?", (name,)):
            raise PresetError("A template with this name already exists.", "duplicate_name")
        pid = self.db.exec("INSERT INTO presets(name,kind,payload_json,created_at) VALUES(?,?,?,?)",
                           (name, "user", json.dumps(payload), datetime.now(timezone.utc).isoformat()))
        return next(p for p in self.list() if p["id"] == pid)

    def delete(self, preset_id: int) -> bool:
        row = self.db.one("SELECT kind FROM presets WHERE id=?", (preset_id,))
        if row is None:
            return False
        if row["kind"] == "builtin":
            raise PresetError("Built-in templates cannot be deleted.", "builtin_protected")
        self.db.exec("DELETE FROM presets WHERE id=?", (preset_id,))
        return True
