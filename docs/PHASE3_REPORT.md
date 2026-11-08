# Phase 3 Report — AI Collection Studio

Date: 2026-11-08 · Spec: `Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.9.md` · Status: Phase 3 built, **not yet committed**.

## 1. What's developed

| Area | Delivered |
|---|---|
| Provider layer | Provider registry with honest per-model status. **Gemini Nano Banana 2.1** is live and primary, with model selection (the other three Gemini models unlock via a free availability check). Cloudflare FLUX.2 Klein 4B is registered but **adapter pending**. OpenAI GPT Image is registered and shown **disabled, with no network code**. A mock provider is available for free testing. |
| Prompting | Prompt compiler with three layers (Style Bible, settings, free wishes) and category rules for Cat. 1–4. Cat. 5 is locked to 0. |
| Category distribution (spec §3.4) | `Auto (Balanced)` [2,2,3,3] (our assumption, not a Playrix ratio) and `Custom`. Validation: Σ=10, each of Cat. 1–4 ≥1, Cat. 5=0. Applied by the planner and identical in every variant. |
| Planner (new) | Seeded `RulesPlanner` with curated pools for Sci-Fi, Detective and Fantasy. Each genre has 5 micro-themes, which allows 1–5 distinct variants. Plan-QA hard rules. |
| Jobs (new) | SQLite job store and state machine. Sequential runner with pilot→full scope, Continue, Cancel, a circuit breaker after 2 failures and restart recovery. Interrupted jobs are never auto-resumed. |
| Cost safety | Explicit cost confirmation, a per-run cap ($0.75), a cumulative cap ($4.00, local ledger), retries off, one call per card and no uncontrolled batches. |
| References | Validated upload (decoded and re-encoded), a rights-policy checkbox, and per-category reference selection with a recorded fallback mode. |
| Provenance | Every PNG has a JSON sidecar: prompt layers, model, references, cost estimate, usage and job context. |
| UI (Vite + React + TS) | Mockup layout with a **Card sets** mode: plan preview before spending, live gallery with per-slot status, progress, Continue and Cancel, Set Plans JSON and an event log. Single-card mode is kept for diagnostics. |

## 2. What's tested

- **Automated, offline:** 109 backend tests pass and 1 is skipped (a paid live test gated behind `RUN_LIVE=1`).
  - **Coverage:** planner rules, distribution validation, job lifecycle, budget stops, the circuit breaker, cancel, restart recovery, reference selection, disabled providers (the OpenAI path is asserted to make no network call), key redaction and upload safety.
  - **Method:** Gemini is exercised through a mocked HTTP transport.
- **Frontend:** typecheck and production build pass. UI behaviour was checked by hand in the browser (the mock 2-variant, 20-card set and the distribution control). There are no automated frontend tests.
- **Live, paid (Gemini):**
  - **Smoke tests:** a text-only image and a reference image succeeded earlier in the session.
  - **Phase 3 pilot:** 1 variant, 4 cards (one per category), reference-guided, with no failures.
  - **Spend:** the local ledger shows about $0.40 of $4.00. This is an estimate, and Google Billing is the source of truth.
- **Art result (my read; your review decides):**
  - **Good:** Cat. 1 and Cat. 2 look on brief.
  - **Weak:** Cat. 3 drifts toward a full scene and shows readable text. Cat. 4 is dark and has no clear focal object.

## 3. Scope still left

| Phase / item | Remaining |
|---|---|
| Art quality (blocking) | Tune the draft Style Bible and the Cat. 3–4 prompts. Add category-specific references (only Cat. 1 was used). Validate the distribution against the real Playrix frames. |
| Phase 2 remainder | Cloudflare fallback adapter (it needs the account ID). The optional TextPlanner for free-form genres and wishes. Today, wishes only steer image prompts and unknown genres are rejected. |
| Phase 4 | Technical QA (text-in-image, format and file checks, duplicate hashes), set-level QA, **selective single-card regeneration** with versions, and approve. |
| Phase 5 | Reference Compare (Split Slider, Side by Side, Grid), Prompt Library (view, edit, version), History, Templates and Settings screens, and edit-plan / replace-card. |
| Phase 6 | Export (ZIP, manifest, contact sheet). The real three-genre run with 3–5 variants each (30 cards per genre, so the €5 budget will need staging). Miro placement and comparison, done by hand. |
| Phase 7 | `start.bat`, a clean-checkout smoke test, final README and limitations, and a rights check. |
| Housekeeping | Commit Phase 3. Remove `Downloads\API.txt`. Align the spec §10.5 file layout with the flat store plus SQLite used now. Note that the 6 remaining pilot cards are still ungenerated. |

## Key risks

- **Cost estimate:** the $0.067 per image figure is unverified against billing. A full 3 genres × 3 variants × 10 cards is about $6, which is more than €5, so it cannot run in one go.
- **Style fit:** the "match Playrix style" claim is unproven. Only human art review can settle it.
