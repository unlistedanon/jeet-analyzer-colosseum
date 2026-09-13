import { AlertTriangle, CheckCircle2, Clipboard, Download, FlaskConical, LogOut, ShieldCheck, Skull } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  enterBeta,
  trackUsage,
  getBetaInvestigation,
  getBetaSession,
  getSafeReport,
  leaveBeta,
  safeReportDownloadUrl,
  startBetaInvestigation,
  submitBetaFeedback,
} from "../api";
import type { BetaInvestigationView, BetaSession } from "../types";
import { InvestigationResults } from "./InvestigationResults";
import { SavedExample } from "./SavedExample";
import { SolMembership } from "./SolMembership";

const ACTIVE_STATES = new Set(["queued", "running"]);
const PERSISTED_INVESTIGATION_KEY = "jeet_beta_last_investigation";

function rememberInvestigation(identifier: string) {
  try { window.localStorage.setItem(PERSISTED_INVESTIGATION_KEY, identifier); } catch { /* storage is optional */ }
}

function rememberedInvestigation() {
  try { return window.localStorage.getItem(PERSISTED_INVESTIGATION_KEY); } catch { return null; }
}

function forgetInvestigation() {
  try { window.localStorage.removeItem(PERSISTED_INVESTIGATION_KEY); } catch { /* storage is optional */ }
}

function creditsExhausted(reason?: string | null) {
  return /(?:^|\s)(?:budget=)?max_estimated_provider_credits(?:\s|$)/.test(reason ?? "");
}

function PaidMembershipInterest({ identifier }: { identifier: string }) {
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const busy = useRef(false);
  const requestMembership = async () => {
    if (busy.current) return;
    busy.current = true;
    setStatus("saving");
    try {
      await submitBetaFeedback(identifier, null, "[PAID_MEMBERSHIP_INTEREST] Interested in a larger investigation credit allowance.");
      setStatus("saved");
    } catch { busy.current = false; setStatus("error"); }
  };
  return <section className="panel beta-feedback" aria-label="Paid membership">
    <div><p className="eyebrow">Go deeper with paid membership</p><h2>Need more investigation credits?</h2></div>
    <p>Register your interest in paid membership for a larger credit allowance. More credits may uncover more evidence; they cannot guarantee complete coverage.</p>
    <p>Check membership options above for SOL checkout availability. Registering interest does not charge you or start another run.</p>
    {status === "saved" ? <p role="status">Membership interest saved with this investigation. No payment was taken.</p> : <button type="button" className="secondary-action" disabled={status === "saving"} onClick={() => void requestMembership()}>{status === "saving" ? "SAVING…" : "I’M INTERESTED IN PAID MEMBERSHIP"}</button>}
    {status === "error" && <p className="form-error" role="alert">Your interest could not be saved. Please try again.</p>}
  </section>;
}

function errorText(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

function BetaProgress({ investigation }: { investigation: BetaInvestigationView }) {
  const latest = [...investigation.progress.events].reverse();
  return (
    <section className="panel beta-progress" aria-label="Investigation progress">
      <div className="section-heading">
        <div><p className="eyebrow">Truthful stage progress</p><h2>{creditsExhausted(investigation.termination_reason) ? "CREDIT LIMIT REACHED" : investigation.stage.replaceAll("_", " ")}</h2></div>
        <span className={`run-state state-${investigation.status === "failed" ? "error" : investigation.status}`}>{investigation.status}</span>
      </div>
      <ol>
        {latest.slice(0, 8).reverse().map((event, index) => (
          <li key={`${event.timestamp}-${event.stage}-${index}`} className={`beta-event-${event.state}`}>
            <span>{event.state === "complete" ? "✓" : event.state === "error" ? "!" : "•"}</span>
            <div><strong>{event.stage.replaceAll("_", " ")}</strong><small>{event.message}</small></div>
          </li>
        ))}
      </ol>
      <p className="beta-cost-note">Reserved ceiling: {investigation.estimated_provider_credits_reserved} estimated provider credits. No percentage is shown because total work is not knowable in advance.</p>
    </section>
  );
}

function InviteGate({ session, onAuthenticated }: { session: BetaSession; onAuthenticated: (session: BetaSession) => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(null);
    try { onAuthenticated(await enterBeta(code)); }
    catch (reason) { setError(errorText(reason, "Beta access could not be verified.")); }
    finally { setBusy(false); }
  };
  return (
    <section className="beta-gate" aria-labelledby="beta-gate-heading">
      <div className="beta-gate-copy">
        <p className="eyebrow"><ShieldCheck size={13} /> Invite-only hosted beta</p>
        <h2 id="beta-gate-heading">Verify the exit.<br /><span>Follow the evidence.</span></h2>
        <p>Trace exit, transfer, re-entry, remaining inventory, related-wallet evidence, and coverage—without signing or trading.</p>
      </div>
      <form className="investigation-card beta-access-card" onSubmit={(event) => void submit(event)}>
        <div className="form-heading"><div><p className="eyebrow">Private beta</p><h2>Enter access code</h2></div><span className="readonly-chip">Read-only</span></div>
        {!session.beta_configured && <div className="coverage-banner danger"><AlertTriangle size={18} /><div><strong>BETA NOT CONFIGURED</strong><span>The server operator must configure hashed invite credentials before access can be granted.</span></div></div>}
        <label className="beta-field"><span>Access code</span><input type="password" autoComplete="one-time-code" value={code} onChange={(event) => setCode(event.target.value)} placeholder="jeet-beta-…" required minLength={12} /></label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="primary-action" disabled={busy || !session.beta_configured}>{busy ? "VERIFYING…" : "ENTER BETA"}</button>
        <p className="form-footnote">The code is verified by the server and is never stored by this browser UI.</p>
      </form>
    </section>
  );
}

function Feedback({ identifier }: { identifier: string }) {
  const [useful, setUseful] = useState<boolean | null>(null);
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(null);
    try { await submitBetaFeedback(identifier, useful, comment); setSent(true); }
    catch (reason) { setError(errorText(reason, "Feedback could not be stored.")); }
  };
  if (sent) return <section className="panel beta-feedback"><CheckCircle2 size={18} /><strong>Feedback recorded. Thank you.</strong></section>;
  return (
    <form className="panel beta-feedback" onSubmit={(event) => void submit(event)}>
      <div><p className="eyebrow">Beta feedback</p><h2>Was this useful?</h2></div>
      <div className="feedback-choice">
        <button type="button" className={useful === true ? "selected" : ""} onClick={() => setUseful(true)}>YES</button>
        <button type="button" className={useful === false ? "selected" : ""} onClick={() => setUseful(false)}>NO</button>
      </div>
      <label><span>What did Jeet miss?</span><textarea value={comment} onChange={(event) => setComment(event.target.value)} maxLength={2000} placeholder="Optional forensic or UX feedback" /></label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="secondary-action" disabled={useful === null && !comment.trim()}>SEND FEEDBACK</button>
    </form>
  );
}

export function BetaExperience() {
  const [session, setSession] = useState<BetaSession | null>(null);
  const [mint, setMint] = useState("");
  const [wallet, setWallet] = useState("");
  const [investigation, setInvestigation] = useState<BetaInvestigationView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [shareMessage, setShareMessage] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const pollAttemptRef = useRef(0);
  const mountedRef = useRef(true);

  const poll = useCallback(async (identifier: string) => {
    try {
      const next = await getBetaInvestigation(identifier);
      if (!mountedRef.current) return;
      pollAttemptRef.current = 0;
      setInvestigation(next);
      setPollError(null);
      rememberInvestigation(identifier);
      if (ACTIVE_STATES.has(next.status)) {
        pollRef.current = window.setTimeout(() => void poll(identifier), 1000);
      }
    } catch (reason) {
      if (!mountedRef.current) return;
      const message = errorText(reason, "Scan status is temporarily unavailable.");
      if (message.toLowerCase().includes("not found")) {
        forgetInvestigation();
        setPollError("The saved investigation is no longer available on this server.");
        return;
      }
      const attempt = Math.min(pollAttemptRef.current + 1, 6);
      pollAttemptRef.current = attempt;
      const delay = Math.min(10_000, 500 * (2 ** (attempt - 1)));
      setPollError(`Temporary connection issue while reading the scan. Retrying in ${Math.ceil(delay / 1000)}s…`);
      // One dropped status request must not strand a long-running scan.
      pollRef.current = window.setTimeout(() => void poll(identifier), delay);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    let active = true;
    void getBetaSession().then((value) => {
      if (!active) return;
      setSession(value);
      const identifier = value.authenticated ? rememberedInvestigation() : null;
      if (identifier) void poll(identifier);
    }).catch((reason) => {
      if (active) { setError(errorText(reason, "The beta service could not be reached.")); setSession({ authenticated: false, beta_enabled: false, beta_configured: false }); }
    });
    return () => {
      active = false;
      mountedRef.current = false;
      if (pollRef.current) window.clearTimeout(pollRef.current);
    };
  }, [poll]);

  useEffect(() => {
    if (session?.authenticated) void trackUsage("visit");
  }, [session?.authenticated]);

  useEffect(() => {
    if (investigation?.result) void trackUsage("result_viewed", investigation.investigation_id);
  }, [investigation?.investigation_id, investigation?.result]);

  const investigate = async (event: FormEvent) => {
    event.preventDefault(); setError(null); setPollError(null); setShareMessage(null);
    void trackUsage("scan_attempt");
    try {
      const submitted = await startBetaInvestigation(mint.trim(), wallet.trim());
      setInvestigation(submitted);
      rememberInvestigation(submitted.investigation_id);
      pollAttemptRef.current = 0;
      if (ACTIVE_STATES.has(submitted.status)) {
        pollRef.current = window.setTimeout(() => void poll(submitted.investigation_id), 300);
      }
    } catch (reason) { setError(errorText(reason, "Investigation could not be admitted.")); }
  };

  const logout = async () => {
    if (pollRef.current) window.clearTimeout(pollRef.current);
    pollAttemptRef.current = 0;
    forgetInvestigation();
    await leaveBeta(); setSession({ authenticated: false, beta_enabled: session?.beta_enabled ?? false, beta_configured: session?.beta_configured ?? false }); setInvestigation(null);
  };

  const copySafe = async () => {
    if (!investigation) return;
    setShareMessage(null);
    try {
      const report = await getSafeReport(investigation.investigation_id);
      await navigator.clipboard.writeText(JSON.stringify(report, null, 2));
      setShareMessage("Public-safe report copied.");
    } catch (reason) { setShareMessage(errorText(reason, "Public-safe report is unavailable.")); }
  };

  if (!session) return <main className="beta-loading">CONNECTING TO JEET ANALYZER…</main>;
  if (!session.authenticated && session.public_access) return <main className="app-shell beta-shell"><BetaMasthead /><section className="api-error" role="alert">{error ?? "Jeet Analyzer is temporarily unavailable. Please refresh to try again."}</section><BetaFooter /></main>;
  if (!session.authenticated) return <main className="app-shell beta-shell"><BetaMasthead /><InviteGate session={session} onAuthenticated={(value) => { setSession(value); const identifier = rememberedInvestigation(); if (identifier) void poll(identifier); }} /><BetaFooter /></main>;

  const active = investigation && ACTIVE_STATES.has(investigation.status);
  const budgetStopped = investigation?.termination_reason?.includes("BUDGET") || investigation?.termination_reason?.startsWith("max_");
  const creditStopped = creditsExhausted(investigation?.termination_reason);
  return (
    <main className="app-shell beta-shell">
      <BetaMasthead onLogout={session.public_access ? undefined : () => void logout()} />
      <section className="beta-hero">
        <div className="beta-hero-copy"><p className="eyebrow"><FlaskConical size={13} /> Read-only chain forensics</p><h2>Verify the exit.<br /><span>Follow the evidence.</span></h2><p>Enter a token mint and seller wallet. Jeet maps the observable trail, related-wallet signals, and coverage limits without pretending uncertainty is proof.</p></div>
        <form className="investigation-card beta-investigation-form" onSubmit={(event) => void investigate(event)}>
          <div className="form-heading"><div><p className="eyebrow">Evidence ledger</p><h2>Inspect a seller exit</h2></div><span className="readonly-chip">Read-only</span></div>
          {!session.beta_enabled && <div className="coverage-banner danger"><AlertTriangle size={18} /><div><strong>INVESTIGATIONS PAUSED</strong><span>New provider work is disabled by the server kill switch. Previously stored reports remain available.</span></div></div>}
          <label className="beta-field"><span>Token mint</span><input value={mint} onChange={(event) => setMint(event.target.value)} placeholder="Solana token mint" autoComplete="off" required /></label>
          <label className="beta-field"><span>Wallet address</span><input value={wallet} onChange={(event) => setWallet(event.target.value)} placeholder="Seller wallet" autoComplete="off" required /></label>
          {error && <p className="form-error" role="alert">{error}</p>}
          {pollError && <p className="form-footnote" role="status">{pollError}</p>}
          <button className="primary-action" disabled={!session.beta_enabled || Boolean(active)}>{active ? "INVESTIGATING…" : "INVESTIGATE"}</button>
          <p className="form-footnote">Trace the token trail</p>
          <p className="form-footnote">Server-owned limits: {session.limits?.runs_per_code_per_day === 0 ? "No daily scan-count limit within your allowance" : `${session.limits?.runs_per_code_per_day ?? "bounded"} runs/day`}, {session.limits?.estimated_credits_per_run ?? "bounded"} estimated credits/run.</p>
        </form>
      </section>

      {!investigation && <SavedExample />}
      {investigation && <section className="investigation-workspace beta-workspace">
        <div className="workspace-heading"><div><p className="eyebrow">Investigation {investigation.investigation_id.slice(0, 8)}</p><h2>Seller exit and related-wallet evidence</h2></div></div>
        <BetaProgress investigation={investigation} />
        {investigation.status === "complete" && investigation.result && <div className="coverage-banner" role="status"><CheckCircle2 size={18} /><div><strong>FINAL RESULT AVAILABLE</strong><span>The durable forensic result is preserved below, including any explicit coverage limits.</span></div></div>}
        {budgetStopped && <div className="coverage-banner" role="status"><AlertTriangle size={18} /><div><strong>{creditStopped ? "INVESTIGATION CREDITS USED UP" : "INVESTIGATION LIMIT REACHED"}</strong><span>{creditStopped ? "This investigation reached its credit allowance before all evidence could be collected." : "A scan or provider limit stopped further collection. This does not mean your credit allowance was used up."} {investigation.result ? "Your results so far are saved and remain available below." : "A complete result is not available."} Missing coverage cannot strengthen the conclusion.</span></div></div>}
        {creditStopped && investigation.result && investigation.status === "complete" && <PaidMembershipInterest key={investigation.investigation_id} identifier={investigation.investigation_id} />}
        {investigation.status === "failed" && <div className="api-error" role="alert"><strong>SYSTEM ERROR</strong><span>{investigation.error ?? "The investigation failed safely. No conclusion was strengthened."}</span></div>}
        {investigation.result && <>
          <InvestigationResults result={investigation.result} />
          <section className="panel beta-share"><div><p className="eyebrow">Explicit public-safe export</p><h2>Share the result, not the internal receipt</h2><p>Wallets and transactions are deterministically aliased by the existing allowlisted exporter.</p></div><div className="share-actions"><button className="secondary-action" disabled={!investigation.safe_report_available} onClick={() => void copySafe()}><Clipboard size={15} /> COPY SAFE REPORT</button><a className={`secondary-action${investigation.safe_report_available ? "" : " disabled"}`} href={investigation.safe_report_available ? safeReportDownloadUrl(investigation.investigation_id) : undefined}><Download size={15} /> DOWNLOAD SAFE REPORT</a></div>{shareMessage && <p className="share-message" role="status">{shareMessage}</p>}</section>
          <Feedback identifier={investigation.investigation_id} />
        </>}
      </section>}
      <SolMembership onActivated={async () => setSession(await getBetaSession())} />
      <BetaFooter />
    </main>
  );
}

function BetaMasthead({ onLogout }: { onLogout?: () => void }) {
  return <header className="masthead"><div className="brand-mark" aria-label="Jeet Analyzer skull mark"><Skull size={24} strokeWidth={2.5} /></div><div><p className="eyebrow">Forensic beta</p><h1>JEET ANALYZER</h1></div><p className="product-line">Evidence ledger for token exits.</p><span className="safety-mark"><ShieldCheck size={15} /> Investigations are read-only.</span><a className="icon-action" href="?game=1">PLAY PAPER GAME</a>{onLogout && <button className="icon-action" onClick={onLogout} aria-label="Leave beta"><LogOut size={15} /> EXIT</button>}</header>;
}

function BetaFooter() {
  return <><section className="beta-disclaimer"><strong>EXPERIMENTAL FORENSIC RESEARCH TOOL</strong><span>We count daily browser visits and key actions to improve the beta. Results depend on available indexed and on-chain evidence. A relationship between wallets does not by itself prove common ownership or control.</span></section><footer><span>JEET ANALYZER / READ-ONLY</span><span>COMMON CONTROL requires proof.</span></footer></>;
}
