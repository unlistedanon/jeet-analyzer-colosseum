# Evidence model

Jeet Analyzer separates observable chain facts from control claims.

`DIRECT_LINK != SAME_OWNER`

A direct asset or SOL transfer proves an on-chain interaction, not that both addresses are controlled by one person.

`DIRECT_SIGNED_SOL_FUNDING != SAME_OWNER`

A direct signed SOL-funding edge means the recorded source signed, paid the fee, and sent the recorded lamports directly to the destination. Its factual `relationship_type` remains `PLAIN_DIRECT_SOL_TRANSFER`. When the same source funded multiple recipients, `relationship_context` becomes `MULTI_RECIPIENT_FUNDER`; the context never replaces the underlying transfer relationship.

`SHARED_FUNDER != SAME_OWNER`

A shared historical funder is a separate recipient-to-recipient relationship: two or more distinct wallet-like recipients received direct signed SOL funding from the same third-party wallet. The third-party funder's direct edges remain `DIRECT_SIGNED_SOL_FUNDING`; they are never relabeled `SHARED_FUNDER`. A shared funder can still be an exchange, service, payroll source, faucet, or unrelated intermediary.

The derived shared-funder relationship is reported separately and does not replace or fabricate a transfer edge. Each supporting direct edge retains its own source, destination, amount, signature, and timestamp.

The recipient is not claimed to have signed the funding transaction. `recipient_independently_signed` only records separate historical signer evidence and is never presented as a receipt-transaction signature.

Unknown programs, address co-occurrence, matching balances, token-account ownership, pool/router participation, and fee/rent plumbing do not independently create wallet edges. Common control is always reported as `NOT_PROVEN`.

Accepted evidence types include:

- `DIRECT_TOKEN_TRANSFER`: exact parsed source/destination token accounts, resolved authorities, mint, amount, signature, and timestamp, outside reciprocal market context.
- `CONFIRMED_DIRECT_LINK`: a wallet signs and pays to create another wallet's ATA and transfers an asset into it in the same transaction.
- `PLAIN_DIRECT_SOL_TRANSFER` / `DIRECT_SIGNED_SOL_FUNDING`: parsed System Program transfer where the source signs and pays the fee, without reciprocal asset or router context. The evidence retains source, destination, amount, signature, and timestamp.
- `THIRD_PARTY_SHARED_FUNDER` / `SHARED_FUNDER`: a separate relationship between distinct recipient wallets supported by direct signed SOL-funding transfers from the same third-party funder. Its receipt identifies the funder, recipient pair, and every supporting transfer.
- `REPEATED_DIRECT_LINK`: repeated evidence-backed non-swap interaction between the same addresses.

`ATA_RENT`, `EXCLUDED_SWAP_INFRASTRUCTURE`, `PROGRAM_DEX_POOL`, token accounts, executable programs, PDAs, and unresolved program-controlled accounts never expand the wallet graph.

Parsed inner SPL Token or Token-2022 instructions outrank positional guesses from an outer program. A concrete transfer remains a transfer even when an unknown program is also present, but the unknown context is preserved and confidence may be reduced.

A wallet can be `VERIFIED_OUT` while its evidence-backed related-wallet cluster remains `NOT_OUT`.

Wallet and cluster terminal states are `VERIFIED_OUT`, `NOT_OUT`, `UNRESOLVED`, or `INSUFFICIENT_DATA`. A positive visible balance can prove `NOT_OUT` even when some history is incomplete; absence of visible inventory cannot prove an exit when coverage or graph branches are incomplete.
