import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { ConnError, read, write } from "../api/connections";
import "./connections.css";

interface CatalogField { name: string; label: string; secret: boolean; required: boolean }
interface CatalogItem {
  kind: string; label: string; types: string[]; needs_model: boolean; fields: CatalogField[];
  model_label?: string; models: string[]; base_url: string | null;
}
interface ProviderItem {
  id: string; label: string; kind: string; types: string[]; local: boolean; status: string; status_label: string;
  message: string; text_models: string[]; image_models: { id: string; label: string }[];
}
interface TextOption { provider_id: string; provider_label: string; model_id: string; local: boolean }
export interface ProvidersState {
  secure_storage: { available: boolean; backend: string };
  providers: ProviderItem[]; catalog: CatalogItem[]; text_options: TextOption[];
  active_text: { provider_id: string; model_id: string; ready: boolean; local: boolean };
}
interface TestOut { ok: boolean; status: string; message: string; models?: string[] }

const EVENT = "studio-providers-changed";
const notify = () => window.dispatchEvent(new Event(EVENT));
const errMsg = (e: unknown) => (e instanceof ConnError ? e.message : "Something went wrong. Please try again.");
const TYPE_LABEL: Record<string, string> = { text: "Text LLM", image: "Image Generation" };

export function useProviders() {
  const [state, setState] = useState<ProvidersState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try { setState(await read<ProvidersState>("/api/providers")); setError(null); } catch (e) { setError(errMsg(e)); }
  }, []);
  useEffect(() => {
    void refresh();
    window.addEventListener(EVENT, refresh);
    return () => window.removeEventListener(EVENT, refresh);
  }, [refresh]);
  return { state, error, refresh, setState };
}

function typesText(p: ProviderItem): string {
  if (p.kind === "ollama" || p.local) return p.types.includes("image") ? "Local LLM + Images" : "Local LLM";
  return p.types.map((t) => TYPE_LABEL[t] ?? t).join(" + ").replace("Text LLM + Image Generation", "Text + Images");
}

/** Which model plans Metaphors. Only connected TEXT models are offered; image models are chosen in Generation Setup. */
export function PlannerModelPicker({ onChanged }: { onChanged?: () => void }) {
  const { state, setState } = useProviders();
  const [err, setErr] = useState<string | null>(null);
  if (!state) return null;
  const cur = `${state.active_text.provider_id}/${state.active_text.model_id}`;
  const opts = state.text_options;
  async function pick(v: string) {
    const i = v.indexOf("/");
    try {
      setState(await write<ProvidersState>("PUT", "/api/active-models/text", { provider_id: v.slice(0, i), model_id: v.slice(i + 1) }));
      setErr(null); notify(); onChanged?.();
    } catch (e) { setErr(errMsg(e)); }
  }
  return (
    <div className="pm-picker">
      <label>
        Planning model (Metaphor Planner){" "}
        <select value={opts.some((o) => `${o.provider_id}/${o.model_id}` === cur) ? cur : ""} onChange={(e) => void pick(e.target.value)}
          disabled={opts.length === 0} aria-label="Planning text model">
          {opts.length === 0 && <option value="">No text model connected</option>}
          {opts.some((o) => `${o.provider_id}/${o.model_id}` === cur) ? null : opts.length > 0 && <option value="">Select a model…</option>}
          {opts.map((o) => (
            <option key={`${o.provider_id}/${o.model_id}`} value={`${o.provider_id}/${o.model_id}`}>
              {o.provider_label} · {o.model_id}{o.local ? " (local)" : ""}
            </option>
          ))}
        </select>
      </label>
      {err && <span className="alert err compact" role="alert">{err}</span>}
    </div>
  );
}

function AddProvider({ state, onState }: { state: ProvidersState; onState: (s: ProvidersState) => void }) {
  const [kind, setKind] = useState("ollama");
  const item = state.catalog.find((c) => c.kind === kind) ?? state.catalog[0];
  const [v, setV] = useState({ api_key: "", account_id: "", base_url: "", model_id: "", label: "" });
  const [found, setFound] = useState<string[]>([]);
  const [busy, setBusy] = useState<null | "test" | "save">(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const hasKey = item.fields.some((f) => f.name === "api_key");
  const hasAcct = item.fields.some((f) => f.name === "account_id");
  const showBase = item.base_url !== null;
  const needsKeyField = item.fields.find((f) => f.name === "api_key" || f.name === "api_token");
  const keyLabel = item.kind === "cloudflare" ? "API Token" : needsKeyField?.label ?? "API Key";

  function pick(k: string) {
    setKind(k); setFound([]); setMsg(null);
    setV((x) => ({ ...x, api_key: "", account_id: "", base_url: "", model_id: "", label: "" }));
  }
  const body = () => ({ kind, api_key: v.api_key, account_id: v.account_id, base_url: v.base_url || item.base_url || "", model_id: v.model_id, label: v.label });

  async function test() {
    setBusy("test"); setMsg(null);
    try {
      const r = await write<TestOut>("POST", "/api/providers/test", body());
      setMsg({ ok: r.ok, text: r.message }); setFound(r.models ?? []);
      if (r.ok && !v.model_id && r.models?.length) setV((x) => ({ ...x, model_id: r.models![0] }));
    } catch (e) { setMsg({ ok: false, text: errMsg(e) }); } finally { setBusy(null); }
  }
  async function save() {
    setBusy("save"); setMsg(null);
    try {
      const r = await write<ProvidersState & { saved: boolean; test: TestOut }>("POST", "/api/providers", body());
      if (r.saved) {
        onState(r); notify();
        setV({ api_key: "", account_id: "", base_url: "", model_id: "", label: "" }); setFound([]);
        setMsg({ ok: true, text: `Connected. ${r.test.message}` });
      } else setMsg({ ok: false, text: `Not saved. ${r.test.message}` });
    } catch (e) { setMsg({ ok: false, text: errMsg(e) }); } finally { setBusy(null); }
  }
  const complete = (!hasKey || item.fields.find((f) => f.name === "api_key")?.required === false || v.api_key.trim() !== "")
    && (item.kind !== "cloudflare" || (v.account_id.trim() !== "" && v.api_key.trim() !== ""))
    && (!item.needs_model || v.model_id.trim() !== "")
    && (item.kind !== "openai_compatible" || v.base_url.trim() !== "");
  const suggestions = Array.from(new Set([...found, ...item.models]));
  const set = (k: keyof typeof v) => (e: { target: { value: string } }) => setV((x) => ({ ...x, [k]: e.target.value }));
  return (
    <section className="pm-add" aria-labelledby="pm-add-title">
      <h4 id="pm-add-title">Add provider</h4>
      <div className="pm-form">
        <label>Provider
          <select value={kind} onChange={(e) => pick(e.target.value)}>
            {state.catalog.map((c) => <option key={c.kind} value={c.kind}>{c.label}</option>)}
          </select>
        </label>
        {kind === "openai_compatible" && (
          <label>Name (optional)<input value={v.label} maxLength={60} onChange={set("label")} placeholder="e.g. LM Studio" /></label>
        )}
        {hasAcct && <label>Account ID<input value={v.account_id} autoComplete="off" spellCheck={false} onChange={set("account_id")} /></label>}
        {(hasKey || item.kind === "cloudflare") && (
          <label>{keyLabel}
            <input type="password" value={v.api_key} autoComplete="off" spellCheck={false} onChange={set("api_key")}
              placeholder={state.secure_storage.available ? "" : "Secure storage unavailable"} disabled={!state.secure_storage.available} />
          </label>
        )}
        {showBase && (
          <label>Base URL
            <input value={v.base_url} spellCheck={false} onChange={set("base_url")} placeholder={item.base_url || "https://host/v1"} />
          </label>
        )}
        {(item.needs_model || item.kind === "gemini") && (
          <label>{item.model_label ?? "Model ID"}
            <input value={v.model_id} list="pm-models" spellCheck={false} onChange={set("model_id")}
              placeholder={item.kind === "ollama" ? "e.g. llama3.1 (use Test Connection to list installed models)" : "choose or type a model ID"} />
            <datalist id="pm-models">{suggestions.map((m) => <option key={m} value={m} />)}</datalist>
          </label>
        )}
      </div>
      {item.kind === "ollama" && <p className="hint">Runs on this PC: no API key needed and no per-call cost. Ollama must be running.</p>}
      <div className="cn-actions">
        <button type="button" className="ghost" disabled={busy !== null || !complete && item.kind !== "ollama"} onClick={() => void test()}>
          {busy === "test" ? "Testing…" : "Test Connection"}
        </button>
        <button type="button" className="primary cn-btn" disabled={busy !== null || !complete} onClick={() => void save()}>
          {busy === "save" ? "Connecting…" : "Save & Connect"}
        </button>
      </div>
      <div aria-live="polite" role="status">
        {msg && <div className={`alert cn-msg ${msg.ok ? "cn-okmsg" : "err"}`}>{msg.text}</div>}
      </div>
    </section>
  );
}

export function ProviderManager({ onChange }: { onChange?: () => void }) {
  const { state, error, setState } = useProviders();
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const apply = (s: ProvidersState) => { setState(s); notify(); onChange?.(); };

  async function disconnect(id: string) {
    setBusy(id); setErr(null); setNote(null);
    try {
      const r = await write<ProvidersState & { note: string }>("DELETE", `/api/providers/${id}`);
      apply(r); setNote(r.note); setConfirm(null);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(null); }
  }
  async function retest(id: string) {
    setBusy(id); setErr(null); setNote(null);
    try {
      const r = await write<ProvidersState & { test: TestOut }>("POST", `/api/providers/${id}/test`);
      apply(r); setNote(r.test.message);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(null); }
  }

  if (!state) return error ? <div className="alert err" role="alert">{error}</div> : <p className="hint">Loading providers…</p>;
  return (
    <div className="pm">
      {!state.secure_storage.available && (
        <div className="alert warn" role="status">Secure storage is unavailable, so API keys can't be saved on this PC. Local servers without a key still work.</div>
      )}
      <section aria-labelledby="pm-conn-title">
        <h4 id="pm-conn-title">Connected providers</h4>
        {state.providers.length === 0 && <p className="hint">Nothing connected yet. Add a provider below.</p>}
        <ul className="pm-list">
          {state.providers.map((p) => {
            const cls = p.status === "connected" ? "cn-ok" : p.status === "configured" ? "cn-warn" : p.status === "invalid" ? "cn-err" : "cn-warn";
            const models = [...p.text_models, ...p.image_models.map((m) => m.label)];
            return (
              <li key={p.id} className="pm-item">
                <div className="pm-main">
                  <strong>{p.label}</strong>
                  <span className={`cn-chip ${cls}`}>{p.status_label}</span>
                  <span className="pm-types">{typesText(p)}</span>
                </div>
                <div className="pm-models" title={p.message}>{models.length ? models.join(" · ") : "No models"}</div>
                <div className="pm-btns">
                  <button type="button" className="ghost sm" disabled={busy !== null} onClick={() => void retest(p.id)}>{busy === p.id ? "…" : "Test"}</button>
                  {confirm === p.id ? (
                    <span className="cn-confirm" role="group" aria-label="Confirm disconnect">
                      Remove this connection and its saved key? History is kept.
                      <button type="button" className="ghost cn-danger sm" disabled={busy !== null} onClick={() => void disconnect(p.id)}>Yes, disconnect</button>
                      <button type="button" className="ghost sm" onClick={() => setConfirm(null)}>Keep</button>
                    </span>
                  ) : (
                    <button type="button" className="ghost cn-danger sm" disabled={busy !== null} onClick={() => setConfirm(p.id)}>Disconnect</button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
        <PlannerModelPicker onChanged={onChange} />
        <div aria-live="polite" role="status">
          {note && <div className="alert cn-msg cn-info">{note}</div>}
          {err && <div className="alert err" role="alert">{err}</div>}
        </div>
      </section>
      <AddProvider state={state} onState={apply} />
      <p className="note cn-privacy">API keys are stored in Windows Credential Manager and are never shown again. Image models are chosen in Generation Setup.</p>
    </div>
  );
}

/** The "Connect Providers" dialog (opened from the header). */
export function ProviderDialog({ open, onClose, onChange }: { open: boolean; onClose: () => void; onChange?: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    return () => prev?.focus?.();
  }, [open]);
  if (!open) return null;
  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Escape") { e.stopPropagation(); onClose(); }
  }
  return (
    <div className="wz-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} className="wz-dialog" role="dialog" aria-modal="true" aria-labelledby="pm-title" tabIndex={-1} onKeyDown={onKeyDown}>
        <div className="pm-head">
          <h2 id="pm-title">AI Providers</h2>
          <button type="button" className="ghost" onClick={onClose}>Close</button>
        </div>
        <ProviderManager onChange={onChange} />
      </div>
    </div>
  );
}
