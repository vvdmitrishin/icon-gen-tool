/** Shared pipeline strip (circle numbers, connecting line, ticks) used by the set and single-card screens. */
export interface PipeStep {
  key: string;
  title: string;
  sub: string;
  done?: boolean;
  active?: boolean;
  /** step the user is currently looking at */
  current?: boolean;
  /** shown with a dashed circle: nothing has to happen for the flow to continue */
  optional?: boolean;
  hint?: string;
  onClick?: () => void;
}

export function PipelineStrip({ steps }: { steps: PipeStep[] }) {
  return (
    <ol className="pipe" aria-label="Pipeline status">
      {steps.map((s, i) => {
        const cls = ["pstep", s.done ? "done" : "", s.active ? "active" : "", s.current ? "current" : "", s.optional ? "optional" : ""].filter(Boolean).join(" ");
        const body = (
          <>
            <span className="pnum" aria-hidden>{s.done ? "✓" : i + 1}</span>
            <span className="ptext"><strong>{s.title}</strong><small>{s.sub}</small></span>
          </>
        );
        return (
          <li key={s.key} className="pitem">
            {s.onClick ? (
              <button type="button" className={cls} onClick={s.onClick} title={s.hint ?? s.sub} aria-current={s.current ? "step" : undefined}>{body}</button>
            ) : (
              <div className={cls} title={s.hint ?? s.sub}>{body}</div>
            )}
            {i < steps.length - 1 && <span className="pline" aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}
