from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

import requests
from unittest.mock import patch

from jeet_analyzer import analyzer, cluster, history, reporting
from jeet_analyzer.investigation import (
    InvestigationContext,
    InvestigationLimits,
    ProviderBudgetExhausted,
)
from tests.fixtures.builders import (
    ROOT,
    OTHER_MINT,
    TARGET_MINT,
    WALLET_B,
    FakeProvider,
    FakeRpc,
    account_record,
    buy_tx,
    sale_tx,
    token_transfer_tx,
)


START = 1_799_900_000
END = 1_800_100_000
METADATA = history.TokenMetadata(TARGET_MINT, "TGT", "Synthetic", 6, 1_000_000)


class Response:
    status_code = 200
    headers = {}

    def __init__(self, body):
        self.body = body
        self.raw = io.BytesIO(json.dumps(body).encode("utf-8"))
        self.closed = False

    def json(self):
        return self.body

    def close(self):
        self.closed = True
        self.raw.close()


class SequenceSession:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = 0
        self.payloads = []

    def post(self, _url, *, json, timeout, stream=False):
        del timeout, stream
        self.payloads.append(json)
        self.calls += 1
        row = self.rows.pop(0)
        if isinstance(row, Exception):
            raise row
        return row


def rpc_client(context, rows, *, retries=0):
    session = SequenceSession(rows)
    return (
        analyzer.ReadOnlyRpcClient(
            "https://provider.invalid/?api-key=redacted",
            session=session,
            max_retries=retries,
            sleeper=lambda _delay: None,
            investigation=context,
        ),
        session,
    )


def owner_balance(amount):
    return Response(
        {
            "result": {
                "context": {"slot": 10},
                "value": [
                    {
                        "pubkey": "TokenAccount11111111111111111111111111111",
                        "account": {
                            "data": {
                                "parsed": {
                                    "info": {
                                        "mint": TARGET_MINT,
                                        "tokenAmount": {"amount": str(amount)},
                                    }
                                }
                            }
                        },
                    }
                ],
            }
        }
    )


class RequestDeduplicationTests(unittest.TestCase):
    def test_cli_exposes_conservative_budget_defaults(self):
        args = analyzer.build_parser().parse_args(
            [
                "cluster-audit",
                "--mint", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6LBG83ByQ1foo123",
                "--wallet", "11111111111111111111111111111111",
            ]
        )
        self.assertEqual(args.max_rpc_requests, 500)
        self.assertEqual(args.max_signatures, 10_000)
        self.assertEqual(args.max_transactions, 5_000)
        self.assertEqual(args.max_estimated_provider_credits, 60_000)

    def test_cli_accepts_operator_credit_ceiling_above_default(self):
        with patch.object(analyzer, "rpc_url_from_environment", return_value="https://rpc.example.invalid"), patch.object(
            analyzer, "ReadOnlyRpcClient"
        ), patch.object(analyzer, "_run_analyze_file", return_value=0):
            result = analyzer.main(
                [
                    "--max-estimated-provider-credits",
                    "60001",
                    "analyze-file",
                    "--mint",
                    "11111111111111111111111111111111",
                    "--input",
                    "ignored.jsonl",
                ]
            )
        self.assertEqual(result, 0)

    def test_early_completion_records_actual_estimate_not_credit_ceiling(self):
        context = InvestigationContext()
        context.before_request("rpc", "getTransactionsForAddress")
        context.before_request("das", "getAsset")
        telemetry = context.to_record()
        self.assertEqual(telemetry["estimated_provider_credits"], 110)
        self.assertEqual(
            telemetry["estimated_provider_credits_by_method"],
            {"das.getAsset": 10, "rpc.getTransactionsForAddress": 100},
        )
        self.assertEqual(telemetry["estimated_provider_credit_ceiling"], 60_000)
        self.assertFalse(telemetry["estimated_provider_credit_cost_is_authoritative"])
        self.assertIsNone(telemetry["terminated_by_budget"])

    def test_identical_transaction_requested_twice_has_one_provider_fetch(self):
        context = InvestigationContext()
        transaction = sale_tx("immutable-signature")
        client, session = rpc_client(context, [Response({"result": transaction})])
        first = client.get_transaction("immutable-signature")
        second = client.get_transaction("immutable-signature")
        self.assertEqual(first, second)
        self.assertEqual(session.calls, 1)
        telemetry = context.to_record()
        self.assertEqual(telemetry["rpc_requests_by_method"], {"getTransaction": 1})
        self.assertEqual(telemetry["transactions_fetched"], 1)
        self.assertEqual(telemetry["cache_hits"], 1)
        self.assertEqual(telemetry["cache_hits_by_namespace"], {"transaction": 1})
        self.assertEqual(telemetry["provider_requests_avoided"], 1)
        self.assertEqual(telemetry["duplicate_evidence_observations"], 0)

    def test_duplicate_evidence_does_not_claim_an_avoided_provider_request(self):
        context = InvestigationContext()
        transaction = sale_tx("duplicate-observation")
        self.assertTrue(context.observe_transaction("duplicate-observation", transaction))
        self.assertFalse(context.observe_transaction("duplicate-observation", transaction))
        telemetry = context.to_record()
        self.assertEqual(telemetry["duplicate_evidence_observations"], 1)
        self.assertEqual(telemetry["provider_requests_avoided"], 0)
        self.assertEqual(telemetry["cache_hits"], 0)

    def test_account_result_cache_reuse_suppresses_duplicate_rpc(self):
        context = InvestigationContext()
        record = {"owner": "11111111111111111111111111111111", "executable": False}
        client, session = rpc_client(context, [Response({"result": {"value": record}})])
        self.assertEqual(client.get_account_info(ROOT), record)
        self.assertEqual(client.get_account_info(ROOT), record)
        self.assertEqual(session.calls, 1)
        telemetry = context.to_record()
        self.assertEqual(telemetry["cache_hits"], 1)
        self.assertEqual(telemetry["cache_hits_by_namespace"], {"account_record": 1})
        self.assertEqual(telemetry["provider_requests_avoided"], 1)

    def test_single_account_result_is_reused_by_later_batch_classification(self):
        context = InvestigationContext()
        record = {"owner": "11111111111111111111111111111111", "executable": False}
        client, session = rpc_client(context, [Response({"result": {"value": record}})])
        self.assertEqual(client.get_account_info(ROOT), record)
        self.assertEqual(client.get_multiple_accounts([ROOT]), {ROOT: record})
        self.assertEqual(session.calls, 1)
        telemetry = context.to_record()
        self.assertEqual(telemetry["provider_requests_avoided"], 1)
        self.assertEqual(telemetry["cache_hits_by_namespace"], {"account_record": 1})

    def test_cache_hit_inside_still_required_batch_does_not_claim_request_avoidance(self):
        context = InvestigationContext()
        root_record = {"owner": "11111111111111111111111111111111", "executable": False}
        wallet_b_record = {"owner": "11111111111111111111111111111111", "executable": False}
        client, session = rpc_client(
            context,
            [
                Response({"result": {"value": root_record}}),
                Response({"result": {"value": [wallet_b_record]}}),
            ],
        )
        self.assertEqual(client.get_account_info(ROOT), root_record)
        self.assertEqual(
            client.get_multiple_accounts([ROOT, WALLET_B]),
            {ROOT: root_record, WALLET_B: wallet_b_record},
        )
        self.assertEqual(session.calls, 2)
        telemetry = context.to_record()
        self.assertEqual(telemetry["cache_hits_by_namespace"], {"account_record": 1})
        self.assertEqual(telemetry["provider_requests_avoided"], 0)

    def test_mutable_owner_balance_is_always_fresh(self):
        context = InvestigationContext()
        client, session = rpc_client(context, [owner_balance(100), owner_balance(250)])
        self.assertEqual(client.get_owner_token_accounts(ROOT, TARGET_MINT).total, 100)
        self.assertEqual(client.get_owner_token_accounts(ROOT, TARGET_MINT).total, 250)
        self.assertEqual(session.calls, 2)
        self.assertEqual(
            context.to_record()["rpc_requests_by_method"],
            {"getTokenAccountsByOwner": 2},
        )

    def test_identical_wallet_history_page_is_fetched_once(self):
        context = InvestigationContext()
        transaction = sale_tx("history-once")
        session = SequenceSession(
            [Response({"result": {"data": [transaction], "paginationToken": None}})]
        )
        provider = history.HeliusHistoricalProvider(
            "metered-key",
            rpc=object(),
            session=session,
            max_retries=0,
            investigation=context,
        )
        first = provider.get_wallet_activity(ROOT, START, END)
        second = provider.get_wallet_activity(ROOT, START, END)
        self.assertTrue(first.complete)
        self.assertTrue(second.complete)
        self.assertEqual(session.calls, 1)
        telemetry = context.to_record()
        self.assertEqual(telemetry["pages_fetched"], 1)
        self.assertEqual(telemetry["rpc_requests_by_method"], {"getTransactionsForAddress": 1})
        self.assertEqual(telemetry["cache_hits_by_namespace"], {"history_page": 1})
        self.assertEqual(telemetry["provider_requests_avoided"], 1)
        self.assertEqual(telemetry["duplicate_evidence_observations"], 1)
        self.assertTrue(telemetry["deduplicated_skipped_requests_deprecated"])

    def test_das_metadata_cache_is_counted_separately(self):
        context = InvestigationContext()
        session = SequenceSession([Response({"result": {"id": TARGET_MINT}})])
        provider = history.HeliusHistoricalProvider(
            "metered-key", rpc=object(), session=session, max_retries=0, investigation=context
        )
        self.assertEqual(provider.get_metadata(TARGET_MINT), {"id": TARGET_MINT})
        self.assertEqual(provider.get_metadata(TARGET_MINT), {"id": TARGET_MINT})
        telemetry = context.to_record()
        self.assertEqual(session.calls, 1)
        self.assertEqual(telemetry["das_requests_by_method"], {"getAsset": 1})
        self.assertEqual(telemetry["cache_hits_by_namespace"], {"mint_metadata": 1})
        self.assertEqual(telemetry["provider_requests_avoided"], 1)

    def test_equal_or_shallower_remaining_depth_is_suppressed(self):
        context = InvestigationContext()
        self.assertTrue(context.should_traverse_wallet(ROOT, 3))
        self.assertFalse(context.should_traverse_wallet(ROOT, 3))
        self.assertFalse(context.should_traverse_wallet(ROOT, 2))
        self.assertTrue(context.should_traverse_wallet(ROOT, 4))
        telemetry = context.to_record()
        self.assertEqual(telemetry["wallets_traversed"], 1)
        self.assertEqual(telemetry["wallet_traversals_suppressed"], 2)
        self.assertEqual(telemetry["provider_requests_avoided"], 0)
        self.assertEqual(telemetry["duplicate_evidence_observations"], 0)


class HardBudgetTests(unittest.TestCase):
    def test_configured_credit_ceiling_cannot_be_exceeded(self):
        context = InvestigationContext(
            InvestigationLimits(
                max_rpc_requests=2_000,
                max_signatures=40_000,
                max_transactions=20_000,
                max_estimated_provider_credits=60_000,
            )
        )
        for _ in range(600):
            context.before_request("rpc", "getTransactionsForAddress")
        with self.assertRaises(ProviderBudgetExhausted):
            context.before_request("rpc", "getTransactionsForAddress")
        telemetry = context.to_record()
        self.assertEqual(telemetry["estimated_provider_credits"], 60_000)
        self.assertEqual(telemetry["rpc_requests"], 600)
        self.assertEqual(
            telemetry["terminated_by_budget"], "max_estimated_provider_credits"
        )

    def test_credit_ceiling_can_be_configured_above_default(self):
        limits = InvestigationLimits(max_estimated_provider_credits=60_001)
        limits.validate()
        self.assertEqual(limits.max_estimated_provider_credits, 60_001)

    def test_beta_scale_transaction_budget_admits_more_than_old_smoke_boundary(self):
        context = InvestigationContext(
            InvestigationLimits(
                max_rpc_requests=1_500,
                max_signatures=40_000,
                max_transactions=20_000,
            )
        )
        for index in range(601):
            context.observe_transaction(
                f"expanded-{index}", sale_tx(f"expanded-{index}")
            )
        self.assertEqual(context.to_record()["transactions_fetched"], 601)
        self.assertIsNone(context.to_record()["terminated_by_budget"])

    def test_credit_budget_exhaustion_during_seed_history_fails_closed(self):
        context = InvestigationContext(
            InvestigationLimits(
                max_rpc_requests=10,
                max_signatures=10,
                max_transactions=10,
                max_estimated_provider_credits=100,
            )
        )
        session = SequenceSession(
            [
                Response(
                    {
                        "result": {
                            "data": [sale_tx("credit-limited-seed")],
                            "paginationToken": "another-page",
                        }
                    }
                )
            ]
        )
        provider = history.HeliusHistoricalProvider(
            "metered-key",
            rpc=object(),
            session=session,
            max_retries=0,
            max_pages=2,
            investigation=context,
        )
        report = cluster.run_cluster_audit(
            provider,
            FakeRpc({ROOT: 0}, {ROOT: account_record()}),
            METADATA,
            ROOT,
            START,
            END,
            config=cluster.ClusterAuditConfig(graph_depth=0, max_wallets=1),
            investigation=context,
        )
        self.assertEqual(session.calls, 1)
        self.assertFalse(report["provider_coverage"]["seed_wallet_complete"])
        self.assertEqual(report["historical_exit_status"], "INSUFFICIENT_DATA")
        self.assertNotEqual(report["current_wallet_status"], "VERIFIED_OUT")
        self.assertNotEqual(report["current_wallet_status"], "RE_ENTERED")
        self.assertEqual(report["cluster_status"], "INSUFFICIENT_DATA")
        self.assertEqual(
            report["request_telemetry"]["terminated_by_budget"],
            "max_estimated_provider_credits",
        )

    def test_credit_ceiling_applies_to_retries_before_network(self):
        context = InvestigationContext(
            InvestigationLimits(
                max_rpc_requests=10,
                max_signatures=10,
                max_transactions=10,
                max_estimated_provider_credits=1,
            )
        )
        client, session = rpc_client(
            context,
            [requests.exceptions.ReadTimeout("first attempt timed out")],
            retries=2,
        )
        with self.assertRaises(ProviderBudgetExhausted):
            client.get_owner_token_accounts(ROOT, TARGET_MINT)
        telemetry = context.to_record()
        self.assertEqual(session.calls, 1)
        self.assertEqual(telemetry["estimated_provider_credits"], 1)
        self.assertEqual(
            telemetry["terminated_by_budget"], "max_estimated_provider_credits"
        )

    def test_material_inventory_stops_optional_mint_enrichment_early(self):
        provider = FakeProvider(
            target_transactions=[sale_tx("optional-market-row")],
            activity={ROOT: []},
            balances={ROOT: 500},
        )
        report = cluster.run_cluster_audit(
            provider,
            FakeRpc({ROOT: 500}, {ROOT: account_record()}),
            METADATA,
            ROOT,
            START,
            END,
            config=cluster.ClusterAuditConfig(graph_depth=0, max_wallets=1),
            investigation=InvestigationContext(),
        )
        self.assertNotIn(("token_events", TARGET_MINT), provider.calls)
        self.assertEqual(report["cluster_status"], "NOT_OUT")
        optional = report["provider_coverage"]["optional_mint_enrichment"]
        self.assertFalse(optional["attempted"])
        self.assertFalse(optional["required_for_conclusion"])
        self.assertEqual(
            optional["skipped_reason"],
            "FRESH_MATERIAL_INVENTORY_ALREADY_PROVES_NOT_OUT",
        )
        self.assertTrue(report["provider_coverage"]["downstream_complete"])

    def test_seeded_cluster_requires_mint_wide_coverage_before_verified_out(self):
        seed_sales = [
            sale_tx("seed-sale-one", amount=1_200, block_time=START + 100),
            sale_tx("seed-sale-two", amount=800, block_time=START + 200),
        ]
        broad_history = [
            *seed_sales,
            *[
                sale_tx(f"broad-sale-{index}", amount=1, block_time=START + 300 + index)
                for index in range(700)
            ],
        ]
        context = InvestigationContext(InvestigationLimits(100, 1_000, 600))
        provider = FakeProvider(
            target_transactions=broad_history,
            activity={ROOT: seed_sales},
            balances={ROOT: 0},
        )
        report = cluster.run_cluster_audit(
            provider,
            FakeRpc({ROOT: 0}, {ROOT: account_record()}),
            METADATA,
            ROOT,
            START,
            END,
            config=cluster.ClusterAuditConfig(graph_depth=0, max_wallets=1),
            investigation=context,
        )

        self.assertEqual(provider.calls[:3], [
            ("wallet_events", ROOT),
            ("wallet_activity", ROOT),
            ("token_events", TARGET_MINT),
        ])
        self.assertEqual(report["current_wallet_status"], "VERIFIED_OUT")
        self.assertEqual(report["historical_exit_status"], "VERIFIED_OUT")
        self.assertEqual(report["cluster_status"], "INSUFFICIENT_DATA")
        sales = report["sales"]
        self.assertEqual(sales["count"], 2)
        self.assertEqual(sales["target_token_sold_raw"], 2_000)
        self.assertTrue(sales["amount_known"])
        self.assertTrue(report["provider_coverage"]["seed_wallet_complete"])
        self.assertFalse(report["provider_coverage"]["downstream_complete"])
        self.assertFalse(report["provider_coverage"]["complete"])
        self.assertEqual(report["request_telemetry"]["transactions_fetched"], 600)
        self.assertEqual(report["request_telemetry"]["terminated_by_budget"], "max_transactions")
        optional = report["provider_coverage"]["optional_mint_enrichment"]
        self.assertTrue(optional["attempted"])
        self.assertTrue(optional["required_for_conclusion"])
        self.assertFalse(optional["complete"])
        self.assertIsNone(optional["skipped_reason"])
        output = io.StringIO()
        with redirect_stdout(output):
            reporting.print_cluster_audit(report)
        self.assertIn("TARGET_TOKEN_SOLD: 0.002 (2 confirmed sales)", output.getvalue())
        self.assertNotIn("TARGET_TOKEN_SOLD: 0 (", output.getvalue())

    def test_budget_exhaustion_during_seed_history_keeps_sales_unknown_and_wallet_insufficient(self):
        transactions = [sale_tx("seed-budget-one"), sale_tx("seed-budget-two")]
        context = InvestigationContext(InvestigationLimits(50, 10, 1))
        provider = FakeProvider(
            target_transactions=transactions,
            activity={ROOT: transactions},
            balances={ROOT: 0},
        )
        report = cluster.run_cluster_audit(
            provider,
            FakeRpc({ROOT: 0}, {ROOT: account_record()}),
            METADATA,
            ROOT,
            START,
            END,
            config=cluster.ClusterAuditConfig(graph_depth=1, max_wallets=2),
            investigation=context,
        )

        self.assertEqual(provider.calls, [("wallet_events", ROOT)])
        self.assertEqual(report["current_wallet_status"], "INSUFFICIENT_DATA")
        self.assertEqual(report["cluster_status"], "INSUFFICIENT_DATA")
        self.assertFalse(report["sales"]["amount_known"])
        self.assertFalse(report["sales"]["coverage_complete"])
        output = io.StringIO()
        with redirect_stdout(output):
            reporting.print_cluster_audit(report)
        self.assertIn("TARGET_TOKEN_SOLD: UNKNOWN", output.getvalue())

    def test_complete_seed_lifecycle_survives_incomplete_related_wallet_history(self):
        sale = sale_tx("complete-seed-sale")
        cross_asset = token_transfer_tx(
            "cross-asset-link",
            mint=OTHER_MINT,
            amount=50,
            block_time=START + 300,
        )
        provider = FakeProvider(
            target_transactions=[sale],
            activity={ROOT: [sale, cross_asset], WALLET_B: [cross_asset]},
            balances={ROOT: 0, WALLET_B: 0},
            incomplete_wallets={WALLET_B},
        )
        report = cluster.run_cluster_audit(
            provider,
            FakeRpc({ROOT: 0, WALLET_B: 0}),
            METADATA,
            ROOT,
            START,
            END,
            config=cluster.ClusterAuditConfig(graph_depth=1, max_wallets=3),
            investigation=InvestigationContext(),
        )

        self.assertEqual(report["current_wallet_status"], "VERIFIED_OUT")
        self.assertEqual(report["cluster_status"], "INSUFFICIENT_DATA")
        self.assertTrue(report["provider_coverage"]["seed_wallet_complete"])
        self.assertFalse(report["provider_coverage"]["downstream_complete"])
        self.assertIn("seed wallet lifecycle was established", report["cluster_status_reasons"][0])

    def test_complete_seed_history_can_establish_zero_observed_sales(self):
        provider = FakeProvider(
            target_transactions=[],
            activity={ROOT: []},
            balances={ROOT: 500},
        )
        report = cluster.run_cluster_audit(
            provider,
            FakeRpc({ROOT: 500}, {ROOT: account_record()}),
            METADATA,
            ROOT,
            START,
            END,
            config=cluster.ClusterAuditConfig(graph_depth=0, max_wallets=1),
            investigation=InvestigationContext(),
        )

        self.assertEqual(report["current_wallet_status"], "NOT_OUT")
        self.assertEqual(report["sales"]["count"], 0)
        self.assertEqual(report["sales"]["target_token_sold_raw"], 0)
        self.assertTrue(report["sales"]["amount_known"])
        output = io.StringIO()
        with redirect_stdout(output):
            reporting.print_cluster_audit(report)
        self.assertIn("TARGET_TOKEN_SOLD: 0 (0 confirmed sales)", output.getvalue())

    def test_max_rpc_request_budget_stops_before_extra_fetch(self):
        context = InvestigationContext(InvestigationLimits(1, 10, 10))
        client, session = rpc_client(context, [owner_balance(10)])
        self.assertEqual(client.get_owner_token_accounts(ROOT, TARGET_MINT).total, 10)
        with self.assertRaises(ProviderBudgetExhausted):
            client.get_owner_token_accounts(ROOT, TARGET_MINT)
        self.assertEqual(session.calls, 1)
        self.assertEqual(context.to_record()["terminated_by_budget"], "max_rpc_requests")

    def test_retry_attempt_obeys_same_rpc_budget(self):
        context = InvestigationContext(InvestigationLimits(1, 10, 10))
        client, session = rpc_client(
            context,
            [requests.exceptions.ReadTimeout("first attempt timed out")],
            retries=2,
        )
        with self.assertRaises(ProviderBudgetExhausted):
            client.get_owner_token_accounts(ROOT, TARGET_MINT)
        self.assertEqual(session.calls, 1)
        self.assertEqual(context.to_record()["rpc_requests"], 1)
        self.assertEqual(context.to_record()["terminated_by_budget"], "max_rpc_requests")

    def test_retry_telemetry_matches_actual_attempts(self):
        context = InvestigationContext(InvestigationLimits(10, 10, 10))
        client, session = rpc_client(
            context,
            [requests.exceptions.ReadTimeout("first attempt timed out"), owner_balance(25)],
            retries=1,
        )
        self.assertEqual(client.get_owner_token_accounts(ROOT, TARGET_MINT).total, 25)
        telemetry = context.to_record()
        self.assertEqual(session.calls, 2)
        self.assertEqual(telemetry["rpc_requests_by_method"], {"getTokenAccountsByOwner": 2})
        self.assertEqual(telemetry["retry_attempts"], 1)

    def test_max_signature_budget_preserves_partial_page_and_fails_closed(self):
        context = InvestigationContext(InvestigationLimits(10, 1, 10))
        session = SequenceSession(
            [
                Response(
                    {
                        "result": {
                            "data": [sale_tx("sig-one"), sale_tx("sig-two")],
                            "paginationToken": None,
                        }
                    }
                )
            ]
        )
        provider = history.HeliusHistoricalProvider(
            "metered-key", rpc=object(), session=session, max_retries=0, investigation=context
        )
        batch = provider.get_wallet_activity(ROOT, START, END)
        self.assertFalse(batch.complete)
        self.assertEqual(batch.failure_category, "PROVIDER_BUDGET_EXHAUSTED")
        self.assertEqual(len(batch.transactions), 1)
        self.assertEqual(context.to_record()["terminated_by_budget"], "max_signatures")

    def test_max_transaction_budget_is_independent_and_typed(self):
        context = InvestigationContext(InvestigationLimits(10, 10, 1))
        context.observe_transaction("one", sale_tx("one"))
        with self.assertRaises(ProviderBudgetExhausted) as caught:
            context.observe_transaction("two", sale_tx("two"))
        self.assertEqual(caught.exception.category, "PROVIDER_BUDGET_EXHAUSTED")
        self.assertEqual(context.to_record()["terminated_by_budget"], "max_transactions")

    def test_budget_exhaustion_cannot_leave_verified_out(self):
        context = InvestigationContext(InvestigationLimits(50, 1, 10))
        transactions = [sale_tx("budget-sale"), buy_tx("budget-hidden-buy", amount=100)]
        provider = FakeProvider(
            target_transactions=transactions,
            activity={ROOT: transactions},
            balances={ROOT: 0},
        )
        report = cluster.run_cluster_audit(
            provider,
            FakeRpc({ROOT: 0}, {ROOT: account_record()}),
            METADATA,
            ROOT,
            START,
            END,
            config=cluster.ClusterAuditConfig(graph_depth=0, max_wallets=1),
            investigation=context,
        )
        self.assertFalse(report["provider_coverage"]["complete"])
        self.assertEqual(report["cluster_status"], "INSUFFICIENT_DATA")
        self.assertNotEqual(report["current_wallet_status"], "VERIFIED_OUT")
        self.assertNotEqual(report["current_wallet_status"], "RE_ENTERED")

    def test_fresh_material_balance_still_permits_not_out(self):
        context = InvestigationContext(InvestigationLimits(50, 1, 10))
        transactions = [sale_tx("visible-sale"), buy_tx("unseen-after-budget", amount=100)]
        provider = FakeProvider(
            target_transactions=transactions,
            activity={ROOT: transactions},
            balances={ROOT: 500},
        )
        report = cluster.run_cluster_audit(
            provider,
            FakeRpc({ROOT: 500}, {ROOT: account_record()}),
            METADATA,
            ROOT,
            START,
            END,
            config=cluster.ClusterAuditConfig(graph_depth=0, max_wallets=1),
            investigation=context,
        )
        self.assertEqual(report["current_wallet_status"], "NOT_OUT")
        self.assertNotEqual(report["current_wallet_status"], "RE_ENTERED")
        self.assertEqual(report["cluster_status"], "NOT_OUT")
        self.assertEqual(report["request_telemetry"]["terminated_by_budget"], "max_signatures")

    def test_graph_cycle_traverses_each_wallet_once(self):
        context = InvestigationContext()
        outbound = token_transfer_tx("root-to-b", destination_owner=WALLET_B)
        inbound = token_transfer_tx(
            "b-to-root", source_owner=WALLET_B, destination_owner=ROOT, block_time=1_800_000_100
        )
        provider = FakeProvider(
            target_transactions=[sale_tx()],
            activity={ROOT: [outbound, inbound], WALLET_B: [outbound, inbound]},
            balances={ROOT: 0, WALLET_B: 0},
        )
        cluster.run_cluster_audit(
            provider,
            FakeRpc({ROOT: 0, WALLET_B: 0}),
            METADATA,
            ROOT,
            START,
            END,
            config=cluster.ClusterAuditConfig(
                graph_depth=3, max_wallets=10, deep_forensic=True
            ),
            investigation=context,
        )
        self.assertEqual(provider.activity_calls.count(ROOT), 1)
        self.assertEqual(provider.activity_calls.count(WALLET_B), 1)
        self.assertEqual(context.to_record()["wallets_traversed"], 2)


if __name__ == "__main__":
    unittest.main()


class MintFilteredTransferHistoryTests(unittest.TestCase):
    def test_transfer_rpc_has_ten_credit_weight(self):
        context = InvestigationContext()
        context.before_request("rpc", "getTransfersByAddress")
        telemetry = context.to_record()
        self.assertEqual(telemetry["estimated_provider_credits"], 10)
        self.assertEqual(
            telemetry["estimated_provider_credits_by_method"],
            {"rpc.getTransfersByAddress": 10},
        )

    def test_helius_target_transfer_stream_filters_by_exact_mint(self):
        context = InvestigationContext()
        transfer = {
            "signature": "mint-filtered-transfer",
            "slot": 123,
            "blockTime": START + 100,
            "type": "transfer",
            "fromUserAccount": ROOT,
            "toUserAccount": WALLET_B,
            "fromTokenAccount": "SourceToken1111111111111111111111111111111",
            "toTokenAccount": "DestToken111111111111111111111111111111111",
            "mint": TARGET_MINT,
            "amount": "2500000",
            "decimals": 6,
            "confirmationStatus": "finalized",
            "transactionIdx": 1,
            "instructionIdx": 2,
            "innerInstructionIdx": 0,
        }
        session = SequenceSession(
            [Response({"result": {"data": [transfer], "paginationToken": None}})]
        )
        provider = history.HeliusHistoricalProvider(
            "metered-key",
            rpc=object(),
            session=session,
            max_retries=0,
            investigation=context,
        )
        events = []
        batch = provider.stream_wallet_target_transfers_bounded(
            ROOT,
            TARGET_MINT,
            START,
            END,
            events.append,
            max_pages=5,
        )
        self.assertTrue(batch.complete)
        self.assertEqual(batch.pages, 1)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_type, "TRANSFER")
        self.assertEqual(event.wallet, ROOT)
        self.assertEqual(event.destination, WALLET_B)
        self.assertEqual(event.token_delta_raw, -2_500_000)
        self.assertEqual(event.token_amount_raw, 2_500_000)
        self.assertTrue(history.is_concrete_transfer_event(event))
        payload = session.payloads[0]
        self.assertEqual(payload["method"], "getTransfersByAddress")
        options = payload["params"][1]
        self.assertEqual(options["mint"], TARGET_MINT)
        self.assertEqual(options["limit"], 100)
        self.assertEqual(options["sortOrder"], "asc")
        self.assertNotIn("solMode", options)
        self.assertNotIn("status", options["filters"])
        self.assertEqual(options["filters"], {"blockTime": {"gte": START, "lt": END}})
        telemetry = context.to_record()
        self.assertEqual(telemetry["rpc_requests_by_method"], {"getTransfersByAddress": 1})
        self.assertEqual(telemetry["estimated_provider_credits"], 10)
        self.assertEqual(telemetry["signatures_examined"], 1)
        self.assertEqual(telemetry["transactions_fetched"], 0)

    def test_transfer_pagination_repeats_fail_closed(self):
        context = InvestigationContext()
        first = {
            "signature": "page-one-transfer",
            "blockTime": START + 1,
            "type": "transfer",
            "fromUserAccount": ROOT,
            "toUserAccount": WALLET_B,
            "fromTokenAccount": "SourceToken1111111111111111111111111111111",
            "toTokenAccount": "DestToken111111111111111111111111111111111",
            "mint": TARGET_MINT,
            "amount": "1",
        }
        session = SequenceSession(
            [
                Response({"result": {"data": [first], "paginationToken": "same-token"}}),
                Response({"result": {"data": [], "paginationToken": "same-token"}}),
            ]
        )
        provider = history.HeliusHistoricalProvider(
            "metered-key",
            rpc=object(),
            session=session,
            max_retries=0,
            investigation=context,
        )
        events = []
        batch = provider.stream_wallet_target_transfers_bounded(
            ROOT, TARGET_MINT, START, END, events.append, max_pages=5
        )
        self.assertFalse(batch.complete)
        self.assertEqual(batch.failure_category, "COVERAGE_EXHAUSTION")
        self.assertIn("repeated a pagination token", batch.limitation)
        self.assertEqual(session.calls, 2)

