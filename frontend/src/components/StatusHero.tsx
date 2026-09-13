import { AlertOctagon, Link2Off, Radar } from "lucide-react";
import type { AnalyzerResult } from "../types";
import { currentSeedBalance, formatRawAmount, formatStatus, statusTone, tokenDecimals } from "../utils";

export function StatusHero({ result }: { result: AnalyzerResult }) {
  const walletStatus = result.current_wallet_status ?? result.wallet_status;
  const targetClusterStatus = result.target_cluster_status ?? result.cluster_status;
  const relationshipStatus = result.relationship_status;
  const walletAudit = result.schema === "jeet-analyzer.wallet-ui.v1";
  const decimals = tokenDecimals(result);
  const visibleCluster = result.current_inventory?.visible_cluster_target_inventory_raw ?? result.visible_cluster_target_inventory_raw;
  if (!walletStatus && !targetClusterStatus) return null;
  return (
    <section className="status-hero" aria-label="Forensic conclusions">
      <StatusBlock label="Wallet status" status={walletStatus} metricLabel="Current inventory" metric={formatRawAmount(currentSeedBalance(result), decimals, true)} />
      {targetClusterStatus && <StatusBlock label="Target cluster status" status={targetClusterStatus} metricLabel="Visible cluster inventory" metric={formatRawAmount(visibleCluster, decimals, true)} />}
      {walletAudit && !targetClusterStatus && <article className="status-block tone-unresolved"><p><Radar size={14} /> Target cluster status</p><strong>NOT EVALUATED</strong><span>Wallet Audit evaluates one wallet; relationship expansion requires Cluster Audit.</span></article>}
      {relationshipStatus && <StatusBlock label="Relationship status" status={relationshipStatus} metricLabel="Meaning" metric="Linkage uncertainty tracked separately" />}
      <article className="status-block common-control">
        <p><Link2Off size={14} /> Common control</p>
        <strong>NOT PROVEN</strong>
        <span>Relationships are evidence, not ownership.</span>
      </article>
      <div className="status-rule"><AlertOctagon size={15} /><strong>Wallet out ≠ cluster out.</strong> Linked wallet ≠ same owner.</div>
    </section>
  );
}

function StatusBlock({ label, status, metricLabel, metric }: { label: string; status?: string | null; metricLabel: string; metric: string }) {
  return (
    <article className={`status-block tone-${statusTone(status)}`}>
      <p><Radar size={14} /> {label}</p>
      <strong>{formatStatus(status)}</strong>
      <span>{metricLabel}: {metric}</span>
    </article>
  );
}
