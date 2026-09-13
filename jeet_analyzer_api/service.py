"""Thin in-process adapter around the existing Python CLI and its receipts."""

from __future__ import annotations

import csv
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator

from jeet_analyzer.cli import main as engine_main

from .models import InvestigationMode


class EngineExecutionError(RuntimeError):
    """Raised only when the engine fails to produce a usable evidence artifact."""


@dataclass
class EngineRunResult:
    result: dict[str, Any]
    exit_code: int
    artifact_paths: dict[str, Path] = field(default_factory=dict)
    console_output: str = ""


class EngineAdapter(Protocol):
    def run(
        self,
        mode: InvestigationMode,
        request: Mapping[str, Any],
        output_dir: Path,
    ) -> EngineRunResult: ...


def _common_args(request: Mapping[str, Any]) -> list[str]:
    return [
        "--request-timeout", str(request["request_timeout"]),
        "--provider-retries", str(request["provider_retries"]),
        "--provider-backoff-cap", str(request["provider_backoff_cap"]),
        "--max-rpc-requests", str(request["max_rpc_requests"]),
        "--max-signatures", str(request["max_signatures"]),
        "--max-transactions", str(request["max_transactions"]),
        "--max-estimated-provider-credits", str(
            request.get("max_estimated_provider_credits", 60_000)
        ),
    ]


def _build_argv(mode: InvestigationMode, request: Mapping[str, Any], output_dir: Path) -> list[str]:
    argv = _common_args(request)
    if mode is InvestigationMode.SELLER_SCAN:
        return argv + [
            "scan", "--mint", str(request["mint"]), "--days", str(request["days"]),
            "--max-pages", str(request["max_pages"]), "--output-dir", str(output_dir),
        ]
    if mode is InvestigationMode.WALLET_AUDIT:
        return argv + [
            "wallet", "--mint", str(request["mint"]), "--wallet", str(request["wallet"]),
            "--days", str(request["days"]), "--max-pages", str(request["max_pages"]),
            "--trace-depth", str(request["trace_depth"]), "--output-dir", str(output_dir),
        ]
    cluster_argv = argv + [
        "cluster-audit", "--mint", str(request["mint"]), "--wallet", str(request["wallet"]),
        "--days", str(request["days"]), "--graph-depth", str(request["graph_depth"]),
        "--max-wallets", str(request["max_wallets"]), "--max-pages", str(request["max_pages"]),
        "--funding-lookback-days", str(request["funding_lookback_days"]),
        "--materiality-inventory-pct", str(request["materiality_inventory_pct"]),
        "--output-dir", str(output_dir),
    ]
    if bool(request.get("deep_forensic", False)):
        cluster_argv.append("--deep-forensic")
    return cluster_argv


def _newest(output_dir: Path, pattern: str) -> Path | None:
    candidates = list(output_dir.glob(pattern))
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def _read_jsonl(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EngineExecutionError(f"engine emitted malformed JSONL at line {line_number}") from exc
            if not isinstance(value, dict):
                raise EngineExecutionError(f"engine emitted a non-object JSONL record at line {line_number}")
            records.append(value)
    if not records:
        raise EngineExecutionError("engine emitted an empty JSONL artifact")
    metadata = records[0]
    evidence = records[1:]
    return metadata, evidence


def _sum_event_amount(events: list[dict[str, Any]], event_type: str, wallet: str) -> int:
    return sum(
        int(event.get("token_amount_raw") or 0)
        for event in events
        if event.get("event_type") == event_type and event.get("wallet") == wallet
    )


def _quote_totals(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for event in events:
        amount = event.get("quote_amount_raw")
        if amount is None:
            continue
        asset = str(event.get("quote_mint") or event.get("quote_symbol") or "UNKNOWN")
        row = totals.setdefault(
            asset,
            {
                "amount_raw": 0,
                "decimals": int(event.get("quote_decimals") or 0),
                "symbol": str(event.get("quote_symbol") or "UNKNOWN"),
            },
        )
        row["amount_raw"] = int(row["amount_raw"]) + int(amount)
    return totals


def _wallet_transport(metadata: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    wallet = str(metadata.get("wallet") or "")
    lifecycle = metadata.get("lifecycle") if isinstance(metadata.get("lifecycle"), dict) else {}
    current_balances = metadata.get("current_balances") if isinstance(metadata.get("current_balances"), dict) else {}
    current = current_balances.get(wallet)
    if current is None:
        current = lifecycle.get("current_target_token_balance_raw")
    coverage_complete = bool(metadata.get("coverage_complete"))
    provider_request = {
        "complete": coverage_complete,
        "retry_count": int(metadata.get("provider_retry_count") or 0),
        "terminal_failure_reason": metadata.get("provider_terminal_failure_reason"),
        "failure_category": None if coverage_complete else "COVERAGE_INCOMPLETE",
        "scope": metadata.get("coverage_scope"),
        "limitation": metadata.get("coverage_limitation"),
    }
    sells = [event for event in events if event.get("wallet") == wallet and event.get("event_type") == "SELL"]
    buys = [event for event in events if event.get("wallet") == wallet and event.get("event_type") == "BUY"]
    incoming = [event for event in events if event.get("destination") == wallet and event.get("event_type") == "TRANSFER"]
    outgoing = [event for event in events if event.get("wallet") == wallet and event.get("event_type") == "TRANSFER"]
    sales = {
        "count": len(sells),
        "target_token_sold_raw": _sum_event_amount(events, "SELL", wallet),
        "events": sells,
        "amount_known": coverage_complete,
        "count_known": coverage_complete,
        "coverage_complete": coverage_complete,
        "coverage_scope": metadata.get("coverage_scope"),
        "coverage_limitation": metadata.get("coverage_limitation"),
    }
    inventory_increases = {
        "count": len(buys) + len(incoming),
        "confirmed_buy_count": len(buys),
        "reacquisition_count": int((lifecycle.get("reacquisitions") or {}).get("count") or 0),
        "target_token_reacquired_raw": _sum_event_amount(events, "BUY", wallet)
        + sum(int(event.get("token_amount_raw") or 0) for event in incoming),
        "events": buys + incoming,
        "lifecycle": lifecycle.get("reacquisitions", {}),
    }
    proceeds = _quote_totals(sells)
    reconstructed_start = lifecycle.get("reconstructed_starting_inventory_raw")
    seed_wallet_accounting = {
        "wallet_status": lifecycle.get("current_wallet_status", "INSUFFICIENT_DATA"),
        "current_target_token_balance_raw": current,
        "reconstructed_starting_inventory_raw": reconstructed_start,
        "reconstructed_starting_target_inventory_raw": reconstructed_start,
        "confirmed_sales": sales,
        "inventory_increases": inventory_increases,
        "proceeds": proceeds,
        "lifecycle": dict(lifecycle),
        "coverage": {
            "complete": coverage_complete,
            "scope": metadata.get("coverage_scope"),
            "limitation": metadata.get("coverage_limitation"),
        },
    }
    return {
        "schema": "jeet-analyzer.wallet-ui.v1",
        "schema_version": "1.0.0",
        "token": metadata.get("token") or {"mint": metadata.get("mint"), "symbol": "UNKNOWN", "decimals": 0},
        "mint": metadata.get("mint"),
        "seed_wallet": wallet,
        "window": {"start": metadata.get("start"), "end": metadata.get("end")},
        "historical_exit_status": lifecycle.get("historical_exit_status", "INSUFFICIENT_DATA"),
        "current_wallet_status": lifecycle.get("current_wallet_status", "INSUFFICIENT_DATA"),
        "wallet_status": lifecycle.get("current_wallet_status", "INSUFFICIENT_DATA"),
        "cluster_status": None,
        "current_inventory": {"seed_target_token_balance_raw": current},
        "seed_wallet_accounting": seed_wallet_accounting,
        "lifecycle": dict(lifecycle),
        "sales": sales,
        "buys_reacquisitions": inventory_increases,
        "proceeds": proceeds,
        "transfers": {"incoming": incoming, "outgoing": outgoing},
        "wallet_relationships": [*incoming, *outgoing],
        "shared_funders": [],
        "excluded_infrastructure": [],
        "unresolved_evidence": [] if coverage_complete else [{"kind": "COVERAGE_INCOMPLETE", "reason": metadata.get("coverage_limitation")}],
        "provider_coverage": {"complete": coverage_complete, "requests": [provider_request]},
        "request_telemetry": metadata.get("request_telemetry") or {},
        "progress": {"phases": {}, "events": []},
        "evidence_receipts": {},
        "common_control": "NOT_PROVEN",
        "events": events,
    }


class InProcessCliEngineAdapter:
    """Run the existing public CLI entry point and read its immutable receipts."""

    _engine_lock = threading.Lock()

    def __init__(self, schema_path: Path | None = None) -> None:
        self.schema_path = schema_path or Path(__file__).resolve().parents[1] / "schemas" / "jeet-analyzer-result-v1.schema.json"
        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        self._result_validator = Draft202012Validator(schema)

    def run(
        self,
        mode: InvestigationMode,
        request: Mapping[str, Any],
        output_dir: Path,
    ) -> EngineRunResult:
        output_dir.mkdir(parents=True, exist_ok=False)
        argv = _build_argv(mode, request, output_dir)
        with self._engine_lock:
            exit_code = int(engine_main(argv))
        console_output = ""
        artifact_paths: dict[str, Path] = {}

        if mode is InvestigationMode.SELLER_SCAN:
            result_path = _newest(output_dir, "summary-*.json")
            events_path = _newest(output_dir, "normalized-events-*.jsonl")
            csv_path = _newest(output_dir, "seller-leaderboard-*.csv")
            if result_path is None:
                raise EngineExecutionError(f"seller scan produced no summary receipt (exit code {exit_code})")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            artifact_paths["result_json"] = result_path
            if events_path:
                artifact_paths["evidence_jsonl"] = events_path
                metadata, _events = _read_jsonl(events_path)
                coverage = result.setdefault("provider_coverage", {})
                coverage.setdefault("complete", bool(metadata.get("coverage_complete")))
                coverage.setdefault("scope", metadata.get("coverage_scope"))
                coverage.setdefault("limitation", metadata.get("coverage_limitation"))
                coverage.setdefault(
                    "requests",
                    [{
                        "complete": bool(metadata.get("coverage_complete")),
                        "retry_count": int(metadata.get("provider_retry_count") or 0),
                        "terminal_failure_reason": metadata.get("provider_terminal_failure_reason"),
                        "failure_category": None if metadata.get("coverage_complete") else result.get("provider_coverage", {}).get("failure_category"),
                        "scope": metadata.get("coverage_scope"),
                        "limitation": metadata.get("coverage_limitation"),
                    }],
                )
            if csv_path:
                artifact_paths["seller_csv"] = csv_path
                with csv_path.open("r", encoding="utf-8", newline="") as handle:
                    result.setdefault("seller_rows", list(csv.DictReader(handle)))
        elif mode is InvestigationMode.WALLET_AUDIT:
            events_path = _newest(output_dir, "wallet-events-*.jsonl")
            if events_path is None:
                raise EngineExecutionError(f"wallet audit produced no evidence receipt (exit code {exit_code})")
            metadata, events = _read_jsonl(events_path)
            result = _wallet_transport(metadata, events)
            artifact_paths["evidence_jsonl"] = events_path
        else:
            result_path = _newest(output_dir, "cluster-audit-*.json")
            events_path = _newest(output_dir, "cluster-audit-events-*.jsonl")
            if result_path is None:
                raise EngineExecutionError(f"cluster audit produced no result receipt (exit code {exit_code})")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            errors = sorted(self._result_validator.iter_errors(result), key=lambda error: list(error.path))
            if errors:
                location = ".".join(str(part) for part in errors[0].path) or "result"
                raise EngineExecutionError(f"engine result failed v1 schema validation at {location}: {errors[0].message}")
            artifact_paths["result_json"] = result_path
            if events_path:
                artifact_paths["evidence_jsonl"] = events_path

        return EngineRunResult(result=result, exit_code=exit_code, artifact_paths=artifact_paths, console_output=console_output)
