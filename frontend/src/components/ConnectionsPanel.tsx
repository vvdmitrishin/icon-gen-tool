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

export function ProviderCard({ provider: p, storageAvailable, onUpdate }: CardProps) {
  const copy = COPY[p.id] ?? { title: p.label, blurb: "" };
  const hasSaved = p.fields.some((f) => f.stored || f.from_env);
  const [replacing, setReplacing] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<null | "save" | "test" | "remove">(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "info"; text: string } | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  const showInputs = !hasSaved || replacing;
  const filled = Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim() !== ""));
  const canSave = storageAvailable && Object.keys(filled).length > 0 && busy === null;

  async function save() {
    setBusy("save"); setMsg(null);
    try {
      const updated = await connectionsApi.save(p.id, filled, true);
      setValues({}); // clear secrets from memory right after a successful save
      setReplacing(false);
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
      setConfirmRemove(false); setReplacing(false); setValues({});
      setMsg({ kind: "info", text: "Removed from this PC." });
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

      <div className="cn-fields">
        {p.fields.map((f) => {
          const id = `cn-${p.id}-${f.name}`;
          const saved = f.stored || f.from_env;
          return (
            <div className="cn-field" key={f.name}>
              <label htmlFor={id}>{f.label}</label>
              {showInputs ? (
                <input
                  id={id}
                  type={f.secret ? "password" : "text"}
                  autoComplete="off"
                  spellCheck={false}
                  value={values[f.name] ?? ""}
                  placeholder={saved ? "Saved - type to replace" : ""}
                  disabled={!storageAvailable || busy !== null}
                  onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
                />
              ) : (
                <input id={id} type="text" readOnly value="" placeholder={saved ? "Saved" : "Not set"} />
              )}
            </div>
          );
        })}
      </div>

      <div className="cn-actions">
        {showInputs && (
          <button type="button" className="primary cn-btn" disabled={!canSave} onClick={() => void save()}>
            {busy === "save" ? "Saving and testing…" : "Save & test"}
          </button>
        )}
        {showInputs && replacing && (
          <button type="button" className="ghost" disabled={busy !== null} onClick={() => { setReplacing(false); setValues({}); }}>
            Cancel
          </button>
        )}
        {hasSaved && !replacing && (
          <>
            <button type="button" className="primary cn-btn" disabled={busy !== null} onClick={() => void test()}>
              {busy === "test" ? "Testing…" : "Test connection"}
            </button>
            <button type="button" className="ghost" disabled={busy !== null || !storageAvailable} onClick={() => setReplacing(true)}>
              Replace
            </button>
          </>
        )}
        {p.source === "credential_manager" && !confirmRemove && (
          <button type="button" className="ghost" disabled={busy !== null} onClick={() => setConfirmRemove(true)}>
            Remove
          </button>
        )}
        {confirmRemove && (
          <span className="cn-confirm" role="group" aria-label="Confirm removal">
            Remove the saved credentials from this PC?
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

      <div className="cn-help">
        <button type="button" className="cn-link" aria-expanded={showHelp} onClick={() => setShowHelp((s) => !s)}>
          {showHelp ? "▾" : "▸"} How do I get this?
        </button>
        {showHelp && (
          <div>
            <ol>{p.help.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
            {p.help.get_credentials_url && (
              <a href={p.help.get_credentials_url} target="_blank" rel="noopener noreferrer">Open the provider's page ↗</a>
            )}
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
      <p className="hint">Add at least one to create real images. Until then you can try the free demo (Mock) provider.</p>
      <PrivacyNote />
      <ProviderCards conn={{ ...conn, replaceProvider: update }} />
    </section>
  );
}
