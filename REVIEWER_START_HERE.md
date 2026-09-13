# Jeet Analyzer: reviewer start here

Jeet Analyzer is a read-only Solana forensic system built around one deceptively simple question:

> **The jeet sold, but is he really out?**

A sell transaction is easy to observe. A defensible exit is harder. Tokens can move through settlement accounts, related wallets, later reacquisition, cross-asset funding paths, routers, pools, and other infrastructure that makes a single-wallet snapshot misleading. Jeet Analyzer reconstructs the evidence chain and returns bounded lifecycle and cluster conclusions instead of pretending every transfer proves ownership.

## Five-minute review path

From the repository root:

```bash
python -m pip install -e ".[ui,test]"
python scripts/demo_flagship.py
python -m pytest -q
```

The committed flagship fixture is alias-only and runs with **zero provider calls**. Its expected high-level shape is:

```text
HISTORICAL_EXIT_STATUS: VERIFIED_OUT
CURRENT_WALLET_STATUS: RE_ENTERED
CLUSTER_STATUS: NOT_OUT
COMMON_CONTROL: NOT_PROVEN
OFFLINE_REPLAY_PROVIDER_CALLS: 0
```

Then validate the UI:

```bash
cd frontend
npm ci
npm test
npm run build
```

Or, after dependencies are installed, run the repository validator:

```bash
python scripts/validate_all.py
```

## Where the core work lives

- `jeet_analyzer/history.py`: indexed historical acquisition and normalized transaction evidence.
- `jeet_analyzer/tx_classifier.py`: transaction autopsy and market-vs-transfer classification.
- `jeet_analyzer/lifecycle.py`: historical exit and re-entry semantics.
- `jeet_analyzer/cluster.py`: bounded evidence-backed related-wallet investigation.
- `jeet_analyzer/investigation.py`: shared request budgets, retry accounting, caching, deduplication, and provider telemetry.
- `docs/EVIDENCE_MODEL.md`: the proof standard for relationships and common-control claims.
- `schemas/jeet-analyzer-result-v1.schema.json`: stable machine-readable result contract.
- `jeet_analyzer_api/` + `frontend/`: read-only API, invite-only beta boundary, and evidence-first UI.

See `docs/ARCHITECTURE.md` for the full data flow and `docs/ETERNAL_BUILD_LOG.md` for the sanitized snapshot provenance (private operational history is omitted).

## Evidence doctrine

Jeet Analyzer deliberately separates discovery from attribution:

- **Hunt wide, prove narrow.**
- A transaction-linked wallet is not automatically the same owner.
- `DIRECT_LINK != SAME_OWNER`.
- Incomplete required coverage may weaken a conclusion, but it may never strengthen one.
- A fresh material balance may prove `NOT_OUT`; missing history cannot prove `VERIFIED_OUT`.
- `COMMON_CONTROL` remains `NOT_PROVEN` unless future evidence explicitly establishes more.

That distinction is a product feature, not a disclaimer bolted on afterward.

## Solana integration

The analyzer reads public Solana state and indexed history through explicit read-only provider/RPC surfaces. It resolves SPL Token and Token-2022 assets at runtime, reconstructs owner-level token inventory, parses transaction evidence, classifies market activity versus direct transfers, and fresh-checks current balances before final status.

It does **not** create wallets, request seed phrases/private keys, construct transactions, sign, swap, trade, or submit transactions.

## Current validation checkpoint

Historical results below belong to the private pre-review checkpoint, not validation of this sanitized candidate. See the README for current commands. That earlier checkpoint recorded:

- 184 Python tests passing;
- 23 frontend tests passing;
- TypeScript/Vite production build passing;
- zero npm vulnerabilities reported by the local install;
- offline flagship replay passing with zero provider calls.

The repository CI repeats the core regression, replay, sanitized-package, frontend-test, and production-build path on every push/PR.

## Current limitations, stated on purpose

- Provider-credit telemetry is a conservative method-weighted estimate, not authoritative billing data.
- Investigation depth, wallet count, pages, signatures, transactions, requests, and estimated provider credits remain explicitly bounded by configured limits.
- Related-wallet discovery is evidence-backed and bounded; it does not promise to discover every wallet controlled by a person.
- Hosted beta access is invite-only and fail-closed by default.
- The product currently focuses on read-only investigation, not transaction execution.

## Product thesis in one line

**Jeet Analyzer turns Solana wallet complexity into a defensible answer, with the evidence and coverage boundary attached.**
