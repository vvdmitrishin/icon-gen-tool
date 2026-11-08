import type { ConnectionsState, ProviderConn, ProviderId, TestResult } from "../types/connections";

export class ConnError extends Error {
  constructor(public code: string, message: string, public status: number) {
    super(message);
  }
}

// Per-process token: memory only (never persisted).
let sessionToken: string | null = null;

async function raw(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(path, init);
  } catch {
    throw new ConnError("network", "Cannot reach the backend on 127.0.0.1:8000. Is it running?", 0);
  }
}

async function fail(res: Response): Promise<never> {
  let code = "error";
  let message = `Request failed (${res.status})`;
  try {
    const body = (await res.json()) as { detail?: unknown };
    const d = body.detail;
    if (Array.isArray(d)) {
      message = d.map((x) => (x && typeof x === "object" && "msg" in x ? String((x as { msg: unknown }).msg) : "invalid input")).join("; ");
      code = "validation";
    } else if (d && typeof d === "object") {
      const o = d as { code?: string; message?: string };
      code = o.code ?? code;
      message = o.message ?? message;
    }
  } catch {
    /* non-JSON body */
  }
  if (res.status === 403 && code === "error") {
    message = "The app blocked this request to protect your credentials. Reload the page and try again.";
    code = "forbidden";
  }
  throw new ConnError(code, message, res.status);
}

async function getToken(force = false): Promise<string> {
  if (sessionToken && !force) return sessionToken;
  const res = await raw("/api/session");
  if (!res.ok) await fail(res);
  const body = (await res.json()) as { token?: string };
  if (!body.token) throw new ConnError("session", "Could not start a protected session.", 0);
  sessionToken = body.token;
  return sessionToken;
}

async function read<T>(path: string): Promise<T> {
  const res = await raw(path);
  if (!res.ok) await fail(res);
  return (await res.json()) as T;
}

async function write<T>(method: "PUT" | "POST" | "DELETE", path: string, body?: unknown): Promise<T> {
  for (let attempt = 0; attempt < 2; attempt++) {
    const token = await getToken(attempt > 0);
    const res = await raw(path, {
      method,
      headers: { "content-type": "application/json", "X-Studio-Token": token },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 403 && attempt === 0) {
      sessionToken = null; // token may be stale (backend restarted): refetch once
      continue;
    }
    if (!res.ok) await fail(res);
    return (await res.json()) as T;
  }
  throw new ConnError("forbidden", "The app blocked this request.", 403);
}

const p = (id: ProviderId) => `/api/connections/${id}`;

export const connectionsApi = {
  get: () => read<ConnectionsState>("/api/connections"),
  save: (id: ProviderId, values: Record<string, string>, test: boolean) =>
    write<ProviderConn>("PUT", p(id), { values, test }),
  remove: (id: ProviderId) => write<ProviderConn>("DELETE", p(id)),
  test: (id: ProviderId) => write<TestResult>("POST", `${p(id)}/test`),
};
