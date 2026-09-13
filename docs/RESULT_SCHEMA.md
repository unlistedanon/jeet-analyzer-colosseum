# Machine-readable result contract

`cluster-audit` emits `jeet-analyzer.result.v1` (`schema_version: 1.0.0`). The normative JSON Schema is [`schemas/jeet-analyzer-result-v1.schema.json`](../schemas/jeet-analyzer-result-v1.schema.json).

The contract keeps observed facts separate from conclusions:

- `token`, `mint`, and `seed_wallet` identify the runtime target.
- `historical_exit_status` records whether a defensible prior exit was observed.
- `current_wallet_status` may be `RE_ENTERED` only after that verified historical exit and later material confirmed inventory restoration.
- `cluster_status` is a separate conclusion over the bounded evidence graph.
- `current_inventory`, `sales`, `buys_reacquisitions`, and `proceeds` contain accounting observations. Cluster receipts keep numeric observed-sale totals for compatibility, while `sales.amount_known`, `sales.count_known`, and `sales.coverage_complete` distinguish a proven zero from a partial observed minimum. Human output renders incomplete seed-sale coverage as `UNKNOWN`.
- `wallet_relationships`, `shared_funders`, and `excluded_infrastructure` preserve graph evidence without asserting ownership. Direct signed SOL-funding edges remain `DIRECT_SIGNED_SOL_FUNDING`; `SHARED_FUNDER` is reserved for recipient-to-recipient relationships through a separately identified third-party funder, with supporting transfers retained.
- `unresolved_evidence` and `provider_coverage` make limitations machine-readable.
- `progress.phases` holds the latest state while `progress.events` is an ordered event log. Counts are optional; percentages are never fabricated when total work is unknown.
- `evidence_receipts` names the JSON result and bounded evidence JSONL.
- `request_telemetry` records factual RPC/DAS attempts, per-namespace cache behavior, actual provider requests avoided, duplicate evidence observations, suppressed wallet traversals, retries, admitted signatures/transactions, fetched pages, hard limits, a non-authoritative method-weighted credit estimate, and any terminating budget. The legacy `deduplicated_skipped_requests` aggregate is deprecated and must not be treated as request savings.
- `common_control` is fixed at `NOT_PROVEN` in v1.

Provider request records contain `retry_count`, `terminal_failure_reason`, and `failure_category`. Credential-bearing URLs and provider messages are redacted. A provider failure result retains the complete schema while setting the wallet and cluster conclusions to `INSUFFICIENT_DATA`.

For seeded cluster audits, `provider_coverage.seed_wallet_complete` records whether fresh seed inventory plus complete seed history established the wallet lifecycle. `provider_coverage.downstream_complete` separately records relationship, related-inventory, and optional mint-enrichment coverage. A defensible seed-wallet status is not overwritten when only downstream work becomes incomplete; the cluster conclusion still fails closed.

`provider_coverage.optional_mint_enrichment` records that supplemental mint-wide history was not required after seeded lifecycle, graph, and inventory work. It is skipped either because fresh material inventory already proves `NOT_OUT`, or because a later market scan cannot change the seeded cluster conclusion. This does not claim exhaustive graph coverage: required graph/history omissions remain incomplete and appear in `unresolved_evidence`; `provider_coverage.termination_reason` and request telemetry identify budget termination separately. Use `scan` for market-wide seller context.
