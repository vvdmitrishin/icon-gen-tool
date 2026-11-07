import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT
from app.main import create_app

DIST = REPO_ROOT / "frontend" / "dist" / "index.html"


@pytest.mark.skipif(not DIST.exists(), reason="UI not built (npm run build)")
def test_backend_serves_the_built_ui_and_keeps_api_404s_json(settings):
    with TestClient(create_app(settings)) as c:
        home = c.get("/")
        assert home.status_code == 200 and "text/html" in home.headers["content-type"] and "AI Collection Studio" in home.text
        assert c.get("/some/client/route").status_code == 200           # SPA fallback
        r = c.get("/api/definitely-not-a-route")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
        assert c.get("/..%2F..%2Fbackend%2Fapp%2Fmain.py").status_code in (200, 404)
        assert "def create_app" not in c.get("/..%2F..%2Fbackend%2Fapp%2Fmain.py").text  # no path traversal out of dist
        assert c.get("/api/health").json()["status"] == "ok"


def test_offline_smoke_script_passes():
    script = REPO_ROOT / "scripts" / "run_smoke_test.py"
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-800:]
    assert "FAIL" not in r.stdout and "steps passed" in r.stdout


def test_check_env_never_prints_secret_values():
    script = Path(REPO_ROOT) / "scripts" / "check_env.py"
    env = {"GEMINI_API_KEY": "A" "Q.TOPSECRETVALUE1234567890abcdef", "CLOUDFLARE_API_TOKEN": "cf" "ut_TOPSECRET1234567890abcdefgh",
           "PATH": __import__("os").environ.get("PATH", ""), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60, env=env)
    assert "TOPSECRET" not in r.stdout + r.stderr
    assert "Gemini" in r.stdout and "OpenAI GPT Image" in r.stdout
