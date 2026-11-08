"""Local-only protection for credential endpoints: Host/Origin allow-lists, per-process token, rate limit."""
from __future__ import annotations

import re
import secrets
import time
from urllib.parse import urlsplit

LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})

_HOST_RE = re.compile(r"^(?P<h>\[[0-9a-fA-F:]+\]|[A-Za-z0-9.\-]+)(?::(?P<p>\d{1,5}))?$")


def is_allowed_host(host_header: str | None) -> bool:
    """Host must be 127.0.0.1 / localhost / [::1], optionally with a port (blocks DNS rebinding)."""
    if not host_header:
        return False
    m = _HOST_RE.match(host_header.strip())
    return bool(m) and m.group("h").lower() in LOCAL_HOSTNAMES


def is_allowed_origin(origin: str | None) -> bool:
    """No Origin is fine (non-browser client); if present it must be an http loopback origin."""
    if origin is None:
        return True
    try:
        u = urlsplit(origin.strip())
        host = u.hostname
        u.port  # raises on a bad port
    except ValueError:
        return False
    if u.scheme != "http" or not host or u.username or u.password or u.path not in ("", "/") or u.query or u.fragment:
        return False
    host = f"[{host}]" if ":" in host else host
    return host.lower() in LOCAL_HOSTNAMES


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_matches(expected: str, supplied: str | None) -> bool:
    if not supplied:
        return False
    return secrets.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))


class RateLimiter:
    """Allow at most `limit` hits per `window_s` per key (in-process, monotonic clock)."""

    def __init__(self, limit: int = 6, window_s: float = 60.0, clock=time.monotonic) -> None:
        self.limit, self.window_s, self._clock = limit, window_s, clock
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window_s]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


# ------------------------------------------------------------------ log redaction
_ACCOUNT_IN_URL = re.compile(r"(/accounts/)[^/\s?\"]+")


class _RedactAccountFilter:
    """httpx logs full request URLs at INFO; hide the Cloudflare account id segment."""

    def filter(self, record) -> bool:  # logging.Filter protocol
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "/accounts/" in msg:
            record.msg, record.args = _ACCOUNT_IN_URL.sub(r"\1***", msg), ()
        return True


_installed = False


def install_log_redaction() -> None:
    global _installed
    import logging

    if not _installed:
        logging.getLogger("httpx").addFilter(_RedactAccountFilter())  # type: ignore[arg-type]
        _installed = True
