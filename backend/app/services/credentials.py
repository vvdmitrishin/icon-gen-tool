"""Credential storage abstraction (Windows Credential Manager via `keyring`) and effective-settings plumbing.

Precedence for every credential field: value in the credential store > .env / Settings value > none.
Secret values are never logged, never put in exception messages, and never returned by any API.
"""
from __future__ import annotations

import dataclasses
import threading
from typing import Protocol

from ..config import Settings

SERVICE_NAME = "AICollectionStudio"

# provider -> {field: Settings attribute}
PROVIDER_FIELDS: dict[str, dict[str, str]] = {
    "gemini": {"api_key": "gemini_api_key"},
    "cloudflare": {"account_id": "cloudflare_account_id", "api_token": "cloudflare_api_token"},
}


class CredentialStore(Protocol):
    backend_name: str

    def get(self, provider: str, field: str) -> str | None: ...
    def set(self, provider: str, field: str, value: str) -> None: ...
    def delete(self, provider: str, field: str) -> None: ...
    def available(self) -> bool: ...


class NullCredentialStore:
    """Always empty and read-only: the default for create_app() and tests."""

    backend_name = "none"

    def get(self, provider: str, field: str) -> str | None:
        return None

    def set(self, provider: str, field: str, value: str) -> None:
        raise RuntimeError("Secure credential storage is not available.")

    def delete(self, provider: str, field: str) -> None:
        return None

    def available(self) -> bool:
        return False


class MemoryCredentialStore:
    """In-memory store for tests."""

    backend_name = "memory"

    def __init__(self) -> None:
        self._d: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def get(self, provider: str, field: str) -> str | None:
        with self._lock:
            return self._d.get((provider, field))

    def set(self, provider: str, field: str, value: str) -> None:
        with self._lock:
            self._d[(provider, field)] = value

    def delete(self, provider: str, field: str) -> None:
        with self._lock:
            self._d.pop((provider, field), None)

    def available(self) -> bool:
        return True


class KeyringCredentialStore:
    """OS credential vault (Windows Credential Manager, DPAPI-protected) through `keyring`."""

    def __init__(self, service: str = SERVICE_NAME) -> None:
        self.service = service
        self._kr = None
        self.backend_name = "unavailable"
        try:
            import keyring
            from keyring.backends import fail

            backend = keyring.get_keyring()
            if not isinstance(backend, fail.Keyring):
                self._kr = keyring
                self.backend_name = _friendly_backend(backend)
        except Exception:  # keyring missing or misconfigured: behave as "no secure storage"
            self._kr = None

    @staticmethod
    def _user(provider: str, field: str) -> str:
        return f"{provider}.{field}"

    def available(self) -> bool:
        return self._kr is not None

    def get(self, provider: str, field: str) -> str | None:
        if self._kr is None:
            return None
        try:
            return self._kr.get_password(self.service, self._user(provider, field)) or None
        except Exception:
            return None

    def set(self, provider: str, field: str, value: str) -> None:
        if self._kr is None:
            raise RuntimeError("Secure credential storage is not available.")
        try:
            self._kr.set_password(self.service, self._user(provider, field), value)
        except Exception as exc:  # do not chain/echo: backend errors could embed the value
            raise RuntimeError(f"Could not write to secure storage ({type(exc).__name__}).") from None

    def delete(self, provider: str, field: str) -> None:
        if self._kr is None:
            return
        try:
            self._kr.delete_password(self.service, self._user(provider, field))
        except Exception:  # PasswordDeleteError when nothing is stored: idempotent
            return


def _friendly_backend(backend: object) -> str:
    name = type(backend).__name__
    if "WinVault" in name or "Windows" in name:
        return "Windows Credential Manager"
    return name


def stored_value(store: CredentialStore, provider: str, field: str) -> str | None:
    v = store.get(provider, field)
    return v.strip() if v and v.strip() else None


def effective_settings(base: Settings, store: CredentialStore | None) -> Settings:
    """Settings with credential-store values layered over the .env ones (store wins)."""
    if store is None:
        return base
    changes: dict[str, str] = {}
    for provider, fields in PROVIDER_FIELDS.items():
        for field, attr in fields.items():
            v = stored_value(store, provider, field)
            if v:
                changes[attr] = v
    return dataclasses.replace(base, **changes) if changes else base


def field_source(base: Settings, store: CredentialStore, provider: str, field: str) -> tuple[bool, bool]:
    """-> (stored_in_credential_manager, present_in_env)."""
    env_val = getattr(base, PROVIDER_FIELDS[provider][field], "") or ""
    return stored_value(store, provider, field) is not None, bool(env_val.strip())
