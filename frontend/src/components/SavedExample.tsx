import { useRef, useState } from "react";
import type { AnalyzerResult } from "../types";
import { trackUsage } from "../api";
import { InvestigationResults } from "./InvestigationResults";

export function SavedExample() {
  const [result, setResult] = useState<AnalyzerResult | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef(false);
  const show = async () => {
    if (pending.current) return;
    setError(null);
    if (result) { setOpen(true); return; }
    pending.current = true; setBusy(true);
    try {
      const response = await fetch("/demo/flagship-result.json");
      if (!response.ok) throw new Error("The saved example could not load. Please try again.");
      const value = await response.json() as AnalyzerResult;
      if (!value.seed_wallet || !value.provider_coverage) throw new Error("The saved example is unavailable. Please try again.");
      setResult(value); setOpen(true);
      void trackUsage("example_opened");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The saved example could not load."); }
    finally { pending.current = false; setBusy(false); }
  };
  return <section className="saved-example" aria-label="Saved example">
    <div className="panel example-intro"><div><p className="eyebrow">New here?</p><h2>See what an investigation tells you.</h2><p>Explore a saved, anonymized case. No scan credits used.</p></div><button type="button" className="secondary-action" disabled={busy} onClick={() => open ? setOpen(false) : void show()} aria-expanded={open}>{busy ? "LOADING EXAMPLE…" : open ? "CLOSE EXAMPLE" : "TRY AN EXAMPLE"}</button></div>
    {error && <p className="form-error" role="alert">{error}</p>}
    {open && result && <div className="example-result"><div className="coverage-banner" role="status"><div><strong>SAVED EXAMPLE — NOT A LIVE SCAN</strong><span>Historical, anonymized evidence. Balances describe the saved report, not today. No investigation was started and your live report is unchanged.</span></div></div><InvestigationResults result={result} publicCase /></div>}
  </section>;
}
