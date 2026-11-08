import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  ExportRecord, JobCard, JobDetail, JobEvent, JobPreview, JobSummary, JobVariant, ModelInfo, PromptsPreviewResponse, QAReport,
} from "../types/api";
import { CheckList, QABadge, ReviewBadge } from "./badges";
import { CompareView } from "./CompareView";
import { catText, vlabel } from "../lib/cards";
import { PlanNotice, type PlanError } from "./PlanNotice";
import { STATUS_LABEL } from "./ModelSettings";
import { PipelineStrip, type PipeStep } from "./Pipeline";
import { PromptLibrary } from "./PromptLibrary";
import { PromptsView } from "./PromptsView";
import { ImagePrompt } from "./PromptViewer";
import { useSessionPrompts } from "../lib/sessionPrompts";
import { MySelection } from "./MySelection";
import { PlanOnlyPanel, type PlanOnlyState } from "./PlanOnlyPanel";
import { themeKey, useFavorites, type FavItem } from "../lib/favorites";

/** Tabs shown in the tab bar; "variants", "compare" and "library" are opened from the title-row buttons / quick actions. */
type View = "results" | "plans" | "qa" | "log" | "variants" | "compare" | "library" | "prompts";

/** Settings of the Generate form, used for the plan preview (no job yet). */
export interface FormSummary {
  collection: string;
  genre: string;
  variants: number;
  numberOfCards: number;
  gold: number;
  setType: "standard" | "grand";
  outputSize: number;
  provider: string;
  model: string;
}

interface Props {
  job: JobDetail | null;
  preview: JobPreview | null;
  previewError: PlanError | null;
  planningAi: boolean;
  onPlanWithAi: () => void;
  onConnectGemini: () => void;
  events: JobEvent[];
  jobs: JobSummary[];
  models: ModelInfo[];
  form: FormSummary;
  error: string | null;
  starting: boolean;
  perImage: number;
  onPickJob: (id: string) => void;
  onContinue: (retryFailed: boolean) => void;
  continueArmed: boolean;
  onCancelContinue: () => void;
  onCancelJob: () => void;
  onNew: () => void;
  onSimilar: (job: JobDetail) => void;
  onOpenHistory: () => void;
  onJob: (d: JobDetail) => void;
  onError: (m: string | null) => void;
  planOnly?: PlanOnlyState | null;
  onClosePlanOnly?: () => void;
  /** compiled prompts of the current plan (same backend compiler as real generation); optional inspection */
  onPreviewPrompts?: () => Promise<PromptsPreviewResponse>;
}

const STATUS_TEXT: Record<string, string> = {
  created: "Created", planning: "Planning", plan_qa: "Plan QA", queued: "Queued", generating: "Generating",
  image_qa: "Technical QA", set_qa: "Set QA", ready_for_review: "Ready for review",
  partial_success: "Partial (some cards missing)", failed: "Failed", cancel_requested: "Cancelling…",
  cancelled: "Cancelled", interrupted: "Interrupted (server restarted)",
};
const msg = (e: unknown, f: string) => (e instanceof ApiError ? `${e.message}${e.code ? ` [${e.code}]` : ""}` : f);

/** Sum of the "Generating N card(s)" -> "Run finished" windows in the job log (real timestamps, no guessing). */
function runSeconds(events: JobEvent[]): number | null {
  let total = 0, start: number | null = null, any = false;
  for (const e of events) {
    const t = Date.parse(e.ts);
    if (Number.isNaN(t)) continue;
    if (e.message.startsWith("Generating ")) start = t;
    else if (e.message.startsWith("Run finished") && start != null) { total += (t - start) / 1000; start = null; any = true; }
  }
  return any ? total : null;
}
function fmtDuration(s: number): string {
  const r = Math.round(s);
  if (r < 60) return `${r} sec`;
  return `${Math.floor(r / 60)} min ${String(r % 60).padStart(2, "0")} sec`;
}
const money = (n: number) => `$${n.toFixed(n < 1 ? 3 : 2)}`;

interface ExportMsg { ok: boolean; text: string; rec?: ExportRecord; retry?: { label: string; run: () => void } }

export function SetPanel(p: Props) {
  const [view, setView] = useState<View>("results");
  const [pickedId, setPickedId] = useState<string | null>(null);
  const [selVariantId, setSelVariantId] = useState<string | null>(null);
  const [retryFailed, setRetryFailed] = useState(false);
  const [exports, setExports] = useState<ExportRecord[]>([]);
  const [expMsg, setExpMsg] = useState<ExportMsg | null>(null);
  const [expBusy, setExpBusy] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [replaceReq, setReplaceReq] = useState(0);
  const [regenReq, setRegenReq] = useState(0);
  const [saved, setSaved] = useState<Record<string, string>>({});
  const detailRef = useRef<HTMLDivElement>(null);
  const variantsRef = useRef<HTMLDivElement>(null);
  const job = p.job;
  const [keepKey, setKeepKey] = useState<string | null>(null);
  const sessionPrompts = useSessionPrompts();

  // ---- My Selection (session favorites), scoped to Collection + Set + Set type of the set that is shown
  const shownSetType: "standard" | "grand" = job ? ((job.set_type ?? job.request.set_type) === "grand" ? "grand" : "standard") : p.form.setType;
  const shownCollection = job?.collection ?? p.form.collection;
  const shownGenre = job?.genre ?? p.form.genre;
  const favKey = themeKey(shownCollection, shownGenre, shownSetType);
  const favs = useFavorites(favKey);
  /** Batch number = order of the job among all jobs of this Collection + Set + Set type. */
  const batchOf = (jobId: string): number | null => {
    const mine = p.jobs.filter((j) => themeKey(j.collection, j.genre, j.set_type ?? "standard") === favKey)
      .sort((a, b) => a.created_at.localeCompare(b.created_at));
    const i = mine.findIndex((j) => j.job_id === jobId);
    return i < 0 ? null : i + 1;
  };
  const makeFav = (c: JobCard, variantId: string, artifactId: string, imageUrl: string, version: number | null): FavItem => ({
    artifact_id: artifactId, job_id: job!.job_id, card_id: c.card_id, variant_id: variantId, version,
    object_name: c.object_name, category: c.category, position: c.position,
    provider: job!.request.provider, model: job!.request.model, batch: batchOf(job!.job_id),
    image_url: imageUrl, added_at: Date.now(),
  });
  const starCard = (c: JobCard, variantId: string) => {
    if (!job || !c.artifact_id || !c.image_url) return;
    favs.toggle(makeFav(c, variantId, c.artifact_id, c.image_url, c.versions.find((x) => x.is_current)?.version ?? null));
  };
  const starVersion = (c: JobCard, variantId: string, v: { artifact_id: string; image_url: string; version: number }) => {
    if (job) favs.toggle(makeFav(c, variantId, v.artifact_id, v.image_url, v.version));
  };

  const pct = job && job.counts.total ? Math.round((job.counts.done / job.counts.total) * 100) : 0;
  const picked = job?.variants.flatMap((v) => v.cards).find((c) => c.card_id === pickedId) ?? null;
  const pickedVariant = job?.variants.find((v) => v.cards.some((c) => c.card_id === pickedId));
  const selV = job ? (job.variants.find((v) => v.variant_id === selVariantId) ?? job.variants[0]) : undefined;
  // real cards-per-variant (old jobs without the field: derive from the cards)
  const cardsN = job ? (job.number_of_cards ?? job.variants[0]?.cards.length ?? 10)
    : p.preview ? (p.preview.number_of_cards ?? p.preview.plan.variants[0]?.cards.length ?? p.form.numberOfCards) : p.form.numberOfCards;
  const selTotal = selV ? (selV.set_qa.total || selV.cards.length) : cardsN;
  // The form no longer describes the displayed job: it is a previous run, not the active generation.
  const jobCards = job ? (job.number_of_cards ?? job.request.number_of_cards ?? job.variants[0]?.cards.length ?? 10) : 0;
  const jobGold = job ? (job.gold_cards ?? job.request.gold_cards ?? 0) : 0;
  const formSig = JSON.stringify([p.form.collection.trim(), p.form.genre.trim(), p.form.variants, p.form.numberOfCards, p.form.gold]);
  const stale = !!job && (job.collection.trim() !== p.form.collection.trim() || job.genre.trim() !== p.form.genre.trim()
    || jobCards !== p.form.numberOfCards || job.variants.length !== p.form.variants || jobGold !== p.form.gold);
  const showStrip = stale && keepKey !== formSig;
  const planMode = job ? (job.planning_mode ?? "rules") : (p.preview?.planning_mode ?? "rules");

  useEffect(() => {
    setPickedId(null);
    setSelVariantId(job?.approved_variant_id ?? job?.variants[0]?.variant_id ?? null);
    setExpMsg(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.job_id]);

  const refreshExports = useCallback(async () => { try { setExports(await api.exports()); } catch { /* shown in History */ } }, []);
  useEffect(() => { refreshExports(); }, [refreshExports, job?.job_id]);
  const jobExports = useMemo(() => {
    if (!job) return [];
    return exports.filter((x) => (x.job_ids ? x.job_ids.includes(job.job_id)
      : x.kind === "variant" && x.genre === job.genre && x.created_at >= job.created_at));
  }, [exports, job]);

  useEffect(() => { if (pickedId) detailRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [pickedId]);

  const pickCard = (id: string) => {
    setPickedId(id);
    const v = job?.variants.find((x) => x.cards.some((c) => c.card_id === id));
    if (v) setSelVariantId(v.variant_id);
    setView("results");
  };

  // ------------------------------------------------------------------------------------ exports
  async function exportVariant(vid: string, force: boolean) {
    if (!job) return;
    setExpBusy(true);
    try {
      const rec = await api.exportVariant(job.job_id, vid, force ? { include_unapproved: true, allow_mock: true } : {});
      setExpMsg({ ok: true, text: "Export ready", rec });
      refreshExports();
    } catch (e) {
      const m = msg(e, "Export failed");
      const retry = /not approved|MOCK/.test(m) && !/incomplete|rejected|set QA failed/.test(m);
      setExpMsg({ ok: false, text: m, retry: retry ? { label: "Export anyway (unapproved / mock, for testing)", run: () => exportVariant(vid, true) } : undefined });
    } finally { setExpBusy(false); }
  }
  async function exportBundle(force: boolean) {
    setExpBusy(true);
    try {
      const rec = await api.exportCollection(force ? { include_unapproved: true, allow_mock: true, allow_partial: true } : {});
      setExpMsg({ ok: true, text: `Final bundle ready${rec.partial ? " (PARTIAL)" : ""}`, rec });
      refreshExports();
    } catch (e) {
      setExpMsg({ ok: false, text: msg(e, "Export failed"), retry: force ? undefined : { label: "Export bundle anyway (testing)", run: () => exportBundle(true) } });
    } finally { setExpBusy(false); }
  }

  // ------------------------------------------------------------------------------ pipeline strip
  const allCards = job?.variants.flatMap((v) => v.cards) ?? [];
  const qaPassed = allCards.filter((c) => c.qa_status === "passed").length;
  const qaFlagged = allCards.filter((c) => c.qa_status === "flagged").length;
  const qaFailed = allCards.filter((c) => c.qa_status === "failed").length;
  const finished = !!job && !job.active && job.counts.done > 0;
  const steps: PipeStep[] = [
    { key: "plan", title: "Plan", sub: job ? `${job.variants.length} variant(s) · ${planMode === "ai" ? "AI planner" : "legacy plan"}` : p.preview ? "Preview ready, nothing spent" : "Fill in the form",
      done: !!job || !!p.preview, current: view === "plans", onClick: () => setView("plans"), hint: "Open the set plans" },
    { key: "gen", title: "Generate", sub: job ? `${job.counts.done}/${job.counts.total} images${job.active ? " · running" : ""}` : "Not started",
      active: !!job?.active, done: finished, current: view === "results", onClick: () => setView("results"), hint: "Back to the results grid" },
    { key: "qa", title: "QA", sub: job && job.counts.done ? `${qaPassed} passed${qaFlagged ? ` · ${qaFlagged} flagged` : ""}${qaFailed ? ` · ${qaFailed} failed` : ""}` : "Runs after generation",
      active: job?.status === "image_qa" || job?.status === "set_qa", done: finished, current: view === "qa", onClick: () => setView("qa"), hint: "Open the QA report" },
    { key: "cmp", title: "Compare", sub: "Variants & references", optional: true, current: view === "variants" || view === "compare",
      onClick: () => setView("variants"), hint: job ? "Compare variants or check a card against a reference" : "Available once a set is generated" },
    { key: "res", title: "Results", sub: job?.approved_variant_id ? `Variant ${vlabel(job.approved_variant_id)} approved` : job ? `Review: ${selV?.set_qa.art_review.approved ?? 0} approved` : "Art review: pending",
      done: !!job?.approved_variant_id, onClick: () => { setView("results"); variantsRef.current?.scrollIntoView({ block: "start", behavior: "smooth" }); }, hint: "Review and approve a variant" },
    { key: "exp", title: "Export", sub: jobExports.length ? `${jobExports.length} export(s) created` : "ZIP · final bundle",
      done: jobExports.length > 0, current: exportOpen, onClick: () => setExportOpen((o) => !o), hint: "Open the Export menu" },
  ];

  const TABS: [View, string][] = [["results", "Results"], ["plans", "Set Plans (JSON)"], ["qa", "QA Report"], ["log", "Generation Log"]];
  const inExtra = view === "variants" || view === "compare" || view === "library" || view === "prompts";
  const heading = job ? `${stale ? "Previous result" : "Generated Sets"} — ${job.collection}: ${job.genre}` : p.preview ? `Plan Preview — ${p.form.collection}: ${p.form.genre}` : `Generated Sets — ${p.form.collection}: ${p.form.genre}`;
  const nVariants = job ? job.variants.length : p.preview ? p.preview.plan.variants.length : p.form.variants;
  const exportItems: MenuItem[] = [
    { key: "v", label: selV ? `Variant ${vlabel(selV.variant_id)} — ZIP` : "Selected variant — ZIP", sub: selV?.approved ? "approved variant" : "needs approval", disabled: !job || !selV || job.active || selV.set_qa.generated < selTotal || expBusy,
      reason: !job ? "Generate a set first" : job.active ? "Wait for the job to finish" : `Needs all ${selTotal} cards generated`, onClick: () => selV && exportVariant(selV.variant_id, false) },
    { key: "b", label: "Final bundle — all genres (ZIP)", sub: "needs one approved variant per genre", disabled: expBusy, onClick: () => exportBundle(false) },
    { key: "sep", label: "", separator: true },
    { key: "vf", label: selV ? `Variant ${vlabel(selV.variant_id)} anyway` : "Variant anyway", sub: "unapproved / mock — testing only", disabled: !job || !selV || job.active || selV.set_qa.generated < selTotal || expBusy,
      reason: `Needs a finished variant with all ${selTotal} cards`, onClick: () => selV && exportVariant(selV.variant_id, true) },
    { key: "bf", label: "Final bundle anyway", sub: "partial / mock — testing only", disabled: expBusy, onClick: () => exportBundle(true) },
    { key: "sep2", label: "", separator: true },
    { key: "h", label: "Export history…", sub: "all earlier exports", onClick: p.onOpenHistory },
  ];

  return (
    <section className="results">
      <PipelineStrip steps={steps} />

      <div className="tabs" role="tablist">
        {TABS.map(([t, label]) => (
          <button key={t} role="tab" aria-selected={view === t} className={view === t ? "on" : ""} onClick={() => setView(t)}>{label}</button>
        ))}
        {p.jobs.length > 0 && (
          <select className="jobpick" aria-label="Recent jobs" value={job?.job_id ?? ""} onChange={(e) => e.target.value && p.onPickJob(e.target.value)}>
            <option value="">Recent jobs…</option>
            {p.jobs.map((j) => (
              <option key={j.job_id} value={j.job_id}>{j.genre} ×{j.variants} · {STATUS_TEXT[j.status] ?? j.status} · {new Date(j.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</option>
            ))}
          </select>
        )}
      </div>

      <div className="banner">
        <div className="banner-main">
          <span className="banner-ic" aria-hidden>▨</span>
          <div>
            <strong>This tool generates object illustrations for collectible cards (not full game windows).</strong>
            <span>{`Artwork only. Each variant is a full ${cardsN}-card set; the category distribution is the same in every variant.`}</span>
          </div>
        </div>
        <ul className="banner-checks">
          <li>Object illustrations (card assets)</li>
          <li>{nVariants} variant{nVariants === 1 ? "" : "s"} per set</li>
          <li>Final set layout assembled manually (outside this tool)</li>
        </ul>
      </div>

      <div className="titlerow">
        <h1>{heading}{stale && <span className="chip prev">Previous run</span>}</h1>
        <div className="titlebtns">
          <button type="button" className={`btn outline ${view === "variants" || view === "compare" ? "on" : ""}`} disabled={!job} onClick={() => setView("variants")}
            title={job ? "Compare the variants side by side, or check cards against references" : "Generate a set first"}><span aria-hidden>◫</span> Compare Variants</button>
          <button type="button" className={`btn outline ${view === "prompts" ? "on" : ""}`} disabled={!job && !p.preview} onClick={() => setView("prompts")}
            title={job || p.preview ? "Optional: see the exact prompts of this set (no image is generated)" : "Plan or generate a set first"}><span aria-hidden>⌕</span> Preview Prompts</button>
          <button type="button" className={`btn outline ${view === "library" ? "on" : ""}`} onClick={() => setView("library")} title="View and edit the prompt templates (session-only or permanent)">
            <span aria-hidden>❝</span> Prompt Library{sessionPrompts.count > 0 && <span className="chip warn tiny sess-badge">Session Override Active</span>}</button>
          <ExportMenu open={exportOpen} setOpen={setExportOpen} items={exportItems} busy={expBusy} />
        </div>
      </div>

      {p.error && <div className="alert err" role="alert">{p.error}</div>}
      {showStrip && job && (
        <div className="prevrun" role="status">
          <p>
            <strong>{job.active ? "Showing the running job" : "Showing a previous run"}:</strong> {job.collection} · {job.genre} ({jobCards} cards, {job.variants.length} variant{job.variants.length === 1 ? "" : "s"}{jobGold > 0 ? `, ${jobGold} ★` : ""}).
            {" "}Your form now says: <strong>{p.form.collection || "(no collection)"} · {p.form.genre || "(no set)"}</strong> ({p.form.numberOfCards} cards, {p.form.variants} variant{p.form.variants === 1 ? "" : "s"}{p.form.gold > 0 ? `, ${p.form.gold} ★` : ""}).
            {" "}Nothing has been generated for the form yet.
          </p>
          <span className="btns">
            <button type="button" className="primary small" disabled={job.active} title={job.active ? "Wait for the running job to finish" : undefined} onClick={p.onNew}>Plan this form</button>
            <button type="button" className="ghost" onClick={() => setKeepKey(formSig)}>Keep viewing</button>
          </span>
        </div>
      )}
      {stale && p.preview && !p.previewError && <p className="hint plan-hint">A plan for your form is ready ({p.preview.images_in_scope} image{p.preview.images_in_scope === 1 ? "" : "s"}, nothing generated). Click “Plan this form” to review it.</p>}
      {p.previewError && (!job || stale) && <PlanNotice error={p.previewError} planning={p.planningAi} onPlanWithAi={p.onPlanWithAi} onConnectGemini={p.onConnectGemini} />}
      {expMsg && (
        <div className={`alert ${expMsg.ok ? "ok" : "warn"}`} role="status">
          {expMsg.text}
          {expMsg.rec && <>: <a href={expMsg.rec.download_url} download={expMsg.rec.filename}>{expMsg.rec.filename}</a> ({(expMsg.rec.size / 1024).toFixed(0)} KB){expMsg.rec.contains_mock_images && " · contains MOCK images"}</>}
          {expMsg.retry && <> <button type="button" className="ghost" onClick={expMsg.retry.run}>{expMsg.retry.label}</button></>}
          <button type="button" className="alert-x" aria-label="Dismiss" onClick={() => setExpMsg(null)}>×</button>
        </div>
      )}

      {view === "results" && (
        <div className="stage flat">
          {p.planOnly && (p.planOnly.busy || p.planOnly.result || p.planOnly.error) && <PlanOnlyPanel state={p.planOnly} onClose={() => p.onClosePlanOnly?.()} />}
          {job && (
            <div className="jobbar">
              <div className="jobhead">
                {batchOf(job.job_id) != null && <span className="chip batch" title="Consecutive generations of this Collection + Set">Batch #{batchOf(job.job_id)} — {job.counts.total} image{job.counts.total === 1 ? "" : "s"}</span>}
                <span className={`chip ${job.status === "ready_for_review" ? "ok" : job.status === "failed" || job.status === "partial_success" ? "warn" : ""}`}>{STATUS_TEXT[job.status] ?? job.status}</span>
                {job.scope === "pilot" && <span className="chip" title="Older job created with the removed test-run option">Pilot run (old job)</span>}
                <span className="chip">Cat. split {[1, 2, 3, 4].map((c) => job.distribution[c] ?? 0).join(" / ")}{(job.distribution[5] ?? 0) > 0 ? <> / <b className="goldtxt">★ {job.distribution[5]}</b></> : null}</span>
                <span className="chip" title="Local estimate, not a provider invoice">Est. spent ${job.spent_estimated_usd.toFixed(3)}</span>
                <span className="jobcounts">
                  Generated <strong>{job.counts.done}/{job.counts.total}</strong>
                  {job.counts.generating + job.counts.queued > 0 && <> · in progress {job.counts.generating + job.counts.queued}</>}
                  {job.counts.failed > 0 && <> · <span className="bad">failed {job.counts.failed}</span></>}
                  {job.counts.planned > 0 && <> · planned (not generated) {job.counts.planned}</>}
                </span>
                {!job.active && <button type="button" className="ghost sm" onClick={p.onNew}>＋ New plan</button>}
              </div>
              {(job.active || pct < 100) && <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><div style={{ width: `${pct}%` }} /></div>}
              {job.error && <div className="alert warn">{job.error}</div>}
              {(job.active || job.can_continue) && (
                <div className="jobactions">
                  {job.active && <button type="button" className="ghost" onClick={p.onCancelJob} disabled={job.status === "cancel_requested"}>Cancel</button>}
                  {job.can_continue && (
                    <>
                      {job.counts.failed > 0 && (
                        <label className="check inline"><input type="checkbox" checked={retryFailed} onChange={(e) => setRetryFailed(e.target.checked)} /><span>also retry failed</span></label>
                      )}
                      <button type="button" className={`primary small ${p.continueArmed ? "armed" : ""}`} onClick={() => p.onContinue(retryFailed)}>
                        {p.continueArmed
                          ? `Confirm & spend ≈ $${(job.pending_estimate_usd + (retryFailed ? job.counts.failed * p.perImage : 0)).toFixed(3)}`
                          : `Continue: ${job.counts.planned}${retryFailed ? `+${job.counts.failed}` : ""} more card(s)${job.pending_estimate_usd > 0 ? ` · ≈ $${job.pending_estimate_usd.toFixed(3)}` : ""}`}
                      </button>
                      {p.continueArmed && <button type="button" className="ghost" onClick={p.onCancelContinue}>Cancel</button>}
                    </>
                  )}
                </div>
              )}
            </div>
          )}

          {!job && !p.preview && !p.starting && (
            <div className="empty">
              <div className="empty-ic" aria-hidden>▦</div>
              <p><strong>No set generated yet.</strong></p>
              <p className="hint">Choose a set, the number of cards and the number of variants on the left. The plan preview appears here before anything is spent.</p>
            </div>
          )}
          {!job && !p.preview && p.starting && <div className="skeleton"><div className="spinner" />Starting the job…</div>}

          <div ref={variantsRef} className="variants" role="radiogroup" aria-label="Variants">
            {job && job.variants.map((v) => (
              <VariantRow key={v.variant_id} job={job} v={v} selected={selV?.variant_id === v.variant_id} onSelect={setSelVariantId}
                pickedId={pickedId} onPick={pickCard} onJob={p.onJob} onError={p.onError} onExport={(id) => exportVariant(id, false)} exportBusy={expBusy}
                isFav={favs.has} onStar={starCard} />
            ))}
            {!job && p.preview && (
              <>
                <p className="hint plan-hint">Plan preview (nothing generated yet) — {p.preview.images_in_scope} image(s).{" "}
                  <span className={`chip tiny plan-${planMode}`}>{planMode === "ai" ? "Planned by AI" : "Legacy plan"}</span>
                  {(p.preview.plan_cost_estimate_usd ?? 0) > 0 && <> · AI plan ≈ {money(p.preview.plan_cost_estimate_usd ?? 0)}</>}</p>
                {p.preview.plan.variants.map((v) => (
                  <PlanRow key={v.variant_id} id={vlabel(v.variant_id)} title={v.title} concept={v.concept} cards={v.cards.map((c) => ({ key: `${v.variant_id}${c.position}`, cat: c.category, name: c.object_name }))} />
                ))}
              </>
            )}
          </div>

          {job && picked && pickedVariant && (
            <div ref={detailRef}>
              <CardDetail job={job} card={picked} variant={pickedVariant} perImage={p.perImage} onJob={p.onJob} onError={p.onError}
                replaceToken={replaceReq} regenToken={regenReq}
                isFav={favs.has} onStarVersion={starVersion}
                onClose={() => setPickedId(null)} onCompare={() => setView("compare")} />
            </div>
          )}

          <MySelection favs={favs} collection={shownCollection} genre={shownGenre} setType={shownSetType} onOpenJob={p.onPickJob} />

          <div className="bottomrow">
            <SetSummary stale={stale} job={job} preview={p.preview} form={p.form} models={p.models} events={p.events} selV={selV} />
            <QuickActions job={job} hasPlan={!!p.preview} picked={picked} onRegenerate={() => { setRegenReq((n) => n + 1); detailRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }}
              onReplace={() => setReplaceReq((n) => n + 1)}
              onEditPlan={() => { if (picked) setReplaceReq((n) => n + 1); else setView("plans"); }}
              onSimilar={() => job && p.onSimilar(job)} />
            <NextSteps job={job} jobExports={jobExports} saved={job ? saved[job.job_id] : undefined}
              onReview={() => { setView("results"); variantsRef.current?.scrollIntoView({ block: "start", behavior: "smooth" }); }}
              onTweak={() => {
                const v = selV; if (!v) return;
                const target = v.cards.find((c) => c.art_review === "rejected" || c.qa_status === "failed" || c.qa_status === "flagged") ?? v.cards.find((c) => c.image_url);
                if (target) pickCard(target.card_id);
              }}
              onExport={() => setExportOpen(true)}
              onSaved={(name) => job && setSaved((s) => ({ ...s, [job.job_id]: name }))} />
          </div>
        </div>
      )}

      {view === "plans" && (
        <div className="stage">
          {job || p.preview ? (
            <PlansView job={job} preview={p.preview} selV={selV} onSelectVariant={setSelVariantId}
              onEdit={(id) => { pickCard(id); setReplaceReq((n) => n + 1); }} />
          ) : <div className="empty">No plan yet. Fill in the form on the left to preview a plan.</div>}
        </div>
      )}

      {view === "qa" && <div className="stage">{job ? <QAPanel job={job} /> : <div className="empty">Generate a set first.</div>}</div>}

      {inExtra && (
        <div className="stage">
          {view !== "library" && (
            <div className="subnav">
              <div className="seg" role="radiogroup" aria-label="Compare kind">
                <button type="button" role="radio" aria-checked={view === "variants"} className={view === "variants" ? "on" : ""} onClick={() => setView("variants")}>Compare Variants</button>
                <button type="button" role="radio" aria-checked={view === "compare"} className={view === "compare" ? "on" : ""} onClick={() => setView("compare")}>Reference Compare</button>
              </div>
              <button type="button" className="ghost sm" onClick={() => setView("results")}>← Back to results</button>
            </div>
          )}
          {view === "prompts" && <div className="subnav"><strong>Preview Prompts</strong><button type="button" className="ghost sm" onClick={() => setView("results")}>← Back to results</button></div>}
          {view === "library" && <div className="subnav"><strong>Prompt Library</strong><button type="button" className="ghost sm" onClick={() => setView("results")}>← Back to results</button></div>}
          {view === "variants" && (job ? <CompareVariants job={job} onPick={pickCard} /> : <div className="empty">Generate a set first.</div>)}
          {view === "compare" && (job ? <CompareView job={job} focusCardId={pickedId} /> : <div className="empty">Generate a set first.</div>)}
          {view === "library" && <PromptLibrary />}
          {view === "prompts" && <PromptsView job={job} variantId={selV?.variant_id} hasPlan={!!p.preview && !!p.onPreviewPrompts} onPreview={() => p.onPreviewPrompts!()} />}
        </div>
      )}

      {view === "log" && (
        <div className="stage">
          {p.events.length ? (
            <ul className="log">{p.events.map((e) => <li key={e.id} className={e.level}><time>{new Date(e.ts).toLocaleTimeString()}</time> {e.message}</li>)}</ul>
          ) : <div className="empty">No events yet.</div>}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------------------------ export menu
interface MenuItem { key: string; label: string; sub?: string; disabled?: boolean; reason?: string; separator?: boolean; onClick?: () => void }

function ExportMenu({ open, setOpen, items, busy }: { open: boolean; setOpen: (v: boolean) => void; items: MenuItem[]; busy: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open, setOpen]);
  return (
    <div className="menu" ref={ref}>
      <button type="button" className="btn solid" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span aria-hidden>⭳</span> {busy ? "Exporting…" : "Export"} <span className="caret" aria-hidden>▾</span>
      </button>
      {open && (
        <ul className="menu-list" role="menu">
          {items.map((it) => it.separator ? <li key={it.key} role="separator" className="sep" /> : (
            <li key={it.key} role="none" title={it.disabled ? it.reason : undefined}>
              <button type="button" role="menuitem" disabled={it.disabled} onClick={() => { setOpen(false); it.onClick?.(); }}>
                <span>{it.label}</span>{it.sub && <small>{it.sub}</small>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------- variant rows
const CAT_LIST = [1, 2, 3, 4, 5] as const;

function CoverageChips({ cards }: { cards: { category: number; status: string }[] }) {
  return (
    <span className="cov" aria-label="Category coverage">
      {CAT_LIST.filter((c) => c < 5 || cards.some((x) => x.category === 5)).map((c) => {
        const total = cards.filter((x) => x.category === c).length;
        const done = cards.filter((x) => x.category === c && x.status === "done").length;
        return <span key={c} className={`covchip cat${c} ${total === 0 ? "none" : done === total ? "full" : done > 0 ? "part" : "zero"}`} title={`${c === 5 ? "Gold (Cat. 5)" : `Category ${c}`}: ${done} of ${total} generated`}>{catText(c)}</span>;
      })}
    </span>
  );
}

function VariantRow({ job, v, selected, onSelect, pickedId, onPick, onJob, onError, onExport, exportBusy, isFav, onStar }: {
  job: JobDetail; v: JobVariant; selected: boolean; onSelect: (id: string) => void; pickedId: string | null; onPick: (id: string) => void;
  onJob: (d: JobDetail) => void; onError: (m: string | null) => void; onExport: (variantId: string) => void; exportBusy: boolean;
  isFav: (artifactId: string) => boolean; onStar: (c: JobCard, variantId: string) => void;
}) {
  const [showQA, setShowQA] = useState(false);
  const [busy, setBusy] = useState(false);
  const qa = v.set_qa;
  const total = qa.total || v.cards.length;
  const canApprove = !job.active && qa.generated === total && qa.status !== "failed" && qa.art_review.rejected === 0;
  const cardQA = (s: string) => v.cards.filter((c) => c.image_url && c.qa_status === s).length;

  async function approve() {
    setBusy(true);
    try { onJob(await (v.approved ? api.unapproveVariant(job.job_id, v.variant_id) : api.approveVariant(job.job_id, v.variant_id))); onError(null); }
    catch (e) { onError(msg(e, "Action failed")); } finally { setBusy(false); }
  }

  const complete = qa.generated >= qa.total && qa.total > 0;
  return (
    <div className={`vrow ${selected ? "sel" : ""} ${v.approved ? "approved" : ""}`}>
      <div className="vmeta">
        <label className="vradio">
          <input type="radio" name="variant" checked={selected} onChange={() => onSelect(v.variant_id)} />
          <strong>Variant {vlabel(v.variant_id)}</strong>
          {v.approved && <span className="chip ok" title="This variant is the approved final set">★ final set</span>}
        </label>
        <p className="vconcept" title={`${v.title} — ${v.concept}`}><em>{v.title}</em> · {v.concept}</p>
        <ul className="vstat">
          <li className={complete ? "ok" : "warn"}><i aria-hidden>{complete ? "✓" : "◔"}</i>{qa.generated} / {qa.total} cards generated</li>
          <li>
            <button type="button" className={`qa qa-${qa.status}`} onClick={() => setShowQA(!showQA)} aria-expanded={showQA} title="Show the set QA checks">Set QA: {qa.status} {showQA ? "▴" : "▾"}</button>
            <span className="muted"> cards: {cardQA("passed")} ✓{cardQA("flagged") ? ` · ${cardQA("flagged")} ⚠` : ""}{cardQA("failed") ? ` · ${cardQA("failed")} ✕` : ""}</span>
          </li>
          <li className={qa.art_review.rejected ? "bad" : ""}><i aria-hidden>◉</i>Review: {qa.art_review.approved} ✓ · {qa.art_review.pending} pending · {qa.art_review.rejected} ✕</li>
          <li className="covline"><i aria-hidden>▦</i>Category coverage: <CoverageChips cards={v.cards} /></li>
        </ul>
        <div className="vbtns">
          <button type="button" className={`ghost sm ${v.approved ? "on" : ""}`} disabled={busy || (!v.approved && !canApprove)} onClick={approve}
            title={canApprove || v.approved ? "" : `Needs ${total} generated cards, no failed set QA and no rejected cards`}>
            {v.approved ? "Withdraw approval" : "Approve variant"}
          </button>
          <button type="button" className="ghost sm" disabled={exportBusy || qa.generated < total || job.active} onClick={() => onExport(v.variant_id)}
            title={qa.generated < total ? `Needs all ${total} cards generated` : "Export this variant as a ZIP"}>Export ZIP</button>
        </div>
        {showQA && <CheckList checks={qa.checks} />}
      </div>
      <Slots cards={v.cards} variantId={v.variant_id} pickedId={pickedId} onPick={onPick} isFav={isFav} onStar={onStar} />
    </div>
  );
}

function Slots({ cards, variantId, pickedId, onPick, isFav, onStar }: {
  cards: JobCard[]; variantId: string; pickedId: string | null; onPick: (id: string) => void;
  isFav: (artifactId: string) => boolean; onStar: (c: JobCard, variantId: string) => void;
}) {
  return (
    <ol className="slots">
      {cards.map((c) => (
        <li key={c.card_id} className={`slot ${c.status} ${c.category === 5 ? "gold" : ""} ${pickedId === c.card_id ? "on" : ""}`}>
          <button type="button" onClick={() => onPick(c.card_id)} title={`${c.object_name} — ${c.error ?? c.object_description}`} aria-label={`${c.object_name}, ${c.category === 5 ? "golden card" : `category ${c.category}`}, ${c.status}`}>
            {c.image_url ? <img src={c.image_url} alt={c.object_name} /> : (
              <span className="ph">{c.status === "generating" ? <i className="spinner sm" /> : c.status === "failed" ? "!" : c.status === "queued" ? "…" : <em>{c.object_name}</em>}</span>
            )}
            <span className={`cat c${c.category}`}>{catText(c.category)}</span>
            {c.image_url && <span className={`dot qa-${c.qa_status}`} aria-label={`QA ${c.qa_status}`} />}
            {c.art_review === "approved" && <span className="rvmark ok">✓</span>}
            {c.art_review === "rejected" && <span className="rvmark bad">✕</span>}
            {c.versions.length > 1 && <span className="vmark">v{c.versions.find((x) => x.is_current)?.version}</span>}
          </button>
          {c.image_url && c.artifact_id && (
            <button type="button" className={`starbtn ${isFav(c.artifact_id) ? "on" : ""}`} aria-pressed={isFav(c.artifact_id)}
              aria-label={isFav(c.artifact_id) ? `Remove ${c.object_name} from My Selection` : `Add ${c.object_name} to My Selection`}
              title={isFav(c.artifact_id) ? "In My Selection (click to remove)" : "Add this exact image to My Selection"}
              onClick={() => onStar(c, variantId)}>{isFav(c.artifact_id) ? "★" : "☆"}</button>
          )}
        </li>
      ))}
    </ol>
  );
}

function PlanRow({ id, title, concept, cards }: { id: string; title: string; concept: string; cards: { key: string; cat: number; name: string }[] }) {
  return (
    <div className="vrow plan">
      <div className="vmeta">
        <div className="vradio"><input type="radio" disabled checked={false} readOnly aria-label={`Variant ${id} (plan only)`} /><strong>Variant {id}</strong><span className="chip">plan</span></div>
        <p className="vconcept" title={`${title} — ${concept}`}><em>{title}</em> · {concept}</p>
        <ul className="vstat"><li className="muted"><i aria-hidden>◔</i>0 / {cards.length} cards generated</li>
          <li className="covline"><i aria-hidden>▦</i>Category coverage: <CoverageChips cards={cards.map((c) => ({ category: c.cat, status: "planned" }))} /></li></ul>
      </div>
      <ol className="slots">
        {cards.map((c) => (
          <li key={c.key} className={`slot planned ${c.cat === 5 ? "gold" : ""}`}><button type="button" disabled title={c.name}><span className="ph"><em>{c.name}</em></span><span className={`cat c${c.cat}`}>{catText(c.cat)}</span></button></li>
        ))}
      </ol>
    </div>
  );
}

// ------------------------------------------------------------------------------------ bottom row
function SetSummary({ stale, job, preview, form, models, events, selV }: {
  stale: boolean; job: JobDetail | null; preview: JobPreview | null; form: FormSummary; models: ModelInfo[]; events: JobEvent[]; selV?: JobVariant;
}) {
  const provider = job?.request.provider ?? form.provider;
  const modelId = job?.request.model ?? form.model;
  const size = job ? (job.output_size ?? job.request.output_size ?? form.outputSize) : (preview?.output_size ?? form.outputSize);
  const nCards = job ? (job.number_of_cards ?? job.variants[0]?.cards.length ?? 10) : preview?.number_of_cards ?? form.numberOfCards;
  const nGold = job ? (job.gold_cards ?? job.request.gold_cards ?? job.variants[0]?.cards.filter((c) => c.category === 5).length ?? 0) : preview?.gold_cards ?? form.gold;
  const m = models.find((x) => x.provider_id === provider && x.model_id === modelId);
  const secs = job ? runSeconds(events) : null;
  const thumbs = (selV ?? job?.variants[0])?.cards.filter((c) => c.image_url).slice(0, 3) ?? [];
  const nVar = job ? job.variants.length : preview?.plan.variants.length ?? form.variants;
  const cost = job ? job.spent_estimated_usd : preview?.estimated_cost_usd ?? 0;
  const free = provider === "mock";
  return (
    <section className="bcard summary" aria-label="Set summary">
      <h3><span aria-hidden>▤</span> Set Summary{stale ? " (previous run)" : ""}</h3>
      <div className="summary-body">
        <dl>
          <dt>Collection</dt><dd>{job?.collection ?? form.collection}</dd>
          <dt>Set</dt><dd>{job?.genre ?? form.genre}</dd>
          <dt>Cards</dt><dd>{nGold > 0 ? `${nCards - nGold} + ${nGold} ★ gold` : nCards}{job && job.scope === "pilot" ? " · pilot run" : ""}</dd>
          <dt>Variants</dt><dd>{nVar}</dd>
          <dt>Image model</dt><dd>{m ? m.label : modelId || "—"} {m && <span className={`chip tiny st-${m.status}`}>{STATUS_LABEL[m.status] ?? m.status}</span>}</dd>
          <dt>Output Resolution</dt><dd>{size} × {size} px</dd>
          <dt>Run time</dt><dd>{secs != null ? fmtDuration(secs) : "—"}{secs != null && <span className="muted"> (from job log)</span>}</dd>
          <dt>Cost</dt><dd>{free ? "$0.000 (mock, free)" : money(cost)} <span className="chip tiny" title="Calculated locally from list prices; not a provider invoice">estimate</span></dd>
        </dl>
        <div className="stack" aria-hidden={thumbs.length === 0}>
          {thumbs.length > 0 ? (
            <>
              <div className="stackimgs">{thumbs.map((c, i) => <img key={c.card_id} src={c.image_url!} alt="" className={`s${i}`} />)}</div>
              <span className="chip tiny stackcap">{job ? `${job.counts.done} / ${job.counts.total} images generated` : ""}</span>
            </>
          ) : <div className="stack-empty">{job ? "No images yet" : "Preview only"}</div>}
        </div>
      </div>
    </section>
  );
}

function QuickActions({ job, hasPlan, picked, onRegenerate, onReplace, onEditPlan, onSimilar }: {
  job: JobDetail | null; hasPlan: boolean; picked: JobCard | null; onRegenerate: () => void; onReplace: () => void; onEditPlan: () => void; onSimilar: () => void;
}) {
  const running = !!job?.active;
  const cardReason = !job ? "Generate a set first" : running ? "Wait for the running job to finish" : !picked ? "Select a card in the grid first" : null;
  const regenReason = cardReason ?? (picked && picked.image_url && picked.regen_remaining <= 0 ? "Regeneration limit reached for this card" : picked && (picked.status === "queued" || picked.status === "generating") ? "This card is being generated" : null);
  const paid = !!job && job.request.provider !== "mock";
  const items: { key: string; icon: string; label: string; reason: string | null; run: () => void; hint?: string }[] = [
    { key: "regen", icon: "↻", label: picked && !picked.image_url ? "Generate selected card" : "Regenerate selected", reason: regenReason, run: onRegenerate, hint: paid ? "Paid model: asks you to confirm the cost first" : undefined },
    { key: "plan", icon: "✎", label: "Edit set plan", reason: running ? "Wait for the running job to finish" : !job && !hasPlan ? "Fill in the form to get a plan first" : null, run: onEditPlan, hint: picked ? "Opens the Replace object form for the selected card" : "Opens the plan; pick a card to replace its object" },
    { key: "replace", icon: "⇄", label: "Replace a card", reason: cardReason, run: onReplace },
    { key: "similar", icon: "⧉", label: "Generate similar set", reason: !job ? "Generate a set first" : running ? "Wait for the running job to finish" : null, run: onSimilar, hint: "Loads the same settings with a new shuffled plan; nothing is generated until you confirm" },
  ];
  return (
    <section className="bcard actions" aria-label="Quick actions">
      <h3><span aria-hidden>⚡</span> Quick Actions</h3>
      <div className="qa-list">
        {items.map((it) => (
          <span key={it.key} title={it.reason ?? it.hint} className="qa-wrap">
            <button type="button" className="qbtn" disabled={!!it.reason} onClick={it.run}><span className="qic" aria-hidden>{it.icon}</span>{it.label}</button>
          </span>
        ))}
      </div>
      {job && !picked && !running && <p className="hint">Select a card in the grid to enable the card actions.</p>}
    </section>
  );
}

function NextSteps({ job, jobExports, saved, onReview, onTweak, onExport, onSaved }: {
  job: JobDetail | null; jobExports: ExportRecord[]; saved?: string; onReview: () => void; onTweak: () => void; onExport: () => void; onSaved: (name: string) => void;
}) {
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const cards = job?.variants.flatMap((v) => v.cards) ?? [];
  const tweaked = cards.some((c) => c.regen_count > 0 || c.spec_version > 1 || c.versions.length > 1);
  const steps: { key: string; label: string; done: boolean; run: () => void; off?: boolean; note?: string }[] = [
    { key: "review", label: "Review & pick best set", done: !!job?.approved_variant_id, run: onReview, off: !job, note: job?.approved_variant_id ? `Variant ${vlabel(job.approved_variant_id)}` : undefined },
    { key: "tweak", label: "Tweak cards (optional)", done: tweaked, run: onTweak, off: !job || cards.every((c) => !c.image_url) },
    { key: "export", label: "Export final set (PNG / ZIP)", done: jobExports.length > 0, run: onExport, off: !job, note: jobExports.length ? `${jobExports.length} export(s)` : undefined },
    { key: "save", label: "Save to project / library", done: !!saved, run: () => { setName(`${job?.genre ?? ""} · ${job?.variants.length ?? 1} variant(s) · ${job?.number_of_cards ?? job?.variants[0]?.cards.length ?? 10} cards`); setNaming(true); setErr(null); }, off: !job, note: saved ? `Template “${saved}”` : undefined },
  ];
  async function save() {
    if (!job) return;
    setBusy(true);
    try {
      const r = job.request;
      await api.createTemplate(name.trim(), {
        collection: job.collection, genre: job.genre, number_of_variants: job.variants.length,
        number_of_cards: job.number_of_cards ?? job.variants[0]?.cards.length ?? 10, gold_cards: job.gold_cards ?? r.gold_cards ?? 0,
        output_size: job.output_size ?? r.output_size ?? 1024, wishes: (r.wishes ?? "").slice(0, 500), category_distribution: r.category_distribution,
      });
      onSaved(name.trim());
      setNaming(false);
    } catch (e) { setErr(msg(e, "Could not save the template")); } finally { setBusy(false); }
  }
  return (
    <section className="bcard next" aria-label="Next steps">
      <h3><span aria-hidden>◎</span> Next Steps</h3>
      <ul className="checklist">
        {steps.map((s) => (
          <li key={s.key}>
            <button type="button" className={`ck ${s.done ? "done" : ""}`} disabled={s.off} onClick={s.run} aria-label={`${s.label}${s.done ? " (done)" : ""}`}
              title={s.off ? "Available once a set is generated" : s.done ? "Done" : "Go to this step"}>
              <span className="box" aria-hidden>{s.done ? "✓" : ""}</span>
              <span>{s.label}{s.note && <small>{s.note}</small>}</span>
            </button>
          </li>
        ))}
      </ul>
      {naming && (
        <div className="savebox">
          <label className="lbl" htmlFor="tplname">Template name <small>(settings only, no images or keys)</small></label>
          <div className="savebox-row">
            <input id="tplname" value={name} maxLength={60} autoFocus onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && save()} />
            <button type="button" className="primary small" disabled={!name.trim() || busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
            <button type="button" className="ghost sm" onClick={() => setNaming(false)}>Cancel</button>
          </div>
          {err && <div className="alert err">{err}</div>}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------------------------- plans
function PlansView({ job, preview, selV, onSelectVariant, onEdit }: {
  job: JobDetail | null; preview: JobPreview | null; selV?: JobVariant; onSelectVariant: (id: string) => void; onEdit: (cardId: string) => void;
}) {
  const json = job
    ? { job_id: job.job_id, seed: job.seed, distribution: job.distribution, notes: job.notes, variants: job.variants.map((v) => ({ variant_id: v.variant_id, title: v.title, concept: v.concept, cards: v.cards.map((c) => ({ position: c.position, category: c.category, object_name: c.object_name, object_description: c.object_description, status: c.status, spec_version: c.spec_version })) })) }
    : preview!.plan;
  return (
    <div className="plans">
      {job && selV && (
        <>
          <div className="subnav">
            <div className="seg" role="radiogroup" aria-label="Variant">
              {job.variants.map((v) => <button key={v.variant_id} type="button" role="radio" aria-checked={selV.variant_id === v.variant_id} className={selV.variant_id === v.variant_id ? "on" : ""} onClick={() => onSelectVariant(v.variant_id)}>Variant {vlabel(v.variant_id)}</button>)}
            </div>
            <span className="hint">Change an object with “Replace…”: the card returns to “planned” and can be generated again.</span>
          </div>
          <table className="gridtbl">
            <thead><tr><th>#</th><th>Cat.</th><th>Object</th><th>Description</th><th>Status</th><th /></tr></thead>
            <tbody>
              {selV.cards.map((c) => (
                <tr key={c.card_id}>
                  <td>{c.position}</td><td className={c.category === 5 ? "goldtxt" : ""}>{catText(c.category)}</td><td><strong>{c.object_name}</strong></td><td className="muted">{c.object_description}</td>
                  <td>{c.status}{c.spec_version > 1 ? ` · edited v${c.spec_version}` : ""}</td>
                  <td><button type="button" className="ghost sm" disabled={job.active} title={job.active ? "Wait for the running job to finish" : "Replace this card's object"} onClick={() => onEdit(c.card_id)}>Replace…</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <details open><summary>Plan JSON</summary><pre className="code">{JSON.stringify(json, null, 2)}</pre></details>
    </div>
  );
}

// -------------------------------------------------------------------------------- card detail
function CardDetail({ job, card, variant, perImage, onJob, onError, onClose, onCompare, replaceToken, regenToken, isFav, onStarVersion }: {
  job: JobDetail; card: JobCard; variant: JobVariant; perImage: number;
  onJob: (d: JobDetail) => void; onError: (m: string | null) => void; onClose: () => void; onCompare: () => void;
  replaceToken: number; regenToken: number;
  isFav: (artifactId: string) => boolean;
  onStarVersion: (c: JobCard, variantId: string, v: { artifact_id: string; image_url: string; version: number }) => void;
}) {
  const [note, setNote] = useState("");
  const [armed, setArmed] = useState(false);
  const [replace, setReplace] = useState(false);
  const [rName, setRName] = useState(card.object_name);
  const [rDesc, setRDesc] = useState(card.object_description);
  const [viewV, setViewV] = useState<number | null>(null);
  const [showPrompt, setShowPrompt] = useState(false);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const paid = job.request.provider !== "mock" && perImage > 0;
  const hasImage = !!card.image_url;
  const locked = job.active || busy;
  const shownVersion = viewV != null ? card.versions.find((v) => v.version === viewV) : undefined;
  const img = shownVersion?.image_url ?? card.image_url;
  const curVersion = card.versions.find((x) => x.is_current);
  const starTarget = shownVersion ?? (curVersion ?? null);
  const noRegenLeft = hasImage && card.regen_remaining <= 0;
  const canRegen = !locked && !noRegenLeft && card.status !== "queued" && card.status !== "generating";

  useEffect(() => { setViewV(null); setArmed(false); setReplace(false); setRName(card.object_name); setRDesc(card.object_description); setNote(""); }, [card.card_id, card.spec_version, card.object_name, card.object_description]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  async function run<T extends JobDetail>(fn: () => Promise<T>) {
    setBusy(true);
    try { onJob(await fn()); onError(null); } catch (e) { onError(msg(e, "Action failed")); } finally { setBusy(false); }
  }
  function regenerate() {
    if (paid && !armed) { setArmed(true); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setArmed(false), 10000); return; }
    setArmed(false);
    run(() => api.regenerateCard(job.job_id, card.card_id, true, note));
  }

  // quick-action requests from the bottom row (a changed token = one request)
  const regenRef = useRef(regenerate); regenRef.current = regenerate;
  const canRegenRef = useRef(canRegen); canRegenRef.current = canRegen;
  const lastRegen = useRef(regenToken); const lastRep = useRef(replaceToken);
  useEffect(() => { if (regenToken !== lastRegen.current) { lastRegen.current = regenToken; if (canRegenRef.current) regenRef.current(); } }, [regenToken]);
  useEffect(() => { if (replaceToken !== lastRep.current) { lastRep.current = replaceToken; setReplace(true); } }, [replaceToken]);

  const label = !hasImage ? "Generate this card" : "Regenerate";

  return (
    <div className="pickdetail card-box">
      <div className="pd-img">
        {img ? <img src={img} alt={card.object_name} /> : <div className="noimg">{card.status}</div>}
        {card.versions.length > 0 && (
          <div className="vers" aria-label="Versions">
            {card.versions.map((v) => (
              <button key={v.version} type="button" className={`${(viewV ?? card.versions.find((x) => x.is_current)?.version) === v.version ? "on" : ""}`} onClick={() => setViewV(v.version)}
                title={v.note ?? `version ${v.version}`}><img src={v.image_url} alt={`v${v.version}`} /><small>v{v.version}{v.is_current ? " ●" : ""}</small></button>
            ))}
          </div>
        )}
      </div>
      <div className="pd-body">
        <div className="pd-head">
          <h3>{card.object_name}</h3>
          <button type="button" className="ghost sm" onClick={onClose} aria-label="Close card detail">Close ×</button>
        </div>
        <p className="hint">{card.object_description}</p>
        <div className="chips">
          <span className="chip">Variant {vlabel(variant.variant_id)} · #{card.position}</span><span className={`chip ${card.category === 5 ? "gold" : ""}`}>{card.category === 5 ? "★ Gold" : `Cat. ${card.category}`}</span>
          <QABadge status={card.qa_status} /><ReviewBadge review={card.art_review} />
          <span className="chip">refs: {card.reference_selection ?? "none"}</span>
          {starTarget && (
            <button type="button" className={`chip viewprompt ${showPrompt ? "on" : ""}`} aria-expanded={showPrompt} onClick={() => setShowPrompt(!showPrompt)}>
              {showPrompt ? "Hide prompt" : "View Prompt"}
            </button>
          )}
          {starTarget && (
            <button type="button" className={`chip starchip ${isFav(starTarget.artifact_id) ? "on" : ""}`} aria-pressed={isFav(starTarget.artifact_id)}
              onClick={() => onStarVersion(card, variant.variant_id, starTarget)}>
              {isFav(starTarget.artifact_id) ? "★ In My Selection" : "☆ Add this image to My Selection"}
            </button>
          )}
        </div>
        {showPrompt && starTarget && <ImagePrompt artifactId={starTarget.artifact_id} />}
        {card.error && <div className="alert warn">{card.error}</div>}
        {shownVersion?.note && <p className="hint">Version note: {shownVersion.note}</p>}
        {card.qa && <details><summary>Technical QA checks ({card.qa.checks.filter((c) => c.status !== "pass").length} issue(s))</summary><CheckList checks={card.qa.checks} /></details>}

        {hasImage && (
          <div className="rvbtns" role="group" aria-label="Art review">
            <button type="button" className="ghost" disabled={locked || card.art_review === "approved"} onClick={() => run(() => api.reviewCard(job.job_id, card.card_id, "approved", card.review_note ?? ""))}>✓ Approve card</button>
            <button type="button" className="ghost" disabled={locked || card.art_review === "rejected"} onClick={() => run(() => api.reviewCard(job.job_id, card.card_id, "rejected", note))}>✕ Reject card</button>
            <button type="button" className="ghost" disabled={locked || card.art_review === "pending"} onClick={() => run(() => api.reviewCard(job.job_id, card.card_id, "pending"))}>Reset</button>
          </div>
        )}

        <label className="lbl" htmlFor="rnote">{hasImage ? "Repair note (what to fix)" : "Note"} <small>(optional, ≤ 300)</small></label>
        <textarea id="rnote" rows={2} maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. no text on the objects; simpler background" />
        <div className="rvbtns">
          <button type="button" className={`primary small ${armed ? "armed" : ""}`} disabled={!canRegen} onClick={regenerate}>
            {armed ? `Confirm & spend ≈ $${perImage.toFixed(3)}` : `${label}${paid ? ` · ≈ $${perImage.toFixed(3)}` : ""}`}
          </button>
          {armed && <button type="button" className="ghost" onClick={() => setArmed(false)}>Cancel</button>}
          <button type="button" className="ghost" disabled={locked} onClick={() => setReplace(!replace)}>Replace object…</button>
          {hasImage && <button type="button" className="ghost" onClick={onCompare}>Compare with reference</button>}
        </div>
        {hasImage && <p className="hint">{card.regen_remaining} of {job.max_regenerations_per_card} regenerations left for this card. Earlier versions are kept.</p>}
        {noRegenLeft && <div className="alert warn">Regeneration limit reached for this card (MAX_REGENERATIONS_PER_CARD).</div>}

        {replace && (
          <div className="replace">
            <input value={rName} maxLength={80} onChange={(e) => setRName(e.target.value)} aria-label="New object name" />
            <input value={rDesc} maxLength={300} onChange={(e) => setRDesc(e.target.value)} aria-label="New object description" />
            <button type="button" className="primary small" disabled={locked || !rName.trim()} onClick={() => run(() => api.replaceCard(job.job_id, card.card_id, rName, rDesc)).then(() => setReplace(false))}>Replace (card returns to planned)</button>
          </div>
        )}
        <div className="rvbtns">
          {card.artifact_id && <a className="ghost link" href={`/api/artifacts/${card.artifact_id}`} target="_blank" rel="noreferrer">Provenance JSON</a>}
          {img && <a className="ghost link" href={img} download={`${card.card_id}.png`}>Download PNG</a>}
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------ compare variants
function CompareVariants({ job, onPick }: { job: JobDetail; onPick: (cardId: string) => void }) {
  const nPos = Math.max(job.number_of_cards ?? 0, ...job.variants.map((v) => v.cards.length));
  const positions = Array.from({ length: nPos }, (_, i) => i + 1);
  const catOf = (pos: number) => job.variants[0]?.cards.find((c) => c.position === pos)?.category;
  return (
    <>
      <p className="hint">Same scale, same position in every variant: each column is one slot, so you can compare looks straight down. Click a thumbnail to open the card.</p>
      <table className="gridtbl varcmp">
        <thead>
          <tr><th scope="col">Variant</th>{positions.map((pos) => <th key={pos} scope="col" className="poscol">#{pos}<br /><small>{catOf(pos) === 5 ? "★ Gold" : `Cat ${catOf(pos)}`}</small></th>)}</tr>
        </thead>
        <tbody>
          {job.variants.map((v) => (
            <tr key={v.variant_id}>
              <th scope="row"><strong>{vlabel(v.variant_id)}</strong><br /><small>{v.title}</small></th>
              {positions.map((pos) => {
                const c = v.cards.find((x) => x.position === pos);
                return (
                  <td key={pos}>
                    {c?.image_url
                      ? <button type="button" onClick={() => onPick(c.card_id)} title={c.object_name}><img src={c.image_url} alt={c.object_name} /><small>{c.object_name}</small></button>
                      : <small className="hint">{c?.object_name ?? "–"}<br />{c?.status}</small>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

// ------------------------------------------------------------------------------------- QA panel
function QAPanel({ job }: { job: JobDetail }) {
  const [rep, setRep] = useState<QAReport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.qaReport(job.job_id).then(setRep).catch((e) => setErr(msg(e, "Could not load QA"))); }, [job.job_id, job.counts.done, job.updated_at]);
  async function rerun() {
    setBusy(true);
    try { setRep(await api.runQa(job.job_id)); setErr(null); } catch (e) { setErr(msg(e, "QA failed")); } finally { setBusy(false); }
  }
  if (!rep) return <div className="empty">{err ?? "Loading QA report…"}</div>;
  return (
    <div className="qapanel">
      <div className="jobhead">
        <strong>Technical QA</strong>
        <span className="chip ok">{rep.technical.passed} passed</span><span className="chip warn">{rep.technical.flagged} flagged</span>
        <span className="chip" style={{ color: "var(--err)" }}>{rep.technical.failed} failed</span>
        <button type="button" className="ghost sm" disabled={busy || job.active} onClick={rerun}>{busy ? "Running…" : "Re-run QA"}</button>
      </div>
      <p className="hint">{rep.limitations}</p>
      {err && <div className="alert err">{err}</div>}
      {Object.values(rep.variants).map((v) => (
        <details key={v.variant_id} open={v.status !== "passed"}>
          <summary>Variant {vlabel(v.variant_id)} — set QA <QABadge status={v.status} /> · {v.generated}/{v.total} generated</summary>
          <CheckList checks={v.checks} />
        </details>
      ))}
      <details><summary>Per-card checks ({Object.keys(rep.cards).length})</summary>
        {Object.entries(rep.cards).map(([id, c]) => (
          <details key={id} className="sub"><summary>{c.object_name} · {c.category === 5 ? "★ Gold" : `Cat ${c.category}`} <QABadge status={c.status} /> <ReviewBadge review={c.art_review} /></summary><CheckList checks={c.checks} /></details>
        ))}
      </details>
      <CheckList checks={rep.job_checks} />
    </div>
  );
}
