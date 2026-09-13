from __future__ import annotations

import pytest

from jeet_analyzer import history
from jeet_analyzer.analyzer import build_parser
from jeet_analyzer.cluster import ClusterAuditConfig
from jeet_analyzer.investigation import estimated_provider_credit_weight


SYSTEM = "11111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"


def test_current_helius_gtfa_credit_weight_is_100() -> None:
    assert estimated_provider_credit_weight("rpc", "getTransactionsForAddress") == 100


def test_cluster_cli_defaults_to_bounded_graph_wallet_history() -> None:
    args = build_parser().parse_args(
        ["cluster-audit", "--mint", WSOL, "--wallet", SYSTEM]
    )
    assert args.max_graph_wallet_pages == 250


def test_cluster_config_rejects_nonpositive_graph_wallet_page_cap() -> None:
    with pytest.raises(history.HistoryError):
        ClusterAuditConfig(max_graph_wallet_pages=0).validate()


def test_helius_bounded_graph_wallet_history_stops_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = history.HeliusHistoricalProvider(
        "test-key",
        object(),
        max_pages=5000,
        full_history_page_limit=100,
    )
    calls = 0
    consumed: list[str] = []

    def fake_stream_page(params, consumer):
        nonlocal calls
        calls += 1
        signature = f"sig-{calls}"
        consumer({"signature": signature, "blockTime": calls})
        return f"cursor-{calls}"

    monkeypatch.setattr(provider, "_stream_history_page", fake_stream_page)

    batch = provider.stream_wallet_activity_bounded(
        SYSTEM,
        1,
        100,
        lambda row: consumed.append(str(row["signature"])),
        max_pages=3,
    )

    assert calls == 3
    assert consumed == ["sig-1", "sig-2", "sig-3"]
    assert batch.pages == 3
    assert batch.complete is False
    assert batch.failure_category == "HIGH_ACTIVITY_HUB"
    assert "max_graph_wallet_pages=3" in batch.limitation
    assert f"wallet={SYSTEM}" in batch.coverage_scope
