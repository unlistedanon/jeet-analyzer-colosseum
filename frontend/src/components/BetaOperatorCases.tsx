import { RefreshCw, X } from "lucide-react";
import { useEffect, useState } from "react";
import { getOperatorCase, getOperatorCases } from "../operatorApi";
import type { OperatorCaseDetail, OperatorCaseSummary } from "../operatorApi";
import { InvestigationResults } from "./InvestigationResults";
import "../operator.css";

function short(value?: string | null, size = 8) {
  if (!value) return "—";
  if (value.length <= size * 2 + 3) return value;
  return `${value.slice(0, size)}…${value.slice(-size)}`;
}

function statusLabel(row: OperatorCaseSummary) {
  return row.target_cluster_status ?? row.cluster_status ?? row.current_wallet_status ?? "—";
}

export function BetaOperatorCases() {
  const [cases, setCases] = useState<OperatorCaseSummary[]>([]);
  const [selected, setSelected] = useState<OperatorCaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setError(null);
    setLoading(true);
    try {
      setCases(await getOperatorCases(100));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Operator cases unavailable.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, []);

  const openCase = async (identifier: string) => {
    setError(null);
    setDetailLoading(true);
    try {
      setSelected(await getOperatorCase(identifier));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Case detail unavailable.");
    } finally {
      setDetailLoading(false);
    }
  };

  return <>
    <section className="panel operator-cases">
      <div className="section-heading operator-heading">
        <div><p className="eyebrow">Operator ledger</p><h2>Tester investigations</h2></div>
        <button className="secondary-action" type="button" onClick={() => void refresh()} disabled={loading}><RefreshCw size={14} className={loading ? "spin" : ""} /> Refresh</button>
      </div>
      {error && <div className="api-error" role="alert">{error}</div>}
      {loading && !cases.length ? <p className="panel-note">Loading recent beta investigations…</p> : cases.length ?
        <div className="operator-table-wrap"><table className="operator-table"><thead><tr><th>Case</th><th>Invite</th><th>Input</th><th>Run</th><th>Verdict</th><th>Coverage</th><th>Credits</th><th>Created</th></tr></thead><tbody>{cases.map((row) => <tr key={row.investigation_id}>
          <td><button className="operator-case-link" type="button" onClick={() => void openCase(row.investigation_id)}>{short(row.investigation_id, 6)}</button></td>
          <td>{row.code_id}</td>
          <td><span className="operator-input"><code>{short(row.request.mint, 6)}</code><code>{short(row.request.wallet, 6)}</code></span></td>
          <td><span className={`run-state state-${row.status === "failed" ? "error" : row.status}`}>{row.status}</span></td>
          <td>{statusLabel(row)}</td>
          <td>{row.provider_complete === true ? "COMPLETE" : row.provider_complete === false ? "INCOMPLETE" : "—"}</td>
          <td>{row.estimated_provider_credits_used.toLocaleString()}</td>
          <td>{new Date(row.created_at).toLocaleString()}</td>
        </tr>)}</tbody></table></div>
        : <p className="panel-note">No tester investigations recorded yet.</p>}
    </section>

    {detailLoading && <section className="panel operator-detail"><p className="panel-note">Loading case detail…</p></section>}
    {selected && !detailLoading && <section className="operator-detail-stack" aria-label="Selected tester investigation">
      <section className="panel operator-detail">
        <div className="section-heading operator-heading"><div><p className="eyebrow">Selected case</p><h2>{short(selected.investigation_id, 12)}</h2></div><button className="secondary-action" type="button" onClick={() => setSelected(null)}><X size={14} /> Close</button></div>
        <div className="operator-facts">
          <div><span>Invite ID</span><strong>{selected.code_id}</strong></div>
          <div><span>Status</span><strong>{selected.status}</strong></div>
          <div><span>Stage</span><strong>{selected.stage}</strong></div>
          <div><span>Credits used</span><strong>{selected.estimated_provider_credits_used.toLocaleString()}</strong></div>
          <div><span>Created</span><strong>{new Date(selected.created_at).toLocaleString()}</strong></div>
          <div><span>Completed</span><strong>{selected.completed_at ? new Date(selected.completed_at).toLocaleString() : "—"}</strong></div>
        </div>
        <div className="operator-submission"><div><span>Mint submitted</span><code>{selected.request.mint}</code></div><div><span>Wallet submitted</span><code>{selected.request.wallet ?? "—"}</code></div></div>
        {(selected.error || selected.termination_reason) && <div className="coverage-banner danger"><div><strong>RUN LIMITATION</strong><span>{selected.error ?? selected.termination_reason}</span></div></div>}
        <details className="operator-raw"><summary>Stored request and progress</summary><pre>{JSON.stringify({ request: selected.request, progress: selected.progress, termination_reason: selected.termination_reason, artifact_names: selected.artifact_names }, null, 2)}</pre></details>
      </section>

      <section className="panel operator-feedback"><div className="section-heading"><div><p className="eyebrow">Tester response</p><h2>Feedback on this case</h2></div></div>{selected.feedback.length ? <ul className="feedback-list">{selected.feedback.map((row) => <li key={row.id}><strong>{row.useful === 1 ? "USEFUL" : row.useful === 0 ? "NOT USEFUL" : "COMMENT"}</strong><span>{row.comment || "No comment"}</span><small>{new Date(row.created_at).toLocaleString()}</small></li>)}</ul> : <p className="panel-note">No feedback attached to this investigation.</p>}</section>

      {selected.result ? <div className="operator-result"><div className="workspace-heading"><div><p className="eyebrow">Exact stored output</p><h2>Forensic result returned by Jeet</h2></div></div><InvestigationResults result={selected.result} /></div> : <section className="panel"><p className="panel-note">This case does not have a completed forensic result.</p></section>}
    </section>}
  </>;
}
