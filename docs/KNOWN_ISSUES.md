# Known issues and limitations

## Art quality (acknowledged, deliberately not tuned yet)
- **Category 3** often drifts into a full scene (reads like Category 4) and can render **readable text** (e.g. magazine titles),
  which breaks the “no text” rule.
- **Category 4** can come out dark/moody with no clear focal object, far from the bright casual reference style.
- Only a Category 1 reference was used in the Phase 3 pilot; Cat. 2–4 cards fell back to “all selected references”.
- The Style Bible and category rules are **draft v0/v1** and unvalidated against the Playrix frames; the `[2,2,3,3]` split is our
  assumption. Prompt Library edits and better category references are the intended levers.
- Baseline for comparisons: `benchmarks/phase3_gemini_baseline` (local, untracked).

## UI vs the mockup (mostly closed; remaining deviations are deliberate)
The UI now follows `Specification/Mockup.png` closely (pipeline strip, title row, tabs, variant rows, Set Summary, Quick Actions,
Next Steps; no horizontal scroll at 1672x940, 1366x768, 1280x720). Remaining differences, mostly spec-driven:
- No style score, best match, Creativity/Consistency sliders or fake cost/time figures (spec: no fake scores).
- Cat. 1-4 are cards-per-category steppers with Auto/Custom (the real distribution feature); Cat. 5 is locked off.
- Collection is a text field; Art style is a single draft style bible.
- Image model and Planning are stacked instead of side by side; icons are simple glyphs.
- Run time is derived from the job log (0 sec for fast mock runs).
- The multi-variant look was verified structurally only.
- Screenshots are in the git-ignored `screenshots/` folder; some show the confidential reference and must never be committed.

## Functional limits
- **QA is structural.** It cannot detect text, frames, wrong style or wrong object. Human review is required (spec: no fake scores).
- **Planner is rules-based:** only Sci-Fi, Detective and Fantasy are supported; free wishes affect image prompts, not object choice.
  A TextPlanner for arbitrary themes is not implemented.
- **Pool limits:** at most 5 distinct variants per genre, and a distribution that needs many Cat. 1–2 cards (simple objects)
  can exhaust a pool with many variants (the error says so).
- **Cloudflare (FLUX.2 Klein 4B, FLUX.1 Schnell) is NOT verified live.** The adapter follows the documented contract and has only run
  against a mocked transport. No Account ID is configured on the dev machine; a live check (`scripts/smoke_cloudflare.py --yes`)
  needs explicit approval. Doc-based capabilities and Neuron estimates are flagged `verified:false`.
- **Cloudflare "free" is a daily allocation**, not unlimited: 10,000 Neurons/day (resets 00:00 UTC, about 90 Klein or 170 Schnell
  images by published estimates). Beyond it requests fail on Free or are billed on Workers Paid. A quota error stops the job after
  one call (`quota_exceeded`, nothing charged); there is no automatic fallback to another provider. Schnell cannot use references.
- **Whole-API loopback guard** only applies in the real app (uvicorn `app_factory`); test clients are exempt, so tests do not prove it end to end.
- **Credentials storage is Windows-only** (Credential Manager via `keyring`); elsewhere only `.env` applies. The real Credential Manager
  round trip has one automated Windows-only test; the UI flow was verified in a real browser only against an isolated harness
  (in-memory credential store + mocked providers, 18/18 checks), not against the real store with real keys.
- **No automatic provider fallback** (switching providers is a manual, visible choice).
- **One final variant per job**, one running job at a time, single user, Windows-oriented `start.bat`.
- **Frontend has no automated tests** (typecheck + build only; the backend has 275 passed, 1 skipped).

## Cost
- Spend shown is a **local estimate** (≈ $0.067/image assumed from the spec); it can differ from the invoice and Google Billing may lag.
- A full 3 genres × 3 variants × 10 cards run is ≈ $6, more than a €5 balance: stage it (pilot → continue) and watch the cap.

## Documentation
- The spec is now v1.9 (new sections 6.15, 12.7.1, 19.1.1, ADR-017); the README "Implementation status" table stays the detailed source.
- File layout deviates from spec §10.5 (flat artifact store + SQLite).
