# Progress log and handoff

Last updated: 2026-11-09 (after Unified AI Planning & Session Prompt System, committed locally). Source of Truth:
`Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.10.md` (v1.12 update block / ADR-020: unified AI planner, session prompts, snapshots, prompt transparency; v1.11 / ADR-019: semantic planner, My Selection, Plan Only; v1.10 update block, section 3.5 Grand Set, ADR-018; v1.9: 6.15, 12.7.1, 19.1.1, ADR-017).
Last commit: see `git log` (UI stabilization `2f16222`, docs commit on top). Mockup: `Specification/Mockup.png`.

## Status in one paragraph
Working local MVP (Phases 0-7 plus the Production-Ready MVP Sprint). Backend: 427 tests collected, all pass except 1 skipped (paid live Gemini test,
`RUN_LIVE=1`). Frontend: typecheck + build only (no unit tests). Clean-checkout verification passed (`scripts/verify_clean_checkout.py`).
`origin/main` is at `a101067`; local-only (NOT pushed): `1493ffb` (UI feedback round 1), `2f16222` (stabilization P0-P5) and the documentation commit. Push only when asked.

## Completed: Unified AI Planning & Session Prompt System (v1.12, ADR-020; local commit, not pushed)
- **One planner:** curated pools / rules planner / `planner` option removed from production (`object_pools.yaml`, `plan_set`, `curated_genre_for` deleted;
  old jobs and exports still load). Movies / Sci-Fi, Detective, Fantasy use the same AI semantic planner as any theme. No silent fallback: if the text
  planner is unavailable the job is refused with an actionable error before any image is generated. Deterministic plans exist only as test fixtures
  (`tests/conftest.py`) and in `scripts/run_smoke_test.py`. Mock *image* provider unchanged (clearly labelled); planning still needs a Gemini key.
- **Session prompt overrides:** Prompt Library > "Session (this tab only)": Edit > Apply to Session > Reset to Default / Reset All (System Style, Category 1-5,
  Closing). `sessionStorage` only (kept on refresh, empty in a new tab); permanent library untouched ("Permanent versions" is a separate mode).
  "Session Override Active" indicator in the Prompt Library and next to Generate; sent with every request.
- **Immutable per-job snapshot:** the full prompt bundle (texts + origin + permanent version) is resolved ONCE at job creation and stored in the job
  request; all cards and regenerations use it (library/session edits mid-job have no effect); old jobs without a snapshot still load.
- **Mandatory rules:** while an override is active the server appends a MANDATORY block (artwork only / no text, frames, stars; golden <= 2 characters)
  and warns about contradicting text (`POST /api/prompts/session-check`).
- **Set context in prompts:** Collection, Set, variant title + concept and the planner's short `rationale` go into every card prompt (shared art direction
  only). **References:** only applicable ones (category-N -> category N only, new `character` type -> golden cards only, style/composition/negative -> all);
  the "send everything" fallback is gone (`none_applicable` -> text rules only). Provider adapters unchanged.
- **Transparency:** "View Prompt" in card detail (exact stored prompt, model, refs, template versions, SESSION markers, Copy); optional "Preview Prompts"
  (stored prompts for generated images + compiled previews for planned ones; same compiler as real generation; no image API, no approval step).
- **Verified:** backend tests incl. `test_prompt_system.py` (snapshot immutability, session override, preview == real prompt, no-fallback, references);
  frontend typecheck + build; real browser on the real UI/code (isolated data folder + stub text planner + Mock images): Movies/Sci-Fi and Winter/Forest via
  the AI planner, override indicator, Mock jobs with override, edit again (old images keep their prompt), reset, refresh keeps overrides, new tab = defaults,
  View Prompt, Preview Prompts, My Selection + export; launcher cold start.
- **Not verified:** real LLM plan quality (no paid text calls), effect of prompt changes on real artwork, Cloudflare Klein references live.
- **Text planner model (fix after the first live Plan Only returned HTTP 404 for `gemini-2.5-flash`):** Plan Only and Generate now both use
  `gemini-3.5-flash-lite` (stable; structured outputs supported per Google docs; `ListModels` for this project's key lists it with `generateContent`).
  `temperature` removed from the request (deprecated on Gemini 3.x). Cost estimate now uses the real list price ($0.30/1M in, $2.50/1M out incl. thinking):
  `base + per_card * variants * cards` (~$0.004 for 1x10, ~$0.015 for 5x10), served to the UI by `GET /api/models -> planner`. Offline tests only
  (`tests/test_text_model.py`); the next live Plan Only run is yours to confirm (the real call has not been made by me).
- **Remove (skip) a variant:** a red `✕ Remove variant` button (two-step confirm) on EVERY variant (with or without generated images; the image files stay in History), `DELETE /api/jobs/{id}/variants/{vid}`. Drops its cards, versions, approval and the variant from the stored plan so Continue no longer spends on it; refused only for the last variant and while the job runs. Tests: `tests/test_variant_remove.py`.
- **Prompt System v0.6 replaces v0.5 as the production default (v3):** the seven image templates (verbatim, `backend/app/config/prompt_system_v06.yaml`) are saved once as new permanent versions and active for all new generations on Gemini and Cloudflare; v1 and v2 stay restorable, templates stay editable (5,000 chars), Producer edits/rollbacks are never overridden, old jobs and snapshots unchanged. Offline tests: `tests/test_prompt_releases.py`. Image-quality effect not measured.
- **Prompt System v0.5 was the previous production default (now v2):** the seven image templates (verbatim from the reviewed v0.5 Markdown, stored in `backend/app/config/prompt_system_v05.yaml`) are saved ONCE as new permanent Prompt Library versions (v2) and active for every new generation; v1 stays for rollback, all seven remain editable in the Prompt Library, `repair` untouched, edited templates never overridden. Template limit raised 3,000 -> 5,000 characters (server `MAX_TEXT` + UI counter). Old jobs/snapshots unchanged. Offline tests only (`tests/test_prompt_v05.py`); image-quality effect not measured.
- **Planner quality tuning (instruction-only, no architecture change):** the single planner prompt now asks for a clear focal subject on Cat.1-4 cards (not applied to Grand Set / golden character scenes), a Category 4 hero subject inside a rich environment (Cat.1-3 definitions untouched), respect for the Set's defining meaning (core subject vs related gear, self-checked in the same call, no keyword lists), and variety as a preference, not a quota. Validation, duplicate safeguards, distribution, Grand Set rules, cost model unchanged. Offline tests: `tests/test_planner_quality.py`. Real LLM effect not measured.
- Note: this machine shows rare random interpreter crashes (Python/Node segfaults, flaky pytest assertion rewriting); full suites pass on re-run
  (`pytest --assert=plain` avoids the rewrite path).

## Completed: Semantic Set Planner, Session Favorites, Plan Only (v1.11, ADR-019; local commit, not pushed)
- **Planner:** AI semantic planner for any Collection/Set (the curated Movie-preset branch of this step was removed in v1.12).
  Prompt treats the Set as the semantic boundary, no subject quotas. Plans are rejected before any image if they contain near-duplicate
  subjects, subjects the Free Wishes excluded ("no buildings" -> house/cabin/hut...), or golden cards without 1-2 characters.
- **Cross-batch history:** subjects of earlier jobs of the same Collection + Set + Set type (<= 48, name + <= 48-char hint, deduplicated) go into the
  prompt as a *preference* for fresh subjects (never a hard ban); other themes are never mixed in.
- **Generate:** plans with AI automatically (text ~$0.002, shown on the button and included in the "Confirm & spend" click, also for free Mock images).
  The free preview never calls the AI; "Preview plan (optional)" does. No approve/replace/edit step is required.
- **Plan Only:** `POST /api/plan/test` + "Test planner (no images)" button: real planner, no image provider, no job; shows cards, categories, text
  model, cost, cached flag, history size. Text backend is a seam (`TextPlanner(backend=...)`); no Cloudflare text LLM yet.
- **My Selection:** star on every set image (grid + card detail), stored in sessionStorage per Collection + Set + Set type, survives refresh and new
  batches, exact image (artifact id) linked to job / card / variant / model / batch. **Export Selected** -> ZIP (artwork + originals + manifest +
  report); compliance problems are warnings, not blocks. Batch #N chip per job.
- **Verified:** backend tests (new: `test_semantic_planner.py`, mocked Gemini only, no paid calls); frontend typecheck + build; real browser with the
  free Mock model: two consecutive batches (#6, #7), stars kept across them and a page refresh, isolated per Set (Detective empty, Sci-Fi restored),
  unstar, Export Selected ZIP from two jobs with warnings.
- **Not verified:** the quality of real LLM plans (no paid text calls were made), Plan Only button against live Gemini, stars on single-card images.

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

## Completed: UI stabilization P0-P5 (commit `2f16222`)
- **API Connections:** per-field editing (root cause: Cloudflare token present via `.env` made the whole card read-only, so the Account ID
  could not be typed). Saving only the Account ID keeps the `.env` token. Precedence: Credential Manager > `.env`. "Remove from this PC"
  is local only (not provider-side revocation) and says so when `.env` still provides the value. Verified in a real browser on the isolated
  harness with fake values; the real vault and keys were not touched.
- **Set** is free text (chips only suggest). Collection + Set go to the AI planner (v1.12: no curated planner any more; Generate plans automatically,
  ~$0.002 text call, "Preview plan (optional)" is the only explicit AI step; not yet run live).
- **Output Resolution** (24-1024, default 1024) is the only size setting; the final PNG has exactly that size (original kept as
  `<id>.original.png`, QA check TQ-10, manifest `output_pixel_size`).
- **Cards/gold:** N = 1..10, gold 0..N, Σ always = N, no silent count changes, compliance flag; golden/short jobs are not
  `final_eligible` for the Movie Collection bundle. Matrix N x G tested (65 combos).
- **Model selector** overlap fixed (fixed-position list); previous-run vs current-form strip added.
- **Verified:** backend 367 passed, 1 skipped (exit 0); frontend typecheck + build; `scripts/verify_clean_checkout.py` VERIFIED; real-browser
  check of API Connections on the isolated harness with fake values (real vault and keys untouched); secret scan 0 hits.
- **Not verified:** real Cloudflare (no Account ID yet), live AI planning (mocked Gemini only), Connections UI against the real vault.

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
  Auto/Custom (Cat. 5 = gold slider 0..N; golden excluded from Movie deliverables); Collection is plain text; single draft Style Bible; Image model and Planning stacked; simple glyph icons.
- QA is structural only (cannot detect text, frames or wrong style): human review required.
- Planner: ONE planner, the semantic AI TextPlanner, for every Collection/Set (no curated pools), run automatically on Generate
  (mocked-tested only, not yet run live; use "Test planner" first). Max 5 variants.
- Credential storage is Windows-only; one final variant per job; one running job at a time; single user.
- Multi-variant layout verified structurally only; Continue/Cancel/Approve/real export not re-exercised in the last UI pass.
- Full list: `docs/KNOWN_ISSUES.md`.

## Agreed execution plan (image-generation priorities)
1. Test and refine real **Gemini Nano Banana 2.1** outputs.
2. Connect and live-verify **Cloudflare FLUX.2 Klein 4B** (add Account ID in Settings > API Connections; get explicit approval; then
   `scripts/smoke_cloudflare.py --model klein --yes`, ONE request; only after a real success may it be called "live").
3. Improve generation quality using these two models (P4 below).
4. Only then decide whether to add more models (Z-Image-Turbo local, Seedream 4.5). No new integrations or paid generation before that.

## Pending (rough order)
- **Grand Set (All Golden): IMPLEMENTED locally** (spec 3.5, ADR-018; not pushed). `set_type` standard|grand; server forces Gold = Number of
  Cards and Cat.1-4 = 0; UI "Set type" selector with locked Gold slider / Cat. steppers; always planned by the AI planner (explicit
  "Plan with AI", ~$0.002); AI plan must give `characters` 1 or 2 for every golden card or it is rejected before any image generation.
  "16th set" = position of the finale, not a card count. NEVER part of the Movie Collection bundle (even when selected explicitly); it has
  normal generation / review / standalone variant-ZIP export. Tests mocked only (`backend/tests/test_grand_set.py`); live AI planning of a
  Grand Set and visual check of the 2-character limit (Art QA) are NOT verified. Standard Set unchanged.
- **Art-quality work (P4):** Cat. 3 scene drift + readable text, Cat. 4 too dark, category-specific references, Style Bible validation vs
  Playrix frames; compare every round with `benchmarks/phase3_gemini_baseline` (local, untracked).
- **Remaining assignment deliverables:** `docs/REFERENCE_ANALYSIS.md`, `docs/ART_RUBRIC.md`, `docs/EXPERIMENT_LOG.md`, product/visual
  comparison, three approved final sets (Sci-Fi, Detective, Fantasy; 3-5 variants each), Miro placement (manual).
- Live check of the semantic AI TextPlanner via Plan Only (needs approval; ~$0.002 per test) and cheapest-text-model comparison (Gemini vs Cloudflare), real-vault check of the Connections UI with real keys (never save fake values
  to the real store), frontend unit tests, Z-Image-Turbo and Seedream 4.5 (backlog, needs approval).

## Security, budget and rules (still apply)
- Never paste keys into chat; no secrets in Git (last scan: 0 hits in tree and history). `.env`, `data/`, `benchmarks/`, `screenshots/` are
  git-ignored; `*CONFIDENTIAL*` screenshots and `compare-reference.png` show the Playrix reference and must never be published or committed.
- No paid or live generation without explicit approval/in-app confirmation; no OpenAI; planned models stay disabled; no auto-retries.
- Caps: $0.75 per run, $5.00 cumulative (raised from $4.00 on 2026-11-09) (ledger `data/ledger.jsonl`); spent so far ~$0.80 (local estimate; Google Billing is the truth).
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

- **Prompt System v0.7 (collectible-subject refinement):** system_style, category_3, category_4 and image_closing saved once as v4 (v1/v0.5/v0.6 stay restorable); category_1/2/5 and repair untouched. Stronger: one dominant subject (~60-75%), low-noise simplified environment, place-like subjects reduced to one iconic focal element, no wide/top-down/location art, no readable text or signage. Only upgrades templates whose active version is the untouched v0.6; Producer edits/rollbacks never overridden. Reference handling unchanged. Not pushed.

- **Prompt System v0.8:** six templates (system_style, category_1-4, image_closing) saved once as new permanent versions (v0.7 snapshot archived in docs/prompt-archive/, DB backup data/collection_studio.before-v08-*.sqlite3); category_5 and repair untouched; Category 3/4 compiler labels corrected. Offline tests only, not pushed.

- **Cost tracking (no spending cap):** the cumulative $5 cap is removed (setting, planner check, generation check, HR-10). The append-only ledger now records `kind` (plan/image), `job_id` and `entry_id`; planning calls are attached to the batch they plan. Header shows lifetime Planning | Generation | Total (estimates; Total = sum); History/Results show per-batch Planning | Generation | Total. Older entries are classified by outcome and attributed through their artifact; planning calls with no recorded batch stay in the lifetime total only ("n/a" per batch). `GET /api/costs`. The per-run size guard remains.

- **Metaphor Planner:** the text-only planner is renamed and moved above Generate as the green "Plan Metaphors" button (Step 1). Plan rows have checkboxes (per card, per variant table and global Select all); "Regenerate Selected" (`POST /api/plan/regenerate`) asks the text model for replacements of ONLY the selected cards with the full Set context (Collection, Set, wishes, every kept concept, selected categories, history) and rejects duplicates; unselected cards, numbers, order and categories are untouched, cost is recorded as planning. Generate Sets sends the curated plan (`plan` in the job request, used as is, no new text call) while the plan-defining inputs are unchanged; otherwise the plan is shown as outdated and automatic planning is unchanged. Not verified against the live Gemini text API (offline mocked tests only).

- **Per-run cap removed:** GEMINI_JOB_BUDGET_USD and the `job_cap` check are gone; no spending cap of any kind remains (only the paid-calls switch and the MAX_IMAGES_PER_JOB request-size limit). Cost tracking unchanged.
