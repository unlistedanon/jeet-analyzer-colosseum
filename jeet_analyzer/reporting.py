"""Human and machine-readable report rendering."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .history import display_amount


def write_cluster_receipts(
    report_path: Path, events_path: Path, report: Mapping[str, Any]
) -> None:
    if isinstance(report, dict):
        report["evidence_receipts"] = {
            "result_json": str(report_path),
            "evidence_jsonl": str(events_path),
        }
        progress = report.get("progress")
        if isinstance(progress, dict):
            phases = progress.setdefault("phases", {})
            events = progress.setdefault("events", [])
            phases["RECEIPT_FINALIZATION"] = "complete"
            events.append(
                {
                    "sequence": len(events) + 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    "phase": "RECEIPT_FINALIZATION",
                    "state": "complete",
                    "message": "Machine-readable evidence receipts finalized",
                    "count": 2,
                }
            )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_name(report_path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    temporary.replace(report_path)

    temporary_events = events_path.with_name(events_path.name + ".tmp")
    with temporary_events.open("w", encoding="utf-8", newline="\n") as handle:
        header = {
            "record_type": "metadata",
            "schema": report["schema"],
            "mint": report["mint"],
            "seed_wallet": report["seed_wallet"],
            "window": report["window"],
            "provider_coverage": report["provider_coverage"],
            "request_telemetry": report.get("request_telemetry", {}),
        }
        handle.write(json.dumps(header, sort_keys=True, separators=(",", ":"), default=str) + "\n")
        for autopsy in report.get("transaction_autopsies", []):
            handle.write(
                json.dumps(
                    {"record_type": "transaction_autopsy", **autopsy},
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                + "\n"
            )
        for edge in report.get("wallet_graph", {}).get("edges", []):
            handle.write(
                json.dumps(
                    {"record_type": "relationship_edge", **edge},
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                + "\n"
            )
    temporary_events.replace(events_path)


def print_cluster_audit(report: Mapping[str, Any]) -> None:
    token = report["token"]
    decimals = int(token["decimals"])
    print(f"TOKEN: {token.get('symbol') or 'UNKNOWN'}")
    print(f"MINT: {report['mint']}")
    print(f"TOKEN PROGRAM: {token.get('token_program') or 'UNKNOWN'}")
    print(f"SEED WALLET: {report['seed_wallet']}")
    accounting = report["seed_wallet_accounting"]
    print(f"SEED TARGET_TOKEN_BALANCE: {display_amount(accounting['current_target_token_balance_raw'], decimals)}")
    sales = accounting["confirmed_sales"]
    if sales.get("amount_known") is False or sales.get("coverage_complete") is False:
        print(
            "TARGET_TOKEN_SOLD: UNKNOWN "
            f"(observed minimum {display_amount(sales['target_token_sold_raw'], decimals)} "
            f"across {sales['count']} confirmed sales; seed history incomplete)"
        )
    else:
        print(
            "TARGET_TOKEN_SOLD: "
            + display_amount(sales["target_token_sold_raw"], decimals)
            + f" ({sales['count']} confirmed sales)"
        )
    print(f"WALLET_STATUS: {report['wallet_status']}")
    print(f"HISTORICAL_EXIT_STATUS: {report.get('historical_exit_status', report['wallet_status'])}")
    print(f"CURRENT_WALLET_STATUS: {report.get('current_wallet_status', report['wallet_status'])}")
    print("EVIDENCE-BACKED WALLET GRAPH:")
    edges = report["wallet_graph"]["edges"]
    if not edges:
        print("  NONE")
    for edge in edges:
        if edge["relationship_type"] == "PLAIN_DIRECT_SOL_TRANSFER":
            print("  DIRECT SOL FUNDING:")
            print(f"    {edge['source']} -> {edge['destination']}")
            print(f"    amount_raw: {edge['amount_raw']} lamports")
            print(f"    source signed: {str(bool(edge.get('source_signed'))).lower()}")
            print(f"    source paid fee: {str(bool(edge.get('source_paid_fee'))).lower()}")
            print(f"    market context: {str(bool(edge.get('market_context'))).lower()}")
            print(f"    relationship context: {edge.get('relationship_context') or 'NONE'}")
            print(f"    signature: {edge['signature']}")
            print(f"    timestamp: {edge['timestamp']}")
            print("    COMMON_CONTROL: NOT_PROVEN")
            continue
        print(
            f"  {edge['classification']} source={edge['source']} destination={edge['destination']} "
            f"asset={edge['asset']} amount_raw={edge['amount_raw']} timestamp={edge['timestamp']} "
            f"signature={edge['signature']} common_control=NOT_PROVEN"
        )
    print("RELATED TARGET-TOKEN INVENTORY:")
    related = report["related_target_token_inventory"]
    if not related:
        print("  NONE")
    for row in related:
        print(
            f"  wallet={row['wallet']} depth={row['depth']} "
            f"target_token_balance={display_amount(row['current_target_token_balance_raw'], decimals)} "
            f"relationship_path={' -> '.join(row['relationship_path'])}"
        )
    shared_funders = report["wallet_graph"]["shared_funders"]
    print(f"THIRD-PARTY SHARED-FUNDER GROUPS: {len(shared_funders)}")
    for relationship in shared_funders:
        for pair in relationship.get("recipient_pairs", []):
            print(
                "  SHARED_FUNDER "
                f"wallet_a={pair['wallet_a']} wallet_b={pair['wallet_b']} "
                f"third_party_funder={pair['third_party_funder']} "
                f"supporting_signatures={','.join(pair.get('supporting_signatures', []))} "
                "common_control=NOT_PROVEN"
            )
    print(f"EXCLUDED INFRASTRUCTURE: {len(report['excluded_infrastructure'])}")
    print("CLUSTER SUMMARY:")
    print(f"WALLET_STATUS: {report['wallet_status']}")
    print(f"TARGET_CLUSTER_STATUS: {report.get('target_cluster_status', report['cluster_status'])}")
    print(f"RELATIONSHIP_STATUS: {report.get('relationship_status', 'UNKNOWN')}")
    print(f"CLUSTER_STATUS: {report['cluster_status']}")
    print(
        "VISIBLE_CLUSTER_TARGET_INVENTORY: "
        + display_amount(report["visible_cluster_target_inventory_raw"], decimals)
    )
    print(
        "UNRESOLVED_NON_WALLET_TARGET_INVENTORY: "
        + display_amount(report["unresolved_non_wallet_target_inventory_raw"], decimals)
    )
    print("COMMON_CONTROL=NOT_PROVEN")
