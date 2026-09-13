import { AlertTriangle, Check, Circle, LoaderCircle, X } from "lucide-react";
import type { ProgressRecord, ProgressState } from "../types";

export const PROGRESS_PHASES = [
  "TOKEN_RESOLUTION", "SELLER_ANALYSIS", "WALLET_HISTORY", "TRANSACTION_CLASSIFICATION",
  "RELATIONSHIP_GRAPH", "FUNDING_ANCESTRY", "RELATED_INVENTORY", "CLUSTER_CONCLUSION", "RECEIPT_FINALIZATION",
];

function StateIcon({ state }: { state: ProgressState }) {
  if (state === "complete") return <Check size={14} />;
  if (state === "running") return <LoaderCircle className="spin" size={14} />;
  if (state === "incomplete") return <AlertTriangle size={14} />;
  if (state === "error") return <X size={14} />;
  return <Circle size={10} />;
}

export function ProgressPipeline({ progress, wrap = false }: { progress?: ProgressRecord; wrap?: boolean }) {
  const phases = progress?.phases ?? {};
  const latest = [...(progress?.events ?? [])].reverse();
  return (
    <section className="panel progress-panel" aria-labelledby="pipeline-heading">
      <div className="section-heading"><div><p className="eyebrow">Investigation pipeline</p><h2 id="pipeline-heading">Evidence processing</h2></div></div>
      <ol className={`pipeline-list${wrap ? " pipeline-list-wrap" : ""}`}>
        {PROGRESS_PHASES.map((phase) => {
          const state = phases[phase] ?? "pending";
          const event = latest.find((item) => item.phase === phase && item.state === state);
          return (
            <li key={phase} className={`phase-${state}`}>
              <span className="phase-icon"><StateIcon state={state} /></span>
              <div><strong>{phase.replaceAll("_", " ")}</strong><small>{event?.message ?? state}</small></div>
              {event?.count !== undefined && <span className="phase-count">{event.count}</span>}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
