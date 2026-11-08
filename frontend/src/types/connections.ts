export type ProviderId = "gemini" | "cloudflare";
export type ConnStatus = "not_configured" | "unverified" | "connected" | "invalid" | "unreachable";
export type ConnSource = "credential_manager" | "env" | "none";

export interface ConnField {
  name: string;
  label: string;
  secret: boolean;
  stored: boolean;
  from_env: boolean;
  /** value also present in .env even if a vault value exists (optional, newer backends) */
  in_env?: boolean;
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
  env_available?: boolean;
  precedence_note?: string;
  /** only on the DELETE response */
  removal_note?: string;
}

export interface ConnectionsState {
  secure_storage: { available: boolean; backend: string };
  first_run: boolean;
  providers: ProviderConn[];
}
