from __future__ import annotations

import io
import json
import unittest
from typing import Any

from jeet_analyzer import history


class _Response:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self) -> None:
        self.raw = io.BytesIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"data": []}}).encode("utf-8")
        )
        self.closed = False

    def json(self) -> dict[str, Any]:
        raise AssertionError("streamed history must not call response.json()")

    def close(self) -> None:
        self.closed = True
        self.raw.close()


class _CaptureSession:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.stream_flags: list[bool] = []
        self.responses: list[_Response] = []

    def post(
        self,
        _url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        stream: bool = False,
    ) -> _Response:
        del timeout
        self.payloads.append(json)
        self.stream_flags.append(stream)
        response = _Response()
        self.responses.append(response)
        return response


class HistoryPageLimitTests(unittest.TestCase):
    def _requested_limit(self, provider: history.HeliusHistoricalProvider, session: _CaptureSession) -> int:
        batch = provider.stream_wallet_activity(
            "Wallet111111111111111111111111111111111111",
            1_800_000_000,
            1_800_000_100,
            lambda _transaction: None,
        )
        self.assertTrue(batch.complete)
        self.assertEqual(batch.pages, 1)
        self.assertEqual(len(session.payloads), 1)
        self.assertEqual(session.stream_flags, [True])
        self.assertTrue(session.responses[0].closed)
        payload = session.payloads[0]
        self.assertEqual(payload["method"], "getTransactionsForAddress")
        return int(payload["params"][1]["limit"])

    def test_default_full_history_page_limit_restores_provider_efficiency(self) -> None:
        session = _CaptureSession()
        provider = history.HeliusHistoricalProvider(
            "test-key",
            rpc=object(),
            session=session,
            max_retries=0,
        )
        self.assertEqual(self._requested_limit(provider, session), 100)
        self.assertEqual(provider.telemetry()["full_history_page_limit"], 100)

    def test_full_history_page_limit_is_configurable_within_provider_contract(self) -> None:
        session = _CaptureSession()
        provider = history.HeliusHistoricalProvider(
            "test-key",
            rpc=object(),
            session=session,
            max_retries=0,
            full_history_page_limit=25,
        )
        self.assertEqual(self._requested_limit(provider, session), 25)

    def test_full_history_page_limit_rejects_out_of_range_values(self) -> None:
        for value in (0, 101):
            with self.subTest(value=value):
                with self.assertRaises(history.ProviderError):
                    history.HeliusHistoricalProvider(
                        "test-key",
                        rpc=object(),
                        session=_CaptureSession(),
                        max_retries=0,
                        full_history_page_limit=value,
                    )


if __name__ == "__main__":
    unittest.main()
