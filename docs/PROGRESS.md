# Progress log and handoff

Last updated: 2026-11-08 (after the Production-Ready MVP Sprint). Source of Truth: `Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.9.md`
(v1.9: §6.15, §12.7.1, §19.1.1, ADR-017). Git HEAD: `89293db`. Mockup: `Specification/Mockup.png`.

## Status in one paragraph
Working local MVP (Phases 0-7 plus the Production-Ready MVP Sprint). Backend: 275 passed, 1 skipped (paid live Gemini test,
`RUN_LIVE=1`). Frontend: typecheck + build only (no unit tests). Clean-checkout verification passed (`scripts/verify_clean_checkout.py`).
Pushed to `origin/main` (HEAD `5d43a24`, docs on top of sprint commit `89293db`). Push only when asked.

## Completed: Production-Ready MVP Sprint
1. **API Connections (P1):** Settings > API Connections to add/replace/remove Gemini (key) and Cloudflare (Account ID + token).
   Stored by the Python backend in **Windows Credential Manager** (`keyring`, service `AICollectionStudio`); `.env` is a dev fallback
   (vault wins). Secrets never in browser storage, SQLite, logs, Git or API responses. Free "Test connection" (lists models, never
   generates). Loopback-only endpoints + per-session token + rate limit; whole-API Host/Origin guard in the real app. First-run
   setup wizard (with "Try the demo without a key"). Verified in a real browser on an isolated harness (18/18); NOT yet with real keys
   in the real vault.
2. **UI (P2):** aligned to the mockup (6-step pipeline, title row, tabs, Set Summary, Quick Actions, Next Steps), no horizontal scroll
   at 1672x940 / 1366x768 / 1280x720.
3. **Cloudflare free models (P3):** FLUX.2 Klein 4B (text + up to 4 refs, downscaled <512x512) and FLUX.1 Schnell (text only), plan-aware
   cost guard (`CLOUDFLARE_PLAN`), `quota_exceeded` stops the job (no silent fallback, no auto-upgrade). Offline/mocked only.
4. P4 (art-quality tuning) not started.

## Providers (verified state)
| Provider / model | State |
|---|---|
| **Gemini Nano Banana 2.1** | **Live, verified primary** (real 10-card Detective set, QA 10/10) |
| **Cloudflare FLUX.2 Klein 4B** | Implemented, **awaiting live verification** (no Account ID on this machine) |
| Cloudflare FLUX.1 Schnell | Implemented, not live (text only) |
| Mock | Free test provider, clearly labelled |
| OpenAI GPT Image | Disabled, no network code |
| Seedream 4.5, local models | Planned/disabled (backlog) |

**Agreed shortlist:** Gemini Nano Banana 2.1 (primary), Cloudflare FLUX.2 Klein 4B (free fallback), local **Z-Image-Turbo**
(planned, not integrated; no weights/runtime installed).

## UI status and known limitations
- Deliberate deviations from the mockup: no style score / best match / sliders / fake cost-time figures; Cat. 1-4 steppers with
  Auto/Custom (Cat. 5 locked 0); Collection is plain text; single draft Style Bible; Image model and Planning stacked; simple glyph icons.
- QA is structural only (cannot detect text, frames or wrong style): human review required.
- Planner is rules-based: only Sci-Fi, Detective, Fantasy; free wishes steer image prompts only. Max 5 distinct variants per genre.
- Credential storage is Windows-only; one final variant per job; one running job at a time; single user.
- Multi-variant layout verified structurally only; Continue/Cancel/Approve/real export not re-exercised in the last UI pass.
- Full list: `docs/KNOWN_ISSUES.md`.

## Pending (next session, in rough order)
1. **Cloudflare live smoke test:** add the Account ID in Settings > API Connections, get explicit user approval, then
   `scripts/smoke_cloudflare.py --model klein --yes` (ONE request). Only after a real success may the model be called "live".
2. **Custom Collection / Set Text Planner (TextPlanner):** arbitrary collection/set themes and free text (today unknown genres are rejected).
3. **Z-Image-Turbo integration** (local, ComfyUI or similar adapter); needs approval before downloading weights/installing runtimes.
4. **Art-quality work (P4):** Cat. 3 scene drift + readable text, Cat. 4 too dark, category-specific references, Style Bible validation vs
   Playrix frames; compare every round with `benchmarks/phase3_gemini_baseline` (local, untracked).
5. **Remaining assignment deliverables:** `docs/REFERENCE_ANALYSIS.md`, `docs/ART_RUBRIC.md`, `docs/EXPERIMENT_LOG.md`, product/visual
   comparison, three approved final sets (Sci-Fi, Detective, Fantasy; 3-5 variants each), Miro placement (manual).
6. Real-vault check of the Connections UI with real keys (never save fake values to the real store); frontend unit tests;
   Seedream 4.5 (needs approval, billing check).

## Security, budget and rules (still apply)
- Never paste keys into chat; no secrets in Git (last scan: 0 hits in tree and history). `.env`, `data/`, `benchmarks/`, `screenshots/` are
  git-ignored; `*CONFIDENTIAL*` screenshots and `compare-reference.png` show the Playrix reference and must never be published or committed.
- No paid or live generation without explicit approval/in-app confirmation; no OpenAI; planned models stay disabled; no auto-retries.
- Caps: $0.75 per run, $4.00 cumulative (ledger `data/ledger.jsonl`); spent so far ~$0.80 (local estimate; Google Billing is the truth).
  Budget ~EUR 5; a full 3 genres x 3 variants x 10 cards on Gemini is ~$6, so stage it (pilot -> continue), reduce variants, or use the
  Cloudflare free allocation (10,000 Neurons/day, resets 00:00 UTC; not unlimited).
- Back up `data/` (database, images, ledger) if it matters; it is not in Git.

## Resume
```powershell
.\start.bat                                                  # app on http://127.0.0.1:8000
backend\.venv\Scripts\python scripts\check_env.py            # what is configured (no secret values printed)
cd backend; .\.venv\Scripts\python -m pytest                 # offline tests (275 passed, 1 skipped)
npm --prefix frontend run build                              # typecheck + build
python scripts\verify_clean_checkout.py                      # re-prove it works from only the committable files
```
