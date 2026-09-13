# Commands

All commands accept a target mint at runtime. No symbol, decimal count, token program, holder address, market route, or pool is fixed in production logic.

```cmd
jeet-analyzer inspect --mint MINT --top 25
jeet-analyzer scan --mint MINT --days 14 --top 25
jeet-analyzer wallet --mint MINT --wallet PUBLIC_ADDRESS --days 14
jeet-analyzer verify-exit --mint MINT --wallet PUBLIC_ADDRESS --days 14 --trace-depth 3
jeet-analyzer cluster-audit --mint MINT --wallet PUBLIC_ADDRESS --days 14 --graph-depth 3 --max-wallets 50 --max-pages 1000
jeet-analyzer watch --mint MINT --wallet PUBLIC_ADDRESS
```

Optional global provider controls are `--request-timeout SECONDS`, `--provider-retries N`, and `--provider-backoff-cap SECONDS`; place them before the subcommand. `--timeout-seconds` remains a backward-compatible alias for `--request-timeout`.

Cost controls are also global and precede the subcommand: `--max-estimated-provider-credits N` (default `60000`; operator-configurable), `--max-rpc-requests N` (default `500`), `--max-signatures N` (default `10000`), and `--max-transactions N` (default `5000`). All must be positive. Retries count against both request and estimated-credit budgets.

Wallet lifecycle output separates `HISTORICAL_EXIT_STATUS` from `CURRENT_WALLET_STATUS`. Current inventory alone is never called a re-entry. `RE_ENTERED` requires complete historical coverage, a defensible observed exit to the configured materiality boundary, and a later material confirmed buy, exact incoming transfer, or target-token owner-balance reacquisition that restores the seed wallet. Linked-wallet balances never enter seed-wallet arithmetic.

`scan` is token-first: it discovers and ranks recent confirmed sellers without requiring a wallet. Each seller row includes observed-volume share, target-token amount sold, defensible quote/native proceeds, fresh balance, and a generic wallet status.

`cluster-audit` performs bounded cross-asset relationship discovery. An evidence edge may involve the target token, SOL, WSOL, a quote token, or an unrelated fungible token. Pools, routers, token accounts, ATA rent, reciprocal swaps, escrows, and unresolved program-controlled addresses are excluded from wallet expansion.

The future `investigate --mint MINT` orchestration is intentionally not exposed until automatic seller selection and per-seller provider budgets can be validated end-to-end. It will compose `scan`, wallet verification, and `cluster-audit` rather than introduce another parser.
