# Progress log and handoff

Last updated: 2026-11-09 (after UI stabilization round 2, uncommitted). Source of Truth: `Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.9.md`
(content v1.10: see its 'ОНОВЛЕННЯ v1.10' block; v1.9: §6.15, §12.7.1, §19.1.1, ADR-017). Last commit: `1493ffb` (round 1); round 2 is not committed yet. Mockup: `Specification/Mockup.png`.

## Status in one paragraph
Working local MVP (Phases 0-7 plus the Production-Ready MVP Sprint). Backend: 367 passed, 1 skipped (paid live Gemini test,
`RUN_LIVE=1`). Frontend: typecheck + build only (no unit tests). Clean-checkout verification passed (`scripts/verify_clean_checkout.py`).
Pushed to `origin/main`: up to `a101067`. Local, NOT pushed: `1493ffb` (UI feedback round 1) plus the uncommitted round-2 stabilization. Push only when asked.

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

## Round 2 stabilization (uncommitted, awaiting producer review)
- **API Connections:** per-field editing (root cause: Cloudflare token present via `.env` made the whole card read-only, so the Account ID
  could not be typed). Saving only the Account ID keeps the `.env` token. Precedence: Credential Manager > `.env`. "Remove from this PC"
  is local only (not provider-side revocation) and says so when `.env` still provides the value. Verified in a real browser on the isolated
  harness with fake values; the real vault and keys were not touched.
- **Set** is free text (chips only suggest). Collection + Set go to the AI planner; the curated rules planner is used only for movie
  collections with Sci-Fi/Detective/Fantasy. Custom themes need an explicit "Plan with AI" (~$0.002 text call; not yet run live).
- **Output Resolution** (24-1024, default 1024) is the only size setting; the final PNG has exactly that size (original kept as
  `<id>.original.png`, QA check TQ-10, manifest `output_pixel_size`).
- **Cards/gold:** N = 1..10, gold 0..N, Σ always = N, no silent count changes, compliance flag; golden/short jobs are not
  `final_eligible` for the Movie Collection bundle. Matrix N x G tested (65 combos).
- **Model selector** overlap fixed (fixed-position list); previous-run vs current-form strip added.

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
cd backend; .\.venv\Scripts\python -m pytest                 # offline tests (367 passed, 1 skipped)
npm --prefix frontend run build                              # typecheck + build
python scripts\verify_clean_checkout.py                      # re-prove it works from only the committable files
```
