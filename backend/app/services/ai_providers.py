"""User-managed AI providers: OpenAI, Ollama (local) and custom OpenAI-compatible endpoints, plus the active text model.

One minimal OpenAI-compatible TEXT adapter (`/models` for the free connection test, `/chat/completions` for planning).
These providers are text-only: no image generation is implemented for them, so none is advertised.
Secrets (API keys) live in the credential store under provider id `ai:<id>`; they never appear in a response or a log.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from .credentials import CredentialStore, stored_value
from .db import Db

KINDS: dict[str, dict[str, Any]] = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "key": "required", "local": False,
               "suggested_models": ["gpt-4o-mini", "gpt-4.1-mini"]},
    "ollama": {"label": "Ollama (local)", "base_url": "http://localhost:11434/v1", "key": "none", "local": True,
               "suggested_models": []},
    "openai_compatible": {"label": "Custom OpenAI-compatible", "base_url": "", "key": "optional", "local": False,
                          "suggested_models": []},
}
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,119}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


class ProviderInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


class ChatError(Exception):
    """Text call failed; `code` is ai_plan_failed (transport/HTTP) or ai_plan_invalid (completed but unusable)."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message, self.code = message, code


def clean_base_url(kind: str, raw: str | None) -> str:
    url = (raw or "").strip() or KINDS[kind]["base_url"]
    if not url:
        raise ProviderInputError("missing_base_url", "A Base URL is required for this provider.")
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ProviderInputError("invalid_base_url", "Base URL must start with http:// or https://.")
    if p.username or p.password or p.query or p.fragment:
        raise ProviderInputError("invalid_base_url", "Base URL must not contain credentials, a query or a fragment.")
    if len(url) > 300:
        raise ProviderInputError("invalid_base_url", "Base URL is too long.")
    return url.rstrip("/")


def clean_model(model: str | None) -> str:
    m = (model or "").strip()
    if not _MODEL_RE.match(m):
        raise ProviderInputError("invalid_model", "Model ID may only contain letters, digits and . _ : / @ + - (max 120).")
    return m


def is_local_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in _LOCAL_HOSTS


def _schema_for_prompt(schema: Any) -> Any:
    """Gemini response schemas use upper-case types and `propertyOrdering`; render them as plain JSON-schema hints."""
    if isinstance(schema, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str) else _schema_for_prompt(v))
                for k, v in schema.items() if k != "propertyOrdering"}
    if isinstance(schema, list):
        return [_schema_for_prompt(x) for x in schema]
    return schema


def _parse_json(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    try:
        data = json.loads(t)
    except ValueError:
        i, j = t.find("{"), t.rfind("}")
        if i < 0 or j <= i:
            raise ChatError("The AI planner did not return valid JSON. No plan was created; try again.", "ai_plan_invalid") from None
        try:
            data = json.loads(t[i:j + 1])
        except ValueError:
            raise ChatError("The AI planner did not return valid JSON. No plan was created; try again.", "ai_plan_invalid") from None
    if not isinstance(data, dict):
        raise ChatError("The AI planner returned an unexpected structure.", "ai_plan_invalid")
    return data


class AiProviders:
    def __init__(self, db: Db, store: CredentialStore, *, transport: httpx.BaseTransport | None = None, timeout_s: float = 120.0) -> None:
        self.db, self.store, self._transport, self._timeout = db, store, transport, timeout_s
        self._lock = threading.Lock()
        db.script("""CREATE TABLE IF NOT EXISTS ai_providers(
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL, base_url TEXT NOT NULL,
            models TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS active_models(role TEXT PRIMARY KEY, provider_id TEXT NOT NULL, model_id TEXT NOT NULL);""")

    # ------------------------------------------------------------------ records
    @staticmethod
    def _sid(pid: str) -> str:
        return f"ai:{pid}"

    def get(self, pid: str) -> dict[str, Any] | None:
        r = self.db.one("SELECT * FROM ai_providers WHERE id=?", (pid,))
        if r:
            r["models"] = json.loads(r["models"] or "[]")
        return r

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT * FROM ai_providers ORDER BY created_at")
        for r in rows:
            r["models"] = json.loads(r["models"] or "[]")
        return rows

    def api_key(self, pid: str) -> str | None:
        return stored_value(self.store, self._sid(pid), "api_key")

    def has_key(self, pid: str) -> bool:
        return self.api_key(pid) is not None

    @staticmethod
    def new_id(kind: str, label: str, base_url: str, existing: set[str]) -> str:
        if kind in ("openai", "ollama"):
            return kind
        slug = re.sub(r"[^a-z0-9]+", "-", (label or urlparse(base_url).hostname or "custom").lower()).strip("-")[:24] or "custom"
        pid, n = f"custom-{slug}", 2
        while pid in existing:
            pid, n = f"custom-{slug}-{n}", n + 1
        return pid

    def save(self, kind: str, *, label: str, base_url: str | None, api_key: str | None, model_id: str | None) -> dict[str, Any]:
        """Create or update a provider (the same kind/base URL updates in place: one entry per connection)."""
        if kind not in KINDS:
            raise ProviderInputError("unknown_kind", "Unknown provider type.")
        url = clean_base_url(kind, base_url)
        key = (api_key or "").strip()
        if key and (len(key) > 512 or any(c.isspace() or ord(c) < 32 for c in key)):
            raise ProviderInputError("invalid_value", "API key contains whitespace or control characters or is too long.")
        spec = KINDS[kind]
        model = clean_model(model_id) if model_id and model_id.strip() else None
        with self._lock:
            rows = self.list()
            same = next((r for r in rows if r["kind"] == kind and r["base_url"] == url and kind != "openai_compatible"), None) \
                or next((r for r in rows if r["kind"] == kind == "openai_compatible" and r["base_url"] == url), None)
            existing_key = bool(same and self.has_key(same["id"]))
            if spec["key"] == "required" and not key and not existing_key:
                raise ProviderInputError("missing_field", "An API key is required for this provider.")
            if key and not self.store.available():
                raise ProviderInputError("storage_unavailable", "Secure credential storage is not available on this machine.")
            if not model and not (same and same["models"]):
                raise ProviderInputError("missing_model", "Choose or enter at least one Model ID.")
            pid = same["id"] if same else self.new_id(kind, label.strip(), url, {r["id"] for r in rows})
            models = list(same["models"]) if same else []
            if model and model not in models:
                models.append(model)
            name = (label.strip()[:60] or (same["label"] if same else "")
                    or (spec["label"] if kind != "openai_compatible" else f"Custom ({urlparse(url).hostname})"))
            if key:
                self.store.set(self._sid(pid), "api_key", key)
            self.db.exec("INSERT OR REPLACE INTO ai_providers(id,kind,label,base_url,models,created_at) VALUES(?,?,?,?,?,?)",
                         (pid, kind, name, url, json.dumps(models),
                          same["created_at"] if same else datetime.now(timezone.utc).isoformat()))
        return self.get(pid) or {}

    def remove(self, pid: str) -> None:
        """Removes the connection and its stored key. Generation history and the cost ledger are never touched."""
        self.store.delete(self._sid(pid), "api_key")
        self.db.exec("DELETE FROM ai_providers WHERE id=?", (pid,))
        self.db.exec("DELETE FROM active_models WHERE provider_id=?", (pid,))

    # ------------------------------------------------------------------ active text model
    def set_active_text(self, provider_id: str, model_id: str) -> None:
        self.db.exec("INSERT OR REPLACE INTO active_models(role,provider_id,model_id) VALUES('text',?,?)", (provider_id, clean_model(model_id)))

    def clear_active_text(self) -> None:
        self.db.exec("DELETE FROM active_models WHERE role='text'")

    def active_text(self) -> tuple[str, str] | None:
        r = self.db.one("SELECT provider_id, model_id FROM active_models WHERE role='text'")
        return (r["provider_id"], r["model_id"]) if r else None

    # ------------------------------------------------------------------ HTTP
    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(timeout=timeout, transport=self._transport)

    @staticmethod
    def _headers(key: str | None) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if key:
            h["authorization"] = f"Bearer {key}"
        return h

    def test(self, kind: str, base_url: str | None, api_key: str | None, *, saved_id: str | None = None) -> dict[str, Any]:
        """Free check: GET {base}/models. Never a chat/completion call. Message never contains the key."""
        try:
            url = clean_base_url(kind, base_url)
        except ProviderInputError as exc:
            return {"ok": False, "status": "invalid", "message": exc.message, "models": []}
        key = (api_key or "").strip() or (self.api_key(saved_id) if saved_id else None)
        if KINDS[kind]["key"] == "required" and not key:
            return {"ok": False, "status": "invalid", "message": "An API key is required for this provider.", "models": []}
        local = KINDS[kind]["local"] or is_local_url(url)
        try:
            with self._client(15.0) as c:
                resp = c.get(f"{url}/models", headers=self._headers(key))
        except httpx.HTTPError:
            where = "the local server is not running or not reachable at this address" if local else "network problem or wrong address"
            return {"ok": False, "status": "unreachable", "message": f"Could not reach {urlparse(url).netloc}: {where}.", "models": []}
        sc = resp.status_code
        if sc in (401, 403):
            return {"ok": False, "status": "invalid", "message": "The provider rejected the API key (invalid, expired or not permitted).", "models": []}
        if sc == 404:
            return {"ok": False, "status": "unreachable", "message": "The address answered but has no /models endpoint: check the Base URL (it usually ends with /v1).", "models": []}
        if sc == 429 or sc >= 500:
            return {"ok": False, "status": "unreachable", "message": f"The provider is unavailable or rate limiting (HTTP {sc}); try again shortly.", "models": []}
        if sc != 200:
            return {"ok": False, "status": "unreachable", "message": f"Unexpected response (HTTP {sc}).", "models": []}
        try:
            body = resp.json()
            items = body.get("data") if isinstance(body, dict) else None
            models = sorted({str(m.get("id")) for m in (items or []) if isinstance(m, dict) and m.get("id")})
        except ValueError:
            return {"ok": False, "status": "unreachable", "message": "The server returned an unreadable response; is this an OpenAI-compatible API?", "models": []}
        return {"ok": True, "status": "connected", "message": f"Connected. {len(models)} model(s) listed." if models else "Connected (no models listed: enter a Model ID).",
                "models": models[:200]}

    def chat_json(self, provider_id: str, model: str, system: str, prompt: str, seed: int | None,
                  schema: dict[str, Any] | None) -> dict[str, Any]:
        p = self.get(provider_id)
        if not p:
            raise ChatError("The selected text provider is no longer connected.", "ai_plan_failed")
        key = self.api_key(provider_id)
        hint = ("\n\nReturn ONE JSON object only (no markdown, no commentary) that matches this JSON schema:\n"
                + json.dumps(_schema_for_prompt(schema))) if schema else "\n\nReturn JSON only."
        body: dict[str, Any] = {"model": model, "messages": [{"role": "system", "content": system},
                                                             {"role": "user", "content": prompt + hint}],
                                "response_format": {"type": "json_object"}}
        if seed is not None:
            body["seed"] = int(seed) % (2**31 - 1)
        local = KINDS[p["kind"]]["local"] or is_local_url(p["base_url"])
        try:
            with self._client(max(self._timeout, 180.0) if local else min(self._timeout, 90.0)) as c:
                resp = c.post(f"{p['base_url']}/chat/completions", headers=self._headers(key), json=body)
        except httpx.HTTPError as exc:
            raise ChatError(f"The text provider '{p['label']}' could not be reached ({type(exc).__name__}). No plan was created.", "ai_plan_failed") from None
        if resp.status_code != 200:
            try:
                err = resp.json().get("error", {})
                msg = err.get("message", "") if isinstance(err, dict) else str(err)
            except Exception:
                msg = resp.text[:200]
            if key:
                msg = msg.replace(key, "***")
            raise ChatError(f"{p['label']} text API error {resp.status_code}: {msg or 'no details'}", "ai_plan_failed")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ChatError("The AI planner did not return valid JSON. No plan was created; try again.", "ai_plan_invalid") from None
        return _parse_json(content)
