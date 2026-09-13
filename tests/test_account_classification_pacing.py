from __future__ import annotations

from jeet_analyzer import cluster
from jeet_analyzer.analyzer import SYSTEM_PROGRAM


class PacingRpc:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.sleeps: list[float] = []

    def sleeper(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def get_multiple_accounts(self, addresses):
        chunk = list(addresses)
        self.calls.append(chunk)
        return {
            address: {
                "owner": SYSTEM_PROGRAM,
                "executable": False,
                "data": ["", "base64"],
            }
            for address in chunk
        }


class FailingMiddleBatchRpc(PacingRpc):
    def get_multiple_accounts(self, addresses):
        chunk = list(addresses)
        self.calls.append(chunk)
        if len(self.calls) == 2:
            raise RuntimeError("synthetic rate-limit failure")
        return {
            address: {
                "owner": SYSTEM_PROGRAM,
                "executable": False,
                "data": ["", "base64"],
            }
            for address in chunk
        }


def _addresses(count: int) -> list[str]:
    return [f"Wallet{index:04d}" for index in range(count)]


def test_account_classification_batches_and_paces_live_rpc() -> None:
    rpc = PacingRpc()
    addresses = _addresses(250)

    classifications, notes = cluster._classify_addresses(rpc, addresses)

    assert [len(chunk) for chunk in rpc.calls] == [100, 100, 50]
    assert rpc.sleeps == [
        cluster.ACCOUNT_CLASSIFICATION_MIN_INTERVAL_SECONDS,
        cluster.ACCOUNT_CLASSIFICATION_MIN_INTERVAL_SECONDS,
    ]
    assert len(classifications) == 250
    assert set(classifications.values()) == {"WALLET_LIKE"}
    assert all(notes[address] for address in addresses)
    assert cluster._classification_batch_count(250) == 3


def test_failed_classification_chunk_stays_fail_closed_and_continues() -> None:
    rpc = FailingMiddleBatchRpc()
    addresses = _addresses(250)

    classifications, _notes = cluster._classify_addresses(rpc, addresses)

    assert [len(chunk) for chunk in rpc.calls] == [100, 100, 50]
    assert rpc.sleeps == [
        cluster.ACCOUNT_CLASSIFICATION_MIN_INTERVAL_SECONDS,
        cluster.ACCOUNT_CLASSIFICATION_MIN_INTERVAL_SECONDS,
    ]
    assert all(classifications[address] == "WALLET_LIKE" for address in addresses[:100])
    assert all(classifications[address] != "WALLET_LIKE" for address in addresses[100:200])
    assert all(classifications[address] == "WALLET_LIKE" for address in addresses[200:])
