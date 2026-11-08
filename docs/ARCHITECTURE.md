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
Spec: `Specification/PLAYRIX_SOURCE_OF_TRUTH_v1.10.md` (sections 6.15, 12.7.1, 19.1.1, ADR-017 cover the API Connections sprint; the v1.10 update block, section 3.5 and ADR-018 cover stabilization and the pending Grand Set).

## Backend modules (`backend/app`)

| Module | Role |
|---|---|
| `providers/base.py` | `ImageProvider` contract, capabilities, model status taxonomy (`documented` → `external_rest_verified` → `app_live`, `disabled_*`, `mock`) |
| `providers/gemini_image.py` | REST `generateContent`, `inlineData` references, key in header, errors redacted, ListModels availability check |
| `providers/cloudflare_image.py` | Workers AI adapter: per-model request builders (Klein multipart with `input_image_0..3`; Schnell JSON), JSON/base64 or raw image response, JPEG normalized to PNG, quota error mapping, account id redacted from httpx logs |
| `providers/cloudflare_models.py` | Cloudflare model table (capabilities, size/step limits, published Neuron estimates; doc-based items flagged `verified:false`) |
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
| `services/cost_guard.py` | estimate, per-run/total caps, JSONL ledger that survives restarts; `CLOUDFLARE_PLAN=free` gives estimate 0 and no confirmation, `paid` applies estimate/confirmation/caps like Gemini |
| `services/credentials.py` | credential store over `keyring` (Windows Credential Manager); precedence Credential Manager, then `.env`, then none |
| `services/connection_check.py` | free metadata-only connection tests (Gemini ListModels, Cloudflare model-catalog search) |
| `services/security.py` | loopback Host/Origin checks, per-session token (`X-Studio-Token`), test rate limit (helpers; the whole-API guard middleware is wired in `main.py`) |
| `services/reference_manager.py` | decode-validated uploads, PNG re-encode, dedupe, thumbnails |
| `services/presets.py`, `imports.py`, `db.py` | templates, honest sample import, SQLite helper (always closes connections) |

## Credentials and local security

- Settings > API Connections lets the user add/replace/remove Gemini (API key) and Cloudflare (Account ID + API token).
  Values are stored by the backend in Windows Credential Manager (DPAPI) via `keyring`: service `AICollectionStudio`, entries
  `gemini.api_key`, `cloudflare.account_id`, `cloudflare.api_token`. `.env` stays the development fallback. Changes apply
  immediately, no restart.
- Secrets are never in browser storage, SQLite, logs, Git or API responses; the API exposes only `stored` / `from_env` booleans.
  422 validation errors do not echo submitted values; httpx log lines redact the Cloudflare account id.
- Connection endpoints accept only loopback Host/Origin and require the per-session token header `X-Studio-Token`
  (obtained from `GET /api/session`); tests are rate limited. "Test connection" never generates an image.
- The real app (uvicorn `app_factory`) also applies a whole-API loopback Host/Origin guard; test clients are exempt.
- Frontend: `ConnectionsPanel`, `SetupWizard` (3 steps, opens when no provider is configured), `Pipeline` (six-step strip),
  `api/connections.ts`.

## Cloudflare models

| Model | Id | Inputs | Notes |
|---|---|---|---|
| FLUX.2 Klein 4B | `@cf/black-forest-labs/flux-2-klein-4b` | text + up to 4 references (multipart `input_image_0..3`, each downscaled to fit under 512x512) | sizes 256-1920, steps fixed; ~110 Neurons (~$0.0012) per 1024x1024 image, ~90/day on the free allocation |
| FLUX.1 Schnell | `@cf/black-forest-labs/flux-1-schnell` | text only (JSON `{prompt, steps}`) | references unavailable: jobs record `reference_selection=unsupported_by_model`, provenance lists no references, a direct card with references is rejected with an explanation; base64 JPEG normalized to PNG; ~58 Neurons (~$0.0006), ~170/day free |

Estimates come from Cloudflare pricing docs and are unverified live. Free tier = Workers Free allocation of 10,000 Neurons/day
(resets 00:00 UTC). Beyond it requests fail on Free or are billed on Workers Paid; the app never upgrades the plan and never
falls back silently to another provider. A quota error (Cloudflare code 3036 / HTTP 429) becomes `quota_exceeded`
(non-retryable, nothing charged) and stops the job after one call. Both models show as "Configured" once credentials exist and
become "Live in app" only after a real request succeeds (not yet reached). Optional `CLOUDFLARE_EST_COST_PER_IMAGE_USD` override.

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

## Model roadmap (post-MVP backlog)

Future cloud and local models plug into the same `ImageProvider` abstraction and registry; local ones would go through a local
inference runtime (potentially ComfyUI API). Until an adapter and runtime configuration exist they are shown as `planned`,
visible but never selectable. Capability metadata (tri-state text-to-image / editing, reference limits, runtime requirements,
optional settings such as ERNIE Prompt Enhancement) is part of each registry entry. Details: spec §12.7.

## Tests

`pytest` (275 passed, 1 skipped): credentials/connections/local-only guard, Cloudflare models and registry, providers (mocked transports), planner, distribution, QA, jobs (lifecycle, budget, breaker, cancel,
restart, migration), review/approval/regeneration, prompt library, compare, export, collection, batch, imports, settings,
static serving, scripts. The skipped test is the paid live Gemini test, gated behind `RUN_LIVE=1`. One Windows-only test
covers the real Credential Manager round trip. The frontend has typecheck + build only, no unit tests.
