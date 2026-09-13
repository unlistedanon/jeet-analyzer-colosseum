import { ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { BetaAdmin } from "./components/BetaAdmin";
import { BetaExperience } from "./components/BetaExperience";
import { FlagshipDemo } from "./components/FlagshipDemo";
import { InvestigationResults } from "./components/InvestigationResults";
import { ProgressPipeline } from "./components/ProgressPipeline";
import { Week1Video } from "./components/Week1Video";
import { WillTheyJeet } from "./components/WillTheyJeet";
import type { AnalyzerResult } from "./types";

export default function App() {
  const search = new URLSearchParams(window.location.search);
  const demo = search.get("demo");
  const demoMode = demo === "flagship";
  const videoMode = demo === "week1-video";
  const adminMode = search.get("admin") === "1";
  const gameMode = search.get("game") === "1";
  const presentationMode = demoMode || videoMode;
  const [demoResult, setDemoResult] = useState<AnalyzerResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!presentationMode) return;
    let active = true;
    void fetch("/demo/flagship-result.json", { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error("Offline result is missing. Run: python scripts\\demo_flagship.py");
        return response.json() as Promise<AnalyzerResult>;
      })
      .then((result) => { if (active) setDemoResult(result); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Offline demo could not be loaded."); });
    return () => { active = false; };
  }, [presentationMode]);

  if (videoMode) {
    const capture = search.get("capture") === "1"
      ? { scene: Number(search.get("scene") ?? 0), caption: Number(search.get("caption") ?? 0) }
      : undefined;
    if (error) return <main className="week1-video-error"><strong>VIDEO DEMO COULD NOT LOAD</strong><span>{error}</span></main>;
    if (!demoResult) return <main className="week1-video-loading">PREPARING OFFLINE EVIDENCE…</main>;
    return <Week1Video result={demoResult} capture={capture} />;
  }

  if (demoMode) return (
    <main className="app-shell demo-shell">
      <header className="masthead">
        <div className="brand-mark" aria-hidden="true">JA</div>
        <div><p className="eyebrow">Solana forensic workbench</p><h1>JEET ANALYZER</h1></div>
        <p className="product-line">Mint in. Forensic map out.</p>
        <span className="safety-mark"><ShieldCheck size={15} /> No signing. No trading.</span>
      </header>
      {error && <div className="api-error" role="alert"><strong>REQUEST FAILED</strong><span>{error}</span></div>}
      {demoResult && <section className="investigation-workspace">
        <div className="workspace-heading"><div><p className="eyebrow">Anonymized flagship</p><h2>offline lifecycle reconstruction</h2></div><span className="run-state state-complete">complete</span></div>
        <FlagshipDemo result={demoResult} />
        {demoResult.progress && <ProgressPipeline progress={demoResult.progress} wrap />}
        <InvestigationResults result={demoResult} publicCase />
      </section>}
      <footer><span>JEET ANALYZER / READ-ONLY</span><span>COMMON CONTROL requires proof.</span></footer>
    </main>
  );

  if (adminMode) return <BetaAdmin />;
  if (gameMode) return <WillTheyJeet />;
  return <BetaExperience />;
}
