import { useEffect, useRef, useState } from "react";

type Member = { expires: number; credits: number; runs_daily: number; daily_credits: number };
type Plan = { enabled: boolean; network: string; days: number; price_lamports: number; credits: number; runs_daily: number; daily_credits: number; pending_invoice?: Invoice | null; membership: Member | null; daily_usage?: { limit: number; committed: number; remaining: number; resets_at: string } | null };
type Invoice = { id: string; amount_sol: string; recipient: string; reference: string; network: string; payment_url: string | null; signature: string | null; expires: number; days: number; credits: number; daily_credits: number; runs_daily: number };

async function api<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(`/api/beta/membership${path}`, { credentials: "same-origin", ...(body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Payment request failed. Please try again.");
  return result;
}

function allowance(value: { credits: number; daily_credits: number; runs_daily: number }) {
  return `${(value.daily_credits || value.credits).toLocaleString()} estimated credits ${value.daily_credits ? "per day" : "per scan"} · ${value.runs_daily === 0 ? "No daily scan-count limit within your allowance" : `${value.runs_daily} scans/day`}`;
}

function PaymentQr({ url }: { url: string }) {
  const [image, setImage] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    void import("qrcode").then((qr) => qr.toDataURL(url, { width: 280, margin: 4, errorCorrectionLevel: "M" })).then((data) => { if (active) setImage(data); }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [url]);
  if (failed) return <p>QR unavailable. Use the wallet link or copy the payment link.</p>;
  return image ? <img src={image} width={280} height={280} style={{ maxWidth: "100%", height: "auto" }} alt="Scan this Solana Pay QR with your wallet to pay this invoice" /> : <p role="status">Preparing payment QR…</p>;
}

export function SolMembership({ onActivated }: { onActivated: () => Promise<void> }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [signature, setSignature] = useState("");
  const [recovery, setRecovery] = useState("");
  const [saved, setSaved] = useState(false);
  const [restoreCode, setRestoreCode] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const pending = useRef(false);
  useEffect(() => {
    if (!invoice || invoice.signature) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [invoice?.id, invoice?.signature]);
  const action = async (work: () => Promise<void>) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to check payment. Do not pay again."); }
    finally { pending.current = false; setBusy(false); }
  };
  const refresh = async () => {
    const value = await api<Plan>(""); setPlan(value); setInvoice(value.pending_invoice ?? null);
  };
  const checkPayment = async () => {
    if (!invoice) return;
    const verified = await api<Invoice>(`/invoices/${invoice.id}/verify`, signature.trim() ? { signature: signature.trim() } : {});
    setInvoice(verified);
    setPlan(await api<Plan>(""));
    await onActivated();
  };
  const expired = Boolean(invoice && now >= invoice.expires * 1000);
  return <section className="panel beta-feedback sol-checkout" aria-label="SOL membership checkout">
    <div><p className="eyebrow">More room to investigate</p><h2>Paid membership · Pay with SOL</h2></div>
    {!plan && <button className="secondary-action" disabled={busy} onClick={() => void action(refresh)}>VIEW MEMBERSHIP OPTIONS</button>}
    {plan?.membership ? <div>
      <p role="status">Membership active until {new Date(plan.membership.expires * 1000).toLocaleString()}. {allowance(plan.membership)}.</p>
      {plan.daily_usage && <p><strong>{plan.daily_usage.remaining.toLocaleString()} credits available today</strong> · {plan.daily_usage.committed.toLocaleString()} spent or reserved. Resets {new Date(plan.daily_usage.resets_at).toLocaleString()} (midnight UTC).</p>}
      <button className="secondary-action" disabled={busy} onClick={() => void action(refresh)}>REFRESH ALLOWANCE</button>
    </div> : plan && <div>
      {!plan.enabled ? <p>SOL checkout is not open yet. No payment is requested.</p> : <>
        <p><strong>{plan.price_lamports / 1e9} SOL for {plan.days} days.</strong> {allowance(plan)}.</p>
        <p>One-time payment; no automatic renewal. Daily credits reset at midnight UTC and do not roll over. Completed scans use their recorded estimated credits; active scans reserve their ceiling. Failed scans without reliable usage records may use the full reservation.</p>
        <p>Scans share the service’s provider budget and availability. More credits cannot guarantee complete coverage. Your wallet also charges a network fee.</p>
        {plan.network !== "mainnet-beta" && <p role="status">DEVNET TEST ONLY — do not send real SOL. Wallet payment links are disabled in test mode.</p>}
      </>}
    </div>}
    {plan && (plan.enabled || plan.membership) && <div className="checkout-recovery">
      <h3>Keep access to your membership</h3>
      <p>Save a recovery code before paying. It restores your membership and reports on another browser. Anyone with it can access them. This is an app recovery code, not a wallet seed phrase.</p>
      <button className="secondary-action" disabled={busy} onClick={() => void action(async () => {
        const value = await api<{ recovery_code: string }>("/recovery-code", {}); setRecovery(value.recovery_code); setSaved(false);
      })}>{recovery ? "REPLACE RECOVERY CODE" : "GENERATE RECOVERY CODE"}</button>
      <p>Generating a replacement invalidates your previous recovery code.</p>
      {recovery && <><label className="beta-field"><span>Your private app recovery code</span><input readOnly value={recovery} /></label><button className="secondary-action" onClick={() => void action(async () => { await navigator.clipboard.writeText(recovery); setNotice("Recovery code copied. Save it somewhere private."); })}>COPY RECOVERY CODE</button><label className="checkout-saved"><input type="checkbox" checked={saved} onChange={(event) => setSaved(event.target.checked)} /> I have saved my recovery code somewhere private.</label></>}
    </div>}
    {plan?.enabled && !plan.membership && (!invoice || expired) && <button className="primary-action" disabled={busy || !saved} onClick={() => void action(async () => { setInvoice(await api<Invoice>("/invoices", {})); setSignature(""); setNow(Date.now()); })}>{expired ? "CREATE NEW PAYMENT REQUEST" : "CREATE SOL PAYMENT REQUEST"}</button>}
    {invoice && !plan?.membership && <div className="checkout-invoice">
      <h3>Your payment request</h3><p>{invoice.amount_sol} SOL · {invoice.days} days · {allowance(invoice)} · {invoice.network}</p>
      <p>Receiving address: <code style={{ overflowWrap: "anywhere" }}>{invoice.recipient}</code></p>
      <p>Pay before {new Date(invoice.expires * 1000).toLocaleString()}. Use this QR or payment link so your invoice reference is included. A plain transfer to the address will not activate membership.</p>
      {plan?.enabled && saved && invoice.payment_url && !expired && !invoice.signature && <>
        <PaymentQr key={invoice.id} url={invoice.payment_url} />
        <p>Scan using a Solana Pay compatible wallet on your phone, or open the payment on this device. Review the amount and recipient in your wallet.</p>
        <a className="primary-action" href={invoice.payment_url}>OPEN PAYMENT IN WALLET</a>
        <button className="secondary-action" onClick={() => void action(async () => { await navigator.clipboard.writeText(invoice.payment_url!); setNotice("Payment link copied."); })}>COPY PAYMENT LINK</button>
      </>}
      {!saved && !invoice.signature && <p>Generate and save a recovery code above to show this payment request.</p>}
      {invoice.signature ? <p role="status">Payment verified. Membership activated. Do not pay this invoice again.</p> : <>
        {expired && <p>This invoice has expired. You can still check a payment made before its deadline.</p>}
        <button className="secondary-action" disabled={busy} onClick={() => void action(checkPayment)}>CHECK FOR PAYMENT</button>
        <details><summary>Have a transaction signature?</summary><label className="beta-field"><span>Transaction signature from your wallet (optional)</span><input value={signature} onChange={(event) => setSignature(event.target.value)} autoComplete="off" /></label></details>
        <p>Checking finds the payment by invoice reference and waits for finalization. If it is pending, wait ten seconds and check again; do not pay again.</p>
      </>}
    </div>}
    <details><summary>Restore an existing membership</summary><label className="beta-field"><span>App recovery code</span><input type="password" value={restoreCode} onChange={(event) => setRestoreCode(event.target.value)} autoComplete="off" /></label><button className="secondary-action" disabled={busy || restoreCode.trim().length !== 43} onClick={() => void action(async () => { await api("/recover", { recovery_code: restoreCode.trim() }); window.location.reload(); })}>RESTORE ACCESS</button></details>
    {notice && <p role="status">{notice}</p>}
    {busy && <p role="status">Checking…</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </section>;
}
