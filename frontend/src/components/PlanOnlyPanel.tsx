import { useEffect, useRef } from "react";
import type { PlanOnlyResult } from "../types/api";
import { catText } from "../lib/cards";

export interface PlanOnlyState {
  busy: boolean;
  result: PlanOnlyResult | null;
  error: string | null;
  form: string;
  /** planKey (all plan-defining inputs) at the time the plan was made; a different key means the plan is outdated */
  key: string;
  /** selected card concepts as `${variant_id}${position}`, e.g. "A3" */
  selected: string[];
  regenBusy: boolean;
  regenError: string | null;
  /** concepts replaced by the last "Regenerate Selected" (highlighted) */
  changed: string[];
}

export const cardKey = (variantId: string, position: number) => `${variantId}${position}`;

interface Props {
  state: PlanOnlyState;
  /** the inputs changed after planning: the plan is kept for reference but Generate will not use it */
  stale: boolean;
  onClose: () => void;
  onToggle: (keys: string[], on: boolean) => void;
  onRegenerate: () => void;
}

/** Metaphor Planner: text-only concepts for a Set. The Producer curates them (select + Regenerate Selected) before paying for images. */
export function PlanOnlyPanel({ state, stale, onClose, onToggle, onRegenerate }: Props) {
  const r = state.result;
  const sel = new Set(state.selected);
  const busy = state.busy || state.regenBusy;
  const allKeys = r ? r.plan.variants.flatMap((v) => v.cards.map((c) => cardKey(v.variant_id, c.position))) : [];
  const allOn = allKeys.length > 0 && allKeys.every((k) => sel.has(k));
  return (
    <section className="bcard planonly" aria-label="Metaphor Planner">
      <div className="mys-head">
        <h3><span aria-hidden>🧠</span> Metaphor Planner <span className="chip">text only · no images generated</span></h3>
        <button type="button" className="ghost sm" onClick={onClose} aria-label="Close Metaphor Planner">Close ×</button>
      </div>
      {state.busy && <p className="hint"><i className="spinner sm" /> Planning metaphors…</p>}
      {state.error && <div className="alert err" role="alert">{state.error}</div>}
      {r && (
        <>
          {stale
            ? <div className="alert warn compact" role="status">Collection, Set or planning settings changed after this plan was made, so Generate will <strong>not</strong> use it. Click <strong>Plan Metaphors</strong> again to plan the new inputs.</div>
            : <p className="hint ok-note">✓ Generate Sets will use this plan, including any replaced concepts. Change the Collection, Set or settings and it must be planned again.</p>}
          <p className="hint">
            {state.form} · text model: <strong>{r.text_model ?? "n/a"}</strong>
            {" · "}{r.cached ? "reused a cached plan (free)" : `text cost ≈ $${r.text_cost_estimate_usd.toFixed(3)} (last call)`}
            {r.history_subjects_used > 0 && <> · {r.history_subjects_used} earlier subject(s) passed to avoid repeats</>}
            {" · "}image provider called: <strong>{r.image_provider_called ? "yes" : "no"}</strong>
          </p>
          <div className="po-toolbar">
            <button type="button" className="primary regen" onClick={onRegenerate} disabled={busy || stale || sel.size === 0}
              title="Asks the text model for new concepts for the selected cards only. The other cards, their numbers, order and categories stay exactly as they are. No images are generated.">
              {state.regenBusy ? <><i className="spinner sm" /> Regenerating…</> : <>↻ Regenerate Selected{sel.size > 0 ? ` (${sel.size})` : ""}</>}
            </button>
            <button type="button" className="ghost sm" onClick={() => onToggle(allKeys, !allOn)} disabled={busy}>{allOn ? "Deselect all" : "Select all"}</button>
            <span className="muted">{sel.size === 0 ? "Tick the concepts you want replaced." : `${sel.size} of ${allKeys.length} selected`}</span>
          </div>
          {state.regenError && <div className="alert err" role="alert">{state.regenError} Your plan is unchanged.</div>}
          {r.plan.variants.map((v) => {
            const keys = v.cards.map((c) => cardKey(v.variant_id, c.position));
            return (
              <div key={v.variant_id} className="po-variant">
                <p className="vconcept"><strong>Variant {v.variant_id}</strong> · <em>{v.title}</em> · {v.concept}</p>
                <table className="gridtbl po-table">
                  <thead><tr>
                    <th className="po-check"><HeaderCheck checked={keys.every((k) => sel.has(k))} some={keys.some((k) => sel.has(k))} disabled={busy}
                      label={`Select all cards of Variant ${v.variant_id}`} onChange={(on) => onToggle(keys, on)} /></th>
                    <th>#</th><th>Cat.</th><th>Subject</th><th>Description</th></tr></thead>
                  <tbody>
                    {v.cards.map((c) => {
                      const k = cardKey(v.variant_id, c.position);
                      const chars = (c as { characters?: number | null }).characters;
                      return (
                        <tr key={c.position} className={`${sel.has(k) ? "po-sel" : ""} ${state.changed.includes(k) ? "po-new" : ""}`}>
                          <td className="po-check"><input type="checkbox" checked={sel.has(k)} disabled={busy} onChange={(e) => onToggle([k], e.target.checked)}
                            aria-label={`Select card ${v.variant_id}${c.position}: ${c.object_name}`} /></td>
                          <td>{c.position}</td><td className={c.category === 5 ? "goldtxt" : ""}>{catText(c.category)}</td>
                          <td><strong>{c.object_name}</strong>{state.changed.includes(k) && <span className="chip new-chip">new</span>}</td>
                          <td className="muted">{c.object_description}{chars ? ` (${chars} character${chars === 1 ? "" : "s"})` : ""}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            );
          })}
          {r.plan.notes.map((n) => <p key={n} className="hint">{n}</p>)}
        </>
      )}
    </section>
  );
}

function HeaderCheck({ checked, some, disabled, label, onChange }: { checked: boolean; some: boolean; disabled: boolean; label: string; onChange: (on: boolean) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = some && !checked; }, [some, checked]);
  return <input ref={ref} type="checkbox" checked={checked} disabled={disabled} aria-label={label} title={checked ? "Deselect all" : "Select all"} onChange={(e) => onChange(e.target.checked)} />;
}
