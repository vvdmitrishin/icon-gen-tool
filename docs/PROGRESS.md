# Progress log and handoff

Last updated: 2026-11-08 (end of session). Spec: `Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.8.md` (status v1.8.1).

## Where things stand
- **Working local MVP, Phases 0–7 implemented.** 171 backend tests pass (1 live test skipped), offline smoke test 16/16,
  clean-checkout verification passes (venv, pip, tests, `npm ci`, build, smoke).
- **Git:** all work is committed; the working tree is clean. `origin/main` is at `d7cce7a` (Phase 3).
  **4 commits are local and NOT pushed:** `4b72606` (Phases 4–7), `13283e1` (spec v1.8 + Model Selector),
  `79afea7` (spec v1.8.1 status), `9fca4e3` (docs test count). Push only when asked.
- **Secrets:** none in the tree or Git history (scanned). Keys live in the git-ignored `.env` (Gemini key; Cloudflare token, no
  account ID yet). `Downloads\API.txt` was overwritten and deleted. Optional: rotate both keys.
- **App:** `start.bat` → http://127.0.0.1:8000 (UI served by the backend).

## What was done (by commit)
| Commit | Content |
|---|---|
| `ac97cc4` | Phase 0–1: scaffold, Gemini adapter, provider registry, prompt compiler, references, cost guard, first UI |
| `b6f4aed` | Spec v1.7 (Card Category Distribution) + code: Auto `[2,2,3,3]`/Custom, category prompt rules |
| `d7cce7a` | Phase 3: rules planner, SQLite jobs, sequential runner, pilot→full, set UI |
| `4b72606` | Phases 4–7: QA, versions, review/approval, regeneration, compare, Prompt Library, Templates, Settings, History, export, collection, batch, imports, Cloudflare adapter, start.bat, scripts, docs |
| `13283e1` | Spec v1.8 + Model Selector grouped by runtime (Seedream 4.5 and 7 local models planned/disabled; capability metadata) |
| `79afea7`, `9fca4e3` | Spec v1.8.1 status refresh; docs test count |

## Local, untracked state (not in Git by design)
- `data/` — database, generated images, references, exports and `ledger.jsonl`. **Back it up if you care about it.**
- `benchmarks/phase3_gemini_baseline/` — frozen read-only benchmark (4 Gemini images + prompts/params/refs/costs/SHA-256).
- Estimated spend so far: **$0.804** (12 Gemini images) of the $4.00 local cap. It is an estimate; check Google AI Studio billing.
  Two Gemini images were single smoke tests, 4 were the Phase 3 pilot, and the other 6 (the remaining Detective cards, via the
  app's two-step Continue at 23:42) completed that set, so it is now the first full live 10-card set (Detective / Noir City Nights;
  technical QA 10/10 passed; art not reviewed).

## Open items (the backlog for the next session)
1. **UI vs mockup gaps** (decision: not now): missing Set Summary, Quick Actions, Next Steps panels; title-row buttons
   (Compare Variants / Prompt Library / Export) are tabs instead; 7 tabs vs the mockup's 4; variant selection and category
   coverage chips; left panel uses selects/distribution control instead of the mockup's steppers/checkboxes; no avatar.
   Deliberate differences (spec corrections): no style score / cost figures, no stars or frames, no creativity sliders, Cat. 5 locked.
2. **Art tuning** (Cat. 3 scene drift + readable text, Cat. 4 too dark, only a Cat. 1 reference so far), Style Bible validation
   against the Playrix frames; compare each round with the benchmark.
3. **Assignment deliverables not written:** `docs/REFERENCE_ANALYSIS.md`, `docs/ART_RUBRIC.md`, `docs/EXPERIMENT_LOG.md`,
   product/visual comparison, three approved final sets (Sci-Fi, Detective, Fantasy; 3–5 variants each), Miro placement (manual).
4. **Budget:** full 3 genres × 3 variants ≈ $6 > €5. Decide: top-up, fewer variants, or staged.
5. **Cloudflare:** add `CLOUDFLARE_ACCOUNT_ID` to `.env` for a live check of the fallback adapter (currently mock-tested only).
6. **Post-MVP backlog:** ComfyUI adapter + local models, Seedream 4.5 (needs approval, billing check and its own cost rate),
   TextPlanner, frontend tests.

## Resume tomorrow
```powershell
.\start.bat                                                  # app on http://127.0.0.1:8000
backend\.venv\Scripts\python scripts\check_env.py            # what is configured (no secret values printed)
cd backend; .\.venv\Scripts\python -m pytest                 # 171 offline tests
python scripts\verify_clean_checkout.py                      # re-prove it works from only the committable files
```
Rules that still apply: never paste keys into chat; no paid generation without the in-app confirmation; OpenAI stays disabled;
planned models stay disabled; do not push unless asked.
