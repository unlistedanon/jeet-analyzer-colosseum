# Jeet Analyzer — Eternal Week 1

## What is Jeet Analyzer?

Jeet Analyzer is a read-only Solana forensic tool built around a deceptively simple question:

> A wallet sold, but is it really out?

It reconstructs token inventory and lifecycle evidence to distinguish true exit, retained inventory, transfers, re-entry, and unresolved evidence. Coverage and uncertainty remain explicit so missing evidence never becomes false certainty.

## Quick demo

From the repository root:

```cmd
python scripts\demo_flagship.py
```

The command loads the alias-only evidence fixture, reconstructs the result, writes a schema-compatible JSON receipt, and prepares the local frontend demo. It needs no API key, RPC URL, wallet, funds, or network connection.

Expected conclusion:

```text
HISTORICAL_EXIT_STATUS: VERIFIED_OUT
CURRENT_WALLET_STATUS: RE_ENTERED
CLUSTER_STATUS: NOT_OUT
COMMON_CONTROL: NOT_PROVEN
OFFLINE_REPLAY_PROVIDER_CALLS: 0
```

These statuses are calculated from the fixture at runtime; they are not stored as expected verdicts in the evidence file.

## Visual demo

Run the offline demo first, then:

```cmd
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173/?demo=flagship`. The page reads only the locally generated alias-only result. The API backend is not required.

## Automated Week 1 video

Generate the narrated, captioned 1920×1080 presentation without manual scrolling or desktop recording:

```cmd
.venv\Scripts\python scripts\render_week1_video.py
```

The self-running browser route is `http://127.0.0.1:5173/?demo=week1-video`. Generated WAV, page-only frames, quality report, and MP4 remain under the ignored `build/eternal-week1/video/` directory. The standard subtitle track is committed as `competition/eternal-week1/week1-video.srt`.

## Tests

```cmd
python -m unittest discover -s tests -v
cd frontend
npm test -- --run
npm run build
```

## Evidence honesty

- Token and transaction identities are redacted or aliased.
- Wallet A's indexed history is complete under the recorded provider contract.
- Downstream relationship expansion is incomplete because the original run stopped at its hard transaction budget.
- Fresh visible inventory safely proves `NOT_OUT`; incomplete downstream coverage is not treated as zero.
- Relationships remain evidence, not ownership. `COMMON_CONTROL` stays `NOT_PROVEN`.
- The original live investigation used 520 estimated Helius credits. This replay uses zero provider calls.

See `competition/eternal-week1/WEEK1_STORY.md`, `competition/eternal-week1/VIDEO_PLAN.md`, `docs/EVIDENCE_MODEL.md`, and `schemas/jeet-analyzer-result-v1.schema.json`.
