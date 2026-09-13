from __future__ import annotations

from jeet_analyzer.progress import ProgressTracker


def test_live_terminal_emits_non_pending_progress(capsys) -> None:
    tracker = ProgressTracker(live_terminal=True)
    assert capsys.readouterr().err == ""

    tracker.running(
        "RELATIONSHIP_GRAPH",
        "Building evidence-backed bounded wallet graph",
        count=12,
        progress={"wallets_discovered": 7, "wallets_queued": 3},
    )

    output = capsys.readouterr().err
    assert "[JEET] RELATIONSHIP_GRAPH RUNNING" in output
    assert "Building evidence-backed bounded wallet graph" in output
    assert "count=12" in output
    assert "wallets_discovered=7" in output
    assert "wallets_queued=3" in output


def test_noninteractive_progress_remains_quiet(capsys) -> None:
    tracker = ProgressTracker(live_terminal=False)
    tracker.running("WALLET_HISTORY", "Fetching history", count=4)
    assert capsys.readouterr().err == ""
