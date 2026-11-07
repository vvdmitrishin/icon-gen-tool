import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { PromptTemplate, PromptVersion } from "../types/api";

const HELP: Record<string, string> = {
  repair: "Placeholders: {note} (required) and {object_name}. Used when a card is regenerated with notes.",
  system_style: "Layer A: the permanent style description sent with every card.",
  image_closing: "Final constraints appended to every prompt (no text, no frames…).",
};

export function PromptLibrary() {
  const [items, setItems] = useState<PromptTemplate[]>([]);
  const [name, setName] = useState("system_style");
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [viewVer, setViewVer] = useState<number | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

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
  const shown = viewVer != null ? versions.find((v) => v.version === viewVer) : undefined;
  const dirty = cur ? text.trim() !== cur.text.trim() : false;

  async function pickName(n: string) {
    setName(n); setMsg(null); setNote("");
    const c = items.find((i) => i.name === n);
    if (c) { setText(c.text); setViewVer(c.version); }
    setVersions(await api.promptVersions(n));
  }
  function pickVersion(v: number) {
    setViewVer(v);
    const found = versions.find((x) => x.version === v);
    if (found) setText(found.text);
  }
  async function save() {
    try {
      await api.savePrompt(name, text, note);
      setMsg({ ok: true, text: "Saved as a new version; it is now used for every generation." });
      setNote("");
      await load(name);
    } catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Save failed" }); }
  }
  async function restore() {
    if (viewVer == null) return;
    try {
      await api.restorePrompt(name, viewVer);
      setMsg({ ok: true, text: `Version ${viewVer} restored as a new active version.` });
      await load(name);
    } catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Restore failed" }); }
  }

  return (
    <div className="plib">
      <div className="plib-top">
        <label className="lbl" htmlFor="tpl">Template</label>
        <select id="tpl" value={name} onChange={(e) => pickName(e.target.value)}>
          {items.map((i) => <option key={i.name} value={i.name}>{i.label} · v{i.version}{i.is_default_text ? "" : " (edited)"}</option>)}
        </select>
        <label className="check inline"><input type="checkbox" checked={advanced} onChange={(e) => setAdvanced(e.target.checked)} /><span>Advanced mode (edit)</span></label>
      </div>
      <p className="hint">{HELP[name] ?? "Category rules: composition + background text sent for cards of this category."} Basic mode is read-only. Every save creates a new immutable version, recorded in each image's provenance.</p>
      <div className="row">
        <label className="lbl" htmlFor="ver">Version</label>
        <select id="ver" value={viewVer ?? ""} onChange={(e) => pickVersion(Number(e.target.value))}>
          {versions.map((v) => <option key={v.version} value={v.version}>v{v.version}{v.is_active ? " (active)" : ""} · {v.author} · {new Date(v.created_at).toLocaleString()}</option>)}
        </select>
      </div>
      {shown?.note && <p className="hint">Note: {shown.note}</p>}
      <textarea className="plib-text" rows={9} value={text} readOnly={!advanced} onChange={(e) => setText(e.target.value)} aria-label="Template text" />
      <div className="counter">{text.length} / 3000</div>
      {advanced && (
        <div className="plib-actions">
          <input placeholder="Change note (what and why)" value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} aria-label="Change note" />
          <button type="button" className="primary small" disabled={!dirty || !text.trim()} onClick={save}>Save as new version</button>
          <button type="button" className="ghost" disabled={!cur || viewVer === cur.version} onClick={restore}>Restore v{viewVer}</button>
          <button type="button" className="ghost" onClick={() => cur && setText(cur.text)} disabled={!dirty}>Discard edits</button>
        </div>
      )}
      {msg && <div className={`alert ${msg.ok ? "ok" : "err"}`} role="status">{msg.text}</div>}
    </div>
  );
}
