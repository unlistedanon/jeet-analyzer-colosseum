# Jeet Analyzer

**The jeet sold—but are they really out? A sell does not necessarily mean an exit.**

In the trenches, a sell alert or an empty wallet can look like the whole story.
Tokens may still sit in related wallets, move through settlement infrastructure, or
be reacquired later. Jeet Analyzer investigates the observed Solana evidence and
separates a historical exit, current holdings, and bounded related-wallet exposure.
It attaches the receipts and missing coverage to the answer.

## Follow the evidence

1. Supply a token mint; discover observed sellers with `scan`, or investigate a mint
   and wallet pair with `cluster-audit`.
2. Reconstruct token movements, market activity and owner-level inventory from
   transaction evidence; distinguish direct transfers from swaps and infrastructure.
3. Check fresh balances and bounded relationships before reporting wallet and cluster
   status. A root wallet can be `VERIFIED_OUT` while its related cluster is `NOT_OUT`.
4. Keep uncertainty visible. Missing required history stays `INSUFFICIENT_DATA` or
   `UNRESOLVED`. A relationship does not establish identity: `COMMON_CONTROL=NOT_PROVEN`.

The core integrates Solana RPC and optional Helius indexed history, SPL Token and
Token-2022 account parsing, signatures, instructions, balances and transaction
metadata. Provider calls have shared request, pagination, transaction and estimated
credit limits. The backend—not the browser or Telegram—owns collection and conclusions.
Investigations do not sign, trade or submit transactions.

## Sprint / submission scope

This candidate combines the preserved read-only CLI baseline with the sprint's
bounded relationship and lifecycle investigation, evidence-first React/FastAPI UI,
durable jobs and budget controls, anonymized offline case replay, and Telegram field
interface. The SOL membership flow reuses one invoice verifier and entitlement ledger;
only a qualifying finalized payment can grant membership. Telegram adds numeric-ID
account linking, reply-to-message commands, async updates and a separately disableable
SOL surface. No duplicate forensic engine or payment ledger is introduced.

The submission is an evaluable source snapshot, not proof of live service readiness.
Private operational history and production deployment/tunnel controls are intentionally
excluded. See [reviewer guide](REVIEWER_START_HERE.md), [evidence model](docs/EVIDENCE_MODEL.md)
and [Telegram status](docs/TELEGRAM_READINESS.md).

## Install and replay offline

Python 3.10+ and Node.js 22 are used by the project. From the repository root:

```sh
python -m venv .venv
# Activate .venv for your shell before the remaining commands.
python -m pip install -e ".[ui,test,telegram]"
python scripts/demo_flagship.py
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173/?demo=flagship`. The replay uses committed alias-only
fixtures and generated local demo data. It needs no provider key, wallet, payment
or running backend. Expected result:

```text
HISTORICAL_EXIT_STATUS: VERIFIED_OUT
CURRENT_WALLET_STATUS: RE_ENTERED
CLUSTER_STATUS: NOT_OUT
COMMON_CONTROL: NOT_PROVEN
OFFLINE_REPLAY_PROVIDER_CALLS: 0
```

`.env.example` deliberately contains blank values only. It is a configuration
inventory, not a ready-to-run operational environment. Configure required values
privately before any network-backed use; never put provider credentials in the frontend.

## Test and build

From the repository root with dependencies installed:

```sh
python -m pytest -q
cd frontend
npm test
npm run build
```

`npm run build` includes the defined TypeScript check (`tsc -b`) and Vite production
build. There is no separate frontend lint script. The existing CI also defines Python
Bandit checks:

```sh
python -m pip install bandit
python -m bandit -r jeet_analyzer jeet_analyzer_api -ll -q -s B608
python -m bandit scripts/generate_beta_codes.py scripts/build_external_review.py -ll -q
```

The fixture suites simulate providers and payments; their success is not evidence of
a real payment or live Telegram delivery. CI is retained in `.github/workflows/ci.yml`.

## Current limitations

- History and graph expansion are bounded. A completed job is not proof of exhaustive
  coverage, human ownership, transaction intent, or token safety.
- Provider-credit accounting is an estimate, not an authoritative bill.
- Telegram is **production-off and only mock-rehearsed**. A lone address may return
  unknown and request mint/wallet context; generic fund-flow and claim analysis are
  not implemented. Full-report links currently open authorized JSON.
- Real Telegram delivery and real SOL membership activation have not been validated.
  Pending or unconfirmed payments never grant paid entitlement.
- The shared SQLite worker model is single-process. Private hosting and tunnel
  scripts are excluded; this repository does not configure a production service.
- The SOL-only Telegram product decision carries acknowledged platform-policy risk.
  Payment controls can be disabled independently of existing access and investigations.

See [commands](docs/COMMANDS.md), [cost protection](docs/COST_PROTECTION.md),
[beta operations](docs/BETA_OPERATIONS.md) and [Telegram operations](docs/TELEGRAM.md).

## License

**Evaluation-only / proprietary. All rights reserved.** Public availability does not
provide an open-source license or permission to copy, modify, distribute, sell or use
the software beyond the copyright holder's written permission. The existing
[LICENSE](LICENSE) is unchanged.
