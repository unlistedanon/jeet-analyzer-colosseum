# Control Risk UI v1

This layer turns the pair-level control assessment into trader-facing exposure intelligence without weakening Jeet Analyzer's proof boundaries.

## Display order

The UI keeps raw forensic evidence visible and adds a dedicated Control Risk panel after the relationship graph and before the detailed related-inventory table.

The panel answers four fast questions:

1. How many evidence-linked related wallets were found?
2. How many wallet pairs are strongly linked?
3. How many pairs reached `LIKELY_COMMON_CONTROL` under the validated backend policy?
4. How much fresh visible target-token inventory sits on related wallets participating in likely-common-control pairs?

## Linkage remains easy to surface

A wallet does not need to reach `LIKELY_COMMON_CONTROL` to appear in the panel. One accepted on-chain relationship is enough to count it as evidence-linked.

If linked wallets exist but no pair-level control assessment is emitted, the UI explicitly says that the wallets remain linked and that the absence of a stronger assessment does not erase the underlying relationship evidence.

## Inventory semantics

The panel reports visible inventory only from fresh balances already present in the result. Missing balances are not treated as zero.

`Visible inventory on likely-control wallets` deduplicates related wallets that participate in at least one `LIKELY_COMMON_CONTROL` pair and excludes the seed wallet so the number represents additional related-wallet ammunition rather than double-counting the primary seller.

The existing Related Inventory panel remains the detailed source for wallet-by-wallet balances and relationship paths.

## Attribution boundary

The panel always keeps the proof boundary visible:

- `LINKED` is an accepted on-chain relationship fact;
- `STRONGLY_LINKED` means independent relationship evidence stacked;
- `LIKELY_COMMON_CONTROL` is a same-controller risk assessment;
- `COMMON_CONTROL=NOT_PROVEN` remains the proof status;
- legal identity and beneficial ownership are not established.
