import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Artifact, PromptRow } from "../types/api";
import { catText } from "../lib/cards";

async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}

export function CopyButton({ text, label = "Copy prompt" }: { text: string; label?: string }) {
  const [done, setDone] = useState<boolean | null>(null);
  return (
    <button type="button" className="ghost sm" onClick={async () => { setDone(await copyText(text)); window.setTimeout(() => setDone(null), 1800); }}>
      {done === true ? "Copied ✓" : done === false ? "Copy failed" : label}
    </button>
  );
}

type Bundle = Record<string, { source: string; version: number | null; base_version?: number | null }>;

/** Which template versions / session overrides the prompt was built from. */
export function BundleInfo({ bundle }: { bundle: Bundle | undefined }) {
  const entries = Object.entries(bundle ?? {});
  if (!entries.length) return <p className="hint">Template versions: not recorded (older image).</p>;
  return (
    <p className="hint pv-bundle">
      Templates:{" "}
      {entries.map(([n, b]) => (
        <span key={n} className={`chip tiny ${b.source === "session_override" ? "warn" : ""}`} title={b.source === "session_override" ? `Session override (based on permanent v${b.base_version ?? "?"})` : `Permanent library v${b.version}`}>
          {n.replace("_", " ")} {b.source === "session_override" ? `· SESSION (base v${b.base_version ?? "?"})` : `· v${b.version}`}
        </span>
      ))}
    </p>
  );
}

/** "View Prompt" for a completed image: the exact immutable prompt stored with it (no provider call). */
export function ImagePrompt({ artifactId }: { artifactId: string }) {
  const [a, setA] = useState<Artifact | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setA(null); setErr(null);
    api.artifact(artifactId).then((x) => { if (live) setA(x); }).catch((e) => { if (live) setErr(e instanceof ApiError ? e.message : "Could not load the prompt"); });
    return () => { live = false; };
  }, [artifactId]);
  if (err) return <div className="alert err">{err}</div>;
  if (!a) return <p className="hint"><i className="spinner sm" /> Loading the stored prompt…</p>;
  const p = a.prompt;
  const refs = a.references ?? [];
  return (
    <div className="pv" aria-label="Exact prompt of this image">
      <div className="pv-head">
        <strong>Exact prompt sent for this image</strong>
        <CopyButton text={p.effective} />
      </div>
      <p className="hint">
        Model: <strong>{a.provider?.label ?? a.provider?.model}</strong> ({a.provider?.id}) · stored with the image, not rebuilt. No API call is made to show it.
        {" "}References: {refs.length ? refs.map((r) => r.role).join(", ") : "none"}.
      </p>
      <BundleInfo bundle={p.bundle} />
      {(p.session_overrides?.length ?? 0) > 0 && <div className="alert warn compact">This image used SESSION prompt overrides: {p.session_overrides!.join(", ")}.</div>}
      {p.planning_context?.variant_title && (
        <p className="hint">Set context: <em>{p.planning_context.variant_title}</em>{p.planning_context.variant_concept ? ` · ${p.planning_context.variant_concept}` : ""}</p>
      )}
      <pre className="code pv-text">{p.effective}</pre>
      {p.warnings?.length > 0 && p.warnings.map((w) => <p key={w} className="hint warnline">⚠ {w}</p>)}
    </div>
  );
}

/** Prompts of a whole variant: stored prompts for generated images, compiled previews (same compiler) for planned ones. */
export function PromptList({ rows, note }: { rows: PromptRow[]; note?: string }) {
  const [open, setOpen] = useState<string | null>(null);
  const all = rows.map((r) => `#${r.position} ${r.object_name} (Cat. ${r.category})\n${r.prompt}`).join("\n\n----------\n\n");
  return (
    <div className="pv-list">
      <div className="pv-head">
        <span className="hint">{note}</span>
        <CopyButton text={all} label="Copy all" />
      </div>
      <ol className="pv-rows">
        {rows.map((r) => {
          const k = `${r.variant_id}-${r.position}`;
          return (
            <li key={k} className={open === k ? "open" : ""}>
              <button type="button" className="pv-row" onClick={() => setOpen(open === k ? null : k)} aria-expanded={open === k}>
                <span className={`cat c${r.category}`}>{catText(r.category)}</span>
                <strong>#{r.position} {r.object_name}</strong>
                <span className={`chip tiny ${r.source === "generated" ? "ok" : ""}`}>{r.source === "generated" ? "stored (generated)" : "preview (not generated)"}</span>
                {r.references.length > 0 && <span className="chip tiny">{r.references.length} ref · {r.references.map((x) => x.role).join(", ")}</span>}
                {r.session_overrides.length > 0 && <span className="chip tiny warn">session override</span>}
              </button>
              {open === k && (
                <div className="pv-body">
                  <div className="pv-head"><span className="hint">{r.provider} / {r.model} · references: {r.reference_selection ?? "n/a"}</span><CopyButton text={r.prompt} /></div>
                  <BundleInfo bundle={r.bundle} />
                  <pre className="code pv-text">{r.prompt}</pre>
                  {r.warnings.map((w) => <p key={w} className="hint warnline">⚠ {w}</p>)}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
