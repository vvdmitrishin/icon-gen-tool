import { useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { RefMeta } from "../types/api";

interface Props {
  refs: RefMeta[];
  selected: string[];
  maxRefs: number;
  refTypes: string[];
  policyAccepted: boolean;
  onChange: () => void;
  onSelect: (ids: string[]) => void;
  onPolicy: (v: boolean) => void;
}

const TYPE_LABEL: Record<string, string> = {
  style: "Style",
  category_1: "Category 1",
  category_2: "Category 2",
  category_3: "Category 3",
  category_4: "Category 4",
  composition: "Composition",
  negative: "Negative example",
};

export function ReferenceUploader(p: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [refType, setRefType] = useState("style");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      let ids = [...p.selected];
      for (const f of Array.from(files)) {
        const meta = await api.uploadReference(f, refType);
        if (!ids.includes(meta.ref_id) && ids.length < p.maxRefs) ids = [...ids, meta.ref_id];
      }
      p.onChange();
      p.onSelect(ids);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Upload failed");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  function toggle(id: string) {
    if (p.selected.includes(id)) p.onSelect(p.selected.filter((x) => x !== id));
    else if (p.selected.length < p.maxRefs) p.onSelect([...p.selected, id]);
  }

  async function remove(id: string) {
    await api.deleteReference(id);
    p.onSelect(p.selected.filter((x) => x !== id));
    p.onChange();
  }

  return (
    <div>
      <div className="row">
        <label className="lbl" htmlFor="reftype">Reference type</label>
        <select id="reftype" value={refType} onChange={(e) => setRefType(e.target.value)}>
          {p.refTypes.map((t) => (
            <option key={t} value={t}>{TYPE_LABEL[t] ?? t}</option>
          ))}
        </select>
      </div>
      <div
        className={`dropzone ${drag ? "drag" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files); }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
      >
        <strong>{busy ? "Uploading…" : "Drop images here or click to upload"}</strong>
        <span>PNG / JPG / WebP · up to {p.maxRefs} used per generation</span>
        <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden
               onChange={(e) => upload(e.target.files)} />
      </div>
      {error && <div className="alert err" role="alert">{error}</div>}
      {p.refs.length > 0 && (
        <ul className="refgrid">
          {p.refs.map((r) => {
            const on = p.selected.includes(r.ref_id);
            return (
              <li key={r.ref_id} className={on ? "on" : ""}>
                <button type="button" className="thumb" onClick={() => toggle(r.ref_id)}
                        title={`${r.original_name} · ${r.width}×${r.height}`} aria-pressed={on}>
                  <img src={`/api/references/${r.ref_id}/thumbnail`} alt={r.original_name} />
                  {on && <span className="tick">✓</span>}
                </button>
                <span className="tag">{TYPE_LABEL[r.ref_type] ?? r.ref_type}</span>
                <button type="button" className="x" aria-label={`Delete ${r.original_name}`}
                        onClick={() => remove(r.ref_id)}>×</button>
              </li>
            );
          })}
        </ul>
      )}
      <p className="hint">
        {p.selected.length}/{p.maxRefs} selected for this generation. Click a thumbnail to toggle.
      </p>
      {p.selected.length > 0 && (
        <label className="check policy">
          <input type="checkbox" checked={p.policyAccepted}
                 onChange={(e) => p.onPolicy(e.target.checked)} />
          <span>I am allowed to send these reference images to the external AI provider.</span>
        </label>
      )}
    </div>
  );
}
