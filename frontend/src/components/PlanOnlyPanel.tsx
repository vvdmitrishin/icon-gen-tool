import type { PlanOnlyResult } from "../types/api";
import { catText } from "../lib/cards";

export interface PlanOnlyState { busy: boolean; result: PlanOnlyResult | null; error: string | null; form: string }

/** Result of "Test planner (no images)": what the semantic planner understood, with its model and text cost. */
export function PlanOnlyPanel({ state, onClose }: { state: PlanOnlyState; onClose: () => void }) {
  const r = state.result;
  return (
    <section className="bcard planonly" aria-label="Planner test">
      <div className="mys-head">
        <h3><span aria-hidden>🧪</span> Planner test <span className="chip">no images generated</span></h3>
        <button type="button" className="ghost sm" onClick={onClose} aria-label="Close planner test">Close ×</button>
      </div>
      {state.busy && <p className="hint"><i className="spinner sm" /> Asking the planner…</p>}
      {state.error && <div className="alert err" role="alert">{state.error}</div>}
      {r && (
        <>
          <p className="hint">
            {state.form} · text model: <strong>{r.text_model ?? "n/a"}</strong>
            {" · "}{r.cached ? "reused a cached plan (free)" : `text cost ≈ $${r.text_cost_estimate_usd.toFixed(3)}`}
            {r.history_subjects_used > 0 && <> · {r.history_subjects_used} earlier subject(s) passed to avoid repeats</>}
            {" · "}image provider called: <strong>{r.image_provider_called ? "yes" : "no"}</strong>
          </p>
          {r.plan.variants.map((v) => (
            <div key={v.variant_id} className="po-variant">
              <p className="vconcept"><strong>Variant {v.variant_id}</strong> · <em>{v.title}</em> · {v.concept}</p>
              <table className="gridtbl">
                <thead><tr><th>#</th><th>Cat.</th><th>Subject</th><th>Description</th></tr></thead>
                <tbody>
                  {v.cards.map((c) => (
                    <tr key={c.position}><td>{c.position}</td><td className={c.category === 5 ? "goldtxt" : ""}>{catText(c.category)}</td>
                      <td><strong>{c.object_name}</strong></td><td className="muted">{c.object_description}{(c as { characters?: number | null }).characters ? ` (${(c as { characters?: number }).characters} character${(c as { characters?: number }).characters === 1 ? "" : "s"})` : ""}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          {r.plan.notes.map((n) => <p key={n} className="hint">{n}</p>)}
        </>
      )}
    </section>
  );
}
