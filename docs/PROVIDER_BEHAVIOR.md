# Provider behavior

Historical scans currently use the metered Helius API when separately enabled with `HELIUS_API_KEY`. Fresh balances use `SOLANA_RPC_URL`, falling back to the public Solana mainnet RPC.

Every provider result records scope, page count, coverage completeness, and limitations. Pagination repetition, page caps, rate-limit exhaustion, malformed results, or missing fresh balances fail closed. Strong exit conclusions are not issued from incomplete coverage.

Global bounded resilience controls must precede the subcommand:

```cmd
jeet-analyzer --request-timeout 30 --provider-retries 2 --provider-backoff-cap 8 cluster-audit --mint MINT --wallet WALLET
```

`--request-timeout` is the per-attempt read timeout. `--provider-retries` is the number of attempts after the initial request (0 through 10). Exponential backoff and provider `Retry-After` values are capped by `--provider-backoff-cap` (greater than 0 through 60 seconds). The legacy `--timeout-seconds` spelling remains an alias. Defaults are 30 seconds, 2 retries, and an 8-second cap. There are no infinite retries.

Failures are distinguished as `TIMEOUT`, `RATE_LIMIT`, `HTTP_ERROR`, `MALFORMED_RESPONSE`, `TRANSPORT_ERROR`, or `COVERAGE_EXHAUSTION`. Receipts retain the bounded retry count and terminal safe reason. Provider failure can only weaken a conclusion; it can never produce `VERIFIED_OUT`.

Hard credit/request/signature/transaction budgets and investigation-scoped caching are documented in [Provider cost protection](COST_PROTECTION.md). `PROVIDER_BUDGET_EXHAUSTED` stops expansion without discarding evidence already admitted. Request receipts count RPC and DAS methods separately, distinguish actual provider requests avoided from local evidence/traversal deduplication, and label method-weighted credits as estimates rather than authoritative provider billing.

`scan` and `verify-exit` use mint/address history normalized through the shared classifier. `cluster-audit` additionally requests all address activity so native-only funding and cross-token transfers are observable. `--max-pages`, `--max-wallets`, and `--graph-depth` bound cost and graph growth; exhausting a required boundary becomes an explicit unresolved branch.

Fresh target-token balances aggregate every parsed token account owned by a wallet for the supplied mint. The mint account's controlling program is discovered dynamically, and both legacy SPL Token and Token-2022 instructions are supported without a fixed account-size filter.

All network clients expose explicit read-only method allowlists. Credentials and URL query parameters are redacted from errors and omitted from receipts.
