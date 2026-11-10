# Presentation board (Miro)

> **Miro board: <https://miro.com/app/board/uXjVEQ0lWao=/>** (link provided by the producer; not opened or verified by the assistant, which has no Miro access).
> Before submitting, open it in a private window and confirm it is view-only and loads without signing in.

## Suggested board structure (7 frames)

| # | Frame | Material to use |
|---|---|---|
| 1 | Project overview | One-paragraph positioning from the README; the three sets side by side |
| 2 | Production workflow | `Collection & Set -> Metaphor Planning -> Producer Review -> Image Generation -> Iteration -> QA -> Selection -> Export` (screenshots of the pipeline strip) |
| 3 | Three 10-card sets | `deliverables/previews/detective.png` (ready); Comedy and Adventure contact sheets once packaged (`scripts/package_deliverables.py`) |
| 4 | Five visual categories | `specification/Ref_category_1..5.png` with the one-line category rule from the Prompt Library |
| 5 | Producer review and selective regeneration | Before/after versions of one card, a repair note, the manual-edit pencil in the Metaphor Planner |
| 6 | QA and approval | Technical QA report, set QA, variant approval, ZIP export contents |
| 7 | Technical approach | Architecture diagram from `docs/ARCHITECTURE.md`; planner -> prompt compiler -> provider data flow |

## How to share it

1. Share > **Anyone with the link can view** (not "can edit"; no Miro account required for viewers).
2. Prefer a **view-only link** with "Allow comments" off; set no password only if the content is cleared for sharing.
3. Open the link in a private window to confirm it works without signing in, then paste it into the README and above.
4. Keep a backup: Miro > Export > PDF (or high-resolution image) of the whole board, store it next to the final deliverables,
   and attach it to the submission in case the link is blocked.

## Content restrictions

- `specification/Ref_category_*.png` are derived from Playrix material: put them on a board only if the board is shared
  with Playrix reviewers (not publicly). See [HANDOFF.md](HANDOFF.md#asset-ownership).
- Do not paste API keys, billing screens or `.env` contents into the board.
