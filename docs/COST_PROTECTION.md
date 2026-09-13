# Provider cost and credit protection

Each network-backed command creates one in-memory `InvestigationContext`. The context is shared by Helius history, DAS enrichment, ordinary Solana RPC, transaction classification, graph traversal, inventory reconciliation, and receipt generation. It is discarded when the command exits and never persists credentials.

For an explicit seeded `cluster-audit`, work is admitted in priority order: fresh seed inventory, bounded seed history and classification, direct seed relationships, breadth-first graph/funding expansion, then related fresh inventory. Every phase charges the same hard budgets. Once that evidence is exhausted, the command stops; it does not append a non-material mint-wide market pass. Use `scan` for market-wide seller context. A downstream graph boundary cannot erase a seed lifecycle already established from complete seed evidence.

## Hard budgets

Global controls must precede the subcommand:

```cmd
jeet-analyzer --max-estimated-provider-credits 60000 --max-rpc-requests 500 --max-signatures 10000 --max-transactions 5000 cluster-audit --mint MINT --wallet WALLET
```

- `--max-rpc-requests 500` limits actual RPC HTTP attempts, including retries. DAS calls are counted separately and are not mislabeled as RPC.
- `--max-signatures 10000` limits unique signatures admitted for examination.
- `--max-transactions 5000` limits unique full transaction records admitted.
- `--max-estimated-provider-credits 60000` is the default hard pre-request ceiling across RPC, DAS, and retries for one investigation. `60000` is a conservative default, not a product maximum; operators may deliberately configure a higher or lower positive ceiling.
- `--max-pages`, `--max-wallets`, and `--graph-depth` remain independent bounds.

The configured number is allowed. Attempting work beyond it emits `PROVIDER_BUDGET_EXHAUSTED`, preserves admitted evidence, stops deeper expansion, and marks coverage incomplete. A fresh material balance may still prove `NOT_OUT`. Incomplete coverage cannot produce `VERIFIED_OUT`; incomplete historical proof cannot produce `RE_ENTERED`.

Retries are charged before each actual retry request. A retry cannot bypass or receive a separate budget. The estimate currently weights `getTransactionsForAddress` at 50, `getProgramAccounts` and DAS methods at 10, standard allowlisted RPC at 1, and an unknown future method at a conservative 100. This model is configuration evidence, not authoritative provider billing.

## Safe investigation cache

Cached only within one command:

- finalized transaction results keyed by signature;
- identical finalized Helius address-history pages with fixed time bounds and pagination token;
- resolved mint account information, supply, token program, and DAS metadata;
- identical account records used for owner/type and deterministic infrastructure classification;
- parsed transaction-derived token-account authorities and normalized/autopsy results through signature reuse.

These values are immutable or safely reusable for the duration of one bounded investigation. Cached objects are copied on read and write so report code cannot mutate the canonical evidence.

Not cached:

- `getTokenAccountsByOwner` current wallet/token-account balances;
- largest-holder results;
- unbounded/current signature listings;
- current program-account scans or DAS token-account balance lists.

Fresh current inventory conclusions therefore perform fresh owner-token-account aggregation. A prior cache entry can never silently replace the required fresh balance read.

## Progressive graph expansion

Traversal is breadth-first: depth 1 is handled before depth 2, and depth 2 before depth 3. Within a depth, evidence is prioritized by target-token relevance, high confidence, sender-created ATA evidence, and observed transfer amount. Only wallet-like classified nodes enter the graph-wallet budget; infrastructure, pools, token accounts, routers, and unresolved program-controlled addresses do not.

No candidate is silently discarded merely to save requests. A depth, wallet, page, signature, transaction, or RPC budget boundary is preserved as unresolved/incomplete evidence.

## Request telemetry

Receipts expose `request_telemetry`:

```json
{
  "total_provider_requests": 0,
  "rpc_requests": 0,
  "das_requests": 0,
  "rpc_requests_by_method": {},
  "das_requests_by_method": {},
  "cache_hits": 0,
  "cache_misses": 0,
  "cache_hits_by_namespace": {},
  "cache_misses_by_namespace": {},
  "provider_requests_avoided": 0,
  "duplicate_evidence_observations": 0,
  "wallet_traversals_suppressed": 0,
  "deduplicated_skipped_requests": 0,
  "deduplicated_skipped_requests_deprecated": true,
  "retry_attempts": 0,
  "wallets_traversed": 0,
  "signatures_examined": 0,
  "transactions_fetched": 0,
  "pages_fetched": 0,
  "estimated_provider_credits": 0,
  "estimated_provider_credits_by_method": {},
  "estimated_provider_credit_ceiling": 60000,
  "estimated_provider_credit_cost_is_authoritative": false,
  "estimated_provider_credit_model": "method-weighted read-only request estimate; cache hits and deduplicated evidence cost zero; retries count as provider attempts",
  "budget_limits": {
    "max_rpc_requests": 500,
    "max_signatures": 10000,
    "max_transactions": 5000,
    "max_estimated_provider_credits": 60000
  },
  "terminated_by_budget": null,
  "termination_reason": null,
  "provider_credit_cost": null,
  "provider_credit_cost_note": "not reported because authoritative provider cost data was not supplied"
}
```

Request counters are factual HTTP attempts. Estimated credits are explicitly labeled estimates and never presented as authoritative cost. The hard ceiling is tested against the next method weight before the network request, so the recorded estimate cannot exceed it.

`provider_requests_avoided` increments only when a cache return eliminates an HTTP request that the current code path would otherwise issue. It can coincide with a cache hit, but not every cache hit avoids a request: a cached account inside a still-required batch does not count. `duplicate_evidence_observations` counts signatures encountered again after admission, and `wallet_traversals_suppressed` counts equal-or-shallower repeat traversal attempts; neither is reported as request savings.

`cache_hits_by_namespace` and `cache_misses_by_namespace` identify logical stores such as `transaction`, `history_page`, `account_record`, `account_request`, and `mint_metadata`.

`deduplicated_skipped_requests` remains temporarily for backward compatibility but is deprecated. It is a legacy aggregate of cache hits, duplicate evidence observations, and suppressed traversals; it must not be interpreted as provider requests avoided.
