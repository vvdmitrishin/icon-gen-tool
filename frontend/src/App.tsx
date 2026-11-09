import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api/client";
import { CategoryDistribution, distributionErrors, type DistMode } from "./components/CategoryDistribution";
import { GoldSlider } from "./components/GoldSlider";
import { PlanNotice, type PlanError } from "./components/PlanNotice";
import type { PlanOnlyState } from "./components/PlanOnlyPanel";
import { useSessionPrompts } from "./lib/sessionPrompts";
import { ModelSettings, ProviderAvailability, keyOf } from "./components/ModelSettings";
import { DEFAULT_CARDS, DEFAULT_SIZE, balancedSplit, fitCounts, type Cat, type DistCounts } from "./lib/cards";
import { ReferenceUploader } from "./components/ReferenceUploader";
import { HistoryView } from "./components/HistoryView";
import { ResultPanel } from "./components/ResultPanel";
import { SetPanel } from "./components/SetPanel";
import { SettingsView } from "./components/SettingsView";
import { SetupWizard, useFirstRun } from "./components/SetupWizard";
import { TemplatesView } from "./components/TemplatesView";
import type {
  Artifact, Budget, GenerateRequest, JobDetail, JobEvent, JobPreview, JobRequestPayload,
  JobSummary, ModelsResponse, PreviewResponse, RefMeta,
} from "./types/api";

const MAX_WISHES = 500;
const CATEGORIES: { n: 1 | 2 | 3 | 4; label: string }[] = [
  { n: 1, label: "Cat. 1 — floating, simple background" },
  { n: 2, label: "Cat. 2 — on a surface" },
  { n: 3, label: "Cat. 3 — realistic surface" },
  { n: 4, label: "Cat. 4 — in an environment" },
];
type View = "generate" | "history" | "templates" | "settings";

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? `${e.message}${e.code ? ` [${e.code}]` : ""}` : fallback);
const toPlanError = (e: unknown): PlanError =>
  e instanceof ApiError ? { code: e.code, message: e.message } : { code: null, message: "Preview failed" };
const clampInt = (v: unknown, lo: number, hi: number, d: number) =>
  typeof v === "number" && Number.isFinite(v) ? Math.max(lo, Math.min(hi, Math.round(v))) : d;

export default function App() {
  const [view, setView] = useState<View>("generate");
  // First-run setup guide / "Connect providers" dialog (credentials are handled by the backend; never stored in the browser).
  const [setupOpen, setSetupOpen] = useState(false);
  const { firstRun, refresh: refreshFirstRun } = useFirstRun();
  useEffect(() => { if (firstRun) setSetupOpen(true); }, [firstRun]);
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refs, setRefs] = useState<RefMeta[]>([]);
  const [selectedRefs, setSelectedRefs] = useState<string[]>([]);
  const [policy, setPolicy] = useState(false);

  const [collection, setCollection] = useState("");
  const [genre, setGenre] = useState("");
  const [numberOfCards, setNumberOfCards] = useState(DEFAULT_CARDS);
  const [gold, setGold] = useState(0);
  const [setType, setSetType] = useState<"standard" | "grand">("standard");
  const grand = setType === "grand";
  const [outputSize, setOutputSize] = useState(DEFAULT_SIZE);
  const [objectName, setObjectName] = useState("");
  const [objectDesc, setObjectDesc] = useState("");
  const [category, setCategory] = useState<1 | 2 | 3 | 4>(2);
  const [wishes, setWishes] = useState("");
  const [modelKey, setModelKey] = useState("");
  const aspect = "1:1"; // Output Resolution is the only user-facing size setting; images are always square
  const [distMode, setDistMode] = useState<DistMode>("auto_balanced");
  const [customDist, setCustomDist] = useState<DistCounts | null>(null);
  const [variants, setVariants] = useState(1);
  const [seed, setSeed] = useState<number | null>(null);

  // single-card slice
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [history, setHistory] = useState<Artifact[]>([]);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);

  // set generation
  // The plan preview is tagged with the plan key it was made for; a result for another Collection/Set/etc. is never shown.
  const [pv, setPv] = useState<{ key: string; preview: JobPreview | null; error: PlanError | null } | null>(null);
  const aiCache = useRef(new Map<string, JobPreview>()); // AI plans already paid for, by exact request
  const [aiPlanKey, setAiPlanKey] = useState<string | null>(null); // planKey the user agreed to plan with the AI for
  const [planningAi, setPlanningAi] = useState(false);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [jobError, setJobError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [continueArmed, setContinueArmed] = useState(false);

  const [armed, setArmed] = useState(false);
  const [planOnly, setPlanOnly] = useState<PlanOnlyState | null>(null);
  const sessionPrompts = useSessionPrompts(); // session-only prompt overrides (sessionStorage), sent with every generation
  const promptOverrides = sessionPrompts.overrides;
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
    api.jobs().then((js) => { setJobs(js); if (js[0]) openJob(js[0].job_id); }).catch(() => undefined); // persists across refresh
  }, [loadModels, loadRefs, loadHistory, openJob]);

  // model availability is backend state: re-read it whenever the Generate view is shown (and when the tab regains focus)
  useEffect(() => { if (view === "generate") void loadModels(); }, [view, loadModels]);
  useEffect(() => {
    const onFocus = () => { void loadModels(); };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [loadModels]);

  // poll the running job
  useEffect(() => {
    if (!job?.active) return;
    const id = job.job_id;
    const t = window.setInterval(async () => {
      try {
        const [d, ev] = await Promise.all([api.job(id), api.jobEvents(id)]);
        setJob(d);
        setEvents(ev);
        loadModels();  // header cost counters follow the running batch
        if (!d.active) { loadJobs(); }
      } catch { /* transient */ }
    }, 1500);
    return () => window.clearInterval(t);
  }, [job?.job_id, job?.active, loadModels, loadJobs]);

  // Single-card mode is driven by "Number of cards" (1 = single card, > 1 = card set).
  const mode: "set" | "single" = numberOfCards === 1 && !grand ? "single" : "set";
  // Grand Set (All Golden): gold = number of cards, Cat. 1-4 = 0
  const goldEff = grand ? numberOfCards : mode === "set" ? Math.min(gold, numberOfCards) : 0;
  const standard = numberOfCards - goldEff;
  // everything the plan depends on; the "ai planned" flag is only valid while this is unchanged
  const planKey = useMemo(
    () => JSON.stringify([collection.trim(), genre.trim(), setType, variants, numberOfCards, goldEff, distMode, distMode === "custom" ? customDist : null, wishes, seed]),
    [collection, genre, setType, variants, numberOfCards, goldEff, distMode, customDist, wishes, seed],
  );
  const aiPlanned = aiPlanKey === planKey;
  // Metaphor Planner: the plan the Producer curated. It is only used while every plan-defining input is unchanged.
  const planStale = !!planOnly?.result && planOnly.key !== planKey;
  const curatedPlan = mode === "set" && planOnly?.result && !planOnly.busy && planOnly.key === planKey ? planOnly.result.plan : null;
  const aiKeyRef = useRef<string | null>(null);
  aiKeyRef.current = aiPlanKey;
  const jobPreview = pv?.key === planKey ? pv.preview : null;
  const jobPreviewError = pv?.key === planKey ? pv.error : null;
  const previewDist = jobPreview?.distribution;
  const previewDistKey = previewDist ? JSON.stringify(previewDist) : "";
  const autoCounts = useMemo<DistCounts>(() => {
    const d = previewDist;
    if (d) {
      const c: DistCounts = { "1": d["1"] ?? 0, "2": d["2"] ?? 0, "3": d["3"] ?? 0, "4": d["4"] ?? 0 };
      if (c["1"] + c["2"] + c["3"] + c["4"] === standard) return c; // the backend is the source of truth
    }
    return balancedSplit(standard);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewDistKey, standard]);
  // Custom counts are never changed silently: a mismatch with the standard-card total is shown and blocks Generate until fixed (Auto-fit button).
  const distErrs = mode === "set" && !grand && distMode === "custom" ? distributionErrors(customDist ?? autoCounts, standard) : [];
  const distBad = distErrs.length > 0;
  const model = useMemo(() => models?.models.find((m) => keyOf(m) === modelKey), [models, modelKey]);
  const refIds = useMemo(() => selectedRefs.filter((id) => refs.some((r) => r.ref_id === id)), [selectedRefs, refs]);

  const perImageSel = model?.est_cost_per_image_usd ?? 0;
  const perImageJob = useMemo(() => {
    const m = models?.models.find((x) => x.provider_id === job?.request.provider && x.model_id === job?.request.model);
    return m?.est_cost_per_image_usd ?? 0;
  }, [models, job?.request.provider, job?.request.model]);

  const applyPreset = useCallback((pl: Record<string, unknown>) => {
    if (typeof pl.collection === "string") setCollection(pl.collection);
    if (typeof pl.genre === "string") setGenre(pl.genre);
    if (typeof pl.number_of_variants === "number") setVariants(clampInt(pl.number_of_variants, 1, 5, 1));
    const n = clampInt(pl.number_of_cards, 1, 10, DEFAULT_CARDS);
    setNumberOfCards(n);
    setGold(clampInt(pl.gold_cards, 0, n, 0));
    setSetType(pl.set_type === "grand" ? "grand" : "standard");
    setOutputSize(clampInt(pl.output_size, 24, 1024, DEFAULT_SIZE));
    if (typeof pl.wishes === "string") setWishes(pl.wishes);
    const d = pl.category_distribution as { mode?: string; counts?: Record<string, number> } | undefined;
    if (d?.mode === "custom" && d.counts) {
      setDistMode("custom");
      setCustomDist(fitCounts({ "1": d.counts["1"] ?? 1, "2": d.counts["2"] ?? 1, "3": d.counts["3"] ?? 1, "4": d.counts["4"] ?? 1 }, n - clampInt(pl.gold_cards, 0, n, 0)));
    } else setDistMode("auto_balanced");
    setSeed(null);
    setView("generate");
  }, []);

  /** "Manage connection": open Settings and bring the API Connections section into view. */
  const manageConnections = useCallback(() => {
    setView("settings");
    window.setTimeout(() => {
      const el = document.getElementById("cn-panel-title");
      if (!el) return;
      el.setAttribute("tabindex", "-1");
      el.scrollIntoView({ block: "start" });
      el.focus({ preventScroll: true });
    }, 120);
  }, []);

  const request = useCallback(
    (confirmed: boolean, policyOverride?: boolean): GenerateRequest => ({
      collection: collection.trim(),
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
      output_size: outputSize,
      confirmed,
      ...(Object.keys(promptOverrides).length ? { prompt_overrides: promptOverrides } : {}),
    }),
    [collection, genre, objectName, objectDesc, category, wishes, model, refIds, policy, aspect, outputSize, promptOverrides],
  );

  const jobRequest = useCallback(
    (confirmed: boolean, policyOverride: boolean | undefined, allowAi: boolean): JobRequestPayload => ({
      collection: collection.trim(),
      genre: genre.trim(),
      wishes,
      number_of_variants: variants,
      number_of_cards: numberOfCards,
      gold_cards: goldEff,
      set_type: setType,
      output_size: outputSize,
      allow_ai_plan: allowAi,
      category_distribution: distMode === "auto_balanced"
        ? { mode: "auto_balanced" }
        : { mode: "custom", counts: { ...(customDist ?? balancedSplit(standard)) } },
      provider: model?.provider_id ?? "",
      model: model?.model_id ?? "",
      reference_ids: refIds,
      reference_policy_accepted: policyOverride ?? policy,
      aspect_ratio: aspect,
      scope: "full",
      seed,
      confirmed,
      ...(Object.keys(promptOverrides).length ? { prompt_overrides: promptOverrides } : {}),
      ...(curatedPlan ? { plan: curatedPlan } : {}),
    }),
    [collection, genre, setType, wishes, variants, numberOfCards, goldEff, standard, outputSize, distMode, customDist, model, refIds, policy, aspect, seed, promptOverrides, curatedPlan],
  );

  const onBatch = useCallback(async (confirmed: boolean) => {
    const { plan: _curated, ...batchReq } = jobRequest(confirmed, undefined, aiPlanned); // a curated plan belongs to ONE Set, never to the 3-genre batch
    await api.batch(batchReq, ["Science Fiction", "Detective", "Fantasy"]);
    loadJobs();
    loadModels();
  }, [jobRequest, aiPlanned, loadJobs, loadModels]);

  /** "Generate similar set": reload the job's settings into the form with a new shuffled seed. Nothing is generated. */
  const startSimilar = useCallback((j: JobDetail) => {
    const r = j.request;
    setCollection(j.collection);
    setGenre(j.genre);
    setVariants(j.variants.length);
    const n = clampInt(j.number_of_cards ?? r.number_of_cards, 1, 10, DEFAULT_CARDS);
    const g = clampInt(j.gold_cards ?? r.gold_cards, 0, n, 0);
    setNumberOfCards(n); setGold(g);
    setSetType((j.set_type ?? r.set_type) === "grand" ? "grand" : "standard");
    setOutputSize(clampInt(j.output_size ?? r.output_size, 24, 1024, DEFAULT_SIZE));
    setWishes(r.wishes ?? "");
    const key = `${r.provider}/${r.model}`;
    if (models?.models.some((m) => keyOf(m) === key && m.selectable)) setModelKey(key);
    const d = r.category_distribution;
    if (d.mode === "custom") {
      setDistMode("custom");
      setCustomDist(fitCounts({ "1": d.counts["1"] ?? 1, "2": d.counts["2"] ?? 1, "3": d.counts["3"] ?? 1, "4": d.counts["4"] ?? 1 }, n - g));
    } else setDistMode("auto_balanced");
    setSeed(Math.floor(Math.random() * 2 ** 31));
    setJob(null); setEvents([]); setJobError(null);
    setView("generate");
  }, [models]);

  const singleValid = !!collection.trim() && !!genre.trim() && !!objectName.trim() && !!model;
  const setValid = !!collection.trim() && !!genre.trim() && !!model;
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
    if (!setValid || distBad) { setPv(null); return; }
    const key = planKey;
    let live = true;
    const t = window.setTimeout(async () => {
      // Free previews never trigger the AI planner unless the user already agreed for these exact inputs.
      const allow = aiKeyRef.current === key;
      const req = jobRequest(false, true, allow);
      const sig = JSON.stringify(req);
      const hit = allow ? aiCache.current.get(sig) : undefined;
      if (hit) { setPv({ key, preview: hit, error: null }); return; } // this exact AI plan was already paid for: never pay twice
      try {
        const r = await api.jobsPreview(req);
        if (allow) aiCache.current.set(sig, r);
        if (live) setPv({ key, preview: r, error: null });
      } catch (e) { if (live) setPv({ key, preview: null, error: toPlanError(e) }); }
    }, 350);
    return () => { live = false; window.clearTimeout(t); };
  }, [mode, setValid, distBad, jobRequest, planKey, models?.budget.spent_estimated_usd]);

  /** "Preview plan (optional)": one small AI text call; the plan is reused by Generate while the inputs stay unchanged. */
  async function planWithAi() {
    setPlanningAi(true);
    const key = planKey;
    try {
      const req = jobRequest(false, true, true);
      const r = await api.jobsPreview(req);
      aiCache.current.set(JSON.stringify(req), r);
      setPv({ key, preview: r, error: null });
      setAiPlanKey(key);
    } catch (e) { setPv({ key, preview: null, error: toPlanError(e) }); }
    finally { setPlanningAi(false); }
  }

  useEffect(() => () => { window.clearTimeout(armTimer.current); window.clearTimeout(contTimer.current); }, []);
  useEffect(() => { setArmed(false); }, [mode, modelKey, objectName, genre, category, wishes, aspect, outputSize, refIds.length, variants, numberOfCards, goldEff, distMode, customDist]);

  // A custom Collection/Set is planned by the AI automatically when Generate is clicked (one tiny text call; the estimate comes from the backend's real text-model pricing).
  // The free preview cannot show that plan yet, so Generate stays available and the cost is estimated locally.
  const aiAuto = mode === "set" && jobPreviewError?.code === "ai_plan_required";
  const planCost = mode === "set" ? curatedPlan ? 0 : (jobPreview?.plan_cost_estimate_usd ?? (aiAuto ? (models?.planner ? models.planner.base_usd + models.planner.per_card_usd * variants * numberOfCards : 0.005) : 0)) : 0;
  const est = mode === "single" ? (preview?.estimated_cost_usd ?? 0) : (jobPreview?.estimated_cost_usd ?? (aiAuto ? variants * numberOfCards * perImageSel : 0));
  const paid = est > 0 || planCost > 0; // the confirmation click covers planning AND images; planning never bypasses it
  const needsPolicy = refIds.length > 0 && model?.provider_id !== "mock" && !policy;
  const policyNeeded = refIds.length > 0 && model?.provider_id !== "mock";
  const previewBlock = mode === "single" ? preview?.blocked?.message : jobPreview?.blocked?.message;
  const blockedReason = previewBlock ?? distErrs[0] ?? (mode === "single" ? previewError : aiAuto ? null : jobPreviewError?.message) ?? (needsPolicy ? "Confirm the reference-image policy checkbox." : null);
  const havePreview = mode === "single" ? !!preview : (!!jobPreview || aiAuto);
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
        const d = await api.createJob(jobRequest(true, undefined, true)); // Generate authorises the AI planning call
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

  /** "Test planner": the real semantic planner, never an image provider. Optional: Generate does not need it. */
  const planFields = () => ({
    collection: collection.trim(), genre: genre.trim(), wishes, number_of_variants: variants,
    number_of_cards: numberOfCards, gold_cards: goldEff, set_type: setType, seed,
    category_distribution: distMode === "auto_balanced" ? { mode: "auto_balanced" } : { mode: "custom", counts: { ...(customDist ?? balancedSplit(standard)) } },
  });

  /** "Plan Metaphors": the real semantic planner, never an image provider. Its plan is what Generate Sets uses while inputs are unchanged. */
  async function onPlanOnly() {
    const form = `${collection.trim()} · ${genre.trim()} (${grand ? "Grand Set" : "Standard Set"}, ${numberOfCards} cards)`;
    const key = planKey;
    const base = { form, key, selected: [] as string[], regenBusy: false, regenError: null, changed: [] as string[] };
    setPlanOnly({ busy: true, result: null, error: null, ...base });
    try {
      const r = await api.planTest(planFields());
      setPlanOnly({ busy: false, result: r, error: null, ...base });
      loadModels();
    } catch (e) { setPlanOnly({ busy: false, result: null, error: errText(e, "Metaphor Planner failed"), ...base }); }
  }

  function onTogglePlan(keys: string[], on: boolean) {
    setPlanOnly((s) => {
      if (!s) return s;
      const cur = new Set(s.selected);
      keys.forEach((k) => (on ? cur.add(k) : cur.delete(k)));
      return { ...s, selected: [...cur] };
    });
  }

  /** "Regenerate Selected": a new text-only call for the selected concepts; everything else in the plan stays as it is. */
  async function onRegeneratePlan() {
    const cur = planOnly;
    if (!cur?.result || cur.selected.length === 0 || cur.regenBusy) return;
    const picked = cur.selected;
    setPlanOnly({ ...cur, regenBusy: true, regenError: null });
    try {
      const r = await api.planRegenerate({
        ...planFields(), plan: cur.result.plan,
        selected: picked.map((k) => ({ variant_id: k[0], position: Number(k.slice(1)) })),
      });
      setPlanOnly((s) => (s ? { ...s, regenBusy: false, result: { ...s.result!, ...r }, selected: [], changed: picked } : s));
      loadModels();
    } catch (e) {
      setPlanOnly((s) => (s ? { ...s, regenBusy: false, regenError: errText(e, "Regenerating the selected concepts failed.") } : s));
    }
  }

  async function onCheck() {
    setChecking(true);
    try { await api.checkAvailability(); } catch { /* error surfaces via availability.error */ }
    await loadModels();
    setChecking(false);
  }

  const buttonLabel: { main: string; sub?: string; sub2?: string } = (() => {
    if (generating || starting) return { main: mode === "single" ? "Generating…" : "Starting…" };
    if (job?.active && mode === "set") return { main: "Job running…" };
    if (armed) return { main: `Confirm & spend ≈ $${(est + planCost).toFixed(3)}`, sub: planCost > 0 ? `Images ≈ $${est.toFixed(3)} + AI planning ≈ $${planCost.toFixed(3)}. Click again to start` : "Click again to start; this uses the paid API" };
    const cost = est > 0 ? `≈ $${est.toFixed(3)} (local estimate)` : "free";
    if (mode === "single") return { main: "Generate card", sub: `Images: ${cost}` };
    const n = jobPreview?.images_in_scope ?? variants * numberOfCards;
    return {
      main: `Generate Sets (${variants} variant${variants > 1 ? "s" : ""})`,
      sub: `Images: ${cost} · ${n} image${n === 1 ? "" : "s"}, paid separately from planning`,
      sub2: planCost > 0 ? `AI plans the cards automatically: text ≈ $${planCost.toFixed(3)}, one time` : undefined,
    };
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
            <HistoryView jobs={jobs} perImage={perImageSel} variants={variants} cardsPerSet={numberOfCards} busy={!!job?.active || starting}
              onOpen={(id) => { openJob(id); setNumberOfCards((n) => (n === 1 ? DEFAULT_CARDS : n)); setView("generate"); }} onBatch={onBatch} onRefresh={loadJobs} />
          )}
          {view === "templates" && (
            <TemplatesView onApply={applyPreset} current={() => ({
              collection: collection.trim(), genre, number_of_variants: variants, number_of_cards: numberOfCards, gold_cards: goldEff, output_size: outputSize,
              wishes: wishes.slice(0, 500),
              category_distribution: distMode === "auto_balanced" ? { mode: "auto_balanced" } : { mode: "custom", counts: { ...(customDist ?? balancedSplit(standard)) } },
            })} />
          )}
          {view === "settings" && <SettingsView onOpenSetup={() => setSetupOpen(true)} onConnectionChanged={loadModels} />}
        </div>
      )}
      <main hidden={view !== "generate"}>
        <aside className="left" aria-label="Request settings">
          <div className="left-scroll">
            {mode === "single" && (
              <div className="modebar" role="status">
                <span><strong>Single-card mode</strong> (Number of cards = 1): one object, one provider call</span>
              </div>
            )}

            <Section n={1} title="Set & Theme">
              <div className="row"><label className="lbl" htmlFor="coll">Collection</label>
                <input id="coll" value={collection} placeholder="e.g. Movies, Transport, Space Travel" onChange={(e) => { setCollection(e.target.value); setSeed(null); }} maxLength={80} /></div>
              <div className="row"><label className="lbl" htmlFor="genre">Set</label>
                <input id="genre" value={genre} placeholder="e.g. Sci-Fi, Detective, Cars" onChange={(e) => { setGenre(e.target.value); setSeed(null); }} maxLength={80} autoComplete="off" /></div>
              <div className="row"><span className="lbl" id="settype-lbl">Set type</span>
                <div className="seg" role="radiogroup" aria-labelledby="settype-lbl" id="settype">
                  {([["standard", "🗽 Standard Set"], ["grand", "🪙 Grand Set"]] as const).map(([v, label]) => (
                    <button key={v} type="button" role="radio" aria-checked={setType === v} className={setType === v ? "on" : ""}
                      title={v === "grand" ? "Grand Set (All Golden): the finale set, every card is golden" : "Standard Set: Categories 1–4 plus optional golden cards"}
                      onClick={() => { setSetType(v); setSeed(null); }}>{label}</button>
                  ))}
                </div></div>
              {grand && <p className="hint rowhint">Finale set: every card is golden (an ironic, emotional scene with at most 2 characters), Categories 1–4 are 0. "Grand" is the final set position in the collection, not a card count. Always planned with AI; not part of the Movie Collection bundle.</p>}
              <div className="row two">
                <div><label className="lbl" htmlFor="ncards">Number of cards</label>
                  <select id="ncards" value={numberOfCards} onChange={(e) => { const n = Number(e.target.value); setNumberOfCards(n); setGold((g) => Math.min(g, n)); setSeed(null); }}>
                    {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n === 10 ? "10 (standard)" : n}</option>)}
                  </select></div>
                {mode === "set" && (
                  <div><label className="lbl" htmlFor="variants">Variants</label>
                    <div className="withbtn">
                      <select id="variants" value={variants} onChange={(e) => { setVariants(Number(e.target.value)); setSeed(null); }}>
                        {Array.from({ length: 5 }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                      <button type="button" className="ghost sm" onClick={() => setSeed(Math.floor(Math.random() * 2 ** 31))}
                        title={seed != null ? `Plan seed ${seed}. Picks a different set of objects; changing the theme, set or variants resets it.` : "Picks a different set of objects"}>Shuffle plan</button>
                    </div></div>
                )}
              </div>
              {mode === "single" && (
                <>
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
                    <label className="cat locked" title="Golden cards are available in card-set mode">
                      <input type="radio" disabled />★ Golden cards (Cat. 5) — card sets only
                    </label>
                  </div>
                </>
              ) : (
                <>
                  <GoldSlider value={goldEff} max={numberOfCards} disabled={grand} onChange={(v) => { setGold(v); setSeed(null); }} />
                  <CategoryDistribution
                    mode={distMode}
                    counts={customDist ?? autoCounts}
                    autoCounts={autoCounts}
                    standard={standard}
                    gold={goldEff}
                    grand={grand}
                    onMode={(m) => { setDistMode(m); setSeed(null); if (m === "custom") setCustomDist((c) => c ?? { ...autoCounts }); }}
                    onCount={(cat: Cat, v) => setCustomDist((c) => ({ ...(c ?? autoCounts), [cat]: Math.max(0, Math.min(standard, v)) }))}
                    onAutoFit={() => setCustomDist((c) => fitCounts(c ?? autoCounts, standard))}
                    compliant={jobPreview?.compliant}
                    complianceNote={jobPreview?.compliance_note}
                  />
                </>
              )}
              <div className="field">
                <label className="lbl" htmlFor="wishes">Producer's free wishes <small>(optional)</small></label>
                <div className="ta-wrap">
                  <textarea id="wishes" rows={3} maxLength={MAX_WISHES} value={wishes} onChange={(e) => setWishes(e.target.value)}
                    placeholder="Desired atmosphere, objects, era, forbidden motifs…" />
                  <span className="counter" aria-live="off">{wishes.length} / {MAX_WISHES}</span>
                </div>
              </div>
              {mode === "single" && preview?.warnings.map((w) => <div key={w} className="alert warn">{w}</div>)}
              <div className="note"><span aria-hidden>ⓘ</span> System rules stay fixed (style, categories, artwork-only). Wishes steer the image prompts and the object plan.</div>
            </Section>

            <Section n={3} title="References">
              <ReferenceUploader refs={refs} selected={refIds} maxRefs={models?.limits.max_refs_per_request ?? 5}
                refTypes={models?.limits.ref_types ?? ["style"]} policyAccepted={policy} policyNeeded={policyNeeded}
                onChange={loadRefs} onSelect={setSelectedRefs} onPolicy={setPolicy} />
            </Section>

            <Section n={4} title="Generation Setup">
              {models ? (
                <>
                  <ModelSettings models={models.models} value={modelKey} onChange={setModelKey} outputSize={outputSize} onOutputSize={setOutputSize} onManage={manageConnections} onConnectionChanged={loadModels} />
                  <ProviderAvailability models={models.models} checking={checking} checkedAt={models.availability.checked_at} availabilityError={models.availability.error} onCheck={onCheck} />
                </>
              ) : <p className="hint">Loading models…</p>}
            </Section>
          </div>

          <div className="cta">
            {mode === "set" && jobPreviewError && formValid
              ? <PlanNotice error={jobPreviewError} planning={planningAi} onPlanWithAi={planWithAi} onConnectGemini={() => setSetupOpen(true)} compact />
              : blockedReason && formValid && <div className="alert warn compact">{blockedReason}</div>}
            {mode === "set" && jobPreview?.warnings.map((w) => <div key={w} className="alert warn compact">{w}</div>)}
            {sessionPrompts.count > 0 && (
              <div className="alert warn compact session-chip" role="status">
                <strong>Session Override Active</strong> ({sessionPrompts.names.map((n) => n.replace("_", " ")).join(", ")}): Generate uses these temporary prompts.
                <button type="button" className="ghost sm" onClick={sessionPrompts.resetAll}>Reset all</button>
              </div>
            )}
            {mode === "set" && (
              <div className="cta-row plan-step">
                <button type="button" className="primary big planbtn" onClick={onPlanOnly} disabled={!genre.trim() || !collection.trim() || distBad || !!planOnly?.busy || !!planOnly?.regenBusy || busy}
                  title="Step 1: runs only the Metaphor Planner on the current Collection / Set / Free Wishes. No image provider is called; a real text call costs a few tenths of a cent. Generate Sets then uses this plan.">
                  <span>{planOnly?.busy ? "Planning…" : "🧠 Plan Metaphors"}</span>
                  <small>{curatedPlan ? "Plan ready · curate it below, then generate" : "Step 1 · text only, no images"}</small>
                </button>
              </div>
            )}
            <div className="cta-row">
              <button type="button" className={`primary big ${armed ? "armed" : ""}`} onClick={onGenerate} disabled={!canGenerate}>
                <span>{buttonLabel.main}</span>{buttonLabel.sub && <small>{buttonLabel.sub}</small>}{buttonLabel.sub2 && <small className="cost2">{buttonLabel.sub2}</small>}
              </button>
              {armed && <button type="button" className="ghost" onClick={() => setArmed(false)}>Cancel</button>}
            </div>
            {!formValid && <p className="hint">{mode === "single" ? "Enter a set and an object, and pick an available model." : "Pick an available model."}</p>}
          </div>
        </aside>

        {mode === "set" ? (
          <SetPanel job={job} preview={jobPreview} previewError={jobPreviewError} events={events} jobs={jobs}
            models={models?.models ?? []}
            form={{ collection, genre, variants, numberOfCards, gold: goldEff, setType, outputSize, provider: model?.provider_id ?? "", model: model?.model_id ?? "" }}
            error={jobError} starting={starting} perImage={perImageJob} onJob={(d) => { setJob(d); loadJobs(); if (!d.active) loadModels(); }}
            onError={setJobError} onPickJob={openJob} onContinue={onContinue}
            planningAi={planningAi} onPlanWithAi={planWithAi} onConnectGemini={() => setSetupOpen(true)}
            continueArmed={continueArmed} onCancelContinue={() => setContinueArmed(false)} onCancelJob={onCancelJob}
            onNew={() => { setJob(null); setEvents([]); setJobError(null); }} onSimilar={startSimilar} onOpenHistory={() => setView("history")}
            planOnly={planOnly} planStale={planStale} onClosePlanOnly={() => setPlanOnly(null)}
            onTogglePlan={onTogglePlan} onRegeneratePlan={onRegeneratePlan}
            onPreviewPrompts={() => api.previewPrompts(jobRequest(false, undefined, true))} />
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
        <div className="costs" title={budget ? `Lifetime estimated cost from recorded usage (not provider billing). ${budget.note}` : undefined} aria-label="Lifetime estimated cost">
          {budget ? <>
            <span className="cost plan">Planning <strong>${budget.planning_estimated_usd.toFixed(2)}</strong></span>
            <span className="cost gen">Generation <strong>${budget.generation_estimated_usd.toFixed(2)}</strong></span>
            <span className="cost tot">Total <strong>${(budget.planning_estimated_usd + budget.generation_estimated_usd).toFixed(2)}</strong></span>
            <em className="hide-sm">est.</em>
          </> : "…"}
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
