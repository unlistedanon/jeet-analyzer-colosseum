import { DatabaseZap, Eye, ShieldQuestion } from "lucide-react";
import type { AnalyzerResult } from "../types";
import { formatRawAmount, record, tokenDecimals } from "../utils";

export function FlagshipDemo({ result }: { result: AnalyzerResult }) {
  const summary = record(result.demo_summary);
  const coverage = result.provider_coverage;
  const decimals = tokenDecimals(result);
  const exitSale = formatRawAmount(summary.exit_sale_raw, decimals, true);
  const exitBalance = formatRawAmount(summary.exit_balance_after_raw, decimals, true);
  const boughtBack = formatRawAmount(summary.post_exit_reacquired_raw, decimals, true);
  return (
    <section className="flagship-demo" aria-labelledby="flagship-question">
      <div className="flagship-question">
        <p className="eyebrow"><ShieldQuestion size={14} /> Eternal Week 1 / deterministic offline case</p>
        <h2 id="flagship-question">{String(result.case_question ?? "The jeet sold, but is he really out?")}</h2>
        <p className="flagship-summary"><strong>{result.seed_wallet}</strong> sold, hit zero, and then rebuilt a material position.</p>
        <div className="flagship-lifecycle" aria-label="Wallet lifecycle">
          <LifecycleStep label="Sold" value={exitSale} />
          <span className="lifecycle-arrow" aria-hidden="true">→</span>
          <LifecycleStep label="Balance hit" value={exitBalance} />
          <span className="lifecycle-arrow" aria-hidden="true">→</span>
          <LifecycleStep label="Bought back" value={boughtBack} />
        </div>
        <div className="flagship-status-transition" aria-label="Lifecycle status transition">
          <strong>{result.historical_exit_status}</strong><span aria-hidden="true">→</span><strong>{result.current_wallet_status}</strong>
        </div>
      </div>
      <div className="flagship-verdicts">
        <DemoMetric label="Historical exit" value={result.historical_exit_status} />
        <DemoMetric label="Current status" value={result.current_wallet_status} />
        <DemoMetric label="Cluster" value={result.cluster_status} />
        <DemoMetric label="Current inventory" value={formatRawAmount(summary.current_inventory_raw, decimals)} />
      </div>
      <div className="flagship-proof-grid">
        <div><Eye size={15} /><span>Genuine target-token transfers</span><strong>{String(summary.genuine_target_token_transfer_count ?? "UNKNOWN")}</strong></div>
        <div><Eye size={15} /><span>Secondary-wallet target increases</span><strong>{String(summary.secondary_wallet_target_increase_count ?? "UNKNOWN")}</strong></div>
        <div><ShieldQuestion size={15} /><span>Common control</span><strong>{result.common_control ?? "NOT_PROVEN"}</strong></div>
        <div><DatabaseZap size={15} /><span>Offline provider calls</span><strong>{String(summary.offline_replay_provider_calls ?? "UNKNOWN")}</strong></div>
      </div>
      <div className="flagship-coverage">
        <span><strong>Seed coverage:</strong> {coverage?.seed_wallet_complete ? "COMPLETE under indexed-provider contract" : "INCOMPLETE"}</span>
        <span><strong>Downstream graph:</strong> {coverage?.downstream_complete ? "COMPLETE" : "INCOMPLETE"}</span>
        <span><strong>Original provider cost:</strong> {String(summary.estimated_original_provider_credits ?? "UNKNOWN")} estimated credits</span>
      </div>
    </section>
  );
}

function DemoMetric({ label, value }: { label: string; value: unknown }) {
  return <div><small>{label}</small><strong>{String(value ?? "UNKNOWN")}</strong></div>;
}

function LifecycleStep({ label, value }: { label: string; value: string }) {
  return <div><small>{label}</small><strong>{value}</strong></div>;
}
