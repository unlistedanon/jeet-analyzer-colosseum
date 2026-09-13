import type {
  BetaAdminMetrics,
  BetaInvestigationView,
  BetaSession,
  InvestigationMode,
  InvestigationRequest,
  InvestigationView,
} from "./types";

const API_ROOT = import.meta.env.VITE_API_ROOT ?? "/api/v1";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const detail = typeof body.detail === "string"
      ? body.detail
      : typeof body.detail?.message === "string"
        ? body.detail.message
        : JSON.stringify(body.detail ?? body);
    throw new Error(detail || `API request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  return requestJson<T>(`${API_ROOT}${path}`, init);
}

export function startInvestigation(mode: InvestigationMode, request: InvestigationRequest) {
  const payload: Record<string, unknown> = { ...request };
  if (mode === "seller-scan") {
    delete payload.wallet;
    delete payload.graph_depth;
    delete payload.trace_depth;
    delete payload.max_wallets;
    delete payload.funding_lookback_days;
    delete payload.materiality_inventory_pct;
  } else if (mode === "wallet-audit") {
    delete payload.graph_depth;
    delete payload.max_wallets;
    delete payload.funding_lookback_days;
    delete payload.materiality_inventory_pct;
  } else {
    delete payload.trace_depth;
  }
  return apiRequest<InvestigationView>(`/investigations/${mode}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getInvestigation(identifier: string) {
  return apiRequest<InvestigationView>(`/investigations/${identifier}`);
}

export function receiptUrl(identifier: string) {
  return `${API_ROOT}/investigations/${identifier}/receipt`;
}

export function eventsUrl(identifier: string) {
  return `${API_ROOT}/investigations/${identifier}/events`;
}

const BETA_ROOT = "/api/beta";

function betaRequest<T>(path: string, init?: RequestInit): Promise<T> {
  return requestJson<T>(path, { credentials: "same-origin", ...init });
}

export async function trackUsage(event: "visit" | "example_opened" | "scan_attempt" | "result_viewed", investigationId?: string) {
  try {
    await betaRequest<{ stored: boolean }>(`${BETA_ROOT}/usage`, {
      method: "POST", body: JSON.stringify({ event, ...(investigationId ? { investigation_id: investigationId } : {}) }),
    });
  } catch { /* Optional usage counts never block the user journey. */ }
}

export function getBetaSession() {
  return betaRequest<BetaSession>(`${BETA_ROOT}/session`);
}

export function enterBeta(accessCode: string) {
  return betaRequest<BetaSession>(`${BETA_ROOT}/session`, {
    method: "POST",
    body: JSON.stringify({ access_code: accessCode }),
  });
}

export function leaveBeta() {
  return betaRequest<BetaSession>(`${BETA_ROOT}/session`, { method: "DELETE" });
}

export function startBetaInvestigation(mint: string, wallet: string) {
  return betaRequest<BetaInvestigationView>(`${BETA_ROOT}/investigations`, {
    method: "POST",
    body: JSON.stringify({ mint, wallet }),
  });
}

export function getBetaInvestigation(identifier: string) {
  return betaRequest<BetaInvestigationView>(`${BETA_ROOT}/investigations/${identifier}`);
}

export function getSafeReport(identifier: string) {
  return betaRequest<Record<string, unknown>>(`${BETA_ROOT}/investigations/${identifier}/share`);
}

export function safeReportDownloadUrl(identifier: string) {
  return `${BETA_ROOT}/investigations/${identifier}/share?download=true`;
}

export function submitBetaFeedback(identifier: string, useful: boolean | null, comment: string) {
  return betaRequest<{ feedback_id: string; stored: true }>(`${BETA_ROOT}/investigations/${identifier}/feedback`, {
    method: "POST",
    body: JSON.stringify({ useful, comment }),
  });
}

export function getAdminSession() {
  return betaRequest<{ authenticated: boolean }>(`${BETA_ROOT}/admin/session`);
}

export function enterAdmin(accessCode: string) {
  return betaRequest<{ authenticated: boolean }>(`${BETA_ROOT}/admin/session`, {
    method: "POST",
    body: JSON.stringify({ access_code: accessCode }),
  });
}

export function getAdminMetrics() {
  return betaRequest<BetaAdminMetrics>(`${BETA_ROOT}/admin/metrics`);
}
