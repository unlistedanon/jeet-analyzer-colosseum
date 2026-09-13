import type { AnalyzerResult, RelationshipEdge } from "./types";

export function shortAddress(value?: string | null, length = 5) {
  if (!value) return "UNKNOWN";
  return value.length <= length * 2 + 2 ? value : `${value.slice(0, length)}…${value.slice(-length)}`;
}

export function formatStatus(value?: string | null) {
  return value ? value.replaceAll("_", " ") : "NOT AVAILABLE";
}

export function statusTone(value?: string | null) {
  if (value === "VERIFIED_OUT" || value === "RESOLVED_WITHIN_SCOPE") return "verified";
  if (value === "RE_ENTERED") return "reentered";
  if (value === "NOT_OUT") return "notout";
  if (value === "INSUFFICIENT_DATA") return "insufficient";
  return "unresolved";
}

export function numberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
}

export function formatRawAmount(value: unknown, decimals = 0, compact = false) {
  const numeric = numberValue(value);
  if (numeric === null) return "UNKNOWN";
  const adjusted = numeric / 10 ** decimals;
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: compact ? 2 : Math.min(Math.max(decimals, 0), 9),
    notation: compact ? "compact" : "standard",
  }).format(adjusted);
}

export function tokenDecimals(result: AnalyzerResult) {
  return numberValue(result.token?.decimals) ?? 0;
}

export function relationships(result: AnalyzerResult): RelationshipEdge[] {
  return result.wallet_relationships ?? result.wallet_graph?.edges ?? [];
}

export function seedAccounting(result: AnalyzerResult) {
  const accounting = result.seed_wallet_accounting;
  return accounting && typeof accounting === "object" ? accounting as Record<string, unknown> : {};
}

export function currentSeedBalance(result: AnalyzerResult) {
  return result.current_inventory?.seed_target_token_balance_raw
    ?? seedAccounting(result).current_target_token_balance_raw;
}

export function coverageComplete(result: AnalyzerResult) {
  return result.provider_coverage?.complete === true;
}

export function timeText(value: unknown) {
  if (typeof value === "number") return new Date(value * 1000).toLocaleString();
  if (typeof value === "string" && value) {
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric > 1_000_000_000) return new Date(numeric * 1000).toLocaleString();
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) return parsed.toLocaleString();
  }
  return "UNKNOWN";
}

export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
}
