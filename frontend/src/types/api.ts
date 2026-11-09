export interface ModelInfo {
  provider_id: string;
  provider_label: string;
  model_id: string;
  label: string;
  role: "primary" | "fallback" | "planned" | "test";
  status: string;
  selectable: boolean;
  disabled_reason: string | null;
  capabilities: ModelCapabilities;
  est_cost_per_image_usd: number | null;
  origin_badge: string;
  availability_checked: boolean;
  /** Last free connection test of this model's provider (backend is the single source of truth). */
  connection_status?: string | null;
  connection_message?: string | null;
  /** Provider/runtime grouping (optional: older backends omit it). */
  runtime?: "cloud_api" | "local" | "test";
  runtime_label?: string;
  runtime_requirements?: string | null;
  options?: ModelOption[];
  price_note?: string | null;
}

/** `null` means "not verified" - never rendered as supported. */
export interface ModelCapabilities {
  supports_reference_images: boolean;
  max_reference_images: number | null;
  aspect_ratios: string[];
  text_to_image?: boolean | null;
  image_editing?: boolean | null;
  notes?: string | null;
  /** false = expectations for a planned model, not verified in this app */
  verified?: boolean;
}

export interface ModelOption {
  key: string;
  label: string;
  default: boolean;
  status: string;
  description?: string;
}

export interface Budget {
  paid_calls_enabled: boolean;
  job_budget_usd: number;
  spent_estimated_usd: number; // lifetime estimate = planning + generation
  planning_estimated_usd: number;
  generation_estimated_usd: number;
  max_card_retries: number;
  note: string;
}

/** Estimated (not billed) cost of one batch. planning_known is false for batches from before per-batch planning costs were recorded. */
export interface BatchCosts {
  planning_usd: number;
  generation_usd: number;
  total_usd: number;
  images_billed: number;
  planning_known: boolean;
}

export interface CostReport {
  planning_usd: number;
  generation_usd: number;
  total_usd: number;
  planning_calls: number;
  image_calls: number;
  cost_is_estimate: boolean;
  unattributed: { planning_usd: number; generation_usd: number; planning_calls: number; image_calls: number };
  incomplete_note: string;
}

export interface ModelsResponse {
  models: ModelInfo[];
  defaults: { provider: string; model: string; category_distribution: Record<string, number> };
  budget: Budget;
  /** text model and price constants of ONE AI planning call: cost = base_usd + per_card_usd * variants * cards */
  planner?: { text_model: string; input_usd_per_m: number; output_usd_per_m: number; base_usd: number; per_card_usd: number };
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
  /** Target icon size in px (square). Models render natively; smaller sizes are downscaled. */
  output_size?: number;
  confirmed: boolean;
  /** Session-only prompt overrides (template name -> text); never stored permanently. */
  prompt_overrides?: Record<string, string>;
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
  is_imported?: boolean;
  image: { url: string; width: number; height: number; bytes: number; sha256: string };
  card: { object_name: string; genre: string; category: number };
  provider: { id: string; model: string; label: string; model_status: string; model_notes: string[] };
  prompt: {
    effective: string;
    warnings: string[];
    bundle?: Record<string, { source: string; version: number | null; base_version?: number | null }>;
    session_overrides?: string[];
    template_versions?: Record<string, number | null>;
    planning_context?: { collection?: string; set?: string; variant_title?: string; variant_concept?: string; rationale?: string };
  };
  references: { ref_id: string; role: string }[];
  cost: { estimated_usd: number; usage_metadata: Record<string, number> };
  timing: { latency_ms: number; attempts: number };
  review: { technical_qa: string; art_review: string };
}

// ---- sets / jobs ----------------------------------------------------------------------------
export interface GenreInfo { genre: string; max_variants: number }

export type DistributionPayload =
  | { mode: "auto_balanced" }
  | { mode: "custom"; counts: Record<string, number> };

export interface JobRequestPayload {
  collection: string;
  genre: string;
  wishes: string;
  number_of_variants: number;
  number_of_cards: number;
  gold_cards: number;
  /** 'grand' = Grand Set (All Golden): the server forces gold = cards and Cat. 1-4 = 0 */
  set_type?: "standard" | "grand";
  output_size: number;
  /** true = the user agreed to one small AI text call to plan custom themes */
  allow_ai_plan: boolean;
  /** Session-only prompt overrides; the server snapshots them per job (nothing is stored in the permanent library). */
  prompt_overrides?: Record<string, string>;
  category_distribution: DistributionPayload;
  provider: string;
  model: string;
  reference_ids: string[];
  reference_policy_accepted: boolean;
  aspect_ratio: string;
  scope: "full";
  seed: number | null;
  confirmed: boolean;
  /** a plan curated in the Metaphor Planner: the server uses it as is instead of planning again */
  plan?: PlanOnlyResult["plan"];
}

export type PlanningMode = "rules" | "ai";

export interface PlanCardLite { position: number; category: number; object_name: string; object_description: string }
export interface PlanVariantLite { variant_id: string; title: string; concept: string; cards: PlanCardLite[] }

export interface JobPreview {
  plan: { genre: string; seed: number; variants: PlanVariantLite[]; notes: string[] };
  distribution: Record<string, number>;
  number_of_cards?: number;
  gold_cards?: number;
  set_type?: "standard" | "grand";
  output_size?: number;
  planning_mode?: PlanningMode;
  plan_cost_estimate_usd?: number;
  /** backend compliance flags (optional) */
  compliant?: boolean;
  compliance_note?: string | null;
  scope: "pilot" | "full";
  images_in_scope: number;
  images_total: number;
  estimated_cost_usd: number;
  estimated_cost_full_usd: number;
  budget: Budget;
  blocked: { code: string; message: string } | null;
  warnings: string[];
}

export interface QACheck { id: string; name: string; status: "pass" | "warn" | "fail"; detail: string }
export type QAStatus = "passed" | "flagged" | "failed" | "not_run";
export type ArtReview = "pending" | "approved" | "rejected";

export interface CardVersion {
  version: number;
  artifact_id: string;
  image_url: string;
  created_at: string;
  note: string | null;
  qa_status: QAStatus;
  art_review: ArtReview;
  is_current: boolean;
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
  qa_status: QAStatus;
  qa: { status: QAStatus; checks: QACheck[] } | null;
  art_review: ArtReview | null;
  review_note: string | null;
  regen_count: number;
  regen_remaining: number;
  spec_version: number;
  versions: CardVersion[];
}

export interface SetQA {
  variant_id: string;
  status: Exclude<QAStatus, "not_run">;
  checks: QACheck[];
  generated: number;
  total: number;
  art_review: { approved: number; pending: number; rejected: number };
}

export interface JobVariant {
  variant_id: string;
  title: string;
  concept: string;
  cards: JobCard[];
  approved: boolean;
  approved_at: string | null;
  set_qa: SetQA;
}

export interface JobDetail {
  job_id: string;
  status: string;
  error: string | null;
  scope: "pilot" | "full";
  created_at: string;
  updated_at: string;
  genre: string;
  collection: string;
  seed: number;
  number_of_cards?: number;
  gold_cards?: number;
  set_type?: "standard" | "grand";
  output_size?: number;
  planning_mode?: PlanningMode;
  distribution: Record<string, number>;
  notes: string[];
  variants: JobVariant[];
  counts: { planned: number; queued: number; generating: number; done: number; failed: number; total: number };
  spent_estimated_usd: number;
  costs: BatchCosts;
  pending_estimate_usd: number;
  active: boolean;
  can_continue: boolean;
  approved_variant_id: string | null;
  max_regenerations_per_card: number;
  request: JobRequestPayload;
  budget: Budget;
}
export interface JobSummary {
  job_id: string;
  status: string;
  created_at: string;
  genre: string;
  collection: string;
  variants: number;
  number_of_cards?: number;
  gold_cards?: number;
  output_size?: number;
  planning_mode?: PlanningMode;
  set_type?: "standard" | "grand";
  spent_est: number;
  provider?: string | null;
  model?: string | null;
  costs: BatchCosts;
  approved_variant_id: string | null;
}
export interface JobEvent { id: number; ts: string; level: string; message: string }

export interface QAReport {
  job_id: string;
  genre: string;
  generated_at: string;
  limitations: string;
  technical: Record<"passed" | "flagged" | "failed" | "not_run", number>;
  cards: Record<string, { status: QAStatus; checks: QACheck[]; art_review: ArtReview | null; object_name: string; category: number }>;
  variants: Record<string, SetQA>;
  job_checks: QACheck[];
  approved_variant_id: string | null;
}

// ---- Reference Compare ----------------------------------------------------------------------
export interface CompareRef {
  ref_id: string;
  ref_type: string;
  original_name: string;
  width: number;
  height: number;
  mode: "auto" | "manual" | "candidate";
  image_url: string;
  thumbnail_url: string;
  suggested?: boolean;
}
export interface CardComparison {
  card_id: string;
  variant_id: string;
  position: number;
  category: number;
  object_name: string;
  status: string;
  image_url: string | null;
  versions: { version: number; image_url: string; is_current: boolean; qa_status: QAStatus }[];
  reference: CompareRef | null;
  candidates: CompareRef[];
  message: string | null;
}
export interface ComparisonPair {
  card_id: string;
  variant_id: string;
  position: number;
  category: number;
  object_name: string;
  status: string;
  image_url: string | null;
  qa_status: QAStatus;
  art_review: ArtReview | null;
  reference: CompareRef | null;
}
export interface ComparisonGrid { job_id: string; pairs: ComparisonPair[]; reference_count: number }

// ---- Prompt Library / Templates / Settings --------------------------------------------------
export interface PromptTemplate {
  name: string;
  label: string;
  version: number;
  text: string;
  author: string;
  created_at: string;
  note: string | null;
  is_default_text: boolean;
  versions: number[];
}
export interface PromptVersion { name: string; version: number; text: string; author: string; created_at: string; note: string | null; is_active: number }
export interface Preset { id: number; name: string; kind: "builtin" | "user"; payload: Record<string, unknown>; created_at: string }

export interface SettingsView {
  version: string;
  spec_version: string;
  providers: {
    gemini: { enabled: boolean; key_configured: boolean; default_model: string; est_cost_per_image_usd: number };
    cloudflare: { token_set: boolean; account_id_set: boolean; configured: boolean };
    openai: { enabled: boolean; paid_calls_enabled: boolean; key_configured: boolean };
    mock: { enabled: boolean };
  };
  budget: Budget;
  limits: Record<string, number>;
  storage: Record<string, string>;
  how_to_change: string;
}

// ---- Collection / export --------------------------------------------------------------------
export interface CollectionGenre {
  genre: string;
  jobs: number;
  job_id: string | null;
  status: string | null;
  approved_variant_id: string | null;
  generated: number;
  ready: boolean;
  blockers: string[];
  contains_mock_images: boolean;
}
export interface CollectionOverview { collection: string; genres: CollectionGenre[]; final_ready: boolean; blockers: string[]; note: string }
export interface ExportRecord {
  export_id: string;
  created_at: string;
  kind: "variant" | "collection" | "selection";
  /** jobs the export was built from (present on current backends) */
  job_ids?: string[];
  size: number;
  download_url: string;
  filename: string;
  genre?: string;
  variant_id?: string;
  approved?: boolean;
  partial?: boolean;
  contains_mock_images: boolean;
  /** selection exports: number of images, compliance warnings (never blocking) */
  count?: number;
  warnings?: string[];
  standard_ten_card_set?: boolean | null;
}

export interface PromptRow {
  position: number;
  category: number;
  variant_id: string;
  object_name: string;
  card_id?: string | null;
  /** generated = the immutable prompt stored with the image; compiled = preview from the same compiler */
  source: "generated" | "compiled";
  artifact_id?: string;
  prompt: string;
  warnings: string[];
  model: string;
  provider: string;
  references: { ref_id: string; role: string }[];
  reference_selection?: string | null;
  bundle: Record<string, { source: string; version: number | null; base_version?: number | null }>;
  session_overrides: string[];
}

export interface JobPromptsResponse { job_id: string; prompts: PromptRow[]; snapshot: boolean }
export interface PromptsPreviewResponse { variants: Record<string, PromptRow[]>; planning_mode: PlanningMode; session_overrides: string[]; warnings: string[] }
export interface SessionCheckResponse { ok: boolean; warnings: string[] }

export interface SelectionExportItem { job_id: string; card_id: string; artifact_id: string }

export interface PlanOnlyRequest {
  collection: string;
  genre: string;
  wishes: string;
  number_of_variants: number;
  number_of_cards: number;
  gold_cards: number;
  set_type: "standard" | "grand";
  category_distribution?: unknown;
  seed: number | null;
}

export interface PlanRegenerateRequest extends PlanOnlyRequest {
  plan: PlanOnlyResult["plan"];
  selected: { variant_id: string; position: number }[];
}

export interface PlanOnlyResult {
  regenerated?: { variant_id: string; position: number }[];
  plan: JobPreview["plan"];
  distribution: Record<string, number>;
  set_type: "standard" | "grand";
  planning_mode: PlanningMode;
  text_model: string | null;
  cached: boolean;
  text_cost_estimate_usd: number;
  history_subjects_used: number;
  history: string[];
  image_provider_called: boolean;
}
