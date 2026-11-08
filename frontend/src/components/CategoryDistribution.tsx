import { CATS, type Cat, type DistCounts } from "../lib/cards";

export type { DistCounts };
export type DistMode = "auto_balanced" | "custom";

/** Mirrors the backend rules; the server stays authoritative. `standard` = number_of_cards - gold_cards. */
export function distributionErrors(counts: DistCounts, standard: number): string[] {
  const errs: string[] = [];
  if (standard >= 4) {
    const empty = CATS.filter((c) => counts[c] < 1);
    if (empty.length) errs.push(`Each of Categories 1–4 needs at least 1 card (empty: ${empty.join(", ")}).`);
  }
  const sum = CATS.reduce((s, c) => s + counts[c], 0);
  if (sum !== standard) errs.push(`Standard categories must total ${standard} card${standard === 1 ? "" : "s"} (currently ${sum}).`);
  return errs;
}

const TIPS: Record<Cat | "gold", { title: string; text: string }> = {
  "1": { title: "Cat. 1", text: "Floating object on a simple, clean background. One clear hero object, no scene." },
  "2": { title: "Cat. 2", text: "Object resting on a surface (shelf/table), minimal background, soft shadow." },
  "3": { title: "Cat. 3", text: "Object on a realistic surface with a few supporting props; slightly richer context, still object-focused." },
  "4": { title: "Cat. 4", text: "Object inside a small environment/scene that tells a story; bright, casual, readable." },
  gold: { title: "★ Gold (Cat. 5)", text: "Most valuable card: key characters of the collection in an ironic, emotional situation. Max 2 characters." },
};

/** Rich tooltip: shows on hover and on keyboard focus of the ⓘ button. */
function InfoTip({ id, tip, align }: { id: string; tip: { title: string; text: string }; align: "left" | "center" | "right" }) {
  return (
    <span className={`tip tip-${align}`}>
      <button type="button" className="tip-btn" aria-label={`About ${tip.title}`} aria-describedby={id}>ⓘ</button>
      <span role="tooltip" id={id} className="tip-bubble"><strong>{tip.title}</strong>{tip.text}</span>
    </span>
  );
}

interface Props {
  mode: DistMode;
  /** custom counts (Cat. 1-4) */
  counts: DistCounts;
  /** what Auto shows: the backend's distribution when a preview exists, otherwise the client-side balanced split */
  autoCounts: DistCounts;
  standard: number;
  gold: number;
  onMode: (m: DistMode) => void;
  onCount: (cat: Cat, v: number) => void;
  /** refit custom counts to the standard-card total (explicit user action; values are never changed silently) */
  onAutoFit?: () => void;
  /** backend flags (optional; computed locally when absent) */
  compliant?: boolean | null;
  complianceNote?: string | null;
}

/** Playrix rule of thumb mirrored client-side: at least 4 standard cards and every Cat. 1-4 has >= 1. */
export function localCompliance(counts: DistCounts, standard: number): { ok: boolean; note: string } {
  const sum = CATS.reduce((s, c) => s + counts[c], 0);
  if (standard < 4) return { ok: false, note: "fewer than 4 standard cards, so not every category can be covered" };
  const empty = CATS.filter((c) => counts[c] < 1);
  if (empty.length) return { ok: false, note: `Cat. ${empty.join(", ")} has no cards` };
  if (sum !== standard) return { ok: false, note: "counts do not add up yet" };
  return { ok: true, note: "" };
}

/** Compact "Card categories" control: cards per category 1-4 plus the ★ Gold slot driven by the golden-cards slider. */
export function CategoryDistribution({ mode, counts, autoCounts, standard, gold, onMode, onCount, onAutoFit, compliant, complianceNote }: Props) {
  const shown = mode === "auto_balanced" ? autoCounts : counts;
  const sum = CATS.reduce((s, c) => s + shown[c], 0);
  const errors = mode === "custom" ? distributionErrors(counts, standard) : [];
  const editable = mode === "custom";
  const local = localCompliance(shown, standard);
  const isOk = compliant ?? local.ok;
  const note = complianceNote || local.note || "not all 4 standard categories are covered";
  const help =
    (mode === "auto_balanced" ? "Auto: balanced split of the standard cards (2:2:3:3 for 10). " : "Producer-defined counts. ") +
    "Applied identically in every variant. Separate from rarity / stars.";

  return (
    <div className="dist">
      <div className="dist-head">
        <span className="lbl" title={help}>Card categories <span className="info" aria-hidden>ⓘ</span></span>
        <div className="seg sm" role="radiogroup" aria-label="Distribution mode">
          {(["auto_balanced", "custom"] as DistMode[]).map((m) => (
            <button key={m} type="button" role="radio" aria-checked={mode === m} className={mode === m ? "on" : ""} onClick={() => onMode(m)}>
              {m === "auto_balanced" ? "Auto" : "Custom"}
            </button>
          ))}
        </div>
      </div>
      <div className="steppers">
        {CATS.map((c, i) => (
          <div key={c} className={`stepper cat${c} ${editable ? "" : "ro"}`}>
            <span className="slbl">Cat. {c}<InfoTip id={`tip-cat${c}`} tip={TIPS[c]} align={i <= 1 ? "left" : i === 2 ? "center" : "right"} /></span>
            <div>
              <button type="button" aria-label={`Decrease category ${c}`} disabled={!editable || shown[c] <= 0} onClick={() => onCount(c, shown[c] - 1)}>−</button>
              <output aria-label={`Category ${c} count`}>{shown[c]}</output>
              <button type="button" aria-label={`Increase category ${c}`} disabled={!editable || shown[c] >= standard} onClick={() => onCount(c, shown[c] + 1)}>+</button>
            </div>
          </div>
        ))}
        <div className="stepper ro cat5">
          <span className="slbl">★ Gold<InfoTip id="tip-gold" tip={TIPS.gold} align="right" /></span>
          <div><output aria-label="Golden cards count">{gold}</output></div>
        </div>
      </div>
      <div className="dist-foot">
        <span className={`sum ${errors.length || sum !== standard ? "bad" : "ok"}`}>Σ = {sum} / {standard}</span>
        {editable && sum !== standard && onAutoFit && <button type="button" className="ghost sm" onClick={onAutoFit}>Auto-fit to {standard}</button>}
        <span className="hint">Standard cards (Cat. 1–4){gold > 0 ? ` + ${gold} ★ gold` : ""}</span>
      </div>
      {errors.map((e) => <div key={e} className="alert err" role="alert">{e}</div>)}
      {errors.length > 0 && <p className="hint">Generate is blocked until the counts add up. Nothing was changed automatically.</p>}
      <div className="compl" role="status">
        {isOk
          ? <span className="chip ok">Playrix-compliant: all 4 standard categories covered</span>
          : <span className="chip warn">Custom / non-compliant set: {note}</span>}
      </div>
    </div>
  );
}
