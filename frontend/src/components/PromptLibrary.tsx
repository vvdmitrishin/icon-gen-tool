import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { PromptTemplate, PromptVersion } from "../types/api";
import { SESSION_EDITABLE, useSessionPrompts } from "../lib/sessionPrompts";

const HELP: Record<string, string> = {
  repair: "Placeholders: {note} (required) and {object_name}. Used when a card is regenerated with notes.",
  system_style: "Layer A: the style description sent with every card.",
  image_closing: "Final constraints appended to every prompt (no text, no frames…).",
};

type Mode = "session" | "permanent";

/**
 * Two clearly separated ways to change a prompt:
 *  - Session (this browser tab only): View → Edit → Apply to Session → Reset. Temporary experiments, stored in sessionStorage,
 *    sent with every Generate, snapshotted per job by the server. The permanent library is NOT touched.
 *  - Permanent: creates a new immutable library version that becomes the default for every future generation.
 */
export function PromptLibrary() {
  const sess = useSessionPrompts();
  const [mode, setMode] = useState<Mode>("session");
  const [items, setItems] = useState<PromptTemplate[]>([]);
  const [name, setName] = useState("system_style");
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [viewVer, setViewVer] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  const load = useCallback(async (keep?: string) => {
    try {
      const list = await api.prompts();
      setItems(list);
      const cur = list.find((i) => i.name === (keep ?? name)) ?? list[0];
      if (cur) { setName(cur.name); setText(cur.text); setViewVer(cur.version); setVersions(await api.promptVersions(cur.name)); }
    } catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Could not load prompts" }); }
  }, [name]);
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const cur = items.find((i) => i.name === name);
  const sessionEditable = (SESSION_EDITABLE as readonly string[]).includes(name);
  const overridden = sess.has(name);
  const shown = viewVer != null ? versions.find((v) => v.version === viewVer) : undefined;
  // what the text box shows: session mode -> the effective text (override if any, else the permanent default)
  const baseText = mode === "session" ? (overridden ? sess.overrides[name] : cur?.text ?? "") : cur?.text ?? "";
  const dirty = text.trim() !== baseText.trim();

  function show(n: string, m: Mode = mode) {
    const c = items.find((i) => i.name === n);
    setName(n); setMsg(null); setNote(""); setEditing(false); setWarnings([]);
    if (c) { setText(m === "session" && sess.has(n) ? sess.overrides[n] : c.text); setViewVer(c.version); }
    api.promptVersions(n).then(setVersions).catch(() => undefined);
  }
  function switchMode(m: Mode) { setMode(m); show(name, m); }
  function pickVersion(v: number) {
    setViewVer(v);
    const found = versions.find((x) => x.version === v);
    if (found) setText(found.text);
  }

  // ---- session actions
  async function applyToSession() {
    const next = { ...sess.overrides, [name]: text.trim() };
    try {
      const r = await api.sessionCheck(next);
      sess.apply(name, text);
      setWarnings(r.warnings);
      setEditing(false);
      setMsg({ ok: true, text: "Applied to this browser session only. Every Generate from now on uses it; the permanent library is unchanged." });
    } catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Could not apply the override" }); }
  }
  function resetOne() {
    sess.reset(name);
    setText(cur?.text ?? ""); setEditing(false); setWarnings([]);
    setMsg({ ok: true, text: "Reset to the permanent default for this template." });
  }
  function resetAll() {
    sess.resetAll();
    setText(cur?.text ?? ""); setEditing(false); setWarnings([]);
    setMsg({ ok: true, text: "All session overrides removed. Generation uses the permanent defaults again." });
  }

  // ---- permanent actions
  async function save() {
    try {
      await api.savePrompt(name, text, note);
      setMsg({ ok: true, text: "Saved as a NEW PERMANENT version; it is now the default for every future generation." });
      setNote(""); setEditing(false);
      await load(name);
    } catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Save failed" }); }
  }
  async function restore() {
    if (viewVer == null) return;
    try {
      await api.restorePrompt(name, viewVer);
      setMsg({ ok: true, text: `Version ${viewVer} restored as a new active permanent version.` });
      await load(name);
    } catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Restore failed" }); }
  }

  return (
    <div className="plib">
      {sess.count > 0 && (
        <div className="alert warn session-banner" role="status">
          <strong>Session Override Active</strong> ({sess.count}): {sess.names.map((n) => n.replace("_", " ")).join(", ")}. Generations in this browser tab use these
          temporary texts; the permanent library below is unchanged.
          <button type="button" className="ghost sm" onClick={resetAll}>Reset All Session Overrides</button>
        </div>
      )}

      <div className="seg plib-mode" role="radiogroup" aria-label="What do you want to change?">
        <button type="button" role="radio" aria-checked={mode === "session"} className={mode === "session" ? "on" : ""} onClick={() => switchMode("session")}>
          Session (this tab only)
        </button>
        <button type="button" role="radio" aria-checked={mode === "permanent"} className={mode === "permanent" ? "on" : ""} onClick={() => switchMode("permanent")}>
          Permanent versions
        </button>
      </div>
      <p className="hint">
        {mode === "session"
          ? "Session: try a prompt without changing the defaults. Stored only in this browser tab (kept on refresh, gone in a new session). Nothing is saved to the Prompt Library."
          : "Permanent: every save creates a new immutable library version that becomes the default for ALL future generations."}
      </p>

      <div className="plib-top">
        <label className="lbl" htmlFor="tpl">Template</label>
        <select id="tpl" value={name} onChange={(e) => show(e.target.value)}>
          {items.map((i) => (
            <option key={i.name} value={i.name}>
              {i.label} · v{i.version}{i.is_default_text ? "" : " (edited)"}{sess.has(i.name) ? " • session override" : ""}
            </option>
          ))}
        </select>
      </div>
      <p className="hint">{HELP[name] ?? "Category rules: composition + background text sent for cards of this category."}</p>

      {mode === "session" ? (
        <>
          {!sessionEditable ? (
            <div className="alert warn compact">This template can only be changed permanently (switch to “Permanent versions”).</div>
          ) : (
            <>
              <div className="chips">
                {overridden ? <span className="chip warn">Session override active for this template</span> : <span className="chip ok">Using the permanent default (v{cur?.version})</span>}
              </div>
              <textarea className="plib-text" rows={9} value={text} readOnly={!editing} onChange={(e) => setText(e.target.value)} aria-label="Template text (session)" />
              <div className="counter">{text.length} / 3000</div>
              <div className="plib-actions">
                {!editing ? (
                  <button type="button" className="ghost" onClick={() => setEditing(true)}>Edit</button>
                ) : (
                  <>
                    <button type="button" className="primary small" disabled={!dirty || !text.trim()} onClick={applyToSession}>Apply to Session</button>
                    <button type="button" className="ghost" onClick={() => { setText(baseText); setEditing(false); }}>Cancel</button>
                  </>
                )}
                <button type="button" className="ghost" disabled={!overridden} onClick={resetOne}>Reset to Default</button>
                <button type="button" className="ghost" disabled={sess.count === 0} onClick={resetAll}>Reset All Session Overrides</button>
              </div>
              {warnings.map((w) => <div key={w} className="alert warn compact" role="alert">⚠ {w}</div>)}
              <p className="hint">Mandatory rules (artwork only, no text/frames/stars, at most 2 characters on a golden card) are always enforced and appended automatically while an override is active.</p>
            </>
          )}
        </>
      ) : (
        <>
          <div className="row">
            <label className="lbl" htmlFor="ver">Version</label>
            <select id="ver" value={viewVer ?? ""} onChange={(e) => pickVersion(Number(e.target.value))}>
              {versions.map((v) => <option key={v.version} value={v.version}>v{v.version}{v.is_active ? " (active)" : ""} · {v.author} · {new Date(v.created_at).toLocaleString()}</option>)}
            </select>
          </div>
          {shown?.note && <p className="hint">Note: {shown.note}</p>}
          <textarea className="plib-text" rows={9} value={text} readOnly={!editing} onChange={(e) => setText(e.target.value)} aria-label="Template text (permanent)" />
          <div className="counter">{text.length} / 3000</div>
          <div className="plib-actions">
            {!editing && <button type="button" className="ghost" onClick={() => setEditing(true)}>Edit permanent version…</button>}
            {editing && (
              <>
                <input placeholder="Change note (what and why)" value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} aria-label="Change note" />
                <button type="button" className="primary small" disabled={!dirty || !text.trim()} onClick={save} title="Creates a new permanent version for all future generations">Save as new PERMANENT version</button>
                <button type="button" className="ghost" onClick={() => { setText(cur?.text ?? ""); setEditing(false); }}>Discard edits</button>
              </>
            )}
            <button type="button" className="ghost" disabled={!cur || viewVer === cur.version} onClick={restore}>Restore v{viewVer}</button>
          </div>
        </>
      )}
      {msg && <div className={`alert ${msg.ok ? "ok" : "err"}`} role="status">{msg.text}</div>}
    </div>
  );
}
