import { useEffect, useMemo, useState } from "react";
import type { AnalyzerResult } from "../types";
import { formatRawAmount, record, tokenDecimals } from "../utils";
import videoPlan from "../video/week1-video.json";

type CapturePosition = { scene: number; caption: number };
type TimelineEntry = { scene: number; caption: number; start: number; end: number; text: string };

export function Week1Video({ result, capture }: { result: AnalyzerResult; capture?: CapturePosition }) {
  const timeline = useMemo(buildTimeline, []);
  const captureEntry = capture
    ? timeline.find((entry) => entry.scene === capture.scene && entry.caption === capture.caption)
    : undefined;
  const [elapsed, setElapsed] = useState(captureEntry?.start ?? 0);

  useEffect(() => {
    if (captureEntry) return;
    const started = performance.now();
    const interval = window.setInterval(() => {
      setElapsed(Math.min((performance.now() - started) / 1000, videoPlan.total_duration_seconds - 0.01));
    }, 80);
    return () => window.clearInterval(interval);
  }, [captureEntry]);

  const active = captureEntry ?? timeline.find((entry) => elapsed >= entry.start && elapsed < entry.end) ?? timeline.at(-1)!;
  const scene = videoPlan.scenes[active.scene];
  const summary = record(result.demo_summary);
  const decimals = tokenDecimals(result);
  const facts = {
    sold: formatRawAmount(summary.exit_sale_raw, decimals, true),
    zero: formatRawAmount(summary.exit_balance_after_raw, decimals, true),
    boughtBack: formatRawAmount(summary.post_exit_reacquired_raw, decimals, true),
    current: formatRawAmount(summary.current_inventory_raw, decimals, true),
    credits: String(summary.estimated_original_provider_credits ?? "NOT INCLUDED"),
    calls: String(summary.offline_replay_provider_calls ?? "NOT INCLUDED"),
  };
  const progress = Math.min(100, ((captureEntry?.start ?? elapsed) / videoPlan.total_duration_seconds) * 100);

  return (
    <main className={`week1-video week1-scene-${scene.kind}${captureEntry ? " week1-capture" : ""}`}>
      <header className="week1-brand"><span>JA</span><div><small>Solana forensic workbench</small><strong>JEET ANALYZER</strong></div><em>ETERNAL / WEEK 1</em></header>
      <section className="week1-stage" aria-label={`Video scene ${active.scene + 1}: ${scene.id}`}>
        <Scene kind={scene.kind} result={result} facts={facts} />
      </section>
      <div className="week1-caption" aria-live="polite">{active.text.split("\n").map((line) => <span key={line}>{line}</span>)}</div>
      <footer className="week1-timeline"><span style={{ width: `${progress}%` }} /><small>SCENE {active.scene + 1} / {videoPlan.scenes.length}</small></footer>
      {!captureEntry && <audio autoPlay src="/demo/week1-narration.wav" onTimeUpdate={(event) => setElapsed(event.currentTarget.currentTime)} />}
    </main>
  );
}

function Scene({ kind, result, facts }: { kind: string; result: AnalyzerResult; facts: Record<string, string> }) {
  if (kind === "intro") return <div className="week1-intro"><p>ONE SIMPLE QUESTION</p><h1>The jeet sold,<br /><span>but is he really out?</span></h1><div>READ-ONLY · EVIDENCE-FIRST · ZERO SIGNING</div></div>;
  if (kind === "lifecycle") return <div className="week1-lifecycle"><p>THE OBSERVED LIFECYCLE</p><Lifecycle label="SOLD" value={facts.sold} /><i>→</i><Lifecycle label="BALANCE HIT" value={facts.zero} /><i>→</i><Lifecycle label="BOUGHT BACK" value={facts.boughtBack} /><div className="week1-transition"><strong>{result.historical_exit_status}</strong><span>→</span><strong>{result.current_wallet_status}</strong></div></div>;
  if (kind === "conclusion") return <div className="week1-conclusion"><p>THE ANSWER CHANGED WITH THE EVIDENCE</p><VideoMetric label="Historical wallet" value={result.historical_exit_status} /><VideoMetric label="Current wallet" value={result.current_wallet_status} accent /><VideoMetric label="Cluster" value={result.cluster_status} /><VideoMetric label="Current inventory" value={facts.current} /></div>;
  if (kind === "evidence") return <div className="week1-evidence"><p>FOLLOW THE POSITION, NOT JUST THE SELL</p><h2>Jeet reconstructs what happened next.</h2><div>{["INVENTORY", "TRANSFERS", "RE-ENTRY", "RELATED WALLETS", "COVERAGE"].map((value) => <strong key={value}>{value}</strong>)}</div></div>;
  if (kind === "honesty") return <div className="week1-honesty"><p>EVIDENCE HONESTY</p><h2>COMMON CONTROL: <span>{result.common_control}</span></h2><div><strong>SEED WALLET LIFECYCLE</strong><em>COMPLETE UNDER INDEXED-PROVIDER CONTRACT</em></div><div><strong>DOWNSTREAM RELATIONSHIP GRAPH</strong><em>INCOMPLETE</em></div><small>Relationships are evidence, not ownership.</small></div>;
  if (kind === "proof") return <div className="week1-proof"><p>ENGINEERING PROOF</p><VideoMetric label="Python" value={`${videoPlan.proof.python_tests}/${videoPlan.proof.python_tests} PASSED`} /><VideoMetric label="Frontend" value={`${videoPlan.proof.frontend_tests}/${videoPlan.proof.frontend_tests} PASSED`} /><VideoMetric label="Original provider cost" value={`${facts.credits} ESTIMATED CREDITS`} /><VideoMetric label="Offline replay" value={`${facts.calls} PROVIDER CALLS`} accent /></div>;
  return <div className="week1-final"><p>SOLD → ZERO → BOUGHT BACK</p><h2>{result.historical_exit_status} <span>→</span> {result.current_wallet_status}</h2><strong>{videoPlan.final_card}</strong></div>;
}

function Lifecycle({ label, value }: { label: string; value: string }) {
  return <div><small>{label}</small><strong>{value}</strong></div>;
}

function VideoMetric({ label, value, accent = false }: { label: string; value: unknown; accent?: boolean }) {
  return <div className={accent ? "week1-metric accent" : "week1-metric"}><small>{label}</small><strong>{String(value ?? "NOT INCLUDED")}</strong></div>;
}

function buildTimeline(): TimelineEntry[] {
  let cursor = 0;
  return videoPlan.scenes.flatMap((scene, sceneIndex) => scene.captions.map((caption, captionIndex) => {
    const entry = { scene: sceneIndex, caption: captionIndex, start: cursor, end: cursor + caption.duration_seconds, text: caption.text };
    cursor = entry.end;
    return entry;
  }));
}
