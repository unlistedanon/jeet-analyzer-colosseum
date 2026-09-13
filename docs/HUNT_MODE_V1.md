# Hunt Mode v1

Hunt Mode widens **discovery** without weakening **attribution**.

## Product question

After a seller appears to exit, what other evidence-linked wallets may still hold enough target-token inventory to matter?

The purpose is practical risk visibility. Jeet should surface wallets that are defensibly linked to the seller and explain exactly why they were discovered. A strong link may justify a later common-control assessment, but linkage itself is not silently promoted into identity.

## Doctrine

- **Hunt wide. Prove narrow.**
- If Jeet says two wallets are linked, the report carries the on-chain reason as `why_linked`.
- `DIRECTLY_LINKED` means an exact relationship was observed. It does not by itself prove the same human controls both wallets.
- `COORDINATED_BEHAVIOR` means an exact coordination signal was observed. Hunt Mode v1 does not automatically promote that signal to proven common control.
- `COMMON_CONTROL=NOT_PROVEN` remains the hard attribution boundary until an explicit evidence-stacking policy is implemented and validated.
- Market/router plumbing is not allowed to become a co-signer or ATA-preparation expansion shortcut.

## New discovery channels

### Non-market co-signers

When two wallet-like addresses sign the same transaction and the transaction is not classified as market/router context, Hunt Mode may create a `NON_MARKET_CO_SIGNER` relationship with classification `COORDINATED_BEHAVIOR`.

The exact signature, signers, fee payer, transaction context, and explanation remain attached to the edge.

### ATA payer to owner preparation

When one wallet signs a non-market transaction that creates an associated token account for another wallet owner, Hunt Mode may create an `ATA_PREPARATION` relationship with classification `DIRECTLY_LINKED`, even when no token transfer follows in that same transaction.

If the same transaction already establishes the stronger `CONFIRMED_DIRECT_LINK` through ATA creation plus a direct token transfer, the preparation-only edge is not duplicated.

### Deep traversal

The core cluster engine and regular API accept explicitly requested graph depth up to `6`. The ordinary/default graph depth remains `3` so deeper hunting is deliberate and remains bounded by wallet/request/signature/transaction/provider-credit ceilings.

## What v1 deliberately does not claim

Hunt Mode v1 does not yet emit a numeric ownership probability or automatically label a pair `LIKELY_COMMON_CONTROL`. The new relationship labels and `why_linked` receipts are the evidence substrate for that next layer. Any future control tier must stack independent evidence without double-counting multiple facts from the same transaction.
