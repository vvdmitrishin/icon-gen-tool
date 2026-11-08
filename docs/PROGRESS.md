# Progress log and handoff

Last updated: 2026-11-08 (end of sprint). Spec: `Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.9.md` (v1.9: sections 6.15, 12.7.1, 19.1.1, ADR-017).

## Where things stand
- **Working local MVP, Phases 0–7 plus the API Connections / Cloudflare / UI-polish sprint.** Backend: 275 passed, 1 skipped
  (the paid live Gemini test, `RUN_LIVE=1`). Frontend: typecheck + build only. Offline smoke test via `scripts/run_smoke_test.py`;
  clean-checkout verifier `scripts/verify_clean_checkout.py`.
- **Git:** nothing is pushed. `origin/main` is at the Phase 3 commit; later work is local commits (see `git log`).
  Push only when asked.
- **Secrets:** none in the tree or Git history (scanned earlier). Keys live in Windows Credential Manager (entered in the UI) or
  the git-ignored `.env` (Gemini key; Cloudflare token, no Account ID on this machine).
- **App:** `start.bat` → http://127.0.0.1:8000 (UI served by the backend).

## What this sprint delivered
1. **API Connections (priority 1):** Settings > API Connections to add/replace/remove Gemini and Cloudflare credentials
   (Windows Credential Manager, `.env` fallback), free "Test connection", loopback-only guarded endpoints with session token,
   first-run setup guide (also "Run setup guide again" and header "Connect providers"). Verified in a real browser against an
   isolated harness (18/18, no secret in storage/DOM/API responses); the real Credential Manager round trip has one Windows-only
   test. No fake credentials were saved to the real store.
2. **Cloudflare free-tier models (priority 3):** FLUX.2 Klein 4B (references up to 4) and FLUX.1 Schnell (text only), plan-aware cost
   guard (`CLOUDFLARE_PLAN`), quota handling (`quota_exceeded`), `scripts/smoke_cloudflare.py`. **Not verified live.**
3. **UI polish (priority 2):** UI follows the mockup closely (pipeline strip, title row, tabs, Set Summary, Quick Actions, Next Steps,
   chips). Remaining deviations are deliberate; see [known issues](KNOWN_ISSUES.md).
4. Spec v1.9, docs refreshed.
No paid or live provider calls were made this sprint.

## Local, untracked state (not in Git by design)
- `data/` — database, generated images, references, exports and `ledger.jsonl`. **Back it up if you care about it.**
- `benchmarks/phase3_gemini_baseline/` — frozen read-only benchmark. `screenshots/` — git-ignored, some show the confidential reference.
- Estimated Gemini spend so far: **~$0.80** of the $4.00 local cap (local estimate; check Google AI Studio billing).
  A first full live 10-card set exists (Detective / Noir City Nights; technical QA 10/10; art not reviewed).

## Open items (the backlog for the next session)
1. **Cloudflare live check:** add the Account ID (Settings > API Connections), then run `scripts/smoke_cloudflare.py --yes`
   (one request, free allocation) with the user's explicit approval. Only then can the models become "Live in app".
2. **Art tuning** (Cat. 3 scene drift + readable text, Cat. 4 too dark, only a Cat. 1 reference so far), Style Bible validation
   against the Playrix frames; compare each round with the benchmark.
3. **Assignment deliverables not written:** `docs/REFERENCE_ANALYSIS.md`, `docs/ART_RUBRIC.md`, `docs/EXPERIMENT_LOG.md`,
   product/visual comparison, three approved final sets (Sci-Fi, Detective, Fantasy; 3–5 variants each), Miro placement (manual).
4. **Budget:** full 3 genres × 3 variants ≈ $6 > €5 on Gemini. Decide: top-up, fewer variants, staged, or the Cloudflare free allocation.
5. **Verify with real keys:** UI connection flow against the real Credential Manager (only the harness and one automated test so far).
6. **Post-MVP backlog:** ComfyUI adapter + local models, Seedream 4.5 (needs approval, billing check, own cost rate),
   TextPlanner, frontend unit tests.

## Resume
```powershell
.\start.bat                                                  # app on http://127.0.0.1:8000
backend\.venv\Scripts\python scripts\check_env.py            # what is configured (no secret values printed)
cd backend; .\.venv\Scripts\python -m pytest                 # offline tests (275 passed, 1 skipped)
npm --prefix frontend run build                              # typecheck + build
python scripts\verify_clean_checkout.py                      # re-prove it works from only the committable files
```
Rules that still apply: never paste keys into chat; no paid or live generation without explicit approval/in-app confirmation;
OpenAI stays disabled; planned models stay disabled; do not push unless asked.
