# User guide (game designer / producer)

Open the app (`start.bat` → http://127.0.0.1:8000). Header: **Generate · History · Templates · Settings**.

## 1. Prepare
1. **Section 3 References**: drop the style images (PNG/JPG/WebP), choose each one's type (Style, Category 1–4, Composition,
   Negative example) and click thumbnails to select up to 5. Tick the rights checkbox: references are sent to the image provider.
2. **Section 4**: pick the **Image model**. Gemini is primary. Models marked “Docs only” unlock via *Check model availability*
   (free). OpenAI and a not-yet-configured Cloudflare are listed but disabled. **Mock** is free and clearly marked.
3. **Card Category Distribution**: Auto (Balanced) = 2/2/3/3 (our assumption, not a Playrix ratio) or Custom (Σ must be 10,
   each of Cat. 1–4 ≥ 1; Cat. 5 is locked to 0).

## 2. Generate a set
1. Mode **Card sets**: choose Genre, Variants (final deliverable needs 3–5; use fewer while the budget is small), Run scope:
   **Pilot** (4 cards per variant, one per category) or **Full** (10 per variant). Optional *Producer's free wishes*
   steer the image prompts only (the planner picks objects from curated pools).
2. Review the **plan preview** and the cost estimate, then press the button. Paid runs need a second click to confirm.
3. Watch the gallery fill in. **Cancel** stops after the in-flight call. After a pilot, **Continue** generates the rest.

## 3. Check, review, approve
- Each card shows a QA dot (green/amber/red) and review marks. **Set QA** per variant lists the checks. QA is structural only:
  *it does not detect text in the image or style problems* — look at every image.
- Click a card: **Approve / Reject**, add a *repair note* and **Regenerate** (max 2 per card; earlier versions stay),
  **Replace object…**, or **Generate this card** if it is still planned.
- **Approve variant** needs 10 generated cards, no failed set QA and no rejected cards. One variant per genre is final.

## 4. Compare
- **Reference Compare**: Split Slider (drag or ←/→), Side by Side with synced zoom (Fit/100%), Grid Overview of all pairs (filter by
  category). The reference is matched automatically (category-typed first); override per card from the dropdown.
- **Compare Variants** puts the same position of every variant side by side.

## 5. Export and the final deliverable
- **Export ZIP** on a variant: `artwork/NN_object_catN.png`, `manifest.json`, `set-plan.json`, `QA-report.json`, `contact-sheet.png`.
- **History → Collection progress** shows each genre's readiness; **Export final bundle** needs an approved, complete,
  non-mock set per genre. *Plan all three genres at once* creates the three jobs under one confirmation.
- Paste the exported artwork into the Playrix Miro frames by hand (as the assignment asks).

## 6. Other tools
- **Prompt Library** (tab): view versions; switch on *Advanced mode* to edit; every save is a new version recorded in each
  image's provenance; restore any older version.
- **Templates**: save/apply Generate-screen presets. **Settings**: read-only provider/budget/limit overview.
- **History → Import a sample image**: add an image made elsewhere (e.g. another model) with honest provider/model labels.
