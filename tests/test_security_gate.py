from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from jeet_analyzer_api.beta_auth import BETA_COOKIE, MAX_SESSION_TOKEN_LENGTH, read_session
from jeet_analyzer_api.secure_app import create_secure_app
from scripts.generate_beta_codes import main as generate_beta_codes
from tests.test_beta_api import ADMIN, INVITE, FakeBetaAdapter, beta_config


class HostedBetaSecurityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def client(self, **changes):
        config = beta_config(
            self.root,
            allowed_hosts=("jeet.example", "127.0.0.1", "localhost"),
            public_origin="https://jeet.example",
            **changes,
        )
        app = create_secure_app(engine_adapter=FakeBetaAdapter(), beta_config=config)
        return TestClient(app, base_url="https://jeet.example"), config

    def test_framework_docs_are_not_public_in_hosted_beta(self):
        client, _ = self.client()
        with client:
            for path in ("/docs", "/redoc", "/openapi.json"):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 404)

    def test_beta_responses_are_non_cacheable_and_non_indexable(self):
        client, _ = self.client()
        with client:
            response = client.get("/api/beta/session")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["pragma"], "no-cache")
            self.assertEqual(response.headers["cross-origin-opener-policy"], "same-origin")
            self.assertEqual(response.headers["cross-origin-resource-policy"], "same-origin")
            self.assertIn("noindex", response.headers["x-robots-tag"])

    def test_unexpected_host_is_rejected_before_application_routes(self):
        client, _ = self.client()
        with client:
            response = client.get("/api/beta/session", headers={"host": "attacker.invalid"})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["detail"], "invalid host")

    def test_cross_origin_state_change_is_rejected(self):
        client, _ = self.client()
        with client:
            response = client.post(
                "/api/beta/session",
                json={"access_code": INVITE},
                headers={"origin": "https://attacker.invalid"},
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["detail"], "cross-origin request rejected")

            accepted = client.post(
                "/api/beta/session",
                json={"access_code": INVITE},
                headers={"origin": "https://jeet.example"},
            )
            self.assertEqual(accepted.status_code, 200)

    def test_failed_invite_logins_are_throttled_per_cloudflare_client_ip(self):
        client, _ = self.client(invite_login_failures=2, login_window_seconds=300)
        headers = {"cf-connecting-ip": "203.0.113.8"}
        with client:
            self.assertEqual(client.post("/api/beta/session", json={"access_code": "wrong-code-000001"}, headers=headers).status_code, 401)
            self.assertEqual(client.post("/api/beta/session", json={"access_code": "wrong-code-000002"}, headers=headers).status_code, 401)
            blocked = client.post("/api/beta/session", json={"access_code": INVITE}, headers=headers)
            self.assertEqual(blocked.status_code, 429)
            self.assertIn("Retry-After", blocked.headers)

            other_ip = client.post(
                "/api/beta/session",
                json={"access_code": INVITE},
                headers={"cf-connecting-ip": "203.0.113.9"},
            )
            self.assertEqual(other_ip.status_code, 200)

    def test_admin_login_has_independent_stricter_throttle(self):
        client, _ = self.client(admin_login_failures=1, login_window_seconds=300)
        headers = {"cf-connecting-ip": "198.51.100.2"}
        with client:
            self.assertEqual(client.post("/api/beta/admin/session", json={"access_code": INVITE}, headers=headers).status_code, 401)
            blocked = client.post("/api/beta/admin/session", json={"access_code": ADMIN}, headers=headers)
            self.assertEqual(blocked.status_code, 429)

    def test_oversized_session_cookie_is_rejected_without_parsing(self):
        _, config = self.client()
        token = "x" * (MAX_SESSION_TOKEN_LENGTH + 1)
        self.assertIsNone(read_session(config, token, kind="beta"))

    def test_new_credentials_keep_admin_secret_out_of_tester_invite_file(self):
        destination = self.root / "private-admin"
        self.assertEqual(generate_beta_codes(["--count", "3", "--output-dir", str(destination)]), 0)
        invite_file = next(destination.glob("invite-codes-*.txt"))
        admin_file = next(destination.glob("admin-code-*.txt"))
        invites = invite_file.read_text(encoding="utf-8")
        admin = admin_file.read_text(encoding="utf-8")
        self.assertNotIn("jeet-admin-", invites)
        self.assertIn("jeet-admin-", admin)


if __name__ == "__main__":
    unittest.main()
