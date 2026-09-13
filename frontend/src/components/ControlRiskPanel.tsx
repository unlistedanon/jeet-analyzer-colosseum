import { Crosshair, Link2, ShieldAlert, WalletCards } from "lucide-react";
import type { AnalyzerResult } from "../types";
import { formatRawAmount, numberValue, relationships, tokenDecimals } from "../utils";
import { Address } from "./Address";
import "./ControlRiskPanel.css";

interface ControlAssessment {
  wallet_a?: string;
  wallet_b?: string;
  link_status?: string;
  control_assessment?: string;
  independent_evidence_classes?: string[];
  supporting_signatures?: string[];
  distinct_transaction_count?: number;
  reason?: string;
  risk_interpretation?: string;
}

function assessments(result: AnalyzerResult): ControlAssessment[] {
  const direct = result.control_assessments;
  if (Array.isArray(direct)) return direct as ControlAssessment[];
  const nested = result.wallet_graph?.control_assessments;
  return Array.isArray(nested) ? nested as ControlAssessment[] : [];
}

function walletBalances(result: AnalyzerResult): Map<string, number> {
  const balances = new Map<string, number>();
  for (const node of result.wallet_graph?.nodes ?? []) {
    const wallet = String(node.wallet ?? "");
    const balance = numberValue(node.current_target_token_balance_raw);
    if (wallet && balance !== null) balances.set(wallet, balance);
  }
  for (const row of result.related_target_token_inventory ?? []) {
    const wallet = String(row.wallet ?? "");
    const balance = numberValue(row.current_target_token_balance_raw);
    if (wallet && balance !== null) balances.set(wallet, balance);
  }
  return balances;
}

function linkedWallets(result: AnalyzerResult): Set<string> {
  const seed = result.seed_wallet ?? "";
  const wallets = new Set<string>();
  for (const edge of relationships(result)) {
    const source = String(edge.source ?? "");
    const destination = String(edge.destination ?? "");
    if (source && source !== seed) wallets.add(source);
    if (destination && destination !== seed) wallets.add(destination);
  }
  return wallets;
}

function likelyWallets(result: AnalyzerResult, rows: ControlAssessment[]): Set<string> {
  const seed = result.seed_wallet ?? "";
  const wallets = new Set<string>();
  for (const row of rows) {
    if (row.control_assessment !== "LIKELY_COMMON_CONTROL") continue;
    const walletA = String(row.wallet_a ?? "");
    const walletB = String(row.wallet_b ?? "");
    if (walletA && walletA !== seed) wallets.add(walletA);
    if (walletB && walletB !== seed) wallets.add(walletB);
  }
  return wallets;
}

function sumWalletBalances(wallets: Set<string>, balances: Map<string, number>) {
  let total = 0;
  let known = 0;
  for (const wallet of wallets) {
    const balance = balances.get(wallet);
    if (balance === undefined) continue;
    total += Math.max(balance, 0);
    known += 1;
  }
  return { total, known };
}

function statusText(value?: string) {
  return value ? value.replaceAll("_", " ") : "NOT ESTABLISHED";
}

export function ControlRiskPanel({ result }: { result: AnalyzerResult }) {
  const rows = assessments(result);
  const linked = linkedWallets(result);
  if (!linked.size && !rows.length) return null;

  const decimals = tokenDecimals(result);
  const balances = walletBalances(result);
  const likelyPairs = rows.filter((row) => row.control_assessment === "LIKELY_COMMON_CONTROL");
  const stronglyLinkedPairs = rows.filter((row) => row.link_status === "STRONGLY_LINKED");
  const likely = likelyWallets(result, rows);
  const linkedInventory = sumWalletBalances(linked, balances);
  const likelyInventory = sumWalletBalances(likely, balances);

  return (
    <section className="panel control-risk-panel" aria-labelledby="control-risk-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Operator-risk intelligence</p>
          <h2 id="control-risk-heading"><ShieldAlert size={18} /> Linked wallet control risk</h2>
        </div>
        <span className="control-proof-boundary">PROOF: NOT PROVEN</span>
      </div>

      <div className="control-risk-metrics">
        <article>
          <Link2 size={17} />
          <small>Evidence-linked wallets</small>
          <strong>{linked.size}</strong>
          <span>One defensible on-chain relationship is enough to surface a wallet as linked.</span>
        </article>
        <article>
          <Crosshair size={17} />
          <small>Strongly linked pairs</small>
          <strong>{stronglyLinkedPairs.length}</strong>
          <span>Multiple independent relationship classes across separate transactions.</span>
        </article>
        <article className={likelyPairs.length ? "control-risk-hot" : ""}>
          <ShieldAlert size={17} />
          <small>Likely common-control pairs</small>
          <strong>{likelyPairs.length}</strong>
          <span>Risk assessment only. Legal identity and beneficial ownership remain unproven.</span>
        </article>
        <article className={likelyInventory.total > 0 ? "control-risk-hot" : ""}>
          <WalletCards size={17} />
          <small>Visible inventory on likely-control wallets</small>
          <strong>{formatRawAmount(likelyInventory.total, decimals)}</strong>
          <span>{likelyInventory.known}/{likely.size || 0} likely-control related wallets have a fresh visible balance.</span>
        </article>
      </div>

      <div className="control-risk-inventory-line">
        <span>VISIBLE TARGET INVENTORY ON ALL EVIDENCE-LINKED RELATED WALLETS</span>
        <strong>{formatRawAmount(linkedInventory.total, decimals)}</strong>
        <small>{linkedInventory.known}/{linked.size} linked wallets with a fresh visible balance. Missing balances are not treated as zero.</small>
      </div>

      {rows.length ? (
        <div className="control-pair-list">
          {rows.map((row, index) => {
            const hot = row.control_assessment === "LIKELY_COMMON_CONTROL";
            return (
              <article className={hot ? "control-pair control-pair-hot" : "control-pair"} key={`${row.wallet_a}-${row.wallet_b}-${index}`}>
                <div className="control-pair-head">
                  <div className="control-pair-addresses">
                    <Address value={String(row.wallet_a ?? "")} />
                    <span>↔</span>
                    <Address value={String(row.wallet_b ?? "")} />
                  </div>
                  <div className="control-pair-badges">
                    <span>{statusText(row.link_status)}</span>
                    <strong>{statusText(row.control_assessment)}</strong>
                  </div>
                </div>
                <p>{row.reason ?? "Accepted on-chain evidence links these wallets."}</p>
                <div className="control-evidence-chips">
                  {(row.independent_evidence_classes ?? []).map((item) => <span key={item}>{statusText(item)}</span>)}
                  <span>{row.distinct_transaction_count ?? row.supporting_signatures?.length ?? 0} TX</span>
                </div>
                <small>{row.risk_interpretation ?? "Relationship evidence informs exposure analysis without proving one human controls both wallets."}</small>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="honest-empty control-risk-empty">
          <strong>Linked wallets were found, but no pair-level control assessment was emitted.</strong>
          <p>The wallets remain surfaced as linked. Lack of a stronger control assessment does not erase the underlying relationship evidence.</p>
        </div>
      )}
    </section>
  );
}
