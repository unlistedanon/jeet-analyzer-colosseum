import type { AnalyzerResult, BetaProgressEvent, InvestigationRequest } from "./types";

export interface OperatorCaseSummary {
  investigation_id: string;
  code_id: string;
  status: "queued" | "running" | "complete" | "failed";
  stage: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  request: InvestigationRequest;
  error?: string | null;
  termination_reason?: string | null;
  estimated_provider_credits_reserved: number;
  estimated_provider_credits_used: number;
  historical_exit_status?: string | null;
  current_wallet_status?: string | null;
  target_cluster_status?: string | null;
  relationship_status?: string | null;
  cluster_status?: string | null;
  common_control?: string | null;
  provider_complete?: boolean | null;
  relationship_count: number;
}

export interface OperatorFeedback {
  id: string;
  created_at: string;
  useful: number | null;
  comment: string;
}

export interface OperatorCaseDetail {
  investigation_id: string;
  code_id: string;
  status: "queued" | "running" | "complete" | "failed";
  stage: string;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  request: InvestigationRequest;
  progress: { stage: string; events: BetaProgressEvent[] };
  result?: AnalyzerResult | null;
  safe_result?: Record<string, unknown> | null;
  safe_report_available: boolean;
  artifact_names: string[];
  error?: string | null;
  termination_reason?: string | null;
  estimated_provider_credits_reserved: number;
  estimated_provider_credits_used: number;
  feedback: OperatorFeedback[];
}

async function operatorRequest<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    throw new Error(detail || `Admin request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function getOperatorCases(limit = 100) {
  const result = await operatorRequest<{ investigations: OperatorCaseSummary[] }>(
    `/api/beta/admin/investigations?limit=${encodeURIComponent(String(limit))}`,
  );
  return result.investigations;
}

export function getOperatorCase(identifier: string) {
  return operatorRequest<OperatorCaseDetail>(
    `/api/beta/admin/investigations/${encodeURIComponent(identifier)}`,
  );
}
