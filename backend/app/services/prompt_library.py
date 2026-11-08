"""Prompt Library: versioned, editable prompt templates stored locally (spec §0.4, §6.3.1, ADR-012).

Works with no LLM: save/restore/versioning is plain application functionality. Every save creates a
new immutable version and becomes the active one; restore copies an older version forward.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import yaml

from .db import Db
from .prompt_compiler import CONFIG_DIR, SESSION_EDITABLE, TEMPLATE_NAMES, default_templates

MAX_TEXT = 5000  # characters per template (permanent versions, session overrides and job snapshots)
V05_AUTHOR = "Prompt System v0.5"
V05_NOTE = "Prompt System v0.5 (producer-reviewed): production default"


V06_AUTHOR = "Prompt System v0.6"
V06_NOTE = "Prompt System v0.6: production default"


def _load_release(filename: str) -> dict[str, str]:
    data = yaml.safe_load((CONFIG_DIR / filename).read_text(encoding="utf-8"))
    return {k: str(v).strip() for k, v in data["templates"].items()}


@lru_cache(maxsize=1)
def v05_templates() -> dict[str, str]:
    """The seven Prompt System v0.5 image templates, verbatim (config/prompt_system_v05.yaml)."""
    return _load_release("prompt_system_v05.yaml")


@lru_cache(maxsize=1)
def v06_templates() -> dict[str, str]:
    """The seven Prompt System v0.6 image templates, verbatim (config/prompt_system_v06.yaml)."""
    return _load_release("prompt_system_v06.yaml")

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
    "category_5": "Category 5 rules (golden cards)",
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
        self.apply_releases()

    def seed(self) -> None:
        defaults = default_templates()
        for name in TEMPLATE_NAMES:
            if not self.db.one("SELECT 1 AS x FROM prompt_templates WHERE name=?", (name,)):
                self._insert(name, 1, defaults[name], "system", "built-in default")

    def apply_releases(self) -> list[str]:
        """Prompt System releases in order (v0.5 -> v0.6): each is saved ONCE as a NEW permanent version and activated. Older versions
        stay for rollback. A template is only upgraded while its active version is still the untouched previous release, so
        Producer edits and deliberate rollbacks are never overridden, and a release that already has a version is never saved again."""
        applied: list[str] = []
        for texts, author, note, previous in ((v05_templates(), V05_AUTHOR, V05_NOTE, "system"),
                                              (v06_templates(), V06_AUTHOR, V06_NOTE, V05_AUTHOR)):
            for name, text in texts.items():
                if self.db.one("SELECT 1 AS x FROM prompt_templates WHERE name=? AND author=?", (name, author)):
                    continue
                row = self.active().get(name)
                if row is None or row["author"] != previous or (previous == "system" and row["version"] != 1):
                    continue
                self.save(name, text, author, note)
                applied.append(f"{name}:{author}")
        return applied

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

    # ---- prompt bundle (permanent library versions + optional session overrides) -------------------------------------
    def resolve_bundle(self, overrides: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
        """The complete effective bundle: every template with its text, origin and permanent version.
        A session override replaces the TEXT of a template for this bundle only; nothing is written to the library."""
        overrides = overrides or {}
        clean: dict[str, str] = {}
        for name, text in overrides.items():
            if name not in SESSION_EDITABLE:
                raise LibraryError(f"'{name}' cannot be overridden for a session.", "unknown_template")
            text = (text or "").strip()
            if not text:
                raise LibraryError(f"The session override of '{name}' is empty.", "empty_override")
            if len(text) > MAX_TEXT:
                raise LibraryError(f"The session override of '{name}' is limited to {MAX_TEXT} characters.", "override_too_long")
            clean[name] = text
        active = self.active()
        bundle: dict[str, dict[str, Any]] = {}
        for name in TEMPLATE_NAMES:
            row = active[name]
            if name in clean:
                bundle[name] = {"text": clean[name], "source": "session_override", "version": None, "base_version": row["version"]}
            else:
                bundle[name] = {"text": row["text"], "source": "library", "version": row["version"], "base_version": row["version"]}
        return bundle

    @staticmethod
    def normalize_bundle(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Validate a stored job snapshot before it is used for generation (names, text type and size)."""
        out: dict[str, dict[str, Any]] = {}
        for name, b in (raw or {}).items():
            if name not in TEMPLATE_NAMES or not isinstance(b, dict) or not isinstance(b.get("text"), str):
                raise LibraryError("The stored prompt bundle is malformed.", "bad_bundle")
            text = b["text"].strip()
            if not text or len(text) > MAX_TEXT:
                raise LibraryError("The stored prompt bundle is malformed.", "bad_bundle")
            out[name] = {"text": text, "source": "session_override" if b.get("source") == "session_override" else "library",
                         "version": b.get("version"), "base_version": b.get("base_version", b.get("version"))}
        return out

    def versions(self, name: str) -> list[dict[str, Any]]:
        if name not in TEMPLATE_NAMES:
            raise LibraryError(f"Unknown template '{name}'.", "unknown_template")
        return self.db.all("SELECT * FROM prompt_templates WHERE name=? ORDER BY version DESC", (name,))

    def listing(self) -> list[dict[str, Any]]:
        out = []
        active = self.active()
        defaults = default_templates()
        shipped = (v05_templates(), v06_templates())
        for name in TEMPLATE_NAMES:
            a = active[name]
            out.append({"name": name, "label": LABELS[name], "version": a["version"], "text": a["text"],
                        "author": a["author"], "created_at": a["created_at"], "note": a["note"],
                        "is_default_text": a["text"] == defaults[name] or any(a["text"] == r.get(name) for r in shipped),  # a shipped default, not hand-edited
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
