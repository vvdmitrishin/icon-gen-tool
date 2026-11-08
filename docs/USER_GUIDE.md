# User guide (game designer / producer)

Open the app (`start.bat` → http://127.0.0.1:8000). Header: **Generate · History · Templates · Settings**, a
**Connect providers** button and the "Local User" badge.

## 0. First-time setup and connecting providers
1. On the first run, when no provider is configured, the **setup guide** opens: *Welcome* (with **Try the demo without a key**,
   which uses the free Mock provider), *Connect providers*, *Done*. Reopen it any time from **Settings > Run setup guide again**
   or the header button **Connect providers**.
2. **Settings > API Connections**: add, replace or remove
   - **Google Gemini**: API key;
   - **Cloudflare Workers AI**: Account ID + API token.
   Press **Test connection** to check them. The test is free (it only lists models) and never generates an image.
3. Your keys are stored by the local backend in Windows Credential Manager, never in the browser, the database, logs or Git.
   The UI shows only whether a credential is stored. Changes apply immediately, no restart. A `.env` file still works as a
   development fallback (the stored credential wins).

## 1. Prepare
1. **References** (left panel): drop the style images (PNG/JPG/WebP), choose each one's type (Style, Category 1–4, Composition,
   Negative example) and click thumbnails to select up to 5. Tick the rights checkbox: references are sent to the image provider.
2. **Generation Setup > Image model**: Gemini is primary. Models marked “Docs only” unlock via *Check model availability*
   (free). OpenAI is listed but disabled. **Mock** is free and clearly marked.
3. **Cards per category** (Auto or Custom): Auto (Balanced) = 2/2/3/3 (our assumption, not a Playrix ratio) or Custom (Σ must be 10,
   each of Cat. 1–4 ≥ 1; Cat. 5 is locked to 0).
4. **Advanced settings** (collapsed): single-card diagnostic mode, plan shuffle/seed, retries info, provider availability.

### Prompts: try ideas without changing the defaults
- **Prompt Library > Session (this tab only):** Edit > Apply to Session changes the System Style, a Category rule or the Closing Constraints for this browser
  tab only (kept on refresh, gone in a new session). "Session Override Active" shows while it is on; Reset to Default / Reset All undo it.
  A job takes a snapshot of its prompts when you press Generate, so later edits never change a running batch. Permanent versions are a separate mode.
- **See the exact prompt:** open a card > **View Prompt** (exact text stored with the image). **Preview Prompts** (optional) shows the prompts of a
  planned set before any image is made. Nothing needs approval.
- **References apply per card:** a Category N reference only goes to Category N cards, a Character reference only to golden cards, Style / Composition /
  Negative references to every card. If none applies, the card is made from the text rules alone.

### Choosing the Cloudflare free models
- After you connect Cloudflare, two models appear as **Configured**: **FLUX.2 Klein 4B** (text + up to 4 references; each is
  downscaled to fit under 512x512) and **FLUX.1 Schnell** (text only). They show **Live in app** only after a real request has
  succeeded; this has not happened yet on the development machine.
- With Schnell, references are not used: the job records that, and a single card started with references is refused with an explanation.
- "Free" means the Workers Free allocation of **10,000 Neurons per day** (resets 00:00 UTC), roughly 90 Klein or 170 Schnell
  images at 1024x1024 by Cloudflare's published estimates. It is not unlimited.
- **Quota error**: when the daily allocation is used up, the job stops after one call with a "quota exceeded" message and
  nothing is charged. Wait for the reset, or switch model/provider yourself; the app never falls back silently and never
  upgrades your plan. If your account is on Workers Paid, set `CLOUDFLARE_PLAN=paid` (see the Runbook) so estimates,
  confirmations and budget caps apply.

## 2. Generate a set
1. Title row / Set & Theme: choose Genre, Variants (final deliverable needs 3–5; use fewer while the budget is small), Run scope:
   **Pilot** (4 cards per variant, one per category) or **Full** (10 per variant). Optional *Producer's free wishes*
   steer subject choice (e.g. “no buildings”) and the mood; the AI planner chooses the cards for your Collection and Set.
2. Review the **plan preview** and the cost estimate, then press the button. Paid runs need a second click to confirm.
   Free-plan Cloudflare runs show no cost and need no confirmation.
3. Watch the gallery fill in. **Cancel** stops after the in-flight call. After a pilot, **Continue** generates the rest.

## 3. Screen tour
- **Pipeline strip** (top): Plan, Generate, QA, Compare, Results, Export; shows where the current job is.
- **Title row**: **Compare Variants**, **Prompt Library** and an **Export** dropdown.
- **Tabs**: Results, Set Plans (JSON), QA Report, Generation Log. Variant rows show a radio, the real QA/review status and
  category-coverage chips; run time comes from the job log (fast mock runs show 0 sec).
- **Set Summary**, **Quick Actions** (Regenerate selected, Edit set plan, Replace a card, Generate similar set) and the
  **Next Steps** checklist reflect the real state of the selected variant.
- Left panel: Set & Theme, Rules/Style/Brief, References, Generation Setup.

## 4. Check, review, approve
- Each card shows a QA dot (green/amber/red) and review marks. **Set QA** per variant lists the checks. QA is structural only:
  *it does not detect text in the image or style problems* — look at every image.
- Click a card: **Approve / Reject**, add a *repair note* and **Regenerate** (max 2 per card; earlier versions stay),
  **Replace object…**, or **Generate this card** if it is still planned.
- **Approve variant** needs 10 generated cards, no failed set QA and no rejected cards. One variant per genre is final.

## 5. Compare
- **Reference Compare**: Split Slider (drag or ←/→), Side by Side with synced zoom (Fit/100%), Grid Overview of all pairs (filter by
  category). The reference is matched automatically (category-typed first); override per card from the dropdown.
- **Compare Variants** puts the same position of every variant side by side.

## 6. Export and the final deliverable
- **Export** (title row dropdown or variant): `artwork/NN_object_catN.png`, `manifest.json`, `set-plan.json`, `QA-report.json`, `contact-sheet.png`.
- **History → Collection progress** shows each genre's readiness; **Export final bundle** needs an approved, complete,
  non-mock set per genre. *Plan all three genres at once* creates the three jobs under one confirmation.
- Paste the exported artwork into the Playrix Miro frames by hand (as the assignment asks).

## 7. Other tools
- **Prompt Library**: view versions; switch on *Advanced mode* to edit; every save is a new version recorded in each
  image's provenance; restore any older version.
- **Templates**: save/apply Generate-screen presets. **Settings**: API Connections, setup guide, provider/budget/limit overview.
- **History → Import a sample image**: add an image made elsewhere (e.g. another model) with honest provider/model labels.
