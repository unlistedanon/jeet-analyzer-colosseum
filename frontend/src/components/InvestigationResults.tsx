import { AnswerSummary } from "./AnswerSummary";
import { AlertTriangle } from "lucide-react";
import type { AnalyzerResult } from "../types";
import { coverageComplete } from "../utils";
import { ControlRiskPanel } from "./ControlRiskPanel";
import { EvidenceGraph } from "./EvidenceGraph";
import { EvidencePanel } from "./EvidencePanel";
import { ReceiptsPanel } from "./ReceiptsPanel";
import { RelatedInventory } from "./RelatedInventory";
import { SellerLeaderboard } from "./SellerLeaderboard";
import { StatusHero } from "./StatusHero";
import { TokenSummary, WalletSummary } from "./SummaryCards";

export function InvestigationResults({ result, investigationId, artifacts, publicCase = false }: { result: AnalyzerResult; investigationId?: string; artifacts?: Record<string, string>; publicCase?: boolean }) {
  const incomplete = !coverageComplete(result);
  const seedEstablished = result.provider_coverage?.seed_wallet_complete === true;
  const targetClusterStatus = result.target_cluster_status ?? result.cluster_status;
  const seedFirstPartial = seedEstablished && targetClusterStatus === "INSUFFICIENT_DATA";
  return (
    <div className="results-stack">
      <AnswerSummary result={result} />
      {incomplete ? <div className="global-incomplete" role="alert"><AlertTriangle size={21} /><div><strong>{publicCase ? "DOWNSTREAM RELATIONSHIP GRAPH: INCOMPLETE." : "INCOMPLETE COVERAGE — conclusions remain fail-closed."}</strong><span>{publicCase ? "SEED WALLET LIFECYCLE: COMPLETE UNDER INDEXED-PROVIDER CONTRACT. Directly observed re-entry and current inventory remain established; missing downstream evidence is never treated as zero." : seedFirstPartial ? "Seed wallet exit was established, but related-wallet coverage was incomplete." : "Proven partial evidence is shown below. Missing evidence is never treated as zero."}</span></div></div> : null}
      <StatusHero result={result} />
      <div className="summary-grid"><TokenSummary result={result} publicCase={publicCase} /><WalletSummary result={result} publicCase={publicCase} /></div>
      <SellerLeaderboard result={result} />
      <EvidenceGraph result={result} />
      <ControlRiskPanel result={result} />
      <RelatedInventory result={result} />
      <EvidencePanel result={result} publicCase={publicCase} />
      <ReceiptsPanel result={result} investigationId={investigationId} artifacts={artifacts} />
    </div>
  );
}
