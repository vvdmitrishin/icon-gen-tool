// Static interface preview (Vercel): build with VITE_DEMO=1. No backend, no keys, no content:
// reads return empty/neutral fixtures so every screen renders; anything that would change data or call a provider is refused.
export const DEMO = import.meta.env.MODE === "demo" || import.meta.env.VITE_DEMO === "1";

export const DEMO_MESSAGE =
  "Interface preview: no backend is connected here, so nothing can be generated or saved. Run the tool locally to use it (see the README).";

const budget = {
  paid_calls_enabled: false,
  spent_estimated_usd: 0,
  planning_estimated_usd: 0,
  generation_estimated_usd: 0,
  max_card_retries: 0,
  note: "Interface preview",
};

const models = {
  models: [
    {
      provider_id: "gemini",
      provider_label: "Google Gemini",
      model_id: "gemini-nano-banana-2.1",
      label: "Nano Banana 2.1",
      role: "primary",
      status: "app_live",
      selectable: true,
      disabled_reason: null,
      capabilities: { supports_reference_images: true, max_reference_images: 5, aspect_ratios: ["1:1"] },
      est_cost_per_image_usd: null,
      origin_badge: "Preview",
      availability_checked: false,
      runtime: "cloud_api",
      runtime_label: "Cloud API",
    },
  ],
  defaults: { provider: "gemini", model: "gemini-nano-banana-2.1", category_distribution: { "1": 2, "2": 2, "3": 3, "4": 3, "5": 0 } },
  budget,
  planner: { text_model: "gemini-3.5-flash-lite", input_usd_per_m: 0, output_usd_per_m: 0, base_usd: 0, per_card_usd: 0 },
  availability: { checked_at: null, error: null },
  limits: { max_refs_per_request: 5, max_upload_bytes: 10485760, ref_types: ["style", "category_1", "category_2", "category_3", "category_4", "category_5"] },
};

const conn = (id: string, label: string) => ({
  id, label, configured: false, source: "none", status: "not_configured", last_tested_at: null,
  message: "Interface preview", fields: [], help: { get_credentials_url: "", steps: [] },
});

const fixtures: Record<string, unknown> = {
  "/api/models": models,
  "/api/costs": { planning_usd: 0, generation_usd: 0, total_usd: 0, planning_calls: 0, image_calls: 0, cost_is_estimate: true,
    unattributed: { planning_usd: 0, generation_usd: 0, planning_calls: 0, image_calls: 0 }, incomplete_note: "" },
  "/api/settings": { version: "preview", spec_version: "1.10",
    providers: { gemini: { enabled: false, key_configured: false, default_model: "gemini-nano-banana-2.1", est_cost_per_image_usd: 0 },
      cloudflare: { token_set: false, account_id_set: false, configured: false },
      openai: { enabled: false, paid_calls_enabled: false, key_configured: false }, mock: { enabled: false } },
    budget, limits: {}, storage: {}, how_to_change: "" },
  "/api/connections": { secure_storage: { available: false, backend: "none" }, first_run: false,
    providers: [conn("gemini", "Google Gemini"), conn("cloudflare", "Cloudflare Workers AI")] },
  "/api/collection/overview": { collection: "Movie Collection", genres: [], final_ready: false, blockers: [], note: "" },
  "/api/session": { token: "preview" },
  "/api/providers": { secure_storage: { available: false, backend: "none" }, providers: [], catalog: [], text_options: [],
    active_text: { provider_id: "", model_id: "", ready: false, local: false } },
};

const lists = ["/api/references", "/api/artifacts", "/api/jobs", "/api/planner/genres", "/api/prompts", "/api/templates", "/api/exports"];

/** undefined = not a demo request; otherwise the response body (or an Error to throw). */
export function demoRead(path: string): unknown {
  const clean = path.split("?")[0];
  if (clean in fixtures) return fixtures[clean];
  if (lists.includes(clean)) return [];
  return [];
}
