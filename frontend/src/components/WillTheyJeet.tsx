import { ArrowLeft, Crosshair, Skull, Trophy, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";

type Pick = "jeet" | "hold";
type LiveRound = { mint: string; creator: string; completed_at: number; completion_signature: string; status: string };
type Bucket = "under-5m" | "5-30m" | "30-60m" | "1-6h" | "6-24h" | "1-3d" | "3-7d";
type EndedRound = { mint: string; jeet_at: number; jeet_signature: string };

const ROUNDS: Array<{ title: string; ticker: string; clue: string; correct: Pick; reveal: string }> = [
  { title: "The chart nuked", ticker: "$RUGGED", clue: "Seller wallet dumped 92%… then a linked wallet starts buying the dip.", correct: "hold", reveal: "NOT A CLEAN EXIT. The seller may be gone, but the cluster is still holding ammunition." },
  { title: "The suspicious transfer", ticker: "$BAGGIE", clue: "Tokens leave the seller wallet five minutes before a sell wave.", correct: "jeet", reveal: "LIKELY JEET. The transfer looks like an exit move—but a real analyzer would still check coverage." },
  { title: "The quiet comeback", ticker: "$REENTRY", clue: "The wallet sells out, disappears for 48 hours, then receives the token again.", correct: "hold", reveal: "THE BAG CAME BACK. A sold-out wallet is not automatically out forever." },
];

export function WillTheyJeet() {
  const [round, setRound] = useState(0);
  const [pick, setPick] = useState<Pick | null>(null);
  const [points, setPoints] = useState(() => {
    const saved = Number(window.localStorage.getItem("jeet-paper-points"));
    const today = new Date().toISOString().slice(0, 10);
    const lastReset = window.localStorage.getItem("jeet-paper-reset");
    const startingPoints = Number.isFinite(saved) && saved >= 0 ? saved : 100;
    if (lastReset !== today) {
      const refilled = Math.max(100, startingPoints);
      window.localStorage.setItem("jeet-paper-points", String(refilled));
      window.localStorage.setItem("jeet-paper-reset", today);
      return refilled;
    }
    return startingPoints;
  });
  const [streak, setStreak] = useState(0);
  const [locked, setLocked] = useState(false);
  const [liveRound, setLiveRound] = useState<LiveRound | null>(null);
  const [bucket, setBucket] = useState<Bucket | null>(null);
  const [serverPrediction, setServerPrediction] = useState<{ bucket: Bucket; result: string | null; delta: number | null } | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const submitting = useRef(false);
  const revision = useRef(0);
  const displayedMint = useRef<string | null>(null);
  const [endedRound, setEndedRound] = useState<EndedRound | null>(null);
  const current = ROUNDS[round];
  const stake = 25;

  useEffect(() => {
    let active = true;
    const refresh = () => {
      if (submitting.current) return;
      const startedRevision = revision.current;
      void fetch("/api/game/state", { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error("State unavailable");
        return response.json();
      })
      .then((payload: { player?: { points: number }; active_round?: LiveRound | null; last_ended_round?: EndedRound | null; prediction?: { bucket: Bucket; result: string | null; delta: number | null } | null } | null) => {
        if (active && !submitting.current && startedRevision === revision.current) {
          setPoints(payload?.player?.points ?? 100);
          setLiveRound(payload?.active_round ?? null);
          setServerPrediction(payload?.prediction ?? null);
          setEndedRound(payload?.last_ended_round ?? null);
          const mint = payload?.active_round?.mint ?? null;
          if (displayedMint.current !== mint) {
            displayedMint.current = mint;
            setBucket(null);
            setPick(null);
            setLocked(Boolean(payload?.prediction));
            setSaveError(null);
          }
          if (payload?.active_round) setLocked(Boolean(payload.prediction));
        }
      })
      .catch(() => { /* Static paper cases remain available when the feed is unavailable. */ });
    };
    refresh();
    const timer = window.setInterval(refresh, 15_000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const lockPick = async () => {
    if (locked || submitting.current) return;
    if (liveRound) {
      if (!bucket || serverPrediction) return;
      submitting.current = true;
      revision.current += 1;
      setSaving(true);
      setSaveError(null);
      try {
        const response = await fetch("/api/game/predict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mint: liveRound.mint, bucket }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Your pick could not be saved. Please try again.");
        if (payload.ok !== true || !payload.state?.prediction || typeof payload.player?.points !== "number") {
          throw new Error("The server did not confirm your pick. Refresh to check before retrying.");
        }
        setPoints(payload.player.points);
        setServerPrediction(payload.state.prediction);
        setLocked(true);
      } catch (error) {
        setSaveError(error instanceof Error ? error.message : "Connection interrupted. Refresh to check whether your pick saved.");
      } finally {
        submitting.current = false;
        setSaving(false);
      }
      return;
    }
    if (!pick) return;
    const correct = pick === current.correct;
    setPoints((value) => {
      const next = Math.max(0, value + (correct ? stake : -stake));
      window.localStorage.setItem("jeet-paper-points", String(next));
      return next;
    });
    setStreak((value) => correct ? value + 1 : 0);
    setLocked(true);
  };

  const nextRound = () => {
    if (liveRound) return;
    setRound((value) => (value + 1) % ROUNDS.length);
    setPick(null);
    setLocked(false);
  };

  return (
    <main className="game-shell">
      <header className="game-topbar">
        <a className="game-back" href="/"><ArrowLeft size={16} /> BACK TO ANALYZER</a>
        <div className="game-mark"><Skull size={20} /><span>WILL THEY JEET?</span></div>
        <div className="game-score"><span>POINTS</span><strong>{points.toString().padStart(4, "0")}</strong></div>
      </header>
      <section className="game-hero">
        <div>
          <p className="game-kicker"><Crosshair size={14} /> LIVE PUMP PREDICTION / PAPER MODE</p>
          <h1>WILL THEY<br /><span>JEET?</span></h1>
          <p className="game-intro">A Pump coin bonds. You call the time. The founding wallet eventually jeets. Your points follow the accuracy of your call.</p>
        </div>
        <aside className="game-warning"><Zap size={18} /><div><strong>FREE-TO-ENTER / PAPER MODE</strong><span>Non-cash points. 25 points at risk. No SOL. No NFTs. No wallet connection. No chain calls.</span></div></aside>
      </section>
      <section className="game-rules" aria-labelledby="rules-heading">
        <div><p className="game-kicker">HOW TO PLAY</p><h2 id="rules-heading">THE RULES</h2></div>
        <ol>
          <li><strong>ROUND STARTS</strong><span>A Pump.fun coin reaches bonding completion. That coin becomes the live round.</span></li>
          <li><strong>MAKE ONE CALL</strong><span>Choose when the founding wallet will sell: minutes, hours, or days.</span></li>
          <li><strong>LOCK 25 POINTS</strong><span>One prediction per round. Your 25-point stake is committed; a correct bucket returns it, and a wrong bucket loses it.</span></li>
          <li><strong>ROUND ENDS</strong><span>A confirmed founding-wallet sell settles the round and starts the next bonded coin.</span></li>
          <li><strong>DAILY RE-UP</strong><span>Each UTC day, any balance below 100 tops up to 100. Balances above 100 stay intact.</span></li>
          <li><strong>WEEKLY PRIZE</strong><span>The highest point total at the announced weekly cutoff is reviewed for the paper-mode prize, subject to the posted contest terms.</span></li>
        </ol>
        <p className="game-rules-note">Free to enter · paper points only · no cash value · no SOL or NFT wagering · no wallet connection.</p>
      </section>
      <section className="game-card">
        {liveRound ? <>
        <div aria-live="polite" aria-atomic="true">
          {liveRound && <div className="game-warning"><Zap size={18} /><div><strong>COIN BONDED — ROUND STARTED</strong><span>{liveRound.mint.slice(0, 6)}…{liveRound.mint.slice(-4)} · {new Date(liveRound.completed_at * 1000).toLocaleString()} · Pick your time below.</span></div></div>}
          {endedRound && <div className="game-warning"><Skull size={18} /><div><strong>FOUNDER JEETED — ROUND OVER</strong><span>Last ended round: {endedRound.mint.slice(0, 6)}…{endedRound.mint.slice(-4)} · {new Date(endedRound.jeet_at * 1000).toLocaleString()}</span><a href={`https://solscan.io/tx/${encodeURIComponent(endedRound.jeet_signature)}`} target="_blank" rel="noreferrer">View sell transaction</a></div></div>}
        </div>
        <div className="game-card-head"><div><p className="game-kicker">LIVE PUMP ROUND / BONDED</p><h2>{liveRound ? `${liveRound.mint.slice(0, 6)}…${liveRound.mint.slice(-4)}` : current.title}</h2></div><span className="game-streak"><Trophy size={15} /> STREAK {streak}</span></div>
        <p className="game-clue">{liveRound ? `Founding wallet: ${liveRound.creator.slice(0, 6)}…${liveRound.creator.slice(-4)}. Confirmed bonding completion is the round start.` : current.clue}</p>
        {!locked ? <>
          <p className="game-question">{liveRound ? "When will the founding wallet jeet?" : "So… will they jeet?"}</p>
          {liveRound ? <div className="game-picks">
            {[["under-5m", "UNDER 5 MIN"], ["5-30m", "5–30 MIN"], ["30-60m", "30–60 MIN"], ["1-6h", "1–6 HOURS"], ["6-24h", "6–24 HOURS"], ["1-3d", "1–3 DAYS"], ["3-7d", "3–7 DAYS"]].map(([value, label]) => <button key={value} className={bucket === value ? "picked jeet-pick" : "jeet-pick"} onClick={() => setBucket(value as Bucket)}><strong>{label}</strong><span>25 points at risk</span></button>)}
          </div> : <div className="game-picks">
            <button className={pick === "jeet" ? "picked jeet-pick" : "jeet-pick"} onClick={() => setPick("jeet")}><strong>JEET</strong><span>Clean exit. No more bag.</span></button>
            <button className={pick === "hold" ? "picked hold-pick" : "hold-pick"} onClick={() => setPick("hold")}><strong>STILL HOLDING</strong><span>Related wallet. Hidden ammo.</span></button>
          </div>}
          {saveError && <p role="alert">{saveError}</p>}
          <button className="game-lock" disabled={saving || (liveRound ? !bucket || points < stake : !pick)} onClick={lockPick}>{saving ? "SAVING PICK…" : "LOCK THE PICK"} <Zap size={15} /></button>
        </> : <div className={`game-reveal ${liveRound ? "correct" : pick === current.correct ? "correct" : "wrong"}`}><p className="game-kicker">{liveRound ? "PREDICTION LOCKED" : pick === current.correct ? "CORRECT CALL" : "BAD READ"}</p><h3>{liveRound ? "Your timing call is on the board." : current.reveal}</h3><p>{liveRound ? serverPrediction?.result ? `${serverPrediction.result}: ${serverPrediction.delta ?? 0} points.` : "The round settles when the founding wallet sells." : pick === current.correct ? `+${stake} fake points. Keep the streak alive.` : `-${stake} fake points. The chain is messy; that is the point.`}</p>{!liveRound && <button className="game-lock" onClick={nextRound}>NEXT CASE <ArrowLeft size={15} /></button>}</div>}
        {liveRound && locked && serverPrediction && <p role="status">Saved: {serverPrediction.bucket}. 25 points committed. Balance: {points} points.</p>}
        </> : <div className="game-waiting" role="status"><p className="game-kicker">WAITING FOR THE NEXT BONDED COIN</p><h2>No live round yet.</h2><p>The game will open automatically when a confirmed Pump bonding-completion event is detected.</p></div>}
      </section>
      <footer className="game-footer"><span>JEET ANALYZER / PAPER MODE</span><span>SKILL CONTEST · DAILY RESET: BALANCES UNDER 100 TOP UP TO 100.</span></footer>
    </main>
  );
}
