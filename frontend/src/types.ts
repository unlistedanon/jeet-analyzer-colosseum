export type LifecycleStatus =
  | "VERIFIED_OUT"
  | "RE_ENTERED"
  | "NOT_OUT"
  | "INSUFFICIENT_DATA"
  | "UNRESOLVED";

export type RelationshipStatus = "RESOLVED_WITHIN_SCOPE" | "UNRESOLVED" | "INSUFFICIENT_DATA";

export type InvestigationMode = "seller-scan" | "wallet-audit" | "cluster-audit";
export type InvestigationState = "queued" | "running" | "complete" | "error";
export type ProgressState = "pending" | "running" | "complete" | "incomplete" | "error";

export interface TokenMetadata {
  mint?: string;
  symbol?: string | null;
  name?: string | null;
  decimals?: number;
  supply?: number | string | null;
  token_program?: string | null;
  token_standard?: string | null;
  [key: string]: unknown;
}

export interface ProgressEvent {
  sequence?: number;
  timestamp?: string;
  phase: string;
  state: ProgressState;
  message: string;
  count?: number;
  progress?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface ProgressRecord {
  phases: Record<string, ProgressState>;
  events: ProgressEvent[];
}

export interface ProviderCoverage {
  complete: boolean;
  seed_wallet_complete?: boolean;
  downstream_complete?: boolean;
  requests?: Array<Record<string, unknown>>;
  scope?: string;
  limitation?: string | null;
  failure_category?: string | null;
  [key: string]: unknown;
}

export interface RequestTelemetry {
  total_provider_requests?: number;
  rpc_requests?: number;
  das_requests?: number;
  rpc_requests_by_method?: Record<string, number>;
  das_requests_by_method?: Record<string, number>;
  cache_hits?: number;
  cache_misses?: number;
  cache_hits_by_namespace?: Record<string, number>;
  cache_misses_by_namespace?: Record<string, number>;
  provider_requests_avoided?: number;
  duplicate_evidence_observations?: number;
  wallet_traversals_suppressed?: number;
  retry_attempts?: number;
  wallets_traversed?: number;
  signatures_examined?: number;
  transactions_fetched?: number;
  pages_fetched?: number;
  estimated_provider_credits?: number;
  estimated_provider_credits_by_method?: Record<string, number>;
  estimated_provider_credit_ceiling?: number;
  estimated_provider_credit_cost_is_authoritative?: false;
  estimated_provider_credit_model?: string;
  budget_limits?: Record<string, number>;
  terminated_by_budget?: string | null;
  termination_reason?: string | null;
  [key: string]: unknown;
}

export interface RelationshipEdge {
  source?: string;
  destination?: string;
  relationship_type?: string;
  relationship_context?: string | null;
  classification?: string;
  asset?: string;
  mint?: string;
  amount_raw?: number | string;
  token_amount_raw?: number | string;
  signature?: string;
  timestamp?: string;
  block_time?: number;
  source_signed?: boolean;
  source_paid_fee?: boolean;
  fee_payer?: string;
  market_context?: boolean;
  common_control?: string;
  confidence?: string | number;
  reason?: string;
  [key: string]: unknown;
}

export interface SharedFunderRelationship {
  funder?: string;
  recipients?: string[];
  relationship_type?: string;
  classification?: string;
  recipient_pairs?: Array<Record<string, unknown>>;
  supporting_transfers?: RelationshipEdge[];
  common_control?: string;
  reason?: string;
  [key: string]: unknown;
}

export interface SellerRow {
  rank?: number | string;
  wallet?: string;
  sells?: number | string;
  tokens_sold_raw?: number | string;
  quote_received?: unknown;
  quote_totals?: Record<string, unknown>;
  largest_sale_raw?: number | string;
  current_balance_raw?: number | string | null;
  observed_inventory_remaining_percentage?: number | string | null;
  most_recent_sell?: string | number | null;
  status?: string;
  [key: string]: unknown;
}

export interface AnalyzerResult {
  schema?: string;
  schema_version?: string;
  status?: string;
  token?: TokenMetadata;
  mint?: string;
  seed_wallet?: string;
  window?: Record<string, unknown>;
  historical_exit_status?: LifecycleStatus;
  current_wallet_status?: LifecycleStatus;
  wallet_status?: LifecycleStatus;
  cluster_status?: LifecycleStatus | null;
  target_cluster_status?: LifecycleStatus | null;
  relationship_status?: RelationshipStatus | null;
  current_inventory?: Record<string, unknown>;
  seed_wallet_accounting?: Record<string, unknown>;
  lifecycle?: Record<string, unknown>;
  sales?: Record<string, unknown>;
  buys_reacquisitions?: Record<string, unknown>;
  transfers?: Record<string, unknown>;
  proceeds?: Record<string, unknown>;
  wallet_relationships?: RelationshipEdge[];
  wallet_graph?: {
    nodes?: Array<Record<string, unknown>>;
    edges?: RelationshipEdge[];
    shared_funders?: SharedFunderRelationship[];
    [key: string]: unknown;
  };
  shared_funders?: SharedFunderRelationship[];
  related_target_token_inventory?: Array<Record<string, unknown>>;
  excluded_infrastructure?: Array<Record<string, unknown>>;
  unresolved_evidence?: Array<Record<string, unknown>>;
  provider_coverage?: ProviderCoverage;
  request_telemetry?: RequestTelemetry;
  progress?: ProgressRecord;
  evidence_receipts?: Record<string, unknown>;
  common_control?: "NOT_PROVEN";
  sellers?: SellerRow[];
  seller_rows?: SellerRow[];
  observed_partial_events?: number;
  events?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface InvestigationRequest {
  mint: string;
  wallet?: string;
  days: number;
  max_pages: number;
  graph_depth?: number;
  trace_depth?: number;
  max_wallets?: number;
  max_rpc_requests: number;
  max_signatures: number;
  max_transactions: number;
  request_timeout: number;
  provider_retries: number;
  provider_backoff_cap?: number;
  funding_lookback_days?: number;
  materiality_inventory_pct?: number;
}

export interface InvestigationView {
  id: string;
  mode: InvestigationMode;
  state: InvestigationState;
  created_at: string;
  updated_at: string;
  request: InvestigationRequest;
  progress: ProgressRecord;
  result?: AnalyzerResult | null;
  error?: string | null;
  artifacts: Record<string, string>;
}

export interface BetaSession {
  public_access?: boolean;
  authenticated: boolean;
  beta_enabled: boolean;
  beta_configured: boolean;
  code_id?: string | null;
  limits?: {
    runs_per_code_per_day: number;
    estimated_credits_per_run: number;
    global_daily_estimated_credits?: number;
    max_concurrent?: number;
  };
}

export interface BetaProgressEvent {
  stage: string;
  state: "pending" | "running" | "complete" | "error";
  timestamp: string;
  message: string;
}

export interface BetaInvestigationView {
  investigation_id: string;
  status: "queued" | "running" | "complete" | "failed";
  stage: string;
  created_at: string;
  updated_at: string;
  request: InvestigationRequest;
  progress: { stage: string; events: BetaProgressEvent[] };
  result?: AnalyzerResult | null;
  error?: string | null;
  termination_reason?: string | null;
  safe_report_available: boolean;
  estimated_provider_credits_reserved: number;
  estimated_provider_credits_used: number;
  credit_cost_is_estimated: true;
  deduplicated: boolean;
}

export interface BetaAdminMetrics {
  usage?: { day: string; counts: Record<string, number> } | null;
  completed_visitors?: number;
  day: string;
  investigations: number;
  running: number;
  queued: number;
  complete: number;
  failed: number;
  estimated_credits: number;
  budget_exhausted: number;
  average_duration_seconds?: number | null;
  feedback_count: number;
  by_code: Array<{ code_id: string; investigations: number; estimated_credits: number }>;
  recent_feedback: Array<{
    id: string;
    investigation_id: string;
    code_id: string;
    created_at: string;
    useful: number | null;
    comment: string;
  }>;
  estimated_credit_note: string;
}
