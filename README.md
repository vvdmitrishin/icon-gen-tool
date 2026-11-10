# AI Collection Studio

**AI Collection Studio is an AI-assisted production workflow for creating, reviewing, refining, and exporting collectible card artwork for casual mobile games.**

A producer picks a *Collection* and a *Set* (for example Movie / Detective), the app plans ten distinct visual metaphors, the
producer reviews and edits them, images are generated one card at a time with category-specific rules and references, and the
approved set is quality-checked and exported. Built as the Playrix AI Producer test assignment.

## View the Final Presentation

| | |
|---|---|
| **Miro board (final presentation)** | `PASTE-MIRO-LINK-HERE` (placeholder, see [docs/PRESENTATION_BOARD.md](docs/PRESENTATION_BOARD.md)) |
| **Live demo** | none: the app runs locally (no hosted instance exists) |
| **Detective, 10 cards** | [deliverables/README.md](deliverables/README.md#detective-10-cards) · [contact sheet](deliverables/previews/detective.png) |
| **Comedy, 10 cards** | pending final selection: [deliverables/README.md](deliverables/README.md) |
| **Adventure, 10 cards** | pending final selection: [deliverables/README.md](deliverables/README.md) |

You will see the three Movie-collection Standard Sets (10 cards each, no golden cards): each card is a finished 512x512
illustration with its category and subject. Open `deliverables/index.html` in a browser for an offline gallery, no app and
no API key required. The Miro board walks through the workflow, the five visual categories with their references,
producer review and selective regeneration, and the QA/approval process.

## What it does

| Capability | Status |
|---|---|
| AI **Metaphor Planner** (Collection + Set + Free Wishes, 1-5 variants, no repeats across batches) | implemented |
| **Manual metaphor editing** (edit a card's Subject/Description in the plan; text only, no AI call) | implemented |
| **Selective concept regeneration** (replace one object; keep the rest) | implemented |
| Five **visual categories** with a Style Bible and category rules; Auto `[2,2,3,3]` or Custom distribution | implemented |
| **Reference images**: curated defaults auto-loaded; each card receives only its own category reference | implemented |
| Image generation per card, **regeneration with a repair note**, immutable **version history** | implemented |
| **Version comparison** and Reference Compare (split slider, side by side, grid) | implemented |
| **Technical QA** (format, size, aspect, blank, integrity) + set QA; human **producer review** and variant approval | implemented |
| Versioned **Prompt Library** (releases v0.5-v0.10, rollback, session overrides, per-job immutable snapshots) | implemented |
| **Generation history and provenance** (exact prompt, model, references, cost per image) | implemented |
| **Cost visibility** (local estimates, confirmation before paid work, ledger) | implemented |
| **My Selection** and **ZIP export** (artwork + manifest + set plan + QA report + contact sheet) | implemented |
| Providers | Gemini Nano Banana 2.1 (verified live), Mock (free); Cloudflare FLUX (mock-tested only), OpenAI (disabled) |
| Not implemented | vision/LLM QA, automatic provider fallback, multi-user, hosted deployment, frontend unit tests |

## Production workflow

`Collection & Set -> Metaphor Planning -> Producer Review -> Image Generation -> Iteration -> QA -> Selection -> Export`

1. **Collection & Set:** theme, optional Free Wishes, category distribution, reference selection.
2. **Metaphor Planning:** the text model proposes variants with 10 subjects each (nothing is drawn yet; planning is cheap).
3. **Producer Review:** edit a subject/description by hand (pencil icon) or replace an object; approve the plan.
4. **Image Generation:** one paid image call per card, sequential, with explicit cost confirmation.
5. **Iteration:** regenerate a single card with a note (up to `MAX_REGENERATIONS_PER_CARD`), compare versions.
6. **QA:** automatic technical checks, then human art review.
7. **Selection & Export:** star favourites into My Selection, approve a variant, export a ZIP.

## Real examples

The three final Movie-collection sets are packaged in [`deliverables/`](deliverables/) (built by
`scripts/package_deliverables.py`, which only copies images the app already produced).
Status: **Detective is packaged; Comedy and Adventure await your final selection** (see
[docs/HANDOFF.md](docs/HANDOFF.md#final-content)).

## Technical architecture

- **Frontend:** React 18 + TypeScript + Vite (`frontend/`), served by the backend in production.
- **Backend:** Python 3.11+, FastAPI, SQLite (`backend/`), loopback-only API.
- **Text planning model:** Gemini `gemini-3.5-flash-lite` (`GEMINI_TEXT_MODEL`).
- **Image model:** Gemini `gemini-nano-banana-2.1` ("Nano Banana 2.1"), 1:1, downscaled to 512x512.
- **Prompt compilation:** Style Bible + category rule + set context + wishes + reference guidance + closing, from versioned Prompt Library templates snapshotted per job.
- **References:** curated `specification/Ref_category_1..5.png` imported on first start; category N reference goes only to category N cards.
- **Storage:** SQLite (`data/collection_studio.sqlite3`) plus files (`data/artifacts`, `references`, `exports`, `ledger.jsonl`).

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Installation

Prerequisites: Python 3.11+, Node.js 18+, and optionally a Google Gemini API key (planning and real images need one; the Mock
image provider needs none for UI exploration).

```bash
# 1. configuration
cp .env.example .env          # Windows: copy .env.example .env   then set GEMINI_API_KEY (or enter it in Settings > API Connections)

# 2. backend
python -m venv backend/.venv
backend/.venv/bin/pip install -e "backend[dev]"      # Windows: backend\.venv\Scripts\pip
# 3. frontend (build once; the backend serves it)
npm --prefix frontend ci && npm --prefix frontend run build
# 4. run
backend/.venv/bin/python -m uvicorn app.main:app_factory --factory --app-dir backend --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. On Windows, `start.bat` does steps 1-4 in one go (`start.bat dev` runs Vite with hot reload on :5173).
Keys: Windows Credential Manager (Settings > API Connections) or `.env`; they never reach the browser.

Tests and checks:

```bash
cd backend && python -m pytest                 # offline, free (mocked providers)
npm --prefix frontend run build                # typecheck + production build
python scripts/verify_clean_checkout.py        # proves the repo works from tracked files only
```

## Handover

- Source of truth: [`specification/PLAYRIX_SOURCE_OF_TRUTH_v1.10.md`](specification/PLAYRIX_SOURCE_OF_TRUTH_v1.10.md) (Ukrainian) and `specification/Mockup.png`.
- Start with [docs/HANDOFF.md](docs/HANDOFF.md) (what is included, default references, asset ownership, limitations),
  then [Architecture](docs/ARCHITECTURE.md), [User guide](docs/USER_GUIDE.md), [Runbook](docs/RUNBOOK.md),
  [Known issues](docs/KNOWN_ISSUES.md), [Presentation board](docs/PRESENTATION_BOARD.md), [Progress log](docs/PROGRESS.md).
- Historical prompt releases live in `backend/app/config/prompt_system_v0*.yaml` and `docs/prompt-archive/`.

## Secrets and rights

- API keys are never committed; `.env`, `data/`, `benchmarks/` and `screenshots/` are git-ignored.
- **Reference artwork originates from Playrix materials.** Do not redistribute this repository publicly with
  `specification/Ref_category_*.png` unless Playrix permits it; see [docs/HANDOFF.md](docs/HANDOFF.md#asset-ownership).
- Costs shown are local estimates; the provider's billing is the source of truth.
