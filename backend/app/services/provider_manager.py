"""One simple provider manager on top of the existing pieces: Gemini / Cloudflare (ConnectionService + credential store) and the
user-added OpenAI / Ollama / custom OpenAI-compatible text providers (AiProviders). Free metadata checks only; never a paid call."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any


from ..providers.base import ProviderError
from ..providers.gemini_image import GeminiImageProvider
from .ai_providers import KINDS, AiProviders, ProviderInputError, clean_model, is_local_url
from .connection_check import (
    PROVIDER_META, ConnectionInputError, ConnectionService, _result, check_cloudflare, validate_values,
)

CATALOG: list[dict[str, Any]] = [
    {"kind": "gemini", "label": "Google Gemini", "types": ["text", "image"], "needs_model": False,
     "fields": [{"name": "api_key", "label": "API Key", "secret": True, "required": True}],
     "model_label": "Text model ID (optional)", "models": [], "base_url": None},
    {"kind": "cloudflare", "label": "Cloudflare Workers AI", "types": ["image"], "needs_model": False,
     "fields": [{"name": "account_id", "label": "Account ID", "secret": False, "required": True},
                {"name": "api_token", "label": "API Token", "secret": True, "required": True}],
     "models": [], "base_url": None},
    {"kind": "openai", "label": "OpenAI", "types": ["text"], "needs_model": True,
     "fields": [{"name": "api_key", "label": "API Key", "secret": True, "required": True}],
     "model_label": "Model ID", "models": KINDS["openai"]["suggested_models"], "base_url": KINDS["openai"]["base_url"]},
    {"kind": "ollama", "label": "Ollama (local)", "types": ["text"], "needs_model": True, "fields": [],
     "model_label": "Model ID", "models": [], "base_url": KINDS["ollama"]["base_url"]},
    {"kind": "openai_compatible", "label": "Custom OpenAI-compatible API", "types": ["text"], "needs_model": True,
     "fields": [{"name": "api_key", "label": "API Key (if required)", "secret": True, "required": False}],
     "model_label": "Model ID", "models": [], "base_url": ""},
]
_STATUS_LABEL = {"connected": "Connected", "configured": "Configured / Not verified", "invalid": "Invalid credentials",
                 "unreachable": "Can't reach provider", "unverified": "Configured / Not verified"}


class ProviderManager:
    def __init__(self, connections: ConnectionService, ai: AiProviders, registry: Any, *, gemini_transport: Any = None) -> None:
        self.conn, self.ai, self.registry, self._gemini_transport = connections, ai, registry, gemini_transport
        registry.ai = ai
        registry.on_success = self.note_success
        self._seed_verified_from_use()

    # ------------------------------------------------------------------ verification by real use
    def _row(self, pid: str) -> dict[str, Any] | None:
        return self.conn.db.one("SELECT status, message, tested_at FROM connection_status WHERE provider=?", (pid,))

    def _write(self, pid: str, status: str, message: str) -> None:
        self.conn.db.exec("INSERT OR REPLACE INTO connection_status(provider,status,message,tested_at) VALUES(?,?,?,?)",
                          (pid, status, message, datetime.now(timezone.utc).isoformat()))

    def note_success(self, pid: str) -> None:
        """A real image/text request through this provider just worked: that verifies the connection."""
        known = pid in PROVIDER_META and self.conn.configured(pid) or (pid not in PROVIDER_META and self.ai.get(pid) is not None)
        if not known:
            return
        row = self._row(pid)
        if row is None or row["status"] != "connected":
            self._write(pid, "connected", "Verified: the app has used this provider successfully.")

    def _seed_verified_from_use(self) -> None:
        """Providers that already produced images (history) count as verified, e.g. Gemini that was never 'tested' by hand."""
        for pid in self.registry.live_providers():
            if pid in PROVIDER_META and self.conn.configured(pid) and self._row(pid) is None:
                self._write(pid, "connected", "Verified: the app has used this provider successfully.")

    # ------------------------------------------------------------------ views
    def _status(self, pid: str, configured: bool = True) -> tuple[str, str, str | None]:
        row = self._row(pid)
        if not configured:
            return "not_configured", "Not configured.", None
        if row is None:
            return "configured", "Saved but not verified yet.", None
        return ("configured" if row["status"] == "unverified" else row["status"]), row["message"], row["tested_at"]

    def _image_models(self, pid: str) -> list[dict[str, Any]]:
        return [{"id": m.model_id, "label": m.label} for m in self.registry.models()
                if m.provider_id == pid and m.selectable and m.role != "planned"]

    def overview(self) -> dict[str, Any]:
        eff = self.registry.effective
        tt = self.registry.text_target()
        items: list[dict[str, Any]] = []
        text_options: list[dict[str, Any]] = []
        if self.conn.configured("gemini"):
            st, msg, at = self._status("gemini")
            tm = [eff.gemini_text_model] + ([tt.model] if tt.provider_id == "gemini" and tt.model != eff.gemini_text_model else [])
            items.append({"id": "gemini", "label": "Google Gemini", "kind": "gemini", "types": ["text", "image"], "local": False,
                          "status": st, "status_label": _STATUS_LABEL.get(st, st), "message": msg, "last_tested_at": at,
                          "source": self.conn.source("gemini"), "base_url": None,
                          "text_models": tm, "image_models": self._image_models("gemini")})
            text_options += [{"provider_id": "gemini", "provider_label": "Google Gemini", "model_id": m, "local": False} for m in tm]
        if self.conn.configured("cloudflare"):
            st, msg, at = self._status("cloudflare")
            items.append({"id": "cloudflare", "label": "Cloudflare Workers AI", "kind": "cloudflare", "types": ["image"], "local": False,
                          "status": st, "status_label": _STATUS_LABEL.get(st, st), "message": msg, "last_tested_at": at,
                          "source": self.conn.source("cloudflare"), "base_url": None,
                          "text_models": [], "image_models": self._image_models("cloudflare")})
        for p in self.ai.list():
            st, msg, at = self._status(p["id"])
            local = KINDS[p["kind"]]["local"] or is_local_url(p["base_url"])
            items.append({"id": p["id"], "label": p["label"], "kind": p["kind"], "types": ["text"], "local": local,
                          "status": st, "status_label": _STATUS_LABEL.get(st, st), "message": msg, "last_tested_at": at,
                          "source": "credential_manager" if self.ai.has_key(p["id"]) else "none", "base_url": p["base_url"],
                          "text_models": p["models"], "image_models": []})
            text_options += [{"provider_id": p["id"], "provider_label": p["label"], "model_id": m, "local": local} for m in p["models"]]
        return {"secure_storage": {"available": self.conn.store.available(), "backend": self.conn.store.backend_name},
                "providers": items, "catalog": CATALOG, "text_options": text_options,
                "active_text": {"provider_id": tt.provider_id, "model_id": tt.model, "ready": tt.ready, "local": tt.free}}

    # ------------------------------------------------------------------ tests / connect / disconnect
    async def _test_gemini_key(self, key: str) -> dict[str, Any]:
        eff = self.registry.effective
        prov = GeminiImageProvider(key, base_url=eff.gemini_base_url, timeout_s=min(eff.gemini_timeout_s, 30.0),
                                   transport=self._gemini_transport)
        try:
            listed = await prov.list_models()
        except ProviderError as exc:
            code = exc.http_status
            if code in (400, 401, 403):
                return _result(False, "invalid", "Gemini rejected this API key (invalid, restricted or disabled).")
            return _result(False, "unreachable", "Could not reach the Gemini API (network problem, rate limit or service error).")
        n = len([m for m in listed if "image" in m or "banana" in m])
        return _result(True, "connected", f"Key accepted. {n} image-capable model(s) listed.")

    async def test_new(self, kind: str, values: dict[str, Any], base_url: str | None, model_id: str | None) -> dict[str, Any]:
        if kind == "gemini":
            try:
                clean = validate_values("gemini", {k: v for k, v in values.items() if k == "api_key"}, set())
            except ConnectionInputError as exc:
                return _result(False, "invalid", exc.message)
            return await self._test_gemini_key(clean["api_key"])
        if kind == "cloudflare":
            try:
                clean = validate_values("cloudflare", {k: v for k, v in values.items() if k in ("account_id", "api_token")}, set())
            except ConnectionInputError as exc:
                return _result(False, "invalid", exc.message)
            eff = self.registry.effective
            return await check_cloudflare(clean["account_id"], clean["api_token"], transport=self.conn._cf_transport,
                                          timeout_s=min(eff.gemini_timeout_s, 30.0))
        if kind not in KINDS:
            return _result(False, "invalid", "Unknown provider type.")
        res = await asyncio.to_thread(self.ai.test, kind, base_url, values.get("api_key"))
        if res["ok"] and model_id and model_id.strip():
            try:
                m = clean_model(model_id)
            except ProviderInputError as exc:
                return {**res, "ok": False, "status": "invalid", "message": exc.message}
            if res["models"] and m not in res["models"]:
                res["message"] += f" Note: '{m}' is not in the provider's model list; it will be tried as entered."
        return res

    async def connect(self, kind: str, values: dict[str, Any], base_url: str | None, model_id: str | None, label: str = "") -> dict[str, Any]:
        """Save & Connect: the free connection test must succeed first; nothing is stored otherwise."""
        res = await self.test_new(kind, values, base_url, model_id)
        if not res["ok"]:
            return {"saved": False, "test": res}
        try:
            if kind in ("gemini", "cloudflare"):
                self.conn.save(kind, {k: v for k, v in values.items() if k in dict.fromkeys(("api_key", "account_id", "api_token"))})
                self._write(kind, "connected", res["message"])
                if kind == "gemini" and model_id and model_id.strip():
                    self.ai.set_active_text("gemini", model_id)
                pid = kind
            else:
                rec = self.ai.save(kind, label=label, base_url=base_url, api_key=values.get("api_key"), model_id=model_id)
                pid = rec["id"]
                self._write(pid, "connected", res["message"])
        except (ConnectionInputError, ProviderInputError) as exc:
            return {"saved": False, "test": _result(False, "invalid", exc.message)}
        return {"saved": True, "id": pid, "test": res}

    async def test_saved(self, pid: str) -> dict[str, Any]:
        if pid in PROVIDER_META:
            return await self.conn.test(pid)
        p = self.ai.get(pid)
        if not p:
            return _result(False, "not_configured", "Not configured: nothing to test.")
        res = await asyncio.to_thread(self.ai.test, p["kind"], p["base_url"], None, saved_id=pid)
        self._write(pid, res["status"], res["message"])
        return res

    def disconnect(self, pid: str) -> dict[str, Any]:
        """Removes stored credentials/configuration. Historical generations and the cost ledger are not touched."""
        if pid in PROVIDER_META:
            self.conn.remove(pid)
            active = self.ai.active_text()
            if pid == "gemini" and active and active[0] == "gemini":
                self.ai.clear_active_text()
            still_env = self.conn.configured(pid)
            return {"removed": True, "still_configured_from_env": still_env,
                    "note": ("Removed from this PC, but the same credential is still in .env, so the provider stays connected."
                             if still_env else "Removed from this PC. This does not revoke the key at the provider.")}
        if self.ai.get(pid) is None:
            return {"removed": False, "note": "Unknown provider."}
        self.ai.remove(pid)
        self.conn.db.exec("DELETE FROM connection_status WHERE provider=?", (pid,))
        return {"removed": True, "still_configured_from_env": False,
                "note": "Removed from this PC. Existing generations and costs are kept."}

    # ------------------------------------------------------------------ active text model
    def set_active_text(self, provider_id: str, model_id: str) -> None:
        if provider_id == "gemini":
            if not self.conn.configured("gemini"):
                raise ProviderInputError("not_connected", "Gemini is not connected.")
        else:
            p = self.ai.get(provider_id)
            if not p:
                raise ProviderInputError("not_connected", "That text provider is not connected.")
            if model_id not in p["models"]:
                raise ProviderInputError("unknown_model", "That model is not set up for this provider.")
        self.ai.set_active_text(provider_id, model_id)
