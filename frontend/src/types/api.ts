export interface ModelInfo {
  provider_id: string;
  provider_label: string;
  model_id: string;
  label: string;
  role: "primary" | "fallback" | "planned" | "test";
  status: string;
  selectable: boolean;
  disabled_reason: string | null;
  capabilities: {
    supports_reference_images: boolean;
    max_reference_images: number;
    aspect_ratios: string[];
  };
  est_cost_per_image_usd: number | null;
  origin_badge: string;
  availability_checked: boolean;
}

export interface Budget {
  paid_calls_enabled: boolean;
  job_budget_usd: number;
  total_budget_usd: number;
  spent_estimated_usd: number;
  remaining_estimated_usd: number;
  max_card_retries: number;
  note: string;
}

export interface ModelsResponse {
  models: ModelInfo[];
  defaults: { provider: string; model: string };
  budget: Budget;
  availability: { checked_at: string | null; error: string | null };
  limits: { max_refs_per_request: number; max_upload_bytes: number; ref_types: string[] };
}

export interface RefMeta {
  ref_id: string;
  ref_type: string;
  original_name: string;
  sha256: string;
  width: number;
  height: number;
}

export interface GenerateRequest {
  collection: string;
  genre: string;
  object_name: string;
  object_description: string;
  category: 1 | 2 | 3 | 4;
  wishes: string;
  provider: string;
  model: string;
  reference_ids: string[];
  reference_policy_accepted: boolean;
  aspect_ratio: string;
  confirmed: boolean;
}

export interface PreviewResponse {
  effective_prompt: string;
  layers: Record<string, string>;
  warnings: string[];
  estimated_cost_usd: number;
  reference_count: number;
  budget: Budget;
  blocked: { code: string; message: string } | null;
}

export interface Artifact {
  artifact_id: string;
  created_at: string;
  origin: string;
  is_mock: boolean;
  image: { url: string; width: number; height: number; bytes: number; sha256: string };
  card: { object_name: string; genre: string; category: number };
  provider: { id: string; model: string; label: string; model_status: string; model_notes: string[] };
  prompt: { effective: string; warnings: string[] };
  references: { ref_id: string; role: string }[];
  cost: { estimated_usd: number; usage_metadata: Record<string, number> };
  timing: { latency_ms: number; attempts: number };
  review: { technical_qa: string; art_review: string };
}
