import { useCallback, useEffect, useState } from "react";
import { connectionsApi, ConnError } from "../api/connections";
import type { ConnectionsState, ConnStatus, ProviderConn, ProviderId } from "../types/connections";
import "./connections.css";

const COPY: Record<ProviderId, { title: string; blurb: string; extra?: string }> = {
  gemini: {
    title: "Google Gemini",
    blurb: "Google's image model. Produces the best-looking cards. Needs one API key.",
  },
  cloudflare: {
    title: "Cloudflare (free FLUX models)",
    blurb: "Free-tier FLUX image models. Needs an Account ID and an API token that has Workers AI permission.",
    extra: "The free plan has a daily allocation, so it can pause when you use it up. This app never upgrades your billing.",
  },
};

const STATUS: Record<ConnStatus, { icon: string; text: string; cls: string }> = {
  not_configured: { icon: "○", text: "Not connected", cls: "cn-none" },
  unverified: { icon: "?", text: "Not tested", cls: "cn-warn" },
  connected: { icon: "✓", text: "Connected", cls: "cn-ok" },
  invalid: { icon: "✕", text: "Invalid credentials", cls: "cn-err" },
  unreachable: { icon: "!", text: "Can't reach provider", cls: "cn-warn" },
};

function sourceText(s: ProviderConn): string {
  if (s.source === "credential_manager") return "Saved securely on this PC";
  if (s.source === "env") return "From the .env file (developer mode)";
  return "Nothing saved yet";
}

function formatTime(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function errMsg(e: unknown): string {
  return e instanceof ConnError ? e.message : "Something went wrong. Please try again.";
}

export interface ConnectionsHandle {
  state: ConnectionsState | null;
  error: string | null;
  unavailable: boolean;
  loading: boolean;
  refresh: () => Promise<void>;
  replaceProvider: (p: ProviderConn) => void;
}

export function useConnections(): ConnectionsHandle {
  const [state, setState] = useState<ConnectionsState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const s = await connectionsApi.get();
      setState(s);
      setError(null);
      setUnavailable(false);
    } catch (e) {
      if (e instanceof ConnError && e.status === 404) setUnavailable(true);
      else setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const replaceProvider = useCallback((p: ProviderConn) => {
    setState((s) => (s ? { ...s, providers: s.providers.map((x) => (x.id === p.id ? p : x)) } : s));
  }, []);

  return { state, error, unavailable, loading, refresh, replaceProvider };
}

export function StatusChip({ status }: { status: ConnStatus }) {
  const s = STATUS[status] ?? STATUS.not_configured;
  return (
    <span className={`cn-chip ${s.cls}`}>
      <span aria-hidden="true">{s.icon}</span> {s.text}
    </span>
  );
}

interface CardProps {
  provider: ProviderConn;
  storageAvailable: boolean;
  onUpdate: (p: ProviderConn) => void;
}

const PRECEDENCE_DEFAULT = "Windows Credential Manager takes priority over the .env file; .env is the fallback.";
const ACCOUNT_ID_HELP = "Find it in the Cloudflare dashboard under Workers AI (Account ID is shown on the right side of the page).";
export const STILL_ENV = "Still connected through the .env file.";

export function ProviderCard({ provider: p, storageAvailable, onUpdate }: CardProps) {
  const copy = COPY[p.id] ?? { title: p.label, blurb: "" };
  const [editing, setEditing] = useState<Record<string, boolean>>({});
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<null | "save" | "test" | "remove">(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "info"; text: string } | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  const hasSaved = p.fields.some((f) => f.stored || f.from_env);
  const anyStored = p.fields.some((f) => f.stored);
  const anyEditing = Object.values(editing).some(Boolean);
  const filled = Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim() !== ""));
  const canSave = storageAvailable && Object.keys(filled).length > 0 && busy === null;

  function cancelEdit(name: string) {
    setEditing((e) => ({ ...e, [name]: false }));
    setValues((v) => { const n = { ...v }; delete n[name]; return n; });
  }

  async function save() {
    setBusy("save"); setMsg(null);
    try {
      const updated = await connectionsApi.save(p.id, filled, true); // only the fields the user typed
      setValues({}); // clear secrets from memory right after a successful save
      setEditing({});
      onUpdate(updated);
      const r = updated.test_result;
      if (r) setMsg({ kind: r.ok ? "ok" : "err", text: r.message });
      else setMsg({ kind: "info", text: "Saved. Use Test connection to check it works." });
    } catch (e) {
      setMsg({ kind: "err", text: errMsg(e) });
    } finally { setBusy(null); }
  }

  async function test() {
    setBusy("test"); setMsg(null);
    try {
      const r = await connectionsApi.test(p.id);
      setMsg({ kind: r.ok ? "ok" : "err", text: r.message });
      const fresh = await connectionsApi.get();
      const mine = fresh.providers.find((x) => x.id === p.id);
      if (mine) onUpdate(mine);
    } catch (e) {
      setMsg({ kind: "err", text: errMsg(e) });
    } finally { setBusy(null); }
  }

  async function remove() {
    setBusy("remove"); setMsg(null);
    try {
      const updated = await connectionsApi.remove(p.id);
      onUpdate(updated);
      setConfirmRemove(false); setEditing({}); setValues({});
      const stillEnv = updated.configured || updated.fields.some((f) => f.from_env);
      const note = updated.removal_note ?? "Removed from this PC. The key itself is still valid at the provider.";
      setMsg({ kind: "info", text: stillEnv && !note.includes(".env") ? `${note} ${STILL_ENV}` : note });
    } catch (e) {
      setMsg({ kind: "err", text: errMsg(e) });
    } finally { setBusy(null); }
  }

  const headId = `cn-${p.id}-title`;
  return (
    <section className="cn-card" aria-labelledby={headId}>
      <div className="cn-head">
        <h4 id={headId}>{copy.title}</h4>
        <StatusChip status={p.status} />
      </div>
      <p className="cn-blurb">{copy.blurb}</p>
      {copy.extra && <p className="hint">{copy.extra}</p>}
      <p className="cn-meta">
        {sourceText(p)} · Last tested: {formatTime(p.last_tested_at)}
        {p.message ? <> · {p.message}</> : null}
      </p>
      <p className="cn-prec">{p.precedence_note ?? PRECEDENCE_DEFAULT}</p>

      <div className="cn-fields">
        {p.fields.map((f) => {
          const id = `cn-${p.id}-${f.name}`;
          const hasValue = f.stored || f.from_env;
          const showInput = !hasValue || editing[f.name];
          const tag = f.stored ? "Windows Credential Manager" : ".env file";
          const isAccount = f.name.toLowerCase().includes("account");
          return (
            <div className="cn-field" key={f.name}>
              <label htmlFor={id}>{f.label}</label>
              {showInput ? (
                <div className="cn-inrow">
                  <input
                    id={id}
                    type={f.secret ? "password" : "text"}
                    autoComplete="off"
                    spellCheck={false}
                    value={values[f.name] ?? ""}
                    placeholder={hasValue ? "Type the new value" : ""}
                    aria-describedby={isAccount ? `${id}-help` : undefined}
                    disabled={!storageAvailable || busy !== null}
                    onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
                  />
                  {editing[f.name] && (
                    <button type="button" className="ghost sm" disabled={busy !== null} onClick={() => cancelEdit(f.name)}>Cancel</button>
                  )}
                </div>
              ) : (
                <div className="cn-inrow">
                  <input id={id} type="password" readOnly value="••••••••" aria-label={`${f.label} (saved)`} />
                  <span className="cn-src">Saved · {tag}{f.stored && f.in_env ? " (+ .env fallback)" : ""}</span>
                  <button type="button" className="ghost sm" disabled={busy !== null || !storageAvailable}
                    onClick={() => setEditing((e) => ({ ...e, [f.name]: true }))}>
                    {f.stored ? "Replace" : "Edit"}
                  </button>
                </div>
              )}
              {isAccount && showInput && <small id={`${id}-help`} className="hint">{ACCOUNT_ID_HELP}</small>}
            </div>
          );
        })}
      </div>

      <div className="cn-actions">
        <button type="button" className="primary cn-btn" disabled={!canSave} onClick={() => void save()}>
          {busy === "save" ? "Saving and testing…" : "Save & test"}
        </button>
        {hasSaved && (
          <button type="button" className="ghost" disabled={busy !== null || anyEditing} onClick={() => void test()}>
            {busy === "test" ? "Testing…" : "Test connection (free, no images)"}
          </button>
        )}
        {anyStored && !confirmRemove && (
          <button type="button" className="ghost" disabled={busy !== null} onClick={() => setConfirmRemove(true)}>
            Remove from this PC
          </button>
        )}
        {confirmRemove && (
          <span className="cn-confirm" role="group" aria-label="Confirm removal">
            Remove the saved credentials from Windows Credential Manager on this PC? This does NOT revoke the key at the provider.
            <button type="button" className="ghost cn-danger" disabled={busy !== null} onClick={() => void remove()}>
              {busy === "remove" ? "Removing…" : "Yes, remove"}
            </button>
            <button type="button" className="ghost" onClick={() => setConfirmRemove(false)}>Keep</button>
          </span>
        )}
      </div>

      <div aria-live="polite" role="status">
        {msg && <div className={`alert cn-msg ${msg.kind === "err" ? "err" : msg.kind === "ok" ? "cn-okmsg" : "cn-info"}`}>{msg.text}</div>}
      </div>

      <p className="hint cn-revoke">
        To revoke a key for real, delete it in the provider's dashboard.
        {p.help.get_credentials_url && (
          <> <a href={p.help.get_credentials_url} target="_blank" rel="noopener noreferrer">Open the provider's page ↗</a></>
        )}
      </p>

      <div className="cn-help">
        <button type="button" className="cn-link" aria-expanded={showHelp} onClick={() => setShowHelp((s) => !s)}>
          {showHelp ? "▾" : "▸"} How do I get this?
        </button>
        {showHelp && (
          <div>
            <ol>{p.help.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
          </div>
        )}
      </div>
    </section>
  );
}

export function PrivacyNote() {
  return (
    <p className="note cn-privacy">
      Your credentials stay on this computer. They are stored in Windows Credential Manager and are never shown again after you save them.
    </p>
  );
}

export function ProviderCards({ conn }: { conn: ConnectionsHandle }) {
  const { state, error, unavailable, loading, replaceProvider } = conn;
  if (loading) return <p className="hint">Loading connections…</p>;
  if (unavailable) return <div className="note">Connecting providers from here is not available in this build. You can still set keys in the .env file.</div>;
  if (error || !state) return <div className="alert err" role="alert">{error ?? "Could not load connections."}</div>;
  return (
    <>
      {!state.secure_storage.available && (
        <div className="alert warn" role="status">
          Credentials can't be saved on this PC right now (secure storage is unavailable). Keys set in the .env file still work.
        </div>
      )}
      <div className="cn-grid">
        {state.providers.map((p) => (
          <ProviderCard key={p.id} provider={p} storageAvailable={state.secure_storage.available} onUpdate={replaceProvider} />
        ))}
      </div>
    </>
  );
}

export function ConnectionsPanel({ onChange }: { onChange?: () => void }) {
  const conn = useConnections();
  const { replaceProvider } = conn;
  const update = useCallback((p: ProviderConn) => { replaceProvider(p); onChange?.(); }, [replaceProvider, onChange]);
  return (
    <section className="card-box" aria-labelledby="cn-panel-title">
      <h3 id="cn-panel-title">Connect your image providers</h3>
      <p className="hint">Connect Gemini to plan and generate sets (the AI planner needs it, even when you pick the free Mock image provider). Cloudflare is an optional free image fallback.</p>
      <PrivacyNote />
      <ProviderCards conn={{ ...conn, replaceProvider: update }} />
    </section>
  );
}
