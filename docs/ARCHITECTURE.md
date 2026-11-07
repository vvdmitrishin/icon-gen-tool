# Architecture

```
React UI (Vite, TS) ──/api──▶ FastAPI ──▶ JobService (sequential runner, one lock)
                                   │            ├─ RulesPlanner + object pools + Card Category Distribution
                                   │            ├─ GenerationService ─▶ PromptCompiler (+ Prompt Library templates)
                                   │            │                        └▶ ModelRegistry ─▶ Gemini | Cloudflare | OpenAI(disabled) | Mock
                                   │            ├─ CostGuard (estimate, caps, ledger)   └▶ ArtifactStore (PNG + JSON sidecar)
                                   │            └─ QA (technical + set) ─▶ versions, review, approval
                                   └─ Exporter / Collection overview · ComparisonService · PresetStore · imports
SQLite (data/collection_studio.sqlite3) + files (data/artifacts, references, exports, ledger.jsonl)
```

Single process on the laptop; inference is a cloud call. `start.bat` builds the UI and the backend serves it
(`frontend/dist`); dev mode uses Vite with an `/api` proxy. Keys are server-side only.

## Backend modules (`backend/app`)

| Module | Role |
|---|---|
| `providers/base.py` | `ImageProvider` contract, capabilities, model status taxonomy (`documented` → `external_rest_verified` → `app_live`, `disabled_*`, `mock`) |
| `providers/gemini_image.py` | REST `generateContent`, `inlineData` references, key in header, errors redacted, ListModels availability check |
| `providers/cloudflare_image.py` | Workers AI multipart (`prompt`, `width`, `height`, `input_image_0`), JSON/base64 or raw image response |
| `providers/openai_image.py` | scaffold only: no HTTP client imported, always refuses |
| `providers/model_registry.py` | catalogue with honest per-model status; a model is selectable only when configured/available |
| `services/prompt_compiler.py` | Layer A (style) + B (CardSpec/category rules) + C (free wishes) + references + repair note + closing; library templates override editable blocks |
| `services/prompt_library.py` | versioned templates (7), save/restore create new immutable versions |
| `services/category_distribution.py` | Auto `[2,2,3,3]`/Custom, Σ=10, each Cat. 1–4 ≥ 1, Cat. 5 = 0, slot assignment |
| `services/planning.py` | seeded rules planner, plan QA (HR rules), distinct micro-theme per variant |
| `services/generation.py` | one card: validate → compile → confirm/cap → provider → PNG + provenance JSON |
| `services/jobs.py` | store + state machine + runner; versions, review, approval, regenerate, replace card, QA orchestration, restart recovery |
| `services/qa.py` | deterministic checks TQ-01..09 and SQ-01..08 (structure only) |
| `services/comparison.py` | generated ↔ reference pairing (auto by category, manual override persisted) |
| `services/exporter.py` | variant ZIP, final bundle, collection readiness |
| `services/cost_guard.py` | estimate, per-run/total caps, JSONL ledger that survives restarts |
| `services/reference_manager.py` | decode-validated uploads, PNG re-encode, dedupe, thumbnails |
| `services/presets.py`, `imports.py`, `db.py` | templates, honest sample import, SQLite helper (always closes connections) |

## Job state machine

`created → planning → plan_qa → queued → generating → image_qa → set_qa → ready_for_review | partial_success | failed`,
plus `cancel_requested → cancelled` and `interrupted` (set on startup if the server died mid-run).
Every transition is stored in SQLite with an event log. Card statuses: `planned | queued | generating | done | failed`.

**Safety rules enforced in code** (each has tests): paid calls need `confirmed`; caps are checked before every call; one
in-flight provider call at a time; circuit breaker after 2 consecutive failures; no automatic retries (unless
`MAX_CARD_RETRIES`); a failed regeneration keeps the previous image; regeneration limit per card; a restart never resumes paid work.

## Card versions and approval

Each generated image is an immutable `card_versions` row (QA result, art-review state). Regeneration adds a version and
makes it current; replacing a card retires its versions (kept as history). One variant per job can be *final*; any change to
its content (regeneration, rejection, replacement) withdraws the approval. Exports require an approved, complete, non-mock,
QA-not-failed variant unless explicitly overridden for testing.

## Data

- SQLite: `jobs, cards, events, card_versions, variant_approvals, comparison_refs, prompt_templates, presets, exports`.
  Older databases are migrated on startup (columns added, version rows backfilled).
- Files: `data/artifacts/<id>.png|json` (immutable), `data/references/`, `data/exports/`, `data/ledger.jsonl`.
- Deviation from spec §10.5: a flat artifact store plus SQLite instead of per-project folders.

## Tests

`pytest` (161 tests): providers (mocked transports), planner, distribution, QA, jobs (lifecycle, budget, breaker, cancel,
restart, migration), review/approval/regeneration, prompt library, compare, export, collection, batch, imports, settings,
static serving, scripts. Live paid test is gated behind `RUN_LIVE=1`.
