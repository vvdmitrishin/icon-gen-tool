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
| `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` | both are needed to enable Cloudflare (or enter them in Settings > API Connections) |
| `CLOUDFLARE_PLAN` (`free` default / `paid`) | `free`: estimate 0, no confirmation. `paid`: published-price estimate, confirmation and budget caps apply as for Gemini |
| `CLOUDFLARE_EST_COST_PER_IMAGE_USD` (optional) | overrides the per-image estimate used when `CLOUDFLARE_PLAN=paid` |
| `OPENAI_IMAGE_ENABLED=false` | leave off; there is no OpenAI network code |
Restart the backend after editing `.env` (credentials entered in the UI apply immediately).

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
| Cloudflare disabled | Add the Account ID as well (the token alone is not enough): Settings > API Connections or `.env` |
| Cloudflare job stops with `quota_exceeded` (code 3036 / HTTP 429) | The 10,000 Neurons/day Workers Free allocation is used up (resets 00:00 UTC). Nothing was charged and the job stopped after one call. Wait, or choose another model/provider manually. On Workers Paid set `CLOUDFLARE_PLAN=paid` so caps and confirmation apply. The app never upgrades the plan or switches provider by itself |
| Cloudflare model shows "Configured", not "Live in app" | Expected until one real request succeeds. See `scripts\smoke_cloudflare.py` below |
| Setup guide keeps opening | No provider is configured yet (Credential Manager or `.env`); connect one or choose the demo |
| API Connections calls refused | Request is not loopback or lacks the session token; use http://127.0.0.1:8000 and reload the page |
| Port 8000 busy | A previous backend is still running; close it |
| UI shows old code | `npm --prefix frontend run build`, restart |

## Backup / reset
- Back up the whole `data/` folder (images, references, database, ledger, exports). `benchmarks/` holds frozen comparison sets.
- Fresh start: stop the app and move `data/` aside. Never delete `data/ledger.jsonl` if you still use the same prepaid balance.

## API Connections (Settings > API Connections)
- Keys entered in the UI are stored in **Windows Credential Manager** (service `AICollectionStudio`, entries `gemini.api_key`,
  `cloudflare.account_id`, `cloudflare.api_token`), DPAPI-protected. They are never written to `.env`, SQLite, logs or browser storage,
  and the API never returns them (only "stored" booleans).
- Precedence: Credential Manager value, else `.env` value, else not configured. `.env` remains the development fallback;
  removing a stored credential in the UI falls back to `.env`. Changes apply immediately, no restart.
- To remove manually: Windows "Credential Manager" > Windows Credentials > delete the `AICollectionStudio` entries
  (or use Remove in the UI). `python scripts\check_env.py` shows (yes/no only) which credentials exist there.
- "Test connection" only calls free metadata endpoints (Gemini ListModels, Cloudflare model search); it never generates an image.
- The endpoints accept only loopback Host/Origin headers plus a per-session token (`X-Studio-Token`, from `GET /api/session`);
  test calls are rate limited. The whole API also has a loopback Host/Origin guard in the real app; keep the backend bound to 127.0.0.1.
- Error responses never echo submitted values, and httpx log lines redact the Cloudflare account id.
- Removing a credential: use Remove in the UI (falls back to `.env` if set there), or delete the entries in Credential Manager as above.

## Cloudflare live smoke test
`backend\.venv\Scripts\python scripts\smoke_cloudflare.py` is a dry run by default (prints what it would do). `--yes` sends
**one** real request and uses part of the free daily allocation, so run it only with explicit approval and with the Account ID and
token configured. Nothing has been sent yet; after a success the model can show as "Live in app". Expected estimates (Cloudflare
docs, unverified): Klein ~110 Neurons, Schnell ~58 Neurons per 1024x1024 image.

## Secrets hygiene
- Before committing: `git status`, confirm no `.env`, `data/`, `benchmarks/`, Playrix PNGs. `python scripts\verify_clean_checkout.py`
  also refuses to proceed if a sensitive path would be committed.
- If a key was ever exposed: **rotate it** in Google AI Studio / Cloudflare, then update `.env`. Deleting a file does not
  undo exposure. The Git history of this repository was scanned and contains no credentials.

## Benchmarks
`scripts\export_benchmark.py --job <id> --name <folder>` freezes a finished job (PNG, prompts, parameters, references, cost
estimates, SHA-256) read-only under `benchmarks/` for comparing other models. `phase3_gemini_baseline` is the first one.
