export type ProviderId = "gemini" | "cloudflare";
export type ConnStatus = "not_configured" | "unverified" | "connected" | "invalid" | "unreachable";
export type ConnSource = "credential_manager" | "env" | "none";

export interface ConnField {
  name: string;
  label: string;
  secret: boolean;
  stored: boolean;
  from_env: boolean;
}

export interface ConnHelp {
  get_credentials_url: string;
  steps: string[];
}

export interface TestResult {
  ok: boolean;
  status: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface ProviderConn {
  id: ProviderId;
  label: string;
  configured: boolean;
  source: ConnSource;
  status: ConnStatus;
  last_tested_at: string | null;
  message: string | null;
  fields: ConnField[];
  help: ConnHelp;
  test_result?: TestResult | null;
}

export interface ConnectionsState {
  secure_storage: { available: boolean; backend: string };
  first_run: boolean;
  providers: ProviderConn[];
}
