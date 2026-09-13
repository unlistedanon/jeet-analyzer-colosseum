"""Token-agnostic cluster-audit orchestration over shared evidence layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal, ROUND_CEILING
from typing import Any, Mapping, Sequence

from . import graph, graph_accumulator, history, lifecycle, report_contract, tx_classifier
from .investigation import InvestigationContext, ProviderBudgetExhausted
from .models import ProviderCoverage, RelationshipEdge, TransactionAutopsy
from .progress import ProgressTracker


ACCOUNT_CLASSIFICATION_BATCH_SIZE = 100
ACCOUNT_CLASSIFICATION_MIN_INTERVAL_SECONDS = 0.12
MIN_DEEP_SOL_FUNDING_LAMPORTS = 1_000_000
HIGH_SIGNAL_DEEP_RELATIONSHIP_TYPES = frozenset(
    {"CONFIRMED_DIRECT_LINK", "ATA_PREPARATION"}
)


@dataclass(frozen=True)
class ClusterAuditConfig:
    graph_depth: int = 3
    max_wallets: int = 50
    max_graph_wallet_pages: int = 250
    funding_lookback_days: int = 365
    materiality_inventory_pct: Decimal = Decimal("0.01")
    deep_forensic: bool = False

    def validate(self) -> None:
        if not 0 <= self.graph_depth <= 6:
            raise history.HistoryError("--graph-depth must be between 0 and 6")
        if self.max_wallets < 1:
            raise history.HistoryError("--max-wallets must be positive")
        if self.max_graph_wallet_pages < 1:
            raise history.HistoryError("--max-graph-wallet-pages must be positive")
        if self.funding_lookback_days < 1:
            raise history.HistoryError("--funding-lookback-days must be positive")
        if not Decimal(0) <= self.materiality_inventory_pct <= Decimal(100):
            raise history.HistoryError("--materiality-inventory-pct must be between 0 and 100")


def _budget_admit_batch(
    batch: history.ProviderBatch, investigation: InvestigationContext | None
) -> history.ProviderBatch:
    if investigation is None:
        return batch
    accepted: list[dict[str, Any]] = []
    for transaction in batch.transactions:
        signature = history.transaction_signature(transaction)
        if not signature:
            continue
        try:
            investigation.observe_transaction(signature, transaction)
        except ProviderBudgetExhausted as exc:
            return history.ProviderBatch(
                accepted,
                False,
                batch.provider,
                batch.coverage_scope,
                str(exc),
                batch.pages,
                batch.retry_count,
                str(exc),
                exc.category,
            )
        accepted.append(transaction)
    return history.ProviderBatch(
        accepted,
        batch.complete,
        batch.provider,
        batch.coverage_scope,
        batch.limitation,
        batch.pages,
        batch.retry_count,
        batch.terminal_failure_reason,
        batch.failure_category,
    )


def _edge_priority(edge: RelationshipEdge, target_mint: str) -> tuple[int, int, int, int, str]:
    # Rank accepted evidence, never incomparable raw units from unrelated mints.
    # This is a scheduling hint only: admission/coverage/control rules are unchanged.
    signal = 0
    if edge.relationship_type == "CONFIRMED_DIRECT_LINK":
        signal = 5
    elif edge.relationship_type == "NON_MARKET_CO_SIGNER":
        signal = 4
    elif edge.relationship_type == "ATA_PREPARATION":
        signal = 3
    elif edge.classification == "REPEATED_DIRECT_LINK":
        signal = 2
    elif (
        edge.relationship_type == "PLAIN_DIRECT_SOL_TRANSFER"
        and edge.asset == "SOL"
        and int(edge.amount_raw) >= MIN_DEEP_SOL_FUNDING_LAMPORTS
    ):
        signal = 1
    return (
        1 if edge.asset == target_mint else 0,
        1 if edge.confidence == "HIGH" else 0,
        signal,
        max(int(edge.amount_raw), 0) if edge.asset == target_mint else 0,
        edge.signature,
    )


def _deep_traversal_reason(edge: RelationshipEdge, target_mint: str) -> str | None:
    """Return why an already-linked downstream wallet deserves recursive history.

    Every defensible relationship remains visible in the graph.  This gate only
    controls whether Jeet spends another full wallet-history crawl behind the
    edge.  Target-token flow always deepens.  Other assets require a high-signal
    preparation/coordination/direct-link signal, repeated direct behavior, or a
    material clean SOL transfer.  Weak cross-asset links and dust funding stay
    reported without automatically turning into another recursive hunt.
    """

    if edge.asset == target_mint:
        return "TARGET_TOKEN_RELATIONSHIP"
    if edge.confidence != "HIGH":
        return None
    # A co-signature is exact coordination evidence, but it is not enough on
    # its own to justify another full wallet-history crawl. The
    # edge remains visible and can still win bounded admission; recursive
    # expansion is reserved for target-token flow, direct/preparation evidence,
    # repeated links, or material signed funding.
    if edge.relationship_type == "NON_MARKET_CO_SIGNER":
        return None
    if edge.relationship_type in HIGH_SIGNAL_DEEP_RELATIONSHIP_TYPES:
        return edge.relationship_type
    if edge.classification == "REPEATED_DIRECT_LINK":
        return "REPEATED_DIRECT_LINK"
    if (
        edge.relationship_type == "PLAIN_DIRECT_SOL_TRANSFER"
        and int(edge.amount_raw) >= MIN_DEEP_SOL_FUNDING_LAMPORTS
    ):
        return "MATERIAL_DIRECT_SOL_FUNDING"
    return None


def _unresolved_relationship_blocks_target_status(
    row: Mapping[str, Any], target_mint: str
) -> bool:
    """Return whether relationship uncertainty can block the target-inventory conclusion.

    Known non-target assets such as SOL or another concrete mint remain valuable
    relationship evidence, but they do not by themselves prove that target-token
    inventory is hidden. Missing/unknown assets and the target mint remain
    fail-closed blockers. Structural traversal limits are handled separately.
    """

    asset = str(row.get("asset") or "").strip()
    if not asset:
        return True
    if asset.upper() in {"UNKNOWN", "UNKNOWN_TOKEN", "NONE"}:
        return True
    return asset == target_mint


def _coverage(batch: history.ProviderBatch) -> ProviderCoverage:
    return ProviderCoverage(
        batch.complete,
        batch.provider,
        batch.coverage_scope,
        batch.limitation,
        batch.pages,
        batch.retry_count,
        batch.terminal_failure_reason,
        batch.failure_category,
    )


def _provider_failure_batch(scope: str, error: history.ProviderError) -> history.ProviderBatch:
    """Preserve completed seed evidence when optional downstream provider work fails."""

    return history.ProviderBatch(
        [],
        False,
        "helius",
        scope,
        str(error),
        0,
        int(getattr(error, "retry_count", 0) or 0),
        str(error),
        str(getattr(error, "category", "PROVIDER_ERROR")),
    )


def _classification_batch_count(address_count: int) -> int:
    if address_count <= 0:
        return 0
    return (address_count + ACCOUNT_CLASSIFICATION_BATCH_SIZE - 1) // ACCOUNT_CLASSIFICATION_BATCH_SIZE


def _classify_addresses(rpc: Any, addresses: Sequence[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve wallet-like account records in bounded, rate-friendly batches.

    Live ``ReadOnlyRpcClient`` instances expose their sleeper.  Pacing only
    applies between account-classification batches, so fake/test RPCs remain
    instantaneous while live Full Forensic runs avoid bursting above common
    shared-RPC rate limits.  Each failed chunk remains fail-closed as
    unresolved rather than being retried indefinitely by this layer.
    """

    unique = sorted(set(addresses))
    if not unique:
        return {}, {}
    records: dict[str, Any] = {}
    sleeper = getattr(rpc, "sleeper", None)
    for batch_index, offset in enumerate(
        range(0, len(unique), ACCOUNT_CLASSIFICATION_BATCH_SIZE)
    ):
        if batch_index and callable(sleeper):
            sleeper(ACCOUNT_CLASSIFICATION_MIN_INTERVAL_SECONDS)
        chunk = unique[offset : offset + ACCOUNT_CLASSIFICATION_BATCH_SIZE]
        try:
            chunk_records = rpc.get_multiple_accounts(chunk)
        except Exception:
            chunk_records = {address: None for address in chunk}
        for address in chunk:
            records[address] = chunk_records.get(address)
    classifications: dict[str, str] = {}
    notes: dict[str, str] = {}
    for address in unique:
        classification, note = tx_classifier.classify_address_record(records.get(address))
        classifications[address] = classification
        notes[address] = note
    return classifications, notes


def _fresh_target_balance(rpc: Any, provider: history.HistoricalProvider, wallet: str, mint: str) -> tuple[int | None, int | None]:
    try:
        snapshot = rpc.get_owner_token_accounts(wallet, mint, commitment="finalized")
        return int(snapshot.total), int(snapshot.slot)
    except TypeError:
        try:
            snapshot = rpc.get_owner_token_accounts(wallet, mint)
            return int(snapshot.total), int(getattr(snapshot, "slot", 0) or 0)
        except Exception:
            pass
    except Exception:
        pass
    try:
        return int(provider.get_wallet_token_balance(wallet, mint)), None
    except Exception:
        return None, None


def _material_threshold(report: Mapping[str, Any], metadata: history.TokenMetadata, config: ClusterAuditConfig) -> int:
    basis = report.get("reconstructed_starting_inventory_raw")
    if basis is None or int(basis) <= 0:
        basis = metadata.supply
    if int(basis or 0) <= 0:
        return 0
    value = Decimal(int(basis)) * config.materiality_inventory_pct / Decimal(100)
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def _paths(seed: str, depths: Mapping[str, int], edges: Sequence[RelationshipEdge]) -> dict[str, list[str]]:
    paths = {seed: [seed]}
    for depth in range(1, max(depths.values(), default=0) + 1):
        for wallet in sorted(address for address, value in depths.items() if value == depth):
            candidates: list[list[str]] = []
            for edge in edges:
                if edge.destination == wallet and edge.source in paths:
                    candidates.append([*paths[edge.source], wallet])
                if edge.source == wallet and edge.destination in paths:
                    candidates.append([*paths[edge.destination], wallet])
            if candidates:
                paths[wallet] = min(candidates, key=lambda row: (len(row), row))
    return paths


def _visible_shared_funders(
    rows: Sequence[Mapping[str, Any]], depths: Mapping[str, int]
) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    for row in rows:
        funder = str(row.get("funder") or "")
        recipients = [
            str(recipient)
            for recipient in row.get("recipients", [])
            if str(recipient) in depths
        ]
        if funder not in depths or len(recipients) < 2:
            continue
        recipient_set = set(recipients)
        visible.append(
            {
                **row,
                "recipients": recipients,
                "recipient_pairs": [
                    pair
                    for pair in row.get("recipient_pairs", [])
                    if str(pair.get("wallet_a") or "") in recipient_set
                    and str(pair.get("wallet_b") or "") in recipient_set
                ],
                "supporting_transfers": [
                    transfer
                    for transfer in row.get("supporting_transfers", [])
                    if str(transfer.get("source") or "") == funder
                    and str(transfer.get("destination") or "") in recipient_set
                ],
            }
        )
    return visible


def _inventory_increases(
    events: Sequence[history.NormalizedEvent], autopsies: Mapping[str, TransactionAutopsy], wallet: str, mint: str
) -> dict[str, Any]:
    first_reduction = min(
        (event.block_time for event in events if event.wallet == wallet and event.token_delta_raw < 0),
        default=None,
    )
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for autopsy in autopsies.values():
        for transfer in autopsy.token_transfers:
            if transfer.mint != mint or transfer.destination_owner != wallet:
                continue
            classification = "CONFIRMED_BUY" if autopsy.market_context else "DIRECT_TRANSFER_IN"
            key = (autopsy.signature, classification)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "classification": classification,
                    "amount_raw": transfer.destination_delta_raw
                    if transfer.destination_delta_raw is not None
                    else transfer.amount_raw,
                    "signature": autopsy.signature,
                    "timestamp": autopsy.timestamp,
                    "source": transfer.source_owner,
                }
            )
    for event in events:
        if event.wallet != wallet or event.token_delta_raw <= 0:
            continue
        autopsy = autopsies.get(event.signature)
        exact_incoming = next(
            (
                transfer
                for transfer in (autopsy.token_transfers if autopsy else [])
                if transfer.mint == mint and transfer.destination_owner == wallet
            ),
            None,
        )
        if event.event_type == "BUY" and autopsy and autopsy.market_context:
            classification = "CONFIRMED_BUY"
        elif exact_incoming:
            classification = "DIRECT_TRANSFER_IN"
        elif first_reduction is not None and event.block_time >= first_reduction:
            classification = "REACQUISITION"
        else:
            classification = "UNKNOWN_INVENTORY_INCREASE"
        key = (event.signature, classification)
        if key in seen or any(existing[0] == event.signature for existing in seen):
            continue
        seen.add(key)
        rows.append(
            {
                "classification": classification,
                "amount_raw": event.token_amount_raw,
                "signature": event.signature,
                "timestamp": event.timestamp,
                "source": exact_incoming.source_owner if exact_incoming else None,
            }
        )
    return {
        "events": rows,
        "counts": {
            name: sum(1 for row in rows if row["classification"] == name)
            for name in (
                "CONFIRMED_BUY",
                "DIRECT_TRANSFER_IN",
                "REACQUISITION",
                "UNKNOWN_INVENTORY_INCREASE",
            )
        },
    }


def _proceeds(
    sales: Sequence[history.NormalizedEvent], autopsies: Mapping[str, TransactionAutopsy], wallet: str
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    gross_native = 0
    net_native = 0
    fees = 0
    quote_totals: dict[str, dict[str, Any]] = {}
    for sale in sales:
        autopsy = autopsies.get(sale.signature)
        native_delta = int(autopsy.native_deltas.get(wallet, 0)) if autopsy else 0
        fee = int(autopsy.fee_lamports) if autopsy and autopsy.fee_payer == wallet else 0
        gross = native_delta + fee if native_delta > 0 else 0
        gross_native += gross
        net_native += native_delta
        fees += fee
        if sale.quote_mint and sale.quote_amount_raw is not None:
            item = quote_totals.setdefault(
                sale.quote_mint,
                {
                    "asset": sale.quote_symbol or sale.quote_mint,
                    "mint": sale.quote_mint,
                    "amount_raw": 0,
                    "decimals": sale.quote_decimals,
                },
            )
            item["amount_raw"] += int(sale.quote_amount_raw)
        rows.append(
            {
                "signature": sale.signature,
                "timestamp": sale.timestamp,
                "target_token_sold_raw": sale.token_amount_raw,
                "gross_quote_received": {
                    "asset": sale.quote_symbol or sale.quote_mint,
                    "mint": sale.quote_mint,
                    "amount_raw": sale.quote_amount_raw,
                    "decimals": sale.quote_decimals,
                },
                "net_native_delta_lamports": native_delta,
                "tx_fee_lamports": fee,
                "net_after_fee_lamports": native_delta,
            }
        )
    return {
        "transactions": rows,
        "gross_native_received_lamports": gross_native,
        "net_native_delta_lamports": net_native,
        "tx_fees_lamports": fees,
        "net_after_fee_lamports": net_native,
        "quote_totals": list(quote_totals.values()),
        "warning": "Observed quote/native flows are evidence, not exact P&L or cost-basis accounting.",
    }


def run_cluster_audit(
    provider: history.HistoricalProvider,
    rpc: Any,
    metadata: history.TokenMetadata,
    seed: str,
    start: int,
    end: int,
    *,
    config: ClusterAuditConfig,
    progress: ProgressTracker | None = None,
    investigation: InvestigationContext | None = None,
) -> dict[str, Any]:
    """Build a bounded cross-asset graph and reconcile target inventory."""

    config.validate()
    tracker = progress or ProgressTracker()
    if tracker.states["TOKEN_RESOLUTION"] == "pending":
        tracker.complete("TOKEN_RESOLUTION", "Target token metadata supplied")
    tracker.complete("SELLER_ANALYSIS", "Seller ranking is not required for a seeded cluster audit")
    tracker.running("RELATED_INVENTORY", "Fetching fresh seed-wallet target-token inventory")
    seed_balance, seed_balance_slot = _fresh_target_balance(rpc, provider, seed, metadata.mint)
    tracker.running("WALLET_HISTORY", "Fetching bounded seed-wallet history before graph enrichment")
    tracker.running("TRANSACTION_CLASSIFICATION", "Classifying seed-wallet target-token transactions")
    observed_transaction_signatures: set[str] = set()
    seed_transaction_signatures: set[str] = set()
    target_events_by_signature: dict[str, list[history.NormalizedEvent]] = {}
    seed_events: list[history.NormalizedEvent] = []
    seed_autopsies: dict[str, TransactionAutopsy] = {}

    def consume_seed_transaction(transaction: dict[str, Any]) -> None:
        signature = history.transaction_signature(transaction)
        if not signature or signature in seed_transaction_signatures:
            return
        seed_transaction_signatures.add(signature)
        observed_transaction_signatures.add(signature)
        normalized = history.normalize_transaction(
            transaction,
            metadata.mint,
            include_unknown_increases=True,
        )
        seed_events.extend(normalized)
        for event in normalized:
            target_events_by_signature.setdefault(event.signature, []).append(event)
        autopsy = tx_classifier.autopsy_transaction(transaction)
        if autopsy and not autopsy.failed:
            seed_autopsies[signature] = autopsy

    seed_raw_batch = provider.stream_wallet_events(
        seed, metadata.mint, start, end, consume_seed_transaction
    )
    seed_batch = _budget_admit_batch(seed_raw_batch, investigation)
    del seed_raw_batch
    # Buffered/non-streaming providers retain compatibility through the base
    # fallback. Helius returns an empty transaction list because each page was
    # already consumed and released inside history._history().
    for transaction in seed_batch.transactions:
        consume_seed_transaction(transaction)
    seed_batch = replace(seed_batch, transactions=[])
    seed_events.sort(key=lambda event: (event.block_time, event.signature, event.wallet or ""))
    seed_transaction_count = len(seed_transaction_signatures)
    seed_coverage = _coverage(seed_batch)
    coverage_rows = [seed_coverage]
    graph_coverage_rows: list[ProviderCoverage] = []
    broad_mint_coverage: ProviderCoverage | None = None
    broad_mint_enrichment_skipped_reason: str | None = None

    preliminary_lifecycle = lifecycle.analyze_wallet_lifecycle(
        seed_events,
        seed,
        seed_balance,
        coverage_complete=seed_batch.complete,
        material_threshold_raw=0,
    )
    material_threshold = _material_threshold(preliminary_lifecycle, metadata, config)
    lifecycle_result = lifecycle.analyze_wallet_lifecycle(
        seed_events,
        seed,
        seed_balance,
        coverage_complete=seed_batch.complete,
        material_threshold_raw=material_threshold,
    )
    wallet_status = lifecycle_result["current_wallet_status"]
    wallet_reasons = list(lifecycle_result["unresolved_evidence"])
    if not wallet_reasons:
        wallet_reasons = [f"current lifecycle state is {wallet_status}"]
    root_sales = [
        event for event in seed_events if event.wallet == seed and event.event_type == "SELL"
    ]
    increases = _inventory_increases(seed_events, seed_autopsies, seed, metadata.mint)
    seed_proceeds = _proceeds(root_sales, seed_autopsies, seed)
    seed_autopsy_count = len(seed_autopsies)
    graph_evidence = graph_accumulator.GraphEvidenceAccumulator()
    graph_evidence.extend(seed_autopsies.values())
    seed_conclusion_established = bool(
        seed_batch.complete
        and seed_balance is not None
        and wallet_status in {"VERIFIED_OUT", "NOT_OUT", "RE_ENTERED"}
    )
    seed_history_progress = tracker.complete if seed_batch.complete else tracker.incomplete
    seed_history_progress(
        "WALLET_HISTORY",
        "Seed-wallet history fetched before downstream expansion"
        if seed_batch.complete
        else "Seed-wallet history is incomplete; seed conclusions remain fail-closed",
        count=seed_transaction_count,
        metadata={"scope": "SEED_WALLET_HISTORY"},
    )
    seed_classification_progress = tracker.complete if seed_batch.complete else tracker.incomplete
    seed_classification_progress(
        "TRANSACTION_CLASSIFICATION",
        "Seed-wallet transactions classified"
        if seed_batch.complete
        else "Seed-wallet transaction classification is based on incomplete history",
        count=seed_autopsy_count,
        metadata={"scope": "SEED_WALLET"},
    )

    tracker.running("RELATIONSHIP_GRAPH", "Building evidence-backed bounded wallet graph")

    classifications, classification_notes = _classify_addresses(rpc, [seed])
    discovered_depths = {seed: 0}
    queue = [seed]
    queued = {seed}
    queue_priorities = {seed: (1, 1, 1, 0, "")}
    fetched: set[str] = set()
    partial_fetched: dict[str, str] = {}
    deepening_skipped: dict[str, dict[str, Any]] = {}
    graph_limit_warnings: list[dict[str, Any]] = []
    max_wallet_overflow: dict[str, dict[str, Any]] = {}
    target_lineage_edges: dict[tuple[str, str, str, str, str], RelationshipEdge] = {}
    activity_start = min(start, end - config.funding_lookback_days * 86400)

    def remember_neighbor(
        source_wallet: str,
        edge: RelationshipEdge,
        next_depth: int,
        *,
        force_deepen: bool = False,
    ) -> None:
        neighbor = edge.destination if edge.source == source_wallet else edge.source
        if not neighbor or neighbor == source_wallet:
            return
        if next_depth > config.graph_depth:
            graph_limit_warnings.append(
                {
                    "kind": "GRAPH_DEPTH_EXHAUSTED",
                    "source": source_wallet,
                    "candidate": neighbor,
                    "next_depth": next_depth,
                }
            )
            return

        previous_depth = discovered_depths.get(neighbor)
        if previous_depth is None:
            if len(discovered_depths) >= config.max_wallets:
                summary = max_wallet_overflow.setdefault(
                    source_wallet,
                    {
                        "kind": "MAX_WALLETS_EXHAUSTED",
                        "source": source_wallet,
                        "max_wallets": config.max_wallets,
                        "rejected_candidates": 0,
                        "sample_candidates": [],
                    },
                )
                summary["rejected_candidates"] += 1
                if len(summary["sample_candidates"]) < 8 and neighbor not in summary["sample_candidates"]:
                    summary["sample_candidates"].append(neighbor)
                return
            discovered_depths[neighbor] = next_depth
        elif next_depth < previous_depth:
            discovered_depths[neighbor] = next_depth

        priority = _edge_priority(edge, metadata.mint)
        if priority > queue_priorities.get(neighbor, (-1, -1, -1, -1, "")):
            queue_priorities[neighbor] = priority

        deep_reason = "FIRST_HOP_FROM_SEED" if force_deepen else _deep_traversal_reason(
            edge, metadata.mint
        )
        if deep_reason:
            deepening_skipped.pop(neighbor, None)
            if neighbor not in fetched and neighbor not in queued:
                queue.append(neighbor)
                queued.add(neighbor)
            return

        if neighbor in fetched or neighbor in queued:
            return
        existing = deepening_skipped.get(neighbor)
        if existing is None or priority > tuple(existing["priority"]):
            deepening_skipped[neighbor] = {
                "wallet": neighbor,
                "source": source_wallet,
                "relationship_type": edge.relationship_type,
                "classification": edge.classification,
                "asset": edge.asset,
                "amount_raw": int(edge.amount_raw),
                "signature": edge.signature,
                "priority": list(priority),
                "reason": (
                    "relationship remains visible, but recursive history was not fetched because the edge "
                    "was neither target-token flow, high-confidence control/preparation evidence, repeated "
                    "direct behavior, nor material clean SOL funding"
                ),
            }

    seed_candidates = sorted(
        candidate
        for candidate in graph_evidence.candidates_for(seed)
        if candidate not in classifications
    )
    if seed_candidates:
        tracker.running(
            "RELATIONSHIP_GRAPH",
            "Classifying seed-linked candidate addresses",
            count=len(seed_candidates),
            progress={
                "candidate_addresses": len(seed_candidates),
                "classification_batches": _classification_batch_count(len(seed_candidates)),
                "wallets_discovered": len(discovered_depths),
                "wallets_fetched": len(fetched),
            },
        )
    seed_candidate_classifications, seed_candidate_notes = _classify_addresses(
        rpc, seed_candidates
    )
    classifications.update(seed_candidate_classifications)
    classification_notes.update(seed_candidate_notes)
    seed_relationships = graph_evidence.evidence(classifications)
    # Seed accounting is complete; downstream traversal keeps only compact graph facts.
    seed_autopsies.clear()
    for edge in sorted(
        [
            row
            for row in seed_relationships.edges
            if row.source == seed or row.destination == seed
        ],
        key=lambda row: _edge_priority(row, metadata.mint),
        reverse=True,
    ):
        remember_neighbor(seed, edge, 1, force_deepen=True)

    while queue:
        # Select one candidate in linear time; do not sort the entire queue each hop.
        wallet = min(queue, key=lambda item: (discovered_depths[item], tuple(-value if isinstance(value, int) else value for value in queue_priorities[item]), item))
        queue.remove(wallet)
        queued.discard(wallet)
        if investigation is not None and investigation.expansion_exhausted:
            graph_limit_warnings.append(
                {
                    "kind": "PROVIDER_BUDGET_EXHAUSTED",
                    "budget": investigation.terminated_by,
                    "reason": investigation.termination_reason,
                }
            )
            break
        remaining_depth = config.graph_depth - discovered_depths[wallet]
        if investigation is not None and not investigation.should_traverse_wallet(wallet, remaining_depth):
            continue
        if wallet in fetched:
            continue
        fetched.add(wallet)
        deepening_skipped.pop(wallet, None)
        tracker.running(
            "RELATIONSHIP_GRAPH",
            "Fetching graph-wallet activity",
            count=len(fetched),
            progress={
                "wallets_fetched": len(fetched),
                "wallets_discovered": len(discovered_depths),
                "wallets_queued": len(queue),
                "deepening_skipped": len(deepening_skipped),
                "autopsies": len(graph_evidence),
            },
        )
        target_wallet_events: list[history.NormalizedEvent] = []
        target_wallet_event_keys: set[tuple[str, str, str, int, str, str]] = set()

        def consume_graph_transaction(transaction: dict[str, Any]) -> None:
            signature = history.transaction_signature(transaction)
            if not signature or signature in observed_transaction_signatures:
                return
            observed_transaction_signatures.add(signature)
            normalized = history.normalize_transaction(
                transaction,
                metadata.mint,
                include_unknown_increases=True,
            )
            if normalized:
                target_events_by_signature[signature] = normalized
            autopsy = tx_classifier.autopsy_transaction(transaction)
            if autopsy and not autopsy.failed:
                graph_evidence.ingest(autopsy)

        def consume_target_transfer(event: history.NormalizedEvent) -> None:
            key = (
                event.signature,
                event.wallet or "",
                event.destination or "",
                int(event.token_amount_raw),
                event.source_token_account or "",
                event.destination_token_account or "",
            )
            if key in target_wallet_event_keys:
                return
            target_wallet_event_keys.add(key)
            target_wallet_events.append(event)
            existing = target_events_by_signature.setdefault(event.signature, [])
            if not any(
                (
                    row.wallet,
                    row.destination,
                    int(row.token_amount_raw),
                    row.source_token_account,
                    row.destination_token_account,
                )
                == (
                    event.wallet,
                    event.destination,
                    int(event.token_amount_raw),
                    event.source_token_account,
                    event.destination_token_account,
                )
                for row in existing
            ):
                existing.append(event)

        try:
            if config.deep_forensic or wallet == seed:
                activity_batch = provider.stream_wallet_activity_bounded(
                    wallet,
                    activity_start,
                    end,
                    consume_graph_transaction,
                    max_pages=config.max_graph_wallet_pages,
                )
            else:
                activity_batch = provider.stream_wallet_target_transfers_bounded(
                    wallet,
                    metadata.mint,
                    start,
                    end,
                    consume_target_transfer,
                    max_pages=config.max_graph_wallet_pages,
                )
        except history.ProviderError as exc:
            activity_batch = _provider_failure_batch(
                "bounded graph-wallet activity",
                exc,
            )
        batch = _budget_admit_batch(activity_batch, investigation)
        del activity_batch
        graph_coverage = _coverage(batch)
        graph_coverage_rows.append(graph_coverage)
        coverage_rows.append(graph_coverage)
        if not batch.complete and batch.failure_category in {"HIGH_ACTIVITY_HUB", "HIGH_ACTIVITY_TARGET_WALLET"}:
            partial_fetched[wallet] = batch.limitation
            graph_limit_warnings.append(
                {
                    "kind": (
                        "HIGH_ACTIVITY_HUB_CAPPED"
                        if batch.failure_category == "HIGH_ACTIVITY_HUB"
                        else "TARGET_LINEAGE_WALLET_CAPPED"
                    ),
                    "wallet": wallet,
                    "pages": batch.pages,
                    "max_graph_wallet_pages": config.max_graph_wallet_pages,
                    "reason": (
                        "full cross-asset history exceeded the per-wallet forensic page cap; "
                        "observed relationships remain visible, later target-token mint-wide coverage "
                        "and fresh balances continue, and cluster conclusions remain fail-closed"
                        if batch.failure_category == "HIGH_ACTIVITY_HUB"
                        else "target-token-bearing wallet history exceeded the per-wallet page cap; "
                        "fresh balances and observed target lineage remain visible and conclusions fail closed"
                    ),
                }
            )
        # Buffered provider fallbacks are admitted to the investigation budget
        # before consumption. Helius has already streamed and released its rows.
        for transaction in batch.transactions:
            consume_graph_transaction(transaction)
        batch = replace(batch, transactions=[])

        if config.deep_forensic or wallet == seed:
            candidates = sorted(
                candidate
                for candidate in graph_evidence.candidates_for(wallet)
                if candidate not in classifications
            )
            if candidates:
                tracker.running(
                    "RELATIONSHIP_GRAPH",
                    "Classifying new candidate addresses",
                    count=len(candidates),
                    progress={
                        "candidate_addresses": len(candidates),
                        "classification_batches": _classification_batch_count(len(candidates)),
                        "wallets_fetched": len(fetched),
                        "wallets_discovered": len(discovered_depths),
                        "wallets_queued": len(queue),
                        "deepening_skipped": len(deepening_skipped),
                        "autopsies": len(graph_evidence),
                    },
                )
            new_classifications, new_notes = _classify_addresses(rpc, candidates)
            classifications.update(new_classifications)
            classification_notes.update(new_notes)
            evidence = graph_evidence.evidence(classifications)
            incident = sorted(
                [edge for edge in evidence.edges if edge.source == wallet or edge.destination == wallet],
                key=lambda edge: _edge_priority(edge, metadata.mint),
                reverse=True,
            )
            next_depth = discovered_depths[wallet] + 1
            for edge in incident:
                remember_neighbor(
                    wallet,
                    edge,
                    next_depth,
                    force_deepen=(wallet == seed),
                )
        else:
            target_candidates = sorted(
                {
                    address
                    for event in target_wallet_events
                    for address in (event.wallet, event.destination)
                    if address and address not in classifications
                }
            )
            if target_candidates:
                tracker.running(
                    "RELATIONSHIP_GRAPH",
                    "Classifying target-lineage counterparties",
                    count=len(target_candidates),
                    progress={
                        "candidate_addresses": len(target_candidates),
                        "classification_batches": _classification_batch_count(len(target_candidates)),
                        "wallets_fetched": len(fetched),
                        "wallets_discovered": len(discovered_depths),
                        "wallets_queued": len(queue),
                        "target_transfers": len(target_wallet_events),
                    },
                )
            new_classifications, new_notes = _classify_addresses(rpc, target_candidates)
            classifications.update(new_classifications)
            classification_notes.update(new_notes)
            incident = []
            for event in target_wallet_events:
                if not history.is_concrete_transfer_event(event):
                    continue
                source = str(event.wallet or "")
                destination = str(event.destination or "")
                if (
                    classifications.get(source) != "WALLET_LIKE"
                    or classifications.get(destination) != "WALLET_LIKE"
                ):
                    continue
                edge = RelationshipEdge(
                    source=source,
                    destination=destination,
                    relationship_type="TARGET_TOKEN_TRANSFER",
                    classification="CONFIRMED_DIRECT_LINK",
                    signature=event.signature,
                    timestamp=event.timestamp,
                    block_time=event.block_time,
                    asset=metadata.mint,
                    amount_raw=int(event.token_amount_raw),
                    signers=[],
                    fee_payer=None,
                    reason=(
                        "Helius mint-filtered parsed target-token transfer with concrete owner and token-account endpoints"
                    ),
                    confidence="HIGH",
                    source_token_account=event.source_token_account,
                    destination_token_account=event.destination_token_account,
                    relationship_context="TARGET_TOKEN_TRANSFER",
                    market_context=False,
                    common_control="NOT_PROVEN",
                )
                incident.append(edge)
            incident.sort(
                key=lambda edge: _edge_priority(edge, metadata.mint), reverse=True
            )
            next_depth = discovered_depths[wallet] + 1
            for edge in incident:
                key = (
                    edge.signature,
                    edge.source,
                    edge.destination,
                    edge.asset,
                    edge.relationship_type,
                )
                target_lineage_edges.setdefault(key, edge)
                remember_neighbor(wallet, edge, next_depth)
        relationships_accepted = len(evidence.edges) if (config.deep_forensic or wallet == seed) else len(incident)
        tracker.running(
            "RELATIONSHIP_GRAPH",
            "Graph expansion advanced",
            count=relationships_accepted,
            progress={
                "relationships_accepted": relationships_accepted,
                "wallets_fetched": len(fetched),
                "wallets_discovered": len(discovered_depths),
                "wallets_queued": len(queue),
                "deepening_skipped": len(deepening_skipped),
                "autopsies": len(graph_evidence),
                "target_transfer_rows": len(target_wallet_events),
            },
        )
        if not batch.complete and batch.failure_category == "PROVIDER_BUDGET_EXHAUSTED":
            graph_limit_warnings.append(
                {
                    "kind": "PROVIDER_BUDGET_EXHAUSTED",
                    "budget": investigation.terminated_by if investigation else None,
                    "reason": batch.limitation,
                }
            )
            break

    graph_limit_warnings.extend(
        max_wallet_overflow[source]
        for source in sorted(max_wallet_overflow)
    )
    evidence = graph_evidence.evidence(classifications)
    if not config.deep_forensic and target_lineage_edges:
        combined_edges: dict[tuple[str, str, str, str, str], RelationshipEdge] = {}
        for edge in [*evidence.edges, *target_lineage_edges.values()]:
            key = (
                edge.signature,
                edge.source,
                edge.destination,
                edge.asset,
                edge.relationship_type,
            )
            combined_edges.setdefault(key, edge)
        evidence.edges = list(combined_edges.values())
    depths, final_limit_warnings = graph.assign_graph_depths(
        seed, evidence.edges, graph_depth=config.graph_depth, max_wallets=config.max_wallets
    )
    graph_limit_warnings.extend(final_limit_warnings)
    skipped_visible = {
        wallet: detail
        for wallet, detail in deepening_skipped.items()
        if wallet in depths and wallet not in fetched
    }
    if skipped_visible:
        graph_limit_warnings.append(
            {
                "kind": "DEEP_TRAVERSAL_PRIORITIZED",
                "wallet_count": len(skipped_visible),
                "wallets": sorted(skipped_visible),
                "reason": (
                    "all evidence-backed links remain visible, but recursive history is reserved for first-hop, "
                    "target-token, high-confidence control/preparation, repeated-direct, or material SOL-funding signals"
                ),
            }
        )
    paths = _paths(seed, depths, evidence.edges)
    graph_incomplete = (
        any(not row.complete for row in graph_coverage_rows)
        or bool(skipped_visible)
        or bool(investigation and investigation.expansion_exhausted)
    )
    graph_progress = tracker.incomplete if graph_incomplete else tracker.complete
    graph_progress(
        "RELATIONSHIP_GRAPH",
        "Relationship graph preserved with incomplete downstream coverage"
        if graph_incomplete
        else "Evidence-backed relationship graph built",
        count=len(evidence.edges),
        metadata={
            "wallets": len(depths),
            "deepening_skipped": len(skipped_visible),
            "partial_graph_wallets": len(partial_fetched),
        },
    )
    graph_progress(
        "FUNDING_ANCESTRY",
        (
            "Funding ancestry is incomplete at the downstream coverage boundary"
            if graph_incomplete
            else "Funding evidence classified conservatively"
        )
        if config.deep_forensic
        else (
            "Seed-direct relationship scan is incomplete; deep ancestry remains opt-in"
            if graph_incomplete
            else "Seed-direct relationship evidence classified; deep ancestry remains opt-in"
        ),
        count=len(evidence.shared_funders) if config.deep_forensic else 0,
    )

    target_events = sorted(
        (
            event
            for transaction_events in target_events_by_signature.values()
            for event in transaction_events
        ),
        key=lambda event: (event.block_time, event.signature, event.wallet or ""),
    )
    target_trace_wallets = history.discover_traced_wallets(target_events, seed, 3)
    missing_trace_classifications = [wallet for wallet in target_trace_wallets if wallet not in classifications]
    trace_classifications, trace_notes = _classify_addresses(rpc, missing_trace_classifications)
    classifications.update(trace_classifications)
    classification_notes.update(trace_notes)
    tracker.running("RELATED_INVENTORY", "Reconciling fresh target-token balances")
    verification_balances: dict[str, int | None] = {seed: seed_balance}
    balance_slots: dict[str, int | None] = {seed: seed_balance_slot}
    for wallet in sorted(target_trace_wallets):
        if wallet == seed:
            continue
        balance, slot = _fresh_target_balance(rpc, provider, wallet, metadata.mint)
        verification_balances[wallet] = balance
        balance_slots[wallet] = slot
    graph_target_coverage_complete = bool(
        seed_batch.complete
        and all(row.complete for row in graph_coverage_rows)
        and not skipped_visible
        and not (investigation and investigation.expansion_exhausted)
    )
    graph_limitations = [row.limitation for row in graph_coverage_rows if not row.complete]
    if skipped_visible:
        graph_limitations.append(
            f"recursive history intentionally not fetched for {len(skipped_visible)} lower-priority linked wallet(s)"
        )
    verification = history.verify_exit(
        target_events,
        metadata,
        seed,
        verification_balances,
        coverage_complete=graph_target_coverage_complete,
        coverage_scope=(
            "seed-wallet history plus bounded graph-wallet activity"
            if config.deep_forensic
            else "seed full-activity direct-link scan plus bounded target-token lineage"
        ),
        coverage_limitation=(
            "; ".join(graph_limitations)
            or seed_batch.limitation
            or "none"
        ),
        trace_depth=3,
        window_start=start,
        window_end=end,
    )

    node_balances: dict[str, int | None] = {}
    for wallet in sorted(depths):
        if wallet in verification_balances:
            node_balances[wallet] = verification_balances[wallet]
        else:
            balance, slot = _fresh_target_balance(rpc, provider, wallet, metadata.mint)
            node_balances[wallet] = balance
            balance_slots[wallet] = slot
    nodes: list[dict[str, Any]] = []
    independently_signed = set(graph_evidence.signers)
    for wallet, depth in sorted(depths.items(), key=lambda item: (item[1], item[0])):
        skipped_detail = skipped_visible.get(wallet)
        partial_detail = partial_fetched.get(wallet)
        nodes.append(
            {
                "wallet": wallet,
                "depth": depth,
                "relationship_path": paths.get(wallet, [wallet]),
                "classification": classifications.get(wallet, "UNRESOLVED"),
                "classification_note": classification_notes.get(wallet),
                "current_target_token_balance_raw": node_balances.get(wallet),
                "independently_signed_observed_transaction": wallet in independently_signed,
                "history_traversal": (
                    "FETCHED_PARTIAL"
                    if partial_detail
                    else "FETCHED"
                    if wallet in fetched
                    else "NOT_DEEPENED"
                ),
                "history_traversal_reason": (
                    partial_detail
                    if partial_detail
                    else skipped_detail.get("reason")
                    if skipped_detail
                    else None
                    if wallet in fetched
                    else "not fetched within bounded prioritized traversal"
                ),
                "common_control": "NOT_PROVEN",
            }
        )

    visible_inventory = sum(max(int(value), 0) for value in node_balances.values() if value is not None)
    related_inventory = sum(
        max(int(value), 0) for wallet, value in node_balances.items() if wallet != seed and value is not None
    )
    related_inventory_incomplete = graph_incomplete or any(
        value is None for value in node_balances.values()
    )
    inventory_progress = tracker.incomplete if related_inventory_incomplete else tracker.complete
    inventory_progress(
        "RELATED_INVENTORY",
        "Inventory evidence is incomplete at the downstream coverage boundary"
        if related_inventory_incomplete
        else "Fresh target-token balances reconciled where available",
        count=len(node_balances),
    )

    # Mint-wide history is supplemental for a seeded cluster audit.  It is
    # deliberately requested only after the seed lifecycle, graph expansion,
    # and fresh related-wallet inventory have been established under the same
    # investigation budget.
    if visible_inventory > material_threshold:
        # A material fresh balance already proves NOT_OUT.  Mint-wide history
        # cannot strengthen that conclusion, so skipping it here is safe.
        broad_mint_enrichment_skipped_reason = (
            "FRESH_MATERIAL_INVENTORY_ALREADY_PROVES_NOT_OUT"
        )
    elif investigation and investigation.expansion_exhausted:
        broad_mint_enrichment_skipped_reason = (
            "PROVIDER_BUDGET_EXHAUSTED_BEFORE_REQUIRED_MINT_COVERAGE"
        )
    else:
        # A strongest-case VERIFIED_OUT conclusion still requires mint-wide
        # target-token coverage.  Incomplete provider coverage may weaken a
        # conclusion, never strengthen it.
        def consume_broad_transaction(transaction: dict[str, Any]) -> None:
            signature = history.transaction_signature(transaction)
            if not signature or signature in observed_transaction_signatures:
                return
            observed_transaction_signatures.add(signature)
            autopsy = tx_classifier.autopsy_transaction(transaction)
            if autopsy and not autopsy.failed:
                graph_evidence.ingest(autopsy)

        try:
            broad_raw_batch = provider.stream_token_events(
                metadata.mint, start, end, consume_broad_transaction
            )
        except history.ProviderError as exc:
            broad_raw_batch = _provider_failure_batch(
                "required mint-wide target-token coverage",
                exc,
            )
        broad_batch = _budget_admit_batch(broad_raw_batch, investigation)
        del broad_raw_batch
        broad_mint_coverage = _coverage(broad_batch)
        coverage_rows.append(broad_mint_coverage)
        for transaction in broad_batch.transactions:
            consume_broad_transaction(transaction)
        broad_batch = replace(broad_batch, transactions=[])

    history_incomplete = any(not row.complete for row in coverage_rows) or bool(
        skipped_visible
    ) or bool(
        investigation and investigation.expansion_exhausted
    )
    history_progress = tracker.incomplete if history_incomplete else tracker.complete
    history_progress(
        "WALLET_HISTORY",
        (
            "Seed-wallet history was established, but downstream history/enrichment is incomplete"
            if seed_batch.complete and history_incomplete
            else "Indexed history is incomplete"
            if history_incomplete
            else "Seed and graph history complete; mint-wide coverage not required because fresh material inventory proves NOT_OUT"
            if broad_mint_enrichment_skipped_reason
            == "FRESH_MATERIAL_INVENTORY_ALREADY_PROVES_NOT_OUT"
            else "Seed-first bounded history and required mint-wide coverage fetched"
        ),
        count=len(observed_transaction_signatures),
        metadata={
            "seed_wallet_history_complete": seed_batch.complete,
            "wallets_fetched": len(fetched),
            "deepening_skipped": len(skipped_visible),
            "broad_mint_enrichment_attempted": broad_mint_coverage is not None,
            "broad_mint_enrichment_skipped_reason": broad_mint_enrichment_skipped_reason,
        },
    )
    classification_progress = tracker.incomplete if history_incomplete else tracker.complete
    classification_progress(
        "TRANSACTION_CLASSIFICATION",
        (
            "Seed-wallet classification was established; downstream classification is incomplete"
            if seed_batch.complete and history_incomplete
            else "Transaction classification is incomplete"
            if history_incomplete
            else "Seed and downstream transactions normalized and classified"
        ),
        count=len(graph_evidence),
        metadata={"seed_wallet_classification_complete": seed_batch.complete},
    )

    unresolved_target_inventory = sum(
        max(int(row.get("fresh_current_inventory_raw") or 0), 0)
        for row in verification.get("traced_wallets", [])
        if row.get("wallet") not in depths
        or classifications.get(str(row.get("wallet"))) != "WALLET_LIKE"
    )
    missing_balances = sorted(wallet for wallet, value in node_balances.items() if value is None)
    rpc_telemetry = rpc.telemetry() if hasattr(rpc, "telemetry") else {
        "retry_count": 0,
        "terminal_failure_reason": None,
        "failure_category": None,
    }
    budget_exhausted = bool(investigation and investigation.expansion_exhausted)
    rpc_complete = not missing_balances and not rpc_telemetry.get("terminal_failure_reason")
    coverage_complete = (
        all(row.complete for row in coverage_rows)
        and rpc_complete
        and not skipped_visible
        and not budget_exhausted
    )
    optional_mint_enrichment_complete_or_not_required = bool(
        (broad_mint_coverage is not None and broad_mint_coverage.complete)
        or broad_mint_enrichment_skipped_reason
        == "FRESH_MATERIAL_INVENTORY_ALREADY_PROVES_NOT_OUT"
    )
    downstream_complete = bool(
        all(row.complete for row in graph_coverage_rows)
        and optional_mint_enrichment_complete_or_not_required
        and not any(wallet != seed for wallet in missing_balances)
        and not skipped_visible
        and not budget_exhausted
    )
    unresolved = [*graph_limit_warnings, *evidence.unresolved_relationships]
    unresolved_relationships_truncated = (
        evidence.unresolved_relationships_total > len(evidence.unresolved_relationships)
    )
    if unresolved_relationships_truncated:
        unresolved.append(
            {
                "kind": "UNRESOLVED_RELATIONSHIP_EVIDENCE_TRUNCATED",
                "total": evidence.unresolved_relationships_total,
                "returned": len(evidence.unresolved_relationships),
                "counts_by_classification": evidence.unresolved_relationship_counts_by_classification,
                "counts_by_reason": evidence.unresolved_relationship_counts_by_reason,
                "reason": (
                    "representative unresolved rows are sampled to bound memory; exact unresolved totals and category counts are preserved"
                ),
            }
        )
    if budget_exhausted and not any(row.get("kind") == "PROVIDER_BUDGET_EXHAUSTED" for row in unresolved):
        unresolved.append(
            {
                "kind": "PROVIDER_BUDGET_EXHAUSTED",
                "budget": investigation.terminated_by if investigation else None,
                "reason": investigation.termination_reason if investigation else None,
            }
        )
    if unresolved_target_inventory > material_threshold:
        unresolved.append(
            {
                "kind": "UNRESOLVED_NON_WALLET_TARGET_INVENTORY",
                "amount_raw": unresolved_target_inventory,
                "reason": "material target inventory remains on an evidence-backed transfer branch that is not a wallet-like graph node",
            }
        )
    # Inventory truth and relationship certainty are different questions. A
    # concrete unresolved SOL/other-mint relationship must remain visible, but
    # it must not erase an otherwise complete target-token inventory result.
    # Unknown/target-mint relationship evidence and structural graph limits stay
    # fail-closed for the target conclusion.
    target_blocking_unresolved = [
        *graph_limit_warnings,
        *[
            row
            for row in evidence.unresolved_relationships
            if _unresolved_relationship_blocks_target_status(row, metadata.mint)
        ],
    ]

    if visible_inventory > material_threshold:
        target_cluster_status = "NOT_OUT"
        target_cluster_reasons = [
            "material target-token inventory remains visible in the evidence-backed wallet cluster"
        ]
    elif unresolved_target_inventory > material_threshold:
        target_cluster_status = "UNRESOLVED"
        target_cluster_reasons = [
            "material target inventory remains on a non-wallet or unresolved target-transfer branch"
        ]
    elif not coverage_complete or missing_balances:
        target_cluster_status = "INSUFFICIENT_DATA"
        target_cluster_reasons = [
            (
                "seed wallet lifecycle was established, but target-lineage or fresh-balance coverage is incomplete"
                if seed_conclusion_established
                else "provider coverage or fresh graph-wallet balance coverage is incomplete"
            )
        ]
    elif target_blocking_unresolved:
        target_cluster_status = "UNRESOLVED"
        target_cluster_reasons = [
            "target-token or structural traversal uncertainty remains on an evidence-backed branch"
        ]
    else:
        target_cluster_status = "VERIFIED_OUT"
        target_cluster_reasons = [
            "no material target-token inventory remains in the completely covered evidence-backed cluster"
        ]
        if evidence.unresolved_relationships:
            target_cluster_reasons.append(
                "non-target relationship uncertainty remains and is reported separately"
            )

    if not coverage_complete or missing_balances:
        relationship_status = "INSUFFICIENT_DATA"
        relationship_status_reasons = [
            "relationship evidence cannot be closed because required provider or fresh-balance coverage is incomplete"
        ]
    elif unresolved:
        relationship_status = "UNRESOLVED"
        relationship_status_reasons = [
            "one or more relationship questions or traversal limits remain unresolved; this does not by itself imply remaining target-token inventory"
        ]
    else:
        relationship_status = "RESOLVED_WITHIN_SCOPE"
        relationship_status_reasons = [
            "no unresolved relationship evidence remains within the requested bounded scope; common control is still not proven"
        ]

    # Preserve the existing combined status for backward-compatible consumers.
    # New product/UI consumers should use target_cluster_status for holdings and
    # relationship_status for linkage uncertainty.
    tracker.running("CLUSTER_CONCLUSION", "Applying fail-closed cluster conclusion rules")
    if visible_inventory > material_threshold:
        cluster_status = "NOT_OUT"
        cluster_reasons = ["material target-token inventory remains visible in the bounded wallet-like graph"]
    elif unresolved_target_inventory > material_threshold:
        cluster_status = "UNRESOLVED"
        cluster_reasons = ["material target inventory remains on a non-wallet or unresolved transfer branch"]
    elif not coverage_complete or missing_balances:
        cluster_status = "INSUFFICIENT_DATA"
        cluster_reasons = [
            (
                "seed wallet lifecycle was established, but downstream relationship/inventory coverage is incomplete"
                if seed_conclusion_established
                else "provider coverage or fresh graph-wallet balance coverage is incomplete"
            )
        ]
    elif unresolved:
        cluster_status = "UNRESOLVED"
        cluster_reasons = ["one or more relationship branches reached a limit or remain unresolved"]
    else:
        cluster_status = "VERIFIED_OUT"
        cluster_reasons = ["no material target inventory remains in the completely covered bounded graph"]
    tracker.complete(
        "CLUSTER_CONCLUSION",
        f"Target cluster: {target_cluster_status}; relationship evidence: {relationship_status}; combined: {cluster_status}",
    )

    report = {
        "schema": "jeet-analyzer.cluster-audit.v1",
        "token": asdict(metadata),
        "mint": metadata.mint,
        "seed_wallet": seed,
        "window": {"start": start, "end": end},
        "configuration": {
            "mode": "DEEP_FORENSIC" if config.deep_forensic else "SEED_CENTRIC_CLUSTER_TRACE",
            "graph_depth": config.graph_depth,
            "max_wallets": config.max_wallets,
            "max_graph_wallet_pages": config.max_graph_wallet_pages,
            "funding_lookback_days": config.funding_lookback_days,
            "materiality_inventory_pct": str(config.materiality_inventory_pct),
            "material_threshold_raw": material_threshold,
            "deep_traversal_policy": {
                "first_hop_from_seed": True,
                "target_token_relationships": True,
                "high_signal_relationship_types": (
                    sorted(HIGH_SIGNAL_DEEP_RELATIONSHIP_TYPES) if config.deep_forensic else []
                ),
                "repeated_direct_links": bool(config.deep_forensic),
                "minimum_direct_sol_funding_lamports": (
                    MIN_DEEP_SOL_FUNDING_LAMPORTS if config.deep_forensic else None
                ),
                "weak_links_remain_visible": True,
                "downstream_cross_asset_recursion": bool(config.deep_forensic),
                "seed_full_activity_direct_links": True,
            },
            "investigation_budgets": investigation.to_record()["budget_limits"] if investigation else None,
        },
        "provider_coverage": {
            "complete": coverage_complete,
            "seed_wallet_complete": seed_conclusion_established,
            "downstream_complete": downstream_complete,
            "termination_reason": (
                investigation.termination_reason if investigation else None
            ),
            "optional_mint_enrichment": {
                "attempted": broad_mint_coverage is not None,
                "required_for_conclusion": visible_inventory <= material_threshold,
                "complete": (
                    broad_mint_coverage.complete
                    if broad_mint_coverage is not None
                    else None
                ),
                "skipped_reason": broad_mint_enrichment_skipped_reason,
            },
            "seed_wallet_history": seed_coverage.to_record(),
            "requests": [
                *[row.to_record() for row in coverage_rows],
                {
                    "complete": rpc_complete,
                    "provider": "solana_rpc",
                    "scope": "fresh finalized balances and account classification",
                    "limitation": (
                        rpc_telemetry.get("terminal_failure_reason")
                        or ("one or more fresh balances are unavailable" if missing_balances else "none")
                    ),
                    "pages": 0,
                    "retry_count": int(rpc_telemetry.get("retry_count") or 0),
                    "terminal_failure_reason": rpc_telemetry.get("terminal_failure_reason"),
                    "failure_category": rpc_telemetry.get("failure_category"),
                },
            ],
        },
        "seed_wallet_accounting": {
            "wallet_status": wallet_status,
            "wallet_status_reasons": wallet_reasons,
            "current_target_token_balance_raw": node_balances.get(seed),
            "reconstructed_starting_target_inventory_raw": lifecycle_result.get("reconstructed_starting_inventory_raw"),
            "seed_history_coverage": {
                "complete": seed_batch.complete,
                "scope": seed_batch.coverage_scope,
                "limitation": seed_batch.limitation,
                "failure_category": seed_batch.failure_category,
            },
            "confirmed_sales": {
                "count": len(root_sales),
                "target_token_sold_raw": sum(event.token_amount_raw for event in root_sales),
                "amount_known": seed_batch.complete,
                "count_known": seed_batch.complete,
                "coverage_complete": seed_batch.complete,
                "coverage_scope": seed_batch.coverage_scope,
                "coverage_limitation": seed_batch.limitation,
            },
            "inventory_increases": increases,
            "proceeds": seed_proceeds,
            "verify_exit_evidence": verification,
            "lifecycle": lifecycle_result,
        },
        "wallet_graph": {
            "nodes": nodes,
            "edges": [edge.to_record() for edge in evidence.edges if edge.source in depths and edge.destination in depths],
            "shared_funders": (
                _visible_shared_funders(evidence.shared_funders, depths)
                if config.deep_forensic
                else []
            ),
            "common_control": "NOT_PROVEN",
        },
        "related_target_token_inventory": [row for row in nodes if row["wallet"] != seed],
        "visible_cluster_target_inventory_raw": visible_inventory,
        "visible_related_target_inventory_raw": related_inventory,
        "unresolved_non_wallet_target_inventory_raw": unresolved_target_inventory,
        "excluded_infrastructure": evidence.excluded_infrastructure,
        "excluded_infrastructure_summary": {
            "total": evidence.excluded_infrastructure_total,
            "returned": len(evidence.excluded_infrastructure),
            "truncated": evidence.excluded_infrastructure_total > len(evidence.excluded_infrastructure),
        },
        "ata_rent": evidence.ata_rent,
        "ata_rent_summary": {
            "total": evidence.ata_rent_total,
            "returned": len(evidence.ata_rent),
            "truncated": evidence.ata_rent_total > len(evidence.ata_rent),
        },
        "unresolved_relationships": unresolved,
        "unresolved_relationships_summary": {
            "total": evidence.unresolved_relationships_total,
            "returned_graph_samples": len(evidence.unresolved_relationships),
            "truncated": unresolved_relationships_truncated,
            "counts_by_classification": evidence.unresolved_relationship_counts_by_classification,
            "counts_by_reason": evidence.unresolved_relationship_counts_by_reason,
        },
        "fresh_balance_slots": balance_slots,
        "transaction_autopsy_count": len(graph_evidence),
        "graph_evidence_storage": graph_evidence.telemetry(),
        "request_telemetry": investigation.to_record() if investigation else {},
        "wallet_status": wallet_status,
        "target_cluster_status": target_cluster_status,
        "target_cluster_status_reasons": target_cluster_reasons,
        "relationship_status": relationship_status,
        "relationship_status_reasons": relationship_status_reasons,
        "cluster_status": cluster_status,
        "cluster_status_reasons": cluster_reasons,
        "common_control": "NOT_PROVEN",
        "warnings": [
            "DIRECT_LINK does not mean SAME_OWNER.",
            "DIRECT_SIGNED_SOL_FUNDING does not mean SAME_OWNER.",
            "SHARED_FUNDER does not mean SAME_OWNER.",
            "ATA_PREPARATION proves a direct preparation link, not SAME_OWNER.",
            "COORDINATED_BEHAVIOR proves observed non-market coordination, not SAME_OWNER.",
            "A wallet can be VERIFIED_OUT while its evidence-backed related-wallet cluster remains NOT_OUT.",
        ],
    }
    return report_contract.apply_result_contract(report, tracker)
