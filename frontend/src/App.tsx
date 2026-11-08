import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api/client";
import { CategoryDistribution, type DistCounts, type DistMode } from "./components/CategoryDistribution";
import { ModelSettings, ProviderAvailability, keyOf } from "./components/ModelSettings";
import { ReferenceUploader } from "./components/ReferenceUploader";
import { HistoryView } from "./components/HistoryView";
import { ResultPanel } from "./components/ResultPanel";
import { SetPanel } from "./components/SetPanel";
import { SettingsView } from "./components/SettingsView";
import { SetupWizard, useFirstRun } from "./components/SetupWizard";
import { TemplatesView } from "./components/TemplatesView";
import type {
  Artifact, Budget, GenerateRequest, GenreInfo, JobDetail, JobEvent, JobPreview, JobRequestPayload,
  JobSummary, ModelsResponse, PreviewResponse, RefMeta,
} from "./types/api";

const MAX_WISHES = 500;
const CATEGORIES: { n: 1 | 2 | 3 | 4; label: string }[] = [
  { n: 1, label: "Cat. 1 — floating, simple background" },
  { n: 2, label: "Cat. 2 — on a surface" },
  { n: 3, label: "Cat. 3 — realistic surface" },
  { n: 4, label: "Cat. 4 — in an environment" },
];
type Mode = "set" | "single";
type View = "generate" | "history" | "templates" | "settings";

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? `${e.message}${e.code ? ` [${e.code}]` : ""}` : fallback);

export default function App() {
  const [mode, setMode] = useState<Mode>("set");
  const [view, setView] = useState<View>("generate");
  // First-run setup guide / "Connect providers" dialog (credentials are handled by the backend; never stored in the browser).
  const [setupOpen, setSetupOpen] = useState(false);
  const { firstRun, refresh: refreshFirstRun } = useFirstRun();
  useEffect(() => { if (firstRun) setSetupOpen(true); }, [firstRun]);
  const [advOpen, setAdvOpen] = useState(false);
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refs, setRefs] = useState<RefMeta[]>([]);
  const [selectedRefs, setSelectedRefs] = useState<string[]>([]);
  const [policy, setPolicy] = useState(false);

  const [collection, setCollection] = useState("Movie Collection");
  const [genre, setGenre] = useState("Science Fiction");
  const [genres, setGenres] = useState<GenreInfo[]>([]);
  const [objectName, setObjectName] = useState("");
  const [objectDesc, setObjectDesc] = useState("");
  const [category, setCategory] = useState<1 | 2 | 3 | 4>(2);
  const [wishes, setWishes] = useState("");
  const [modelKey, setModelKey] = useState("");
  const [aspect, setAspect] = useState("1:1");
  const [distMode, setDistMode] = useState<DistMode>("auto_balanced");
  const [customDist, setCustomDist] = useState<DistCounts | null>(null);
  const [variants, setVariants] = useState(1);
  const [scope, setScope] = useState<"pilot" | "full">("pilot");
  const [seed, setSeed] = useState<number | null>(null);

  // single-card slice
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [history, setHistory] = useState<Artifact[]>([]);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);

  // set generation
  const [jobPreview, setJobPreview] = useState<JobPreview | null>(null);
  const [jobPreviewError, setJobPreviewError] = useState<string | null>(null);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [jobError, setJobError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [continueArmed, setContinueArmed] = useState(false);

  const [armed, setArmed] = useState(false);
  const [checking, setChecking] = useState(false);
  const armTimer = useRef<number | undefined>(undefined);
  const contTimer = useRef<number | undefined>(undefined);

  const loadModels = useCallback(async () => {
    try {
      const m = await api.models();
      setModels(m);
      setLoadError(null);
      setModelKey((cur) => {
        if (cur) return cur;
        const def = m.models.find((x) => x.provider_id === m.defaults.provider && x.model_id === m.defaults.model && x.selectable)
          ?? m.models.find((x) => x.selectable);
        return def ? keyOf(def) : "";
      });
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : "Failed to load models");
    }
  }, []);
  const loadRefs = useCallback(async () => { try { setRefs(await api.references()); } catch { /* shown via loadError */ } }, []);
  const loadHistory = useCallback(async () => {
    try {
      const h = await api.artifacts();
      setHistory(h);
      setArtifact((cur) => cur ?? h[0] ?? null);
    } catch { /* ignore */ }
  }, []);
  const loadJobs = useCallback(async () => { try { setJobs(await api.jobs()); } catch { /* ignore */ } }, []);
  const openJob = useCallback(async (id: string) => {
    try {
      const [d, ev] = await Promise.all([api.job(id), api.jobEvents(id)]);
      setJob(d);
      setEvents(ev);
      setJobError(null);
    } catch (e) { setJobError(errText(e, "Failed to load job")); }
  }, []);

  useEffect(() => {
    loadModels(); loadRefs(); loadHistory();
    api.genres().then(setGenres).catch(() => undefined);
    api.jobs().then((js) => { setJobs(js); if (js[0]) openJob(js[0].job_id); }).catch(() => undefined); // persists across refresh
  }, [loadModels, loadRefs, loadHistory, openJob]);

  // poll the running job
  useEffect(() => {
    if (!job?.active) return;
    const id = job.job_id;
    const t = window.setInterval(async () => {
      try {
        const [d, ev] = await Promise.all([api.job(id), api.jobEvents(id)]);
        setJob(d);
        setEvents(ev);
        if (!d.active) { loadModels(); loadJobs(); }
      } catch { /* transient */ }
    }, 1500);
    return () => window.clearInterval(t);
  }, [job?.job_id, job?.active, loadModels, loadJobs]);

  const defaultDist = useMemo<DistCounts>(() => {
    const d = models?.defaults.category_distribution ?? {};
    return { "1": d["1"] ?? 2, "2": d["2"] ?? 2, "3": d["3"] ?? 3, "4": d["4"] ?? 3 };
  }, [models]);
  const model = useMemo(() => models?.models.find((m) => keyOf(m) === modelKey), [models, modelKey]);
  const refIds = useMemo(() => selectedRefs.filter((id) => refs.some((r) => r.ref_id === id)), [selectedRefs, refs]);
  const maxVariants = genres.find((g) => g.genre === genre)?.max_variants ?? 5;

  const perImageSel = model?.est_cost_per_image_usd ?? 0;
  const perImageJob = useMemo(() => {
    const m = models?.models.find((x) => x.provider_id === job?.request.provider && x.model_id === job?.request.model);
    return m?.est_cost_per_image_usd ?? 0;
  }, [models, job?.request.provider, job?.request.model]);

  const applyPreset = useCallback((pl: Record<string, unknown>) => {
    setMode("set");
    if (typeof pl.genre === "string") setGenre(pl.genre);
    if (typeof pl.number_of_variants === "number") setVariants(pl.number_of_variants);
    if (pl.scope === "pilot" || pl.scope === "full") setScope(pl.scope);
    if (typeof pl.wishes === "string") setWishes(pl.wishes);
    const d = pl.category_distribution as { mode?: string; counts?: Record<string, number> } | undefined;
    if (d?.mode === "custom" && d.counts) {
      setDistMode("custom");
      setCustomDist({ "1": d.counts["1"] ?? 1, "2": d.counts["2"] ?? 1, "3": d.counts["3"] ?? 1, "4": d.counts["4"] ?? 1 });
    } else setDistMode("auto_balanced");
    setSeed(null);
    setView("generate");
  }, []);

  const request = useCallback(
    (confirmed: boolean, policyOverride?: boolean): GenerateRequest => ({
      collection: collection.trim() || "Movie Collection",
      genre: genre.trim(),
      object_name: objectName.trim(),
      object_description: objectDesc.trim(),
      category,
      wishes,
      provider: model?.provider_id ?? "",
      model: model?.model_id ?? "",
      reference_ids: refIds,
      reference_policy_accepted: policyOverride ?? policy,
      aspect_ratio: aspect,
      confirmed,
    }),
    [collection, genre, objectName, objectDesc, category, wishes, model, refIds, policy, aspect],
  );

  const jobRequest = useCallback(
    (confirmed: boolean, policyOverride?: boolean): JobRequestPayload => ({
      collection: collection.trim() || "Movie Collection",
      genre: genre.trim(),
      wishes,
      number_of_variants: variants,
      category_distribution: distMode === "auto_balanced"
        ? { mode: "auto_balanced" }
        : { mode: "custom", counts: { ...(customDist ?? defaultDist) } },
      provider: model?.provider_id ?? "",
      model: model?.model_id ?? "",
      reference_ids: refIds,
      reference_policy_accepted: policyOverride ?? policy,
      aspect_ratio: aspect,
      scope,
      seed,
      confirmed,
    }),
    [collection, genre, wishes, variants, distMode, customDist, defaultDist, model, refIds, policy, aspect, scope, seed],
  );

  const onBatch = useCallback(async (confirmed: boolean) => {
    await api.batch(jobRequest(confirmed), ["Science Fiction", "Detective", "Fantasy"]);
    loadJobs();
    loadModels();
  }, [jobRequest, loadJobs, loadModels]);

  /** "Generate similar set": reload the job's settings into the form with a new shuffled seed. Nothing is generated. */
  const startSimilar = useCallback((j: JobDetail) => {
    const r = j.request;
    setMode("set");
    setCollection(j.collection);
    setGenre(j.genre);
    setVariants(j.variants.length);
    setScope(r.scope);
    setWishes(r.wishes ?? "");
    setAspect(r.aspect_ratio);
    const key = `${r.provider}/${r.model}`;
    if (models?.models.some((m) => keyOf(m) === key && m.selectable)) setModelKey(key);
    const d = r.category_distribution;
    if (d.mode === "custom") {
      setDistMode("custom");
      setCustomDist({ "1": d.counts["1"] ?? 1, "2": d.counts["2"] ?? 1, "3": d.counts["3"] ?? 1, "4": d.counts["4"] ?? 1 });
    } else setDistMode("auto_balanced");
    setSeed(Math.floor(Math.random() * 2 ** 31));
    setJob(null); setEvents([]); setJobError(null);
    setView("generate");
  }, [models]);

  const singleValid = !!genre.trim() && !!objectName.trim() && !!model;
  const setValid = !!genre.trim() && !!model;
  const formValid = mode === "single" ? singleValid : setValid;

  // Debounced FREE previews (never call a provider).
  useEffect(() => {
    if (mode !== "single") return;
    if (!singleValid) { setPreview(null); setPreviewError(null); return; }
    const t = window.setTimeout(async () => {
      try { setPreview(await api.preview(request(false, true))); setPreviewError(null); }
      catch (e) { setPreview(null); setPreviewError(e instanceof ApiError ? e.message : "Preview failed"); }
    }, 350);
    return () => window.clearTimeout(t);
  }, [mode, singleValid, request, models?.budget.spent_estimated_usd]);

  useEffect(() => {
    if (mode !== "set") return;
    if (!setValid) { setJobPreview(null); setJobPreviewError(null); return; }
    const t = window.setTimeout(async () => {
      try { setJobPreview(await api.jobsPreview(jobRequest(false, true))); setJobPreviewError(null); }
      catch (e) { setJobPreview(null); setJobPreviewError(e instanceof ApiError ? e.message : "Preview failed"); }
    }, 350);
    return () => window.clearTimeout(t);
  }, [mode, setValid, jobRequest, models?.budget.spent_estimated_usd]);

  useEffect(() => () => { window.clearTimeout(armTimer.current); window.clearTimeout(contTimer.current); }, []);
  useEffect(() => { setArmed(false); }, [mode, modelKey, objectName, genre, category, wishes, aspect, refIds.length, variants, scope, distMode, customDist]);

  const est = mode === "single" ? (preview?.estimated_cost_usd ?? 0) : (jobPreview?.estimated_cost_usd ?? 0);
  const paid = est > 0;
  const needsPolicy = refIds.length > 0 && model?.provider_id !== "mock" && !policy;
  const policyNeeded = refIds.length > 0 && model?.provider_id !== "mock";
  const previewBlock = mode === "single" ? preview?.blocked?.message : jobPreview?.blocked?.message;
  const blockedReason = previewBlock ?? (mode === "single" ? previewError : jobPreviewError) ?? (needsPolicy ? "Confirm the reference-image policy checkbox." : null);
  const havePreview = mode === "single" ? !!preview : !!jobPreview;
  const busy = generating || starting || !!job?.active;
  const canGenerate = formValid && !busy && !blockedReason && havePreview;

  function arm() {
    setArmed(true);
    window.clearTimeout(armTimer.current);
    armTimer.current = window.setTimeout(() => setArmed(false), 10000);
  }

  async function onGenerate() {
    if (!canGenerate) return;
    if (paid && !armed) { arm(); return; }
    setArmed(false);
    if (mode === "single") {
      setGenerating(true);
      setGenError(null);
      try {
        const a = await api.generate(request(true));
        setArtifact(a);
        setHistory((h) => [a, ...h.filter((x) => x.artifact_id !== a.artifact_id)].slice(0, 24));
        loadModels();
      } catch (e) { setGenError(errText(e, "Generation failed")); loadModels(); }
      finally { setGenerating(false); }
    } else {
      setStarting(true);
      setJobError(null);
      try {
        const d = await api.createJob(jobRequest(true));
        setJob(d);
        setEvents(await api.jobEvents(d.job_id));
        loadJobs();
      } catch (e) { setJobError(errText(e, "Could not start the job")); loadModels(); }
      finally { setStarting(false); }
    }
  }

  async function onContinue(retryFailed: boolean) {
    if (!job) return;
    const needsConfirm = job.request.provider !== "mock";
    if (needsConfirm && !continueArmed) {
      setContinueArmed(true);
      window.clearTimeout(contTimer.current);
      contTimer.current = window.setTimeout(() => setContinueArmed(false), 10000);
      return;
    }
    setContinueArmed(false);
    try { setJob(await api.continueJob(job.job_id, true, retryFailed)); setJobError(null); }
    catch (e) { setJobError(errText(e, "Could not continue")); }
  }

  async function onCancelJob() {
    if (!job) return;
    try { setJob(await api.cancelJob(job.job_id)); } catch (e) { setJobError(errText(e, "Could not cancel")); }
  }

  async function onCheck() {
    setChecking(true);
    try { await api.checkAvailability(); } catch { /* error surfaces via availability.error */ }
    await loadModels();
    setChecking(false);
  }

  const buttonLabel: { main: string; sub?: string } = (() => {
    if (generating || starting) return { main: mode === "single" ? "Generating…" : "Starting…" };
    if (job?.active && mode === "set") return { main: "Job running…" };
    if (armed) return { main: `Confirm & spend ≈ $${est.toFixed(3)}`, sub: "Click again to start; this uses the paid API" };
    const cost = paid ? `≈ $${est.toFixed(3)} (local estimate)` : "free";
    if (mode === "single") return { main: "Generate card", sub: cost };
    const n = jobPreview?.images_in_scope ?? 0;
    const full = paid && jobPreview && jobPreview.images_in_scope < jobPreview.images_total ? ` · full set ≈ $${jobPreview.estimated_cost_full_usd.toFixed(2)}` : "";
    return { main: `Generate Sets (${variants} variant${variants > 1 ? "s" : ""})`, sub: `${n} image${n === 1 ? "" : "s"} · ${cost}${full}` };
  })();

  return (
    <div className="app">
      <Header budget={models?.budget} view={view} onView={setView} onConnect={() => setSetupOpen(true)} />
      <SetupWizard open={setupOpen} onClose={() => setSetupOpen(false)}
        onConnected={() => { loadModels(); refreshFirstRun(); }} />
      {loadError && <div className="alert err top" role="alert">{loadError}</div>}
      {view !== "generate" && (
        <div className="page">
          {view === "history" && (
            <HistoryView jobs={jobs} perImage={perImageSel} variants={variants} scope={scope} busy={!!job?.active || starting}
              onOpen={(id) => { openJob(id); setMode("set"); setView("generate"); }} onBatch={onBatch} onRefresh={loadJobs} />
          )}
          {view === "templates" && (
            <TemplatesView onApply={applyPreset} current={() => ({
              genre, number_of_variants: variants, scope, wishes: wishes.slice(0, 500),
              category_distribution: distMode === "auto_balanced" ? { mode: "auto_balanced" } : { mode: "custom", counts: { ...(customDist ?? defaultDist) } },
            })} />
          )}
          {view === "settings" && <SettingsView onOpenSetup={() => setSetupOpen(true)} />}
        </div>
      )}
      <main hidden={view !== "generate"}>
        <aside className="left" aria-label="Request settings">
          <div className="left-scroll">
            {mode === "single" && (
              <div className="modebar" role="status">
                <span><strong>Single-card test mode</strong> (diagnostic: one object, one provider call)</span>
                <button type="button" className="ghost sm" onClick={() => setMode("set")}>Back to card sets</button>
              </div>
            )}

            <Section n={1} title="Set & Theme">
              <div className="row"><label className="lbl" htmlFor="coll">Collection</label>
                <input id="coll" value={collection} onChange={(e) => setCollection(e.target.value)} maxLength={80} /></div>
              {mode === "set" ? (
                <>
                  <div className="row"><label className="lbl" htmlFor="genre">Genre / Set</label>
                    <select id="genre" value={genre} onChange={(e) => { setGenre(e.target.value); setSeed(null); setVariants((v) => Math.min(v, genres.find((g) => g.genre === e.target.value)?.max_variants ?? 5)); }}>
                      {(genres.length ? genres.map((g) => g.genre) : [genre]).map((g) => <option key={g} value={g}>{g}</option>)}
                    </select></div>
                  <div className="row two">
                    <div><span className="lbl">Number of cards</span><input value="10 (fixed)" disabled aria-label="Cards per set" /></div>
                    <div><label className="lbl" htmlFor="variants">Variants</label>
                      <select id="variants" value={variants} onChange={(e) => { setVariants(Number(e.target.value)); setSeed(null); }}>
                        {Array.from({ length: maxVariants }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n}</option>)}
                      </select></div>
                  </div>
                  <div className="field">
                    <span className="lbl" id="scope-lbl">How much to generate</span>
                    <div className="seg" role="radiogroup" aria-labelledby="scope-lbl">
                      <button type="button" role="radio" aria-checked={scope === "pilot"} className={scope === "pilot" ? "on" : ""} onClick={() => setScope("pilot")}>Test run · 4 cards</button>
                      <button type="button" role="radio" aria-checked={scope === "full"} className={scope === "full" ? "on" : ""} onClick={() => setScope("full")}>Full set · 10 cards</button>
                    </div>
                    <p className="hint">{scope === "pilot"
                      ? "One card per category in each variant. Cheap check of the look; you can continue to the full set afterwards."
                      : "All 10 cards in every variant."}</p>
                  </div>
                </>
              ) : (
                <>
                  <div className="row"><label className="lbl" htmlFor="genre">Genre / Set</label>
                    <input id="genre" list="genres" value={genre} onChange={(e) => setGenre(e.target.value)} maxLength={80} />
                    <datalist id="genres"><option value="Science Fiction" /><option value="Detective" /><option value="Fantasy" /></datalist></div>
                  <div className="row"><label className="lbl" htmlFor="obj">Object</label>
                    <input id="obj" placeholder="e.g. Retro Space Helmet" value={objectName} onChange={(e) => setObjectName(e.target.value)} maxLength={80} /></div>
                  <div className="row"><label className="lbl" htmlFor="objd">Details <small>(optional)</small></label>
                    <input id="objd" placeholder="e.g. glass visor, brass rivets" value={objectDesc} onChange={(e) => setObjectDesc(e.target.value)} maxLength={300} /></div>
                </>
              )}
            </Section>

            <Section n={2} title="Rules, Style & Brief">
              {mode === "single" ? (
                <>
                  <span className="lbl">Card category</span>
                  <div className="cats" role="radiogroup" aria-label="Card category">
                    {CATEGORIES.map((c) => (
                      <label key={c.n} className={`cat ${category === c.n ? "on" : ""}`}>
                        <input type="radio" name="cat" checked={category === c.n} onChange={() => setCategory(c.n)} />{c.label}
                      </label>
                    ))}
                    <label className="cat locked" title="Golden cards are locked OFF for the Movie demo">
                      <input type="radio" disabled />Golden cards (Cat. 5) — locked OFF
                    </label>
                  </div>
                </>
              ) : (
                <CategoryDistribution
                  mode={distMode}
                  counts={customDist ?? defaultDist}
                  defaults={defaultDist}
                  appliedToSets
                  onMode={(m) => { setDistMode(m); setSeed(null); if (m === "custom") setCustomDist((c) => c ?? { ...defaultDist }); }}
                  onCount={(cat, v) => setCustomDist((c) => ({ ...(c ?? defaultDist), [cat]: Math.max(0, Math.min(10, v)) }))}
                />
              )}
              <div className="field">
                <label className="lbl" htmlFor="artstyle">Art style</label>
                <select id="artstyle" disabled title="One style bible in this version: Reference Match v1 (draft)"><option>Reference Match v1 (draft)</option></select>
              </div>
              <div className="field">
                <label className="lbl" htmlFor="wishes">Producer's free wishes <small>(optional)</small></label>
                <div className="ta-wrap">
                  <textarea id="wishes" rows={3} maxLength={MAX_WISHES} value={wishes} onChange={(e) => setWishes(e.target.value)}
                    placeholder="Desired atmosphere, objects, era, forbidden motifs…" />
                  <span className="counter" aria-live="off">{wishes.length} / {MAX_WISHES}</span>
                </div>
              </div>
              {mode === "single" && preview?.warnings.map((w) => <div key={w} className="alert warn">{w}</div>)}
              <div className="note"><span aria-hidden>ⓘ</span> System rules stay fixed (style, categories, artwork-only). Wishes steer the image prompts; the rules planner picks objects from its curated pool.</div>
            </Section>

            <Section n={3} title="References">
              <ReferenceUploader refs={refs} selected={refIds} maxRefs={models?.limits.max_refs_per_request ?? 5}
                refTypes={models?.limits.ref_types ?? ["style"]} policyAccepted={policy} policyNeeded={policyNeeded}
                onChange={loadRefs} onSelect={setSelectedRefs} onPolicy={setPolicy} />
            </Section>

            <Section n={4} title="Generation Setup">
              {models ? (
                <ModelSettings models={models.models} value={modelKey} onChange={setModelKey} aspect={aspect} onAspect={setAspect} />
              ) : <p className="hint">Loading models…</p>}
            </Section>

            <details className="adv main" open={advOpen || mode === "single"} onToggle={(e) => setAdvOpen((e.currentTarget as HTMLDetailsElement).open)}>
              <summary>Advanced settings</summary>
              <div className="adv-body">
                <div className="field">
                  <span className="lbl" id="mode-lbl">Mode</span>
                  <div className="seg" role="radiogroup" aria-labelledby="mode-lbl">
                    <button type="button" role="radio" aria-checked={mode === "set"} className={mode === "set" ? "on" : ""} onClick={() => setMode("set")}>Card sets</button>
                    <button type="button" role="radio" aria-checked={mode === "single"} className={mode === "single" ? "on" : ""} onClick={() => setMode("single")}>Single card (test)</button>
                  </div>
                </div>
                {mode === "set" && (
                  <div className="field">
                    <span className="lbl">Plan shuffle</span>
                    <button type="button" className="ghost" onClick={() => setSeed(Math.floor(Math.random() * 2 ** 31))}>Shuffle plan{seed != null ? ` (seed ${seed})` : ""}</button>
                    <p className="hint">Picks a different set of objects from the curated pools. Changing genre or variants resets the seed.</p>
                  </div>
                )}
                <p className="hint">Automatic retries: <strong>{models?.budget.max_card_retries ?? 0}</strong> (off). Sequential generation, circuit breaker after 2 consecutive failures.</p>
                {models && <ProviderAvailability models={models.models} checking={checking} checkedAt={models.availability.checked_at} availabilityError={models.availability.error} onCheck={onCheck} />}
                <button type="button" className="ghost" onClick={() => setView("settings")}>Open Settings (read-only)</button>
              </div>
            </details>
          </div>

          <div className="cta">
            {blockedReason && formValid && <div className="alert warn compact">{blockedReason}</div>}
            {mode === "set" && jobPreview?.warnings.map((w) => <div key={w} className="alert warn compact">{w}</div>)}
            <div className="cta-row">
              <button type="button" className={`primary big ${armed ? "armed" : ""}`} onClick={onGenerate} disabled={!canGenerate}>
                <span>{buttonLabel.main}</span>{buttonLabel.sub && <small>{buttonLabel.sub}</small>}
              </button>
              {armed && <button type="button" className="ghost" onClick={() => setArmed(false)}>Cancel</button>}
            </div>
            {!formValid && <p className="hint">{mode === "single" ? "Enter a genre and an object, and pick an available model." : "Pick an available model."}</p>}
          </div>
        </aside>

        {mode === "set" ? (
          <SetPanel job={job} preview={jobPreview} previewError={jobPreviewError} events={events} jobs={jobs}
            models={models?.models ?? []} form={{ collection, genre, variants, provider: model?.provider_id ?? "", model: model?.model_id ?? "", aspect }}
            error={jobError} starting={starting} perImage={perImageJob} onJob={(d) => { setJob(d); loadJobs(); if (!d.active) loadModels(); }}
            onError={setJobError} onPickJob={openJob} onContinue={onContinue}
            continueArmed={continueArmed} onCancelContinue={() => setContinueArmed(false)} onCancelJob={onCancelJob}
            onNew={() => { setJob(null); setEvents([]); setJobError(null); }} onSimilar={startSimilar} onOpenHistory={() => setView("history")} />
        ) : (
          <ResultPanel artifact={artifact} history={history} onPick={setArtifact} preview={preview} generating={generating} error={genError} />
        )}
      </main>
    </div>
  );
}

const NAV: { v: View; label: string; icon: string }[] = [
  { v: "generate", label: "Generate", icon: "✦" },
  { v: "history", label: "History", icon: "◷" },
  { v: "templates", label: "Templates", icon: "❏" },
  { v: "settings", label: "Settings", icon: "⚙" },
];

function Header({ budget, view, onView, onConnect }: { budget?: Budget; view: View; onView: (v: View) => void; onConnect: () => void }) {
  return (
    <header className="top">
      <div className="brand">
        <span className="logo" aria-hidden>
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2.5" y="6" width="19" height="12" rx="5" /><path d="M8 10v4M6 12h4" /><circle cx="15.5" cy="11" r=".8" fill="currentColor" /><circle cx="17.7" cy="13" r=".8" fill="currentColor" />
          </svg>
        </span>
        <strong>AI Collection Studio</strong>
        <span className="tag">Game Art Production for Collectible Card Sets</span>
      </div>
      <nav aria-label="Main">
        {NAV.map(({ v, label, icon }) => (
          <button key={v} className={`pill ${view === v ? "on" : ""}`} aria-current={view === v ? "page" : undefined} onClick={() => onView(v)}>
            <span aria-hidden>{icon}</span> {label}
          </button>
        ))}
      </nav>
      <div className="hright">
        <div className="budget" title={budget?.note}>
          {budget ? <>Est. spend <strong>${budget.spent_estimated_usd.toFixed(2)}</strong> / ${budget.total_budget_usd.toFixed(2)} <em className="hide-sm">(local estimate)</em></> : "…"}
        </div>
        <button type="button" className="connect" onClick={onConnect} title="Connect or check image providers (API keys stay on the server)">Connect providers</button>
        <div className="user" title="Local single-user session">
          <span className="avatar" aria-hidden>LU</span><span className="uname">Local User</span>
        </div>
      </div>
    </header>
  );
}

function Section({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="sec">
      <h2><span className="n">{n}</span>{title}</h2>
      {children}
    </section>
  );
}
