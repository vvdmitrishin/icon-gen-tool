import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { ExportRecord, JobCard, JobDetail, JobEvent, JobPreview, JobSummary, JobVariant, QAReport } from "../types/api";
import { CheckList, QABadge, ReviewBadge } from "./badges";
import { CompareView } from "./CompareView";
import { PromptLibrary } from "./PromptLibrary";

type Tab = "results" | "variants" | "plans" | "qa" | "compare" | "library" | "log";

interface Props {
  job: JobDetail | null;
  preview: JobPreview | null;
  previewError: string | null;
  events: JobEvent[];
  jobs: JobSummary[];
  error: string | null;
  starting: boolean;
  perImage: number;
  onPickJob: (id: string) => void;
  onContinue: (retryFailed: boolean) => void;
  continueArmed: boolean;
  onCancelContinue: () => void;
  onCancelJob: () => void;
  onNew: () => void;
  onJob: (d: JobDetail) => void;
  onError: (m: string | null) => void;
}

const STATUS_TEXT: Record<string, string> = {
  created: "Created", planning: "Planning", plan_qa: "Plan QA", queued: "Queued", generating: "Generating",
  image_qa: "Technical QA", set_qa: "Set QA", ready_for_review: "Ready for review",
  partial_success: "Partial (some cards missing)", failed: "Failed", cancel_requested: "Cancelling…",
  cancelled: "Cancelled", interrupted: "Interrupted (server restarted)",
};
const msg = (e: unknown, f: string) => (e instanceof ApiError ? `${e.message}${e.code ? ` [${e.code}]` : ""}` : f);

export function SetPanel(p: Props) {
  const [tab, setTab] = useState<Tab>("results");
  const [pickedId, setPickedId] = useState<string | null>(null);
  const [retryFailed, setRetryFailed] = useState(false);
  const job = p.job;
  const pct = job && job.counts.total ? Math.round((job.counts.done / job.counts.total) * 100) : 0;
  const picked = job?.variants.flatMap((v) => v.cards).find((c) => c.card_id === pickedId) ?? null;
  const pickedVariant = job?.variants.find((v) => v.cards.some((c) => c.card_id === pickedId));

  useEffect(() => { setPickedId(null); }, [job?.job_id]);

  const TABS: [Tab, string][] = [["results", "Results"], ["variants", "Compare Variants"], ["plans", "Set Plans (JSON)"],
    ["qa", "QA Report"], ["compare", "Reference Compare"], ["library", "Prompt Library"], ["log", "Generation Log"]];

  return (
    <section className="results">
      <div className="steps" aria-label="Pipeline status">
        <Step n={1} title="Plan" sub={job ? `${job.variants.length} variant(s) · rules planner` : p.preview ? "Preview ready" : "Fill the form"} done={!!job || !!p.preview} />
        <Step n={2} title="Generate" sub={job ? `${job.counts.done}/${job.counts.total} images` : "Not started"} active={!!job?.active} done={!!job && !job.active && job.counts.done > 0} />
        <Step n={3} title="QA" sub={job && job.counts.done ? `Technical: ${job.variants.flatMap((v) => v.cards).filter((c) => c.qa_status === "passed").length} passed` : "Runs after generation"} done={!!job && !job.active && job.counts.done > 0} />
        <Step n={4} title="Results" sub={job?.approved_variant_id ? `Variant ${job.approved_variant_id} approved` : "Art review: pending"} done={!!job?.approved_variant_id} />
      </div>

      <div className="tabs" role="tablist">
        {TABS.map(([t, label]) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{label}</button>
        ))}
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
                <span className={`chip ${job.status === "ready_for_review" ? "ok" : job.status === "failed" || job.status === "partial_success" ? "warn" : ""}`}>{STATUS_TEXT[job.status] ?? job.status}</span>
                <span className="chip">{job.scope === "pilot" ? "Pilot scope" : "Full scope"}</span>
                <span className="chip">Cat. split {[1, 2, 3, 4].map((c) => job.distribution[c]).join(" / ")}</span>
                <span className="chip">Est. spent ${job.spent_estimated_usd.toFixed(3)}</span>
                {!job.active && <button type="button" className="ghost" onClick={p.onNew}>＋ New plan</button>}
              </div>
              <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><div style={{ width: `${pct}%` }} /></div>
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
                        ? `Confirm & spend ≈ $${(job.pending_estimate_usd + (retryFailed ? job.counts.failed * p.perImage : 0)).toFixed(3)}`
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

          {job && job.variants.map((v) => (
            <VariantBlock key={v.variant_id} job={job} v={v} pickedId={pickedId} onPick={setPickedId} onJob={p.onJob} onError={p.onError} />
          ))}

          {!job && p.preview && (
            <>
              <p className="hint plan-hint">Plan preview (nothing generated yet) — {p.preview.images_in_scope} image(s) in {p.preview.scope} scope.</p>
              {p.preview.plan.variants.map((v) => (
                <PlanRow key={v.variant_id} id={v.variant_id} title={v.title} concept={v.concept} cards={v.cards.map((c) => ({ key: `${v.variant_id}${c.position}`, cat: c.category, name: c.object_name }))} />
              ))}
            </>
          )}

          {job && picked && pickedVariant && (
            <CardDetail job={job} card={picked} variant={pickedVariant} perImage={p.perImage} onJob={p.onJob} onError={p.onError}
              onClose={() => setPickedId(null)} onCompare={() => setTab("compare")} />
          )}
        </div>
      )}

      {tab === "variants" && (
        <div className="stage">
          {job ? <CompareVariants job={job} onPick={(id) => { setPickedId(id); setTab("results"); }} /> : <div className="empty">Generate a set first.</div>}
        </div>
      )}

      {tab === "plans" && (
        <div className="stage">
          {job || p.preview ? (
            <pre className="code">{JSON.stringify(job ? { job_id: job.job_id, seed: job.seed, distribution: job.distribution, notes: job.notes, variants: job.variants.map((v) => ({ variant_id: v.variant_id, title: v.title, concept: v.concept, cards: v.cards.map((c) => ({ position: c.position, category: c.category, object_name: c.object_name, object_description: c.object_description, status: c.status, spec_version: c.spec_version })) })) } : p.preview!.plan, null, 2)}</pre>
          ) : <div className="empty">No plan yet.</div>}
        </div>
      )}

      {tab === "qa" && <div className="stage">{job ? <QAPanel job={job} /> : <div className="empty">Generate a set first.</div>}</div>}
      {tab === "compare" && <div className="stage">{job ? <CompareView job={job} focusCardId={pickedId} /> : <div className="empty">Generate a set first.</div>}</div>}
      {tab === "library" && <div className="stage"><PromptLibrary /></div>}

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

// ------------------------------------------------------------------------------- variant block
function VariantBlock({ job, v, pickedId, onPick, onJob, onError }: {
  job: JobDetail; v: JobVariant; pickedId: string | null; onPick: (id: string) => void;
  onJob: (d: JobDetail) => void; onError: (m: string | null) => void;
}) {
  const [showQA, setShowQA] = useState(false);
  const [exp, setExp] = useState<{ rec?: ExportRecord; err?: string; retry?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const qa = v.set_qa;
  const canApprove = !job.active && qa.generated === 10 && qa.status !== "failed" && qa.art_review.rejected === 0;

  async function approve() {
    setBusy(true);
    try { onJob(await (v.approved ? api.unapproveVariant(job.job_id, v.variant_id) : api.approveVariant(job.job_id, v.variant_id))); onError(null); }
    catch (e) { onError(msg(e, "Action failed")); } finally { setBusy(false); }
  }
  async function doExport(force: boolean) {
    setBusy(true);
    try { setExp({ rec: await api.exportVariant(job.job_id, v.variant_id, force ? { include_unapproved: true, allow_mock: true } : {}) }); }
    catch (e) {
      const m = msg(e, "Export failed");
      setExp({ err: m, retry: /not approved|MOCK/.test(m) && !/incomplete|rejected|set QA failed/.test(m) });
    } finally { setBusy(false); }
  }

  return (
    <div className={`variant ${v.approved ? "approved" : ""}`}>
      <div className="vhead">
        <strong>Variant {v.variant_id}</strong> <span>{v.title}</span><small>{v.concept}</small>
        {v.approved && <span className="chip ok">★ final set</span>}
      </div>
      <div className="vtools">
        <button type="button" className={`qa qa-${qa.status}`} onClick={() => setShowQA(!showQA)} aria-expanded={showQA}>Set QA: {qa.status} ▾</button>
        <span className="chip">{qa.generated}/10 generated</span>
        <span className="chip">review: {qa.art_review.approved} ✓ · {qa.art_review.pending} pending · {qa.art_review.rejected} ✕</span>
        <button type="button" className={`ghost ${v.approved ? "on" : ""}`} disabled={busy || (!v.approved && !canApprove)} onClick={approve}
          title={canApprove || v.approved ? "" : "Needs 10 generated cards, no failed set QA and no rejected cards"}>
          {v.approved ? "Withdraw approval" : "Approve variant"}
        </button>
        <button type="button" className="ghost" disabled={busy || qa.generated < 10 || job.active} onClick={() => doExport(false)}>Export ZIP</button>
      </div>
      {showQA && <CheckList checks={qa.checks} />}
      {exp?.rec && <div className="alert ok">Export ready: <a href={exp.rec.download_url} download={exp.rec.filename}>{exp.rec.filename}</a> ({(exp.rec.size / 1024).toFixed(0)} KB){exp.rec.contains_mock_images && " · contains MOCK images"}</div>}
      {exp?.err && (
        <div className="alert warn">{exp.err}
          {exp.retry && <> <button type="button" className="ghost" onClick={() => doExport(true)}>Export anyway (unapproved / mock, for testing)</button></>}
        </div>
      )}
      <ol className="slots">
        {v.cards.map((c) => (
          <li key={c.card_id} className={`slot ${c.status} ${pickedId === c.card_id ? "on" : ""}`}>
            <button type="button" onClick={() => onPick(c.card_id)} title={c.error ?? c.object_description}>
              {c.image_url ? <img src={c.image_url} alt={c.object_name} /> : (
                <span className="ph">{c.status === "generating" ? <i className="spinner sm" /> : c.status === "failed" ? "!" : c.status === "queued" ? "…" : ""}</span>
              )}
              <span className="cat">{c.category}</span>
              {c.image_url && <span className={`dot qa-${c.qa_status}`} aria-label={`QA ${c.qa_status}`} />}
              {c.art_review === "approved" && <span className="rvmark ok">✓</span>}
              {c.art_review === "rejected" && <span className="rvmark bad">✕</span>}
              {c.versions.length > 1 && <span className="vmark">v{c.versions.find((x) => x.is_current)?.version}</span>}
            </button>
            <small>{c.object_name}</small>
          </li>
        ))}
      </ol>
    </div>
  );
}

function PlanRow({ id, title, concept, cards }: { id: string; title: string; concept: string; cards: { key: string; cat: number; name: string }[] }) {
  return (
    <div className="variant">
      <div className="vhead"><strong>Variant {id}</strong> <span>{title}</span><small>{concept}</small></div>
      <ol className="slots">
        {cards.map((c) => (
          <li key={c.key} className="slot planned"><button type="button" disabled><span className="ph" /><span className="cat">{c.cat}</span></button><small>{c.name}</small></li>
        ))}
      </ol>
    </div>
  );
}

// -------------------------------------------------------------------------------- card detail
function CardDetail({ job, card, variant, perImage, onJob, onError, onClose, onCompare }: {
  job: JobDetail; card: JobCard; variant: JobVariant; perImage: number;
  onJob: (d: JobDetail) => void; onError: (m: string | null) => void; onClose: () => void; onCompare: () => void;
}) {
  const [note, setNote] = useState("");
  const [armed, setArmed] = useState(false);
  const [replace, setReplace] = useState(false);
  const [rName, setRName] = useState(card.object_name);
  const [rDesc, setRDesc] = useState(card.object_description);
  const [viewV, setViewV] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const paid = job.request.provider !== "mock" && perImage > 0;
  const hasImage = !!card.image_url;
  const locked = job.active || busy;
  const shownVersion = viewV != null ? card.versions.find((v) => v.version === viewV) : undefined;
  const img = shownVersion?.image_url ?? card.image_url;

  useEffect(() => { setViewV(null); setArmed(false); setReplace(false); setRName(card.object_name); setRDesc(card.object_description); setNote(""); }, [card.card_id, card.spec_version]);
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
  const noRegenLeft = hasImage && card.regen_remaining <= 0;
  const label = !hasImage ? "Generate this card" : "Regenerate";

  return (
    <div className="pickdetail">
      <div>
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
      <div>
        <h3>{card.object_name}</h3>
        <p className="hint">{card.object_description}</p>
        <div className="chips">
          <span className="chip">Variant {variant.variant_id} · #{card.position}</span><span className="chip">Cat. {card.category}</span>
          <QABadge status={card.qa_status} /><ReviewBadge review={card.art_review} />
          <span className="chip">refs: {card.reference_selection ?? "none"}</span>
        </div>
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
          <button type="button" className={`primary small ${armed ? "armed" : ""}`} disabled={locked || noRegenLeft || card.status === "queued" || card.status === "generating"} onClick={regenerate}>
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
          <button type="button" className="ghost" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------ compare variants
function CompareVariants({ job, onPick }: { job: JobDetail; onPick: (cardId: string) => void }) {
  const positions = Array.from({ length: 10 }, (_, i) => i + 1);
  return (
    <>
      <p className="hint">Same scale, same position in every variant. Click a thumbnail to open the card.</p>
      <table className="gridtbl varcmp">
        <thead><tr><th>#</th>{job.variants.map((v) => <th key={v.variant_id}>Variant {v.variant_id}<br /><small>{v.title}</small></th>)}</tr></thead>
        <tbody>
          {positions.map((pos) => (
            <tr key={pos}>
              <td>{pos}<br /><small>Cat {job.variants[0]?.cards.find((c) => c.position === pos)?.category}</small></td>
              {job.variants.map((v) => {
                const c = v.cards.find((x) => x.position === pos);
                return <td key={v.variant_id}>{c?.image_url ? <button type="button" onClick={() => onPick(c.card_id)}><img src={c.image_url} alt={c.object_name} /><small>{c.object_name}</small></button> : <small className="hint">{c?.object_name ?? "–"}<br />{c?.status}</small>}</td>;
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
        <button type="button" className="ghost" disabled={busy || job.active} onClick={rerun}>Re-run QA</button>
      </div>
      <p className="hint">{rep.limitations}</p>
      {err && <div className="alert err">{err}</div>}
      {Object.values(rep.variants).map((v) => (
        <details key={v.variant_id} open={v.status !== "passed"}>
          <summary>Variant {v.variant_id} — set QA <QABadge status={v.status} /> · {v.generated}/{v.total} generated</summary>
          <CheckList checks={v.checks} />
        </details>
      ))}
      <details><summary>Per-card checks ({Object.keys(rep.cards).length})</summary>
        {Object.entries(rep.cards).map(([id, c]) => (
          <details key={id} className="sub"><summary>{c.object_name} · Cat {c.category} <QABadge status={c.status} /> <ReviewBadge review={c.art_review} /></summary><CheckList checks={c.checks} /></details>
        ))}
      </details>
      <CheckList checks={rep.job_checks} />
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
