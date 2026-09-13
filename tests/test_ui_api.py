from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

from fastapi.testclient import TestClient

from jeet_analyzer_api.app import create_app
from jeet_analyzer_api.models import ClusterAuditRequest, InvestigationMode
from jeet_analyzer_api.service import EngineRunResult, InProcessCliEngineAdapter, _build_argv, _wallet_transport
API_MINT = "11111111111111111111111111111111"
API_WALLET = "4vJ9JU1bJJE96FWSJKvHsmmF5xmD3e3shZV4L6jH4b5"


def cluster_result(*, complete: bool = True, cluster_status: str = "NOT_OUT") -> dict[str, Any]:
    return {
        "schema": "jeet-analyzer.result.v1",
        "schema_version": "1.0.0",
        "token": {"mint": API_MINT, "symbol": "TGT", "name": "Synthetic Target", "decimals": 6},
        "mint": API_MINT,
        "seed_wallet": API_WALLET,
        "historical_exit_status": "NOT_OUT" if complete else "INSUFFICIENT_DATA",
        "current_wallet_status": "NOT_OUT",
        "cluster_status": cluster_status,
        "current_inventory": {"seed_target_token_balance_raw": 25_000_000},
        "sales": {"count": 1, "target_token_sold_raw": 5_000_000},
        "buys_reacquisitions": {"events": [], "counts": {}},
        "proceeds": {"SOL": {"amount_raw": 2_000_000_000, "decimals": 9, "symbol": "SOL"}},
        "wallet_relationships": [],
        "shared_funders": [],
        "excluded_infrastructure": [],
        "unresolved_evidence": [] if complete else [{"kind": "PROVIDER_BUDGET_EXHAUSTED"}],
        "provider_coverage": {
            "complete": complete,
            "requests": [{"complete": complete, "retry_count": 0, "terminal_failure_reason": None, "failure_category": None}],
        },
        "request_telemetry": {
            "total_provider_requests": 4,
            "rpc_requests": 3,
            "das_requests": 1,
            "rpc_requests_by_method": {"getTransaction": 1},
            "das_requests_by_method": {"getAsset": 1},
            "cache_hits": 1,
            "cache_misses": 3,
            "cache_hits_by_namespace": {"transaction": 1},
            "cache_misses_by_namespace": {"transaction": 1},
            "provider_requests_avoided": 1,
            "duplicate_evidence_observations": 0,
            "wallet_traversals_suppressed": 0,
            "deduplicated_skipped_requests": 1,
            "deduplicated_skipped_requests_deprecated": True,
            "retry_attempts": 0,
            "wallets_traversed": 1,
            "signatures_examined": 1,
            "transactions_fetched": 1,
            "pages_fetched": 1,
            "estimated_provider_credits": 13,
            "estimated_provider_credits_by_method": {
                "das.getAsset": 10,
                "rpc.getTransaction": 1,
                "rpc.otherStandardReads": 2,
            },
            "estimated_provider_credit_ceiling": 60_000,
            "estimated_provider_credit_cost_is_authoritative": False,
            "estimated_provider_credit_model": "deterministic fixture estimate",
            "budget_limits": {
                "max_rpc_requests": 500,
                "max_signatures": 10_000,
                "max_transactions": 5_000,
                "max_estimated_provider_credits": 60_000,
            },
            "terminated_by_budget": None if complete else "max_transactions",
            "termination_reason": None if complete else "PROVIDER_BUDGET_EXHAUSTED",
        },
        "progress": {
            "phases": {"TOKEN_RESOLUTION": "complete", "CLUSTER_CONCLUSION": "complete" if complete else "incomplete"},
            "events": [],
        },
        "evidence_receipts": {},
        "common_control": "NOT_PROVEN",
    }


class FakeAdapter:
    def __init__(self, *, result: dict[str, Any] | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[InvestigationMode, dict[str, Any]]] = []

    def run(self, mode: InvestigationMode, request: Mapping[str, Any], output_dir: Path) -> EngineRunResult:
        self.calls.append((mode, dict(request)))
        if self.error:
            raise self.error
        output_dir.mkdir(parents=True)
        receipt = self.result or cluster_result()
        result_path = output_dir / "result.json"
        events_path = output_dir / "events.jsonl"
        result_path.write_text(json.dumps(receipt), encoding="utf-8")
        events_path.write_text(json.dumps({"record_type": "metadata", "mint": API_MINT}) + "\n", encoding="utf-8")
        return EngineRunResult(receipt, 0, {"result_json": result_path, "evidence_jsonl": events_path})


class UiApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def client(self, adapter: FakeAdapter) -> TestClient:
        return TestClient(create_app(engine_adapter=adapter, receipt_root=self.root))

    def test_api_accepts_operator_credit_ceiling_above_default(self):
        request = ClusterAuditRequest(
            mint=API_MINT,
            wallet=API_WALLET,
            max_estimated_provider_credits=60_001,
        )
        self.assertEqual(request.max_estimated_provider_credits, 60_001)

    def test_health_declares_read_only_capability(self):
        response = self.client(FakeAdapter()).get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["capability"], "read-only")

    def test_invalid_mint_is_rejected_before_engine_execution(self):
        adapter = FakeAdapter()
        response = self.client(adapter).post("/api/v1/investigations/seller-scan", json={"mint": "not-an-address"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(adapter.calls, [])

    def test_seller_scan_forwards_bounded_controls(self):
        adapter = FakeAdapter(result={"schema": "jeet-analyzer.scan.v1", "status": "COMPLETE", "sellers": []})
        client = self.client(adapter)
        response = client.post(
            "/api/v1/investigations/seller-scan",
            json={"mint": API_MINT, "max_rpc_requests": 44, "max_signatures": 55, "max_transactions": 33},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(adapter.calls[0][0], InvestigationMode.SELLER_SCAN)
        self.assertEqual(adapter.calls[0][1]["max_rpc_requests"], 44)
        identifier = response.json()["id"]
        finished = client.get(f"/api/v1/investigations/{identifier}")
        self.assertEqual(finished.json()["state"], "complete")

    def test_incomplete_scan_progress_is_typed_and_does_not_claim_seller_completion(self):
        adapter = FakeAdapter(result={
            "schema": "jeet-analyzer.scan.v1",
            "status": "INSUFFICIENT_DATA",
            "provider_coverage": {"complete": False},
            "sellers": [],
        })
        client = self.client(adapter)
        submitted = client.post("/api/v1/investigations/seller-scan", json={"mint": API_MINT})
        view = client.get(f"/api/v1/investigations/{submitted.json()['id']}").json()
        self.assertEqual(view["progress"]["phases"]["SELLER_ANALYSIS"], "incomplete")
        self.assertTrue(all("sequence" in event and "timestamp" in event for event in view["progress"]["events"]))

    def test_wallet_requires_a_public_wallet(self):
        adapter = FakeAdapter()
        response = self.client(adapter).post("/api/v1/investigations/wallet-audit", json={"mint": API_MINT})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(adapter.calls, [])

    def test_wallet_request_reaches_wallet_engine_mode(self):
        adapter = FakeAdapter(result={"schema": "jeet-analyzer.wallet-ui.v1", "current_wallet_status": "NOT_OUT"})
        client = self.client(adapter)
        response = client.post(
            "/api/v1/investigations/wallet-audit",
            json={"mint": API_MINT, "wallet": API_WALLET, "trace_depth": 1},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(adapter.calls[0][0], InvestigationMode.WALLET_AUDIT)
        view = client.get(f"/api/v1/investigations/{response.json()['id']}").json()
        self.assertEqual(view["result"]["current_wallet_status"], "NOT_OUT")

    def test_cluster_request_forwards_graph_bounds(self):
        adapter = FakeAdapter()
        client = self.client(adapter)
        response = client.post(
            "/api/v1/investigations/cluster-audit",
            json={"mint": API_MINT, "wallet": API_WALLET, "graph_depth": 1, "max_wallets": 3, "max_pages": 2},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(adapter.calls[0][0], InvestigationMode.CLUSTER_AUDIT)
        self.assertEqual(adapter.calls[0][1]["max_wallets"], 3)
        self.assertEqual(adapter.calls[0][1]["max_pages"], 2)

    def test_cluster_receipt_preserves_incomplete_coverage_and_common_control(self):
        adapter = FakeAdapter(result=cluster_result(complete=False, cluster_status="INSUFFICIENT_DATA"))
        client = self.client(adapter)
        submitted = client.post(
            "/api/v1/investigations/cluster-audit",
            json={"mint": API_MINT, "wallet": API_WALLET, "graph_depth": 2, "max_wallets": 4},
        )
        identifier = submitted.json()["id"]
        view = client.get(f"/api/v1/investigations/{identifier}").json()
        self.assertEqual(view["state"], "complete")
        self.assertFalse(view["result"]["provider_coverage"]["complete"])
        self.assertEqual(view["result"]["cluster_status"], "INSUFFICIENT_DATA")
        self.assertEqual(view["result"]["common_control"], "NOT_PROVEN")
        self.assertEqual(view["result"]["request_telemetry"]["terminated_by_budget"], "max_transactions")

    def test_cluster_transport_preserves_verified_seed_status_when_downstream_is_incomplete(self):
        result = cluster_result(complete=False, cluster_status="INSUFFICIENT_DATA")
        result.update({
            "historical_exit_status": "VERIFIED_OUT",
            "current_wallet_status": "VERIFIED_OUT",
        })
        result["provider_coverage"].update({
            "seed_wallet_complete": True,
            "downstream_complete": False,
        })
        client = self.client(FakeAdapter(result=result))
        submitted = client.post(
            "/api/v1/investigations/cluster-audit",
            json={"mint": API_MINT, "wallet": API_WALLET},
        )
        view = client.get(f"/api/v1/investigations/{submitted.json()['id']}").json()["result"]
        self.assertEqual(view["historical_exit_status"], "VERIFIED_OUT")
        self.assertEqual(view["current_wallet_status"], "VERIFIED_OUT")
        self.assertEqual(view["cluster_status"], "INSUFFICIENT_DATA")
        self.assertTrue(view["provider_coverage"]["seed_wallet_complete"])
        self.assertEqual(view["common_control"], "NOT_PROVEN")

    def test_receipt_and_evidence_are_retrievable(self):
        client = self.client(FakeAdapter())
        submitted = client.post(
            "/api/v1/investigations/cluster-audit",
            json={"mint": API_MINT, "wallet": API_WALLET},
        )
        identifier = submitted.json()["id"]
        receipt = client.get(f"/api/v1/investigations/{identifier}/receipt")
        evidence = client.get(f"/api/v1/investigations/{identifier}/events")
        artifact = client.get(f"/api/v1/investigations/{identifier}/artifacts/evidence_jsonl")
        self.assertEqual(receipt.status_code, 200)
        self.assertEqual(receipt.json()["common_control"], "NOT_PROVEN")
        self.assertEqual(evidence.json()["records"][0]["record_type"], "metadata")
        self.assertEqual(artifact.headers["content-type"], "application/x-ndjson")

    def test_engine_boundary_redacts_credential_bearing_errors(self):
        client = self.client(FakeAdapter(error=RuntimeError("failed https://rpc.invalid/?api-key=supersecret")))
        submitted = client.post(
            "/api/v1/investigations/cluster-audit",
            json={"mint": API_MINT, "wallet": API_WALLET},
        )
        view = client.get(f"/api/v1/investigations/{submitted.json()['id']}").json()
        self.assertEqual(view["state"], "error")
        self.assertNotIn("supersecret", view["error"])
        self.assertIn("[URL_REDACTED]", view["error"])

    def test_cli_argument_mapping_uses_existing_commands(self):
        request = {
            "mint": API_MINT, "wallet": API_WALLET, "days": 14, "max_pages": 7,
            "request_timeout": 9, "provider_retries": 1, "provider_backoff_cap": 3,
            "max_rpc_requests": 20, "max_signatures": 30, "max_transactions": 10,
            "graph_depth": 2, "max_wallets": 8, "funding_lookback_days": 90,
            "materiality_inventory_pct": 0.1,
        }
        argv = _build_argv(InvestigationMode.CLUSTER_AUDIT, request, self.root)
        self.assertIn("cluster-audit", argv)
        self.assertEqual(argv[argv.index("--graph-depth") + 1], "2")
        self.assertEqual(argv[argv.index("--max-rpc-requests") + 1], "20")
        self.assertEqual(
            argv[argv.index("--max-estimated-provider-credits") + 1], "60000"
        )

    def test_wallet_transport_keeps_lifecycle_separate_from_current_balance(self):
        metadata = {
            "mint": API_MINT,
            "wallet": API_WALLET,
            "token": {"mint": API_MINT, "symbol": "TGT", "decimals": 6},
            "coverage_complete": True,
            "current_balances": {API_WALLET: 10_000},
            "lifecycle": {
                "historical_exit_status": "VERIFIED_OUT",
                "current_wallet_status": "RE_ENTERED",
                "reconstructed_starting_inventory_raw": 2_523_470_290_239,
                "reacquisitions": {"count": 1},
            },
        }
        events = [{"record_type": "event", "wallet": API_WALLET, "event_type": "BUY", "token_amount_raw": 10_000}]
        result = _wallet_transport(metadata, events)
        self.assertEqual(result["historical_exit_status"], "VERIFIED_OUT")
        self.assertEqual(result["current_wallet_status"], "RE_ENTERED")
        self.assertEqual(result["current_inventory"]["seed_target_token_balance_raw"], 10_000)
        self.assertEqual(result["lifecycle"]["reconstructed_starting_inventory_raw"], 2_523_470_290_239)
        self.assertEqual(result["seed_wallet_accounting"]["reconstructed_starting_inventory_raw"], 2_523_470_290_239)
        self.assertEqual(result["seed_wallet_accounting"]["lifecycle"], metadata["lifecycle"])
        self.assertEqual(result["common_control"], "NOT_PROVEN")

    def test_wallet_receipt_transport_and_api_preserve_verified_exit_accounting(self):
        reconstructed_start = 2_523_470_290_239

        def fake_engine(argv):
            output_dir = Path(argv[argv.index("--output-dir") + 1])
            header = {
                "record_type": "metadata",
                "schema": "jeet-analyzer.events.v1",
                "mint": API_MINT,
                "wallet": API_WALLET,
                "coverage_complete": True,
                "coverage_scope": "synthetic complete wallet history and fresh current balances",
                "coverage_limitation": None,
                "token": {"mint": API_MINT, "symbol": "TST", "decimals": 6},
                "lifecycle": {
                    "historical_exit_status": "VERIFIED_OUT",
                    "current_wallet_status": "VERIFIED_OUT",
                    "reconstructed_starting_inventory_raw": reconstructed_start,
                    "current_target_token_balance_raw": 0,
                    "reconciled": True,
                    "reacquisitions": {"count": 0, "events": []},
                },
            }
            sales = [
                {"record_type": "event", "wallet": API_WALLET, "event_type": "SELL", "token_amount_raw": 1_500_000_000_000},
                {"record_type": "event", "wallet": API_WALLET, "event_type": "SELL", "token_amount_raw": 1_023_470_290_239},
            ]
            payload = "\n".join(json.dumps(record) for record in [header, *sales]) + "\n"
            (output_dir / "wallet-events-lifecycle-regression.jsonl").write_text(payload, encoding="utf-8")
            return 0

        request = {
            "mint": API_MINT,
            "wallet": API_WALLET,
            "days": 5,
            "max_pages": 2,
            "trace_depth": 1,
            "request_timeout": 5,
            "provider_retries": 0,
            "provider_backoff_cap": 1,
            "max_rpc_requests": 10,
            "max_signatures": 10,
            "max_transactions": 10,
        }
        with patch("jeet_analyzer_api.service.engine_main", side_effect=fake_engine):
            client = self.client(InProcessCliEngineAdapter())
            submitted = client.post("/api/v1/investigations/wallet-audit", json=request)
            self.assertEqual(submitted.status_code, 202)
            result = client.get(f"/api/v1/investigations/{submitted.json()['id']}").json()["result"]

        self.assertIsInstance(result["seed_wallet_accounting"], dict)
        self.assertIsInstance(result["seed_wallet_accounting"]["lifecycle"], dict)
        self.assertEqual(result["seed_wallet_accounting"]["reconstructed_starting_inventory_raw"], reconstructed_start)
        self.assertEqual(result["seed_wallet_accounting"]["lifecycle"]["reconstructed_starting_inventory_raw"], reconstructed_start)
        self.assertEqual(result["lifecycle"]["reconstructed_starting_inventory_raw"], reconstructed_start)
        self.assertEqual(result["historical_exit_status"], "VERIFIED_OUT")
        self.assertEqual(result["current_wallet_status"], "VERIFIED_OUT")
        self.assertEqual(result["wallet_status"], "VERIFIED_OUT")
        self.assertEqual(result["current_inventory"]["seed_target_token_balance_raw"], 0)
        self.assertEqual(result["common_control"], "NOT_PROVEN")

    def test_scan_adapter_adds_coverage_from_engine_jsonl_without_provider_calls(self):
        def fake_engine(argv):
            output_dir = Path(argv[argv.index("--output-dir") + 1])
            summary = {"schema": "jeet-analyzer.summary.v1", "token": {"mint": API_MINT, "symbol": "TST", "decimals": 6}, "sellers": []}
            (output_dir / "summary-synthetic.json").write_text(json.dumps(summary), encoding="utf-8")
            (output_dir / "normalized-events-synthetic.jsonl").write_text(
                json.dumps({
                    "record_type": "metadata", "schema": "jeet-analyzer.events.v1", "mint": API_MINT,
                    "coverage_complete": True, "coverage_scope": "synthetic complete history",
                    "coverage_limitation": None, "provider_retry_count": 0,
                }) + "\n",
                encoding="utf-8",
            )
            (output_dir / "seller-leaderboard-synthetic.csv").write_text("rank,wallet\n", encoding="utf-8")
            return 0

        request = {
            "mint": API_MINT, "days": 5, "max_pages": 2, "request_timeout": 5,
            "provider_retries": 0, "provider_backoff_cap": 1, "max_rpc_requests": 10,
            "max_signatures": 10, "max_transactions": 10,
        }
        with patch("jeet_analyzer_api.service.engine_main", side_effect=fake_engine):
            run = InProcessCliEngineAdapter().run(InvestigationMode.SELLER_SCAN, request, self.root / "scan")
        self.assertTrue(run.result["provider_coverage"]["complete"])
        self.assertEqual(run.result["provider_coverage"]["requests"][0]["scope"], "synthetic complete history")

    def test_wallet_adapter_preserves_quote_proceeds_from_normalized_engine_events(self):
        def fake_engine(argv):
            output_dir = Path(argv[argv.index("--output-dir") + 1])
            header = {
                "record_type": "metadata", "schema": "jeet-analyzer.events.v1", "mint": API_MINT,
                "wallet": API_WALLET, "coverage_complete": True,
                "token": {"mint": API_MINT, "symbol": "TST", "decimals": 6},
                "current_balances": {API_WALLET: 0},
                "lifecycle": {"historical_exit_status": "VERIFIED_OUT", "current_wallet_status": "VERIFIED_OUT"},
            }
            sale = {
                "record_type": "event", "wallet": API_WALLET, "event_type": "SELL",
                "token_amount_raw": 2_000_000, "quote_amount_raw": 1_500_000_000,
                "quote_symbol": "SOL", "quote_mint": "SOL", "quote_decimals": 9,
            }
            (output_dir / "wallet-events-synthetic.jsonl").write_text(
                json.dumps(header) + "\n" + json.dumps(sale) + "\n", encoding="utf-8"
            )
            return 0

        request = {
            "mint": API_MINT, "wallet": API_WALLET, "days": 5, "max_pages": 2,
            "trace_depth": 1, "request_timeout": 5, "provider_retries": 0,
            "provider_backoff_cap": 1, "max_rpc_requests": 10, "max_signatures": 10,
            "max_transactions": 10,
        }
        with patch("jeet_analyzer_api.service.engine_main", side_effect=fake_engine):
            run = InProcessCliEngineAdapter().run(InvestigationMode.WALLET_AUDIT, request, self.root / "wallet")
        self.assertEqual(run.result["proceeds"]["SOL"]["amount_raw"], 1_500_000_000)
        self.assertEqual(run.result["current_wallet_status"], "VERIFIED_OUT")


if __name__ == "__main__":
    unittest.main()
