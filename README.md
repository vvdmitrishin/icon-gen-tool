# AI Collection Studio

Local web app that produces collectible-card **artwork** (not game frames) for the Playrix AI Producer
assignment. The source of truth is [`Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.6.md`](Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.6.md).

## Implementation status (honest)

| Area | Status |
|---|---|
| Phase 0 – scaffold, `.gitignore`, `.env.example` | implemented |
| Phase 1 – single-card vertical slice (input → prompt compiler → Gemini → PNG → metadata → UI) | implemented; **unit/integration tests use a mocked Gemini transport**; a live call needs your key (see below) |
| Reference upload + reference-based generation | implemented (validated, re-encoded, policy checkbox) |
| Cost guard (per-call + cumulative ledger, retries OFF, no batches) | implemented – local estimate only |
| Cloudflare FLUX.2 Klein 4B fallback | registered, **adapter pending (Phase 2)** |
| OpenAI GPT Image | registered in registry/UI, **disabled, no network code** |
| 10-card sets, 3–5 variants, planner, QA, regenerate, Reference Compare, export | **not implemented yet** (Phases 3–6) |

## Run (Windows)

```powershell
Copy-Item .env.example .env          # then put GEMINI_API_KEY in .env (git-ignored)
cd backend; python -m venv .venv; .\.venv\Scripts\pip install -e ".[dev]"; cd ..
cd frontend; npm install; cd ..

# terminal 1
backend\.venv\Scripts\python -m uvicorn app.main:app_factory --factory --app-dir backend --port 8000
# terminal 2
npm --prefix frontend run dev        # http://127.0.0.1:5173
```

## Tests

```powershell
cd backend; .\.venv\Scripts\python -m pytest          # offline, free
$env:RUN_LIVE="1"; .\.venv\Scripts\python -m pytest -m live   # real paid call (~1 image)
```

## Staged live verification (spends real money; each step needs `--yes`)

```powershell
backend\.venv\Scripts\python scripts\smoke_gemini.py                       # dry run, shows estimate
backend\.venv\Scripts\python scripts\smoke_gemini.py --yes                 # 1) single text-only image
backend\.venv\Scripts\python scripts\smoke_gemini.py --yes --ref <png> --ref-type category_1   # 2) reference pilot
```

## Secrets and rights

- API keys live only in the local `.env` and are read by the backend; they never reach the browser bundle.
- `.env`, `data/` (generated outputs, uploaded references, cost ledger) and the Playrix reference PNGs are git-ignored.
  Provided Playrix references are not part of this repository.
