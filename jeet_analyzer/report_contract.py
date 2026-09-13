"""Stable v1 result contract for CLI receipts and future UI consumers."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from .control import assess_control_likelihood
from .history import TokenMetadata
from .investigation import InvestigationContext
from .progress import ProgressTracker, safe_message


RESULT_SCHEMA = "jeet-analyzer.result.v1"
RESULT_SCHEMA_VERSION = "1.0.0"


def apply_result_contract(report: dict[str, Any], progress: ProgressTracker | None = None) -> dict[str, Any]:
    accounting = report.get("seed_wallet_accounting", {})
    wallet_graph = report.get("wallet_graph", {})
    lifecycle = accounting.get("lifecycle", {})
    report["schema"] = RESULT_SCHEMA
    report["schema_version"] = RESULT_SCHEMA_VERSION
    report["historical_exit_status"] = lifecycle.get(
        "historical_exit_status", report.get("wallet_status", "INSUFFICIENT_DATA")
    )
    report["current_wallet_status"] = lifecycle.get(
        "current_wallet_status", report.get("wallet_status", "INSUFFICIENT_DATA")
    )
    report["current_inventory"] = {
        "seed_target_token_balance_raw": accounting.get("current_target_token_balance_raw"),
        "visible_cluster_target_inventory_raw": report.get("visible_cluster_target_inventory_raw"),
        "visible_related_target_inventory_raw": report.get("visible_related_target_inventory_raw"),
    }
    report["sales"] = accounting.get("confirmed_sales", {"count": 0, "target_token_sold_raw": 0})
    report["buys_reacquisitions"] = accounting.get("inventory_increases", {"events": [], "counts": {}})
    report["proceeds"] = accounting.get("proceeds", {})
    report["wallet_relationships"] = wallet_graph.get("edges", [])
    report["shared_funders"] = wallet_graph.get("shared_funders", [])

    control_assessments = assess_control_likelihood(report["wallet_relationships"])
    wallet_graph["control_assessments"] = control_assessments
    report["wallet_graph"] = wallet_graph
    report["control_assessments"] = control_assessments
    report["control_summary"] = {
        "pairs_assessed": len(control_assessments),
        "likely_common_control_pairs": sum(
            1
            for row in control_assessments
            if row.get("control_assessment") == "LIKELY_COMMON_CONTROL"
        ),
        "strongly_linked_pairs": sum(
            1 for row in control_assessments if row.get("link_status") == "STRONGLY_LINKED"
        ),
        "proof_status": "NOT_PROVEN",
    }

    report["unresolved_evidence"] = report.get("unresolved_relationships", [])
    report["progress"] = progress.to_record() if progress is not None else report.get(
        "progress", {"phases": {}, "events": []}
    )
    report.setdefault("evidence_receipts", {})
    if not report.get("request_telemetry"):
        report["request_telemetry"] = InvestigationContext().to_record()
    report.setdefault(
        "target_cluster_status",
        report.get("cluster_status", "INSUFFICIENT_DATA"),
    )
    if "relationship_status" not in report:
        if report.get("target_cluster_status") == "INSUFFICIENT_DATA":
            report["relationship_status"] = "INSUFFICIENT_DATA"
        elif report.get("unresolved_relationships"):
            report["relationship_status"] = "UNRESOLVED"
        else:
            report["relationship_status"] = "RESOLVED_WITHIN_SCOPE"
    report.setdefault("target_cluster_status_reasons", report.get("cluster_status_reasons", []))
    report.setdefault("relationship_status_reasons", [])
    report["common_control"] = "NOT_PROVEN"
    return report


def failure_result(
    *,
    mint: str,
    seed_wallet: str,
    start: int,
    end: int,
    reason: object,
    provider_telemetry: Mapping[str, Any] | None,
    progress: ProgressTracker,
    request_telemetry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    safe_reason = safe_message(reason)
    telemetry = dict(provider_telemetry or {})
    request = {
        "complete": False,
        "provider": "helius",
        "scope": "cluster audit",
        "limitation": safe_reason,
        "pages": 0,
        "retry_count": int(telemetry.get("retry_count") or 0),
        "terminal_failure_reason": safe_message(
            telemetry.get("terminal_failure_reason") or safe_reason
        ),
        "failure_category": telemetry.get("failure_category") or "PROVIDER_ERROR",
    }
    report: dict[str, Any] = {
        "token": asdict(TokenMetadata(mint, "UNKNOWN", None, 0, 0)),
        "mint": mint,
        "seed_wallet": seed_wallet,
        "window": {"start": start, "end": end},
        "configuration": {},
        "provider_coverage": {
            "complete": False,
            "seed_wallet_complete": False,
            "downstream_complete": False,
            "seed_wallet_history": request,
            "requests": [request],
        },
        "seed_wallet_accounting": {
            "wallet_status": "INSUFFICIENT_DATA",
            "current_target_token_balance_raw": None,
            "confirmed_sales": {
                "count": 0,
                "target_token_sold_raw": 0,
                "amount_known": False,
                "count_known": False,
                "coverage_complete": False,
                "coverage_scope": "cluster audit provider failure",
                "coverage_limitation": safe_reason,
            },
            "inventory_increases": {"events": [], "counts": {}},
            "proceeds": {},
            "lifecycle": {
                "historical_exit_status": "INSUFFICIENT_DATA",
                "current_wallet_status": "INSUFFICIENT_DATA",
            },
        },
        "wallet_graph": {
            "nodes": [],
            "edges": [],
            "shared_funders": [],
            "control_assessments": [],
            "common_control": "NOT_PROVEN",
        },
        "related_target_token_inventory": [],
        "visible_cluster_target_inventory_raw": None,
        "visible_related_target_inventory_raw": None,
        "unresolved_non_wallet_target_inventory_raw": None,
        "excluded_infrastructure": [],
        "unresolved_relationships": [{"kind": "PROVIDER_FAILURE", "reason": safe_reason}],
        "transaction_autopsies": [],
        "wallet_status": "INSUFFICIENT_DATA",
        "cluster_status": "INSUFFICIENT_DATA",
        "cluster_status_reasons": [safe_reason],
        "request_telemetry": dict(request_telemetry or telemetry.get("investigation") or {}),
    }
    return apply_result_contract(report, progress)
