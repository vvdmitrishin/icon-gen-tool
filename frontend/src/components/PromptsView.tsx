import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { JobDetail, PromptRow, PromptsPreviewResponse } from "../types/api";
import { PromptList } from "./PromptViewer";

interface Props {
  job: JobDetail | null;
  variantId?: string;
  /** a plan exists (preview) even though no job was started yet */
  hasPlan: boolean;
  onPreview: () => Promise<PromptsPreviewResponse>;
}

/**
 * Optional "Preview Prompts": the compiled prompts of the selected Set / variant, before (or after) generating images. Rows for
 * generated images show the immutable stored prompt; rows for planned cards are compiled by the SAME backend code as real
 * generation (never a frontend approximation). No image provider is called and nothing has to be approved.
 */
export function PromptsView({ job, variantId, hasPlan, onPreview }: Props) {
  const [rows, setRows] = useState<Record<string, PromptRow[]>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const jobId = job?.job_id;

  useEffect(() => {
    let live = true;
    setBusy(true); setErr(null);
    (async () => {
      try {
        if (jobId) {
          const r = await api.jobPrompts(jobId, variantId);
          if (!live) return;
          const grouped: Record<string, PromptRow[]> = {};
          r.prompts.forEach((p) => { (grouped[p.variant_id] ??= []).push(p); });
          setRows(grouped);
          setNote(r.snapshot ? "From this job's immutable prompt snapshot." : "This older job has no snapshot: planned prompts use the current library.");
        } else if (hasPlan) {
          const r = await onPreview();
          if (!live) return;
          setRows(r.variants);
          setNote(r.session_overrides.length ? `Preview with SESSION overrides: ${r.session_overrides.join(", ")}.` : "Preview with the permanent defaults.");
        } else { setRows({}); }
      } catch (e) { if (live) setErr(e instanceof ApiError ? e.message : "Could not compile the prompts"); }
      finally { if (live) setBusy(false); }
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, variantId, hasPlan]);

  const variants = Object.keys(rows).sort();
  if (!jobId && !hasPlan) return <div className="empty">Plan or generate a set first; then you can inspect its prompts here.</div>;
  return (
    <div className="prompts-view">
      <p className="hint">Optional inspection. Nothing here needs approval, and no image API is called. {note}</p>
      {busy && <p className="hint"><i className="spinner sm" /> Compiling…</p>}
      {err && <div className="alert err" role="alert">{err}</div>}
      {variants.map((v) => (
        <section key={v} className="bcard">
          <h3>Variant {v}</h3>
          <PromptList rows={rows[v]} note={`${rows[v].filter((r) => r.source === "generated").length} stored · ${rows[v].filter((r) => r.source === "compiled").length} preview`} />
        </section>
      ))}
    </div>
  );
}
