from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import requests

from jeet_analyzer import analyzer, history, lifecycle, progress, report_contract, reporting
from tests.fixtures.builders import ROOT, TARGET_MINT, WALLET_B, buy_tx, sale_tx


START = 1_799_900_000
END = 1_800_100_000


class Response:
    def __init__(self, body, status_code=200, headers=None):
        self.body = body
        self.status_code = status_code
        self.headers = headers or {}
        self.raw = io.BytesIO(json.dumps(body).encode("utf-8"))
        self.closed = False

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    def close(self):
        self.closed = True
        self.raw.close()


class SequenceSession:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = 0

    def post(self, _url, *, json, timeout, stream=False):
        del json, timeout, stream
        self.calls += 1
        row = self.rows.pop(0)
        if isinstance(row, Exception):
            raise row
        return row


def provider_with(rows, *, retries=2, sleeper=None):
    return history.HeliusHistoricalProvider(
        "credential-that-must-never-appear",
        rpc=object(),
        session=SequenceSession(rows),
        timeout_seconds=3,
        max_retries=retries,
        max_pages=1,
        backoff_cap_seconds=4,
        sleeper=sleeper or (lambda _delay: None),
    )


class ProviderResilienceTests(unittest.TestCase):
    def test_timeout_then_successful_retry(self):
        delays = []
        provider = provider_with(
            [
                requests.exceptions.ReadTimeout("secret https://host.invalid/?api-key=leak"),
                Response({"result": {"data": [], "paginationToken": None}}),
            ],
            sleeper=delays.append,
        )
        batch = provider.get_wallet_activity(ROOT, START, END)
        self.assertTrue(batch.complete)
        self.assertEqual(batch.retry_count, 1)
        self.assertEqual(provider.total_retry_count, 1)
        self.assertEqual(delays, [1])

    def test_repeated_timeout_is_bounded_and_typed(self):
        session = SequenceSession([requests.exceptions.ReadTimeout("boom")] * 3)
        provider = history.HeliusHistoricalProvider(
            "metered-key",
            rpc=object(),
            session=session,
            max_retries=2,
            max_pages=1,
            sleeper=lambda _delay: None,
        )
        with self.assertRaises(history.ProviderTimeoutError) as caught:
            provider.get_wallet_activity(ROOT, START, END)
        self.assertEqual(session.calls, 3)
        self.assertEqual(caught.exception.retry_count, 2)
        self.assertEqual(caught.exception.category, "TIMEOUT")

    def test_rate_limit_retries_then_succeeds(self):
        delays = []
        provider = provider_with(
            [
                Response({}, status_code=429, headers={"Retry-After": "3"}),
                Response({"result": {"data": [], "paginationToken": None}}),
            ],
            sleeper=delays.append,
        )
        batch = provider.get_wallet_activity(ROOT, START, END)
        self.assertTrue(batch.complete)
        self.assertEqual(batch.retry_count, 1)
        self.assertEqual(delays, [3])

    def test_malformed_provider_response_is_not_retried_as_success(self):
        provider = provider_with([Response({"unexpected": []})])
        with self.assertRaises(history.ProviderMalformedResponseError) as caught:
            provider.get_wallet_activity(ROOT, START, END)
        self.assertEqual(caught.exception.category, "MALFORMED_RESPONSE")
        self.assertEqual(caught.exception.retry_count, 0)

    def test_terminal_reason_and_receipt_redact_credentials(self):
        provider = provider_with(
            [requests.exceptions.ReadTimeout("https://host.invalid/?api-key=super-secret")],
            retries=0,
        )
        with self.assertRaises(history.ProviderTimeoutError) as caught:
            provider.get_wallet_activity(ROOT, START, END)
        tracker = progress.ProgressTracker(clock=lambda: "2026-08-29T00:00:00Z")
        report = report_contract.failure_result(
            mint=TARGET_MINT,
            seed_wallet=ROOT,
            start=START,
            end=END,
            reason=caught.exception,
            provider_telemetry=provider.telemetry(),
            progress=tracker,
        )
        encoded = json.dumps(report)
        self.assertNotIn("super-secret", encoded)
        self.assertNotIn("host.invalid", encoded)
        self.assertEqual(report["cluster_status"], "INSUFFICIENT_DATA")
        self.assertEqual(report["current_wallet_status"], "INSUFFICIENT_DATA")
        self.assertEqual(report["provider_coverage"]["requests"][0]["failure_category"], "TIMEOUT")

    def test_retry_count_never_exceeds_configured_bound(self):
        delays = []
        session = SequenceSession([Response({}, status_code=503)] * 4)
        provider = history.HeliusHistoricalProvider(
            "metered-key",
            rpc=object(),
            session=session,
            max_retries=2,
            max_pages=1,
            sleeper=delays.append,
        )
        with self.assertRaises(history.ProviderHttpError) as caught:
            provider.get_wallet_activity(ROOT, START, END)
        self.assertEqual(session.calls, 3)
        self.assertEqual(caught.exception.retry_count, 2)
        self.assertEqual(len(delays), 2)

    def test_repeated_timeout_cli_receipt_is_insufficient_data(self):
        valid_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6LBG83ByQ1foo123"
        valid_wallet = "11111111111111111111111111111111"
        output = io.StringIO()
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            analyzer,
            "_helius_provider",
            side_effect=history.ProviderTimeoutError(
                "Helius getTransactionsForAddress timed out after 3 attempts; URL redacted",
                retry_count=2,
            ),
        ), redirect_stdout(output), redirect_stderr(errors):
            code = analyzer.main(
                [
                    "--provider-retries", "2",
                    "cluster-audit",
                    "--mint", valid_mint,
                    "--wallet", valid_wallet,
                    "--days", "2",
                    "--graph-depth", "0",
                    "--max-wallets", "1",
                    "--max-pages", "1",
                    "--output-dir", temporary,
                ]
            )
            receipts = list(Path(temporary).glob("cluster-audit-*.json"))
            self.assertEqual(len(receipts), 1)
            receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
        self.assertEqual(code, 2)
        self.assertEqual(receipt["historical_exit_status"], "INSUFFICIENT_DATA")
        self.assertEqual(receipt["current_wallet_status"], "INSUFFICIENT_DATA")
        self.assertEqual(receipt["cluster_status"], "INSUFFICIENT_DATA")
        self.assertEqual(receipt["provider_coverage"]["requests"][0]["retry_count"], 2)
        self.assertEqual(receipt["provider_coverage"]["requests"][0]["failure_category"], "TIMEOUT")
        self.assertNotIn("VERIFIED_OUT", output.getvalue() + errors.getvalue())


class LifecycleTests(unittest.TestCase):
    def test_verified_exit_then_two_buys_is_reentered(self):
        sale_amount = 7_521_892_705_710
        first = 1_926_809_500_766
        second = 1_872_015_512_595
        transactions = [
            sale_tx("exit", amount=sale_amount, block_time=1_800_000_100),
            buy_tx("reentry-one", amount=first, block_time=1_800_000_200),
            buy_tx("reentry-two", amount=second, block_time=1_800_000_300),
        ]
        events = history.normalize_transactions(transactions, TARGET_MINT, include_unknown_increases=True)
        result = lifecycle.analyze_wallet_lifecycle(
            events,
            ROOT,
            first + second,
            coverage_complete=True,
            material_threshold_raw=0,
        )
        self.assertEqual(result["historical_exit_status"], "VERIFIED_OUT")
        self.assertEqual(result["current_wallet_status"], "RE_ENTERED")
        self.assertEqual(result["reconstructed_starting_inventory_raw"], sale_amount)
        self.assertEqual(result["reacquisitions"]["count"], 2)
        self.assertEqual(result["reacquisitions"]["amount_raw"], first + second)
        self.assertTrue(result["reconciled"])

    def test_current_inventory_without_prior_zero_is_not_reentered(self):
        events = history.normalize_transactions(
            [buy_tx("only-buy", amount=500, block_time=1_800_000_200)],
            TARGET_MINT,
            include_unknown_increases=True,
        )
        result = lifecycle.analyze_wallet_lifecycle(
            events, ROOT, 500, coverage_complete=True, material_threshold_raw=0
        )
        self.assertEqual(result["historical_exit_status"], "NOT_OUT")
        self.assertEqual(result["current_wallet_status"], "NOT_OUT")
        self.assertEqual(result["reacquisitions"]["count"], 0)

    def test_incomplete_history_cannot_claim_reentry(self):
        events = history.normalize_transactions(
            [sale_tx("exit-incomplete", amount=1_000), buy_tx("buy-incomplete", amount=100)],
            TARGET_MINT,
            include_unknown_increases=True,
        )
        result = lifecycle.analyze_wallet_lifecycle(
            events, ROOT, 100, coverage_complete=False, material_threshold_raw=0
        )
        self.assertEqual(result["historical_exit_status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["current_wallet_status"], "NOT_OUT")
        self.assertNotEqual(result["current_wallet_status"], "RE_ENTERED")

    def test_linked_wallet_balance_cannot_enter_root_arithmetic(self):
        events = history.normalize_transactions(
            [sale_tx("root-sale", amount=1_000), buy_tx("linked-buy", wallet=WALLET_B, amount=5_000)],
            TARGET_MINT,
            include_unknown_increases=True,
        )
        result = lifecycle.analyze_wallet_lifecycle(
            events, ROOT, 0, coverage_complete=True, material_threshold_raw=0
        )
        self.assertEqual(result["reconstructed_starting_inventory_raw"], 1_000)
        self.assertEqual(result["reacquisitions"]["count"], 0)
        self.assertEqual(result["inventory_scope"], "SEED_WALLET_ONLY")


class ProgressAndContractTests(unittest.TestCase):
    def test_progress_has_stable_phases_and_no_fake_percentages(self):
        tracker = progress.ProgressTracker(clock=lambda: "2026-08-29T00:00:00Z")
        tracker.running("WALLET_HISTORY", "Fetching https://host.invalid/?api-key=secret", count=1)
        record = tracker.to_record()
        self.assertEqual(tuple(record["phases"]), progress.PHASES)
        self.assertNotIn("host.invalid", json.dumps(record))
        self.assertFalse(any("percent" in event for event in record["events"]))

    def test_failure_receipt_satisfies_stable_required_contract(self):
        tracker = progress.ProgressTracker(clock=lambda: "2026-08-29T00:00:00Z")
        report = report_contract.failure_result(
            mint=TARGET_MINT,
            seed_wallet=ROOT,
            start=START,
            end=END,
            reason="bounded timeout",
            provider_telemetry={"retry_count": 2, "failure_category": "TIMEOUT"},
            progress=tracker,
        )
        schema_path = Path(__file__).parents[1] / "schemas" / "jeet-analyzer-result-v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertTrue(set(schema["required"]).issubset(report))
        self.assertEqual(report["common_control"], "NOT_PROVEN")
        self.assertNotEqual(report["cluster_status"], "VERIFIED_OUT")
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "result.json"
            events_path = Path(temporary) / "events.jsonl"
            reporting.write_cluster_receipts(report_path, events_path, report)
            saved = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["progress"]["phases"]["RECEIPT_FINALIZATION"], "complete")
        self.assertEqual(saved["evidence_receipts"]["result_json"], str(report_path))


if __name__ == "__main__":
    unittest.main()
