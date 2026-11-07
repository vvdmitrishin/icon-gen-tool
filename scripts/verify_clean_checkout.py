"""Clean-checkout verification: proves the project works from ONLY the files Git would contain.

Copies `git ls-files --cached --others --exclude-standard` (so .env, data/, benchmarks/, node_modules,
virtualenvs and confidential references are NOT included) into a temp folder, then in that folder:
create a venv, install the backend, run pytest, npm ci + build the UI, run the offline smoke test.

  python scripts\\verify_clean_checkout.py          (takes a few minutes; needs internet for pip/npm)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENSITIVE = ("playrix_", "gemini_ref_test", ".env", "benchmarks/", "data/")


def run(label: str, cmd: list[str], cwd: Path, timeout: int = 900) -> bool:
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=timeout, shell=False)
    ok = p.returncode == 0
    tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-4:])
    print(f"[{'PASS' if ok else 'FAIL'}] {label} ({time.time() - t0:.0f}s)")
    if not ok:
        print("       " + tail.replace("\n", "\n       "))
    return ok


def main() -> int:
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                           capture_output=True, text=True).stdout.splitlines()
    leaked = [f for f in files if f != ".env.example" and any(f.startswith(s) or f.endswith(s) or ("/" + s) in f for s in SENSITIVE)]
    leaked = [f for f in leaked if not f.startswith("Specification/PLAYRIX_SOURCE")]
    print(f"{len(files)} files would be committed; sensitive paths among them: {leaked or 'none'}")
    if leaked:
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="collection-studio-clean-"))
    try:
        for f in files:
            src = ROOT / f
            if src.is_file():
                dst = tmp / f
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        print(f"clean copy: {tmp}")
        venv = tmp / "backend" / ".venv"
        py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")  # path of the venv we are about to create
        npm = shutil.which("npm") or "npm"
        steps = [
            ("create venv", [sys.executable, "-m", "venv", str(tmp / "backend" / ".venv")], tmp),
            ("pip install backend", [str(py), "-m", "pip", "install", "-q", "-e", "backend[dev]"], tmp),
            ("backend tests (pytest)", [str(py), "-m", "pytest", "-p", "no:warnings"], tmp / "backend"),
            ("npm ci", [npm, "--prefix", "frontend", "ci", "--no-audit", "--no-fund"], tmp),
            ("UI build (tsc + vite)", [npm, "--prefix", "frontend", "run", "build"], tmp),
            ("offline smoke test", [str(py), "scripts/run_smoke_test.py"], tmp),
        ]
        results = [run(label, cmd, cwd) for label, cmd, cwd in steps[:1]]
        for label, cmd, cwd in steps[1:]:
            if not all(results):
                print(f"[SKIP] {label}")
                results.append(False)
                continue
            if cmd[0] == npm and sys.platform == "win32":
                cmd = ["cmd", "/c", *cmd]
            results.append(run(label, cmd, cwd))
        print("\nCLEAN CHECKOUT:", "VERIFIED" if all(results) else "FAILED")
        return 0 if all(results) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
