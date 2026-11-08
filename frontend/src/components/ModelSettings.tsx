import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { connectionsApi, ConnError } from "../api/connections";
import { OUTPUT_SIZES } from "../lib/cards";
import { modelLogo } from "../lib/logos";
import type { ProviderId } from "../types/connections";
import type { ModelCapabilities, ModelInfo } from "../types/api";
import { STILL_ENV, useConnections } from "./ConnectionsPanel";

export const STATUS_LABEL: Record<string, string> = {
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
  connection_failed: "Connection failed",
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
  outputSize: number;
  onOutputSize: (v: number) => void;
  /** open the API Connections settings */
  onManage: () => void;
  /** a credential was removed: reload models */
  onConnectionChanged: () => void;
}

interface AvailabilityProps {
  models: ModelInfo[];
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

/** Display order: Gemini Nano Banana, Cloudflare FLUX.2 Klein, Z-Image-Turbo (local), then everything else (FLUX.1 Schnell last). */
function rankOf(m: ModelInfo): number {
  const t = `${m.model_id} ${m.label}`.toLowerCase();
  if (m.provider_id === "gemini" || /nano.?banana/.test(t)) return 0;
  if (/klein/.test(t)) return 1;
  if (/z-?image/.test(t)) return 2;
  if (/schnell/.test(t)) return 4;
  return 3;
}

/** User-facing status wording for a model (does not change what is selectable). */
export function statusText(m: ModelInfo): string {
  if (runtimeOf(m) === "local" && !m.selectable) return "Planned · local";
  if (m.status === "configured_app") return "Connected · awaiting live verification";
  if (m.selectable && (m.status === "app_live" || m.status === "external_rest_verified" || m.status === "available_in_account")) return "Active";
  if (m.status === "app_live") return "Active";
  if (m.status === "planned" || m.status === "adapter_pending") return "Planned";
  if (!m.selectable && m.status === "disabled_no_key") return "Not connected";
  return STATUS_LABEL[m.status] ?? m.status;
}

function badgeText(m: ModelInfo): string {
  if (runtimeOf(m) === "local") return "Planned · local";
  if (m.status === "planned" || m.status === "adapter_pending") return "Planned";
  if (m.status === "connection_failed") return "Connection failed";
  if (m.status === "documented") return "Docs only"; // provider is connected; the model just has not been confirmed for this key
  return "Not connected";
}

function modelGroups(models: ModelInfo[]) {
  const groups = new Map<string, { rt: Runtime; items: ModelInfo[] }>();
  [...models]
    .sort((a, b) => rankOf(a) - rankOf(b))
    .forEach((m) => {
      const rt = runtimeOf(m);
      const label = groupLabel(rt, m);
      const g = groups.get(label) ?? { rt, items: [] };
      g.items.push(m);
      groups.set(label, g);
    });
  return [...groups.entries()];
}

/** Brand logo (purely decorative: the provider name is always in the text next to it). */
function LogoMark({ m }: { m: ModelInfo }) {
  const l = modelLogo(m);
  if (!l) return null;
  return <span className="plogo" aria-hidden style={{ background: l.color, WebkitMaskImage: `url(${JSON.stringify(l.url)})`, maskImage: `url(${JSON.stringify(l.url)})` }} />;
}

const optId = (k: string) => `mopt-${k.replace(/[^a-z0-9_-]/gi, "_")}`;

interface ListPos { left: number; width: number; top?: number; bottom?: number; maxHeight: number }

/**
 * Custom listbox. The list is `position: fixed`, placed from the trigger's rect (flips upward when there is no room below),
 * sits above the sticky Generate footer and scrolls on its own. Closes on outside click, Esc, resize and page scroll.
 */
function ModelPicker({ models, value, onChange }: { models: ModelInfo[]; value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState<string>(value);
  const [pos, setPos] = useState<ListPos | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const groups = modelGroups(models);
  const selectable = groups.flatMap(([, g]) => g.items).filter((m) => m.selectable).map(keyOf);
  const current = models.find((m) => keyOf(m) === value);
  const startActive = () => (selectable.includes(value) ? value : selectable[0] ?? value);

  const place = useCallback(() => {
    const r = btn.current?.getBoundingClientRect();
    if (!r) return;
    const vh = window.innerHeight;
    const cta = document.querySelector(".cta")?.getBoundingClientRect();
    const limit = cta && cta.top > r.bottom ? cta.top : vh; // keep clear of the sticky footer when possible
    const below = limit - r.bottom - 8;
    const above = r.top - 8;
    const want = 380;
    if (below >= Math.min(want, 240) || below >= above) {
      setPos({ left: r.left, width: r.width, top: r.bottom + 4, maxHeight: Math.max(140, Math.min(want, below)) });
    } else {
      setPos({ left: r.left, width: r.width, bottom: vh - r.top + 4, maxHeight: Math.max(140, Math.min(want, above)) });
    }
  }, []);

  useLayoutEffect(() => { if (open) place(); }, [open, place]);
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onDoc = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) close(); };
    const onScroll = (e: Event) => { if (list.current && e.target instanceof Node && list.current.contains(e.target)) return; close(); };
    const onEsc = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") { close(); btn.current?.focus(); } };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);
  useEffect(() => {
    if (open && pos) document.getElementById(optId(active))?.scrollIntoView({ block: "nearest" });
  }, [open, active, pos]);

  const move = (delta: number | "first" | "last") => {
    if (!selectable.length) return;
    const i = selectable.indexOf(active);
    const next = delta === "first" ? 0 : delta === "last" ? selectable.length - 1 : Math.max(0, Math.min(selectable.length - 1, (i < 0 ? 0 : i) + delta));
    setActive(selectable[next]);
  };
  const choose = (k: string) => { onChange(k); setOpen(false); };

  function onKey(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) { setActive(startActive()); setOpen(true); } else move(e.key === "ArrowDown" ? 1 : -1);
    } else if (e.key === "Home" && open) { e.preventDefault(); move("first"); }
    else if (e.key === "End" && open) { e.preventDefault(); move("last"); }
    else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!open) { setActive(startActive()); setOpen(true); }
      else if (selectable.includes(active)) choose(active);
    } else if (e.key === "Tab") setOpen(false);
  }

  return (
    <div className="mpick" ref={wrap}>
      <button type="button" ref={btn} id="model" className={`mpick-btn ${current?.selectable ? "live" : ""}`} role="combobox" aria-haspopup="listbox"
        aria-expanded={open} aria-controls="model-list" aria-activedescendant={open ? optId(active) : undefined} onKeyDown={onKey}
        onClick={() => { if (!open) setActive(startActive()); setOpen(!open); }}>
        {current ? (
          <>
            <i className={`mdot ${current.selectable ? "on" : ""}`} aria-hidden />
            <LogoMark m={current} />
            <span className="mpick-label">{current.label}</span>
            {!current.selectable && <span className="mbadge">{badgeText(current)}</span>}
          </>
        ) : <span className="mpick-label muted">Select a model</span>}
        <span className="caret" aria-hidden>▾</span>
      </button>
      {open && (
        <ul id="model-list" ref={list} className="mpick-list" role="listbox" aria-label="Image model"
          style={pos ? { left: pos.left, width: pos.width, top: pos.top, bottom: pos.bottom, maxHeight: pos.maxHeight } : { visibility: "hidden" }}>
          {groups.map(([label, g]) => (
            <li key={label} role="presentation" className="mgroup">
              <span className="mgroup-h" role="presentation">{g.items[0] && <LogoMark m={g.items[0]} />}{label}</span>
              <ul role="group" aria-label={label}>
                {g.items.map((m) => {
                  const k = keyOf(m);
                  const sel = k === value;
                  return (
                    <li key={k} id={optId(k)} role="option" aria-selected={sel} aria-disabled={!m.selectable}
                      className={`mopt ${m.selectable ? "live" : "off"} ${sel ? "sel" : ""} ${active === k ? "act" : ""}`}
                      title={!m.selectable ? (m.disabled_reason ?? statusText(m)) : undefined}
                      onMouseEnter={() => { if (m.selectable) setActive(k); }} onClick={() => { if (m.selectable) choose(k); }}>
                      <i className={`mdot ${m.selectable ? "on" : ""}`} aria-hidden />
                      <LogoMark m={m} />
                      <span className="mopt-main">
                        <span className="mopt-name">{m.label}</span>
                        <small>{statusText(m)}{m.role && ROLE_LABEL[m.role] && ROLE_LABEL[m.role] !== statusText(m) && ROLE_LABEL[m.role] !== "Planned" ? ` · ${ROLE_LABEL[m.role]}` : ""}</small>
                      </span>
                      {!m.selectable && <span className="mbadge">{badgeText(m)}</span>}
                      {sel && <span className="mcheck" aria-hidden>✓</span>}
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const CONN_PROVIDERS: string[] = ["gemini", "cloudflare"];

/** Connection line under the model picker: status + Manage / Remove from this PC. Never shows or stores secret values. */
function ModelConnection({ model, refreshKey, onManage, onChanged }: { model: ModelInfo; refreshKey: unknown; onManage: () => void; onChanged: () => void }) {
  const conn = useConnections();
  const { refresh } = conn;
  useEffect(() => { void refresh(); }, [refresh, refreshKey]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const managed = CONN_PROVIDERS.includes(model.provider_id);
  const pc = conn.state?.providers.find((x) => x.id === model.provider_id);
  const hasVault = !!pc?.fields.some((f) => f.stored);
  const [note, setNote] = useState<string | null>(null);
  const connected = model.selectable;
  const failed = model.status === "connection_failed";
  const label = model.provider_label;

  async function removeLocal() {
    if (!window.confirm("Remove the saved credentials from Windows Credential Manager on this PC? This does NOT revoke the key at the provider.")) return;
    setBusy(true); setErr(null); setNote(null);
    try {
      const updated = await connectionsApi.remove(model.provider_id as ProviderId);
      const stillEnv = updated.configured || updated.fields.some((f) => f.from_env);
      const base = updated.removal_note ?? "Removed from this PC.";
      setNote(stillEnv && !base.includes(".env") ? `${base} ${STILL_ENV}` : base);
      await refresh();
      onChanged();
    } catch (e) { setErr(e instanceof ConnError ? e.message : "Could not remove the credential."); }
    finally { setBusy(false); }
  }

  return (
    <div className="connline" aria-label={`Connection for ${label}`}>
      <span className={`cn-chip ${connected ? "cn-ok" : "cn-none"}`}><span aria-hidden>{connected ? "✓" : "○"}</span> {label}: {connected ? "Connected" : failed ? "Connection failed" : "Not connected"}</span>
      {managed && (
        <>
          <button type="button" className="ghost sm" onClick={onManage}>Manage connection</button>
          {hasVault && <button type="button" className="ghost sm cn-danger" disabled={busy} onClick={() => void removeLocal()}>{busy ? "Removing…" : "Remove from this PC"}</button>}
        </>
      )}
      {!connected && model.disabled_reason && managed && <div className="alert warn compact" role="status">{model.disabled_reason}</div>}
      {err && <div className="alert err compact" role="alert">{err}</div>}
      {note && <div className="alert cn-info compact" role="status">{note}</div>}
    </div>
  );
}

export function ModelSettings(p: Props) {
  const current = p.models.find((m) => keyOf(m) === p.value);

  return (
    <div className="gensetup">
      <div className="field">
        <label className="lbl" htmlFor="model">Image model</label>
        <ModelPicker models={p.models} value={p.value} onChange={p.onChange} />
        {current && <ModelConnection model={current} refreshKey={p.models} onManage={p.onManage} onChanged={p.onConnectionChanged} />}
        {current && (
          <div className="chips tight" aria-label="Selected model">
            <span className={`chip st-${current.status}`}>{statusText(current)}</span>
            <span className="chip">{current.runtime_label ?? RUNTIME_HEADING[runtimeOf(current)]}</span>
            {current.origin_badge && <span className="chip">{current.origin_badge}</span>}
          </div>
        )}
      </div>

      {current && (
        <details className="modeldetails" aria-label="Model details">
          <summary>Model details &amp; capabilities</summary>
          <dl>
            <dt>Identifier</dt><dd><code>{current.model_id}</code></dd>
            {(current.runtime_label || current.runtime) && <><dt>Runtime</dt><dd>{current.runtime_label ?? RUNTIME_HEADING[runtimeOf(current)]}</dd></>}
            {current.price_note && <><dt>Price</dt><dd>{current.price_note}</dd></>}
          </dl>
          <CapabilityChips c={current.capabilities} />
          {current.capabilities.notes && <p className="hint">{current.capabilities.notes}</p>}
          <OptionalToggles m={current} />
        </details>
      )}

      <div className="field">
        <label className="lbl" htmlFor="outsize">Output Resolution</label>
        <select id="outsize" value={p.outputSize} onChange={(e) => p.onOutputSize(Number(e.target.value))}>
          {OUTPUT_SIZES.map((n) => <option key={n} value={n}>{n} × {n} px</option>)}
        </select>
      </div>
    </div>
  );
}

/** Provider availability + disabled models (collapsible, inside Generation Setup). */
export function ProviderAvailability(p: AvailabilityProps) {
  const disabledByRuntime = RUNTIME_ORDER.map((rt) => ({
    rt,
    items: p.models.filter((m) => !m.selectable && runtimeOf(m) === rt),
  })).filter((g) => g.items.length > 0);

  return (
    <details className="adv nested">
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
  );
}
