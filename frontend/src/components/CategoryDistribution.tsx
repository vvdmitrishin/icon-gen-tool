export type DistMode = "auto_balanced" | "custom";
export type DistCounts = Record<"1" | "2" | "3" | "4", number>;

const CATS = ["1", "2", "3", "4"] as const;
export const SET_SIZE = 10;

/** Mirrors the backend rules (spec §3.4); the server stays authoritative. */
export function distributionErrors(counts: DistCounts): string[] {
  const errs: string[] = [];
  const empty = CATS.filter((c) => counts[c] < 1);
  if (empty.length) errs.push(`Each of Categories 1–4 needs at least 1 card (empty: ${empty.join(", ")}).`);
  const sum = CATS.reduce((s, c) => s + counts[c], 0);
  if (sum !== SET_SIZE) errs.push(`Counts must total ${SET_SIZE} cards (currently ${sum}).`);
  return errs;
}

interface Props {
  mode: DistMode;
  counts: DistCounts;
  defaults: DistCounts;
  onMode: (m: DistMode) => void;
  onCount: (cat: "1" | "2" | "3" | "4", v: number) => void;
  appliedToSets?: boolean;
}

export function CategoryDistribution({ mode, counts, defaults, onMode, onCount, appliedToSets }: Props) {
  const shown = mode === "auto_balanced" ? defaults : counts;
  const sum = CATS.reduce((s, c) => s + shown[c], 0);
  const errors = mode === "custom" ? distributionErrors(counts) : [];
  const editable = mode === "custom";

  return (
    <div className="dist">
      <span className="lbl">Card Category Distribution</span>
      <div className="seg" role="radiogroup" aria-label="Distribution mode">
        {(["auto_balanced", "custom"] as DistMode[]).map((m) => (
          <button key={m} type="button" role="radio" aria-checked={mode === m}
                  className={mode === m ? "on" : ""} onClick={() => onMode(m)}>
            {m === "auto_balanced" ? "Auto (Balanced)" : "Custom"}
          </button>
        ))}
      </div>
      <div className="steppers">
        {CATS.map((c) => (
          <div key={c} className={`stepper ${editable ? "" : "ro"}`}>
            <span>Cat. {c}</span>
            <div>
              <button type="button" aria-label={`Decrease category ${c}`} disabled={!editable || shown[c] <= 0}
                      onClick={() => onCount(c, shown[c] - 1)}>−</button>
              <output aria-label={`Category ${c} count`}>{shown[c]}</output>
              <button type="button" aria-label={`Increase category ${c}`} disabled={!editable || shown[c] >= SET_SIZE}
                      onClick={() => onCount(c, shown[c] + 1)}>+</button>
            </div>
          </div>
        ))}
        <div className="stepper ro locked" title="Golden cards are locked to 0 for the Movie Collection pilot">
          <span>Cat. 5</span><div><output aria-label="Category 5 count">0</output><small>locked</small></div>
        </div>
      </div>
      <div className={`sum ${errors.length || sum !== SET_SIZE ? "bad" : "ok"}`}>Σ = {sum} / {SET_SIZE}</div>
      {errors.map((e) => <div key={e} className="alert err" role="alert">{e}</div>)}
      <p className="hint">
        {mode === "auto_balanced"
          ? "Default [2,2,3,3] is our design assumption, not a Playrix-mandated ratio. "
          : "Producer-defined counts. "}
        {appliedToSets
          ? "Applied when the planner assigns objects to category slots (identical in every variant). "
          : "Used by card sets; single-card mode uses the category chosen in section 2. "}
        Separate from rarity / stars.
      </p>
    </div>
  );
}
