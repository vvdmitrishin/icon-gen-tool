# AI Collection Studio

Local web app for the Playrix AI Producer assignment. It plans and generates **collectible-card artwork** (artwork only: no
frames, stars or game UI) for themed 10-card sets, checks it, lets a human review and approve it, and exports it.

Source of truth: [`Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.10.md`](Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.10.md) ·
Docs: [Progress and handoff](docs/PROGRESS.md) · [User guide](docs/USER_GUIDE.md) · [Architecture](docs/ARCHITECTURE.md) · [Runbook](docs/RUNBOOK.md) · [Known issues](docs/KNOWN_ISSUES.md)

## Quick start (Windows)

```powershell
.\start.bat          # first run: creates .env, venv, installs, builds the UI, opens http://127.0.0.1:8000
```

On the first run, when no provider is configured, a 3-step **setup guide** opens (Welcome with "Try the demo without a key",
Connect providers, Done). Connect Google Gemini (API key) and/or Cloudflare Workers AI (Account ID + API token) in
**Settings > API Connections**; they are stored in Windows Credential Manager, not in files. The guide can be reopened from
Settings ("Run setup guide again") or the header button "Connect providers". The git-ignored `.env` still works as the
development fallback (Credential Manager first, then `.env`). Without any key you can try everything with the free **Mock** provider.
`start.bat dev` runs the Vite dev server (hot reload) instead. `python scripts\check_env.py` shows what is configured
(never prints a secret).

## What it does

| Step | Capability |
|---|---|
| Plan | AI semantic planner (Collection + Set + Free Wishes; one mechanism for every theme, no curated pools; avoids repeats across batches), 1–5 variants, **Card Category Distribution** (Auto `[2,2,3,3]` or Custom; Cat. 5 locked 0) applied to category slots, plan QA |
| Connect | Settings > API Connections: add/replace/remove Gemini and Cloudflare credentials (Windows Credential Manager), free "Test connection", first-run setup guide |
| Generate | Gemini Nano Banana 2.1 (primary, model selectable), Cloudflare FLUX.2 Klein 4B (up to 4 references) and FLUX.1 Schnell (text-only) on the Workers Free allocation, OpenAI GPT Image (listed, **disabled**), Mock (free). References per category; one call per card; sequential; retries off; no silent provider fallback |
| Check | Technical QA per image (format, size, aspect, blank, integrity, metadata) and set QA (completeness, distribution, duplicates, rejects) |
| Review | Approve / reject cards, repair-note **regeneration of one card** (versions kept), replace a card's object, approve one variant as final |
| Compare | Reference Compare: Split Slider, Side by Side (1×1), Grid Overview; auto/manual reference match; Compare Variants |
| Configure | Versioned **Prompt Library**, **Templates** (presets), read-only **Settings**, **History** of jobs/exports |
| Deliver | ZIP export per variant (artwork + manifest + set plan + QA report + contact sheet) and a three-genre **final bundle**; collection readiness overview; batch "plan all three genres" |
| Safety | Cost estimate + explicit confirmation, estimated cost tracked in a local ledger (no spending caps), circuit breaker, no auto-resume of paid work after a restart, keys only in Credential Manager / `.env`, loopback-only API |

## Implementation status (honest)

| Area | Status |
|---|---|
| Phases 0–7 of the plan | implemented; **275 backend tests pass, 1 skipped** (the paid live Gemini test, `RUN_LIVE=1`); frontend has typecheck + build only (no unit tests); offline smoke test (`scripts/run_smoke_test.py`) |
| API Connections + setup guide | implemented. Verified in a real browser against an isolated harness (in-memory credential store, mocked providers): 18/18 checks, no secret in browser storage, DOM or API responses. The real Windows Credential Manager round trip is covered by one Windows-only automated test; no fake credentials were saved to the real credential store |
| Gemini | **verified live** in earlier sessions: text-only image, reference image, 4-card pilot, one full 10-card set. This sprint made no paid or live provider calls; everything since is mocked |
| Cloudflare (FLUX.2 Klein 4B, FLUX.1 Schnell) | implemented from the documented REST contract; **mock-tested only, NOT verified live**. A model becomes "Live in app" only after a real request succeeds. Live check: `scripts/smoke_cloudflare.py` (needs your explicit approval; no Account ID on the dev machine yet). The free tier is a daily allocation (10,000 Neurons/day), not unlimited |
| OpenAI GPT Image | in registry/UI/settings, **disabled, no network code** |
| Model roadmap (spec §12.7) | future backlog, **not MVP scope**: the selector is grouped by runtime and lists Seedream 4.5 (via Cloudflare) and local FLUX.2 Klein 4B, Z-Image, ERNIE-Image, Qwen-Image as **planned / disabled**; nothing is downloaded or enabled |
| Art quality | **not tuned** (by decision). Known: Cat. 3 drifts to full scenes and can render text; Cat. 4 can be dark. See [known issues](docs/KNOWN_ISSUES.md) |
| UI | follows `Specification/Mockup.png` closely (pipeline strip, title row, Set Summary, Quick Actions, Next Steps, category chips); remaining deliberate deviations in [known issues](docs/KNOWN_ISSUES.md) |
| Not implemented | TextPlanner (free-form genres/wishes), Vision/LLM QA, automatic provider fallback, Miro automation (manual per spec), multi-user, frontend unit tests |

## Commands

```powershell
backend\.venv\Scripts\python -m pytest                       # offline tests (free), from the backend folder
backend\.venv\Scripts\python scripts\run_smoke_test.py       # offline end-to-end walk-through (mock)
python scripts\verify_clean_checkout.py                      # proves it works from only the files Git would contain
backend\.venv\Scripts\python scripts\smoke_gemini.py --yes   # ONE real paid Gemini image (dry run without --yes)
backend\.venv\Scripts\python scripts\smoke_cloudflare.py     # dry run; add --yes to send ONE real Cloudflare request (free allocation, needs approval)
backend\.venv\Scripts\python scripts\export_benchmark.py --job <id> --name <folder>   # freeze a job as a local benchmark
```

## Secrets and rights

- API keys live only in Windows Credential Manager (entered in Settings > API Connections) or the local `.env` (development
  fallback). The backend reads them; they never reach browser storage, SQLite, logs, API responses or the exports.
- `.env`, `data/` (generated images, references, ledger, database, exports), `benchmarks/` and the Playrix reference PNGs are
  git-ignored. The provided Playrix references are **not** part of this repository.
- Costs shown are **local estimates**; Google Billing is the source of truth.
