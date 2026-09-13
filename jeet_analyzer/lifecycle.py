"""Conservative, token-agnostic wallet lifecycle reconstruction.

Only target-token deltas demonstrably owned by the seed wallet participate in
the arithmetic.  Balances belonging to graph neighbors are intentionally never
included in the seed wallet's starting inventory or reacquisition totals.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .history import NormalizedEvent, is_concrete_transfer_event


LIFECYCLE_STATUSES = (
    "VERIFIED_OUT",
    "NOT_OUT",
    "RE_ENTERED",
    "INSUFFICIENT_DATA",
    "UNRESOLVED",
)


@dataclass
class _TimelineRow:
    block_time: int
    timestamp: str
    signature: str
    delta_raw: int = 0
    evidence: list[dict[str, Any]] = field(default_factory=list)


def _root_timeline(events: Iterable[NormalizedEvent], wallet: str) -> list[_TimelineRow]:
    grouped: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for event in events:
        grouped[event.signature].append(event)
    rows: dict[str, _TimelineRow] = {}
    seen: set[tuple[str, str, str | None, int]] = set()
    for signature, transaction_events in grouped.items():
        root_events = [event for event in transaction_events if event.wallet == wallet]
        inbound = [
            event
            for event in transaction_events
            if is_concrete_transfer_event(event) and event.destination == wallet
        ]
        selected: list[tuple[NormalizedEvent, int, str, str | None]] = []
        if root_events:
            for event in root_events:
                source = inbound[0].wallet if event.token_delta_raw > 0 and inbound else None
                selected.append((event, int(event.token_delta_raw), event.event_type, source))
        else:
            for event in inbound:
                selected.append((event, int(event.token_amount_raw), "DIRECT_TRANSFER_IN", event.wallet))
        for event, delta, classification, source in selected:
            identity = (signature, classification, source, delta)
            if identity in seen:
                continue
            seen.add(identity)
            row = rows.setdefault(
                signature,
                _TimelineRow(event.block_time, event.timestamp, signature),
            )
            row.delta_raw += delta
            row.evidence.append(
                {
                    "classification": classification,
                    "delta_raw": delta,
                    "source": source,
                    "evidence": event.evidence,
                }
            )
    return sorted(rows.values(), key=lambda row: (row.block_time, row.signature))


def analyze_wallet_lifecycle(
    events: Iterable[NormalizedEvent],
    wallet: str,
    current_balance_raw: int | None,
    *,
    coverage_complete: bool,
    material_threshold_raw: int = 0,
) -> dict[str, Any]:
    """Reconstruct historical exit and current lifecycle state conservatively."""

    threshold = max(int(material_threshold_raw), 0)
    timeline = _root_timeline(events, wallet)
    if current_balance_raw is None:
        return {
            "historical_exit_status": "INSUFFICIENT_DATA",
            "current_wallet_status": "INSUFFICIENT_DATA",
            "reconstructed_starting_inventory_raw": None,
            "current_target_token_balance_raw": None,
            "material_threshold_raw": threshold,
            "reconciled": False,
            "reconciliation_difference_raw": None,
            "reacquisitions": {"count": 0, "amount_raw": 0, "events": []},
            "exit_evidence": None,
            "unresolved_evidence": ["fresh seed-wallet target-token balance is unavailable"],
            "inventory_scope": "SEED_WALLET_ONLY",
        }

    current = int(current_balance_raw)
    net_delta = sum(row.delta_raw for row in timeline)
    starting = current - net_delta
    running = starting
    exit_evidence: dict[str, Any] | None = None
    exit_block_time: int | None = None
    reacquisitions: list[dict[str, Any]] = []
    unresolved: list[str] = []
    prior_unknown_reduction = False

    if starting < 0:
        unresolved.append("root-wallet starting inventory reconstructed below zero")

    for row in timeline:
        before = running
        running += row.delta_raw
        negative_classes = {
            item["classification"] for item in row.evidence if int(item["delta_raw"]) < 0
        }
        if "UNKNOWN" in negative_classes or "LIQUIDITY_EVENT" in negative_classes:
            prior_unknown_reduction = True
        defensible_reduction = bool(negative_classes.intersection({"SELL", "TRANSFER", "BURN"}))
        if (
            exit_evidence is None
            and before > threshold
            and running <= threshold
            and defensible_reduction
            and not prior_unknown_reduction
        ):
            exit_evidence = {
                "signature": row.signature,
                "timestamp": row.timestamp,
                "balance_before_raw": before,
                "balance_after_raw": running,
                "supporting_classifications": sorted(negative_classes),
            }
            exit_block_time = row.block_time
        if exit_evidence is not None and row.block_time >= int(exit_block_time or 0) and row.delta_raw > 0:
            positive = [item for item in row.evidence if int(item["delta_raw"]) > 0]
            classes = {item["classification"] for item in positive}
            if "BUY" in classes:
                classification = "CONFIRMED_BUY"
            elif "DIRECT_TRANSFER_IN" in classes:
                classification = "DIRECT_TRANSFER_IN"
            else:
                classification = "REACQUISITION"
            reacquisitions.append(
                {
                    "classification": classification,
                    "amount_raw": row.delta_raw,
                    "signature": row.signature,
                    "timestamp": row.timestamp,
                    "sources": sorted(
                        {str(item["source"]) for item in positive if item.get("source")}
                    ),
                }
            )

    difference = current - running
    reconciled = difference == 0 and starting >= 0
    if not reconciled:
        unresolved.append("root-wallet inventory timeline does not reconcile to the fresh balance")
    if prior_unknown_reduction:
        unresolved.append("an unclassified root-wallet reduction can invalidate the historical exit")

    if not coverage_complete:
        historical = "INSUFFICIENT_DATA"
    elif not reconciled or prior_unknown_reduction:
        historical = "UNRESOLVED"
    elif exit_evidence is not None:
        historical = "VERIFIED_OUT"
    elif starting <= threshold and not timeline:
        historical = "INSUFFICIENT_DATA"
    else:
        historical = "NOT_OUT"

    material_reacquisition = sum(max(int(row["amount_raw"]), 0) for row in reacquisitions) > threshold
    if current > threshold:
        if historical == "VERIFIED_OUT" and material_reacquisition:
            current_status = "RE_ENTERED"
        else:
            current_status = "NOT_OUT"
    elif historical == "VERIFIED_OUT" and reconciled:
        current_status = "VERIFIED_OUT"
    elif not coverage_complete:
        current_status = "INSUFFICIENT_DATA"
    elif unresolved:
        current_status = "UNRESOLVED"
    else:
        current_status = "NOT_OUT"

    return {
        "historical_exit_status": historical,
        "current_wallet_status": current_status,
        "reconstructed_starting_inventory_raw": starting if starting >= 0 else None,
        "current_target_token_balance_raw": current,
        "material_threshold_raw": threshold,
        "reconciled": reconciled,
        "reconciliation_difference_raw": difference,
        "reacquisitions": {
            "count": len(reacquisitions),
            "amount_raw": sum(int(row["amount_raw"]) for row in reacquisitions),
            "events": reacquisitions,
        },
        "exit_evidence": exit_evidence,
        "unresolved_evidence": unresolved,
        "inventory_scope": "SEED_WALLET_ONLY",
    }
