# Public case export

`scripts/build_public_case.py` is an offline presentation boundary for judge-facing case studies. It reads an existing internal `cluster-audit` receipt, replays seed-wallet accounting from persisted transaction autopsies with zero provider calls, and writes:

- an internal replay receipt retaining real evidence identifiers;
- a strict allowlisted public JSON case using `Wallet A`, `Wallet B`, and `TX-001` aliases;
- an alias-only Markdown case study.

The public export omits token name, symbol, mint, metadata URI, artwork/social identity, raw wallet addresses, raw signatures, token-account/program identifiers, explorer links, and internal receipt paths. Redaction occurs only after offline classification and reconciliation. Public aliases are never used as graph, cache, deduplication, or accounting keys.

Example:

```cmd
.venv\Scripts\python scripts\build_public_case.py --input receipts\internal\cluster-audit.json --replay-output receipts\internal\offline-replay.json --json-output receipts\public\case.json --markdown-output receipts\public\case-study.md --estimated-provider-credits 500 --credit-ceiling 900
```

Estimated credits are labeled estimates. The exporter makes no network request and always emits `COMMON_CONTROL: NOT_PROVEN`.
