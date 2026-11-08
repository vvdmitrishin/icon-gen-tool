"""Free connection checks and the API Connections view model.

Hard rules: only metadata endpoints are ever called (Gemini ListModels, Cloudflare model search);
never an image generation / inference endpoint. Secret values never appear in results or messages.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import Settings
from ..providers.base import ProviderError
from .credentials import PROVIDER_FIELDS, CredentialStore, field_source
from .db import Db

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
CF_MODELS_OF_INTEREST = ("@cf/black-forest-labs/flux-2-klein-4b", "@cf/black-forest-labs/flux-1-schnell")

PROVIDER_META: dict[str, dict[str, Any]] = {
    "gemini": {
        "label": "Google Gemini",
        "fields": [("api_key", "Gemini API key", True)],
        "help": {
            "get_credentials_url": "https://aistudio.google.com/app/apikey",
            "steps": [
                "Open Google AI Studio and sign in.",
                "Create an API key (a billing-enabled project is needed for image models).",
                "Paste the key here and press Save and test. The key is stored in Windows Credential Manager.",
            ],
        },
    },
    "cloudflare": {
        "label": "Cloudflare Workers AI",
        "fields": [("account_id", "Account ID", False), ("api_token", "API token", True)],
        "help": {
            "get_credentials_url": "https://dash.cloudflare.com/profile/api-tokens",
            "steps": [
                "Copy your Account ID from the Cloudflare dashboard (Workers AI overview page).",
                "Create an API token from the 'Workers AI' template (permission: Workers AI Read and Edit).",
                "Paste both here and press Save and test. Both are stored in Windows Credential Manager.",
            ],
        },
    },
}

_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_SECRET_LEN = 512


class ConnectionInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def validate_values(provider: str, values: dict[str, Any], already_have: set[str]) -> dict[str, str]:
    """Trim + sanity-check submitted values. Error messages never echo a value."""
    allowed = PROVIDER_FIELDS[provider]
    clean: dict[str, str] = {}
    for name, raw in values.items():
        if name not in allowed:
            raise ConnectionInputError("unknown_field", f"Unknown field '{name[:40]}' for {provider}.")
        if not isinstance(raw, str):
            raise ConnectionInputError("invalid_value", f"{name} must be text.")
        v = raw.strip()
        if not v:
            raise ConnectionInputError("empty_value", f"{name} must not be empty.")
        if len(v) > MAX_SECRET_LEN:
            raise ConnectionInputError("too_long", f"{name} is too long (max {MAX_SECRET_LEN} characters).")
        if any(ord(c) < 32 or ord(c) == 127 for c in v) or any(c.isspace() for c in v):
            raise ConnectionInputError("invalid_characters", f"{name} contains whitespace or control characters.")
        if name == "account_id" and not _ACCOUNT_RE.match(v):
            raise ConnectionInputError("invalid_account_id", "Account ID may only contain letters, digits, '-' and '_' (max 64).")
        clean[name] = v
    missing = [n for n in allowed if n not in clean and n not in already_have]
    if missing:
        raise ConnectionInputError("missing_field", f"Missing required field(s): {', '.join(missing)}.")
    return clean


# ----------------------------------------------------------------------------- checks
def _result(ok: bool, status: str, message: str, **details: Any) -> dict[str, Any]:
    return {"ok": ok, "status": status, "message": message, "details": details}


async def check_gemini(registry: Any) -> dict[str, Any]:
    """ListModels only (free, metadata). Uses the registry so availability flags are refreshed too."""
    try:
        info = await registry.check_gemini_availability()
    except ProviderError as exc:
        code = exc.http_status
        if code in (400, 401, 403):
            return _result(False, "invalid", "Gemini rejected this API key (invalid, restricted or disabled).", http_status=code)
        if code == 429:
            return _result(False, "unreachable", "Gemini is rate limiting requests; try again shortly.", http_status=code)
        if exc.code in ("network_error", "timeout") or (code or 0) >= 500:
            return _result(False, "unreachable", "Could not reach the Gemini API (network problem or service error).", http_status=code)
        if exc.code in ("not_configured", "provider_disabled"):
            return _result(False, "not_configured", "No Gemini API key is configured.")
        return _result(False, "unreachable", f"Unexpected Gemini response (HTTP {code}).", http_status=code)
    listed = info.get("image_like_models_listed", [])
    msg = (f"Key accepted. {len(listed)} image-capable model(s) listed." if listed
           else "Key accepted, but no image models are listed for this key.")
    return _result(True, "connected", msg, image_models_listed=listed, available=info.get("available", {}))


async def check_cloudflare(account_id: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None,
                           timeout_s: float = 20.0, base_url: str = CLOUDFLARE_API) -> dict[str, Any]:
    """GET /accounts/{id}/ai/models/search (metadata only; never an inference endpoint)."""
    if not _ACCOUNT_RE.match(account_id or ""):
        return _result(False, "invalid", "Account ID looks malformed.")
    url = f"{base_url.rstrip('/')}/accounts/{account_id}/ai/models/search"
    try:
        async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
            resp = await client.get(url, params={"per_page": 50, "search": "flux"},
                                    headers={"Authorization": f"Bearer {token}"})
    except httpx.TimeoutException:
        return _result(False, "unreachable", "Cloudflare did not respond in time.")
    except httpx.HTTPError:
        return _result(False, "unreachable", "Could not reach Cloudflare (network problem).")
    sc = resp.status_code
    if sc == 200:
        try:
            body = resp.json()
            items = body.get("result") or []
            names = {str(i.get("name") or i.get("id") or "") for i in items if isinstance(i, dict)}
        except Exception:
            return _result(False, "unreachable", "Cloudflare returned an unreadable response.", http_status=sc)
        flux = {m: m in names for m in CF_MODELS_OF_INTEREST}
        if flux.get(CF_MODELS_OF_INTEREST[0]):
            msg = "Connected. FLUX.2 Klein 4B is available for this account."
        else:
            msg = "Connected, but FLUX.2 Klein 4B was not listed for this account."
        return _result(True, "connected", msg, models=flux, models_listed=len(names))
    if sc == 401:
        return _result(False, "invalid", "Cloudflare rejected the API token (invalid or expired).", http_status=sc)
    if sc == 403:
        return _result(False, "invalid", "Access denied: the token is invalid, lacks Workers AI permission, or the Account ID is wrong.", http_status=sc)
    if sc == 404:
        return _result(False, "invalid", "Account not found: check the Account ID.", http_status=sc)
    if sc == 429 or sc >= 500:
        return _result(False, "unreachable", f"Cloudflare is unavailable or rate limiting (HTTP {sc}); try again shortly.", http_status=sc)
    return _result(False, "unreachable", f"Unexpected Cloudflare response (HTTP {sc}).", http_status=sc)


# ----------------------------------------------------------------------------- service
class ConnectionService:
    def __init__(self, base: Settings, store: CredentialStore, db: Db, registry: Any,
                 cloudflare_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base, self.store, self.db, self.registry = base, store, db, registry
        self._cf_transport = cloudflare_transport
        db.script("""CREATE TABLE IF NOT EXISTS connection_status(
            provider TEXT PRIMARY KEY, status TEXT NOT NULL, message TEXT NOT NULL, tested_at TEXT NOT NULL);""")

    # -- helpers
    def known(self, provider: str) -> bool:
        return provider in PROVIDER_META

    def _have(self, provider: str, field: str) -> bool:
        stored, env = field_source(self.base, self.store, provider, field)
        return stored or env

    def configured(self, provider: str) -> bool:
        if provider == "gemini" and not self.base.gemini_api_enabled:
            return False
        return all(self._have(provider, f) for f in PROVIDER_FIELDS[provider])

    def first_run(self) -> bool:
        return not any(self.configured(p) for p in PROVIDER_META)

    def source(self, provider: str) -> str:
        if not self.configured(provider):
            return "none"
        stored = [field_source(self.base, self.store, provider, f)[0] for f in PROVIDER_FIELDS[provider]]
        return "credential_manager" if any(stored) else "env"

    def provider_view(self, provider: str) -> dict[str, Any]:
        meta = PROVIDER_META[provider]
        configured = self.configured(provider)
        row = self.db.one("SELECT status, message, tested_at FROM connection_status WHERE provider=?", (provider,))
        if not configured:
            status, last, msg = "not_configured", None, "Not configured."
        elif row:
            status, last, msg = row["status"], row["tested_at"], row["message"]
        else:
            status, last, msg = "unverified", None, "Saved but not tested yet."
        fields = []
        for name, label, secret in meta["fields"]:
            stored, env = field_source(self.base, self.store, provider, name)
            fields.append({"name": name, "label": label, "secret": secret, "stored": stored, "from_env": env and not stored})
        return {"id": provider, "label": meta["label"], "configured": configured, "source": self.source(provider),
                "status": status, "last_tested_at": last, "message": msg, "fields": fields, "help": meta["help"]}

    def overview(self) -> dict[str, Any]:
        return {"secure_storage": {"available": self.store.available(), "backend": self.store.backend_name},
                "first_run": self.first_run(),
                "providers": [self.provider_view(p) for p in PROVIDER_META]}

    # -- mutations
    def _record(self, provider: str, res: dict[str, Any]) -> None:
        self.db.exec("INSERT OR REPLACE INTO connection_status(provider,status,message,tested_at) VALUES(?,?,?,?)",
                     (provider, res["status"], res["message"], datetime.now(timezone.utc).isoformat()))

    def _clear_status(self, provider: str) -> None:
        self.db.exec("DELETE FROM connection_status WHERE provider=?", (provider,))

    def _changed(self, provider: str) -> None:
        self._clear_status(provider)
        self.registry.credentials_changed()

    def save(self, provider: str, values: dict[str, Any]) -> None:
        if not self.store.available():
            raise ConnectionInputError("storage_unavailable", "Secure credential storage is not available on this machine.")
        have = {f for f in PROVIDER_FIELDS[provider] if self._have(provider, f)}
        clean = validate_values(provider, values, have)
        for name, v in clean.items():
            self.store.set(provider, name, v)
        self._changed(provider)

    def remove(self, provider: str) -> None:
        for f in PROVIDER_FIELDS[provider]:
            self.store.delete(provider, f)
        self._changed(provider)

    async def test(self, provider: str) -> dict[str, Any]:
        eff = self.registry.effective
        if not self.configured(provider):
            res = _result(False, "not_configured", "Not configured: nothing to test.")
            return res
        if provider == "gemini":
            res = await check_gemini(self.registry)
        else:
            res = await check_cloudflare(eff.cloudflare_account_id.strip(), eff.cloudflare_api_token.strip(),
                                         transport=self._cf_transport, timeout_s=min(eff.gemini_timeout_s, 30.0))
        self._record(provider, res)
        return res
