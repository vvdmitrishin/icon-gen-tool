export interface PlanError { code: string | null; message: string }

interface Props {
  error: PlanError;
  planning: boolean;
  onPlanWithAi: () => void;
  onConnectGemini: () => void;
  compact?: boolean;
}

/** Plain-English wording for known error codes; the server message is appended when it adds detail. */
const FRIENDLY: Record<string, string> = {
  paid_disabled: "Paid image generation is switched off in this app, so nothing can be generated with a paid model. Pick a free or demo model, or ask the person who runs the app to enable paid calls.",
  budget_exceeded: "This would go over the spending limit set for this app (local estimate). Reduce the number of cards or variants, or pick a cheaper model.",
};

/** Explains a failed free plan preview: custom theme needs an AI plan, or the Gemini key is missing. */
export function PlanNotice({ error, planning, onPlanWithAi, onConnectGemini, compact }: Props) {
  if (error.code === "ai_plan_required") {
    return (
      <div className={`plannotice ${compact ? "compact" : ""}`} role="status">
        <p>
          <span aria-hidden>✦</span> <strong>Custom theme.</strong> The AI chooses the objects for this Collection and Set first, so you can review them before any image is made.
          {" "}<b>Planning (AI text): ≈ $0.002, one time.</b> Images are paid separately, only when you click Generate.
        </p>
        <button type="button" className="primary small" disabled={planning} onClick={onPlanWithAi}>{planning ? "Planning…" : "Plan with AI"}</button>
      </div>
    );
  }
  if (error.code === "gemini_key_missing") {
    return (
      <div className={`alert warn ${compact ? "compact" : ""} plan-key`} role="alert">
        <span>Planning a custom theme needs a Gemini key, and none is connected. {error.message}</span>
        <button type="button" className="primary small" onClick={onConnectGemini}>Connect Gemini</button>
      </div>
    );
  }
  const friendly = error.code ? FRIENDLY[error.code] : undefined;
  return (
    <div className={`alert warn ${compact ? "compact" : ""}`} role="alert">
      {friendly ? <>{friendly} <small>({error.message})</small></> : error.message}
    </div>
  );
}
