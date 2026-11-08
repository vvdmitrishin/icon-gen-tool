import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { ExportRecord } from "../types/api";
import { catText, vlabel } from "../lib/cards";
import { selectionSummary, type Favorites } from "../lib/favorites";

interface Props {
  favs: Favorites;
  collection: string;
  genre: string;
  setType: "standard" | "grand";
  onOpenJob: (jobId: string) => void;
}

/** Session shortlist: every starred image stays here across new batches, page refreshes and different jobs. */
export function MySelection({ favs, collection, genre, setType, onOpenJob }: Props) {
  const [busy, setBusy] = useState(false);
  const [rec, setRec] = useState<ExportRecord | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const items = favs.items;
  const sum = selectionSummary(items, setType);

  async function exportSelected() {
    setBusy(true); setErr(null); setRec(null);
    try {
      setRec(await api.exportSelection({
        collection, genre, set_type: setType,
        items: items.map((i) => ({ job_id: i.job_id, card_id: i.card_id, artifact_id: i.artifact_id })),
      }));
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Export failed"); }
    finally { setBusy(false); }
  }

  return (
    <section className="bcard myselection" aria-label="My Selection">
      <div className="mys-head">
        <h3><span aria-hidden>★</span> My Selection <span className="chip" aria-live="polite">{items.length} selected</span></h3>
        <div className="mys-actions">
          <button type="button" className="primary small" disabled={!items.length || busy} onClick={exportSelected}
            title="Export exactly these images as a ZIP (original files, with a manifest)">{busy ? "Exporting…" : "Export Selected"}</button>
          <button type="button" className="ghost sm" disabled={!items.length} onClick={favs.clear} title="Remove all stars (no image is deleted)">Clear</button>
        </div>
      </div>
      {!items.length ? (
        <p className="hint">Click the ☆ on any generated image to keep it here. Your selection stays while you generate more batches, and is kept for this browser session only.</p>
      ) : (
        <>
          <div className="mys-summary">
            {[1, 2, 3, 4, 5].map((c) => <span key={c} className={`covchip cat${c} ${sum.counts[c] ? "full" : "zero"}`} title={`${c === 5 ? "Gold" : `Category ${c}`}: ${sum.counts[c]} selected`}>{catText(c)} {sum.counts[c]}</span>)}
            {sum.tenCard === true && <span className="chip ok">Forms a standard 10-card Set</span>}
            {sum.tenCard === false && <span className="chip warn">Not a standard 10-card Set</span>}
          </div>
          {sum.warnings.map((w) => <p key={w} className="hint warnline">⚠ {w}</p>)}
          <ul className="mys-grid">
            {items.map((i) => (
              <li key={i.artifact_id} className={`mys-item ${i.category === 5 ? "gold" : ""}`}>
                <button type="button" className="mys-img" onClick={() => onOpenJob(i.job_id)} title={`${i.object_name}: open the job of Batch #${i.batch ?? "?"}`}>
                  <img src={i.image_url} alt={i.object_name} loading="lazy" />
                  <span className={`cat c${i.category}`}>{catText(i.category)}</span>
                </button>
                <button type="button" className="mys-x" aria-label={`Remove ${i.object_name} from My Selection`} onClick={() => favs.remove(i.artifact_id)}>★</button>
                <small title={`${i.provider} / ${i.model}`}>{i.object_name}<br />Batch #{i.batch ?? "?"} · V{vlabel(i.variant_id)}{i.version && i.version > 1 ? ` · v${i.version}` : ""}</small>
              </li>
            ))}
          </ul>
        </>
      )}
      {err && <div className="alert err" role="alert">{err}</div>}
      {rec && (
        <div className={`alert ${rec.warnings?.length ? "warn" : "ok"}`} role="status">
          Exported {rec.count} image{rec.count === 1 ? "" : "s"}: <a href={rec.download_url} download={rec.filename}>{rec.filename}</a> ({(rec.size / 1024).toFixed(0)} KB)
          {rec.contains_mock_images && " · contains MOCK images"}
          {rec.warnings?.map((w) => <div key={w}>⚠ {w}</div>)}
          <button type="button" className="alert-x" aria-label="Dismiss" onClick={() => setRec(null)}>×</button>
        </div>
      )}
    </section>
  );
}
