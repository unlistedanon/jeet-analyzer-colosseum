from __future__ import annotations

from decimal import Decimal

from jeet_analyzer import cluster, history
from jeet_analyzer.tx_classifier import TOKEN_PROGRAM
from tests.fixtures.builders import (
    ROOT,
    TARGET_MINT,
    WALLET_B,
    FakeProvider,
    FakeRpc,
    account_record,
    sale_tx,
    token_transfer_tx,
)


START = 1_799_900_000
END = 1_800_100_000
METADATA = history.TokenMetadata(
    TARGET_MINT,
    "TGT",
    "Synthetic Target",
    6,
    1_000_000,
    token_program=TOKEN_PROGRAM,
)


class MintScopedSeedProvider(FakeProvider):
    """Mimic live behavior where seed mint history omits cross-asset links.

    The cross-asset direct link appears only during the later full seed-activity
    pass, matching the hosted Journey path that exposed the first-hop gap.
    """

    def get_wallet_events(self, wallet, mint, start, end):
        self.calls.append(("wallet_events", wallet))
        if wallet == ROOT:
            return self._batch(
                self.target_transactions,
                complete=True,
                scope="mint-scoped seed fixture",
            )
        return self._batch(
            self.activity.get(wallet, []),
            complete=True,
            scope="mint-scoped downstream fixture",
        )


def test_seed_full_activity_direct_link_is_still_fetched_as_first_hop() -> None:
    sale = sale_tx()
    direct = token_transfer_tx(
        "seed-full-activity-cross-asset-link",
        source_owner=ROOT,
        destination_owner=WALLET_B,
        amount=777,
        block_time=1_800_000_300,
    )
    provider = MintScopedSeedProvider(
        target_transactions=[sale],
        activity={ROOT: [sale, direct], WALLET_B: [direct]},
        balances={ROOT: 0, WALLET_B: 0},
    )
    rpc = FakeRpc(
        {ROOT: 0, WALLET_B: 0},
        {ROOT: account_record(), WALLET_B: account_record()},
    )

    report = cluster.run_cluster_audit(
        provider,
        rpc,
        METADATA,
        ROOT,
        START,
        END,
        config=cluster.ClusterAuditConfig(
            graph_depth=3,
            max_wallets=25,
            funding_lookback_days=365,
            materiality_inventory_pct=Decimal("0.01"),
            deep_forensic=False,
        ),
    )

    nodes = {row["wallet"]: row for row in report["wallet_graph"]["nodes"]}
    assert WALLET_B in nodes
    assert nodes[WALLET_B]["depth"] == 1
    assert nodes[WALLET_B]["history_traversal"] == "FETCHED"
    assert report["provider_coverage"]["complete"] is True
    assert report["provider_coverage"]["downstream_complete"] is True
    assert report["target_cluster_status"] == "VERIFIED_OUT"
    assert report["common_control"] == "NOT_PROVEN"
    assert not any(
        row.get("kind") == "DEEP_TRAVERSAL_PRIORITIZED"
        for row in report["unresolved_relationships"]
    )
