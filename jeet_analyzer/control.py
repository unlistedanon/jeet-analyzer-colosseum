"""Explainable pair-level common-control likelihood over accepted graph evidence.

This layer never identifies a human. It aggregates already-accepted wallet
relationship evidence and answers a narrower product question: does the
observed on-chain pattern make common control a reasonable risk assessment?
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from .models import RelationshipEdge


CONTROL_SIGNAL_CLASSES = frozenset({"CO_SIGN", "ATA_PREPARATION", "ATA_TOKEN_COMPOSITE"})
RESOURCE_SIGNAL_CLASSES = frozenset({"TOKEN_FLOW", "SOL_FUNDING", "ATA_TOKEN_COMPOSITE"})


EdgeLike = RelationshipEdge | Mapping[str, Any]


def _value(edge: EdgeLike, name: str, default: Any = None) -> Any:
    if isinstance(edge, Mapping):
        return edge.get(name, default)
    return getattr(edge, name, default)


def _pair(edge: EdgeLike) -> tuple[str, str]:
    return tuple(sorted((str(_value(edge, "source") or ""), str(_value(edge, "destination") or ""))))


def _evidence_class(edge: EdgeLike) -> str | None:
    relationship_type = str(_value(edge, "relationship_type") or "")
    if relationship_type == "NON_MARKET_CO_SIGNER":
        return "CO_SIGN"
    if relationship_type == "ATA_PREPARATION":
        return "ATA_PREPARATION"
    if relationship_type == "CONFIRMED_DIRECT_LINK":
        # ATA creation plus token movement in one transaction is deliberately
        # one composite class, not two independent signals.
        return "ATA_TOKEN_COMPOSITE"
    if relationship_type == "DIRECT_TOKEN_TRANSFER":
        return "TOKEN_FLOW"
    if relationship_type == "PLAIN_DIRECT_SOL_TRANSFER":
        return "SOL_FUNDING"
    return None


def assess_control_likelihood(edges: Sequence[EdgeLike]) -> list[dict[str, Any]]:
    """Aggregate accepted edges into pair-level, non-identity risk assessments.

    `DELEGATED_TOKEN_AUTHORITY` is intentionally not a control-signal class;
    it remains relationship evidence only until a separate control-evidence
    decision is made. `LIKELY_COMMON_CONTROL` requires independent evidence classes observed in
    at least two distinct transactions, including both a coordination/control
    signal and an economic/resource signal. Same-transaction facts are never
    double-counted into independence.
    """

    grouped: dict[tuple[str, str], list[EdgeLike]] = defaultdict(list)
    for edge in edges:
        source = str(_value(edge, "source") or "")
        destination = str(_value(edge, "destination") or "")
        if not source or not destination or source == destination:
            continue
        evidence_class = _evidence_class(edge)
        if evidence_class is not None:
            grouped[_pair(edge)].append(edge)

    assessments: list[dict[str, Any]] = []
    for (wallet_a, wallet_b), pair_edges in sorted(grouped.items()):
        classes = sorted(
            {
                evidence_class
                for edge in pair_edges
                if (evidence_class := _evidence_class(edge)) is not None
            }
        )
        signatures = sorted(
            {
                str(signature)
                for edge in pair_edges
                if (signature := _value(edge, "signature"))
            }
        )
        has_control_signal = bool(set(classes) & CONTROL_SIGNAL_CLASSES)
        has_resource_signal = bool(set(classes) & RESOURCE_SIGNAL_CLASSES)
        independent_signal_count = len(classes)
        distinct_transaction_count = len(signatures)

        likely = bool(
            independent_signal_count >= 2
            and distinct_transaction_count >= 2
            and has_control_signal
            and has_resource_signal
        )

        if likely:
            control_assessment = "LIKELY_COMMON_CONTROL"
            link_status = "STRONGLY_LINKED"
            reason = (
                "multiple independent on-chain evidence classes across separate non-market transactions "
                "combine a coordination/control signal with an economic/resource signal"
            )
        elif independent_signal_count >= 2 and distinct_transaction_count >= 2:
            control_assessment = "NOT_ESTABLISHED"
            link_status = "STRONGLY_LINKED"
            reason = (
                "multiple independent on-chain relationship classes were observed across separate transactions, "
                "but the evidence mix does not meet the common-control likelihood rule"
            )
        elif "CO_SIGN" in classes:
            control_assessment = "COORDINATED_BEHAVIOR"
            link_status = "LINKED"
            reason = (
                "non-market co-signing proves coordination, but there is not enough independent evidence "
                "to assess likely common control"
            )
        else:
            control_assessment = "NOT_ESTABLISHED"
            link_status = "LINKED"
            reason = "accepted on-chain relationship evidence links the wallets, but common control is not established"

        edge_records = []
        for edge in sorted(
            pair_edges,
            key=lambda row: (
                int(_value(row, "block_time") or 0),
                str(_value(row, "signature") or ""),
                str(_value(row, "relationship_type") or ""),
            ),
        ):
            edge_records.append(
                {
                    "signature": str(_value(edge, "signature") or ""),
                    "timestamp": str(_value(edge, "timestamp") or ""),
                    "relationship_type": str(_value(edge, "relationship_type") or ""),
                    "classification": str(_value(edge, "classification") or ""),
                    "evidence_class": _evidence_class(edge),
                    "source": str(_value(edge, "source") or ""),
                    "destination": str(_value(edge, "destination") or ""),
                    "asset": str(_value(edge, "asset") or ""),
                    "amount_raw": int(_value(edge, "amount_raw") or 0),
                    "why_linked": str(
                        _value(edge, "why_linked")
                        or _value(edge, "reason")
                        or "accepted relationship evidence"
                    ),
                }
            )

        assessments.append(
            {
                "wallet_a": wallet_a,
                "wallet_b": wallet_b,
                "link_status": link_status,
                "control_assessment": control_assessment,
                "common_control": "NOT_PROVEN",
                "legal_identity": "NOT_ESTABLISHED",
                "independent_evidence_classes": classes,
                "independent_signal_count": independent_signal_count,
                "supporting_signatures": signatures,
                "distinct_transaction_count": distinct_transaction_count,
                "has_control_signal": has_control_signal,
                "has_resource_signal": has_resource_signal,
                "reason": reason,
                "risk_interpretation": (
                    "Treat LIKELY_COMMON_CONTROL as same-controller risk for exposure analysis, not proof of legal identity or beneficial ownership."
                    if likely
                    else "Relationship evidence should inform exposure analysis without assuming one human controls both wallets."
                ),
                "evidence": edge_records,
            }
        )

    return assessments
