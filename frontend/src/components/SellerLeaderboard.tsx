import { AlertTriangle, ListFilter } from "lucide-react";
import type { AnalyzerResult, SellerRow } from "../types";
import { formatRawAmount, numberValue, record, timeText, tokenDecimals } from "../utils";
import { Address } from "./Address";

export function SellerLeaderboard({ result }: { result: AnalyzerResult }) {
  const sellers = (result.sellers ?? result.seller_rows ?? []) as SellerRow[];
  const incomplete = result.provider_coverage?.complete === false || result.status === "INSUFFICIENT_DATA";
  const decimals = tokenDecimals(result);
  const isScan = Boolean(result.schema?.includes("scan") || result.schema?.includes("summary"));
  if (!sellers.length && !isScan) return null;
  return (
    <section className="panel table-panel" aria-labelledby="leaderboard-heading">
      <div className="section-heading"><div><p className="eyebrow">Seller discovery</p><h2 id="leaderboard-heading"><ListFilter size={18} /> Seller leaderboard</h2></div></div>
      {incomplete && <div className="coverage-banner warning"><AlertTriangle size={18} /><div><strong>PARTIAL COVERAGE</strong><span>Seller ranking is based only on evidence retrieved before provider coverage ended.</span></div></div>}
      {!sellers.length ? (
        incomplete
          ? <div className="honest-empty"><strong>No complete leaderboard was emitted.</strong><p>This does not mean no sellers exist. The engine failed closed before it could produce a defensible ranking.</p></div>
          : <div className="honest-empty"><strong>No confirmed sellers were observed.</strong><p>Coverage completed for the requested interval, but no confirmed sell events entered the ranking.</p></div>
      ) : (
        <div className="table-scroll"><table><thead><tr><th>Rank</th><th>Wallet</th><th>Sells</th><th>Tokens sold</th><th>Quote received</th><th>Largest sale</th><th>Current balance</th><th>Inventory remaining</th><th>Most recent</th><th>Status</th></tr></thead><tbody>
          {sellers.map((seller, index) => <tr key={`${seller.wallet}-${index}`}><td>{seller.rank ?? index + 1}</td><td><Address value={seller.wallet} /></td><td>{seller.sells ?? 0}</td><td>{formatRawAmount(seller.tokens_sold_raw, decimals)}</td><td>{quoteText(seller)}</td><td>{formatRawAmount(seller.largest_sale_raw, decimals)}</td><td>{formatRawAmount(seller.current_balance_raw, decimals)}</td><td>{seller.observed_inventory_remaining_percentage ?? "UNKNOWN"}{seller.observed_inventory_remaining_percentage != null ? "%" : ""}</td><td>{timeText(seller.most_recent_sell)}</td><td><span className="table-status">{seller.status ?? "UNRESOLVED"}</span></td></tr>)}
        </tbody></table></div>
      )}
    </section>
  );
}

function quoteText(seller: SellerRow) {
  if (typeof seller.quote_received === "string") return seller.quote_received;
  const totals = seller.quote_totals;
  if (!totals || !Object.keys(totals).length) return "UNKNOWN";
  return Object.entries(totals).map(([asset, value]) => {
    const item = record(value);
    if (Object.keys(item).length) {
      return `${formatRawAmount(item.amount_raw, numberValue(item.decimals) ?? 0)} ${String(item.symbol ?? asset)}`;
    }
    return `${String(value)} ${asset}`;
  }).join(" · ");
}
