import { Activity, ArrowDownLeft, ArrowUpRight, Coins, History, WalletCards } from "lucide-react";
import type { AnalyzerResult } from "../types";
import { Address } from "./Address";
import { currentSeedBalance, formatRawAmount, formatStatus, numberValue, record, records, seedAccounting, timeText, tokenDecimals } from "../utils";

export function TokenSummary({ result, publicCase = false }: { result: AnalyzerResult; publicCase?: boolean }) {
  const token = result.token ?? {};
  const coverage = result.provider_coverage;
  const window = result.window ?? {};
  const withheld = publicCase ? "WITHHELD IN PUBLIC CASE" : "UNKNOWN";
  const displayName = String(token.symbol || token.name || (publicCase ? "REDACTED FOR PUBLIC DEMO" : "UNKNOWN"));
  const program = token.token_program ?? withheld;
  const programType = !token.token_program ? withheld : typeof program === "string" && program.includes("Tokenz") ? "Token-2022" : "SPL Token";
  return (
    <article className="panel summary-card">
      <div className="card-title"><Coins size={17} /><div><p className="eyebrow">Target asset</p><h3>{displayName}</h3></div></div>
      <dl className="detail-grid">
        <div><dt>Name</dt><dd>{String(token.name || withheld)}</dd></div>
        <div><dt>Mint</dt><dd>{publicCase ? withheld : <Address value={result.mint ?? token.mint} />}</dd></div>
        <div><dt>Program type</dt><dd>{programType}</dd></div>
        <div><dt>Token program</dt><dd>{publicCase && !token.token_program ? withheld : <Address value={typeof program === "string" ? program : undefined} />}</dd></div>
        <div><dt>Decimals</dt><dd>{token.decimals ?? "UNKNOWN"}</dd></div>
        <div><dt>Analysis window</dt><dd>{timeText(window.start)} → {timeText(window.end)}</dd></div>
        <div><dt>Provider/history coverage</dt><dd className={coverage?.complete ? "text-good" : "text-warning"}>{coverage?.complete ? "COMPLETE" : "INCOMPLETE"}</dd></div>
        <div><dt>Fresh balance coverage</dt><dd>{freshBalanceText(result)}</dd></div>
      </dl>
      <div className="truth-notes">
        <p><strong>Provider/history coverage</strong> reports whether the requested historical evidence scope completed.</p>
        <p><strong>Fresh balance observation</strong> reports whether current target-token inventory was directly observed for this run.</p>
      </div>
    </article>
  );
}

function freshBalanceText(result: AnalyzerResult) {
  const values = Object.values(result.current_inventory ?? {});
  if (values.some((value) => value !== null && value !== undefined)) return "OBSERVED";
  const requests = result.provider_coverage?.requests ?? [];
  const fresh = requests.find((request) => String(request.scope ?? "").toLowerCase().includes("balance"));
  if (fresh) return fresh.complete === true ? "OBSERVED" : "NOT OBSERVED";
  return "NOT OBSERVED";
}

export function WalletSummary({ result, publicCase = false }: { result: AnalyzerResult; publicCase?: boolean }) {
  if (!result.seed_wallet) return null;
  const decimals = tokenDecimals(result);
  const accounting = seedAccounting(result);
  const sales = record(Object.keys(result.sales ?? {}).length ? result.sales : accounting.confirmed_sales);
  const increases = record(Object.keys(result.buys_reacquisitions ?? {}).length ? result.buys_reacquisitions : accounting.inventory_increases);
  const increaseCounts = record(increases.counts);
  const lifecycle = record(accounting.lifecycle);
  const lifecycleReacquisitions = record(lifecycle.reacquisitions);
  const transferRecord = record(result.transfers);
  const incoming = records(transferRecord.incoming);
  const outgoing = records(transferRecord.outgoing);
  const verification = record(accounting.verify_exit_evidence);
  const incomingCount = incoming.length || numberValue(record(verification.incoming_transfers).count) || 0;
  const outgoingCount = outgoing.length || numberValue(record(verification.outgoing_transfers).count) || 0;
  const saleEvents = records(sales.events);
  const salesCoverageComplete = sales.amount_known !== false && sales.coverage_complete !== false;
  const observedSaleCount = sales.count ?? saleEvents.length ?? 0;
  const saleCountText = salesCoverageComplete ? String(observedSaleCount) : `UNKNOWN (${String(observedSaleCount)} observed)`;
  const increaseEvents = records(increases.events);
  const confirmedBuyCount = numberValue(increaseCounts.CONFIRMED_BUY)
    ?? numberValue(increases.confirmed_buy_count)
    ?? increaseEvents.filter((event) => event.classification === "CONFIRMED_BUY" || event.event_type === "BUY").length;
  const reacquisitionCount = numberValue(lifecycleReacquisitions.count)
    ?? numberValue(increases.reacquisition_count)
    ?? numberValue(increaseCounts.REACQUISITION)
    ?? 0;
  const starting = lifecycle.reconstructed_starting_inventory_raw
    ?? accounting.reconstructed_starting_target_inventory_raw
    ?? accounting.reconstructed_starting_inventory_raw;
  const current = currentSeedBalance(result);
  const startingNumber = numberValue(starting);
  const currentNumber = numberValue(current);
  const remaining = startingNumber && currentNumber !== null ? `${((currentNumber / startingNumber) * 100).toFixed(2)}%` : "UNKNOWN";
  const largestSell = Math.max(0, ...saleEvents.map((event) => numberValue(event.token_amount_raw) ?? 0));
  const proceeds = record(Object.keys(result.proceeds ?? {}).length ? result.proceeds : accounting.proceeds);
  return (
    <article className="panel summary-card wallet-card">
      <div className="card-title"><WalletCards size={17} /><div><p className="eyebrow">Seed wallet</p><h3><Address value={result.seed_wallet} /></h3></div></div>
      <div className="metric-strip">
        <Metric icon={<Coins size={16} />} label="Current target balance" value={formatRawAmount(current, decimals)} />
        <Metric icon={<History size={16} />} label="Reconstructed start" value={formatRawAmount(starting, decimals)} />
        <Metric icon={<Activity size={16} />} label="Current inventory vs window start" value={remaining} />
      </div>
      <dl className="detail-grid compact">
        <div><dt>Confirmed sells</dt><dd>{saleCountText}</dd></div>
        <div><dt>Largest sell</dt><dd>{formatRawAmount(largestSell || sales.largest_sale_raw, decimals)}</dd></div>
        <div><dt>Confirmed buys</dt><dd>{confirmedBuyCount}</dd></div>
        <div><dt>Reacquisitions</dt><dd>{reacquisitionCount}</dd></div>
        <div><dt>Incoming transfers</dt><dd><ArrowDownLeft size={13} /> {incomingCount}</dd></div>
        <div><dt>Outgoing transfers</dt><dd><ArrowUpRight size={13} /> {outgoingCount}</dd></div>
        <div><dt>Attributable proceeds</dt><dd>{formatProceeds(proceeds, publicCase)}</dd></div>
        <div><dt>Historical exit status</dt><dd>{formatStatus(result.historical_exit_status)}</dd></div>
        <div><dt>Current wallet status</dt><dd>{formatStatus(result.current_wallet_status ?? result.wallet_status)}</dd></div>
      </dl>
      <div className="truth-notes">
        <p><strong>Confirmed buys</strong> count all observed market buys in the requested window.</p>
        <p><strong>Reacquisitions</strong> count confirmed target-token inventory entering after a defensible exit; pre-exit buys are not reacquisitions.</p>
      </div>
    </article>
  );
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div className="metric"><span>{icon}</span><div><small>{label}</small><strong>{value}</strong></div></div>;
}

function formatProceeds(value: Record<string, unknown>, publicCase = false) {
  if (!Object.keys(value).length) return publicCase ? "NOT INCLUDED IN PUBLIC CASE" : "UNKNOWN";
  if (Array.isArray(value.quote_totals) && value.quote_totals.length) {
    return value.quote_totals.map((rawValue) => {
      const item = record(rawValue);
      return `${formatRawAmount(item.amount_raw, numberValue(item.decimals) ?? 0)} ${String(item.asset ?? item.symbol ?? "QUOTE")}`;
    }).join(" · ");
  }
  if (numberValue(value.gross_native_received_lamports)) {
    return `${formatRawAmount(value.gross_native_received_lamports, 9)} SOL`;
  }
  return Object.entries(value).map(([asset, rawValue]) => {
    if (!rawValue || typeof rawValue !== "object") return null;
    const item = record(rawValue);
    const amount = item.amount_raw ?? item.quote_amount_raw ?? rawValue;
    const decimals = numberValue(item.decimals) ?? (asset === "SOL" ? 9 : 0);
    return `${formatRawAmount(amount, decimals)} ${String(item.symbol ?? asset)}`;
  }).filter(Boolean).join(" · ") || "UNKNOWN";
}
