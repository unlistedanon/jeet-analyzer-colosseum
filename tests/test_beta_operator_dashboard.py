from __future__ import annotations

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jeet_analyzer_api.beta_auth import ADMIN_COOKIE, issue_session
from jeet_analyzer_api.beta_store import SQLiteBetaStore
from jeet_analyzer_api.operator_dashboard import create_operator_router
from jeet_analyzer_api.secure_app import create_secure_app
from tests.test_beta_api import API_MINT, API_WALLET, beta_config


class BetaOperatorDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = beta_config(self.root)
        self.store = SQLiteBetaStore(self.config.database_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def app_client(self) -> TestClient:
        app = FastAPI()
        app.include_router(create_operator_router(self.config))
        return TestClient(app)

    def seed_case(self) -> str:
        request = self.config.investigation_request(API_MINT, API_WALLET)
        record, duplicate = self.store.create_or_get(
            code_id="tester-01",
            fingerprint="operator-test-fingerprint",
            request=request,
            config=self.config,
        )
        self.assertFalse(duplicate)
        result = {
            "historical_exit_status": "VERIFIED_OUT",
            "current_wallet_status": "RE_ENTERED",
            "target_cluster_status": "NOT_OUT",
            "relationship_status": "RESOLVED_WITHIN_SCOPE",
            "cluster_status": "NOT_OUT",
            "common_control": "NOT_PROVEN",
            "provider_coverage": {"complete": True},
            "wallet_relationships": [{"source": API_WALLET, "destination": "linked-wallet"}],
            "request_telemetry": {"estimated_provider_credits": 123},
            "evidence_receipts": {
                "result_json": str(self.root / "private" / "receipt.json"),
                "evidence_jsonl": str(self.root / "private" / "events.jsonl"),
            },
        }
        self.store.complete(
            record["id"],
            result=result,
            safe_result={"schema": "jeet-analyzer.public-case.v1"},
            artifacts={"receipt_json": str(self.root / "private" / "receipt.json")},
        )
        self.store.add_feedback(
            identifier=record["id"],
            code_id="tester-01",
            useful=True,
            comment="Caught the linked inventory.",
        )
        return record["id"]

    def authenticate(self, client: TestClient) -> None:
        token = issue_session(self.config, subject="beta-admin", kind="admin")
        client.cookies.set(ADMIN_COOKIE, token)

    def test_operator_routes_require_admin_session(self):
        with self.app_client() as client:
            self.assertEqual(client.get("/api/beta/admin/investigations").status_code, 401)

    def test_operator_list_shows_inputs_and_verdict_without_sensitive_fields(self):
        identifier = self.seed_case()
        with self.app_client() as client:
            self.authenticate(client)
            response = client.get("/api/beta/admin/investigations?limit=100")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            row = response.json()["investigations"][0]
            self.assertEqual(row["investigation_id"], identifier)
            self.assertEqual(row["code_id"], "tester-01")
            self.assertEqual(row["request"]["mint"], API_MINT)
            self.assertEqual(row["request"]["wallet"], API_WALLET)
            self.assertEqual(row["target_cluster_status"], "NOT_OUT")
            self.assertEqual(row["estimated_provider_credits_used"], 123)
            self.assertEqual(row["relationship_count"], 1)
            serialized = response.text
            self.assertNotIn("operator-test-fingerprint", serialized)
            self.assertNotIn("receipt.json", serialized)

    def test_operator_detail_returns_exact_result_feedback_and_artifact_names_only(self):
        identifier = self.seed_case()
        with self.app_client() as client:
            self.authenticate(client)
            response = client.get(f"/api/beta/admin/investigations/{identifier}")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["request"]["mint"], API_MINT)
            self.assertEqual(body["result"]["historical_exit_status"], "VERIFIED_OUT")
            self.assertEqual(body["result"]["current_wallet_status"], "RE_ENTERED")
            self.assertEqual(body["result"]["target_cluster_status"], "NOT_OUT")
            self.assertEqual(body["result"]["common_control"], "NOT_PROVEN")
            self.assertEqual(
                body["result"]["evidence_receipts"],
                {
                    "result_json": "available via the operator receipt download",
                    "evidence_jsonl": "available via the operator receipt download",
                },
            )
            self.assertEqual(body["feedback"][0]["comment"], "Caught the linked inventory.")
            self.assertEqual(body["artifact_names"], ["receipt_json"])
            serialized = response.text
            self.assertNotIn("operator-test-fingerprint", serialized)
            self.assertNotIn(str(self.root), serialized)

    def test_secure_host_keeps_operator_api_ahead_of_spa_catchall(self):
        identifier = self.seed_case()
        frontend = self.root / "frontend-dist"
        frontend.mkdir()
        (frontend / "index.html").write_text("<html>spa-shell</html>", encoding="utf-8")
        self.config = replace(self.config, frontend_dist=frontend)

        with TestClient(create_secure_app(beta_config=self.config)) as client:
            self.authenticate(client)
            response = client.get("/api/beta/admin/investigations")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("application/json"))
            self.assertEqual(response.json()["investigations"][0]["investigation_id"], identifier)
            self.assertNotIn("spa-shell", response.text)
            self.assertIn("spa-shell", client.get("/").text)
            self.assertEqual(client.get("/some-frontend-route").status_code, 404)


if __name__ == "__main__":
    unittest.main()
