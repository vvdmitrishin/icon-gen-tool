import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api/client";
import { CategoryDistribution, type DistCounts, type DistMode } from "./components/CategoryDistribution";
import { ModelSettings, keyOf } from "./components/ModelSettings";
import { ReferenceUploader } from "./components/ReferenceUploader";
import { ResultPanel } from "./components/ResultPanel";
import type { Artifact, Budget, GenerateRequest, ModelsResponse, PreviewResponse, RefMeta } from "./types/api";

const MAX_WISHES = 500;
const CATEGORIES: { n: 1 | 2 | 3 | 4; label: string }[] = [
  { n: 1, label: "Cat. 1 — floating, simple background" },
  { n: 2, label: "Cat. 2 — on a surface" },
  { n: 3, label: "Cat. 3 — realistic surface" },
  { n: 4, label: "Cat. 4 — in an environment" },
];

export default function App() {
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refs, setRefs] = useState<RefMeta[]>([]);
  const [selectedRefs, setSelectedRefs] = useState<string[]>([]);
  const [policy, setPolicy] = useState(false);

  const [collection, setCollection] = useState("Movie Collection");
  const [genre, setGenre] = useState("Science Fiction");
  const [objectName, setObjectName] = useState("");
  const [objectDesc, setObjectDesc] = useState("");
  const [category, setCategory] = useState<1 | 2 | 3 | 4>(2);
  const [wishes, setWishes] = useState("");
  const [modelKey, setModelKey] = useState("");
  const [aspect, setAspect] = useState("1:1");
  const [distMode, setDistMode] = useState<DistMode>("auto_balanced");
  const [customDist, setCustomDist] = useState<DistCounts | null>(null);

  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [history, setHistory] = useState<Artifact[]>([]);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [armed, setArmed] = useState(false);
  const [checking, setChecking] = useState(false);
  const armTimer = useRef<number | undefined>(undefined);

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

  useEffect(() => { loadModels(); loadRefs(); loadHistory(); }, [loadModels, loadRefs, loadHistory]);

  const defaultDist = useMemo<DistCounts>(() => {
    const d = models?.defaults.category_distribution ?? {};
    return { "1": d["1"] ?? 2, "2": d["2"] ?? 2, "3": d["3"] ?? 3, "4": d["4"] ?? 3 };
  }, [models]);
  const model = useMemo(() => models?.models.find((m) => keyOf(m) === modelKey), [models, modelKey]);
  const refIds = useMemo(() => selectedRefs.filter((id) => refs.some((r) => r.ref_id === id)), [selectedRefs, refs]);

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

  const formValid = !!genre.trim() && !!objectName.trim() && !!model;

  // Debounced free preview (prompt + estimate + warnings). Never calls a provider.
  useEffect(() => {
    if (!formValid) { setPreview(null); setPreviewError(null); return; }
    const t = window.setTimeout(async () => {
      try {
        setPreview(await api.preview(request(false, true)));
        setPreviewError(null);
      } catch (e) {
        setPreview(null);
        setPreviewError(e instanceof ApiError ? e.message : "Preview failed");
      }
    }, 350);
    return () => window.clearTimeout(t);
  }, [formValid, request, models?.budget.spent_estimated_usd]);

  useEffect(() => () => window.clearTimeout(armTimer.current), []);
  useEffect(() => { setArmed(false); }, [modelKey, objectName, genre, category, wishes, aspect, refIds.length]);

  const est = preview?.estimated_cost_usd ?? 0;
  const paid = est > 0;
  const needsPolicy = refIds.length > 0 && model?.provider_id !== "mock" && !policy;
  const blockedReason = preview?.blocked?.message ?? previewError ?? (needsPolicy ? "Confirm the reference-image policy checkbox." : null);
  const canGenerate = formValid && !generating && !blockedReason && !!preview;

  async function onGenerate() {
    if (!canGenerate) return;
    if (paid && !armed) {
      setArmed(true);
      window.clearTimeout(armTimer.current);
      armTimer.current = window.setTimeout(() => setArmed(false), 10000);
      return;
    }
    setArmed(false);
    setGenerating(true);
    setGenError(null);
    try {
      const a = await api.generate(request(true));
      setArtifact(a);
      setHistory((h) => [a, ...h.filter((x) => x.artifact_id !== a.artifact_id)].slice(0, 24));
      loadModels(); // refresh spend + live status
    } catch (e) {
      setGenError(e instanceof ApiError ? `${e.message}${e.code ? ` [${e.code}]` : ""}` : "Generation failed");
      loadModels();
    } finally {
      setGenerating(false);
    }
  }

  async function onCheck() {
    setChecking(true);
    try { await api.checkAvailability(); } catch { /* error surfaces via availability.error */ }
    await loadModels();
    setChecking(false);
  }

  return (
    <div className="app">
      <Header budget={models?.budget} />
      {loadError && <div className="alert err top" role="alert">{loadError}</div>}
      <main>
        <aside className="left" aria-label="Request settings">
          <Section n={1} title="Set & Theme">
            <div className="row"><label className="lbl" htmlFor="coll">Collection</label>
              <input id="coll" value={collection} onChange={(e) => setCollection(e.target.value)} maxLength={80} /></div>
            <div className="row"><label className="lbl" htmlFor="genre">Genre / Set</label>
              <input id="genre" list="genres" value={genre} onChange={(e) => setGenre(e.target.value)} maxLength={80} />
              <datalist id="genres"><option value="Science Fiction" /><option value="Detective" /><option value="Fantasy" /></datalist></div>
            <div className="row"><label className="lbl" htmlFor="obj">Object</label>
              <input id="obj" placeholder="e.g. Retro Space Helmet" value={objectName} onChange={(e) => setObjectName(e.target.value)} maxLength={80} /></div>
            <div className="row"><label className="lbl" htmlFor="objd">Details <small>(optional)</small></label>
              <input id="objd" placeholder="e.g. glass visor, brass rivets" value={objectDesc} onChange={(e) => setObjectDesc(e.target.value)} maxLength={300} /></div>
            <div className="row two">
              <div><span className="lbl">Cards</span><input value="1 (slice)" disabled aria-label="Cards" /></div>
              <div><span className="lbl">Variants</span><input value="1 (slice)" disabled aria-label="Variants" /></div>
            </div>
            <p className="hint">The full 10-card × 3–5 variant set arrives in Phase 3; this slice generates one card.</p>
          </Section>

          <Section n={2} title="Rules, Style & Brief">
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
            <div className="row"><label className="lbl">Art style</label>
              <select disabled><option>Reference Match v1 (draft Style Bible)</option></select></div>
            <label className="lbl" htmlFor="wishes">Producer's free wishes <small>(optional)</small></label>
            <textarea id="wishes" rows={3} maxLength={MAX_WISHES} value={wishes} onChange={(e) => setWishes(e.target.value)}
              placeholder="Desired atmosphere, objects, era, forbidden motifs…" />
            <div className="counter">{wishes.length} / {MAX_WISHES}</div>
            {preview?.warnings.map((w) => <div key={w} className="alert warn">{w}</div>)}
            <div className="note">System rules stay fixed (style, categories, artwork-only). Producer wishes are optional creative guidance.</div>
          </Section>

          <Section n={3} title="References">
            <ReferenceUploader refs={refs} selected={refIds} maxRefs={models?.limits.max_refs_per_request ?? 5}
              refTypes={models?.limits.ref_types ?? ["style"]} policyAccepted={policy}
              onChange={loadRefs} onSelect={setSelectedRefs} onPolicy={setPolicy} />
          </Section>

          <Section n={4} title="Generation Setup">
            <CategoryDistribution
              mode={distMode}
              counts={customDist ?? defaultDist}
              defaults={defaultDist}
              onMode={(m) => { setDistMode(m); if (m === "custom") setCustomDist((c) => c ?? { ...defaultDist }); }}
              onCount={(cat, v) => setCustomDist((c) => ({ ...(c ?? defaultDist), [cat]: Math.max(0, Math.min(10, v)) }))}
            />
            {models ? (
              <ModelSettings models={models.models} value={modelKey} onChange={setModelKey} aspect={aspect} onAspect={setAspect}
                checking={checking} checkedAt={models.availability.checked_at} availabilityError={models.availability.error} onCheck={onCheck} />
            ) : <p className="hint">Loading models…</p>}
            <details className="adv"><summary>Advanced</summary>
              <p className="hint">Automatic retries: <strong>{models?.budget.max_card_retries ?? 0}</strong> (off). Parallel jobs: 1. Batches are blocked in this slice.</p>
            </details>
          </Section>

          <div className="cta">
            {blockedReason && formValid && <div className="alert warn">{blockedReason}</div>}
            {paid && (
              <div className="estimate">Estimated cost: <strong>${est.toFixed(3)}</strong> <em>(local estimate, 1 image)</em></div>
            )}
            <div className="cta-row">
              <button type="button" className={`primary ${armed ? "armed" : ""}`} onClick={onGenerate} disabled={!canGenerate}>
                {generating ? "Generating…" : armed ? `Confirm & spend ≈ $${est.toFixed(3)}` : paid ? `Generate card · ≈ $${est.toFixed(3)}` : "Generate card (free)"}
              </button>
              {armed && <button type="button" className="ghost" onClick={() => setArmed(false)}>Cancel</button>}
            </div>
            {!formValid && <p className="hint">Enter a genre and an object, and pick an available model.</p>}
          </div>
        </aside>

        <ResultPanel artifact={artifact} history={history} onPick={setArtifact} preview={preview} generating={generating} error={genError} />
      </main>
    </div>
  );
}

function Header({ budget }: { budget?: Budget }) {
  return (
    <header className="top">
      <div className="brand">
        <span className="logo" aria-hidden>▣</span>
        <strong>AI Collection Studio</strong>
        <span className="tag">Game Art Production for Collectible Card Sets</span>
      </div>
      <nav>
        <button className="pill on">✦ Generate</button>
        <button className="pill" disabled title="Coming in a later phase">History</button>
        <button className="pill" disabled title="Coming in a later phase">Templates</button>
        <button className="pill" disabled title="Coming in a later phase">Settings</button>
      </nav>
      <div className="budget" title={budget?.note}>
        {budget ? <>Est. spend <strong>${budget.spent_estimated_usd.toFixed(2)}</strong> / ${budget.total_budget_usd.toFixed(2)} cap <em>(local estimate)</em></> : "…"}
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
