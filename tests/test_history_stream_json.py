from __future__ import annotations

import io
import json
import unittest
from typing import Any

from jeet_analyzer import history
from jeet_analyzer.investigation import InvestigationContext, InvestigationLimits


class _StreamingResponse:
    def __init__(
        self,
        body: dict[str, Any],
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.raw = io.BytesIO(json.dumps(body).encode("utf-8"))
        self.closed = False
        self.json_called = False

    def json(self) -> dict[str, Any]:
        self.json_called = True
        raise AssertionError("R11 history path must not call response.json()")

    def close(self) -> None:
        self.closed = True
        self.raw.close()


class _StreamingSession:
    def __init__(self, responses: list[_StreamingResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []
        self.stream_flags: list[bool] = []

    def post(
        self,
        _url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        stream: bool = False,
    ) -> _StreamingResponse:
        del timeout
        self.calls += 1
        self.payloads.append(json)
        self.stream_flags.append(stream)
        return self.responses[self.calls - 1]


def _transaction(signature: str, block_time: int, *, padding: int = 0, ratio: float | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "err": None,
        "fee": 0,
        "preBalances": [],
        "postBalances": [],
        "preTokenBalances": [],
        "postTokenBalances": [],
        "innerInstructions": [],
        "logMessages": ["x" * padding] if padding else [],
    }
    if ratio is not None:
        meta["testRatio"] = ratio
    return {
        "blockTime": block_time,
        "transaction": {
            "signatures": [signature],
            "message": {"accountKeys": [], "instructions": []},
        },
        "meta": meta,
    }


def _body(data: list[dict[str, Any]], pagination_token: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"data": data}
    if pagination_token is not None:
        result["paginationToken"] = pagination_token
    return {"jsonrpc": "2.0", "id": 1, "result": result}


class HistoryJsonStreamingTests(unittest.TestCase):
    def test_full_100_transaction_page_streams_without_response_json(self) -> None:
        rows = [
            _transaction(f"sig-{index}", 1_800_000_000 + index, padding=2048)
            for index in range(100)
        ]
        response = _StreamingResponse(_body(rows))
        session = _StreamingSession([response])
        provider = history.HeliusHistoricalProvider(
            "test-key", rpc=object(), session=session, max_retries=0
        )
        seen: list[str] = []

        batch = provider.stream_wallet_activity(
            "Wallet111111111111111111111111111111111111",
            1_800_000_000,
            1_800_001_000,
            lambda row: seen.append(history.transaction_signature(row) or ""),
        )

        self.assertTrue(batch.complete)
        self.assertEqual(batch.pages, 1)
        self.assertEqual(len(seen), 100)
        self.assertEqual(session.stream_flags, [True])
        self.assertEqual(session.payloads[0]["params"][1]["limit"], 100)
        self.assertFalse(response.json_called)
        self.assertTrue(response.closed)

    def test_two_full_pages_cost_two_requests_not_twenty(self) -> None:
        first = [_transaction(f"a-{index}", 1_800_000_000 + index) for index in range(100)]
        second = [_transaction(f"b-{index}", 1_800_000_100 + index) for index in range(25)]
        session = _StreamingSession(
            [
                _StreamingResponse(_body(first, "page-2")),
                _StreamingResponse(_body(second)),
            ]
        )
        investigation = InvestigationContext(
            InvestigationLimits(
                max_rpc_requests=100,
                max_signatures=10_000,
                max_transactions=10_000,
                max_estimated_provider_credits=10_000,
            )
        )
        provider = history.HeliusHistoricalProvider(
            "test-key",
            rpc=object(),
            session=session,
            max_retries=0,
            investigation=investigation,
        )
        seen: list[str] = []

        batch = provider.stream_wallet_activity(
            "Wallet111111111111111111111111111111111111",
            1_800_000_000,
            1_800_001_000,
            lambda row: seen.append(history.transaction_signature(row) or ""),
        )

        telemetry = investigation.to_record()
        self.assertTrue(batch.complete)
        self.assertEqual(batch.pages, 2)
        self.assertEqual(len(seen), 125)
        self.assertEqual(telemetry["total_provider_requests"], 2)
        self.assertEqual(telemetry["estimated_provider_credits"], 200)
        self.assertEqual(telemetry["pages_fetched"], 2)
        self.assertEqual(telemetry["history_page_manifests"], 2)

    def test_stream_parser_preserves_float_semantics(self) -> None:
        response = _StreamingResponse(
            _body([_transaction("float-sig", 1_800_000_001, ratio=1.25)])
        )
        provider = history.HeliusHistoricalProvider(
            "test-key", rpc=object(), session=_StreamingSession([response]), max_retries=0
        )
        ratios: list[Any] = []
        provider.stream_wallet_activity(
            "Wallet111111111111111111111111111111111111",
            1_800_000_000,
            1_800_000_100,
            lambda row: ratios.append(row["meta"]["testRatio"]),
        )
        self.assertEqual(ratios, [1.25])
        self.assertIsInstance(ratios[0], float)

    def test_json_rpc_rate_limit_retries_streaming_page(self) -> None:
        session = _StreamingSession(
            [
                _StreamingResponse({"jsonrpc": "2.0", "id": 1, "error": {"code": -32029, "message": "rate"}}),
                _StreamingResponse(_body([_transaction("after-retry", 1_800_000_001)])),
            ]
        )
        provider = history.HeliusHistoricalProvider(
            "test-key",
            rpc=object(),
            session=session,
            max_retries=1,
            sleeper=lambda _seconds: None,
        )
        seen: list[str] = []
        batch = provider.stream_wallet_activity(
            "Wallet111111111111111111111111111111111111",
            1_800_000_000,
            1_800_000_100,
            lambda row: seen.append(history.transaction_signature(row) or ""),
        )
        self.assertTrue(batch.complete)
        self.assertEqual(seen, ["after-retry"])
        self.assertEqual(session.calls, 2)
        self.assertEqual(provider.total_retry_count, 1)

    def test_malformed_streamed_history_shape_fails_closed(self) -> None:
        provider = history.HeliusHistoricalProvider(
            "test-key",
            rpc=object(),
            session=_StreamingSession(
                [_StreamingResponse({"jsonrpc": "2.0", "id": 1, "result": {"paginationToken": "x"}})]
            ),
            max_retries=0,
        )
        with self.assertRaises(history.ProviderMalformedResponseError):
            provider.stream_wallet_activity(
                "Wallet111111111111111111111111111111111111",
                1_800_000_000,
                1_800_000_100,
                lambda _row: None,
            )

    def test_cached_history_replay_avoids_provider_without_whole_page_reconstruction(self) -> None:
        transaction = _transaction("cache-me", 1_800_000_001, padding=4096)
        session = _StreamingSession([_StreamingResponse(_body([transaction]))])
        investigation = InvestigationContext()
        provider = history.HeliusHistoricalProvider(
            "test-key",
            rpc=object(),
            session=session,
            max_retries=0,
            investigation=investigation,
        )
        first: list[str] = []
        second: list[str] = []
        args = (
            "Wallet111111111111111111111111111111111111",
            1_800_000_000,
            1_800_000_100,
        )
        provider.stream_wallet_activity(*args, lambda row: first.append(history.transaction_signature(row) or ""))
        provider.stream_wallet_activity(*args, lambda row: second.append(history.transaction_signature(row) or ""))
        telemetry = investigation.to_record()
        self.assertEqual(first, ["cache-me"])
        self.assertEqual(second, ["cache-me"])
        self.assertEqual(session.calls, 1)
        self.assertEqual(telemetry["pages_fetched"], 1)
        self.assertEqual(telemetry["cache_hits_by_namespace"], {"history_page": 1})
        self.assertEqual(telemetry["provider_requests_avoided"], 1)
        self.assertEqual(telemetry["history_page_manifests"], 1)


if __name__ == "__main__":
    unittest.main()
