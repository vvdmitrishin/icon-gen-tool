import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import { vlabel } from "../lib/cards";
import { SaveExport } from "./SaveExport";
import type { Artifact, CollectionOverview, ExportRecord, JobSummary } from "../types/api";

interface Props {
  jobs: JobSummary[];
  perImage: number;
  variants: number;
  cardsPerSet: number;
  busy: boolean;
  onOpen: (jobId: string) => void;
  onBatch: (confirmed: boolean) => Promise<void>;
  onRefresh: () => void;
}

const msg = (e: unknown, f: string) => (e instanceof ApiError ? e.message : f);
const GENRES = ["Science Fiction", "Detective", "Fantasy"];

export function HistoryView(p: Props) {
  const [ov, setOv] = useState<CollectionOverview | null>(null);
  const [exports, setExports] = useState<ExportRecord[]>([]);
  const [imports, setImports] = useState<Artifact[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [armed, setArmed] = useState(false);
  const [exportErr, setExportErr] = useState<{ text: string; blockers: string[] } | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      const [o, e, a] = await Promise.all([api.overview(), api.exports(), api.artifacts()]);
      setOv(o); setExports(e); setImports(a.filter((x) => x.is_imported));
    } catch (e) { setErr(msg(e, "Could not load history")); }
  }, []);
  useEffect(() => { refresh(); }, [refresh, p.jobs.length]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const images = GENRES.length * p.variants * p.cardsPerSet;
  const est = images * p.perImage;
  async function batch() {
    if (est > 0 && !armed) { setArmed(true); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setArmed(false), 10000); return; }
    setArmed(false);
    try { await p.onBatch(true); setOk(`Started ${GENRES.length} jobs; they run one after another.`); setErr(null); p.onRefresh(); }
    catch (e) { setErr(msg(e, "Batch failed")); }
  }
  async function exportAll(force: boolean) {
    try {
      const rec = await api.exportCollection(force ? { include_unapproved: true, allow_mock: true, allow_partial: true } : {});
      setOk(`Bundle ready: ${rec.filename}${rec.partial ? " (PARTIAL)" : ""}`); setExportErr(null); await refresh();
    } catch (e) {
      setExportErr({ text: msg(e, "Export failed"), blockers: [] });
    }
  }

  return (
    <div className="view">
      <h2>History &amp; Collection</h2>
      {err && <div className="alert err" role="alert">{err}</div>}
      {ok && <div className="alert ok" role="status">{ok}</div>}

      <section className="card-box">
        <h3>Collection progress — {ov?.collection ?? "Movie Collection"}</h3>
        <p className="hint">{ov?.note}</p>
        <div className="genres">
          {ov?.genres.map((g) => (
            <div key={g.genre} className={`genre ${g.ready ? "ready" : ""}`}>
              <strong>{g.genre}</strong>
              <span className={`chip ${g.ready ? "ok" : "warn"}`}>{g.ready ? "ready" : "not ready"}</span>
              <small>{g.jobs} job(s){g.approved_variant_id ? ` · variant ${vlabel(g.approved_variant_id)} approved` : ""}{g.generated ? ` · ${g.generated} generated` : ""}</small>
              {g.blockers.map((b) => <small key={b} className="bad">• {b}</small>)}
              {g.job_id && <button type="button" className="ghost" onClick={() => p.onOpen(g.job_id!)}>Open</button>}
            </div>
          ))}
        </div>
        <div className="rvbtns">
          <span className={`chip ${ov?.final_ready ? "ok" : "warn"}`}>{ov?.final_ready ? "Final deliverable ready" : "Final deliverable not ready"}</span>
          <button type="button" className="primary small" disabled={!ov?.final_ready} onClick={() => exportAll(false)}>Export final bundle (ZIP)</button>
          <button type="button" className="ghost" onClick={() => exportAll(true)} title="Includes unapproved, partial and mock sets - for testing only">Export anyway (testing)</button>
        </div>
        {exportErr && <div className="alert warn">{exportErr.text}</div>}
        {ov && ov.blockers.length > 0 && <details><summary>Why not ready ({ov.blockers.length})</summary><ul>{ov.blockers.map((b) => <li key={b}>{b}</li>)}</ul></details>}
      </section>

      <section className="card-box">
        <h3>Plan all three genres at once</h3>
        <p className="hint">Uses the current Generate-screen settings (variants: {p.variants}, cards per set: {p.cardsPerSet}, model, references). One confirmation covers all {GENRES.length} jobs; they still run sequentially under the same cost caps.</p>
        <div className="rvbtns">
          <button type="button" className={`primary small ${armed ? "armed" : ""}`} disabled={p.busy} onClick={batch}>
            {armed ? `Confirm & spend ≈ $${est.toFixed(3)}` : `Create ${GENRES.length} jobs · ${images} images${est > 0 ? ` · ≈ $${est.toFixed(3)}` : " (free)"}`}
          </button>
          {armed && <button type="button" className="ghost" onClick={() => setArmed(false)}>Cancel</button>}
        </div>
        {est > 0.75 && <div className="alert warn">This exceeds the per-run cap (GEMINI_JOB_BUDGET_USD) and will be refused; lower the variants or the number of cards.</div>}
      </section>

      <section className="card-box">
        <h3>Jobs</h3>
        {p.jobs.length === 0 ? <p className="hint">No jobs yet.</p> : (
          <table className="gridtbl"><thead><tr><th>Created</th><th>Genre</th><th>Variants</th><th>Cards</th><th>Status</th><th>Est. spent</th><th>Final</th><th /></tr></thead>
            <tbody>{p.jobs.map((j) => (
              <tr key={j.job_id}><td>{new Date(j.created_at).toLocaleString()}</td><td>{j.genre}</td><td>{j.variants}</td><td>{j.number_of_cards ?? 10}{(j.gold_cards ?? 0) > 0 ? ` (+${j.gold_cards} ★)` : ""}</td><td>{j.status.replace(/_/g, " ")}</td>
                <td>${j.spent_est.toFixed(3)}</td><td>{j.approved_variant_id ? `★ ${vlabel(j.approved_variant_id)}` : "–"}</td>
                <td><button type="button" className="ghost" onClick={() => p.onOpen(j.job_id)}>Open</button></td></tr>
            ))}</tbody></table>
        )}
      </section>

      <section className="card-box">
        <h3>Exports</h3>
        {exports.length === 0 ? <p className="hint">No exports yet. Approve a variant, then use “Export ZIP”.</p> : (
          <table className="gridtbl"><thead><tr><th>Created</th><th>Kind</th><th>Contents</th><th>Size</th><th /></tr></thead>
            <tbody>{exports.map((x) => (
              <tr key={x.export_id}><td>{new Date(x.created_at).toLocaleString()}</td><td>{x.kind}</td>
                <td>{x.kind === "variant" ? `${x.genre} · variant ${vlabel(x.variant_id)} · ${x.approved ? "approved" : "unapproved"}` : `final bundle${x.partial ? " (partial)" : ""}`}{x.contains_mock_images ? " · MOCK" : ""}</td>
                <td>{(x.size / 1024).toFixed(0)} KB</td><td><a href={x.download_url} download={x.filename}>Download</a> <SaveExport exportId={x.export_id} /></td></tr>
            ))}</tbody></table>
        )}
      </section>

      <ImportSection imports={imports} onDone={refresh} />
    </div>
  );
}

function ImportSection({ imports, onDone }: { imports: Artifact[]; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [f, setF] = useState({ provider: "", model: "", prompt: "", object: "" });
  const [m, setM] = useState<{ ok: boolean; t: string } | null>(null);
  async function submit() {
    if (!file) return;
    try { await api.importSample(file, f); setM({ ok: true, t: "Imported. It is labelled “Imported” and never counts as pipeline output." }); setFile(null); onDone(); }
    catch (e) { setM({ ok: false, t: msg(e, "Import failed") }); }
  }
  return (
    <section className="card-box">
      <h3>Import a sample image</h3>
      <p className="hint">For research comparisons (e.g. another model). Provider and model labels are required so the origin stays honest; no generation cost is recorded.</p>
      <div className="importform">
        <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Sample image" />
        <input placeholder="Provider (e.g. Z-Image)" value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })} />
        <input placeholder="Model / version" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} />
        <input placeholder="Object name (optional)" value={f.object} onChange={(e) => setF({ ...f, object: e.target.value })} />
        <input placeholder="Prompt / how it was made (optional)" value={f.prompt} onChange={(e) => setF({ ...f, prompt: e.target.value })} />
        <button type="button" className="primary small" disabled={!file || !f.provider.trim() || !f.model.trim()} onClick={submit}>Import</button>
      </div>
      {m && <div className={`alert ${m.ok ? "ok" : "err"}`}>{m.t}</div>}
      {imports.length > 0 && <div className="imps">{imports.map((a) => <figure key={a.artifact_id}><img src={a.image.url} alt={a.card.object_name} /><figcaption>{a.origin}</figcaption></figure>)}</div>}
    </section>
  );
}
