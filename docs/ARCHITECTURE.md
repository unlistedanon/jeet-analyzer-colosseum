# Architecture

Jeet Analyzer uses one forensic engine across CLI, API, offline replay, and the invite-only beta. The UI does not implement an independent blockchain interpretation layer.

## Data flow

```text
runtime mint / optional seed wallet
              |
              v
+----------------------------------+
| read-only provider boundary      |
| Helius indexed history / DAS     |
| Solana JSON-RPC fresh state      |
+----------------------------------+
              |
              v
+----------------------------------+
| InvestigationContext             |
| request + credit ceilings        |
| retry accounting                 |
| immutable evidence cache         |
| signature/transaction dedupe     |
+----------------------------------+
              |
              v
+----------------------------------+
| normalized transaction evidence  |
| SPL + Token-2022 owner flows      |
| signer / fee-payer context        |
+----------------------------------+
              |
              v
+----------------------------------+
| transaction classifier/autopsy   |
| BUY / SELL / TRANSFER / noise    |
| market evidence stays explicit   |
+----------------------------------+
              |
        +-----+------+
        |            |
        v            v
 wallet lifecycle   relationship graph
 inventory/proceeds evidence-backed edges
 exit/re-entry      infrastructure filtering
        |            |
        +-----+------+
              |
              v
 fresh owner-level target inventory
              |
              v
 result contract + coverage + receipts
              |
       +------+------+
       |             |
      CLI        FastAPI / React
```

## One truth layer

The Python forensic engine owns classification, lifecycle, accounting, relationship evidence, and coverage semantics. The API invokes that engine and transports its receipts. The frontend renders those receipts.

This prevents a UI convenience rule from silently becoming a second forensic implementation.

## Provider boundary

Provider access is read-only and explicit. The engine uses indexed historical acquisition where needed and fresh Solana RPC reads for current inventory. Credentials live only in runtime environment variables and are never part of a result receipt.

Every network-backed run shares one `InvestigationContext`. It enforces configured ceilings before provider attempts, including retries, while keeping factual request counts separate from non-authoritative estimated-credit accounting.

Safe immutable/deterministic evidence may be reused inside one investigation. Fresh current balance reads are intentionally not replaced by stale cache entries.

## Classification boundary

A token decrease does not become a `SELL` merely because native SOL also increased. Market classification requires explicit market evidence under the transaction classifier's rules. Exact non-market token movement remains transfer evidence.

This boundary matters because a false `SELL` can cascade into a false historical exit.

## Lifecycle boundary

The engine separates:

1. historical exit status;
2. current wallet lifecycle status;
3. related-wallet cluster status.

A wallet can therefore be historically `VERIFIED_OUT`, later `RE_ENTERED`, while the current related-wallet cluster is `NOT_OUT`.

A `RE_ENTERED` conclusion requires a defensible earlier exit plus later material reacquisition evidence. A current nonzero balance alone is `NOT_OUT`, not proof of re-entry.

## Relationship boundary

The graph expands only on accepted evidence types and filters pools, routers, token accounts, PDAs, ATA rent, reciprocal swaps, escrows, and unresolved program-controlled infrastructure from wallet expansion.

The graph answers **what is evidence-linked and why**. It does not silently answer **who owns what**. Common control remains `NOT_PROVEN`.

See `docs/EVIDENCE_MODEL.md` for the exact relationship semantics.

## Coverage boundary

The permanent safety invariant is:

> **Incomplete required coverage may weaken a conclusion, but it may never strengthen one.**

Examples:

- fresh material target inventory can directly establish `NOT_OUT`;
- incomplete required historical evidence cannot establish `VERIFIED_OUT`;
- incomplete historical proof cannot establish `RE_ENTERED`;
- exhausted graph traversal is reported as a boundary, not interpreted as absence of linked wallets.

## Offline replay

The committed flagship evidence is sanitized/aliased but retains the evidence structure needed to exercise the real lifecycle/report path. `scripts/demo_flagship.py` reconstructs the result with zero provider calls.

This gives reviewers and CI a deterministic proof path without spending provider credits or exposing case identifiers.

## Hosted beta boundary

The hosted beta wraps the same engine with server-owned runtime limits, invite authentication, durable job/feedback state, concurrency/daily admission, safe report export, and a fail-closed kill switch. Beta clients submit only the target mint and seller wallet; they do not receive provider credentials or control server-owned beta limits.
