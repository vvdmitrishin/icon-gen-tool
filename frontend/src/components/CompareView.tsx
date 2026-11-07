import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { CardComparison, ComparisonGrid, JobDetail } from "../types/api";
import { QABadge } from "./badges";

type Mode = "split" | "side" | "grid";

interface Props {
  job: JobDetail;
  /** card to open (e.g. from the gallery) */
  focusCardId?: string | null;
}

export function CompareView({ job, focusCardId }: Props) {
  const allCards = job.variants.flatMap((v) => v.cards.filter((c) => c.image_url).map((c) => ({ ...c, variant: v.variant_id })));
  const [mode, setMode] = useState<Mode>("split");
  const [cardId, setCardId] = useState<string | null>(focusCardId ?? allCards[0]?.card_id ?? null);
  const [cmp, setCmp] = useState<CardComparison | null>(null);
  const [grid, setGrid] = useState<ComparisonGrid | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pos, setPos] = useState(50);
  const [zoom, setZoom] = useState<"fit" | "100">("fit");
  const [catFilter, setCatFilter] = useState<number | null>(null);

  useEffect(() => { if (focusCardId) setCardId(focusCardId); }, [focusCardId]);

  const loadCard = useCallback(async (id: string) => {
    try { setCmp(await api.cardComparison(job.job_id, id)); setErr(null); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Could not load comparison"); }
  }, [job.job_id]);
  useEffect(() => { if (cardId) loadCard(cardId); }, [cardId, loadCard]);
  useEffect(() => {
    if (mode !== "grid") return;
    api.comparisonGrid(job.job_id).then(setGrid).catch((e) => setErr(e instanceof ApiError ? e.message : "Could not load grid"));
  }, [mode, job.job_id, job.counts.done]);

  async function pickRef(ref: string | null) {
    if (!cardId) return;
    try { setCmp(await api.setComparisonRef(job.job_id, cardId, ref)); } catch (e) { setErr(e instanceof ApiError ? e.message : "Failed"); }
  }

  const idx = allCards.findIndex((c) => c.card_id === cardId);
  const step = (d: number) => { if (allCards.length) setCardId(allCards[(idx + d + allCards.length) % allCards.length].card_id); };

  if (allCards.length === 0) return <div className="empty">Generate at least one card to compare it with a reference.</div>;

  return (
    <div className="compare">
      <div className="cmp-bar">
        <div className="seg" role="radiogroup" aria-label="Compare mode">
          {([["split", "Split Slider"], ["side", "Side by Side (1×1)"], ["grid", "Grid Overview"]] as [Mode, string][]).map(([m, l]) => (
            <button key={m} type="button" role="radio" aria-checked={mode === m} className={mode === m ? "on" : ""} onClick={() => setMode(m)}>{l}</button>
          ))}
        </div>
        {mode !== "grid" && (
          <>
            <select aria-label="Card" value={cardId ?? ""} onChange={(e) => setCardId(e.target.value)}>
              {allCards.map((c) => <option key={c.card_id} value={c.card_id}>{c.variant}{String(c.position).padStart(2, "0")} · Cat {c.category} · {c.object_name}</option>)}
            </select>
            <button type="button" className="ghost" onClick={() => step(-1)} aria-label="Previous card">‹ Prev</button>
            <button type="button" className="ghost" onClick={() => step(1)} aria-label="Next card">Next ›</button>
            <span className="hint">Card {idx + 1} / {allCards.length}</span>
          </>
        )}
      </div>
      {err && <div className="alert err" role="alert">{err}</div>}

      {mode !== "grid" && cmp && (
        <>
          <div className="cmp-ref">
            <label className="lbl" htmlFor="cmpref">Reference</label>
            <select id="cmpref" value={cmp.reference?.mode === "manual" ? cmp.reference.ref_id : ""} onChange={(e) => pickRef(e.target.value || null)}>
              <option value="">{cmp.reference ? `Auto → ${cmp.reference.ref_type} (${cmp.reference.original_name})` : "Auto (none available)"}</option>
              {cmp.candidates.map((r) => <option key={r.ref_id} value={r.ref_id}>{r.ref_type} · {r.original_name}{r.suggested ? " ★" : ""}</option>)}
            </select>
            {cmp.reference && <span className={`chip ${cmp.reference.mode === "manual" ? "warn" : ""}`}>{cmp.reference.mode === "manual" ? "manual match" : "auto match"}</span>}
            <div className="zoomctl">
              <button type="button" className={`ghost ${zoom === "fit" ? "on" : ""}`} onClick={() => setZoom("fit")}>Fit</button>
              <button type="button" className={`ghost ${zoom === "100" ? "on" : ""}`} onClick={() => setZoom("100")}>100%</button>
              <button type="button" className="ghost" onClick={() => { setZoom("fit"); setPos(50); }}>Reset</button>
            </div>
          </div>
          {cmp.message && <div className="alert warn">{cmp.message}</div>}
          {cmp.image_url && cmp.reference && mode === "split" && <Split gen={cmp.image_url} ref_={cmp.reference.image_url} pos={pos} setPos={setPos} zoom={zoom} />}
          {cmp.image_url && cmp.reference && mode === "side" && <Side gen={cmp.image_url} ref_={cmp.reference.image_url} zoom={zoom} />}
          {cmp.image_url && !cmp.reference && <div className="single"><img src={cmp.image_url} alt={cmp.object_name} /></div>}
          {cmp.versions.length > 1 && (
            <p className="hint">Showing the current version. Earlier versions: {cmp.versions.filter((v) => !v.is_current).map((v) => `v${v.version}`).join(", ")} (see the card detail).</p>
          )}
        </>
      )}

      {mode === "grid" && grid && (
        <>
          <div className="cmp-bar">
            <span className="lbl">Filter</span>
            {[null, 1, 2, 3, 4].map((c) => (
              <button key={String(c)} type="button" className={`ghost ${catFilter === c ? "on" : ""}`} onClick={() => setCatFilter(c)}>{c === null ? "All" : `Cat. ${c}`}</button>
            ))}
          </div>
          <table className="gridtbl">
            <thead><tr><th>#</th><th>Cat</th><th>Object</th><th>Generated</th><th>Reference</th><th>QA</th></tr></thead>
            <tbody>
              {grid.pairs.filter((p) => catFilter === null || p.category === catFilter).map((p) => (
                <tr key={p.card_id} onClick={() => { if (p.image_url) { setCardId(p.card_id); setMode("split"); } }} className={p.image_url ? "click" : ""}>
                  <td>{p.variant_id}{String(p.position).padStart(2, "0")}</td><td>{p.category}</td><td>{p.object_name}</td>
                  <td>{p.image_url ? <img src={p.image_url} alt={p.object_name} /> : <span className="hint">{p.status}</span>}</td>
                  <td>{p.reference ? <img src={p.reference.thumbnail_url} alt={p.reference.original_name} /> : <span className="hint">none</span>}</td>
                  <td><QABadge status={p.qa_status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint">Click a row to open that pair in the Split Slider.</p>
        </>
      )}
    </div>
  );
}

function Split({ gen, ref_, pos, setPos, zoom }: { gen: string; ref_: string; pos: number; setPos: (n: number) => void; zoom: "fit" | "100" }) {
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef(false);
  const move = (x: number) => {
    const r = box.current?.getBoundingClientRect();
    if (r) setPos(Math.max(0, Math.min(100, ((x - r.left) / r.width) * 100)));
  };
  return (
    <div className={`split ${zoom === "100" ? "z100" : ""}`}>
      <div ref={box} className="splitbox"
        onPointerDown={(e) => { drag.current = true; (e.target as Element).setPointerCapture?.(e.pointerId); move(e.clientX); }}
        onPointerMove={(e) => drag.current && move(e.clientX)}
        onPointerUp={() => { drag.current = false; }}>
        <img className="under" src={gen} alt="Generated artwork" draggable={false} />
        <img className="over" src={ref_} alt="Reference" draggable={false} style={{ clipPath: `inset(0 0 0 ${pos}%)` }} />
        <span className="tag l">GENERATED</span><span className="tag r">REFERENCE</span>
        <div className="handle" style={{ left: `${pos}%` }} role="slider" aria-label="Comparison divider" aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={Math.round(pos)} tabIndex={0}
          onKeyDown={(e) => { if (e.key === "ArrowLeft") setPos(Math.max(0, pos - 2)); if (e.key === "ArrowRight") setPos(Math.min(100, pos + 2)); }}>
          <span>⇔</span>
        </div>
      </div>
    </div>
  );
}

function Side({ gen, ref_, zoom }: { gen: string; ref_: string; zoom: "fit" | "100" }) {
  const a = useRef<HTMLDivElement>(null);
  const b = useRef<HTMLDivElement>(null);
  const sync = (from: HTMLDivElement | null, to: HTMLDivElement | null) => {
    if (from && to) { to.scrollLeft = from.scrollLeft; to.scrollTop = from.scrollTop; }
  };
  return (
    <div className="side">
      <figure><figcaption>GENERATED</figcaption><div ref={a} className={`vp ${zoom === "100" ? "z100" : ""}`} onScroll={() => sync(a.current, b.current)}><img src={gen} alt="Generated artwork" /></div></figure>
      <figure><figcaption>REFERENCE</figcaption><div ref={b} className={`vp ${zoom === "100" ? "z100" : ""}`} onScroll={() => sync(b.current, a.current)}><img src={ref_} alt="Reference" /></div></figure>
    </div>
  );
}
