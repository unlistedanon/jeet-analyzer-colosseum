from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from scripts.build_external_review import audit_package, build_package
from scripts.demo_flagship import DEFAULT_EVIDENCE, build_demo_result, load_evidence
from scripts.render_week1_video import caption_timeline, load_video_plan, render_srt


ROOT = Path(__file__).parents[1]


class FlagshipDemoTests(unittest.TestCase):
    def setUp(self):
        self.evidence = load_evidence(DEFAULT_EVIDENCE)

    def test_result_is_reconstructed_from_evidence_and_matches_v1_schema(self):
        self.assertNotIn("verdict", self.evidence)
        with patch("socket.socket", side_effect=AssertionError("network access is forbidden")):
            result = build_demo_result(self.evidence)
        schema = json.loads((ROOT / "schemas" / "jeet-analyzer-result-v1.schema.json").read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(result))
        self.assertEqual(errors, [], [error.message for error in errors])
        self.assertEqual(result["historical_exit_status"], "VERIFIED_OUT")
        self.assertEqual(result["current_wallet_status"], "RE_ENTERED")
        self.assertEqual(result["cluster_status"], "NOT_OUT")
        self.assertEqual(result["common_control"], "NOT_PROVEN")
        self.assertEqual(result["current_inventory"]["seed_target_token_balance_raw"], 5_974_478_422_911)
        self.assertEqual(result["sales"]["target_token_sold_raw"], 7_666_267_453_718)
        self.assertEqual(result["demo_summary"]["genuine_target_token_transfer_count"], 0)
        self.assertEqual(result["demo_summary"]["secondary_wallet_target_increase_count"], 0)
        self.assertEqual(result["demo_summary"]["exit_sale_raw"], 7_521_892_705_710)
        self.assertEqual(result["demo_summary"]["exit_balance_after_raw"], 0)
        self.assertEqual(result["demo_summary"]["post_exit_reacquired_raw"], 5_974_478_422_911)
        self.assertEqual(result["request_telemetry"]["total_provider_requests"], 0)
        video_plan = load_video_plan()
        self.assertEqual(video_plan["total_duration_seconds"], 109)
        self.assertEqual(len(video_plan["scenes"]), 7)
        self.assertEqual(caption_timeline(video_plan)[-1]["end"], 109)
        subtitles = render_srt(video_plan)
        self.assertIn("SELL DOES NOT NECESSARILY MEAN EXIT", json.dumps(video_plan))
        self.assertIn("00:01:49,000", subtitles)
        self.assertNotRegex(subtitles, r"(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,100}(?![A-Za-z0-9])")

    def test_inventory_tampering_fails_reconciliation(self):
        changed = deepcopy(self.evidence)
        changed["accounting_evidence"]["confirmed_sold_raw"] += 1
        with self.assertRaisesRegex(ValueError, "sale evidence does not reconcile"):
            build_demo_result(changed)

    def test_result_contains_only_public_aliases(self):
        text = json.dumps(build_demo_result(self.evidence), sort_keys=True)
        self.assertNotRegex(text, r"(?:https?|wss?)://")
        self.assertNotRegex(text, r"(?i)(?:[A-Z]:\\Users\\|[A-Z]:\\josh420-devnet|/Users/|/home/)")
        self.assertNotRegex(text, r"(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,100}(?![A-Za-z0-9])")
        self.assertRegex(text, r"Wallet A")
        self.assertRegex(text, r"TX-[0-9]{3}")

    def test_external_package_is_allowlisted_and_audited(self):
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            output = Path(temporary) / "review"
            package, archive, audit = build_package(output)
            self.assertTrue(package.is_dir())
            self.assertTrue(archive.is_file())
            self.assertEqual(audit["status"], "PASS")
            self.assertFalse((package / ".env").exists())
            self.assertFalse((package / "receipts").exists())
            self.assertTrue((package / "demo" / "flagship-evidence.v1.json").exists())
            self.assertTrue((package / "scripts" / "demo_flagship.py").exists())
            self.assertTrue((package / "scripts" / "render_week1_video.py").exists())
            self.assertTrue((package / "scripts" / "week1_video_media.ps1").exists())
            self.assertTrue((package / "competition" / "eternal-week1" / "week1-video.srt").exists())
            self.assertEqual(audit_package(package)["status"], "PASS")
            self.assertIn("https://jeet.example", (package / "frontend/index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
