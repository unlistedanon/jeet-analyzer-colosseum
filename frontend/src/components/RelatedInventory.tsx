import { Boxes, Info } from "lucide-react";
import type { AnalyzerResult } from "../types";
import { formatRawAmount, tokenDecimals } from "../utils";
import { Address } from "./Address";

export function RelatedInventory({ result }: { result: AnalyzerResult }) {
  const rows = result.related_target_token_inventory ?? [];
  if (!rows.length && !result.cluster_status) return null;
  const decimals = tokenDecimals(result);
  const total = result.current_inventory?.visible_cluster_target_inventory_raw ?? result.visible_cluster_target_inventory_raw;
  return (
    <section className="panel inventory-panel" aria-labelledby="inventory-heading">
      <div className="section-heading"><div><p className="eyebrow">Fresh visible evidence</p><h2 id="inventory-heading"><Boxes size={18} /> Related target-token inventory</h2></div></div>
      <div className="inventory-total"><small>VISIBLE CLUSTER TARGET INVENTORY</small><strong>{formatRawAmount(total, decimals)}</strong><span>Observed in evidence-backed wallets; this is not a claim about entire cluster holdings.</span></div>
      {result.provider_coverage?.complete === false && <p className="inline-caveat"><Info size={15} /> Visible inventory may be incomplete because graph/provider coverage is incomplete.</p>}
      <div className="inventory-rows">
        {rows.map((row, index) => {
          const path = Array.isArray(row.relationship_path) ? row.relationship_path.join(" → ") : row.relationship_type ?? "EVIDENCE LINK";
          return <article key={`${row.wallet}-${index}`}><Address value={String(row.wallet ?? "")} /><dl><div><dt>Balance</dt><dd>{formatRawAmount(row.current_target_token_balance_raw, decimals)}</dd></div><div><dt>Relationship</dt><dd>{String(path)}</dd></div><div><dt>Status</dt><dd>{String(row.status ?? "VISIBLE")}</dd></div><div><dt>Evidence</dt><dd>{String(row.evidence_strength ?? row.relationship_type ?? "DETERMINISTIC LINK")}</dd></div></dl></article>;
        })}
        {!rows.length && <div className="honest-empty"><strong>No evidence-backed related-wallet inventory was emitted.</strong><p>This is not proof that no related inventory exists.</p></div>}
      </div>
    </section>
  );
}
