import type { ModelCapabilities, ModelInfo } from "../types/api";

const STATUS_LABEL: Record<string, string> = {
  external_rest_verified: "REST-verified",
  app_live: "Live in app",
  available_in_account: "Available",
  documented: "Docs only",
  adapter_pending: "Adapter pending",
  planned: "Planned",
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

type Runtime = "cloud_api" | "local" | "test";
const RUNTIME_ORDER: Runtime[] = ["cloud_api", "local", "test"];

/** Old backends send no `runtime`; infer it so the selector still groups sensibly. */
function runtimeOf(m: ModelInfo): Runtime {
  if (m.runtime) return m.runtime;
  if (m.provider_id === "mock") return "test";
  if (m.provider_id === "local") return "local";
  return "cloud_api";
}

function groupLabel(rt: Runtime, m: ModelInfo): string {
  if (!m.runtime) return m.provider_label; // legacy grouping
  if (rt === "cloud_api") return `Cloud API · ${m.provider_label}`;
  if (rt === "local") return `Local models (planned) · ${m.provider_label}`;
  return "Development / testing";
}

const RUNTIME_HEADING: Record<Runtime, string> = {
  cloud_api: "Cloud API models",
  local: "Local models (planned)",
  test: "Development / testing",
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

const tri = (v: boolean | null | undefined, expected = false) =>
  v === true ? (expected ? "✓ expected" : "✓") : v === false ? (expected ? "not expected" : "✗ not supported") : "unverified";
const triClass = (v: boolean | null | undefined) => (v === true ? "ok" : v === false ? "no" : "unk");

function isEditingModel(c: ModelCapabilities) {
  return c.image_editing === true && c.text_to_image !== true;
}

function CapabilityChips({ c }: { c: ModelCapabilities }) {
  const expected = c.verified === false; // planned model: values are expectations
  const refs = !c.supports_reference_images
    ? (expected ? "not assumed" : "✗ not supported")
    : `✓ (${c.max_reference_images != null ? `max ${c.max_reference_images}` : "max unknown"})`;
  return (
    <div className="chips capchips" aria-label="Capabilities">
      {isEditingModel(c) && <span className="chip cap-edit">Editing model</span>}
      {c.verified === false && <span className="chip cap-unk">expected, not verified</span>}
      <span className={`chip cap-${triClass(c.text_to_image)}`}>Text→image {tri(c.text_to_image, expected)}</span>
      <span className={`chip cap-${triClass(c.image_editing)}`}>Editing {tri(c.image_editing, expected)}</span>
      <span className={`chip cap-${c.supports_reference_images ? "ok" : "no"}`}>References {refs}</span>
    </div>
  );
}

function OptionalToggles({ m }: { m: ModelInfo }) {
  if (!m.options?.length) return null;
  return (
    <div className="modelopts">
      {m.options.map((o) => (
        <label key={o.key} className="check inline" title={o.description}>
          <input type="checkbox" disabled checked={false} readOnly aria-label={`${o.label} (${STATUS_LABEL[o.status] ?? o.status}, off)`} />
          <span>Optional: {o.label} ({STATUS_LABEL[o.status]?.toLowerCase() ?? o.status}, {o.default ? "on" : "off"})</span>
        </label>
      ))}
    </div>
  );
}

export function ModelSettings(p: Props) {
  const groups = new Map<string, { rt: Runtime; items: ModelInfo[] }>();
  [...p.models]
    .sort((a, b) => RUNTIME_ORDER.indexOf(runtimeOf(a)) - RUNTIME_ORDER.indexOf(runtimeOf(b)))
    .forEach((m) => {
      const rt = runtimeOf(m);
      const label = groupLabel(rt, m);
      const g = groups.get(label) ?? { rt, items: [] };
      g.items.push(m);
      groups.set(label, g);
    });
  const current = p.models.find((m) => keyOf(m) === p.value);
  const disabledByRuntime = RUNTIME_ORDER.map((rt) => ({
    rt,
    items: p.models.filter((m) => !m.selectable && runtimeOf(m) === rt),
  })).filter((g) => g.items.length > 0);

  return (
    <div>
      <div className="row">
        <label className="lbl" htmlFor="model">Image model</label>
        <select id="model" value={p.value} onChange={(e) => p.onChange(e.target.value)}>
          {[...groups.entries()].map(([label, g]) => (
            <optgroup key={label} label={label}>
              {g.items.map((m) => (
                <option key={keyOf(m)} value={keyOf(m)} disabled={!m.selectable}>
                  {m.label} — {STATUS_LABEL[m.status] ?? m.status}{m.role && ROLE_LABEL[m.role] && ROLE_LABEL[m.role] !== STATUS_LABEL[m.status] ? ` · ${ROLE_LABEL[m.role]}` : ""}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>

      {current && (
        <div className="modeldetails" aria-label="Model details">
          <div className="chips">
            <span className={`chip st-${current.status}`}>{STATUS_LABEL[current.status] ?? current.status}</span>
            <span className="chip">{current.origin_badge}</span>
          </div>
          <dl>
            <dt>Identifier</dt><dd><code>{current.model_id}</code></dd>
            {(current.runtime_label || current.runtime) && <><dt>Runtime</dt><dd>{current.runtime_label ?? RUNTIME_HEADING[runtimeOf(current)]}</dd></>}
            {current.price_note && <><dt>Price</dt><dd>{current.price_note}</dd></>}
          </dl>
          <CapabilityChips c={current.capabilities} />
          {current.capabilities.notes && <p className="hint">{current.capabilities.notes}</p>}
          <OptionalToggles m={current} />
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
        {disabledByRuntime.map(({ rt, items }) => (
          <section key={rt} className="disabled-group">
            <h4>{RUNTIME_HEADING[rt]}</h4>
            <ul className="disabled-list">
              {items.map((m) => (
                <li key={keyOf(m)}>
                  <strong>{m.provider_label} · {m.label} <span className={`chip st-${m.status}`}>{STATUS_LABEL[m.status] ?? m.status}</span></strong>
                  <code>{m.model_id}</code>
                  {m.disabled_reason && <span>Why disabled: {m.disabled_reason}</span>}
                  {m.runtime_requirements && <span>Requires: {m.runtime_requirements}</span>}
                  {m.price_note && <span>Price: {m.price_note}</span>}
                  <CapabilityChips c={m.capabilities} />
                  {m.capabilities.notes && <span>{m.capabilities.notes}</span>}
                  <OptionalToggles m={m} />
                </li>
              ))}
            </ul>
          </section>
        ))}
        <button type="button" className="ghost" onClick={p.onCheck} disabled={p.checking}>
          {p.checking ? "Checking…" : "Check model availability (free)"}
        </button>
        {p.checkedAt && <p className="hint">Last checked {new Date(p.checkedAt).toLocaleTimeString()}</p>}
        {p.availabilityError && <div className="alert err">{p.availabilityError}</div>}
      </details>
    </div>
  );
}
