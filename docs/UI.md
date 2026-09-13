# UI architecture and API

Jeet Analyzer UI v1 is a local read-only presentation and transport layer around the existing Python forensic engine.

```text
React / Vite / TypeScript
          |
FastAPI validation and investigation registry
          |
InProcessCliEngineAdapter
          |
existing jeet_analyzer Python engine and receipts
          |
read-only Helius / Solana RPC
```

## Engine adapter boundary

The engine currently exposes stable command handlers through `jeet_analyzer.cli.main`, while those handlers own receipt creation and some orchestration remains private to `jeet_analyzer.analyzer`. The API therefore calls that Python entry point **in-process**, not through a shell or subprocess. This preserves one source of forensic truth and current CLI behavior.

The call is isolated behind `EngineAdapter`. A process-wide lock serializes access to the current CLI entry point so investigations cannot collide in process-wide CLI state. Each investigation receives a unique receipt directory. The adapter reads the engine's JSON/JSONL/CSV artifacts; native cluster receipts are validated against `schemas/jeet-analyzer-result-v1.schema.json` before they reach the UI.

The adapter does not reinterpret cluster conclusions. A CLI exit code indicating incomplete coverage remains an investigation result if the engine emitted a valid fail-closed receipt. Only missing or malformed artifacts become API adapter errors.

`wallet` currently emits normalized JSONL rather than the full cluster result contract. The adapter constructs a presentation-only `jeet-analyzer.wallet-ui.v1` transport from that metadata and its normalized events. Historical exit status, current wallet status, balances, sales, buys, and transfer evidence are copied from the engine receipt; they are not recalculated into a different forensic conclusion.

## API routes

- `GET /api/v1/health`
- `POST /api/v1/investigations/seller-scan`
- `POST /api/v1/investigations/wallet-audit`
- `POST /api/v1/investigations/cluster-audit`
- `GET /api/v1/investigations/{id}`
- `GET /api/v1/investigations/{id}/events`
- `GET /api/v1/investigations/{id}/receipt`
- `GET /api/v1/investigations/{id}/artifacts/{name}`

The non-versioned `/api/health`, `/api/scan`, `/api/wallet`, and `/api/cluster-audit` routes are compatibility aliases. V1 has no account or authentication layer and binds to `127.0.0.1` by default.

Investigation states are `queued`, `running`, `complete`, and `error`. `complete` means the engine produced a usable receipt; it does **not** mean provider coverage is complete. The UI separately renders `provider_coverage.complete`, lifecycle states, budget termination, and unresolved evidence.

When the engine establishes the seed-wallet lifecycle before a later graph/enrichment budget boundary, the UI preserves the wallet result and independently renders `CLUSTER STATUS: INSUFFICIENT_DATA` with an incomplete-coverage explanation. It never flattens the two conclusions or displays incomplete seed-sale coverage as a proven zero.

The v1 investigation registry is process-local. Restarting the API clears its status index, although already-written receipt files remain on disk. Durable multi-user job storage and authentication are deliberately outside this local v1 scope.

## Honest progress

The API reports the initial engine start without inventing percentages. Native cluster progress is replaced by the engine's own phase/event receipt at completion. Scan and wallet transports mark only phases known to have finished after their artifacts exist. Unknown work remains pending.

## Credential and receipt handling

- The frontend has no Helius or RPC configuration.
- The backend reads `SOLANA_RPC_URL` and `HELIUS_API_KEY` from its process environment.
- CORS defaults to the two local Vite origins and permits only `GET` and `POST`.
- API errors pass through the engine's URL/credential redaction boundary.
- Receipts are written below `receipts/ui-investigations` by default and remain ignored by Git.
- No endpoint constructs, signs, or submits a transaction.

## Test fixtures

Frontend demo fixtures live only under `frontend/src/test`. They are deterministic synthetic presentation inputs and are never loaded by the production application. Automated API tests inject a fake `EngineAdapter`, so test runs make no Helius, Solana RPC, or wallet request.
