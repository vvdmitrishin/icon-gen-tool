"""Environment self-check. Prints what is configured WITHOUT revealing any secret value.

  backend\\.venv\\Scripts\\python scripts\\check_env.py
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
# console-safe output on Windows (cp1252)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import load_settings  # noqa: E402


def line(ok: bool | None, label: str, detail: str = "") -> None:
    mark = {True: "OK  ", False: "FAIL", None: "info"}[ok]
    print(f"[{mark}] {label}{(' - ' + detail) if detail else ''}")


def main() -> int:
    bad = 0
    line(sys.version_info >= (3, 11), "Python >= 3.11", sys.version.split()[0])
    bad += sys.version_info < (3, 11)

    node = shutil.which("node")
    ver = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip() if node else ""
    line(bool(node), "Node.js (for the UI build)", ver or "not found")
    bad += not node

    line((ROOT / ".env").exists(), ".env present (git-ignored)", "copy .env.example to .env" if not (ROOT / ".env").exists() else "")
    s = load_settings()
    line(None, "Gemini", "configured" if s.gemini_configured else "no GEMINI_API_KEY (Gemini unavailable; Mock still works)")
    line(None, "Cloudflare fallback", "configured" if s.cloudflare_configured else
         f"token {'set' if s.cloudflare_api_token else 'missing'}, account id {'set' if s.cloudflare_account_id else 'missing'}")
    from app.services.credentials import PROVIDER_FIELDS, KeyringCredentialStore  # noqa: E402

    store = KeyringCredentialStore()
    if store.available():
        for prov, fields in PROVIDER_FIELDS.items():  # booleans only, never values
            have = ", ".join(f"{f}={'yes' if store.get(prov, f) else 'no'}" for f in fields)
            line(None, f"Credential manager ({store.backend_name}) - {prov}", have)
    else:
        line(None, "Credential manager", "not available (keys come from .env only)")
    line(None, "OpenAI GPT Image", f"disabled by design (enabled flag = {s.openai_image_enabled}); no calls are made")
    line(None, "Budget guard", f"no cumulative cap; ${s.job_budget_usd:.2f} per-run size guard, retries {s.max_card_retries}, "
                               f"regenerations/card {s.max_regenerations_per_card}")

    for name, path in (("data", s.data_root), ("artifacts", s.artifact_root)):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_test"
            probe.write_text("x")
            probe.unlink()
            line(True, f"{name} folder writable", str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name)
        except OSError as exc:
            line(False, f"{name} folder writable", exc.__class__.__name__)
            bad += 1

    dist = ROOT / "frontend" / "dist" / "index.html"
    line(None, "UI build", "present (served by the backend)" if dist.exists() else "not built yet (start.bat builds it)")
    with socket.socket() as sock:
        free = sock.connect_ex(("127.0.0.1", 8000)) != 0
    line(None, "Port 8000", "free" if free else "in use (backend already running?)")
    print("\nResult:", "ready" if not bad else f"{bad} problem(s) found")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
