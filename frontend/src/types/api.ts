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
  defaults: { provider: string; model: string; category_distribution: Record<string, number> };
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

// ---- Phase 3: sets / jobs -------------------------------------------------------------------
export interface GenreInfo { genre: string; microthemes: string[]; max_variants: number }

export type DistributionPayload =
  | { mode: "auto_balanced" }
  | { mode: "custom"; counts: Record<string, number> };

export interface JobRequestPayload {
  collection: string;
  genre: string;
  wishes: string;
  number_of_variants: number;
  category_distribution: DistributionPayload;
  provider: string;
  model: string;
  reference_ids: string[];
  reference_policy_accepted: boolean;
  aspect_ratio: string;
  scope: "pilot" | "full";
  seed: number | null;
  confirmed: boolean;
}

export interface PlanCardLite { position: number; category: number; object_name: string; object_description: string }
export interface PlanVariantLite { variant_id: string; title: string; concept: string; cards: PlanCardLite[] }

export interface JobPreview {
  plan: { genre: string; seed: number; variants: PlanVariantLite[]; notes: string[] };
  distribution: Record<string, number>;
  scope: "pilot" | "full";
  images_in_scope: number;
  images_total: number;
  estimated_cost_usd: number;
  estimated_cost_full_usd: number;
  budget: Budget;
  blocked: { code: string; message: string } | null;
  warnings: string[];
}

export interface JobCard {
  card_id: string;
  position: number;
  category: number;
  object_name: string;
  object_description: string;
  status: "planned" | "queued" | "generating" | "done" | "failed";
  artifact_id: string | null;
  error: string | null;
  reference_selection: string | null;
  image_url: string | null;
}
export interface JobVariant { variant_id: string; title: string; concept: string; cards: JobCard[] }

export interface JobDetail {
  job_id: string;
  status: string;
  error: string | null;
  scope: "pilot" | "full";
  created_at: string;
  genre: string;
  seed: number;
  distribution: Record<string, number>;
  notes: string[];
  variants: JobVariant[];
  counts: { planned: number; queued: number; generating: number; done: number; failed: number; total: number };
  spent_estimated_usd: number;
  pending_estimate_usd: number;
  active: boolean;
  can_continue: boolean;
  request: JobRequestPayload;
  budget: Budget;
}
export interface JobSummary { job_id: string; status: string; created_at: string; genre: string; variants: number; spent_est: number }
export interface JobEvent { id: number; ts: string; level: string; message: string }
