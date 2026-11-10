# Handoff

## What is included

| Path | Content |
|---|---|
| `backend/` | FastAPI app, prompt system (`app/config/*.yaml`), tests (`backend/tests`) |
| `frontend/` | React + TypeScript UI |
| `specification/` | Source of truth (`PLAYRIX_SOURCE_OF_TRUTH_v1.10.md`), `Mockup.png`, curated defaults `Ref_category_1..5.png` |
| `deliverables/` | Final images per set, `set.json` metadata, contact sheets, offline `index.html`, `selection.json` manifest |
| `docs/` | Architecture, user guide, runbook, known issues, progress log, prompt archive (`prompt-archive/`), old reports (`archive/`) |
| `scripts/` | smoke tests, clean-checkout verification, benchmark export, `package_deliverables.py` |

Continue development with `docs/ARCHITECTURE.md` (modules, data flow), `docs/PROGRESS.md` (history) and the spec's ADRs.

## Prompt system and versioning

Templates (system style, categories 1-5, closing, repair) live in SQLite `prompt_templates`. Releases are YAML files
`backend/app/config/prompt_system_v05.yaml` ... `v10.yaml`; each is applied once as a **new version** on startup, only if the
active version is still the untouched previous release, so producer edits and rollbacks are never overridden. Every job
stores an immutable snapshot of the prompts it used (`prompt_bundle`); new templates affect only **new jobs after a backend
restart**. Current actives: system_style 5, category_1 4, category_2 4, category_3 7 (v0.10), category_4 6, category_5 3, closing 5, repair 1.

## Default references

- Source: `specification/Ref_category_1.png` ... `Ref_category_5.png` (cleaned mosaics, 1838x1224; the older framed
  `category_N_ref.png` files were obsolete, referenced nowhere and have been removed).
- Loading: at startup `ReferenceManager.seed_defaults()` imports them as `category_1..5`; the References panel shows and
  pre-selects them. Folder is `DEFAULT_REFERENCES_DIR` (repo-relative; no absolute path at runtime).
- Replace: upload a new image with the same category type in the UI (and delete/deselect the default), or overwrite the file
  in `specification/` (a changed file is re-imported on next start and replaces its earlier seeded copy).
- A default deleted in the UI is **not** re-imported (marker `data/references/.default_references.json`).
- Routing is unchanged: a card receives only its own category's reference. The rights checkbox before sending references to
  an external provider is unchanged.

## QA, regeneration and export

Technical QA runs per image and per set (structure only: format, size, aspect, blank, integrity; no vision judgement). The
producer approves/rejects cards, regenerates a card with a note (limit `MAX_REGENERATIONS_PER_CARD`, versions kept),
approves one variant as final, then exports a ZIP (artwork, manifest, plan, QA report, contact sheet). Exports require an
approved, complete, non-mock, QA-not-failed variant.

## Configuration

See `.env.example` (placeholders only). Notable: `GEMINI_API_KEY`, `GEMINI_TEXT_MODEL`, `DEFAULT_IMAGE_MODEL`,
`MAX_REGENERATIONS_PER_CARD`, `MAX_REFS_PER_REQUEST`, `DEFAULT_REFERENCES_DIR`, `DATA_ROOT`. Restart the backend after editing `.env`.

## Final content

`scripts/package_deliverables.py` copies the *current* image of each selected card from `data/artifacts` (no generation, no
API) according to `deliverables/selection.json`.

| Set | Status | Why |
|---|---|---|
| Detective | **packaged**: job `job-20261110-162806-1a1f`, 10 cards (2/2/3/3), all done | the only complete 10-card job for this set |
| Adventure | **pending** | candidate job `job-20261110-134631-4709` (10 cards, genre typo "advenrute") was partly re-rolled in `job-20261110-140905-9327`; which images are final cannot be told from stored data |
| Comedy | **pending** | no single job holds 10 cards (several 6-card jobs and 2-card tests); the final 10 were assembled via My Selection, which the app does not persist |

To finish: list the 10 `card_id`s per set in `deliverables/selection.json` (or a `job`), then run
`python scripts/package_deliverables.py`. Nothing was guessed.

## Asset ownership

- `specification/Ref_category_1..5.png` come from Playrix reference material. This repository on GitHub is currently **public**
  and an earlier commit (`041d30c`, "Added refs") already contains these images (under a different filename) in its history.
  Before sharing the repo publicly, either make it private, or remove the images (and rewrite history if needed), and ship the
  references to reviewers separately: the app then simply starts with an empty References panel and the producer uploads them.
- The source-of-truth document contains two historical log lines with a local Windows path (`C:\Users\<name>\...`); harmless, left untouched.
- Generated images in `deliverables/` are outputs of Gemini Nano Banana 2.1 produced with those references: confirm with
  Playrix that sharing them is acceptable.

## Known limitations

See [KNOWN_ISSUES.md](KNOWN_ISSUES.md). Highlights: art quality is judged by the human producer (QA is structural);
Gemini is the only verified live image provider; planner and My Selection state live in the browser page; Windows-oriented
launch scripts and credential store (Linux/macOS use `.env`); intermittent Python segfaults on the dev machine (environmental).
