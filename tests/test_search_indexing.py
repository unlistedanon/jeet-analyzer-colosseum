from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from jeet_analyzer_api.secure_app import create_secure_app
from tests.test_beta_api import FakeBetaAdapter, beta_config


class PublicSearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        dist = self.root / "dist"
        (dist / "methodology").mkdir(parents=True)
        (dist / "index.html").write_text("<html><h1>Jeet Analyzer</h1></html>")
        (dist / "methodology" / "index.html").write_text("<html><h1>Methodology</h1></html>")
        self.config = beta_config(self.root, frontend_dist=dist, public_access=True,
                                  allowed_hosts=("jeet.example",), public_origin="https://jeet.example")
        self.engine = FakeBetaAdapter()

    def tearDown(self):
        self.temporary.cleanup()

    def client(self):
        return TestClient(create_secure_app(engine_adapter=self.engine, beta_config=self.config),
                          base_url="https://jeet.example")

    @patch("jeet_analyzer_api.pump_feed.PumpRoundFeed.start")
    def test_only_explicit_public_documents_are_indexable(self, _feed):
        with self.client() as client:
            for path in ("/", "/methodology/", "/robots.txt", "/sitemap.xml"):
                for method in (client.get, client.head):
                    with self.subTest(path=path, method=method.__name__):
                        response = method(path)
                        self.assertEqual(response.status_code, 200)
                        self.assertNotIn("noindex", response.headers.get("x-robots-tag", ""))
            for path in ("/?admin=1", "/?demo=flagship", "/?game=1", "/?report=anything", "/api/beta/admin/session", "/api/beta/session", "/api/beta/admin/metrics", "/docs", "/not-real", "/admin/", "/auth/", "/reports/private"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertIn("noindex", response.headers.get("x-robots-tag", ""))
            self.assertEqual(client.get("/api/beta/admin/metrics").status_code, 401)
            self.assertEqual(client.get("/not-real").status_code, 404)
            self.assertEqual(client.get("/admin/").status_code, 404)
            self.assertEqual(self.engine.calls, [])

    @patch("jeet_analyzer_api.pump_feed.PumpRoundFeed.start")
    def test_sitemap_robots_and_redirects_are_real(self, _feed):
        with self.client() as client:
            response = client.get("/sitemap.xml")
            self.assertIn("application/xml", response.headers["content-type"])
            tree = ElementTree.fromstring(response.text)
            urls = [e.text for e in tree.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
            self.assertEqual(urls, ["https://jeet.example/", "https://jeet.example/methodology/"])
            robots = client.get("/robots.txt")
            self.assertIn("text/plain", robots.headers["content-type"])
            self.assertNotIn("<html", robots.text)
            for agent in ("Googlebot", "Bingbot", "OAI-SearchBot"):
                self.assertIn(f"User-agent: {agent}\nAllow: /", robots.text)
            self.assertIn("Disallow: /api/", robots.text)
            self.assertIn("Sitemap: https://jeet.example/sitemap.xml", robots.text)
            for source, target in (("/index.html", "/"), ("/methodology", "/methodology/")):
                response = client.get(source, follow_redirects=False)
                self.assertEqual(response.status_code, 308)
                self.assertEqual(response.headers["location"], target)
            response = client.get("/", headers={"x-forwarded-proto": "http"}, follow_redirects=False)
            self.assertEqual(response.status_code, 308)
            self.assertEqual(response.headers["location"], "https://jeet.example/")
            self.assertEqual(client.get("/", headers={"host": "attacker.invalid"}).status_code, 400)

    @patch("jeet_analyzer_api.pump_feed.PumpRoundFeed.start")
    def test_invite_only_configuration_does_not_open_indexing(self, _feed):
        from dataclasses import replace
        self.config = replace(self.config, public_access=False)
        with self.client() as client:
            self.assertIn("noindex", client.get("/").headers["x-robots-tag"])


if __name__ == "__main__":
    unittest.main()
