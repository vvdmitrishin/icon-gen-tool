import { useState } from "react";
import type { JobCard, JobDetail, JobEvent, JobPreview, JobSummary, JobVariant } from "../types/api";

type Tab = "results" | "plans" | "log";

interface Props {
  job: JobDetail | null;
  preview: JobPreview | null;
  previewError: string | null;
  events: JobEvent[];
  jobs: JobSummary[];
  error: string | null;
  starting: boolean;
  onPickJob: (id: string) => void;
  onContinue: (retryFailed: boolean) => void;
  continueArmed: boolean;
  onCancelContinue: () => void;
  onCancelJob: () => void;
  onNew: () => void;
}

const STATUS_TEXT: Record<string, string> = {
  created: "Created", planning: "Planning", plan_qa: "Plan QA", queued: "Queued", generating: "Generating",
  ready_for_review: "Ready for review", partial_success: "Partial (some cards missing)", failed: "Failed",
  cancel_requested: "Cancelling…", cancelled: "Cancelled", interrupted: "Interrupted (server restarted)",
};

export function SetPanel(p: Props) {
  const [tab, setTab] = useState<Tab>("results");
  const [picked, setPicked] = useState<JobCard | null>(null);
  const [retryFailed, setRetryFailed] = useState(false);
  const job = p.job;
  const pct = job && job.counts.total ? Math.round((job.counts.done / job.counts.total) * 100) : 0;

  return (
    <section className="results">
      <div className="steps" aria-label="Pipeline status">
        <Step n={1} title="Plan" sub={job ? `${job.variants.length} variant(s) · rules planner` : p.preview ? "Preview ready" : "Fill the form"} done={!!job || !!p.preview} />
        <Step n={2} title="Generate" sub={job ? `${job.counts.done}/${job.counts.total} images` : "Not started"} active={!!job?.active} done={!!job && !job.active && job.counts.done > 0} />
        <Step n={3} title="QA" sub="Technical QA: Phase 4" />
        <Step n={4} title="Results" sub="Art review: pending" done={!!job && job.counts.done > 0} />
      </div>

      <div className="tabs" role="tablist">
        {([["results", "Results"], ["plans", "Set Plans (JSON)"], ["log", "Generation Log"]] as [Tab, string][]).map(([t, label]) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{label}</button>
        ))}
        <button role="tab" disabled title="Phase 5">Reference Compare</button>
        <button role="tab" disabled title="Phase 5">Prompt Library</button>
        {p.jobs.length > 0 && (
          <select className="jobpick" aria-label="Recent jobs" value={job?.job_id ?? ""} onChange={(e) => e.target.value && p.onPickJob(e.target.value)}>
            <option value="">Recent jobs…</option>
            {p.jobs.map((j) => (
              <option key={j.job_id} value={j.job_id}>{j.genre} ×{j.variants} · {STATUS_TEXT[j.status] ?? j.status} · {new Date(j.created_at).toLocaleTimeString()}</option>
            ))}
          </select>
        )}
      </div>

      <div className="banner">
        <strong>This tool generates object illustrations for collectible cards (not full game windows).</strong>
        <span>Artwork only. Each variant is a full 10-card set; the category distribution is the same in every variant.</span>
      </div>

      {p.error && <div className="alert err" role="alert">{p.error}</div>}
      {p.previewError && !job && <div className="alert warn">{p.previewError}</div>}

      {tab === "results" && (
        <div className="stage">
          {job && (
            <div className="jobbar">
              <div className="jobhead">
                <strong>{job.genre}</strong>
                <span className={`chip ${job.status === "ready_for_review" ? "ok" : job.status === "failed" || job.status === "partial_success" ? "warn" : ""}`}>
                  {STATUS_TEXT[job.status] ?? job.status}
                </span>
                <span className="chip">{job.scope === "pilot" ? "Pilot scope" : "Full scope"}</span>
                <span className="chip">Cat. split {[1, 2, 3, 4].map((c) => job.distribution[c]).join(" / ")}</span>
                <span className="chip">Est. spent ${job.spent_estimated_usd.toFixed(3)}</span>
                {!job.active && <button type="button" className="ghost" onClick={p.onNew}>＋ New plan</button>}
              </div>
              <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                <div style={{ width: `${pct}%` }} />
              </div>
              <div className="jobcounts">
                Generated <strong>{job.counts.done}/{job.counts.total}</strong>
                {job.counts.generating + job.counts.queued > 0 && <> · in progress {job.counts.generating + job.counts.queued}</>}
                {job.counts.failed > 0 && <> · <span className="bad">failed {job.counts.failed}</span></>}
                {job.counts.planned > 0 && <> · planned (not generated) {job.counts.planned}</>}
              </div>
              {job.error && <div className="alert warn">{job.error}</div>}
              <div className="jobactions">
                {job.active && <button type="button" className="ghost" onClick={p.onCancelJob} disabled={job.status === "cancel_requested"}>Cancel</button>}
                {job.can_continue && (
                  <>
                    {job.counts.failed > 0 && (
                      <label className="check inline"><input type="checkbox" checked={retryFailed} onChange={(e) => setRetryFailed(e.target.checked)} /><span>also retry failed</span></label>
                    )}
                    <button type="button" className={`primary small ${p.continueArmed ? "armed" : ""}`} onClick={() => p.onContinue(retryFailed)}>
                      {p.continueArmed
                        ? `Confirm & spend ≈ $${(job.pending_estimate_usd + (retryFailed ? job.counts.failed * 0.067 : 0)).toFixed(3)}`
                        : `Continue: ${job.counts.planned}${retryFailed ? `+${job.counts.failed}` : ""} more card(s)${job.pending_estimate_usd > 0 ? ` · ≈ $${job.pending_estimate_usd.toFixed(3)}` : ""}`}
                    </button>
                    {p.continueArmed && <button type="button" className="ghost" onClick={p.onCancelContinue}>Cancel</button>}
                  </>
                )}
              </div>
            </div>
          )}

          {!job && !p.preview && !p.starting && (
            <div className="empty"><p>No set generated yet.</p><p className="hint">Choose a genre and variants; the plan preview appears here before anything is spent.</p></div>
          )}

          {job && job.variants.map((v) => <VariantRow key={v.variant_id} v={v} onPick={setPicked} pickedId={picked?.card_id} />)}

          {!job && p.preview && (
            <>
              <p className="hint plan-hint">Plan preview (nothing generated yet) — {p.preview.images_in_scope} image(s) in {p.preview.scope} scope.</p>
              {p.preview.plan.variants.map((v) => (
                <VariantRow key={v.variant_id} onPick={() => undefined}
                  v={{ ...v, cards: v.cards.map((c) => ({ ...c, card_id: `${v.variant_id}${c.position}`, status: "planned" as const, artifact_id: null, error: null, reference_selection: null, image_url: null })) }} />
              ))}
            </>
          )}

          {picked && picked.image_url && (
            <div className="pickdetail">
              <img src={picked.image_url} alt={picked.object_name} />
              <div>
                <h3>{picked.object_name}</h3>
                <p className="hint">{picked.object_description}</p>
                <div className="chips"><span className="chip">Cat. {picked.category}</span><span className="chip">refs: {picked.reference_selection ?? "none"}</span></div>
                {picked.artifact_id && <a className="ghost link" href={`/api/artifacts/${picked.artifact_id}`} target="_blank" rel="noreferrer">Provenance JSON</a>}
                <a className="ghost link" href={picked.image_url} download={`${picked.card_id}.png`}>Download PNG</a>
                <button type="button" className="ghost" onClick={() => setPicked(null)}>Close</button>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "plans" && (
        <div className="stage">
          {job || p.preview ? (
            <pre className="code">{JSON.stringify(job ? { job_id: job.job_id, seed: job.seed, distribution: job.distribution, notes: job.notes, variants: job.variants.map((v) => ({ ...v, cards: v.cards.map(({ image_url: _i, ...c }) => c) })) } : p.preview!.plan, null, 2)}</pre>
          ) : <div className="empty">No plan yet.</div>}
        </div>
      )}

      {tab === "log" && (
        <div className="stage">
          {p.events.length ? (
            <ul className="log">{p.events.map((e) => <li key={e.id} className={e.level}><time>{new Date(e.ts).toLocaleTimeString()}</time> {e.message}</li>)}</ul>
          ) : <div className="empty">No events yet.</div>}
        </div>
      )}
    </section>
  );
}

function VariantRow({ v, onPick, pickedId }: { v: JobVariant; onPick: (c: JobCard) => void; pickedId?: string }) {
  return (
    <div className="variant">
      <div className="vhead"><strong>Variant {v.variant_id}</strong> <span>{v.title}</span><small>{v.concept}</small></div>
      <ol className="slots">
        {v.cards.map((c) => (
          <li key={c.card_id} className={`slot ${c.status} ${pickedId === c.card_id ? "on" : ""}`}>
            <button type="button" disabled={!c.image_url} onClick={() => onPick(c)} title={c.error ?? c.object_description}>
              {c.image_url ? <img src={c.image_url} alt={c.object_name} /> : (
                <span className="ph">{c.status === "generating" ? <i className="spinner sm" /> : c.status === "failed" ? "!" : c.status === "queued" ? "…" : ""}</span>
              )}
              <span className="cat">{c.category}</span>
            </button>
            <small>{c.object_name}</small>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Step(p: { n: number; title: string; sub: string; done?: boolean; active?: boolean }) {
  return (
    <div className={`step ${p.done ? "done" : ""} ${p.active ? "active" : ""}`}>
      <span className="num">{p.done ? "✓" : p.n}</span>
      <div><strong>{p.title}</strong><small>{p.sub}</small></div>
    </div>
  );
}
