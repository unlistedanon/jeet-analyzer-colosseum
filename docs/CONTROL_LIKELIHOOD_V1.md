# Control Likelihood v1

Jeet Analyzer separates three different questions:

1. **Are these wallets linked by accepted on-chain evidence?**
2. **Does the pattern suggest one controller may operate both wallets?**
3. **Has common control or legal identity actually been proven?**

Control Likelihood v1 answers the second question without pretending it answers the third.

## User-facing doctrine

- **Linked is a fact about observed evidence.**
- **Likely common control is an evidence-backed risk assessment.**
- **Legal identity and beneficial ownership remain unproven.**
- The existing top-level `COMMON_CONTROL=NOT_PROVEN` proof boundary remains unchanged.
- A trader may still reasonably treat a `LIKELY_COMMON_CONTROL` wallet as same-controller risk when evaluating how much target-token inventory could be deployed against the market.

## Pair-level output

The result contract aggregates accepted relationship edges for each unordered wallet pair and emits:

- `link_status`: `LINKED` or `STRONGLY_LINKED`;
- `control_assessment`: `NOT_ESTABLISHED`, `COORDINATED_BEHAVIOR`, or `LIKELY_COMMON_CONTROL`;
- `common_control`: always `NOT_PROVEN` in v1;
- `legal_identity`: always `NOT_ESTABLISHED` in v1;
- independent evidence classes;
- distinct supporting transaction signatures;
- the raw edge evidence and `why_linked` explanation;
- a plain-language risk interpretation.

## Evidence classes

Accepted graph relationships are normalized into independent evidence classes:

- `CO_SIGN`: two wallet-like addresses sign the same non-market transaction;
- `ATA_PREPARATION`: one wallet signs to prepare another wallet's associated token account;
- `ATA_TOKEN_COMPOSITE`: ATA preparation plus token transfer in the same transaction;
- `TOKEN_FLOW`: direct non-market token transfer;
- `SOL_FUNDING`: clean direct signed SOL funding.

`CONFIRMED_DIRECT_LINK` is intentionally one composite class. ATA preparation and token movement inside the same transaction are not counted as two independent signals.

## `LIKELY_COMMON_CONTROL` rule

A pair reaches `LIKELY_COMMON_CONTROL` only when all of these are true:

1. at least **two distinct evidence classes** are present;
2. the support spans at least **two distinct transaction signatures**;
3. at least one class is a coordination/control signal (`CO_SIGN`, `ATA_PREPARATION`, or `ATA_TOKEN_COMPOSITE`);
4. at least one class is an economic/resource signal (`TOKEN_FLOW`, `SOL_FUNDING`, or `ATA_TOKEN_COMPOSITE`).

This means none of the following can produce `LIKELY_COMMON_CONTROL` by itself:

- one token transfer;
- one SOL funding transfer;
- one ATA creation;
- one co-signed transaction;
- one ATA-plus-token composite transaction;
- repeated token transfers with no independent control signal;
- multiple facts extracted from the same transaction.

## Examples

`CO_SIGN` in transaction A + `TOKEN_FLOW` in transaction B -> `LIKELY_COMMON_CONTROL`.

`ATA_PREPARATION` in transaction A + `SOL_FUNDING` in transaction B -> `LIKELY_COMMON_CONTROL`.

`CO_SIGN` + `TOKEN_FLOW` inside the same transaction only -> `COORDINATED_BEHAVIOR`, not likely common control.

Two direct token transfers across two transactions -> linked, but common control remains `NOT_ESTABLISHED` because the evidence lacks an independent control signal.

## Why this is useful

The product question is not whether Jeet can identify a legal person from public addresses. The practical question is whether a seller who appears out may still have meaningful inventory in wallets that the chain strongly suggests are operated under common control.

A `LIKELY_COMMON_CONTROL` assessment is therefore intended as **exposure/risk intelligence**, not a legal or identity claim.
