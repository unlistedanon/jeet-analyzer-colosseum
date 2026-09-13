from __future__ import annotations

import ast
import inspect
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from tests.fixtures.builders import (
    FUNDER,
    OTHER_MINT,
    POOL,
    ROOT,
    TARGET_MINT,
    UNKNOWN_PROGRAM,
    WALLET_B,
    WALLET_C,
    WALLET_D,
    FakeProvider,
    FakeRpc,
    account_record,
    buy_tx,
    sale_tx,
    signed_only_tx,
    sol_transfer_tx,
    token_transfer_tx,
)
from jeet_analyzer import analyzer, cluster, graph, history, reporting, tx_classifier
from jeet_analyzer.tx_classifier import TOKEN_2022_PROGRAM, TOKEN_PROGRAM


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


def run_audit(
    *,
    target_transactions=None,
    activity=None,
    balances=None,
    records=None,
    complete=True,
    incomplete_wallets=None,
    depth=3,
    max_wallets=50,
    materiality_pct=Decimal("0.01"),
    investigation=None,
    deep_forensic=True,
):
    target_transactions = target_transactions if target_transactions is not None else [sale_tx()]
    activity = activity if activity is not None else {ROOT: list(target_transactions)}
    balances = balances if balances is not None else {ROOT: 0}
    records = records if records is not None else {wallet: account_record() for wallet in balances}
    provider = FakeProvider(
        target_transactions=target_transactions,
        activity=activity,
        balances=balances,
        complete=complete,
        incomplete_wallets=incomplete_wallets,
    )
    rpc = FakeRpc(balances, records)
    report = cluster.run_cluster_audit(
        provider,
        rpc,
        METADATA,
        ROOT,
        START,
        END,
        config=cluster.ClusterAuditConfig(
            graph_depth=depth,
            max_wallets=max_wallets,
            funding_lookback_days=30,
            materiality_inventory_pct=materiality_pct,
            deep_forensic=deep_forensic,
        ),
        investigation=investigation,
    )
    return report, provider, rpc


class ClusterAuditTests(unittest.TestCase):
    def test_seed_sells_entire_bag_and_is_verified_out(self):
        report, _provider, _rpc = run_audit()
        self.assertEqual(report["wallet_status"], "VERIFIED_OUT")
        self.assertEqual(report["seed_wallet_accounting"]["confirmed_sales"]["target_token_sold_raw"], 1_000)
        self.assertEqual(report["cluster_status"], "VERIFIED_OUT")

    def test_cross_token_transfer_discovers_related_wallet(self):
        sale = sale_tx()
        cross = token_transfer_tx("cross-token", mint=OTHER_MINT, amount=777, block_time=1_800_000_300)
        report, provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, cross], WALLET_B: [cross]},
            balances={ROOT: 0, WALLET_B: 0},
        )
        self.assertIn(WALLET_B, provider.activity_calls)
        edge = next(row for row in report["wallet_graph"]["edges"] if row["asset"] == OTHER_MINT)
        self.assertEqual(edge["relationship_type"], "DIRECT_TOKEN_TRANSFER")

    def test_related_wallet_target_inventory_makes_cluster_not_out(self):
        sale = sale_tx()
        cross = token_transfer_tx("cross-holding", mint=OTHER_MINT, amount=777, block_time=1_800_000_300)
        report, _provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, cross], WALLET_B: [cross]},
            balances={ROOT: 0, WALLET_B: 5_000},
        )
        self.assertEqual(report["wallet_status"], "VERIFIED_OUT")
        self.assertEqual(report["cluster_status"], "NOT_OUT")
        self.assertEqual(report["visible_related_target_inventory_raw"], 5_000)
        self.assertEqual(report["common_control"], "NOT_PROVEN")

    def test_non_wallet_transfer_linked_inventory_blocks_cluster_verified_out(self):
        token_account = "TargetTokenAccountNode1111111111111111111111111"
        transfer = token_transfer_tx(
            "non-wallet-target-branch",
            destination_owner=token_account,
            mint=TARGET_MINT,
            amount=500,
            block_time=1_800_000_300,
            token_program=TOKEN_PROGRAM,
        )
        report, _provider, _rpc = run_audit(
            target_transactions=[transfer],
            activity={ROOT: [transfer]},
            balances={ROOT: 0, token_account: 500},
            records={
                ROOT: account_record(),
                token_account: account_record(owner=TOKEN_PROGRAM),
            },
        )
        self.assertEqual(report["wallet_status"], "VERIFIED_OUT")
        self.assertEqual(report["cluster_status"], "UNRESOLVED")
        self.assertEqual(report["unresolved_non_wallet_target_inventory_raw"], 500)

    def test_sender_created_ata_creates_link_but_rent_does_not(self):
        sale = sale_tx()
        direct = token_transfer_tx("ata-link", create_ata=True, block_time=1_800_000_300)
        destination_account = direct["meta"]["postTokenBalances"][1]["accountIndex"]
        del destination_account
        records = {
            ROOT: account_record(),
            WALLET_B: account_record(),
            next(
                row["pubkey"]
                for row in direct["transaction"]["message"]["accountKeys"]
                if str(row["pubkey"]).startswith("DestinationToken")
            ): account_record(owner=TOKEN_2022_PROGRAM),
        }
        report, _provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, direct], WALLET_B: [direct]},
            balances={ROOT: 0, WALLET_B: 0},
            records=records,
        )
        self.assertEqual(report["wallet_graph"]["edges"][0]["relationship_type"], "CONFIRMED_DIRECT_LINK")
        self.assertEqual(report["ata_rent"][0]["classification"], "ATA_RENT")
        self.assertFalse(report["ata_rent"][0]["creates_graph_edge"])

    def test_recipient_independently_signs_later_transaction(self):
        sale = sale_tx()
        cross = token_transfer_tx("signed-link", block_time=1_800_000_300)
        later = signed_only_tx("recipient-later", WALLET_B, block_time=1_800_000_400)
        report, _provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, cross], WALLET_B: [cross, later]},
            balances={ROOT: 0, WALLET_B: 0},
        )
        edge = report["wallet_graph"]["edges"][0]
        self.assertTrue(edge["recipient_independently_signed"])

    def test_clean_system_program_transfer_creates_strong_sol_edge(self):
        sale = sale_tx()
        funding = sol_transfer_tx("direct-sol", source=FUNDER, destination=ROOT, block_time=1_800_000_300)
        report, _provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, funding], FUNDER: [funding]},
            balances={ROOT: 0, FUNDER: 0},
        )
        edge = next(row for row in report["wallet_graph"]["edges"] if row["asset"] == "SOL")
        self.assertEqual(edge["relationship_type"], "PLAIN_DIRECT_SOL_TRANSFER")
        self.assertEqual(edge["classification"], "DIRECT_SIGNED_SOL_FUNDING")
        self.assertEqual(edge["source"], FUNDER)
        self.assertEqual(edge["destination"], ROOT)
        self.assertEqual(edge["signature"], "direct-sol")
        self.assertEqual(edge["amount_raw"], 10_000_000)
        self.assertEqual(edge["relationship_context"], "DIRECT_SIGNED_SOL_FUNDING")
        self.assertTrue(edge["source_signed"])
        self.assertTrue(edge["source_paid_fee"])
        self.assertFalse(edge["market_context"])
        self.assertEqual(edge["common_control"], "NOT_PROVEN")
        self.assertEqual(edge["confidence"], "HIGH")
        self.assertEqual(report["wallet_graph"]["shared_funders"], [])

    def test_direct_funding_edges_stay_direct_while_recipients_share_third_party_funder(self):
        sale = sale_tx()
        root_funding = sol_transfer_tx("fund-root", source=FUNDER, destination=ROOT, block_time=1_800_000_300)
        b_funding = sol_transfer_tx("fund-b", source=FUNDER, destination=WALLET_B, block_time=1_800_000_400)
        report, _provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, root_funding], FUNDER: [root_funding, b_funding], WALLET_B: [b_funding]},
            balances={ROOT: 0, FUNDER: 0, WALLET_B: 0},
        )
        funding_edges = [
            edge
            for edge in report["wallet_graph"]["edges"]
            if edge["relationship_type"] == "PLAIN_DIRECT_SOL_TRANSFER"
        ]
        self.assertEqual(len(funding_edges), 2)
        self.assertTrue(
            all(edge["classification"] == "DIRECT_SIGNED_SOL_FUNDING" for edge in funding_edges)
        )
        self.assertTrue(
            all(edge["relationship_context"] == "MULTI_RECIPIENT_FUNDER" for edge in funding_edges)
        )
        self.assertTrue(all(edge["relationship_type"] == "PLAIN_DIRECT_SOL_TRANSFER" for edge in funding_edges))
        self.assertFalse(
            any(edge["classification"] == "SHARED_FUNDER" for edge in report["wallet_graph"]["edges"])
        )
        shared = report["wallet_graph"]["shared_funders"][0]
        self.assertEqual(shared["funder"], FUNDER)
        self.assertEqual(shared["classification"], "SHARED_FUNDER")
        self.assertEqual(shared["relationship_type"], "THIRD_PARTY_SHARED_FUNDER")
        self.assertEqual(shared["relationship_scope"], "RECIPIENT_TO_RECIPIENT_VIA_THIRD_PARTY")
        self.assertEqual(set(shared["recipients"]), {ROOT, WALLET_B})
        self.assertEqual(shared["common_control"], "NOT_PROVEN")
        self.assertEqual(len(shared["recipient_pairs"]), 1)
        pair = shared["recipient_pairs"][0]
        self.assertEqual({pair["wallet_a"], pair["wallet_b"]}, {ROOT, WALLET_B})
        self.assertEqual(pair["third_party_funder"], FUNDER)
        self.assertNotIn(FUNDER, {pair["wallet_a"], pair["wallet_b"]})
        self.assertEqual(pair["classification"], "SHARED_FUNDER")
        self.assertEqual(pair["common_control"], "NOT_PROVEN")
        self.assertEqual(
            {row["signature"] for row in shared["supporting_transfers"]},
            {"fund-root", "fund-b"},
        )
        self.assertTrue(
            all(row["classification"] == "DIRECT_SIGNED_SOL_FUNDING" for row in shared["supporting_transfers"])
        )
        self.assertTrue(
            all(row["common_control"] == "NOT_PROVEN" for row in shared["supporting_transfers"])
        )
        self.assertEqual(report["common_control"], "NOT_PROVEN")
        output = io.StringIO()
        with redirect_stdout(output):
            reporting.print_cluster_audit(report)
        console = output.getvalue()
        self.assertIn("DIRECT SOL FUNDING:", console)
        self.assertIn(f"{FUNDER} -> {ROOT}", console)
        self.assertIn("source signed: true", console)
        self.assertIn("source paid fee: true", console)
        self.assertIn("market context: false", console)
        self.assertIn("relationship context: MULTI_RECIPIENT_FUNDER", console)
        self.assertIn("COMMON_CONTROL: NOT_PROVEN", console)
        self.assertIn(
            f"SHARED_FUNDER wallet_a={pair['wallet_a']} wallet_b={pair['wallet_b']} "
            f"third_party_funder={FUNDER}",
            console,
        )
        self.assertNotIn(
            f"SHARED_FUNDER source={FUNDER} destination={ROOT}",
            console,
        )

    def test_repeated_direct_sol_funding_is_not_reclassified_as_shared_funder(self):
        sale = sale_tx()
        first = sol_transfer_tx("fund-one", source=FUNDER, destination=ROOT, block_time=1_800_000_300)
        second = sol_transfer_tx("fund-two", source=FUNDER, destination=ROOT, block_time=1_800_000_400)
        report, _provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, first, second], FUNDER: [first, second]},
            balances={ROOT: 0, FUNDER: 0},
        )
        funding_edges = [edge for edge in report["wallet_graph"]["edges"] if edge["asset"] == "SOL"]
        self.assertEqual(len(funding_edges), 2)
        self.assertTrue(
            all(edge["classification"] == "DIRECT_SIGNED_SOL_FUNDING" for edge in funding_edges)
        )
        self.assertTrue(
            all(edge["relationship_type"] == "PLAIN_DIRECT_SOL_TRANSFER" for edge in funding_edges)
        )
        self.assertTrue(
            all(edge["relationship_context"] == "DIRECT_SIGNED_SOL_FUNDING" for edge in funding_edges)
        )
        self.assertEqual(report["wallet_graph"]["shared_funders"], [])

    def test_router_and_pool_are_excluded_from_nodes(self):
        sale = sale_tx()
        pool_transfer = token_transfer_tx(
            "pool-direct", destination_owner=POOL, block_time=1_800_000_300, outer_program=None
        )
        records = {ROOT: account_record(), POOL: account_record(owner=UNKNOWN_PROGRAM)}
        report, _provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, pool_transfer]},
            balances={ROOT: 0},
            records=records,
        )
        self.assertEqual([node["wallet"] for node in report["wallet_graph"]["nodes"]], [ROOT])
        self.assertGreaterEqual(len(report["excluded_infrastructure"]), 2)

    def test_graph_depth_limit_stops_expansion(self):
        sale = sale_tx()
        ab = token_transfer_tx("ab", destination_owner=WALLET_B, block_time=1_800_000_300)
        bc = token_transfer_tx(
            "bc", source_owner=WALLET_B, destination_owner=WALLET_C, block_time=1_800_000_400
        )
        report, provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, ab], WALLET_B: [ab, bc], WALLET_C: [bc]},
            balances={ROOT: 0, WALLET_B: 0, WALLET_C: 0},
            depth=1,
        )
        self.assertEqual({row["wallet"] for row in report["wallet_graph"]["nodes"]}, {ROOT, WALLET_B})
        self.assertNotIn(WALLET_C, provider.activity_calls)
        self.assertTrue(any(row["kind"] == "GRAPH_DEPTH_EXHAUSTED" for row in report["unresolved_relationships"]))

    def test_max_wallets_limit_stops_expansion(self):
        sale = sale_tx()
        ab = token_transfer_tx("limit-ab", destination_owner=WALLET_B, block_time=1_800_000_300)
        ac = token_transfer_tx("limit-ac", destination_owner=WALLET_C, block_time=1_800_000_301)
        report, _provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, ab, ac], WALLET_B: [ab], WALLET_C: [ac]},
            balances={ROOT: 0, WALLET_B: 0, WALLET_C: 0},
            max_wallets=2,
        )
        self.assertEqual(len(report["wallet_graph"]["nodes"]), 2)
        self.assertTrue(any(row["kind"] == "MAX_WALLETS_EXHAUSTED" for row in report["unresolved_relationships"]))

    def test_incomplete_provider_coverage_fails_closed(self):
        report, _provider, _rpc = run_audit(complete=False)
        self.assertEqual(report["wallet_status"], "INSUFFICIENT_DATA")
        self.assertEqual(report["cluster_status"], "INSUFFICIENT_DATA")
        self.assertFalse(report["provider_coverage"]["complete"])

    def test_cluster_inventory_does_not_double_count_wallet_with_multiple_edges(self):
        sale = sale_tx()
        first = token_transfer_tx("repeat-one", amount=10, block_time=1_800_000_300)
        second = token_transfer_tx("repeat-two", amount=20, block_time=1_800_000_301)
        report, _provider, rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, first, second], WALLET_B: [first, second]},
            balances={ROOT: 0, WALLET_B: 500},
        )
        self.assertEqual(report["visible_cluster_target_inventory_raw"], 500)
        self.assertEqual(sum(1 for row in report["wallet_graph"]["nodes"] if row["wallet"] == WALLET_B), 1)
        self.assertEqual(rpc.balance_calls.count(WALLET_B), 1)

    def test_confirmed_buy_is_separate_from_transfer_and_reacquisition(self):
        sale = sale_tx(amount=1_000, block_time=1_800_000_100)
        buy = buy_tx(amount=100, block_time=1_800_000_200)
        report, _provider, _rpc = run_audit(
            target_transactions=[sale, buy],
            activity={ROOT: [sale, buy]},
            balances={ROOT: 100},
        )
        counts = report["seed_wallet_accounting"]["inventory_increases"]["counts"]
        self.assertEqual(counts["CONFIRMED_BUY"], 1)
        self.assertEqual(counts["DIRECT_TRANSFER_IN"], 0)

    def test_cluster_preserves_verified_historical_exit_and_current_reentry(self):
        sale_amount = 7_521_892_705_710
        first = 1_926_809_500_766
        second = 1_872_015_512_595
        transactions = [
            sale_tx("lifecycle-exit", amount=sale_amount, block_time=1_800_000_100),
            buy_tx("lifecycle-reentry-one", amount=first, block_time=1_800_000_200),
            buy_tx("lifecycle-reentry-two", amount=second, block_time=1_800_000_300),
        ]
        report, _provider, _rpc = run_audit(
            target_transactions=transactions,
            activity={ROOT: transactions},
            balances={ROOT: first + second},
        )
        self.assertEqual(report["historical_exit_status"], "VERIFIED_OUT")
        self.assertEqual(report["current_wallet_status"], "RE_ENTERED")
        self.assertEqual(report["wallet_status"], "RE_ENTERED")
        self.assertEqual(report["cluster_status"], "NOT_OUT")

    def test_transfer_in_and_unproven_reacquisition_remain_separate(self):
        sale = sale_tx(amount=1_000, block_time=1_800_000_100)
        direct = token_transfer_tx(
            "direct-in",
            source_owner=WALLET_B,
            destination_owner=ROOT,
            mint=TARGET_MINT,
            amount=100,
            block_time=1_800_000_200,
        )
        unknown = token_transfer_tx(
            "unknown-increase",
            source_owner=WALLET_C,
            destination_owner=ROOT,
            mint=TARGET_MINT,
            amount=50,
            block_time=1_800_000_300,
        )
        unknown["meta"]["innerInstructions"] = []
        report, _provider, _rpc = run_audit(
            target_transactions=[sale, direct, unknown],
            activity={ROOT: [sale, direct, unknown], WALLET_B: [direct]},
            balances={ROOT: 150, WALLET_B: 0, WALLET_C: 0},
        )
        counts = report["seed_wallet_accounting"]["inventory_increases"]["counts"]
        self.assertEqual(counts["DIRECT_TRANSFER_IN"], 1)
        self.assertEqual(counts["REACQUISITION"], 1)

    def test_materiality_is_configurable_relative_not_a_whale_token_count(self):
        sale = sale_tx()
        cross = token_transfer_tx("tiny-related", amount=1, block_time=1_800_000_300)
        common = {
            "target_transactions": [sale],
            "activity": {ROOT: [sale, cross], WALLET_B: [cross]},
            "balances": {ROOT: 0, WALLET_B: 1},
        }
        relative, _provider, _rpc = run_audit(**common, materiality_pct=Decimal("0.01"))
        strict, _provider, _rpc = run_audit(**common, materiality_pct=Decimal("0"))
        self.assertEqual(relative["configuration"]["material_threshold_raw"], 1)
        self.assertEqual(relative["cluster_status"], "VERIFIED_OUT")
        self.assertEqual(strict["cluster_status"], "NOT_OUT")

    def test_sale_proceeds_keep_gross_net_and_fee_separate(self):
        sale = sale_tx(proceeds_lamports=200_000_000, fee_lamports=5_000)
        report, _provider, _rpc = run_audit(target_transactions=[sale], activity={ROOT: [sale]})
        proceeds = report["seed_wallet_accounting"]["proceeds"]
        self.assertEqual(proceeds["gross_native_received_lamports"], 200_000_000)
        self.assertEqual(proceeds["net_native_delta_lamports"], 199_995_000)
        self.assertEqual(proceeds["tx_fees_lamports"], 5_000)
        self.assertEqual(proceeds["net_after_fee_lamports"], 199_995_000)

    def test_receipts_contain_evidence_and_generic_target_fields(self):
        sale = sale_tx()
        cross = token_transfer_tx("receipt-edge", block_time=1_800_000_300)
        report, _provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, cross], WALLET_B: [cross]},
            balances={ROOT: 0, WALLET_B: 50},
        )
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "cluster-audit.json"
            events_path = Path(temporary) / "cluster-audit-events.jsonl"
            reporting.write_cluster_receipts(report_path, events_path, report)
            report_text = report_path.read_text(encoding="utf-8")
            events_text = events_path.read_text(encoding="utf-8")
            saved_report = json.loads(report_text)
            schema = json.loads(
                (Path(__file__).parents[1] / "schemas" / "jeet-analyzer-result-v1.schema.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertIn("visible_cluster_target_inventory_raw", report_text)
        self.assertIn("relationship_edge", events_text)
        self.assertNotIn(f"{METADATA.symbol.lower()}_", report_text.lower())
        self.assertTrue(set(schema["required"]).issubset(saved_report))
        self.assertEqual(saved_report["common_control"], "NOT_PROVEN")
        self.assertEqual(saved_report["progress"]["phases"]["RECEIPT_FINALIZATION"], "complete")

    def test_cluster_cli_redacts_provider_url_and_key(self):
        valid_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6LBG83ByQ1foo123"
        valid_wallet = "11111111111111111111111111111111"
        output = io.StringIO()
        errors = io.StringIO()
        with patch.object(
            analyzer,
            "_helius_provider",
            side_effect=history.ProviderError("failed https://provider.invalid/?api-key=super-secret"),
        ), redirect_stdout(output), redirect_stderr(errors):
            code = analyzer.main(
                ["cluster-audit", "--mint", valid_mint, "--wallet", valid_wallet, "--days", "1"]
            )
        combined = output.getvalue() + errors.getvalue()
        self.assertEqual(code, 2)
        self.assertNotIn("super-secret", combined)
        self.assertNotIn("provider.invalid", combined)
        self.assertIn("CLUSTER_STATUS: INSUFFICIENT_DATA", combined)

    def test_production_has_no_signing_or_submission_calls(self):
        forbidden = {
            "sign",
            "sign_message",
            "sign_transaction",
            "send_transaction",
            "send_raw_transaction",
            "request_airdrop",
        }
        for module in (analyzer, cluster, graph, history, reporting, tx_classifier):
            tree = ast.parse(inspect.getsource(module))
            calls = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            self.assertTrue(forbidden.isdisjoint(calls), module.__name__)

    def test_scan_exposes_generic_automatic_seller_fields(self):
        events = history.normalize_transactions([sale_tx()], TARGET_MINT)
        summary = history.analyze_sellers(
            events,
            METADATA,
            {ROOT: 0},
            coverage_complete=True,
            config=history.AnalysisConfig(),
        )
        seller = summary["sellers"][0]
        self.assertEqual(seller["wallet_status"], "WALLET_VERIFIED_OUT")
        self.assertEqual(seller["target_token_sold_raw"], 1_000)
        self.assertEqual(seller["percentage_of_observed_sell_volume"], "100")

    def test_cluster_wallet_activity_paginates_without_token_only_filter(self):
        class Response:
            status_code = 200
            headers = {}

            def __init__(self, body):
                self.raw = io.BytesIO(json.dumps(body).encode("utf-8"))
                self.closed = False

            def json(self):
                raise AssertionError("streamed history must not call response.json()")

            def close(self):
                self.closed = True
                self.raw.close()

        class Session:
            def __init__(self):
                self.payloads = []

            def post(self, _url, *, json, timeout, stream=False):
                del timeout
                self.assert_stream = stream
                self.payloads.append(json)
                if len(self.payloads) == 1:
                    return Response(
                        {
                            "jsonrpc": "2.0",
                            "id": json["id"],
                            "result": {"data": [{"signature": "one", "blockTime": START + 1}], "paginationToken": "next"},
                        }
                    )
                return Response(
                    {"jsonrpc": "2.0", "id": json["id"], "result": {"data": [], "paginationToken": None}}
                )

        session = Session()
        provider = history.HeliusHistoricalProvider(
            "metered-key",
            rpc=object(),
            session=session,
            max_pages=3,
            sleeper=lambda _delay: None,
        )
        batch = provider.get_wallet_activity(ROOT, START, END)
        self.assertTrue(batch.complete)
        self.assertEqual(batch.pages, 2)
        filters = session.payloads[0]["params"][1]["filters"]
        self.assertNotIn("tokenAccounts", filters)


class SeedCentricClusterTraceTests(unittest.TestCase):
    def test_default_mode_keeps_seed_direct_link_and_balance_without_downstream_cross_asset_crawl(self):
        sale = sale_tx()
        direct = token_transfer_tx(
            "seed-cross-asset-link",
            mint=OTHER_MINT,
            source_owner=ROOT,
            destination_owner=WALLET_B,
            amount=777,
            block_time=1_800_000_300,
        )
        downstream_sol = sol_transfer_tx(
            "downstream-sol-only",
            source=WALLET_B,
            destination=WALLET_C,
            block_time=1_800_000_400,
        )
        report, provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={
                ROOT: [sale, direct],
                WALLET_B: [direct, downstream_sol],
                WALLET_C: [downstream_sol],
            },
            balances={ROOT: 0, WALLET_B: 5_000, WALLET_C: 9_000},
            deep_forensic=False,
        )
        self.assertEqual(report["configuration"]["mode"], "SEED_CENTRIC_CLUSTER_TRACE")
        self.assertIn(ROOT, provider.activity_calls)
        self.assertNotIn(WALLET_B, provider.activity_calls)
        self.assertNotIn(WALLET_C, provider.activity_calls)
        self.assertIn(WALLET_B, {row["wallet"] for row in report["wallet_graph"]["nodes"]})
        self.assertNotIn(WALLET_C, {row["wallet"] for row in report["wallet_graph"]["nodes"]})
        self.assertEqual(report["visible_related_target_inventory_raw"], 5_000)
        self.assertEqual(report["cluster_status"], "NOT_OUT")
        self.assertEqual(report["common_control"], "NOT_PROVEN")

    def test_default_mode_recurses_target_token_after_seed_direct_link(self):
        sale = sale_tx()
        funding = sol_transfer_tx(
            "seed-funds-b",
            source=ROOT,
            destination=WALLET_B,
            block_time=1_800_000_300,
        )
        bc = token_transfer_tx(
            "b-to-c-target",
            source_owner=WALLET_B,
            destination_owner=WALLET_C,
            mint=TARGET_MINT,
            amount=700,
            block_time=1_800_000_400,
            token_program=TOKEN_PROGRAM,
        )
        report, provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, funding], WALLET_B: [bc], WALLET_C: []},
            balances={ROOT: 0, WALLET_B: 0, WALLET_C: 700},
            records={ROOT: account_record(), WALLET_B: account_record(), WALLET_C: account_record()},
            deep_forensic=False,
        )
        nodes = {row["wallet"]: row for row in report["wallet_graph"]["nodes"]}
        self.assertIn(WALLET_B, nodes)
        self.assertIn(WALLET_C, nodes)
        self.assertEqual(nodes[WALLET_C]["depth"], 2)
        self.assertNotIn(WALLET_B, provider.activity_calls)
        self.assertNotIn(WALLET_C, provider.activity_calls)
        self.assertEqual(report["visible_related_target_inventory_raw"], 700)
        self.assertTrue(
            any(
                edge["asset"] == TARGET_MINT
                and edge["source"] == WALLET_B
                and edge["destination"] == WALLET_C
                for edge in report["wallet_graph"]["edges"]
            )
        )
        self.assertEqual(report["common_control"], "NOT_PROVEN")

    def test_deep_forensic_mode_remains_explicitly_available(self):
        sale = sale_tx()
        ab = token_transfer_tx("deep-ab", mint=OTHER_MINT, block_time=1_800_000_300)
        bc = token_transfer_tx(
            "deep-bc",
            source_owner=WALLET_B,
            destination_owner=WALLET_C,
            mint=OTHER_MINT,
            block_time=1_800_000_400,
            create_ata=True,
        )
        report, provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, ab], WALLET_B: [ab, bc], WALLET_C: [bc]},
            balances={ROOT: 0, WALLET_B: 0, WALLET_C: 0},
            deep_forensic=True,
        )
        self.assertEqual(report["configuration"]["mode"], "DEEP_FORENSIC")
        self.assertIn(WALLET_B, provider.activity_calls)
        self.assertIn(WALLET_C, provider.activity_calls)


class TargetRelationshipStatusSplitTests(unittest.TestCase):
    def test_target_status_blocker_distinguishes_known_non_target_assets(self):
        self.assertFalse(
            cluster._unresolved_relationship_blocks_target_status({"asset": "SOL"}, TARGET_MINT)
        )
        self.assertFalse(
            cluster._unresolved_relationship_blocks_target_status({"asset": OTHER_MINT}, TARGET_MINT)
        )
        self.assertTrue(
            cluster._unresolved_relationship_blocks_target_status({"asset": TARGET_MINT}, TARGET_MINT)
        )
        self.assertTrue(
            cluster._unresolved_relationship_blocks_target_status({"asset": "UNKNOWN_TOKEN"}, TARGET_MINT)
        )
        self.assertTrue(cluster._unresolved_relationship_blocks_target_status({}, TARGET_MINT))

    def test_unresolved_sol_link_is_separate_from_target_inventory_truth(self):
        sale = sale_tx()
        ambiguous_sol = sol_transfer_tx(
            "ambiguous-inbound-sol",
            source=FUNDER,
            destination=ROOT,
            lamports=152_742_126,
            block_time=1_800_000_300,
        )
        keys = ambiguous_sol["transaction"]["message"]["accountKeys"]
        keys[0]["signer"] = False
        keys[1]["signer"] = True
        keys[0], keys[1] = keys[1], keys[0]
        for field in ("preBalances", "postBalances"):
            values = ambiguous_sol["meta"][field]
            values[0], values[1] = values[1], values[0]

        report, _provider, _rpc = run_audit(
            target_transactions=[sale],
            activity={ROOT: [sale, ambiguous_sol]},
            balances={ROOT: 0, FUNDER: 0},
            records={ROOT: account_record(), FUNDER: account_record()},
            deep_forensic=False,
        )

        unresolved = report["unresolved_relationships"]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["asset"], "SOL")
        self.assertEqual(
            unresolved[0]["reason"],
            "native transfer source was not both signer and fee payer",
        )
        self.assertTrue(report["provider_coverage"]["complete"] )
        self.assertEqual(report["visible_cluster_target_inventory_raw"], 0)
        self.assertEqual(report["unresolved_non_wallet_target_inventory_raw"], 0)
        self.assertEqual(report["target_cluster_status"], "VERIFIED_OUT")
        self.assertEqual(report["relationship_status"], "UNRESOLVED")
        self.assertEqual(report["cluster_status"], "UNRESOLVED")
        self.assertEqual(report["common_control"], "NOT_PROVEN")

        output = io.StringIO()
        with redirect_stdout(output):
            reporting.print_cluster_audit(report)
        console = output.getvalue()
        self.assertIn("TARGET_CLUSTER_STATUS: VERIFIED_OUT", console)
        self.assertIn("RELATIONSHIP_STATUS: UNRESOLVED", console)
        self.assertIn("CLUSTER_STATUS: UNRESOLVED", console)


if __name__ == "__main__":
    unittest.main()
