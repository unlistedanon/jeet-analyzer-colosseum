import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { GitBranch, Network } from "lucide-react";
import { useMemo, useState } from "react";
import type { AnalyzerResult, RelationshipEdge } from "../types";
import { formatRawAmount, record, relationships, shortAddress, tokenDecimals } from "../utils";
import { Address } from "./Address";

type SelectedEvidence = { kind: "node"; id: string; data: Record<string, unknown> } | { kind: "edge"; edge: RelationshipEdge };

export function EvidenceGraph({ result }: { result: AnalyzerResult }) {
  const [selected, setSelected] = useState<SelectedEvidence | null>(null);
  const graph = useMemo(() => buildGraph(result), [result]);
  const walletAuditWithoutExpansion = result.schema === "jeet-analyzer.wallet-ui.v1" && graph.rawEdges.length === 0;
  if (!graph.nodes.length) return null;
  if (walletAuditWithoutExpansion) {
    return (
      <section className="panel graph-panel graph-panel-empty" aria-labelledby="graph-heading">
        <div className="section-heading"><div><p className="eyebrow">Relationship evidence</p><h2 id="graph-heading"><Network size={18} /> Evidence graph</h2></div><span className="panel-note">Edges indicate evidence, never ownership.</span></div>
        <div className="honest-empty"><strong>Relationship expansion was not performed for this Wallet Audit.</strong><p>Run Cluster Audit to map evidence-backed related wallets. No relationship edge is implied by this empty view.</p></div>
      </section>
    );
  }
  return (
    <section className="panel graph-panel" aria-labelledby="graph-heading">
      <div className="section-heading"><div><p className="eyebrow">Relationship evidence</p><h2 id="graph-heading"><Network size={18} /> Evidence graph</h2></div><span className="panel-note">Edges indicate evidence, never ownership.</span></div>
      <div className="graph-layout">
        <div className="flow-canvas" aria-label="Interactive wallet relationship graph">
          <ReactFlow
            nodes={graph.nodes}
            edges={graph.edges}
            fitView
            minZoom={0.35}
            maxZoom={1.8}
            nodesDraggable
            nodesConnectable={false}
            onNodeClick={(_, node) => setSelected({ kind: "node", id: node.id, data: record(node.data.raw) })}
            onEdgeClick={(_, edge) => setSelected({ kind: "edge", edge: record(edge.data?.raw) as RelationshipEdge })}
          >
            <Background color="#263039" gap={24} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        <aside className="evidence-inspector" aria-live="polite">
          {selected ? <EvidenceDetail selected={selected} result={result} /> : <div className="inspector-empty"><GitBranch size={22} /><strong>Select evidence</strong><p>Click a node or edge to inspect the facts supporting it.</p></div>}
        </aside>
      </div>
      <div className="edge-evidence-list" aria-label="Graph evidence controls">
        {graph.rawEdges.map((edge, index) => <button type="button" key={`${edge.signature}-${index}`} onClick={() => setSelected({ kind: "edge", edge })}>Inspect edge {shortAddress(edge.source)} → {shortAddress(edge.destination)} · {edge.relationship_type ?? edge.classification ?? "UNKNOWN"}</button>)}
      </div>
    </section>
  );
}

function buildGraph(result: AnalyzerResult) {
  const directEdges = relationships(result);
  const shared = result.shared_funders ?? result.wallet_graph?.shared_funders ?? [];
  const sharedContextEdges: RelationshipEdge[] = shared.flatMap((relationship) => (relationship.recipients ?? []).map((recipient) => ({
    source: relationship.funder,
    destination: recipient,
    relationship_type: "THIRD_PARTY_SHARED_FUNDER",
    classification: "SHARED_FUNDER",
    relationship_context: "RECIPIENT_TO_RECIPIENT_VIA_THIRD_PARTY",
    common_control: "NOT_PROVEN",
    reason: relationship.reason,
    supporting_transfers: relationship.supporting_transfers,
  })));
  const rawEdges = [...directEdges, ...sharedContextEdges].filter((edge) => edge.source && edge.destination);
  const rawNodes = result.wallet_graph?.nodes ?? [];
  const related = result.related_target_token_inventory ?? [];
  const infrastructure = result.excluded_infrastructure ?? [];
  const nodeData = new Map<string, Record<string, unknown>>();
  if (result.seed_wallet) nodeData.set(result.seed_wallet, { wallet: result.seed_wallet, node_kind: "seed" });
  for (const item of [...rawNodes, ...related]) {
    const wallet = String(item.wallet ?? item.address ?? "");
    if (wallet) nodeData.set(wallet, { ...nodeData.get(wallet), ...item, node_kind: wallet === result.seed_wallet ? "seed" : "wallet" });
  }
  for (const edge of rawEdges) {
    if (edge.source && !nodeData.has(edge.source)) nodeData.set(edge.source, { wallet: edge.source, node_kind: edge.relationship_type === "THIRD_PARTY_SHARED_FUNDER" ? "funder" : "wallet" });
    if (edge.destination && !nodeData.has(edge.destination)) nodeData.set(edge.destination, { wallet: edge.destination, node_kind: "wallet" });
  }
  for (const item of infrastructure) {
    const address = String(item.address ?? item.wallet ?? item.account ?? item.program ?? "");
    if (address) nodeData.set(address, { ...item, wallet: address, node_kind: "infrastructure" });
  }
  const columns = { seed: 0, wallet: 1, funder: 2, infrastructure: 3 };
  const counts: Record<string, number> = {};
  const nodes: Node[] = [...nodeData.entries()].map(([id, data]) => {
    const kind = String(data.node_kind ?? "wallet") as keyof typeof columns;
    const row = counts[kind] ?? 0;
    counts[kind] = row + 1;
    const balance = data.current_target_token_balance_raw;
    return {
      id,
      position: { x: columns[kind] * 260, y: row * 115 + (kind === "seed" ? 100 : 0) },
      data: { label: `${kind.toUpperCase()}\n${shortAddress(id)}${balance != null ? `\nBAL ${formatRawAmount(balance, tokenDecimals(result), true)}` : ""}`, raw: data },
      className: `flow-node node-${kind}`,
      style: { whiteSpace: "pre-line" },
    };
  });
  const edges: Edge[] = rawEdges.map((edge, index) => ({
    id: `${edge.signature ?? edge.relationship_type ?? "edge"}-${index}`,
    source: edge.source!,
    target: edge.destination!,
    label: edgeLabel(edge),
    animated: edge.relationship_type === "PLAIN_DIRECT_SOL_TRANSFER",
    className: edge.relationship_type === "THIRD_PARTY_SHARED_FUNDER" ? "edge-shared" : "edge-direct",
    markerEnd: { type: MarkerType.ArrowClosed, color: "#5ee8d5" },
    data: { raw: edge },
  }));
  return { nodes, edges, rawEdges };
}

function edgeLabel(edge: RelationshipEdge) {
  if (edge.relationship_type === "PLAIN_DIRECT_SOL_TRANSFER") return "DIRECT SOL FUNDING";
  if (edge.relationship_type === "THIRD_PARTY_SHARED_FUNDER") return "THIRD-PARTY SHARED FUNDER";
  return edge.classification ?? edge.relationship_type ?? "EVIDENCE LINK";
}

function EvidenceDetail({ selected, result }: { selected: SelectedEvidence; result: AnalyzerResult }) {
  if (selected.kind === "node") {
    const data = selected.data;
    return <div><p className="eyebrow">Selected node</p><h3><Address value={selected.id} /></h3><dl className="inspector-list"><div><dt>Node type</dt><dd>{String(data.node_kind ?? data.classification ?? "wallet")}</dd></div><div><dt>Current target inventory</dt><dd>{formatRawAmount(data.current_target_token_balance_raw, tokenDecimals(result))}</dd></div><div><dt>Evidence summary</dt><dd>{String(data.reason ?? data.relationship_type ?? "Included by evidence graph")}</dd></div></dl></div>;
  }
  const edge = selected.edge;
  const amount = edge.amount_raw ?? edge.token_amount_raw;
  const decimals = edge.asset === "SOL" ? 9 : tokenDecimals(result);
  const supportingTransfers = Array.isArray(edge.supporting_transfers) ? edge.supporting_transfers as RelationshipEdge[] : [];
  return <div><p className="eyebrow">Selected edge</p><h3>{edgeLabel(edge)}</h3><div className="direction"><Address value={edge.source} /> <span>→</span> <Address value={edge.destination} /></div><dl className="inspector-list">
    <div><dt>Relationship type</dt><dd>{edge.relationship_type ?? "UNKNOWN"}</dd></div>
    <div><dt>Context</dt><dd>{edge.relationship_context ?? "NONE"}</dd></div>
    <div><dt>Amount</dt><dd>{formatRawAmount(amount, decimals)} {edge.asset ?? "TARGET_TOKEN"}</dd></div>
    <div><dt>Mint / asset</dt><dd>{edge.mint ?? edge.asset ?? result.mint ?? "UNKNOWN"}</dd></div>
    <div><dt>Signature</dt><dd><Address value={edge.signature} /></dd></div>
    <div><dt>Timestamp</dt><dd>{edge.timestamp ?? "UNKNOWN"}</dd></div>
    <div><dt>Source signer</dt><dd>{edge.source_signed === true ? "TRUE" : edge.source_signed === false ? "FALSE" : "UNKNOWN"}</dd></div>
    <div><dt>Fee payer</dt><dd>{edge.fee_payer ?? (edge.source_paid_fee ? edge.source : "UNKNOWN")}</dd></div>
    <div><dt>Market context</dt><dd>{edge.market_context === true ? "TRUE" : edge.market_context === false ? "FALSE" : "UNKNOWN"}</dd></div>
    <div><dt>Classification</dt><dd>{edge.classification ?? "UNKNOWN"}</dd></div>
    <div><dt>Confidence / reason</dt><dd>{String(edge.confidence ?? edge.reason ?? "Evidence fields shown above")}</dd></div>
    {supportingTransfers.length > 0 && <div><dt>Supporting signatures</dt><dd>{supportingTransfers.map((item) => item.signature ?? "UNKNOWN").join(" · ")}</dd></div>}
    <div><dt>Common control</dt><dd>NOT_PROVEN</dd></div>
  </dl></div>;
}
