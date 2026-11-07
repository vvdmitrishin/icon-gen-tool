# Runbook

## Start / stop
- Start: `start.bat` (or `start.bat dev`). Stop: Ctrl+C in the console. State is in `data/`; stopping is safe.
- Health: `http://127.0.0.1:8000/api/health` · Self-check: `backend\.venv\Scripts\python scripts\check_env.py`.

## Configuration (`.env`, git-ignored; template: `.env.example`)
| Variable | Meaning |
|---|---|
| `GEMINI_API_KEY` | Gemini key (server-side only). Never paste it into chat, tickets or commits |
| `GEMINI_JOB_BUDGET_USD` (0.75) | max estimated spend of one run; fits one full 10-card variant (~0.67) |
| `GEMINI_TOTAL_BUDGET_USD` (4.00) | cumulative local cap; keep it below your prepaid balance |
| `GEMINI_EST_COST_PER_IMAGE_USD` (0.067) | estimate used by the guard; verify against Google Billing |
| `MAX_CARD_RETRIES` (0) / `MAX_REGENERATIONS_PER_CARD` (2) | automatic retries stay off; per-card regenerations |
| `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` | both are needed to enable the fallback provider |
| `OPENAI_IMAGE_ENABLED=false` | leave off; there is no OpenAI network code |
Restart the backend after editing `.env`.

## Cost control
- The ledger (`data/ledger.jsonl`) sums estimated spend across restarts and drives the cumulative cap. It is an **estimate**;
  compare with Google AI Studio billing regularly. To re-baseline after a top-up, raise `GEMINI_TOTAL_BUDGET_USD`
  (do not delete the ledger: it is your audit trail).
- A paid action always needs an explicit confirmation; the plan preview shows the estimate first.

## Common situations
| Symptom | Action |
|---|---|
| Job shows **Interrupted** | The server restarted mid-run. Finished cards are kept. Press **Continue** (optionally *retry failed*) |
| `402 job_cap / total_cap` | Cap reached. Lower variants/scope, or raise the cap in `.env` deliberately |
| Gemini `400` mentioning `imageConfig` | Set `GEMINI_SEND_IMAGE_CONFIG=false` |
| Gemini `429/5xx` | The circuit breaker stops after 2 failures; wait, then Continue |
| Cloudflare disabled | Add `CLOUDFLARE_ACCOUNT_ID` (the token alone is not enough) |
| Port 8000 busy | A previous backend is still running; close it |
| UI shows old code | `npm --prefix frontend run build`, restart |

## Backup / reset
- Back up the whole `data/` folder (images, references, database, ledger, exports). `benchmarks/` holds frozen comparison sets.
- Fresh start: stop the app and move `data/` aside. Never delete `data/ledger.jsonl` if you still use the same prepaid balance.

## Secrets hygiene
- Before committing: `git status`, confirm no `.env`, `data/`, `benchmarks/`, Playrix PNGs. `python scripts\verify_clean_checkout.py`
  also refuses to proceed if a sensitive path would be committed.
- If a key was ever exposed: **rotate it** in Google AI Studio / Cloudflare, then update `.env`. Deleting a file does not
  undo exposure. The Git history of this repository was scanned and contains no credentials.

## Benchmarks
`scripts\export_benchmark.py --job <id> --name <folder>` freezes a finished job (PNG, prompts, parameters, references, cost
estimates, SHA-256) read-only under `benchmarks/` for comparing other models. `phase3_gemini_baseline` is the first one.
