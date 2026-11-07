# Known issues and limitations

## Art quality (acknowledged, deliberately not tuned yet)
- **Category 3** often drifts into a full scene (reads like Category 4) and can render **readable text** (e.g. magazine titles),
  which breaks the “no text” rule.
- **Category 4** can come out dark/moody with no clear focal object, far from the bright casual reference style.
- Only a Category 1 reference was used in the Phase 3 pilot; Cat. 2–4 cards fell back to “all selected references”.
- The Style Bible and category rules are **draft v0/v1** and unvalidated against the Playrix frames; the `[2,2,3,3]` split is our
  assumption. Prompt Library edits and better category references are the intended levers.
- Baseline for comparisons: `benchmarks/phase3_gemini_baseline` (local, untracked).

## Functional limits
- **QA is structural.** It cannot detect text, frames, wrong style or wrong object. Human review is required (spec: no fake scores).
- **Planner is rules-based:** only Sci-Fi, Detective and Fantasy are supported; free wishes affect image prompts, not object choice.
  A TextPlanner for arbitrary themes is not implemented.
- **Pool limits:** at most 5 distinct variants per genre, and a distribution that needs many Cat. 1–2 cards (simple objects)
  can exhaust a pool with many variants (the error says so).
- **Cloudflare adapter** follows the documented contract but has only run against a mocked transport; its account ID is not configured.
- **No automatic provider fallback** (switching providers is a manual, visible choice).
- **One final variant per job**, one running job at a time, single user, Windows-oriented `start.bat`.
- **Frontend has no automated tests** (UI was checked manually in a browser; the backend has 161 tests).

## Cost
- Spend shown is a **local estimate** (≈ $0.067/image assumed from the spec); it can differ from the invoice and Google Billing may lag.
- A full 3 genres × 3 variants × 10 cards run is ≈ $6, more than a €5 balance: stage it (pilot → continue) and watch the cap.

## Documentation
- The spec's header still says “APP NOT IMPLEMENTED”; the README is the source for implementation status.
- File layout deviates from spec §10.5 (flat artifact store + SQLite).
