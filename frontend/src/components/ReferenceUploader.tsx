import { useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { RefMeta } from "../types/api";

interface Props {
  refs: RefMeta[];
  selected: string[];
  maxRefs: number;
  refTypes: string[];
  policyAccepted: boolean;
  /** the rights checkbox is only shown when selected references would be sent to an external provider */
  policyNeeded: boolean;
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
  category_5: "Category 5 (golden)",
  character: "Character (golden cards)",
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
    try {
      await api.deleteReference(id);
      p.onSelect(p.selected.filter((x) => x !== id));
      p.onChange();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not delete the reference");
    }
  }

  return (
    <div className="refs">
      <span className="lbl">Upload style references (1–{p.maxRefs})</span>
      <div className="reftiles">
        <div
          className={`dropzone ${drag ? "drag" : ""}`}
          onClick={() => input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files); }}
          role="button"
          tabIndex={0}
          aria-label="Upload reference images"
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), input.current?.click())}
        >
          <span className="dz-ic" aria-hidden>⇪</span>
          <strong>{busy ? "Uploading…" : "Drop images or click to upload"}</strong>
          <span>PNG · JPG</span>
          <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden onChange={(e) => upload(e.target.files)} />
        </div>
        {p.refs.map((r) => {
          const on = p.selected.includes(r.ref_id);
          return (
            <div key={r.ref_id} className={`reftile ${on ? "on" : ""}`}>
              <button type="button" className="thumb" onClick={() => toggle(r.ref_id)}
                title={`${r.original_name} · ${r.width}×${r.height} · ${TYPE_LABEL[r.ref_type] ?? r.ref_type}. Click to ${on ? "stop using" : "use"} it.`} aria-pressed={on}>
                <img src={`/api/references/${r.ref_id}/thumbnail`} alt={r.original_name} />
                {on && <span className="tick" aria-hidden>✓</span>}
                <span className="rtag">{TYPE_LABEL[r.ref_type] ?? r.ref_type}</span>
              </button>
              <button type="button" className="x" aria-label={`Delete ${r.original_name}`} title="Delete this reference" onClick={() => remove(r.ref_id)}>×</button>
            </div>
          );
        })}
      </div>
      {error && <div className="alert err" role="alert">{error}</div>}
      <div className="refmeta">
        {p.refTypes.length > 1 && (
          <label className="reftype">
            <span className="lbl">New uploads are</span>
            <select id="reftype" value={refType} onChange={(e) => setRefType(e.target.value)} aria-label="Reference type for new uploads">
              {p.refTypes.map((t) => <option key={t} value={t}>{TYPE_LABEL[t] ?? t}</option>)}
            </select>
          </label>
        )}
        <span className="hint">{p.selected.length}/{p.maxRefs} used for this generation{p.refs.length ? " · click a tile to toggle" : ""}</span>
      </div>
      {p.policyNeeded && p.selected.length > 0 && (
        <label className="check policy">
          <input type="checkbox" checked={p.policyAccepted} onChange={(e) => p.onPolicy(e.target.checked)} />
          <span>I am allowed to send these reference images to the external AI provider.</span>
        </label>
      )}
    </div>
  );
}
