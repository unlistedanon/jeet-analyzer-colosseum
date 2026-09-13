from __future__ import annotations

from dataclasses import replace
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

from fastapi.testclient import TestClient

from jeet_analyzer_api.app import create_app
from jeet_analyzer_api.beta_config import BetaConfig, hash_secret
from jeet_analyzer_api.beta_store import SQLiteBetaStore
from jeet_analyzer_api.models import InvestigationMode
from jeet_analyzer_api.service import EngineRunResult
from tests.test_ui_api import API_MINT, API_WALLET, cluster_result
from scripts.generate_beta_codes import main as generate_beta_codes


INVITE = "jeet-beta-test-code-0001"
ADMIN = "jeet-beta-admin-code-0001"
OTHER_WALLET = "8qbHbw2BbbTHBW1sbeqakYXV4q3eFrGjdM4i4yE2M5j"


def beta_config(root: Path, **changes: Any) -> BetaConfig:
    config = BetaConfig(
        enabled=True,
        code_hashes={"tester-01": hash_secret(INVITE)},
        admin_code_hash=hash_secret(ADMIN),
        session_secret="test-session-secret-that-is-more-than-thirty-two-bytes",
        session_ttl_seconds=3600,
        cookie_secure=False,
        max_concurrent=2,
        max_runs_per_code_per_day=3,
        max_estimated_credits_per_run=650,
        global_daily_credit_cap=3000,
        database_path=root / "beta.sqlite3",
        output_root=root / "outputs",
        frontend_dist=root / "missing-frontend",
    )
    return replace(config, **changes)


class FakeBetaAdapter:
    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.result = result or cluster_result()
        self.error = error
        self.calls: list[tuple[InvestigationMode, dict[str, Any], Path]] = []

    def run(self, mode: InvestigationMode, request: Mapping[str, Any], output_dir: Path) -> EngineRunResult:
        self.calls.append((mode, dict(request), output_dir))
        if self.error:
            raise self.error
        output_dir.mkdir(parents=True)
        return EngineRunResult(self.result, 0, {})


class BlockingAdapter(FakeBetaAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(self, mode: InvestigationMode, request: Mapping[str, Any], output_dir: Path) -> EngineRunResult:
        self.calls.append((mode, dict(request), output_dir))
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("test adapter release timeout")
        output_dir.mkdir(parents=True)
        return EngineRunResult(self.result, 0, {})


class ReceiptThenErrorAdapter(FakeBetaAdapter):
    def run(self, mode: InvestigationMode, request: Mapping[str, Any], output_dir: Path) -> EngineRunResult:
        self.calls.append((mode, dict(request), output_dir))
        output_dir.mkdir(parents=True)
        result_path = output_dir / "cluster-audit-recoverable.json"
        result_path.write_text(json.dumps(self.result), encoding="utf-8")
        (output_dir / "cluster-audit-events-recoverable.jsonl").write_text("{}\n", encoding="utf-8")
        raise RuntimeError("late adapter failure")


class BetaApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def open_client(self, adapter: FakeBetaAdapter, config: BetaConfig | None = None) -> TestClient:
        return TestClient(create_app(engine_adapter=adapter, beta_config=config or beta_config(self.root)))

    @staticmethod
    def login(client: TestClient, code: str = INVITE):
        return client.post("/api/beta/session", json={"access_code": code})

    @staticmethod
    def wait_for(client: TestClient, identifier: str, terminal: set[str] | None = None) -> dict[str, Any]:
        terminal = terminal or {"complete", "failed"}
        for _ in range(200):
            response = client.get(f"/api/beta/investigations/{identifier}")
            if response.status_code == 200 and response.json()["status"] in terminal:
                return response.json()
            time.sleep(0.01)
        raise AssertionError("beta investigation did not reach a terminal state")

    def test_invite_accept_reject_session_logout_and_revocation(self):
        config = beta_config(self.root)
        adapter = FakeBetaAdapter()
        with self.open_client(adapter, config) as client:
            self.assertEqual(self.login(client, "definitely-wrong-code").status_code, 401)
            accepted = self.login(client)
            self.assertEqual(accepted.status_code, 200)
            self.assertIn("HttpOnly", accepted.headers["set-cookie"])
            self.assertIn("SameSite=strict", accepted.headers["set-cookie"])
            self.assertTrue(client.get("/api/beta/session").json()["authenticated"])
            config.code_hashes.clear()
            self.assertFalse(client.get("/api/beta/session").json()["authenticated"])
            self.assertEqual(client.delete("/api/beta/session").status_code, 200)

    def test_production_session_cookie_is_secure(self):
        with self.open_client(FakeBetaAdapter(), beta_config(self.root, cookie_secure=True)) as client:
            response = self.login(client)
            self.assertIn("Secure", response.headers["set-cookie"])

    def test_malformed_addresses_are_rejected_before_engine_or_quota_work(self):
        adapter = FakeBetaAdapter()
        with self.open_client(adapter) as client:
            self.assertEqual(self.login(client).status_code, 200)
            self.assertEqual(client.post("/api/beta/investigations", json={"mint": "bad", "wallet": API_WALLET}).status_code, 422)
            self.assertEqual(client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": "bad"}).status_code, 422)
            self.assertEqual(adapter.calls, [])

    def test_server_owns_the_per_investigation_forensic_budget(self):
        adapter = FakeBetaAdapter()
        config = beta_config(self.root, max_estimated_credits_per_run=321, max_rpc_requests=27, max_signatures=33, max_transactions=19)
        with self.open_client(adapter, config) as client:
            self.login(client)
            submitted = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": API_WALLET})
            view = self.wait_for(client, submitted.json()["investigation_id"])
            request = adapter.calls[0][1]
            self.assertEqual(request["max_rpc_requests"], 27)
            self.assertEqual(request["max_signatures"], 33)
            self.assertEqual(request["max_transactions"], 19)
            self.assertEqual(request["max_estimated_provider_credits"], 321)
            self.assertTrue(request["deep_forensic"])
            self.assertEqual(view["estimated_provider_credits_reserved"], 321)
            self.assertEqual(view["estimated_provider_credits_used"], 13)

    def test_default_beta_policy_is_coverage_scale_and_server_owned(self):
        names = [name for name in os.environ if name.startswith("JEET_BETA_")]
        with patch.dict(os.environ, {}, clear=False):
            for name in names:
                os.environ.pop(name, None)
            config = BetaConfig.from_env(self.root)
        self.assertEqual(config.max_concurrent, 1)
        self.assertEqual(config.max_runs_per_code_per_day, 1)
        self.assertEqual(config.max_estimated_credits_per_run, 60_000)
        self.assertEqual(config.global_daily_credit_cap, 250_000)
        self.assertEqual(config.days, 30)
        self.assertEqual(config.graph_depth, 3)
        self.assertEqual(config.max_wallets, 25)
        self.assertEqual(config.max_pages, 200)
        self.assertEqual(config.max_rpc_requests, 1_500)
        self.assertEqual(config.max_signatures, 40_000)
        self.assertEqual(config.max_transactions, 20_000)
        self.assertEqual(config.funding_lookback_days, 365)
        self.assertTrue(config.deep_forensic)

    def test_beta_deep_forensic_mode_can_be_disabled_by_the_operator(self):
        config = beta_config(self.root, deep_forensic=False)
        self.assertFalse(config.investigation_request(API_MINT, API_WALLET)["deep_forensic"])

    def test_late_engine_error_returns_an_already_written_forensic_receipt(self):
        adapter = ReceiptThenErrorAdapter()
        with self.open_client(adapter) as client:
            self.login(client)
            submitted = client.post(
                "/api/beta/investigations",
                json={"mint": API_MINT, "wallet": API_WALLET},
            ).json()
            view = self.wait_for(client, submitted["investigation_id"])
            self.assertEqual(view["status"], "complete")
            self.assertEqual(view["result"]["schema"], "jeet-analyzer.result.v1")
            self.assertEqual(view["result"]["common_control"], "NOT_PROVEN")
            self.assertIsNone(view["error"])

    def test_late_engine_error_rejects_an_unreplayable_receipt(self):
        adapter = ReceiptThenErrorAdapter(
            result={
                "schema": "jeet-analyzer.result.v1",
                "mint": API_MINT,
                "seed_wallet": API_WALLET,
                "provider_coverage": {},
                "common_control": "NOT_PROVEN",
            }
        )
        with self.open_client(adapter) as client:
            self.login(client)
            submitted = client.post(
                "/api/beta/investigations",
                json={"mint": API_MINT, "wallet": API_WALLET},
            ).json()
            view = self.wait_for(client, submitted["investigation_id"])
            self.assertEqual(view["status"], "failed")
            self.assertIsNone(view["result"])
            self.assertEqual(view["termination_reason"], "ENGINE_EXECUTION_FAILED")

    def test_beta_per_run_credit_policy_is_configurable_above_default(self):
        config = beta_config(
            self.root,
            max_estimated_credits_per_run=60_001,
            global_daily_credit_cap=120_000,
        )
        self.assertEqual(config.max_estimated_credits_per_run, 60_001)

    def test_early_completion_releases_unused_daily_reservation(self):
        adapter = FakeBetaAdapter()
        config = beta_config(
            self.root,
            max_estimated_credits_per_run=650,
            global_daily_credit_cap=700,
        )
        with self.open_client(adapter, config) as client:
            self.login(client)
            first = client.post(
                "/api/beta/investigations",
                json={"mint": API_MINT, "wallet": API_WALLET},
            )
            first_view = self.wait_for(client, first.json()["investigation_id"])
            self.assertEqual(first_view["estimated_provider_credits_used"], 13)
            second = client.post(
                "/api/beta/investigations",
                json={"mint": API_MINT, "wallet": OTHER_WALLET},
            )
            self.assertEqual(second.status_code, 202)
            second_view = self.wait_for(client, second.json()["investigation_id"])
            self.assertEqual(second_view["estimated_provider_credits_used"], 13)

    def test_duplicate_active_submission_is_reused_without_second_engine_call(self):
        adapter = BlockingAdapter()
        with self.open_client(adapter) as client:
            self.login(client)
            first = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": API_WALLET})
            self.assertTrue(adapter.entered.wait(2))
            second = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": API_WALLET})
            self.assertEqual(first.json()["investigation_id"], second.json()["investigation_id"])
            self.assertTrue(second.json()["deduplicated"])
            self.assertEqual(len(adapter.calls), 1)
            adapter.release.set()
            self.wait_for(client, first.json()["investigation_id"])

    def test_concurrency_limit_is_atomic_and_fail_closed(self):
        adapter = BlockingAdapter()
        config = beta_config(self.root, max_concurrent=1)
        with self.open_client(adapter, config) as client:
            self.login(client)
            first = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": API_WALLET})
            self.assertTrue(adapter.entered.wait(2))
            rejected = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": OTHER_WALLET})
            self.assertEqual(rejected.status_code, 503)
            self.assertEqual(rejected.json()["detail"]["kind"], "BETA_CONCURRENCY_LIMIT")
            adapter.release.set()
            self.wait_for(client, first.json()["investigation_id"])

    def test_per_code_daily_quota_and_global_credit_cap(self):
        adapter = FakeBetaAdapter()
        quota_config = beta_config(self.root, max_runs_per_code_per_day=1)
        with self.open_client(adapter, quota_config) as client:
            self.login(client)
            first = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": API_WALLET})
            self.wait_for(client, first.json()["investigation_id"])
            rejected = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": OTHER_WALLET})
            self.assertEqual(rejected.json()["detail"]["kind"], "BETA_CODE_DAILY_QUOTA")

        second_root = self.root / "global"
        cap_config = beta_config(second_root, global_daily_credit_cap=650)
        with self.open_client(adapter, cap_config) as client:
            self.login(client)
            first = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": API_WALLET})
            self.wait_for(client, first.json()["investigation_id"])
            rejected = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": OTHER_WALLET})
            self.assertEqual(rejected.status_code, 503)
            self.assertEqual(rejected.json()["detail"]["kind"], "BETA_GLOBAL_DAILY_CREDIT_CAP")

    def test_progress_result_budget_exhaustion_and_common_control_truth_survive_transport(self):
        result = cluster_result(complete=False, cluster_status="INSUFFICIENT_DATA")
        result["historical_exit_status"] = "INSUFFICIENT_DATA"
        result["current_wallet_status"] = "NOT_OUT"
        result["common_control"] = "NOT_PROVEN"
        adapter = FakeBetaAdapter(result)
        with self.open_client(adapter) as client:
            self.login(client)
            submitted = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": API_WALLET}).json()
            view = self.wait_for(client, submitted["investigation_id"])
            self.assertEqual(view["status"], "complete")
            self.assertEqual(view["result"]["cluster_status"], "INSUFFICIENT_DATA")
            self.assertEqual(view["result"]["current_wallet_status"], "NOT_OUT")
            self.assertEqual(view["result"]["common_control"], "NOT_PROVEN")
            self.assertEqual(view["termination_reason"], "PROVIDER_BUDGET_EXHAUSTED")
            stages = [event["stage"] for event in view["progress"]["events"]]
            self.assertIn("COLLECTING_HISTORY", stages)
            self.assertIn("BUILDING_REPORT", stages)
            self.assertIn("COMPLETE", stages)
            report_states = [event["state"] for event in view["progress"]["events"] if event["stage"] == "BUILDING_REPORT"]
            self.assertEqual(report_states, ["running", "complete"])

    def test_provider_error_is_redacted_and_returns_no_stack_trace(self):
        adapter = FakeBetaAdapter(error=RuntimeError("provider failed https://rpc.invalid/?api-key=do-not-leak"))
        with self.open_client(adapter) as client:
            self.login(client)
            submitted = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": API_WALLET}).json()
            view = self.wait_for(client, submitted["investigation_id"])
            self.assertEqual(view["status"], "failed")
            self.assertNotIn("do-not-leak", view["error"])
            self.assertIn("[URL_REDACTED]", view["error"])
            self.assertNotIn("Traceback", view["error"])
            self.assertEqual(view["estimated_provider_credits_used"], 650)

    def test_safe_share_uses_allowlisted_export_and_feedback_is_owner_scoped(self):
        adapter = FakeBetaAdapter()
        safe = {"schema": "jeet-analyzer.public-case.v1", "wallet": "Wallet A", "common_control": "NOT_PROVEN"}
        with patch("jeet_analyzer_api.beta_service.replay_cluster_receipt", return_value={}), patch(
            "jeet_analyzer_api.beta_service.build_public_case", return_value=safe
        ):
            with self.open_client(adapter) as client:
                self.login(client)
                submitted = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": API_WALLET}).json()
                view = self.wait_for(client, submitted["investigation_id"])
                self.assertTrue(view["safe_report_available"])
                shared = client.get(f"/api/beta/investigations/{submitted['investigation_id']}/share")
                serialized = shared.text
                self.assertEqual(shared.json()["brand"], "JEET ANALYZER")
                self.assertIn("generated_at", shared.json())
                self.assertNotIn(API_MINT, serialized)
                self.assertNotIn(API_WALLET, serialized)
                feedback = client.post(
                    f"/api/beta/investigations/{submitted['investigation_id']}/feedback",
                    json={"useful": True, "comment": "Clear and conservative."},
                )
                self.assertEqual(feedback.status_code, 201)
                self.assertTrue(feedback.json()["stored"])

    def test_optional_share_export_failure_does_not_discard_forensic_result(self):
        adapter = FakeBetaAdapter()
        with patch("jeet_analyzer_api.beta_service.replay_cluster_receipt", side_effect=RuntimeError("share-only failure")):
            with self.open_client(adapter) as client:
                self.login(client)
                submitted = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": API_WALLET}).json()
                view = self.wait_for(client, submitted["investigation_id"])
                self.assertEqual(view["status"], "complete")
                self.assertEqual(view["result"]["common_control"], "NOT_PROVEN")
                self.assertFalse(view["safe_report_available"])

    def test_admin_is_separately_protected_and_reports_estimated_metrics(self):
        adapter = FakeBetaAdapter()
        with self.open_client(adapter) as client:
            self.assertEqual(client.get("/api/beta/admin/metrics").status_code, 401)
            self.assertEqual(client.post("/api/beta/admin/session", json={"access_code": INVITE}).status_code, 401)
            self.assertEqual(client.post("/api/beta/admin/session", json={"access_code": ADMIN}).status_code, 200)
            metrics = client.get("/api/beta/admin/metrics")
            self.assertEqual(metrics.status_code, 200)
            self.assertEqual(metrics.json()["investigations"], 0)
            self.assertIn("estimates", metrics.json()["estimated_credit_note"])

    def test_kill_switch_blocks_new_work_but_keeps_auth_and_readiness_truthful(self):
        adapter = FakeBetaAdapter()
        config = beta_config(self.root, enabled=False)
        with self.open_client(adapter, config) as client:
            self.assertEqual(self.login(client).status_code, 200)
            session = client.get("/api/beta/session").json()
            self.assertFalse(session["beta_enabled"])
            rejected = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": API_WALLET})
            self.assertEqual(rejected.status_code, 503)
            legacy_requests = (
                ("/api/v1/investigations/seller-scan", {"mint": API_MINT}),
                ("/api/scan", {"mint": API_MINT}),
                ("/api/v1/investigations/wallet-audit", {"mint": API_MINT, "wallet": API_WALLET}),
                ("/api/wallet", {"mint": API_MINT, "wallet": API_WALLET}),
                ("/api/v1/investigations/cluster-audit", {"mint": API_MINT, "wallet": API_WALLET}),
                ("/api/cluster-audit", {"mint": API_MINT, "wallet": API_WALLET}),
            )
            for path, body in legacy_requests:
                with self.subTest(path=path):
                    self.assertEqual(client.post(path, json=body).status_code, 404)
            self.assertEqual(adapter.calls, [])
            self.assertEqual(client.get("/ready").status_code, 200)

    def test_persisted_reports_remain_readable_after_kill_switch_restart(self):
        adapter = FakeBetaAdapter()
        enabled = beta_config(self.root)
        with self.open_client(adapter, enabled) as client:
            self.login(client)
            submitted = client.post("/api/beta/investigations", json={"mint": API_MINT, "wallet": API_WALLET}).json()
            self.wait_for(client, submitted["investigation_id"])
            identifier = submitted["investigation_id"]
        with self.open_client(adapter, replace(enabled, enabled=False)) as client:
            self.login(client)
            preserved = client.get(f"/api/beta/investigations/{identifier}")
            self.assertEqual(preserved.status_code, 200)
            self.assertEqual(preserved.json()["status"], "complete")
            self.assertEqual(preserved.json()["result"]["common_control"], "NOT_PROVEN")

    def test_request_size_boundary_and_security_headers(self):
        with self.open_client(FakeBetaAdapter()) as client:
            oversized = client.post("/api/beta/session", content=b"x" * 16_385, headers={"content-type": "application/json"})
            self.assertEqual(oversized.status_code, 413)
            health = client.get("/health")
            self.assertEqual(health.headers["x-content-type-options"], "nosniff")
            self.assertEqual(health.headers["x-frame-options"], "DENY")
            self.assertIn("frame-ancestors 'none'", health.headers["content-security-policy"])

    def test_hosted_beta_rejects_forwarded_http_and_sets_hsts_for_https(self):
        with self.open_client(FakeBetaAdapter()) as client:
            rejected = client.get("/health", headers={"x-forwarded-proto": "http"})
            self.assertEqual(rejected.status_code, 426)
            self.assertEqual(rejected.json()["detail"], "HTTPS is required")
            self.assertEqual(rejected.headers["cache-control"], "no-store")

            secure = client.get("/health", headers={"x-forwarded-proto": "https"})
            self.assertEqual(secure.status_code, 200)
            self.assertEqual(secure.headers["strict-transport-security"], "max-age=31536000")

            self.assertEqual(client.get("/health").status_code, 200)

    def test_restart_recovery_marks_lost_jobs_failed(self):
        config = beta_config(self.root)
        store = SQLiteBetaStore(config.database_path)
        store.initialize()
        record, duplicate = store.create_or_get(
            code_id="tester-01", fingerprint="fingerprint", request=config.investigation_request(API_MINT, API_WALLET), config=config
        )
        self.assertFalse(duplicate)
        self.assertEqual(store.recover_interrupted(), 1)
        recovered = store.get(record["id"])
        self.assertEqual(recovered["state"], "failed")
        self.assertEqual(recovered["termination_reason"], "SERVER_RESTARTED")
        self.assertEqual(recovered["estimated_credits_used"], 0)

    def test_invite_generator_keeps_raw_codes_out_of_console_and_emits_hashes(self):
        destination = self.root / "private-admin"
        with patch("builtins.print") as printer:
            self.assertEqual(generate_beta_codes(["--count", "20", "--output-dir", str(destination)]), 0)
        raw_path = next(destination.glob("invite-codes-*.txt"))
        environment_path = next(destination.glob("beta-secrets-*.env"))
        raw = raw_path.read_text(encoding="utf-8")
        environment = environment_path.read_text(encoding="utf-8")
        invite_lines = [line for line in raw.splitlines() if line.startswith("beta-")]
        self.assertEqual(len(invite_lines), 20)
        self.assertEqual(len(set(invite_lines)), 20)
        first_identifier, first_code = invite_lines[0].split(": ", 1)
        self.assertIn(f"{first_identifier}:{hash_secret(first_code)}", environment)
        console = " ".join(str(call.args[0]) for call in printer.call_args_list)
        self.assertNotIn(first_code, console)


if __name__ == "__main__":
    unittest.main()
