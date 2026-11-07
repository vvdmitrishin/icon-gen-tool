import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Preset } from "../types/api";

interface Props {
  /** current structural settings of the Generate form (no credentials) */
  current: () => Record<string, unknown>;
  onApply: (payload: Record<string, unknown>) => void;
}

const msg = (e: unknown, f: string) => (e instanceof ApiError ? e.message : f);

function describe(p: Record<string, unknown>) {
  const d = p.category_distribution as { mode?: string; counts?: Record<string, number> } | undefined;
  const dist = d?.mode === "custom" && d.counts ? `custom ${[1, 2, 3, 4].map((c) => d.counts![String(c)]).join("/")}` : "auto balanced";
  return `${p.genre ?? "any genre"} · ${p.number_of_variants ?? 1} variant(s) · ${p.scope ?? "pilot"} · ${dist}${p.wishes ? ` · “${String(p.wishes).slice(0, 40)}”` : ""}`;
}

export function TemplatesView({ current, onApply }: Props) {
  const [items, setItems] = useState<Preset[]>([]);
  const [name, setName] = useState("");
  const [m, setM] = useState<{ ok: boolean; t: string } | null>(null);
  const load = useCallback(async () => { try { setItems(await api.templates()); } catch (e) { setM({ ok: false, t: msg(e, "Could not load templates") }); } }, []);
  useEffect(() => { load(); }, [load]);

  async function save() {
    try { await api.createTemplate(name, current()); setName(""); setM({ ok: true, t: "Template saved." }); await load(); }
    catch (e) { setM({ ok: false, t: msg(e, "Save failed") }); }
  }
  async function del(id: number) {
    try { await api.deleteTemplate(id); await load(); } catch (e) { setM({ ok: false, t: msg(e, "Delete failed") }); }
  }

  return (
    <div className="view">
      <h2>Templates</h2>
      <p className="hint">Saved presets for the Generate screen: genre, variants, run scope, category distribution and free wishes. Templates never store API keys, models or references.</p>
      {m && <div className={`alert ${m.ok ? "ok" : "err"}`} role="status">{m.t}</div>}
      <section className="card-box">
        <h3>Save the current Generate settings</h3>
        <div className="importform">
          <input placeholder="Template name" maxLength={60} value={name} onChange={(e) => setName(e.target.value)} aria-label="Template name" />
          <button type="button" className="primary small" disabled={!name.trim()} onClick={save}>Save template</button>
        </div>
      </section>
      <section className="card-box">
        <h3>Available templates</h3>
        <ul className="tpls">
          {items.map((t) => (
            <li key={t.id}>
              <div><strong>{t.name}</strong> <span className={`chip ${t.kind === "builtin" ? "" : "ok"}`}>{t.kind}</span><br /><small>{describe(t.payload)}</small></div>
              <div className="rvbtns">
                <button type="button" className="primary small" onClick={() => onApply(t.payload)}>Apply</button>
                {t.kind === "user" && <button type="button" className="ghost" onClick={() => del(t.id)}>Delete</button>}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
