import { ChevronDown, Search, ShieldCheck } from "lucide-react";
import { useState } from "react";
import type { InvestigationMode, InvestigationRequest } from "../types";

interface InvestigationFormProps {
  busy: boolean;
  onSubmit: (mode: InvestigationMode, request: InvestigationRequest) => void;
}

const modeCopy: Record<InvestigationMode, string> = {
  "seller-scan": "Discover and rank recent sellers",
  "wallet-audit": "Reconstruct one wallet lifecycle",
  "cluster-audit": "Map evidence-backed relationships",
};

const walletFieldCopy: Record<InvestigationMode, { label: string; qualifier: string; placeholder: string; missing: string }> = {
  "seller-scan": {
    label: "Seller wallet",
    qualifier: "optional",
    placeholder: "Optional public wallet address",
    missing: "",
  },
  "wallet-audit": {
    label: "Audited wallet",
    qualifier: "required",
    placeholder: "Required public wallet address",
    missing: "Wallet Audit requires an audited wallet.",
  },
  "cluster-audit": {
    label: "Seed wallet",
    qualifier: "required",
    placeholder: "Required public seed wallet address",
    missing: "Cluster Audit requires a seed wallet.",
  },
};

const DEFAULT_CONTROLS = {
  days: 5,
  max_pages: 1000,
  graph_depth: 3,
  trace_depth: 1,
  max_wallets: 50,
  max_rpc_requests: 500,
  max_signatures: 10_000,
  max_transactions: 5_000,
  request_timeout: 30,
  provider_retries: 2,
  provider_backoff_cap: 8,
  funding_lookback_days: 365,
  materiality_inventory_pct: 0.01,
};

export function InvestigationForm({ busy, onSubmit }: InvestigationFormProps) {
  const [mode, setMode] = useState<InvestigationMode>("seller-scan");
  const [mint, setMint] = useState("");
  const [wallet, setWallet] = useState("");
  const [error, setError] = useState<string | null>(null);
  const needsWallet = mode !== "seller-scan";
  const walletCopy = walletFieldCopy[mode];
  const [controls, setControls] = useState(DEFAULT_CONTROLS);

  const changeNumber = (key: keyof typeof controls, value: string) => {
    setControls((current) => ({ ...current, [key]: Number(value) }));
  };

  const changeMode = (value: InvestigationMode) => {
    setMode(value);
    setControls((current) => ({ ...current, days: value === "cluster-audit" ? 14 : 5 }));
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!mint.trim()) {
      setError("Enter a target token mint.");
      return;
    }
    if (needsWallet && !wallet.trim()) {
      setError(walletCopy.missing);
      return;
    }
    setError(null);
    onSubmit(mode, { mint: mint.trim(), wallet: wallet.trim() || undefined, ...controls });
  };

  return (
    <form className="investigation-card" onSubmit={submit} noValidate>
      <div className="form-heading">
        <div>
          <p className="eyebrow">New investigation</p>
          <h2>Mint in. Forensic map out.</h2>
        </div>
        <span className="readonly-chip"><ShieldCheck size={14} /> Read-only</span>
      </div>

      <div className="input-grid">
        <label>
          <span>Token mint</span>
          <div className="field-row"><Search size={17} /><input value={mint} onChange={(event) => setMint(event.target.value)} placeholder="Solana mint address" /></div>
        </label>
        <label>
          <span>{walletCopy.label} <em>{walletCopy.qualifier}</em></span>
          <div className="field-row"><input value={wallet} onChange={(event) => setWallet(event.target.value)} placeholder={walletCopy.placeholder} required={needsWallet} aria-required={needsWallet} /></div>
        </label>
      </div>

      <fieldset className="mode-picker">
        <legend>Investigation mode</legend>
        <div className="mode-grid">
          {(Object.keys(modeCopy) as InvestigationMode[]).map((value) => (
            <label key={value}>
              <input type="radio" name="mode" value={value} checked={mode === value} onChange={() => changeMode(value)} />
              <strong>{value.replace("-", " ")}</strong>
              <small>{modeCopy[value]}</small>
            </label>
          ))}
        </div>
      </fieldset>

      <details className="advanced-controls">
        <summary><span><ChevronDown size={16} /> Advanced bounded controls</span><small>Default bounded investigation limits</small></summary>
        <div className="control-grid">
          <NumberControl label="Days" value={controls.days} min={0.1} max={3650} step={0.1} onChange={(value) => changeNumber("days", value)} />
          <NumberControl label="Max pages" value={controls.max_pages} min={1} max={1000} onChange={(value) => changeNumber("max_pages", value)} />
          {mode === "cluster-audit" && <NumberControl label="Graph depth" value={controls.graph_depth} min={0} max={3} onChange={(value) => changeNumber("graph_depth", value)} />}
          {mode === "cluster-audit" && <NumberControl label="Max wallets" value={controls.max_wallets} min={1} max={10000} onChange={(value) => changeNumber("max_wallets", value)} />}
          <NumberControl label="Max RPC requests" value={controls.max_rpc_requests} min={1} onChange={(value) => changeNumber("max_rpc_requests", value)} />
          <NumberControl label="Max signatures" value={controls.max_signatures} min={1} onChange={(value) => changeNumber("max_signatures", value)} />
          <NumberControl label="Max transactions" value={controls.max_transactions} min={1} onChange={(value) => changeNumber("max_transactions", value)} />
          <NumberControl label="Request timeout (s)" value={controls.request_timeout} min={1} max={300} onChange={(value) => changeNumber("request_timeout", value)} />
          <NumberControl label="Provider retries" value={controls.provider_retries} min={0} max={10} onChange={(value) => changeNumber("provider_retries", value)} />
        </div>
      </details>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="primary-action" type="submit" disabled={busy}>{busy ? "INVESTIGATING…" : "INVESTIGATE"}</button>
      <p className="form-footnote">Public addresses only. Provider credentials stay in the backend environment.</p>
    </form>
  );
}

function NumberControl({ label, value, min, max, step = 1, onChange }: { label: string; value: number; min: number; max?: number; step?: number; onChange: (value: string) => void }) {
  return <label><span>{label}</span><input type="number" value={value} min={min} max={max} step={step} onChange={(event) => onChange(event.target.value)} /></label>;
}
