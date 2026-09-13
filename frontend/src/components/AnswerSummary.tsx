import type { AnalyzerResult } from "../types";
import { currentSeedBalance, formatRawAmount, numberValue, record, records, relationships, seedAccounting, timeText, tokenDecimals } from "../utils";

export function AnswerSummary({ result }: { result: AnalyzerResult }) {
  if (!result.seed_wallet) return null;
  const sales = Object.keys(result.sales ?? {}).length ? record(result.sales) : record(seedAccounting(result).confirmed_sales);
  const saleEvents = records(sales.events);
  const saleCount = numberValue(sales.count);
  const sold = (saleCount ?? 0) > 0 || saleEvents.length > 0;
  const noSalesInScope = saleCount === 0 && sales.count_known !== false && sales.coverage_complete === true;
  const balance = numberValue(currentSeedBalance(result));
  const targetMint = result.mint ?? result.token?.mint;
  // Funding in SOL or another token must not imply a transfer of this token.
  const transfers = relationships(result).filter((edge) =>
    edge.source === result.seed_wallet && edge.destination && edge.destination !== result.seed_wallet
    && targetMint && (edge.mint ?? edge.asset) === targetMint && edge.market_context !== true
    && edge.relationship_type === "TARGET_TOKEN_TRANSFER" && (numberValue(edge.amount_raw ?? edge.token_amount_raw) ?? 0) > 0,
  );
  const complete = result.provider_coverage?.complete === true;
  return <section className="panel answer-summary" aria-label="Your answer">
    <p className="eyebrow">Your answer · evidence from this report</p>
    <h2>What happened to the tokens?</h2>
    <div className="answer-grid">
      <article><h3>Did this wallet sell?</h3><strong>{sold ? "Yes — sales observed" : noSalesInScope ? "No confirmed sales in scope" : "Not established"}</strong>
        <p>{sold ? `${saleCount ?? saleEvents.length} confirmed sale(s) reported. This does not by itself mean the wallet exited completely.` : noSalesInScope ? "The completed sales scope reports zero confirmed sales. This is limited to the requested history." : "The report does not establish whether this wallet sold. Missing history is not evidence of no sales."}</p>
        {saleEvents.some((event) => typeof event.signature === "string") && <details><summary>Supporting sales</summary><ul>{saleEvents.filter((event) => typeof event.signature === "string").slice(0, 3).map((event, index) => <li key={`${event.signature}-${index}`}><code>{String(event.signature)}</code><small>{timeText(event.timestamp ?? event.block_time)}</small></li>)}</ul></details>}
      </article>
      <article><h3>Does it still hold tokens?</h3><strong>{balance === null || balance < 0 ? "Not established" : balance > 0 ? "Yes — balance observed" : "Zero balance observed"}</strong>
        <p>{balance !== null && balance >= 0 ? `${formatRawAmount(balance, tokenDecimals(result))} target tokens at the report’s balance check. This is a snapshot, not a live balance.` : "A current target-token balance was not established in this report."}</p>
        {result.current_wallet_status === "RE_ENTERED" && <p>The lifecycle evidence also records re-entry after an exit.</p>}
      </article>
      <article><h3>Did tokens move to other wallets?</h3><strong>{transfers.length ? "Yes — transfers observed" : "Not established"}</strong>
        <p>{transfers.length ? "Direct transfers of this token are recorded below. A recipient is not proof of the same owner." : "No direct transfer of this token is established by the relationship evidence shown here. Other links, such as SOL funding, do not prove it moved."}</p>
        {transfers.length > 0 && <details><summary>Supporting transfers</summary><ul>{transfers.slice(0, 3).map((edge, index) => <li key={`${edge.signature}-${index}`}><code>{edge.destination}</code>{edge.signature && <small>Transaction: {edge.signature}</small>}</li>)}</ul></details>}
      </article>
    </div>
    <p className="answer-coverage"><strong>{complete ? "Coverage completed within the requested scope." : "Coverage is incomplete or unconfirmed."}</strong> {complete ? "These answers remain limited to the evidence and observation times in this report." : "Observed sales and balances can still be useful. Missing evidence cannot establish a full exit or common ownership."} Full evidence and coverage receipt follow below.</p>
  </section>;
}
