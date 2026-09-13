import { Check, Clipboard, Download, FileJson, FileText } from "lucide-react";
import { useState } from "react";
import { eventsUrl, receiptUrl } from "../api";
import type { AnalyzerResult } from "../types";

export function ReceiptsPanel({ result, investigationId, artifacts = {} }: { result: AnalyzerResult; investigationId?: string; artifacts?: Record<string, string> }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(JSON.stringify(result, null, 2));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };
  const downloadLocal = () => {
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "jeet-analyzer-result.json";
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <section className="panel receipts-panel" aria-labelledby="receipts-heading">
      <div className="section-heading"><div><p className="eyebrow">Machine-readable evidence</p><h2 id="receipts-heading"><FileJson size={18} /> Receipts</h2></div></div>
      <div className="receipt-actions">
        <button type="button" onClick={copy}>{copied ? <Check size={16} /> : <Clipboard size={16} />} {copied ? "COPIED" : "COPY JSON"}</button>
        {investigationId ? <a href={receiptUrl(investigationId)} download><Download size={16} /> DOWNLOAD RECEIPT</a> : <button type="button" onClick={downloadLocal}><Download size={16} /> DOWNLOAD RECEIPT</button>}
        {investigationId && <a href={eventsUrl(investigationId)} target="_blank" rel="noreferrer"><FileText size={16} /> VIEW EVENT JSONL</a>}
      </div>
      <dl className="receipt-paths">
        {Object.entries(artifacts).map(([name, path]) => <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd><a href={path} download>{path}</a></dd></div>)}
        {!Object.keys(artifacts).length && Object.entries(result.evidence_receipts ?? {}).map(([name, path]) => <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd>{String(path)}</dd></div>)}
      </dl>
      <details className="json-viewer"><summary>View result JSON</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
    </section>
  );
}
