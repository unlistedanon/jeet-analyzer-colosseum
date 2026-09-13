"""Offline cluster-receipt replay and presentation-only public case export.

Internal receipts retain real identifiers.  Public exports are rebuilt from a
strict allowlist and use only wallet/transaction aliases; they are never fed
back into classification, graph construction, caching, or reconciliation.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from . import history, lifecycle


_IDENTIFIER_RE = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,100}(?![1-9A-HJ-NP-Za-km-z])")
_URL_RE = re.compile(r"(?:https?|wss?)://", re.IGNORECASE)


def _metadata(record: Mapping[str, Any]) -> history.TokenMetadata:
    return history.TokenMetadata(
        mint=str(record["mint"]),
        symbol=str(record.get("symbol") or "UNKNOWN"),
        name=record.get("name"),
        decimals=int(record["decimals"]),
        supply=int(record.get("supply") or 0),
        price_usd=(Decimal(str(record["price_usd"])) if record.get("price_usd") is not None else None),
        metadata_source=str(record.get("metadata_source") or "persisted receipt"),
        token_program=record.get("token_program"),
    )

def _target_transfers(autopsy: Mapping[str, Any], mint: str) -> list[Mapping[str, Any]]:
    return [row for row in autopsy.get("token_transfers", []) if row.get("mint") == mint]


def _known_signatures(report: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    accounting = report.get("seed_wallet_accounting") or {}
    increases = (report.get("buys_reacquisitions") or {}).get("events") or (
        accounting.get("inventory_increases") or {}
    ).get("events", [])
    buys = {
        str(row["signature"])
        for row in increases
        if row.get("signature") and row.get("classification") in {"CONFIRMED_BUY", "BUY"}
    }
    sales = {
        str(row["signature"])
        for row in (report.get("proceeds") or accounting.get("proceeds") or {}).get("transactions", [])
        if row.get("signature")
    }
    return buys, sales


def _transfer_event(row: Mapping[str, Any], mint: str) -> history.NormalizedEvent:
    amount = int(row["amount_raw"])
    return history.NormalizedEvent(
        str(row["timestamp"]),
        int(row["block_time"]),
        str(row["signature"]),
        mint,
        "TRANSFER",
        str(row["source_owner"]),
        -amount,
        amount,
        destination=str(row["destination_owner"]),
        source_token_account=str(row["source_token_account"]),
        destination_token_account=str(row["destination_token_account"]),
        evidence="replayed concrete SPL transfer from persisted transaction autopsy",
    )


def _replay_events(report: Mapping[str, Any]) -> list[history.NormalizedEvent]:
    root = str(report["seed_wallet"])
    mint = str(report["mint"])
    buy_signatures, sale_signatures = _known_signatures(report)
    events: list[history.NormalizedEvent] = []
    for autopsy in report.get("transaction_autopsies", []):
        signature = str(autopsy.get("signature") or "")
        transfers = _target_transfers(autopsy, mint)
        incoming = [
            row for row in transfers if row.get("destination_owner") == root and row.get("source_owner") != root
        ]
        outgoing = [
            row for row in transfers if row.get("source_owner") == root and row.get("destination_owner") != root
        ]
        delta_record = (((autopsy.get("token_owner_deltas") or {}).get(root) or {}).get(mint) or {})
        delta = int(delta_record.get("delta_raw") or 0)
        if delta > 0:
            if signature in buy_signatures:
                events.append(
                    history.NormalizedEvent(
                        str(autopsy["timestamp"]), int(autopsy["block_time"]), signature, mint,
                        "BUY", root, delta, delta,
                        evidence="replayed confirmed market buy from persisted transaction autopsy",
                    )
                )
            elif not incoming:
                events.append(
                    history.NormalizedEvent(
                        str(autopsy["timestamp"]), int(autopsy["block_time"]), signature, mint,
                        "UNKNOWN", root, delta, delta,
                        evidence="persisted positive owner delta lacks confirmed buy or concrete transfer attribution",
                    )
                )
        elif delta < 0:
            amount = -delta
            if signature in sale_signatures:
                kind = "SELL"
                destination = source_account = destination_account = None
            elif len(outgoing) == 1 and int(outgoing[0].get("amount_raw") or 0) == amount:
                kind = "TRANSFER"
                destination = str(outgoing[0]["destination_owner"])
                source_account = str(outgoing[0]["source_token_account"])
                destination_account = str(outgoing[0]["destination_token_account"])
            elif "burn" in json.dumps(autopsy.get("instruction_summaries", [])).lower():
                kind = "BURN"
                destination = source_account = destination_account = None
            else:
                kind = "UNKNOWN"
                destination = source_account = destination_account = None
            events.append(
                history.NormalizedEvent(
                    str(autopsy["timestamp"]), int(autopsy["block_time"]), signature, mint,
                    kind, root, delta, amount,
                    destination=destination,
                    source_token_account=source_account,
                    destination_token_account=destination_account,
                    evidence="replayed root owner delta from persisted transaction autopsy",
                )
            )
        for transfer in transfers:
            if transfer.get("source_owner") != root:
                events.append(_transfer_event(transfer, mint))
    return sorted(events, key=lambda row: (row.block_time, row.signature, row.wallet or ""))


def replay_cluster_receipt(report: Mapping[str, Any], *, source_bytes: bytes | None = None) -> dict[str, Any]:
    """Recompute seed lifecycle/exit evidence without any provider object."""

    source = deepcopy(dict(report))
    root = str(source["seed_wallet"])
    mint = str(source["mint"])
    events = _replay_events(source)
    root_events = [row for row in events if row.wallet == root]
    accounting = source.get("seed_wallet_accounting") or {}
    current = int(accounting.get("current_target_token_balance_raw") or 0)
    prior_lifecycle = accounting.get("lifecycle") or {}
    threshold = int(prior_lifecycle.get("material_threshold_raw") or 1)
    coverage = (source.get("provider_coverage") or {}).get("seed_wallet_history") or {}
    life = lifecycle.analyze_wallet_lifecycle(
        root_events, root, current,
        coverage_complete=bool(coverage.get("complete")),
        material_threshold_raw=threshold,
    )
    balances: dict[str, int | None] = {root: current}
    for row in source.get("related_target_token_inventory", []):
        if row.get("wallet"):
            value = row.get("current_target_token_balance_raw")
            balances[str(row["wallet"])] = None if value is None else int(value)
    window = source.get("window") or {}
    verification = history.verify_exit(
        events,
        _metadata(source["token"]),
        root,
        balances,
        coverage_complete=bool(coverage.get("complete")),
        coverage_scope=str(coverage.get("scope") or "persisted seed-wallet history"),
        coverage_limitation=str(coverage.get("limitation") or "none"),
        trace_depth=3,
        window_start=(int(window["start"]) if window.get("start") is not None else None),
        window_end=(int(window["end"]) if window.get("end") is not None else None),
    )
    return {
        "schema": "jeet-analyzer.cluster-replay.v1",
        "source_receipt_sha256": hashlib.sha256(source_bytes or json.dumps(source, sort_keys=True).encode()).hexdigest(),
        "provider_calls_during_replay": 0,
        "historical_transactions_reacquired": 0,
        "source_transaction_autopsies_used": len(source.get("transaction_autopsies", [])),
        "mint": mint,
        "token": source["token"],
        "seed_wallet": root,
        "window": window,
        "root_events": [row.to_record() for row in root_events],
        "lifecycle": life,
        "verification": verification,
        "cluster_status": source.get("cluster_status"),
        "target_cluster_status": source.get("target_cluster_status", source.get("cluster_status")),
        "relationship_status": source.get("relationship_status"),
        "provider_coverage": source.get("provider_coverage"),
        "source_request_telemetry": source.get("request_telemetry"),
        "relationships": source.get("wallet_relationships") or [],
        "related_inventory": source.get("related_target_token_inventory") or [],
        "proceeds": source.get("proceeds") or {},
        "common_control": "NOT_PROVEN",
        "unresolved_evidence_count": len(source.get("unresolved_evidence") or []),
    }


def _token_amount(raw: Any, decimals: int) -> str:
    return format(Decimal(int(raw or 0)) / (Decimal(10) ** decimals), "f")


def build_public_case(
    replay: Mapping[str, Any], *, estimated_provider_credits: int, credit_ceiling: int
) -> dict[str, Any]:
    """Build an alias-only, allowlisted public artifact from an internal replay."""

    root = str(replay["seed_wallet"])
    decimals = int((replay.get("token") or {}).get("decimals") or 0)
    aliases = {root: "Wallet A"}

    def wallet_alias(value: Any) -> str | None:
        if not value:
            return None
        key = str(value)
        if key not in aliases:
            index = len(aliases)
            label = ""
            while True:
                label = chr(ord("A") + index % 26) + label
                index = index // 26 - 1
                if index < 0:
                    break
            aliases[key] = f"Wallet {label}"
        return aliases[key]

    relationships = sorted(
        replay.get("relationships") or [],
        key=lambda row: (int(row.get("block_time") or 0), str(row.get("signature") or "")),
    )
    related = replay.get("related_inventory") or []
    for row in relationships:
        wallet_alias(row.get("source")); wallet_alias(row.get("destination"))
    for row in related:
        wallet_alias(row.get("wallet"))
        for item in row.get("relationship_path") or []:
            wallet_alias(item)

    life = replay["lifecycle"]
    verification = replay["verification"]
    post_exit_signatures = {row["signature"] for row in (life.get("reacquisitions") or {}).get("events", [])}
    evidence_events = [
        row for row in replay.get("root_events", [])
        if row.get("event_type") == "SELL" or row.get("signature") in post_exit_signatures
    ]
    signature_times: dict[str, tuple[int, str]] = {}
    for row in evidence_events:
        signature_times[str(row["signature"])] = (int(row.get("block_time") or 0), str(row.get("timestamp") or ""))
    for row in relationships:
        if row.get("signature"):
            signature_times[str(row["signature"])] = (int(row.get("block_time") or 0), str(row.get("timestamp") or ""))
    transaction_aliases = {
        signature: f"TX-{index:03d}"
        for index, (signature, _value) in enumerate(sorted(signature_times.items(), key=lambda item: (item[1], item[0])), 1)
    }

    timeline = [
        {
            "transaction": transaction_aliases[str(row["signature"])],
            "timestamp": row.get("timestamp"),
            "event_type": "POST_EXIT_BUY" if row.get("signature") in post_exit_signatures else "SELL",
            "amount": _token_amount(row.get("token_amount_raw"), decimals),
        }
        for row in sorted(evidence_events, key=lambda row: (int(row.get("block_time") or 0), str(row.get("signature"))))
    ]
    public_relationships = []
    for row in relationships:
        asset = row.get("asset")
        public_relationships.append(
            {
                "source": wallet_alias(row.get("source")),
                "destination": wallet_alias(row.get("destination")),
                "relationship_type": row.get("relationship_type") or row.get("classification"),
                "relationship_context": row.get("relationship_context"),
                "asset": "TARGET_TOKEN" if asset == replay.get("mint") else ("SOL" if asset == "SOL" else "OTHER_ASSET"),
                "amount_raw": row.get("amount_raw"),
                "timestamp": row.get("timestamp"),
                "transaction": transaction_aliases.get(str(row.get("signature"))),
                "source_signed": row.get("source_signed"),
                "source_paid_fee": row.get("source_paid_fee"),
                "market_context": row.get("market_context"),
                "common_control": "NOT_PROVEN",
            }
        )
    public_related = [
        {
            "wallet": wallet_alias(row.get("wallet")),
            "depth": row.get("depth"),
            "current_target_token_balance": _token_amount(row.get("current_target_token_balance_raw"), decimals),
            "relationship_path": [wallet_alias(item) for item in row.get("relationship_path") or []],
            "common_control": "NOT_PROVEN",
        }
        for row in related
    ]
    coverage = replay.get("provider_coverage") or {}
    telemetry = replay.get("source_request_telemetry") or {}
    result = {
        "schema": "jeet-analyzer.public-case.v1",
        "token": "[REDACTED TOKEN]",
        "primary_seller": "Wallet A",
        "question": f"Wallet A sold {_token_amount(verification['confirmed_sales']['amount_raw'], decimals)} tokens. But is it actually out?",
        "what_most_dashboards_show": "Wallet A sold its position.",
        "what_jeet_analyzer_checks": [
            "remaining inventory", "transfers", "downstream recipients", "reacquisition",
            "secondary-wallet activity", "evidence coverage",
        ],
        "verdict": {
            "historical_exit_status": life.get("historical_exit_status"),
            "current_wallet_status": life.get("current_wallet_status"),
            "target_cluster_status": replay.get("target_cluster_status", replay.get("cluster_status")),
            "relationship_status": replay.get("relationship_status"),
            "cluster_status": replay.get("cluster_status"),
            "common_control": "NOT_PROVEN",
        },
        "inventory_reconciliation": {
            "reconstructed_starting_inventory": _token_amount(life.get("reconstructed_starting_inventory_raw"), decimals),
            "confirmed_sold": _token_amount(verification["confirmed_sales"]["amount_raw"], decimals),
            "confirmed_buy_total": _token_amount(sum(int(row.get("token_amount_raw") or 0) for row in replay.get("root_events", []) if row.get("event_type") == "BUY"), decimals),
            "post_exit_reacquired": _token_amount((life.get("reacquisitions") or {}).get("amount_raw"), decimals),
            "current_inventory": _token_amount(verification.get("fresh_current_inventory_raw"), decimals),
            "incoming_transfers": verification.get("incoming_transfers"),
            "outgoing_transfers": verification.get("outgoing_transfers"),
            "burns": verification.get("burns"),
            "market_settlement_transfers_excluded_from_transfer_accounting": verification.get("excluded_market_settlement_transfers"),
            "reconciled": bool(life.get("reconciled") and verification.get("reconciliation", {}).get("root_equation_balanced")),
        },
        "evidence_timeline": timeline,
        "relationship_evidence": public_relationships,
        "related_inventory": public_related,
        "coverage": {
            "overall_complete": coverage.get("complete"),
            "seed_wallet_complete": coverage.get("seed_wallet_complete"),
            "downstream_complete": coverage.get("downstream_complete"),
            "terminated_by_budget": telemetry.get("terminated_by_budget"),
            "unresolved_evidence_count": replay.get("unresolved_evidence_count"),
        },
        "cost": {
            "estimated_provider_credits_used": int(estimated_provider_credits),
            "hard_estimated_credit_ceiling": int(credit_ceiling),
            "offline_replay_provider_calls": int(replay.get("provider_calls_during_replay") or 0),
            "cost_is_estimated": True,
        },
        "redaction": {
            "token_identity": "REDACTED",
            "wallets": "ALIASED",
            "transactions": "ALIASED",
            "explorer_urls": "OMITTED",
            "metadata_and_social_identity": "OMITTED",
            "internal_evidence_mutated": False,
        },
    }
    assert_public_safe(result)
    return result


def assert_public_safe(result: Mapping[str, Any]) -> None:
    text = json.dumps(result, sort_keys=True)
    if _IDENTIFIER_RE.search(text):
        raise ValueError("public case contains an address-like or signature-like identifier")
    if _URL_RE.search(text):
        raise ValueError("public case contains a URL")
    if result.get("token") != "[REDACTED TOKEN]" or result.get("primary_seller") != "Wallet A":
        raise ValueError("public case is missing required token/wallet aliases")
    if (result.get("verdict") or {}).get("common_control") != "NOT_PROVEN":
        raise ValueError("public case must not infer common control")


def public_case_markdown(case: Mapping[str, Any]) -> str:
    verdict = case["verdict"]
    inventory = case["inventory_reconciliation"]
    coverage = case["coverage"]
    cost = case["cost"]
    return "\n".join(
        [
            "# THE JEET SOLD, BUT IS HE REALLY OUT?",
            "",
            "## Question",
            "",
            str(case["question"]),
            "",
            "## What most dashboards show",
            "",
            str(case["what_most_dashboards_show"]),
            "",
            "## What Jeet Analyzer checks",
            "",
            *[f"- {item}" for item in case["what_jeet_analyzer_checks"]],
            "",
            "## Verdict",
            "",
            f"Historical exit: **{verdict['historical_exit_status']}**  ",
            f"Current wallet: **{verdict['current_wallet_status']}**  ",
            f"Bounded cluster: **{verdict['cluster_status']}**  ",
            "Common control: **NOT_PROVEN**",
            "",
            "## Evidence",
            "",
            f"- Reconstructed start: {inventory['reconstructed_starting_inventory']}",
            f"- Confirmed sold: {inventory['confirmed_sold']}",
            f"- Post-exit reacquired: {inventory['post_exit_reacquired']}",
            f"- Current inventory: {inventory['current_inventory']}",
            f"- True outgoing target-token transfers: {inventory['outgoing_transfers']['count']}",
            f"- True incoming target-token transfers: {inventory['incoming_transfers']['count']}",
            f"- Reconciled: {str(inventory['reconciled']).lower()}",
            "",
            "## Coverage",
            "",
            f"Seed wallet complete: {str(coverage['seed_wallet_complete']).lower()}  ",
            f"Downstream complete: {str(coverage['downstream_complete']).lower()}  ",
            f"Budget boundary: {coverage['terminated_by_budget'] or 'none'}",
            "",
            "## Cost",
            "",
            f"Estimated provider credits used: {cost['estimated_provider_credits_used']} / {cost['hard_estimated_credit_ceiling']}  ",
            f"Offline replay provider calls: {cost['offline_replay_provider_calls']}",
            "",
            "SELL does not equal EXIT. The evidence shows Wallet A exited, then re-entered.",
            "",
        ]
    )
