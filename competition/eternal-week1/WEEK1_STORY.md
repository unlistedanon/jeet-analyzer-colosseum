# Eternal Week 1 story

Jeet Analyzer began with one question:

> The jeet sold, but is he really out?

This week we tested that question against real Solana history. The first real-world corpus contained 4,937 unique transactions and 9,408 normalized events. It exposed a mixed mint/market-buy classifier defect: a real market purchase inside a transaction that also minted an asset could be mislabeled. The fix was generic, and the corpus became a heavyweight offline regression fixture.

The alias-only flagship investigation exposed a second generic defect. Market settlement delivery legs could be counted again alongside confirmed buy owner deltas. That could distort reconstructed inventory. The correction now requires an exact signature, destination owner, and aggregate amount match before a settlement delivery is excluded from transfer accounting. Unmatched direct transfers remain transfers.

The flagship result is the point of the product:

- Wallet A historically reached a defensible zero balance: `VERIFIED_OUT`.
- Later, eleven confirmed buys rebuilt a 5,974,478.422911-token position: `RE_ENTERED`.
- Fresh visible inventory makes the bounded cluster `NOT_OUT`.
- No genuine target-token transfer or secondary-wallet target-token increase was proven.
- Relationship evidence does not prove ownership: `COMMON_CONTROL=NOT_PROVEN`.

The old answer was not wrong. It became stale because the chain changed. Jeet detected that change.

Week 1 closes with 155/155 Python tests and 14/14 frontend tests passing in the packaged checkpoint. Provider use was bounded to 520 estimated Helius credits, an intentionally tiny-budget run failed closed, and the deterministic public replay makes zero provider calls. The demo is anonymized and does not claim complete Solana-wide coverage.
