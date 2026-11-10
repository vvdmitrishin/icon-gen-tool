import { useState } from "react";
import type { Artifact, PreviewResponse } from "../types/api";
import { PipelineStrip } from "./Pipeline";

type Tab = "results" | "prompt" | "metadata";

interface Props {
  artifact: Artifact | null;
  history: Artifact[];
  onPick: (a: Artifact) => void;
  preview: PreviewResponse | null;
  generating: boolean;
  error: string | null;
}

export function ResultPanel({ artifact, history, onPick, preview, generating, error }: Props) {
  const [tab, setTab] = useState<Tab>("results");
  const [framed, setFramed] = useState(false);

  return (
    <section className="results">
      <PipelineStrip steps={[
        { key: "plan", title: "Plan", sub: preview ? "Prompt compiled" : "Fill in the form", done: !!preview, current: tab === "prompt", onClick: () => setTab("prompt") },
        { key: "gen", title: "Generate", sub: generating ? "Calling provider…" : "One image", active: generating, done: !!artifact && !generating, current: tab === "results", onClick: () => setTab("results") },
        { key: "qa", title: "QA", sub: "Technical QA: not run (single-card test)", optional: true },
        { key: "res", title: "Results", sub: artifact ? `Art review: ${artifact.review.art_review}` : "Art review: pending", done: !!artifact, onClick: () => setTab("results") },
      ]} />

      <div className="tabs" role="tablist">
        {(["results", "prompt", "metadata"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {t === "results" ? "Results" : t === "prompt" ? "Effective Prompt" : "Metadata (JSON)"}
          </button>
        ))}
      </div>

      <div className="banner">
        <div className="banner-main">
          <span className="banner-ic" aria-hidden>▨</span>
          <div>
            <strong>Single-card diagnostic mode: one object, one provider call.</strong>
            <span>Artwork only. Frames, stars and badges are never part of the generated image.</span>
          </div>
        </div>
      </div>

      <div className="titlerow"><h1>Single Card Test</h1></div>

      {error && <div className="alert err" role="alert">{error}</div>}

      {tab === "results" && (
        <div className="stage">
          {generating && <div className="skeleton"><div className="spinner" />Generating… (a single call, no automatic retries)</div>}
          {!generating && !artifact && (
            <div className="empty">
              <p>No card generated yet.</p>
              <p className="hint">Fill in the object on the left, review the cost estimate, then generate one card.</p>
            </div>
          )}
          {!generating && artifact && (
            <div className="card-result">
              <div className={`art-wrap ${framed ? "framed" : ""}`}>
                <img src={artifact.image.url} alt={artifact.card.object_name} />
              </div>
              <div className="meta">
                <h3>{artifact.card.object_name}</h3>
                <div className="chips">
                  <span className={`chip ${artifact.is_mock ? "warn" : "ok"}`}>{artifact.origin}</span>
                  <span className="chip">Cat. {artifact.card.category}</span>
                  <span className="chip">Art review: {artifact.review.art_review}</span>
                  <span className="chip">Technical QA: {artifact.review.technical_qa.replace("_", " ")}</span>
                </div>
                <dl>
                  <dt>Size</dt><dd>{artifact.image.width}×{artifact.image.height} PNG · {(artifact.image.bytes / 1024).toFixed(0)} KB</dd>
                  <dt>Model</dt><dd>{artifact.provider.label} <code>{artifact.provider.model}</code></dd>
                  <dt>References</dt><dd>{artifact.references.length === 0 ? "none (text only)" : artifact.references.map((r) => r.role).join(", ")}</dd>
                  <dt>Latency</dt><dd>{(artifact.timing.latency_ms / 1000).toFixed(1)} s · {artifact.timing.attempts} attempt</dd>
                  <dt>Est. cost</dt><dd>${artifact.cost.estimated_usd.toFixed(3)} <em>(local estimate)</em></dd>
                  {artifact.cost.usage_metadata.totalTokenCount != null && (
                    <><dt>Tokens</dt><dd>{artifact.cost.usage_metadata.totalTokenCount}</dd></>
                  )}
                </dl>
                {artifact.provider.model_notes.length > 0 && (
                  <p className="hint">Model note: {artifact.provider.model_notes.join(" ").slice(0, 240)}</p>
                )}
                <label className="check">
                  <input type="checkbox" checked={framed} onChange={(e) => setFramed(e.target.checked)} />
                  <span>Preview inside a card frame (preview only — not part of the file)</span>
                </label>
                <a className="ghost link" href={artifact.image.url} download={`${(artifact.card.object_name ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "icon"}_cat${artifact.card.category}.png`}>Download PNG</a>
              </div>
            </div>
          )}
          {history.length > 0 && (
            <div className="history">
              <h4>Recent generations</h4>
              <ul>
                {history.map((h) => (
                  <li key={h.artifact_id}>
                    <button type="button" onClick={() => onPick(h)} className={artifact?.artifact_id === h.artifact_id ? "on" : ""} title={`${h.card.object_name} · ${h.origin}`}>
                      <img src={h.image.url} alt={h.card.object_name} />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {tab === "prompt" && (
        <div className="stage">
          {preview ? (
            <>
              {preview.warnings.map((w) => <div key={w} className="alert warn">{w}</div>)}
              <pre className="code">{preview.effective_prompt}</pre>
              <p className="hint">Preview of the prompt that will be sent ({preview.reference_count} reference image(s) attached separately).</p>
            </>
          ) : <div className="empty">Fill in the form to see the compiled prompt.</div>}
        </div>
      )}

      {tab === "metadata" && (
        <div className="stage">
          {artifact ? <pre className="code">{JSON.stringify(artifact, null, 2)}</pre>
            : <div className="empty">Generate a card to see its provenance metadata.</div>}
        </div>
      )}
    </section>
  );
}
