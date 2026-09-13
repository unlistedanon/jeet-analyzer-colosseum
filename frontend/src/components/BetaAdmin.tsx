import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { enterAdmin, getAdminMetrics, getAdminSession } from "../api";
import type { BetaAdminMetrics } from "../types";
import { BetaOperatorCases } from "./BetaOperatorCases";

export function BetaAdmin() {
  const [authenticated, setAuthenticated] = useState(false);
  const [checked, setChecked] = useState(false);
  const [code, setCode] = useState("");
  const [metrics, setMetrics] = useState<BetaAdminMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { void getAdminSession().then((value) => { setAuthenticated(value.authenticated); setChecked(true); }).catch(() => setChecked(true)); }, []);
  useEffect(() => {
    if (!authenticated) return;
    void getAdminMetrics().then(setMetrics).catch((reason) => setError(reason instanceof Error ? reason.message : "Admin metrics unavailable."));
  }, [authenticated]);

  const login = async (event: FormEvent) => {
    event.preventDefault(); setError(null);
    try { const result = await enterAdmin(code); setAuthenticated(result.authenticated); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Admin access denied."); }
  };

  if (!checked) return <main className="beta-loading">CHECKING ADMIN SESSION…</main>;
  if (!authenticated) return <main className="app-shell beta-admin"><section className="investigation-card beta-admin-login"><p className="eyebrow">Separate protected surface</p><h1>JEET BETA ADMIN</h1><form onSubmit={(event) => void login(event)}><label className="beta-field"><span>Admin credential</span><input type="password" value={code} onChange={(event) => setCode(event.target.value)} required minLength={12} /></label>{error && <p className="form-error" role="alert">{error}</p>}<button className="primary-action">OPEN ADMIN</button></form></section></main>;
  return <main className="app-shell beta-admin"><header className="masthead"><div className="brand-mark">JA</div><div><p className="eyebrow">Protected operations</p><h1>JEET BETA ADMIN</h1></div></header>{error && <div className="api-error" role="alert">{error}</div>}{metrics && <>
    <section className="admin-metrics" aria-label="Beta metrics">{[
      ["Investigations today", metrics.investigations], ["Running", metrics.running], ["Completed", metrics.complete], ["Failed", metrics.failed], ["Estimated credits", metrics.estimated_credits], ["Budget exhausted", metrics.budget_exhausted], ["Feedback", metrics.feedback_count], ["Average seconds", metrics.average_duration_seconds?.toFixed(1) ?? "—"],
    ].map(([label, value]) => <article className="panel" key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>
    {metrics.usage && <section className="panel usage-panel" aria-label="Beta usage journey"><p className="eyebrow">{metrics.usage.day} · UTC</p><h2>Where visitors get to</h2><div className="admin-metrics">{[
      ["Visited", metrics.usage.counts.visit], ["Opened example", metrics.usage.counts.example_opened],
      ["Tried a scan", metrics.usage.counts.scan_attempt], ["Scan admitted", metrics.usage.counts.scan_admitted],
      ["Scan completed", metrics.completed_visitors ?? 0], ["Viewed a result", metrics.usage.counts.result_viewed],
      ["Sent feedback", metrics.usage.counts.feedback_submitted],
    ].map(([label, value]) => <article key={label}><span>{label}</span><strong>{value}</strong></article>)}</div>
      <p className="panel-note">Daily browser identities reaching each step, not unique people or a sequential conversion rate. Reloads count once per step. Visits, attempts and views are browser-reported; admissions, completions and feedback come from server records. Completed scans were submitted today; other actions happened today. Saved examples are excluded from scan counts. Counts may include operator testing.</p></section>}
    {metrics.usage === null && <p role="status">Usage counters are temporarily unavailable. Scan metrics remain available above.</p>}
    <BetaOperatorCases />
    <section className="panel"><div className="section-heading"><div><p className="eyebrow">Usage isolation</p><h2>Investigations by visitor / invite ID</h2></div></div><table><thead><tr><th>Visitor / invite ID</th><th>Runs</th><th>Estimated credits</th></tr></thead><tbody>{metrics.by_code.map((row) => <tr key={row.code_id}><td>{row.code_id}</td><td>{row.investigations}</td><td>{row.estimated_credits}</td></tr>)}</tbody></table><p className="panel-note">{metrics.estimated_credit_note}</p></section>
    <section className="panel"><div className="section-heading"><div><p className="eyebrow">Recent beta feedback</p><h2>User signal</h2></div></div>{metrics.recent_feedback.length ? <ul className="feedback-list">{metrics.recent_feedback.map((row) => <li key={row.id}><strong>{row.useful === 1 ? "USEFUL" : row.useful === 0 ? "NOT USEFUL" : "COMMENT"}</strong><span>{row.comment || "No comment"}</span><small>{row.code_id} / {row.created_at}</small></li>)}</ul> : <p className="panel-note">No feedback recorded yet.</p>}</section>
  </>}</main>;
}
