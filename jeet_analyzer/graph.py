"""Evidence-backed, cross-asset wallet relationship graph construction."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Iterable, Mapping, Sequence

from .models import RelationshipEdge, TransactionAutopsy


WALLET_LIKE_CLASSIFICATIONS = frozenset({"WALLET_LIKE"})


@dataclass
class GraphEvidence:
    edges: list[RelationshipEdge] = field(default_factory=list)
    excluded_infrastructure: list[dict[str, Any]] = field(default_factory=list)
    ata_rent: list[dict[str, Any]] = field(default_factory=list)
    unresolved_relationships: list[dict[str, Any]] = field(default_factory=list)
    shared_funders: list[dict[str, Any]] = field(default_factory=list)
    excluded_infrastructure_total: int = 0
    ata_rent_total: int = 0
    unresolved_relationships_total: int = 0
    unresolved_relationship_counts_by_classification: dict[str, int] = field(default_factory=dict)
    unresolved_relationship_counts_by_reason: dict[str, int] = field(default_factory=dict)


@dataclass
class SignerActivityIndex:
    """Bounded exact signer history for independent-signature checks.

    Relationship extraction only needs to answer whether a recipient signed a
    transaction with a signature different from the edge being evaluated. Two
    distinct signatures per signer are sufficient to answer that question
    exactly, regardless of how many transactions the signer has appeared in.
    """

    signatures: dict[str, tuple[str, str | None]] = field(default_factory=dict)

    def observe(self, autopsy: TransactionAutopsy) -> None:
        signature = autopsy.signature
        for signer in autopsy.signers:
            observed = self.signatures.get(signer)
            if observed is None:
                self.signatures[signer] = (signature, None)
                continue
            first, second = observed
            if signature == first or signature == second:
                continue
            if second is None:
                self.signatures[signer] = (first, signature)

    @classmethod
    def from_autopsies(cls, autopsies: Sequence[TransactionAutopsy]) -> "SignerActivityIndex":
        index = cls()
        for autopsy in autopsies:
            index.observe(autopsy)
        return index

    def has_other_signature(self, signer: str, current_signature: str) -> bool:
        observed = self.signatures.get(signer)
        if observed is None:
            return False
        first, second = observed
        if first != current_signature:
            return True
        return second is not None and second != current_signature


def _wallet_like(address: str, classifications: Mapping[str, str]) -> bool:
    return classifications.get(address) in WALLET_LIKE_CLASSIFICATIONS


def candidate_addresses(autopsy: TransactionAutopsy, wallet: str) -> set[str]:
    """Return exact observed counterparties worth account-type resolution.

    Candidate discovery intentionally casts a wider net than edge acceptance.
    Exact token/native counterparties remain eligible, and Hunt Mode adds three
    transaction-level signals when the transaction is not market/router
    context: external token authorities, co-signers, and ATA payer/owner
    preparation. Account
    classification and relationship extraction still have to pass before graph
    expansion, so discovery never becomes an ownership claim by itself.
    """

    candidates: set[str] = set()
    if autopsy.failed:
        return candidates
    for transfer in autopsy.token_transfers:
        if transfer.source_owner == wallet:
            candidates.add(transfer.destination_owner)
            if not autopsy.market_context and transfer.authority != transfer.source_owner:
                candidates.add(transfer.authority)
        elif transfer.destination_owner == wallet:
            candidates.add(transfer.source_owner)
            if not autopsy.market_context and transfer.authority != transfer.source_owner:
                candidates.add(transfer.authority)
    for transfer in autopsy.native_transfers:
        if transfer.source == wallet:
            candidates.add(transfer.destination)
        elif transfer.destination == wallet:
            candidates.add(transfer.source)

    # Swap/router transactions routinely contain unrelated signing/plumbing
    # accounts. They may remain in the autopsy, but they do not seed Hunt Mode
    # co-signer or ATA-preparation expansion.
    if not autopsy.market_context:
        if wallet in autopsy.signers:
            candidates.update(signer for signer in autopsy.signers if signer != wallet)
        for creation in autopsy.ata_creations:
            if creation.payer == wallet:
                candidates.add(creation.owner)
            elif creation.owner == wallet:
                candidates.add(creation.payer)

    return {candidate for candidate in candidates if candidate and candidate != wallet}


def _exclusion(
    autopsy: TransactionAutopsy,
    source: str,
    destination: str,
    classification: str,
    reason: str,
    *,
    asset: str,
    amount_raw: int,
) -> dict[str, Any]:
    return {
        "source": source,
        "destination": destination,
        "classification": classification,
        "reason": reason,
        "asset": asset,
        "amount_raw": amount_raw,
        "signature": autopsy.signature,
        "timestamp": autopsy.timestamp,
        "program_ids": autopsy.program_ids,
        "creates_graph_edge": False,
    }


def extract_relationships(
    autopsies: Iterable[TransactionAutopsy],
    classifications: Mapping[str, str],
    *,
    signer_activity: SignerActivityIndex | None = None,
    exclusion_limit: int | None = None,
    unresolved_limit: int | None = None,
) -> GraphEvidence:
    """Classify exact cross-asset relationships without inferring identity.

    ``exclusion_limit`` bounds only report-only infrastructure/rent rows.
    ``unresolved_limit`` bounds representative unresolved relationship rows while
    preserving exact totals and reason/classification counts. Accepted edges are
    never truncated by either setting.
    """

    if exclusion_limit is not None and exclusion_limit < 0:
        raise ValueError("exclusion_limit cannot be negative")
    if unresolved_limit is not None and unresolved_limit < 0:
        raise ValueError("unresolved_limit cannot be negative")
    if signer_activity is None:
        if not isinstance(autopsies, Sequence):
            autopsies = tuple(autopsies)
        signer_activity = SignerActivityIndex.from_autopsies(autopsies)

    result = GraphEvidence()
    # Deduplicate accepted relationship evidence as it is produced.  The old
    # implementation appended every raw edge to result.edges and only built a
    # unique dictionary after the entire spool had been replayed.  Dense graph
    # wallets could therefore create millions of temporary RelationshipEdge
    # objects even when the final exact graph contained only thousands.
    unique_edges: dict[tuple[str, str, str, str, str], RelationshipEdge] = {}

    def record_edge(edge: RelationshipEdge) -> None:
        key = (edge.signature, edge.source, edge.destination, edge.asset, edge.relationship_type)
        unique_edges.setdefault(key, edge)

    excluded_infrastructure_total = 0
    ata_rent_total = 0
    unresolved_relationships_total = 0
    unresolved_counts_by_classification: Counter[str] = Counter()
    unresolved_counts_by_reason: Counter[str] = Counter()

    def record_excluded(row: dict[str, Any]) -> None:
        nonlocal excluded_infrastructure_total
        excluded_infrastructure_total += 1
        if exclusion_limit is None or len(result.excluded_infrastructure) < exclusion_limit:
            result.excluded_infrastructure.append(row)

    def record_ata_rent(row: dict[str, Any]) -> None:
        nonlocal ata_rent_total
        ata_rent_total += 1
        if exclusion_limit is None or len(result.ata_rent) < exclusion_limit:
            result.ata_rent.append(row)

    def record_unresolved(row: dict[str, Any]) -> None:
        nonlocal unresolved_relationships_total
        unresolved_relationships_total += 1
        unresolved_counts_by_classification[str(row.get("classification") or "UNKNOWN")] += 1
        unresolved_counts_by_reason[str(row.get("reason") or "UNKNOWN")] += 1
        if unresolved_limit is None or len(result.unresolved_relationships) < unresolved_limit:
            result.unresolved_relationships.append(row)

    for autopsy in autopsies:
        if autopsy.failed:
            continue

        # Only same-transaction CONFIRMED_DIRECT_LINK pairs matter for ATA
        # preparation suppression. Keeping them locally makes this exact O(1)
        # membership work instead of repeatedly scanning every prior edge.
        confirmed_direct_link_pairs: set[tuple[str, str]] = set()

        ata_by_account = {creation.token_account: creation for creation in autopsy.ata_creations}
        native_ata_accounts = {
            transfer.destination
            for transfer in autopsy.native_transfers
            if transfer.destination in ata_by_account
        }
        for creation in autopsy.ata_creations:
            if creation.token_account in native_ata_accounts:
                continue
            record_ata_rent(
                _exclusion(
                    autopsy,
                    creation.payer,
                    creation.token_account,
                    "ATA_RENT",
                    f"associated-token-account creation rent/plumbing for owner {creation.owner}",
                    asset="SOL",
                    amount_raw=max(int(autopsy.native_deltas.get(creation.token_account, 0)), 0),
                )
            )
        for transfer in autopsy.token_transfers:
            source = transfer.source_owner
            destination = transfer.destination_owner
            if source == destination:
                continue
            if autopsy.market_context:
                record_excluded(
                    _exclusion(
                        autopsy,
                        source,
                        destination,
                        "EXCLUDED_SWAP_INFRASTRUCTURE",
                        "; ".join(autopsy.market_reasons),
                        asset=transfer.mint,
                        amount_raw=transfer.amount_raw,
                    )
                )
                continue
            if transfer.authority and transfer.authority != source:
                authority = transfer.authority
                if not _wallet_like(source, classifications) or not _wallet_like(authority, classifications):
                    record_unresolved(
                        _exclusion(
                            autopsy,
                            source,
                            authority,
                            "DELEGATED_AUTHORITY_NOT_WALLET",
                            "parsed external token authority is not proven wallet-like; authority expansion blocked",
                            asset=transfer.mint,
                            amount_raw=transfer.amount_raw,
                        )
                    )
                elif authority not in autopsy.signers:
                    record_unresolved(
                        _exclusion(
                            autopsy,
                            source,
                            authority,
                            "DELEGATED_AUTHORITY_UNVERIFIED",
                            "parsed external token authority is not an observed signer; delegated relationship not accepted",
                            asset=transfer.mint,
                            amount_raw=transfer.amount_raw,
                        )
                    )
                else:
                    record_edge(
                        RelationshipEdge(
                            source=source,
                            destination=authority,
                            relationship_type="DELEGATED_TOKEN_AUTHORITY",
                            classification="DELEGATED_TOKEN_AUTHORITY",
                            signature=autopsy.signature,
                            timestamp=autopsy.timestamp,
                            block_time=autopsy.block_time,
                            asset=transfer.mint,
                            amount_raw=transfer.amount_raw,
                            signers=autopsy.signers,
                            fee_payer=autopsy.fee_payer,
                            reason=(
                                "external wallet-like authority signed and moved target tokens from a token account "
                                f"owned by {source}; authority is {authority}"
                            ),
                            confidence="HIGH" if not autopsy.unknown_program_ids else "MEDIUM",
                            source_token_account=transfer.source_token_account,
                            destination_token_account=transfer.destination_token_account,
                            relationship_context="DELEGATED_TOKEN_AUTHORITY",
                            source_signed=source in autopsy.signers,
                            source_paid_fee=autopsy.fee_payer == source,
                            market_context=False,
                            common_control="NOT_PROVEN",
                        )
                    )
            if not _wallet_like(source, classifications) or not _wallet_like(destination, classifications):
                record_excluded(
                    _exclusion(
                        autopsy,
                        source,
                        destination,
                        "UNKNOWN_COUNTERPARTY",
                        "source or destination is not proven wallet-like; pool/program/token-account expansion blocked",
                        asset=transfer.mint,
                        amount_raw=transfer.amount_raw,
                    )
                )
                continue
            creation = ata_by_account.get(transfer.destination_token_account)
            ata_link = bool(
                creation
                and creation.owner == destination
                and creation.payer == source
                and source in autopsy.signers
            )
            authority_signed = transfer.authority in autopsy.signers
            if not authority_signed and not ata_link:
                record_unresolved(
                    _exclusion(
                        autopsy,
                        source,
                        destination,
                        "UNKNOWN_COUNTERPARTY",
                        "parsed transfer is concrete but no wallet-like source authority signed; relationship not expanded",
                        asset=transfer.mint,
                        amount_raw=transfer.amount_raw,
                    )
                )
                continue
            relationship_type = "CONFIRMED_DIRECT_LINK" if ata_link else "DIRECT_TOKEN_TRANSFER"
            confidence = "HIGH" if ata_link or not autopsy.unknown_program_ids else "MEDIUM"
            reason = (
                "sender signed and paid to create recipient ATA, then a parsed token instruction transferred the asset"
                if ata_link
                else "parsed SPL Token/Token-2022 transfer with exact owners, accounts, amount, signature, and time"
            )
            if autopsy.unknown_program_ids:
                reason += "; unknown programs preserved, so identity/control is not inferred"
            record_edge(
                RelationshipEdge(
                    source,
                    destination,
                    relationship_type,
                    relationship_type,
                    autopsy.signature,
                    autopsy.timestamp,
                    autopsy.block_time,
                    transfer.mint,
                    transfer.amount_raw,
                    autopsy.signers,
                    autopsy.fee_payer,
                    reason,
                    confidence,
                    source_token_account=transfer.source_token_account,
                    destination_token_account=transfer.destination_token_account,
                    recipient_independently_signed=signer_activity.has_other_signature(
                        destination, autopsy.signature
                    ),
                )
            )
            if relationship_type == "CONFIRMED_DIRECT_LINK":
                confirmed_direct_link_pairs.add((source, destination))

        for transfer in autopsy.native_transfers:
            matching_ata = next(
                (
                    creation
                    for creation in autopsy.ata_creations
                    if creation.token_account == transfer.destination and creation.payer == transfer.source
                ),
                None,
            )
            if matching_ata:
                record_ata_rent(
                    _exclusion(
                        autopsy,
                        transfer.source,
                        transfer.destination,
                        "ATA_RENT",
                        f"rent for associated token account owned by {matching_ata.owner}",
                        asset="SOL",
                        amount_raw=transfer.lamports,
                    )
                )
                continue
            if autopsy.market_context or autopsy.token_transfers:
                record_excluded(
                    _exclusion(
                        autopsy,
                        transfer.source,
                        transfer.destination,
                        "EXCLUDED_SWAP_INFRASTRUCTURE",
                        "native movement shares transaction context with reciprocal/market token behavior",
                        asset="SOL",
                        amount_raw=transfer.lamports,
                    )
                )
                continue
            if not _wallet_like(transfer.source, classifications) or not _wallet_like(
                transfer.destination, classifications
            ):
                record_excluded(
                    _exclusion(
                        autopsy,
                        transfer.source,
                        transfer.destination,
                        "UNKNOWN_COUNTERPARTY",
                        "native counterparty is not proven wallet-like",
                        asset="SOL",
                        amount_raw=transfer.lamports,
                    )
                )
                continue
            if not transfer.source_is_signer or not transfer.source_is_fee_payer:
                record_unresolved(
                    _exclusion(
                        autopsy,
                        transfer.source,
                        transfer.destination,
                        "UNKNOWN_COUNTERPARTY",
                        "native transfer source was not both signer and fee payer",
                        asset="SOL",
                        amount_raw=transfer.lamports,
                    )
                )
                continue
            record_edge(
                RelationshipEdge(
                    transfer.source,
                    transfer.destination,
                    "PLAIN_DIRECT_SOL_TRANSFER",
                    "DIRECT_SIGNED_SOL_FUNDING",
                    autopsy.signature,
                    autopsy.timestamp,
                    autopsy.block_time,
                    "SOL",
                    transfer.lamports,
                    autopsy.signers,
                    autopsy.fee_payer,
                    "clean parsed System Program transfer; source signed and paid fee; no reciprocal market movement",
                    "HIGH",
                    recipient_independently_signed=signer_activity.has_other_signature(
                        transfer.destination, autopsy.signature
                    ),
                    relationship_context="DIRECT_SIGNED_SOL_FUNDING",
                    source_signed=transfer.source_is_signer,
                    source_paid_fee=transfer.source_is_fee_payer,
                    market_context=autopsy.market_context,
                )
            )

        if autopsy.market_context:
            continue

        # A wallet that pays to prepare another wallet's ATA is directly linked
        # by an exact on-chain action even if no token transfer follows in the
        # same transaction. If the same transaction already produced the
        # stronger CONFIRMED_DIRECT_LINK, do not duplicate the preparation
        # signal as a second edge.
        for creation in autopsy.ata_creations:
            payer = creation.payer
            owner = creation.owner
            if not payer or not owner or payer == owner:
                continue
            if not _wallet_like(payer, classifications) or not _wallet_like(owner, classifications):
                continue
            if payer not in autopsy.signers:
                record_unresolved(
                    _exclusion(
                        autopsy,
                        payer,
                        owner,
                        "ATA_PREPARATION_UNRESOLVED",
                        "ATA payer/owner relationship is parsed but the payer is not an observed signer",
                        asset=creation.mint or "UNKNOWN_TOKEN",
                        amount_raw=0,
                    )
                )
                continue
            if (payer, owner) in confirmed_direct_link_pairs:
                continue
            record_edge(
                RelationshipEdge(
                    payer,
                    owner,
                    "ATA_PREPARATION",
                    "DIRECTLY_LINKED",
                    autopsy.signature,
                    autopsy.timestamp,
                    autopsy.block_time,
                    creation.mint or "UNKNOWN_TOKEN",
                    0,
                    autopsy.signers,
                    autopsy.fee_payer,
                    "payer signed a non-market transaction that created an associated token account for another wallet owner; this proves a preparation link, not common ownership",
                    "HIGH" if not autopsy.unknown_program_ids else "MEDIUM",
                    recipient_independently_signed=signer_activity.has_other_signature(
                        owner, autopsy.signature
                    ),
                    relationship_context="ATA_PAYER_TO_OWNER",
                    source_signed=True,
                    source_paid_fee=autopsy.fee_payer == payer,
                    market_context=False,
                )
            )

        # Co-signing the same non-market transaction is an exact coordination
        # signal. It is deliberately represented as COORDINATED_BEHAVIOR, not
        # SAME_OWNER. Market/router transactions are excluded above.
        wallet_signers = sorted(
            {signer for signer in autopsy.signers if _wallet_like(signer, classifications)}
        )
        for source, destination in combinations(wallet_signers, 2):
            record_edge(
                RelationshipEdge(
                    source,
                    destination,
                    "NON_MARKET_CO_SIGNER",
                    "COORDINATED_BEHAVIOR",
                    autopsy.signature,
                    autopsy.timestamp,
                    autopsy.block_time,
                    "SIGNATURE",
                    0,
                    autopsy.signers,
                    autopsy.fee_payer,
                    "both wallet-like addresses signed the same non-market transaction; this is direct coordination evidence, not proof of common ownership",
                    "HIGH" if not autopsy.unknown_program_ids else "MEDIUM",
                    recipient_independently_signed=signer_activity.has_other_signature(
                        destination, autopsy.signature
                    ),
                    relationship_context="NON_MARKET_CO_SIGNATURE",
                    source_signed=True,
                    source_paid_fee=autopsy.fee_payer == source,
                    market_context=False,
                )
            )

    result.excluded_infrastructure_total = excluded_infrastructure_total
    result.ata_rent_total = ata_rent_total
    result.unresolved_relationships_total = unresolved_relationships_total
    result.unresolved_relationship_counts_by_classification = dict(
        sorted(unresolved_counts_by_classification.items())
    )
    result.unresolved_relationship_counts_by_reason = dict(
        sorted(unresolved_counts_by_reason.items())
    )

    # Accepted edges were deduplicated during spool replay, so materialize only
    # the exact final edge set here.  This keeps peak memory proportional to the
    # final graph rather than to every duplicate observation encountered.
    result.edges = list(unique_edges.values())

    pair_counts = Counter((edge.source, edge.destination) for edge in result.edges)
    preserved_classifications = {
        "CONFIRMED_DIRECT_LINK",
        "PLAIN_DIRECT_SOL_TRANSFER",
        "ATA_PREPARATION",
        "NON_MARKET_CO_SIGNER",
    }
    for edge in result.edges:
        if (
            pair_counts[(edge.source, edge.destination)] > 1
            and edge.relationship_type not in preserved_classifications
        ):
            edge.classification = "REPEATED_DIRECT_LINK"

    funding_edges_by_funder: dict[str, list[RelationshipEdge]] = defaultdict(list)
    for edge in result.edges:
        if edge.relationship_type == "PLAIN_DIRECT_SOL_TRANSFER" and edge.source != edge.destination:
            funding_edges_by_funder[edge.source].append(edge)
    for funder, funding_edges in sorted(funding_edges_by_funder.items()):
        recipients = sorted({edge.destination for edge in funding_edges})
        if len(recipients) < 2:
            continue
        support_by_recipient: dict[str, list[RelationshipEdge]] = defaultdict(list)
        for edge in funding_edges:
            edge.relationship_context = "MULTI_RECIPIENT_FUNDER"
            support_by_recipient[edge.destination].append(edge)
        recipient_pairs = []
        for wallet_a, wallet_b in combinations(recipients, 2):
            pair_support = sorted(
                support_by_recipient[wallet_a] + support_by_recipient[wallet_b],
                key=lambda edge: (edge.block_time, edge.signature, edge.destination),
            )
            recipient_pairs.append(
                {
                    "wallet_a": wallet_a,
                    "wallet_b": wallet_b,
                    "third_party_funder": funder,
                    "relationship_type": "THIRD_PARTY_SHARED_FUNDER",
                    "classification": "SHARED_FUNDER",
                    "supporting_signatures": [edge.signature for edge in pair_support],
                    "common_control": "NOT_PROVEN",
                    "reason": "distinct wallet-like recipients received direct signed SOL funding from the same third-party wallet",
                }
            )
        result.shared_funders.append(
            {
                "funder": funder,
                "recipients": recipients,
                "relationship_type": "THIRD_PARTY_SHARED_FUNDER",
                "classification": "SHARED_FUNDER",
                "relationship_scope": "RECIPIENT_TO_RECIPIENT_VIA_THIRD_PARTY",
                "recipient_pairs": recipient_pairs,
                "supporting_transfers": [
                    {
                        "source": edge.source,
                        "destination": edge.destination,
                        "asset": edge.asset,
                        "amount_raw": edge.amount_raw,
                        "signature": edge.signature,
                        "timestamp": edge.timestamp,
                        "block_time": edge.block_time,
                        "relationship_type": edge.relationship_type,
                        "classification": edge.classification,
                        "relationship_context": edge.relationship_context,
                        "source_signed": edge.source_signed,
                        "source_paid_fee": edge.source_paid_fee,
                        "market_context": edge.market_context,
                        "common_control": edge.common_control,
                    }
                    for edge in sorted(
                        funding_edges,
                        key=lambda edge: (edge.block_time, edge.signature, edge.destination),
                    )
                ],
                "common_control": "NOT_PROVEN",
                "reason": "multiple distinct wallet-like recipients share direct signed SOL funding from the same third-party wallet",
            }
        )

    result.edges.sort(key=lambda edge: (edge.block_time, edge.signature, edge.source, edge.destination, edge.relationship_type))
    return result


def assign_graph_depths(
    seed: str, edges: Sequence[RelationshipEdge], *, graph_depth: int, max_wallets: int
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Breadth-first depth assignment with deterministic explosion limits.

    Overflow receipts preserve exact rejection counts plus bounded samples instead
    of materializing one warning dictionary per rejected candidate.
    """

    depths = {seed: 0}
    unresolved: list[dict[str, Any]] = []
    overflow_counts: Counter[str] = Counter()
    overflow_samples: dict[str, list[str]] = defaultdict(list)
    frontier = deque([seed])
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        adjacency[edge.source].add(edge.destination)
        adjacency[edge.destination].add(edge.source)

    while frontier:
        wallet = frontier.popleft()
        depth = depths[wallet]
        for neighbor in sorted(adjacency.get(wallet, ())):
            if neighbor in depths:
                continue
            next_depth = depth + 1
            if next_depth > graph_depth:
                unresolved.append(
                    {
                        "kind": "GRAPH_DEPTH_EXHAUSTED",
                        "source": wallet,
                        "candidate": neighbor,
                        "next_depth": next_depth,
                    }
                )
                continue
            if len(depths) >= max_wallets:
                overflow_counts[wallet] += 1
                sample = overflow_samples[wallet]
                if len(sample) < 8 and neighbor not in sample:
                    sample.append(neighbor)
                continue
            depths[neighbor] = next_depth
            frontier.append(neighbor)
    for source in sorted(overflow_counts):
        unresolved.append(
            {
                "kind": "MAX_WALLETS_EXHAUSTED",
                "source": source,
                "max_wallets": max_wallets,
                "rejected_candidates": int(overflow_counts[source]),
                "sample_candidates": overflow_samples[source],
            }
        )
    for edge in edges:
        edge.source_depth = depths.get(edge.source, graph_depth + 1)
        edge.destination_depth = depths.get(edge.destination, graph_depth + 1)
    return depths, unresolved
