import { AlertTriangle, Database, Gauge, ServerCrash } from "lucide-react";
import type { AnalyzerResult } from "../types";

export function EvidencePanel({ result, publicCase = false }: { result: AnalyzerResult; publicCase?: boolean }) {
  const telemetry = result.request_telemetry ?? {};
  const coverage = result.provider_coverage;
  const incomplete = coverage?.complete === false;
  const requests = coverage?.requests ?? [];
  const limitations = requests.map((request) => request.limitation ?? request.terminal_failure_reason).filter(Boolean);
  return (
    <section className="panel evidence-panel" aria-labelledby="evidence-heading">
      <div className="section-heading"><div><p className="eyebrow">Provider and evidence</p><h2 id="evidence-heading"><Database size={18} /> Coverage receipt</h2></div><span className={incomplete ? "coverage-pill incomplete" : "coverage-pill complete"}>{incomplete ? "INCOMPLETE" : "COMPLETE"}</span></div>
      {incomplete && <div className="coverage-banner danger"><ServerCrash size={19} /><div><strong>{publicCase ? "DOWNSTREAM RELATIONSHIP GRAPH INCOMPLETE" : "INVESTIGATION INCOMPLETE"}</strong><span>{publicCase ? "Seed wallet lifecycle is complete under the indexed-provider contract. Re-entry and fresh current inventory remain directly observed." : "Evidence was preserved, but provider coverage ended before every conclusion could be proven."}</span></div></div>}
      <div className="telemetry-grid">
        <Telemetry label="Provider requests" value={telemetry.total_provider_requests} />
        <Telemetry label="RPC requests" value={telemetry.rpc_requests} />
        <Telemetry label="DAS requests" value={telemetry.das_requests} />
        <Telemetry label="Pages fetched" value={telemetry.pages_fetched} />
        <Telemetry label="Transactions fetched" value={telemetry.transactions_fetched} />
        <Telemetry label="Signatures examined" value={telemetry.signatures_examined} />
        <Telemetry label="Retry attempts" value={telemetry.retry_attempts} />
        <Telemetry label="Provider requests avoided" value={telemetry.provider_requests_avoided} />
        <Telemetry label="Duplicate evidence" value={telemetry.duplicate_evidence_observations} />
        <Telemetry label="Traversals suppressed" value={telemetry.wallet_traversals_suppressed} />
        <Telemetry label="Cache hits" value={telemetry.cache_hits} />
        <Telemetry label="Cache misses" value={telemetry.cache_misses} />
      </div>
      <div className="evidence-columns">
        <div><h3><Gauge size={15} /> Budget controls</h3><KeyValues value={telemetry.budget_limits} empty="No budget receipt emitted" /><p><strong>Terminated by:</strong> {telemetry.terminated_by_budget ?? "NONE"}</p><p><strong>Reason:</strong> {telemetry.termination_reason ?? "NONE"}</p></div>
        <div><h3><AlertTriangle size={15} /> Coverage limitations</h3>{limitations.length ? <ul>{limitations.map((value, index) => <li key={index}>{String(value)}</li>)}</ul> : <p>No provider limitation reported.</p>}</div>
      </div>
    </section>
  );
}

function Telemetry({ label, value }: { label: string; value: unknown }) {
  return <div><small>{label}</small><strong>{typeof value === "number" ? value.toLocaleString() : "—"}</strong></div>;
}

function KeyValues({ value, empty }: { value: Record<string, number> | undefined; empty: string }) {
  if (!value || !Object.keys(value).length) return <p>{empty}</p>;
  return <dl className="key-values">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{item.toLocaleString()}</dd></div>)}</dl>;
}
