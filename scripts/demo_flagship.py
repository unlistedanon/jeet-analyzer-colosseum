"""Deterministic, provider-free Colosseum flagship demo.

The input is presentation-only evidence containing aliases rather than live
identifiers. Conclusions are reconstructed from the evidence at runtime; the
fixture does not contain expected lifecycle or cluster statuses.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = REPOSITORY_ROOT / "demo" / "flagship-evidence.v1.json"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "build" / "eternal-week1" / "flagship-result.json"
DEFAULT_FRONTEND_OUTPUT = REPOSITORY_ROOT / "frontend" / "public" / "demo" / "flagship-result.json"
TRANSACTION_ALIAS = re.compile(r"TX-[0-9]{3}")
WALLET_ALIAS = re.compile(r"Wallet [A-Z]+")
PROGRESS_PHASES = (
    "TOKEN_RESOLUTION",
    "SELLER_ANALYSIS",
    "WALLET_HISTORY",
    "TRANSACTION_CLASSIFICATION",
    "RELATIONSHIP_GRAPH",
    "FUNDING_ANCESTRY",
    "RELATED_INVENTORY",
    "CLUSTER_CONCLUSION",
    "RECEIPT_FINALIZATION",
)


class DemoEvidenceError(ValueError):
    """Raised when sanitized evidence cannot support a safe conclusion."""


def load_evidence(path: Path = DEFAULT_EVIDENCE) -> dict[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise DemoEvidenceError("demo evidence must be a JSON object")
    return record


def _integer(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DemoEvidenceError(f"{key} must be a non-negative integer")
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise DemoEvidenceError("evidence timestamp must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DemoEvidenceError("evidence timestamp is malformed") from exc


def _validate_alias(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise DemoEvidenceError(f"{label} must use a public alias")
    return value


def _progress(coverage: Mapping[str, Any]) -> dict[str, Any]:
    incomplete = {"RELATIONSHIP_GRAPH", "FUNDING_ANCESTRY", "RELATED_INVENTORY"}
    phases = {
        phase: ("incomplete" if phase in incomplete and not coverage["downstream_complete"] else "complete")
        for phase in PROGRESS_PHASES
    }
    events = [
        {
            "sequence": index,
            "timestamp": "2026-08-30T04:11:09Z",
            "phase": phase,
            "state": state,
            "message": (
                "Replayed from alias-only evidence; bounded downstream coverage remains incomplete."
                if state == "incomplete"
                else "Completed from persisted alias-only evidence without provider access."
            ),
        }
        for index, (phase, state) in enumerate(phases.items(), 1)
    ]
    return {"phases": phases, "events": events}


def build_demo_result(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstruct a UI/result-contract record without provider or wallet access."""

    source = deepcopy(dict(evidence))
    if source.get("schema") != "jeet-analyzer.public-evidence.v1":
        raise DemoEvidenceError("unsupported demo evidence schema")
    token = source.get("token") or {}
    if token.get("label") != "[REDACTED TOKEN]":
        raise DemoEvidenceError("demo token identity must remain redacted")
    decimals = _integer(token, "decimals")
    wallet = _validate_alias(source.get("primary_wallet"), WALLET_ALIAS, "primary wallet")
    threshold = _integer(source, "material_threshold_raw")
    accounting = source.get("accounting_evidence") or {}
    current_raw = _integer(accounting, "fresh_current_inventory_raw")
    buy_total_raw = _integer(accounting, "confirmed_buy_total_raw")
    sold_raw = _integer(accounting, "confirmed_sold_raw")
    transfers = list(accounting.get("target_token_transfers") or [])
    secondary_increases = list(accounting.get("secondary_wallet_target_increases") or [])
    burns = list(accounting.get("burns") or [])
    if transfers or secondary_increases or burns:
        raise DemoEvidenceError("flagship evidence contains an unexpected target-token movement")

    timeline = sorted(
        [dict(row) for row in source.get("evidence_timeline") or []],
        key=lambda row: (_timestamp(row.get("timestamp")), str(row.get("transaction") or "")),
    )
    if not timeline:
        raise DemoEvidenceError("demo evidence timeline is empty")
    seen_transactions: set[str] = set()
    for row in timeline:
        transaction = _validate_alias(row.get("transaction"), TRANSACTION_ALIAS, "transaction")
        if transaction in seen_transactions:
            raise DemoEvidenceError("duplicate transaction alias in evidence timeline")
        seen_transactions.add(transaction)
        _integer(row, "amount_raw")
        if row.get("event_type") not in {"SELL", "POST_EXIT_BUY"}:
            raise DemoEvidenceError("unsupported evidence event type")

    sale_events = [row for row in timeline if row["event_type"] == "SELL"]
    post_exit_buys = [row for row in timeline if row["event_type"] == "POST_EXIT_BUY"]
    if sum(_integer(row, "amount_raw") for row in sale_events) != sold_raw:
        raise DemoEvidenceError("sale evidence does not reconcile with confirmed sold total")
    post_exit_raw = sum(_integer(row, "amount_raw") for row in post_exit_buys)

    exit_evidence = source.get("historical_exit_evidence") or {}
    exit_transaction = _validate_alias(exit_evidence.get("transaction"), TRANSACTION_ALIAS, "exit transaction")
    exit_time = _timestamp(exit_evidence.get("timestamp"))
    balance_before = _integer(exit_evidence, "balance_before_raw")
    balance_after = _integer(exit_evidence, "balance_after_raw")
    matching_exit = [
        row for row in sale_events
        if row["transaction"] == exit_transaction
        and _timestamp(row["timestamp"]) == exit_time
        and _integer(row, "amount_raw") == balance_before
    ]

    coverage = source.get("coverage") or {}
    seed_complete = coverage.get("seed_wallet_complete") is True
    downstream_complete = coverage.get("downstream_complete") is True
    historical_exit_status = (
        "VERIFIED_OUT"
        if seed_complete
        and len(matching_exit) == 1
        and exit_evidence.get("event_type") == "SELL"
        and balance_after <= threshold
        else "INSUFFICIENT_DATA"
    )
    post_exit_ordered = bool(post_exit_buys) and all(_timestamp(row["timestamp"]) > exit_time for row in post_exit_buys)
    if current_raw > threshold:
        current_wallet_status = (
            "RE_ENTERED"
            if historical_exit_status == "VERIFIED_OUT"
            and post_exit_ordered
            and post_exit_raw > threshold
            and post_exit_raw == current_raw
            else "NOT_OUT"
        )
    else:
        current_wallet_status = historical_exit_status

    incoming_raw = sum(_integer(row, "amount_raw") for row in transfers if row.get("direction") == "IN")
    outgoing_raw = sum(_integer(row, "amount_raw") for row in transfers if row.get("direction") == "OUT")
    burned_raw = sum(_integer(row, "amount_raw") for row in burns)
    reconstructed_start_raw = current_raw - buy_total_raw - incoming_raw + sold_raw + outgoing_raw + burned_raw
    if reconstructed_start_raw < 0:
        raise DemoEvidenceError("reconstructed starting inventory is negative")
    reconciled_current = reconstructed_start_raw + buy_total_raw + incoming_raw - sold_raw - outgoing_raw - burned_raw
    if reconciled_current != current_raw:
        raise DemoEvidenceError("inventory evidence does not reconcile")

    related = []
    related_total_raw = 0
    for row in source.get("related_wallet_inventory") or []:
        alias = _validate_alias(row.get("wallet"), WALLET_ALIAS, "related wallet")
        balance = _integer(row, "current_target_token_balance_raw")
        related_total_raw += balance
        related.append(
            {
                "wallet": alias,
                "depth": _integer(row, "depth"),
                "current_target_token_balance_raw": balance,
                "relationship_path": [wallet, alias],
                "status": "VISIBLE",
                "evidence_strength": "EVIDENCE_BACKED_ALIAS",
                "common_control": "NOT_PROVEN",
            }
        )
    visible_cluster_raw = current_raw + related_total_raw
    cluster_status = (
        "NOT_OUT"
        if visible_cluster_raw > threshold
        else "VERIFIED_OUT" if downstream_complete and historical_exit_status == "VERIFIED_OUT" else "INSUFFICIENT_DATA"
    )

    relationships = []
    for row in source.get("relationship_evidence") or []:
        item = dict(row)
        _validate_alias(item.get("transaction"), TRANSACTION_ALIAS, "relationship transaction")
        _validate_alias(item.get("source"), WALLET_ALIAS, "relationship source")
        _validate_alias(item.get("destination"), WALLET_ALIAS, "relationship destination")
        if item.get("common_control") != "NOT_PROVEN":
            raise DemoEvidenceError("relationship evidence may not claim common control")
        relationships.append(item)
    if source.get("common_control_proof"):
        raise DemoEvidenceError("demo proof model does not support common-control attribution")

    original_cost = source.get("original_live_cost") or {}
    estimated_credits = _integer(original_cost, "estimated_helius_credits")
    hard_ceiling = _integer(original_cost, "hard_estimated_credit_ceiling")
    if original_cost.get("cost_is_estimated") is not True or estimated_credits > hard_ceiling:
        raise DemoEvidenceError("original provider cost evidence is invalid")

    seed_request = {
        "complete": seed_complete,
        "scope": str(coverage.get("seed_scope") or "indexed seed-wallet evidence"),
        "limitation": str(coverage.get("seed_limitation") or "none"),
        "retry_count": 0,
        "terminal_failure_reason": None,
        "failure_category": None,
    }
    downstream_request = {
        "complete": downstream_complete,
        "scope": "bounded relationship expansion",
        "limitation": str(coverage.get("downstream_limitation") or "none"),
        "retry_count": 0,
        "terminal_failure_reason": (
            None if downstream_complete else str(coverage.get("downstream_limitation") or "bounded coverage incomplete")
        ),
        "failure_category": None if downstream_complete else "PROVIDER_BUDGET_EXHAUSTED",
    }
    zero_telemetry = {
        "total_provider_requests": 0,
        "rpc_requests": 0,
        "das_requests": 0,
        "rpc_requests_by_method": {},
        "das_requests_by_method": {},
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_hits_by_namespace": {},
        "cache_misses_by_namespace": {},
        "provider_requests_avoided": 0,
        "duplicate_evidence_observations": 0,
        "wallet_traversals_suppressed": 0,
        "deduplicated_skipped_requests": 0,
        "deduplicated_skipped_requests_deprecated": True,
        "retry_attempts": 0,
        "wallets_traversed": 0,
        "signatures_examined": 0,
        "transactions_fetched": 0,
        "pages_fetched": 0,
        "estimated_provider_credits": 0,
        "estimated_provider_credits_by_method": {},
        "estimated_provider_credit_ceiling": 1,
        "estimated_provider_credit_cost_is_authoritative": False,
        "estimated_provider_credit_model": "offline replay; no provider requests",
        "budget_limits": {
            "max_rpc_requests": 1,
            "max_signatures": 1,
            "max_transactions": 1,
            "max_estimated_provider_credits": 1,
        },
        "terminated_by_budget": None,
        "termination_reason": None,
        "provider_credit_cost": 0,
        "provider_credit_cost_note": "Offline replay made zero provider requests.",
    }
    relationship_nodes = [{"wallet": wallet, "node_kind": "seed", "current_target_token_balance_raw": current_raw}]
    relationship_nodes.extend(
        {"wallet": row["wallet"], "node_kind": "wallet", "current_target_token_balance_raw": row["current_target_token_balance_raw"]}
        for row in related
    )
    reacquisition_events = [
        {
            "signature": row["transaction"],
            "timestamp": row["timestamp"],
            "classification": "CONFIRMED_BUY",
            "event_type": "BUY",
            "token_amount_raw": row["amount_raw"],
        }
        for row in post_exit_buys
    ]
    sale_records = [
        {
            "signature": row["transaction"],
            "timestamp": row["timestamp"],
            "event_type": "SELL",
            "token_amount_raw": row["amount_raw"],
        }
        for row in sale_events
    ]
    return {
        "schema": "jeet-analyzer.result.v1",
        "schema_version": "1.0.0",
        "token": {"mint": "[REDACTED TOKEN]", "symbol": "[REDACTED TOKEN]", "name": None, "decimals": decimals, "token_program": None},
        "mint": "[REDACTED TOKEN]",
        "seed_wallet": wallet,
        "window": {"start": timeline[0]["timestamp"], "end": timeline[-1]["timestamp"]},
        "historical_exit_status": historical_exit_status,
        "current_wallet_status": current_wallet_status,
        "wallet_status": current_wallet_status,
        "cluster_status": cluster_status,
        "current_inventory": {
            "seed_target_token_balance_raw": current_raw,
            "visible_cluster_target_inventory_raw": visible_cluster_raw,
            "visible_related_target_inventory_raw": related_total_raw,
        },
        "seed_wallet_accounting": {
            "current_target_token_balance_raw": current_raw,
            "reconstructed_starting_target_inventory_raw": reconstructed_start_raw,
            "confirmed_sales": {
                "count": len(sale_records),
                "target_token_sold_raw": sold_raw,
                "largest_sale_raw": max(row["token_amount_raw"] for row in sale_records),
                "events": sale_records,
                "amount_known": True,
                "count_known": True,
                "coverage_complete": seed_complete,
                "coverage_scope": seed_request["scope"],
                "coverage_limitation": seed_request["limitation"],
            },
            "inventory_increases": {
                "events": reacquisition_events,
                "counts": {"CONFIRMED_BUY": len(reacquisition_events), "REACQUISITION": len(reacquisition_events)},
                "confirmed_buy_count": len(reacquisition_events),
                "reacquisition_count": len(reacquisition_events),
            },
            "proceeds": {},
            "lifecycle": {
                "historical_exit_status": historical_exit_status,
                "current_wallet_status": current_wallet_status,
                "reconstructed_starting_inventory_raw": reconstructed_start_raw,
                "reacquisitions": {"count": len(reacquisition_events), "amount_raw": post_exit_raw, "events": reacquisition_events},
                "reconciled": True,
                "reconciliation_difference_raw": 0,
            },
            "verify_exit_evidence": {
                "incoming_transfers": {"count": 0, "amount_raw": incoming_raw},
                "outgoing_transfers": {"count": 0, "amount_raw": outgoing_raw},
                "linked_wallet_increases": {"count": len(secondary_increases), "amount_raw": 0},
                "burns": {"count": len(burns), "amount_raw": burned_raw},
                "reconciliation": {"root_equation_balanced": True},
            },
        },
        "sales": {
            "count": len(sale_records),
            "target_token_sold_raw": sold_raw,
            "largest_sale_raw": max(row["token_amount_raw"] for row in sale_records),
            "events": sale_records,
            "amount_known": True,
            "count_known": True,
            "coverage_complete": seed_complete,
            "coverage_scope": seed_request["scope"],
            "coverage_limitation": seed_request["limitation"],
        },
        "buys_reacquisitions": {
            "events": reacquisition_events,
            "counts": {"CONFIRMED_BUY": len(reacquisition_events), "REACQUISITION": len(reacquisition_events)},
            "confirmed_buy_count": len(reacquisition_events),
            "reacquisition_count": len(reacquisition_events),
            "target_token_reacquired_raw": post_exit_raw,
        },
        "transfers": {"incoming": [], "outgoing": [], "secondary_wallet_target_increases": []},
        "proceeds": {},
        "wallet_relationships": relationships,
        "wallet_graph": {"nodes": relationship_nodes, "edges": relationships, "shared_funders": [], "common_control": "NOT_PROVEN"},
        "shared_funders": [],
        "related_target_token_inventory": related,
        "excluded_infrastructure": [],
        "unresolved_evidence": [
            {
                "kind": "BOUNDED_DOWNSTREAM_COVERAGE",
                "reason": downstream_request["limitation"],
                "count": _integer(coverage, "unresolved_evidence_count"),
            }
        ],
        "provider_coverage": {
            "complete": seed_complete and downstream_complete,
            "seed_wallet_complete": seed_complete,
            "downstream_complete": downstream_complete,
            "seed_wallet_history": seed_request,
            "requests": [seed_request, downstream_request],
        },
        "request_telemetry": zero_telemetry,
        "progress": _progress(coverage),
        "evidence_receipts": {"public_evidence": "demo/flagship-evidence.v1.json"},
        "common_control": "NOT_PROVEN",
        "case_question": "The jeet sold, but is he really out?",
        "demo_summary": {
            "primary_wallet": wallet,
            "confirmed_sold_raw": sold_raw,
            "exit_sale_raw": balance_before,
            "exit_balance_after_raw": balance_after,
            "post_exit_reacquired_raw": post_exit_raw,
            "current_inventory_raw": current_raw,
            "genuine_target_token_transfer_count": len(transfers),
            "secondary_wallet_target_increase_count": len(secondary_increases),
            "estimated_original_provider_credits": estimated_credits,
            "hard_estimated_credit_ceiling": hard_ceiling,
            "offline_replay_provider_calls": 0,
            "seed_coverage": seed_request["limitation"],
            "downstream_coverage": downstream_request["limitation"],
        },
        "redaction": source.get("redaction") or {},
        "events": timeline,
    }


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.name


def _write_json(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the alias-only Jeet Analyzer flagship demo offline")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--frontend-output", type=Path, default=DEFAULT_FRONTEND_OUTPUT)
    parser.add_argument("--stdout-json", action="store_true")
    args = parser.parse_args(argv)
    result = build_demo_result(load_evidence(args.evidence))
    _write_json(args.output, result)
    _write_json(args.frontend_output, result)
    summary = result["demo_summary"]
    decimals = int(result["token"]["decimals"])
    scale = 10 ** decimals
    print("JEET ANALYZER / ETERNAL WEEK 1 / OFFLINE DEMO")
    print(f"QUESTION: {result['case_question']}")
    print(f"TOKEN: {result['mint']}")
    print(f"PRIMARY_WALLET: {result['seed_wallet']}")
    print(f"CONFIRMED_SOLD: {summary['confirmed_sold_raw'] / scale:.{decimals}f}")
    print(f"HISTORICAL_EXIT_STATUS: {result['historical_exit_status']}")
    print(f"CURRENT_WALLET_STATUS: {result['current_wallet_status']}")
    print(f"CLUSTER_STATUS: {result['cluster_status']}")
    print(f"CURRENT_INVENTORY: {summary['current_inventory_raw'] / scale:.{decimals}f}")
    print(f"GENUINE_TARGET_TOKEN_TRANSFERS: {summary['genuine_target_token_transfer_count']}")
    print(f"SECONDARY_WALLET_TARGET_INCREASES: {summary['secondary_wallet_target_increase_count']}")
    print(f"COMMON_CONTROL: {result['common_control']}")
    print(f"ORIGINAL_LIVE_PROVIDER_CREDITS_ESTIMATED: {summary['estimated_original_provider_credits']}")
    print(f"OFFLINE_REPLAY_PROVIDER_CALLS: {summary['offline_replay_provider_calls']}")
    print(f"RESULT: {_relative(args.output)}")
    print(f"FRONTEND_RESULT: {_relative(args.frontend_output)}")
    if args.stdout_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
