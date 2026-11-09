import type {
  ReplacementSuggestion,
  PlanRegenerateRequest,
  CostReport,
  Artifact,
  CardComparison,
  CollectionOverview,
  ComparisonGrid,
  ExportRecord,
  JobPromptsResponse,
  PlanOnlyRequest,
  PromptsPreviewResponse,
  SessionCheckResponse,
  PlanOnlyResult,
  SelectionExportItem,
  GenerateRequest,
  GenreInfo,
  JobDetail,
  JobEvent,
  JobPreview,
  JobRequestPayload,
  JobSummary,
  ModelsResponse,
  Preset,
  PreviewResponse,
  PromptTemplate,
  PromptVersion,
  QAReport,
  RefMeta,
  SettingsView,
} from "../types/api";

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError("network", "Cannot reach the backend on 127.0.0.1:8000. Is it running?", 0);
  }
  if (!res.ok) {
    let code = "error";
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      const d = body.detail;
      if (d && typeof d === "object" && !Array.isArray(d)) {
        code = d.code ?? code;
        message = d.message ?? message;
      } else if (Array.isArray(d)) {
        message = d.map((x: { msg: string }) => x.msg).join("; ");
        code = "validation";
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(code, message, res.status);
  }
  return (await res.json()) as T;
}

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});
const json = (body: unknown) => send("POST", body);

export const api = {
  costs: () => call<CostReport>("/api/costs"),
  models: () => call<ModelsResponse>("/api/models"),
  checkAvailability: () => call<unknown>("/api/models/check-availability", { method: "POST" }),
  settings: () => call<SettingsView>("/api/settings"),
  references: () => call<RefMeta[]>("/api/references"),
  uploadReference: (file: File, refType: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("ref_type", refType);
    return call<RefMeta>("/api/references", { method: "POST", body: fd });
  },
  deleteReference: (id: string) => call<{ deleted: boolean }>(`/api/references/${id}`, { method: "DELETE" }),
  preview: (req: GenerateRequest) => call<PreviewResponse>("/api/prompt/preview", json(req)),
  generate: (req: GenerateRequest) => call<Artifact>("/api/generate-card", json(req)),
  artifacts: () => call<Artifact[]>("/api/artifacts"),
  artifact: (id: string) => call<Artifact>(`/api/artifacts/${id}`),
  importSample: (file: File, f: { provider: string; model: string; prompt: string; object: string }) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("provider_label", f.provider);
    fd.append("model_label", f.model);
    fd.append("prompt_note", f.prompt);
    fd.append("object_name", f.object);
    return call<Artifact>("/api/imports", { method: "POST", body: fd });
  },

  genres: () => call<GenreInfo[]>("/api/planner/genres"),
  jobsPreview: (req: JobRequestPayload) => call<JobPreview>("/api/jobs/preview", json(req)),
  createJob: (req: JobRequestPayload) => call<JobDetail>("/api/jobs", json(req)),
  batch: (req: JobRequestPayload, genres: string[]) => call<JobDetail[]>("/api/collection/batch", json({ ...req, genres })),
  jobs: () => call<JobSummary[]>("/api/jobs"),
  job: (id: string) => call<JobDetail>(`/api/jobs/${id}`),
  jobEvents: (id: string, after = 0) => call<JobEvent[]>(`/api/jobs/${id}/events?after=${after}`),
  continueJob: (id: string, confirmed: boolean, retryFailed: boolean) =>
    call<JobDetail>(`/api/jobs/${id}/continue`, json({ confirmed, retry_failed: retryFailed })),
  cancelJob: (id: string) => call<JobDetail>(`/api/jobs/${id}/cancel`, { method: "POST" }),

  qaReport: (id: string) => call<QAReport>(`/api/jobs/${id}/qa`),
  runQa: (id: string) => call<QAReport>(`/api/jobs/${id}/qa`, { method: "POST" }),
  reviewCard: (job: string, card: string, decision: "approved" | "rejected" | "pending", note = "") =>
    call<JobDetail>(`/api/jobs/${job}/cards/${card}/review`, json({ decision, note })),
  regenerateCard: (job: string, card: string, confirmed: boolean, note: string) =>
    call<JobDetail>(`/api/jobs/${job}/cards/${card}/regenerate`, json({ confirmed, note })),
  replaceCard: (job: string, card: string, object_name: string, object_description: string, extra?: { rationale?: string; characters?: number | null }) =>
    call<JobDetail>(`/api/jobs/${job}/cards/${card}`, send("PATCH", { object_name, object_description, ...(extra ?? {}) })),
  suggestReplacement: (job: string, card: string) =>
    call<ReplacementSuggestion>(`/api/jobs/${job}/cards/${card}/suggest-replacement`, json({})),
  approveVariant: (job: string, variant: string) =>
    call<JobDetail>(`/api/jobs/${job}/variants/${variant}/approve`, json({ mark_cards_approved: true })),
  removeVariant: (job: string, variant: string) => call<JobDetail>(`/api/jobs/${job}/variants/${variant}`, { method: "DELETE" }),
  unapproveVariant: (job: string, variant: string) =>
    call<JobDetail>(`/api/jobs/${job}/variants/${variant}/unapprove`, { method: "POST" }),

  comparisonGrid: (job: string) => call<ComparisonGrid>(`/api/jobs/${job}/comparison`),
  cardComparison: (job: string, card: string) => call<CardComparison>(`/api/jobs/${job}/cards/${card}/comparison`),
  setComparisonRef: (job: string, card: string, ref: string | null) =>
    call<CardComparison>(`/api/jobs/${job}/cards/${card}/comparison-reference`, send("PATCH", { ref_id: ref })),

  prompts: () => call<PromptTemplate[]>("/api/prompts"),
  promptVersions: (name: string) => call<PromptVersion[]>(`/api/prompts/${name}/versions`),
  savePrompt: (name: string, text: string, note: string) =>
    call<PromptTemplate>("/api/prompts", json({ name, text, note })),
  restorePrompt: (name: string, version: number) =>
    call<PromptTemplate>(`/api/prompts/${name}/restore`, json({ version })),

  templates: () => call<Preset[]>("/api/templates"),
  createTemplate: (name: string, payload: Record<string, unknown>) => call<Preset>("/api/templates", json({ name, payload })),
  deleteTemplate: (id: number) => call<{ deleted: boolean }>(`/api/templates/${id}`, { method: "DELETE" }),

  overview: (collection = "Movie Collection") =>
    call<CollectionOverview>(`/api/collection/overview?collection=${encodeURIComponent(collection)}`),
  exports: () => call<ExportRecord[]>("/api/exports"),
  saveExport: (id: string) => call<{ path: string }>(`/api/exports/${id}/save-to-downloads`, { method: "POST" }),
  exportVariant: (job_id: string, variant_id: string, flags: { include_unapproved?: boolean; allow_mock?: boolean } = {}) =>
    call<ExportRecord>("/api/exports", json({ job_id, variant_id, ...flags })),
  exportSelection: (body: { collection: string; genre: string; set_type: "standard" | "grand"; items: SelectionExportItem[] }) =>
    call<ExportRecord>("/api/exports/selection", json(body)),
  previewPrompts: (req: JobRequestPayload) => call<PromptsPreviewResponse>("/api/jobs/preview-prompts", json(req)),
  jobPrompts: (jobId: string, variantId?: string) =>
    call<JobPromptsResponse>(`/api/jobs/${jobId}/prompts${variantId ? `?variant_id=${encodeURIComponent(variantId)}` : ""}`),
  sessionCheck: (overrides: Record<string, string>) => call<SessionCheckResponse>("/api/prompts/session-check", json({ overrides })),
  planTest: (req: PlanOnlyRequest) => call<PlanOnlyResult>("/api/plan/test", json(req)),
  planRegenerate: (req: PlanRegenerateRequest) => call<PlanOnlyResult>("/api/plan/regenerate", json(req)),
  exportCollection: (flags: { include_unapproved?: boolean; allow_mock?: boolean; allow_partial?: boolean } = {}) =>
    call<ExportRecord>("/api/collection/export", json({ collection: "Movie Collection", ...flags })),
};
