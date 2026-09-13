# Eternal Week 1 video plan

Target length: 95–110 seconds. All wallet and transaction identities remain aliases.

## Automated production

Run from the repository root:

```cmd
.venv\Scripts\python scripts\render_week1_video.py
```

This generates synchronized local narration, subtitles, page-only 1920×1080 frames, and `build/eternal-week1/video/jeet-analyzer-week1.mp4`. The fixed self-running route is `?demo=week1-video`; no manual scrolling or browser chrome capture is used.

## Narrator script

**0–10 seconds**
“A wallet sells its tokens. Most dashboards stop there. Jeet Analyzer asks one more question: did the wallet actually leave?”

**10–30 seconds**
“In this real, anonymized Solana case, Wallet A sold 7,666,267.453718 tokens. The evidence shows it reached a defensible zero balance. Historically, that is a verified exit.”

**30–50 seconds**
“But Jeet follows the lifecycle beyond the sale. It checks fresh inventory, genuine transfers, downstream wallets, later buys, burns, and the coverage behind every conclusion.”

**50–70 seconds**
“Eleven confirmed post-exit buys rebuilt a 5,974,478.422911-token position. Wallet A is not still out. Its current status is re-entered, and the visible cluster is not out.”

**70–90 seconds**
“The report stays honest. Zero genuine target-token transfers were proven. Zero secondary-wallet target-token increases were proven. Downstream coverage is incomplete, and common control is not proven.”

**90–105 seconds**
“The original investigation was capped at 520 estimated Helius credits. This judge demo reconstructs the anonymized result locally with zero provider calls. The test suite guards the two real defects the evidence exposed.”

**Closing**
“SELL does not necessarily mean EXIT. Jeet Analyzer checks what happened next.”

## Screen sequence and overlays

| Time | Screen | Exact overlay |
|---|---|---|
| 0–10 | Jeet Analyzer masthead and question | `THE JEET SOLD — BUT IS HE REALLY OUT?` |
| 10–20 | Flagship summary, sold amount | `WALLET A SOLD 7,666,267.453718` |
| 20–30 | Historical status card | `HISTORICAL EXIT: VERIFIED_OUT` |
| 30–40 | Evidence timeline | `FOLLOW THE LIFECYCLE, NOT JUST THE SALE` |
| 40–50 | Transfer and related-wallet evidence | `TRANSFERS · RECIPIENTS · REACQUISITION · COVERAGE` |
| 50–60 | Current wallet status card | `CURRENT STATUS: RE_ENTERED` |
| 60–70 | Current inventory and cluster card | `5,974,478.422911 CURRENT · CLUSTER NOT_OUT` |
| 70–80 | Evidence honesty row | `TARGET TRANSFERS: 0 · SECONDARY INCREASES: 0` |
| 80–90 | Coverage/common-control section | `DOWNSTREAM INCOMPLETE · COMMON_CONTROL NOT_PROVEN` |
| 90–100 | Terminal running the demo | `OFFLINE REPLAY · 0 PROVIDER CALLS` |
| 100–110 | Closing title | `SELL ≠ EXIT` |

## What must remain visible

- `[REDACTED TOKEN]` and `Wallet A`, never live identifiers.
- Historical and current lifecycle states as separate facts.
- Current inventory and confirmed sold amount.
- Seed coverage complete under the indexed-provider contract.
- Downstream graph coverage incomplete.
- Original cost labeled estimated; offline provider calls shown as zero.
- `COMMON_CONTROL: NOT_PROVEN` beside relationship evidence.
