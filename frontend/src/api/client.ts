import type {
  Artifact,
  GenerateRequest,
  GenreInfo,
  JobDetail,
  JobEvent,
  JobPreview,
  JobRequestPayload,
  JobSummary,
  ModelsResponse,
  PreviewResponse,
  RefMeta,
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

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  models: () => call<ModelsResponse>("/api/models"),
  checkAvailability: () => call<unknown>("/api/models/check-availability", { method: "POST" }),
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
  genres: () => call<GenreInfo[]>("/api/planner/genres"),
  jobsPreview: (req: JobRequestPayload) => call<JobPreview>("/api/jobs/preview", json(req)),
  createJob: (req: JobRequestPayload) => call<JobDetail>("/api/jobs", json(req)),
  jobs: () => call<JobSummary[]>("/api/jobs"),
  job: (id: string) => call<JobDetail>(`/api/jobs/${id}`),
  jobEvents: (id: string, after = 0) => call<JobEvent[]>(`/api/jobs/${id}/events?after=${after}`),
  continueJob: (id: string, confirmed: boolean, retryFailed: boolean) =>
    call<JobDetail>(`/api/jobs/${id}/continue`, json({ confirmed, retry_failed: retryFailed })),
  cancelJob: (id: string) => call<JobDetail>(`/api/jobs/${id}/cancel`, { method: "POST" }),
};
