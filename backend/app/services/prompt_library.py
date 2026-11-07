"""Prompt Library: versioned, editable prompt templates stored locally (spec §0.4, §6.3.1, ADR-012).

Works with no LLM: save/restore/versioning is plain application functionality. Every save creates a
new immutable version and becomes the active one; restore copies an older version forward.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import Db
from .prompt_compiler import TEMPLATE_NAMES, default_templates

MAX_TEXT = 3000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, version INTEGER NOT NULL, text TEXT NOT NULL,
  is_active INTEGER NOT NULL, created_at TEXT NOT NULL, author TEXT NOT NULL, note TEXT,
  UNIQUE(name, version)
);
"""

LABELS = {
    "system_style": "System style (Style Bible text)",
    "category_1": "Category 1 rules", "category_2": "Category 2 rules",
    "category_3": "Category 3 rules", "category_4": "Category 4 rules",
    "image_closing": "Closing constraints", "repair": "Repair prompt (regenerate with notes)",
}


class LibraryError(ValueError):
    def __init__(self, message: str, code: str = "invalid_template") -> None:
        super().__init__(message)
        self.message, self.code = message, code


class PromptLibrary:
    def __init__(self, db: Db) -> None:
        self.db = db
        db.script(_SCHEMA)
        self.seed()

    def seed(self) -> None:
        defaults = default_templates()
        for name in TEMPLATE_NAMES:
            if not self.db.one("SELECT 1 AS x FROM prompt_templates WHERE name=?", (name,)):
                self._insert(name, 1, defaults[name], "system", "built-in default")

    def _insert(self, name: str, version: int, text: str, author: str, note: str) -> None:
        self.db.exec("UPDATE prompt_templates SET is_active=0 WHERE name=?", (name,))
        self.db.exec("INSERT INTO prompt_templates(name,version,text,is_active,created_at,author,note) "
                     "VALUES(?,?,?,?,?,?,?)",
                     (name, version, text, 1, datetime.now(timezone.utc).isoformat(), author, note))

    @staticmethod
    def _validate(name: str, text: str) -> str:
        if name not in TEMPLATE_NAMES:
            raise LibraryError(f"Unknown template '{name}'.", "unknown_template")
        text = (text or "").strip()
        if not text:
            raise LibraryError("Template text cannot be empty.")
        if len(text) > MAX_TEXT:
            raise LibraryError(f"Template text is limited to {MAX_TEXT} characters.")
        if name == "repair" and "{note}" not in text:
            raise LibraryError("The repair template must contain the {note} placeholder.")
        return text

    def active(self) -> dict[str, dict[str, Any]]:
        rows = self.db.all("SELECT * FROM prompt_templates WHERE is_active=1")
        return {r["name"]: r for r in rows}

    def active_texts(self) -> tuple[dict[str, str], dict[str, int]]:
        a = self.active()
        return {n: r["text"] for n, r in a.items()}, {n: r["version"] for n, r in a.items()}

    def versions(self, name: str) -> list[dict[str, Any]]:
        if name not in TEMPLATE_NAMES:
            raise LibraryError(f"Unknown template '{name}'.", "unknown_template")
        return self.db.all("SELECT * FROM prompt_templates WHERE name=? ORDER BY version DESC", (name,))

    def listing(self) -> list[dict[str, Any]]:
        out = []
        active = self.active()
        defaults = default_templates()
        for name in TEMPLATE_NAMES:
            a = active[name]
            out.append({"name": name, "label": LABELS[name], "version": a["version"], "text": a["text"],
                        "author": a["author"], "created_at": a["created_at"], "note": a["note"],
                        "is_default_text": a["text"] == defaults[name],
                        "versions": [v["version"] for v in self.versions(name)]})
        return out

    def save(self, name: str, text: str, author: str = "Local User", note: str = "") -> dict[str, Any]:
        text = self._validate(name, text)
        cur = self.db.one("SELECT MAX(version) AS v FROM prompt_templates WHERE name=?", (name,))
        version = (cur["v"] if cur and cur["v"] else 0) + 1
        self._insert(name, version, text, (author or "Local User")[:60], (note or "")[:200])
        return self.db.one("SELECT * FROM prompt_templates WHERE name=? AND version=?", (name, version)) or {}

    def restore(self, name: str, version: int, author: str = "Local User") -> dict[str, Any]:
        old = self.db.one("SELECT * FROM prompt_templates WHERE name=? AND version=?", (name, version))
        if old is None:
            raise LibraryError(f"Version {version} of '{name}' does not exist.", "unknown_version")
        return self.save(name, old["text"], author, f"restored from v{version}")
