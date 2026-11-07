import type { ModelInfo } from "../types/api";

const STATUS_LABEL: Record<string, string> = {
  external_rest_verified: "REST-verified",
  app_live: "Live in app",
  available_in_account: "Available",
  documented: "Docs only",
  adapter_pending: "Adapter pending",
  disabled_no_key: "Not configured",
  disabled_by_flag: "Disabled",
  mock: "Mock",
  configured_app: "Configured",
};

const ROLE_LABEL: Record<string, string> = {
  primary: "Primary",
  fallback: "Fallback",
  planned: "Planned",
  test: "Test only",
};

interface Props {
  models: ModelInfo[];
  value: string; // "provider/model"
  onChange: (v: string) => void;
  aspect: string;
  onAspect: (v: string) => void;
  checking: boolean;
  checkedAt: string | null;
  availabilityError: string | null;
  onCheck: () => void;
}

export const keyOf = (m: ModelInfo) => `${m.provider_id}/${m.model_id}`;

export function ModelSettings(p: Props) {
  const groups = new Map<string, ModelInfo[]>();
  p.models.forEach((m) => groups.set(m.provider_label, [...(groups.get(m.provider_label) ?? []), m]));
  const current = p.models.find((m) => keyOf(m) === p.value);

  return (
    <div>
      <div className="row">
        <label className="lbl" htmlFor="model">Image model</label>
        <select id="model" value={p.value} onChange={(e) => p.onChange(e.target.value)}>
          {[...groups.entries()].map(([label, ms]) => (
            <optgroup key={label} label={label}>
              {ms.map((m) => (
                <option key={keyOf(m)} value={keyOf(m)} disabled={!m.selectable}>
                  {m.label} — {ROLE_LABEL[m.role]} · {STATUS_LABEL[m.status] ?? m.status}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>
      {current && (
        <div className="chips">
          <span className={`chip st-${current.status}`}>{STATUS_LABEL[current.status] ?? current.status}</span>
          <span className="chip">{current.origin_badge}</span>
        </div>
      )}
      <div className="row">
        <label className="lbl" htmlFor="aspect">Aspect ratio</label>
        <select id="aspect" value={p.aspect} onChange={(e) => p.onAspect(e.target.value)}>
          {(current?.capabilities.aspect_ratios ?? ["1:1"]).map((a) => (
            <option key={a} value={a}>{a === "1:1" ? "1:1 (square card art)" : a}</option>
          ))}
        </select>
      </div>
      <div className="row">
        <label className="lbl">Planning</label>
        <select disabled><option>Rules / Templates (Phase 3)</option></select>
      </div>
      <details className="adv">
        <summary>Provider availability &amp; disabled models</summary>
        <ul className="disabled-list">
          {p.models.filter((m) => !m.selectable).map((m) => (
            <li key={keyOf(m)}>
              <strong>{m.provider_label} · {m.label}</strong>
              <span>{m.disabled_reason}</span>
            </li>
          ))}
        </ul>
        <button type="button" className="ghost" onClick={p.onCheck} disabled={p.checking}>
          {p.checking ? "Checking…" : "Check model availability (free)"}
        </button>
        {p.checkedAt && <p className="hint">Last checked {new Date(p.checkedAt).toLocaleTimeString()}</p>}
        {p.availabilityError && <div className="alert err">{p.availabilityError}</div>}
      </details>
    </div>
  );
}
