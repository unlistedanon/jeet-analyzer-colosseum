from __future__ import annotations

import unittest
from decimal import Decimal

from jeet_analyzer import cluster, history
from jeet_analyzer.investigation import InvestigationContext
from jeet_analyzer.models import RelationshipEdge
from jeet_analyzer.tx_classifier import TOKEN_PROGRAM
from tests.fixtures.builders import (
    OTHER_MINT,
    ROOT,
    TARGET_MINT,
    WALLET_B,
    WALLET_C,
    FakeProvider,
    FakeRpc,
    account_record,
    sale_tx,
    signed_only_tx,
    token_transfer_tx,
)


METADATA = history.TokenMetadata(
    TARGET_MINT,
    "TGT",
    "Synthetic Target",
    6,
    1_000_000,
    token_program=TOKEN_PROGRAM,
)
START = 1_799_900_000
END = 1_800_100_000


def relationship_edge(
    *,
    relationship_type: str = "DIRECT_TOKEN_TRANSFER",
    classification: str | None = None,
    asset: str = OTHER_MINT,
    amount_raw: int = 100,
    confidence: str = "HIGH",
) -> RelationshipEdge:
    return RelationshipEdge(
        ROOT,
        WALLET_B,
        relationship_type,
        classification or relationship_type,
        "sig",
        "2026-01-01T00:00:00Z",
        1_800_000_000,
        asset,
        amount_raw,
        [ROOT],
        ROOT,
        "fixture relationship",
        confidence,
    )


def run_audit(*, activity, balances, investigation=None, max_wallets=20):
    provider = FakeProvider(
        target_transactions=[sale_tx()],
        activity=activity,
        balances=balances,
    )
    rpc = FakeRpc(
        balances,
        {wallet: account_record() for wallet in balances},
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
            max_wallets=max_wallets,
            funding_lookback_days=7,
            materiality_inventory_pct=Decimal("0.01"),
            deep_forensic=True,
        ),
        investigation=investigation,
    )
    return report, provider


class DeepTraversalPolicyTests(unittest.TestCase):
    def test_wallet_cap_admits_repeated_evidence_before_large_unrelated_transfer(self):
        sale = sale_tx()
        noise = token_transfer_tx("large-noise", destination_owner=WALLET_B, amount=10**15)
        preparation = token_transfer_tx("repeat-one", destination_owner=WALLET_C, amount=1)
        repeated = token_transfer_tx("repeat-two", destination_owner=WALLET_C, amount=1)
        report, provider = run_audit(
            activity={ROOT: [sale, noise, preparation, repeated], WALLET_B: [noise], WALLET_C: [preparation, repeated]},
            balances={ROOT: 0, WALLET_B: 0, WALLET_C: 0},
            max_wallets=2,
        )
        self.assertIn(WALLET_C, provider.activity_calls)
        self.assertNotIn(WALLET_B, provider.activity_calls)
        self.assertLessEqual(len(provider.activity_calls), 2)
        self.assertEqual(report["cluster_status"], "UNRESOLVED")

    def test_unrelated_token_raw_units_do_not_change_priority(self):
        small = relationship_edge(amount_raw=1)
        large = relationship_edge(amount_raw=10**24)
        self.assertEqual(cluster._edge_priority(small, TARGET_MINT), cluster._edge_priority(large, TARGET_MINT))

    def test_preparation_and_repeated_evidence_beat_large_unrelated_transfer(self):
        noise = relationship_edge(amount_raw=10**24)
        for edge in (
            relationship_edge(relationship_type="ATA_PREPARATION", amount_raw=1),
            relationship_edge(classification="REPEATED_DIRECT_LINK", amount_raw=1),
        ):
            self.assertGreater(cluster._edge_priority(edge, TARGET_MINT), cluster._edge_priority(noise, TARGET_MINT))

    def test_target_amounts_remain_comparable_and_target_stays_first(self):
        small = relationship_edge(asset=TARGET_MINT, amount_raw=1, confidence="MEDIUM")
        large = relationship_edge(asset=TARGET_MINT, amount_raw=100, confidence="MEDIUM")
        other = relationship_edge(relationship_type="CONFIRMED_DIRECT_LINK", amount_raw=10**24)
        self.assertGreater(cluster._edge_priority(large, TARGET_MINT), cluster._edge_priority(small, TARGET_MINT))
        self.assertGreater(cluster._edge_priority(small, TARGET_MINT), cluster._edge_priority(other, TARGET_MINT))

    def test_target_token_relationship_always_deepens(self):
        edge = relationship_edge(asset=TARGET_MINT, confidence="MEDIUM")
        self.assertEqual(
            cluster._deep_traversal_reason(edge, TARGET_MINT),
            "TARGET_TOKEN_RELATIONSHIP",
        )

    def test_weak_cross_asset_direct_transfer_stays_shallow(self):
        edge = relationship_edge()
        self.assertIsNone(cluster._deep_traversal_reason(edge, TARGET_MINT))

    def test_high_signal_control_or_preparation_relationship_deepens(self):
        for relationship_type in ("CONFIRMED_DIRECT_LINK", "ATA_PREPARATION"):
            with self.subTest(relationship_type=relationship_type):
                edge = relationship_edge(relationship_type=relationship_type)
                self.assertEqual(
                    cluster._deep_traversal_reason(edge, TARGET_MINT),
                    relationship_type,
                )

    def test_single_cosignature_stays_visible_without_recursive_history(self):
        edge = relationship_edge(relationship_type="NON_MARKET_CO_SIGNER")
        self.assertIsNone(cluster._deep_traversal_reason(edge, TARGET_MINT))

    def test_downstream_cosigner_is_reported_but_not_fetched(self):
        sale = sale_tx()
        bridge = token_transfer_tx(
            "bridge-to-b",
            source_owner=ROOT,
            destination_owner=WALLET_B,
            mint=OTHER_MINT,
            block_time=1_800_000_300,
        )
        cosign = signed_only_tx("downstream-cosign", WALLET_B, block_time=1_800_000_400)
        keys = cosign["transaction"]["message"]["accountKeys"]
        keys.append({"pubkey": WALLET_C, "signer": True})
        cosign["meta"]["preBalances"].append(1_000_000)
        cosign["meta"]["postBalances"].append(1_000_000)
        report, provider = run_audit(
            activity={ROOT: [sale, bridge], WALLET_B: [bridge, cosign]},
            balances={ROOT: 0, WALLET_B: 0, WALLET_C: 0},
        )

        self.assertIn(WALLET_B, provider.activity_calls)
        self.assertNotIn(WALLET_C, provider.activity_calls)
        self.assertIn(WALLET_C, {node["wallet"] for node in report["wallet_graph"]["nodes"]})
        self.assertTrue(
            any(
                edge["relationship_type"] == "NON_MARKET_CO_SIGNER"
                and {edge["source"], edge["destination"]} == {WALLET_B, WALLET_C}
                for edge in report["wallet_graph"]["edges"]
            )
        )

    def test_medium_confidence_cross_asset_control_signal_stays_shallow(self):
        edge = relationship_edge(
            relationship_type="CONFIRMED_DIRECT_LINK",
            confidence="MEDIUM",
        )
        self.assertIsNone(cluster._deep_traversal_reason(edge, TARGET_MINT))

    def test_repeated_direct_link_deepens(self):
        edge = relationship_edge(classification="REPEATED_DIRECT_LINK")
        self.assertEqual(
            cluster._deep_traversal_reason(edge, TARGET_MINT),
            "REPEATED_DIRECT_LINK",
        )

    def test_dust_sol_stays_shallow_but_material_sol_deepens(self):
        dust = relationship_edge(
            relationship_type="PLAIN_DIRECT_SOL_TRANSFER",
            classification="DIRECT_SIGNED_SOL_FUNDING",
            asset="SOL",
            amount_raw=3,
        )
        material = relationship_edge(
            relationship_type="PLAIN_DIRECT_SOL_TRANSFER",
            classification="DIRECT_SIGNED_SOL_FUNDING",
            asset="SOL",
            amount_raw=cluster.MIN_DEEP_SOL_FUNDING_LAMPORTS,
        )
        self.assertIsNone(cluster._deep_traversal_reason(dust, TARGET_MINT))
        self.assertEqual(
            cluster._deep_traversal_reason(material, TARGET_MINT),
            "MATERIAL_DIRECT_SOL_FUNDING",
        )

    def test_downstream_weak_link_remains_visible_without_recursive_history(self):
        sale = sale_tx()
        ab = token_transfer_tx(
            "weak-ab",
            source_owner=ROOT,
            destination_owner=WALLET_B,
            mint=OTHER_MINT,
            block_time=1_800_000_300,
        )
        bc = token_transfer_tx(
            "weak-bc",
            source_owner=WALLET_B,
            destination_owner=WALLET_C,
            mint=OTHER_MINT,
            block_time=1_800_000_400,
        )
        report, provider = run_audit(
            activity={ROOT: [sale, ab], WALLET_B: [ab, bc], WALLET_C: [bc]},
            balances={ROOT: 0, WALLET_B: 0, WALLET_C: 0},
        )
        self.assertIn(WALLET_B, provider.activity_calls)
        self.assertNotIn(WALLET_C, provider.activity_calls)
        self.assertIn(WALLET_C, {node["wallet"] for node in report["wallet_graph"]["nodes"]})
        self.assertTrue(
            any(
                edge["source"] == WALLET_B and edge["destination"] == WALLET_C
                for edge in report["wallet_graph"]["edges"]
            )
        )
        node = next(node for node in report["wallet_graph"]["nodes"] if node["wallet"] == WALLET_C)
        self.assertEqual(node["history_traversal"], "NOT_DEEPENED")
        self.assertEqual(report["cluster_status"], "INSUFFICIENT_DATA")
        self.assertTrue(
            any(
                row.get("kind") == "DEEP_TRAVERSAL_PRIORITIZED"
                for row in report["unresolved_relationships"]
            )
        )

    def test_downstream_target_token_link_gets_recursive_history(self):
        sale = sale_tx()
        ab = token_transfer_tx(
            "target-ab",
            source_owner=ROOT,
            destination_owner=WALLET_B,
            mint=OTHER_MINT,
            block_time=1_800_000_300,
        )
        bc = token_transfer_tx(
            "target-bc",
            source_owner=WALLET_B,
            destination_owner=WALLET_C,
            mint=TARGET_MINT,
            block_time=1_800_000_400,
        )
        _report, provider = run_audit(
            activity={ROOT: [sale, ab], WALLET_B: [ab, bc], WALLET_C: [bc]},
            balances={ROOT: 0, WALLET_B: 0, WALLET_C: 0},
        )
        self.assertIn(WALLET_C, provider.activity_calls)

    def test_seed_neighbor_queue_is_unique_across_repeated_edges(self):
        sale = sale_tx()
        first = token_transfer_tx(
            "repeat-one",
            source_owner=ROOT,
            destination_owner=WALLET_B,
            mint=OTHER_MINT,
            block_time=1_800_000_300,
        )
        second = token_transfer_tx(
            "repeat-two",
            source_owner=ROOT,
            destination_owner=WALLET_B,
            mint=OTHER_MINT,
            block_time=1_800_000_400,
        )
        context = InvestigationContext()
        report, provider = run_audit(
            activity={ROOT: [sale, first, second], WALLET_B: [first, second]},
            balances={ROOT: 0, WALLET_B: 0},
            investigation=context,
        )
        self.assertEqual(provider.activity_calls.count(WALLET_B), 1)
        self.assertEqual(report["request_telemetry"]["wallet_traversals_suppressed"], 0)


if __name__ == "__main__":
    unittest.main()
