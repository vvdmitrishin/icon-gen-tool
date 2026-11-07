# AI Collection Studio

Local web app for the Playrix AI Producer assignment. It plans and generates **collectible-card artwork** (artwork only: no
frames, stars or game UI) for themed 10-card sets, checks it, lets a human review and approve it, and exports it.

Source of truth: [`Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.8.md`](Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.8.md) ·
Docs: [User guide](docs/USER_GUIDE.md) · [Architecture](docs/ARCHITECTURE.md) · [Runbook](docs/RUNBOOK.md) · [Known issues](docs/KNOWN_ISSUES.md)

## Quick start (Windows)

```powershell
.\start.bat          # first run: creates .env, venv, installs, builds the UI, opens http://127.0.0.1:8000
```

Put your `GEMINI_API_KEY` in the git-ignored `.env`. Without a key you can still try everything with the free **Mock** provider.
`start.bat dev` runs the Vite dev server (hot reload) instead. `python scripts\check_env.py` shows what is configured
(never prints a secret).

## What it does

| Step | Capability |
|---|---|
| Plan | Rules planner with curated object pools (Sci-Fi, Detective, Fantasy; 5 micro-themes each), 1–5 variants, **Card Category Distribution** (Auto `[2,2,3,3]` or Custom; Cat. 5 locked 0) applied to category slots, plan QA |
| Generate | Gemini Nano Banana 2.1 (primary, model selectable), Cloudflare FLUX.2 Klein 4B (fallback), OpenAI GPT Image (listed, **disabled**), Mock (free). References per category; one call per card; sequential; retries off |
| Check | Technical QA per image (format, size, aspect, blank, integrity, metadata) and set QA (completeness, distribution, duplicates, rejects) |
| Review | Approve / reject cards, repair-note **regeneration of one card** (versions kept), replace a card's object, approve one variant as final |
| Compare | Reference Compare: Split Slider, Side by Side (1×1), Grid Overview; auto/manual reference match; Compare Variants |
| Configure | Versioned **Prompt Library**, **Templates** (presets), read-only **Settings**, **History** of jobs/exports |
| Deliver | ZIP export per variant (artwork + manifest + set plan + QA report + contact sheet) and a three-genre **final bundle**; collection readiness overview; batch "plan all three genres" |
| Safety | Cost estimate + explicit confirmation, per-run and cumulative caps from a local ledger, circuit breaker, no auto-resume of paid work after a restart, keys only in `.env` |

## Implementation status (honest)

| Area | Status |
|---|---|
| Phases 0–7 of the plan | implemented; **171 automated backend tests pass**, offline smoke test 16/16, UI build clean |
| Gemini | **verified live**: text-only image, reference image, 4-card pilot (Phase 3). Everything since is tested against a mocked transport |
| Cloudflare adapter | implemented from the documented REST contract; **mock-tested only** until `CLOUDFLARE_ACCOUNT_ID` is set for a live check |
| OpenAI GPT Image | in registry/UI/settings, **disabled, no network code** |
| Model roadmap (spec §12.7) | future backlog, **not MVP scope**: the selector is grouped by runtime and lists Seedream 4.5 (via Cloudflare) and local FLUX.2 Klein 4B, Z-Image, ERNIE-Image, Qwen-Image as **planned / disabled**; nothing is downloaded or enabled |
| Art quality | **not tuned** (by decision). Known: Cat. 3 drifts to full scenes and can render text; Cat. 4 can be dark. See [known issues](docs/KNOWN_ISSUES.md) |
| Not implemented | TextPlanner (free-form genres/wishes), Vision/LLM QA, automatic provider fallback, Miro automation (manual per spec), multi-user, frontend unit tests |

## Commands

```powershell
backend\.venv\Scripts\python -m pytest                       # offline tests (free), from the backend folder
backend\.venv\Scripts\python scripts\run_smoke_test.py       # offline end-to-end walk-through (mock)
python scripts\verify_clean_checkout.py                      # proves it works from only the files Git would contain
backend\.venv\Scripts\python scripts\smoke_gemini.py --yes   # ONE real paid Gemini image (dry run without --yes)
backend\.venv\Scripts\python scripts\export_benchmark.py --job <id> --name <folder>   # freeze a job as a local benchmark
```

## Secrets and rights

- API keys live only in the local `.env` and are read by the backend; they never reach the browser or the exports.
- `.env`, `data/` (generated images, references, ledger, database, exports), `benchmarks/` and the Playrix reference PNGs are
  git-ignored. The provided Playrix references are **not** part of this repository.
- Costs shown are **local estimates**; Google Billing is the source of truth.
