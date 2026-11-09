import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { SettingsView as S } from "../types/api";
import { ConnectionsPanel } from "./ConnectionsPanel";

const yes = (v: boolean) => <span className={`chip ${v ? "ok" : "warn"}`}>{v ? "yes" : "no"}</span>;

export function SettingsView({ onOpenSetup, onConnectionChanged }: { onOpenSetup?: () => void; onConnectionChanged?: () => void } = {}) {
  const [s, setS] = useState<S | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.settings().then(setS).catch((e) => setErr(e instanceof ApiError ? e.message : "Could not load settings")); }, []);
  if (!s) return <div className="view"><ConnectionsPanel onChange={onConnectionChanged} />{err ? <div className="alert err">{err}</div> : "Loading settings…"}</div>;
  const g = s.providers.gemini, cf = s.providers.cloudflare, oa = s.providers.openai, b = s.budget;
  return (
    <div className="view">
      <h2>Settings</h2>
      <ConnectionsPanel onChange={onConnectionChanged} />
      {onOpenSetup && <div><button type="button" className="ghost" onClick={onOpenSetup}>Run setup guide again</button></div>}
      <p className="hint">The sections below are read-only. {s.how_to_change} App v{s.version} · spec v{s.spec_version}.</p>

      <section className="card-box">
        <h3>Image providers</h3>
        <table className="gridtbl"><thead><tr><th>Provider</th><th>Role</th><th>Status</th></tr></thead><tbody>
          <tr><td>Google Gemini <small>({g.default_model})</small></td><td>primary</td><td>enabled {yes(g.enabled)} · key configured {yes(g.key_configured)} · est. ${g.est_cost_per_image_usd}/image</td></tr>
          <tr><td>Cloudflare Workers AI <small>(FLUX.2 Klein 4B, FLUX.1 Schnell)</small></td><td>free-tier fallback</td><td>token {yes(cf.token_set)} · account ID {yes(cf.account_id_set)} · usable {yes(cf.configured)}</td></tr>
          <tr><td>OpenAI GPT Image</td><td>planned</td><td>enabled {yes(oa.enabled)} · paid calls {yes(oa.paid_calls_enabled)} · key {yes(oa.key_configured)} <small>(disabled by design: no calls)</small></td></tr>
          <tr><td>Mock (offline)</td><td>testing</td><td>enabled {yes(s.providers.mock.enabled)}</td></tr>
        </tbody></table>
      </section>

      <section className="card-box">
        <h3>Cost tracking <small>(lifetime estimate, not provider billing)</small></h3>
        <ul className="kv">
          <li><span>Planning (text)</span><strong>${b.planning_estimated_usd.toFixed(3)}</strong></li>
          <li><span>Generation (images)</span><strong>${b.generation_estimated_usd.toFixed(3)}</strong></li>
          <li><span>Total</span><strong>${b.spent_estimated_usd.toFixed(3)}</strong></li>
          <li><span>Paid calls enabled</span>{yes(b.paid_calls_enabled)}</li>
        </ul>
        <p className="hint">{b.note}</p>
      </section>

      <section className="card-box">
        <h3>Limits</h3>
        <ul className="kv">{Object.entries(s.limits).map(([k, v]) => <li key={k}><span>{k.replace(/_/g, " ")}</span><strong>{v}</strong></li>)}</ul>
      </section>

      <section className="card-box">
        <h3>Storage <small>(relative to the project folder)</small></h3>
        <ul className="kv">{Object.entries(s.storage).map(([k, v]) => <li key={k}><span>{k}</span><code>{v}</code></li>)}</ul>
        <p className="hint">API keys are never displayed here or sent to the browser.</p>
      </section>
    </div>
  );
}
