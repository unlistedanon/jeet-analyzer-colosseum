# Bounded wallet priority update

Wallet scheduling continues to prefer the target mint, then high-confidence
evidence. Within those groups it prefers confirmed direct links, non-market
co-signing, account preparation, repeated direct links, and material SOL funding.
Raw amounts break ties only for the target mint; unrelated token base units are
not comparable. Signal ranks schedule work; they are not ownership probabilities.

Non-market co-signing remains an exact coordination edge and stays visible in
the graph. A co-signature edge by itself does not trigger another full
wallet-history crawl behind that edge. This reduces unrelated downstream
wallet expansion while preserving the evidence and fail-closed status.

The next wallet is selected with a linear minimum instead of sorting the entire
queue on every hop. No new history requests, caches, retained transaction copies,
wallet/depth limits, or provider budgets were introduced. Different scheduling
can still select wallets with different real-world history sizes and latency.
Existing admission, coverage, and conclusion rules remain in force.

Offline comparison against ec2005c on 2026-09-05 used the same three-wallet
synthetic audit, five batches of 100 runs per version, and a separate tracemalloc
sample. Median time per audit: 7.158 ms before, 7.165 ms after. Peak traced Python
allocations: 369882 bytes before, 369476 after. Both made three wallet activity
calls and returned the same fixture conclusion. This is a small CPU/allocation
regression check, not a measurement of live RPC latency or process RSS.

Regression coverage includes unrelated-token amount invariance, target-token
priority, repeated/preparation evidence priority, and a two-wallet cap that admits
repeated evidence ahead of a large unrelated transfer while retaining UNRESOLVED.

## Delegated token authority

For a successful non-market SPL Token or Token-2022 transfer, Hunt Mode now
also considers an external parsed `transfer.authority` when it differs from
the source token-account owner. The relationship is accepted only when the
source owner and authority both classify as wallet-like and the authority is
an observed signer. The receipt preserves the source account, mint, amount,
signers, signature, timestamp, and a `why_linked` explanation that the
authority signed to move tokens from the source owner's account.

Market/router transactions remain excluded. Program, PDA, multisig,
infrastructure, token-account, and unresolved authorities remain excluded or
unresolved and are never expanded as wallet nodes. Target-mint delegated
relationships may deepen under the existing target-token traversal rule;
unrelated-mint relationships do not create a new deep-crawl rule. This edge
does not contribute to `LIKELY_COMMON_CONTROL`; `COMMON_CONTROL` remains
`NOT_PROVEN`, and one transaction is not multiple independent signals.
