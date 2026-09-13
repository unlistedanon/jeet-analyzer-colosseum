from __future__ import annotations

import io
import json
import inspect
import unittest
from typing import Any

from jeet_analyzer import cluster, history


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self.raw = io.BytesIO(json.dumps(body).encode("utf-8"))
        self.closed = False

    def json(self) -> dict[str, Any]:
        raise AssertionError("streamed history must not materialize response.json()")

    def close(self) -> None:
        self.closed = True
        self.raw.close()


class _CheckingSession:
    def __init__(self, bodies: list[dict[str, Any]]) -> None:
        self._bodies = list(bodies)
        self.calls = 0
        self.responses: list[_FakeResponse] = []
        self.stream_flags: list[bool] = []

    def post(
        self,
        _url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        stream: bool = False,
    ) -> _FakeResponse:
        del json, timeout
        if self.responses and not self.responses[-1].closed:
            raise AssertionError("previous history response was still open when next request began")
        self.calls += 1
        self.stream_flags.append(stream)
        response = _FakeResponse(self._bodies.pop(0))
        self.responses.append(response)
        return response


def _transaction(signature: str, block_time: int, padding: int = 0) -> dict[str, Any]:
    return {
        "blockTime": block_time,
        "transaction": {
            "signatures": [signature],
            "message": {"accountKeys": [], "instructions": []},
        },
        "meta": {
            "err": None,
            "fee": 0,
            "preBalances": [],
            "postBalances": [],
            "preTokenBalances": [],
            "postTokenBalances": [],
            "innerInstructions": [],
            "logMessages": ["x" * padding] if padding else [],
        },
    }


def _rpc_body(result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "result": result}


class HistoryStreamingTests(unittest.TestCase):
    def test_helius_wallet_activity_closes_previous_page_before_next_request(self) -> None:
        session = _CheckingSession(
            [
                _rpc_body({"data": [_transaction("first-page", 1_800_000_001, 100_000)], "paginationToken": "next-page"}),
                _rpc_body({"data": [_transaction("second-page", 1_800_000_002, 100_000)]}),
            ]
        )
        provider = history.HeliusHistoricalProvider(
            "test-key",
            rpc=object(),
            session=session,
            max_retries=0,
            max_pages=10,
        )
        seen: list[str] = []

        batch = provider.stream_wallet_activity(
            "Wallet111111111111111111111111111111111111",
            1_800_000_000,
            1_800_000_100,
            lambda transaction: seen.append(history.transaction_signature(transaction) or ""),
        )

        self.assertEqual(seen, ["first-page", "second-page"])
        self.assertEqual(batch.transactions, [])
        self.assertTrue(batch.complete)
        self.assertEqual(batch.pages, 2)
        self.assertEqual(session.calls, 2)
        self.assertEqual(session.stream_flags, [True, True])
        self.assertTrue(all(response.closed for response in session.responses))

    def test_helius_seed_and_mint_streams_return_coverage_without_raw_rows(self) -> None:
        for method_name in ("stream_wallet_events", "stream_token_events"):
            session = _CheckingSession([_rpc_body({"data": [_transaction(method_name, 1_800_000_001)]})])
            provider = history.HeliusHistoricalProvider(
                "test-key",
                rpc=object(),
                session=session,
                max_retries=0,
            )
            seen: list[str] = []
            if method_name == "stream_wallet_events":
                batch = provider.stream_wallet_events(
                    "Wallet111111111111111111111111111111111111",
                    "Mint11111111111111111111111111111111111111",
                    1_800_000_000,
                    1_800_000_100,
                    lambda row: seen.append(history.transaction_signature(row) or ""),
                )
            else:
                batch = provider.stream_token_events(
                    "Mint11111111111111111111111111111111111111",
                    1_800_000_000,
                    1_800_000_100,
                    lambda row: seen.append(history.transaction_signature(row) or ""),
                )
            self.assertEqual(seen, [method_name])
            self.assertEqual(batch.transactions, [])
            self.assertTrue(batch.complete)
            self.assertEqual(batch.pages, 1)

    def test_cluster_uses_streaming_boundaries_for_all_full_history_lanes(self) -> None:
        source = inspect.getsource(cluster.run_cluster_audit)
        self.assertIn("provider.stream_wallet_events", source)
        self.assertIn("provider.stream_wallet_activity", source)
        self.assertIn("provider.stream_token_events", source)
        self.assertNotIn("provider.get_wallet_activity(wallet, activity_start, end)", source)
        self.assertNotIn("provider.get_token_events(metadata.mint, start, end)", source)


if __name__ == "__main__":
    unittest.main()
