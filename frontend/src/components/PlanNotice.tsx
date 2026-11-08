export interface PlanError { code: string | null; message: string }

interface Props {
  error: PlanError;
  planning: boolean;
  onPlanWithAi: () => void;
  onConnectGemini: () => void;
  compact?: boolean;
}

/** Explains a failed free plan preview: custom theme needs an AI plan, or the Gemini key is missing. */
export function PlanNotice({ error, planning, onPlanWithAi, onConnectGemini, compact }: Props) {
  if (error.code === "ai_plan_required") {
    return (
      <div className={`plannotice ${compact ? "compact" : ""}`} role="status">
        <p><span aria-hidden>✦</span> Custom theme: the AI will plan the objects (one small Gemini text call, about $0.002).</p>
        <button type="button" className="primary small" disabled={planning} onClick={onPlanWithAi}>{planning ? "Planning…" : "Plan with AI"}</button>
      </div>
    );
  }
  if (error.code === "gemini_key_missing") {
    return (
      <div className={`alert warn ${compact ? "compact" : ""} plan-key`} role="alert">
        <span>{error.message}</span>
        <button type="button" className="primary small" onClick={onConnectGemini}>Connect Gemini</button>
      </div>
    );
  }
  return <div className={`alert warn ${compact ? "compact" : ""}`}>{error.message}</div>;
}
