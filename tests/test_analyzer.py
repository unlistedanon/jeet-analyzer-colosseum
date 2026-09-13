from __future__ import annotations

import io
import ast
import inspect
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from jeet_analyzer import analyzer
from jeet_analyzer import history


MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6LBG83ByQ1foo123"
WALLET_A = "11111111111111111111111111111111"
WALLET_B = "Vote111111111111111111111111111111111111111"
WALLET_C = "Stake11111111111111111111111111111111111111"
WALLET_D = "SysvarRent111111111111111111111111111111111"
TARGET_A_1 = "TargetAcctA111111111111111111111111111111111"
TARGET_A_2 = "TargetAcctA222222222222222222222222222222222"
TARGET_POOL = "TargetPool1111111111111111111111111111111111"
TARGET_B = "TargetAcctB111111111111111111111111111111111"
QUOTE_A = "QuoteAcctA1111111111111111111111111111111111"
QUOTE_POOL = "QuotePool11111111111111111111111111111111111"
UNKNOWN_PROGRAM = "BPFLoaderUpgradeab1e11111111111111111111111"


def balance(index, mint, owner, amount, decimals=6):
    return {
        "accountIndex": index,
        "mint": mint,
        "owner": owner,
        "uiTokenAmount": {"amount": str(amount), "decimals": decimals},
    }


def swap_transaction(
    signature: str,
    *,
    mint: str = MINT,
    wallet: str = WALLET_A,
    target_pre: int = 1_000,
    target_post: int = 500,
    quote_mint: str | None = None,
    quote_pre: int = 0,
    quote_post: int = 0,
    quote_decimals: int = 9,
    programs: tuple[str, ...] = (UNKNOWN_PROGRAM,),
    block_time: int = 1_700_000_000,
    native_received: int = 0,
    failed: bool = False,
):
    keys = [
        {"pubkey": wallet, "signer": True},
        {"pubkey": TARGET_A_1, "signer": False},
        {"pubkey": TARGET_POOL, "signer": False},
    ]
    pre_token = [balance(1, mint, wallet, target_pre), balance(2, mint, WALLET_C, 100)]
    post_token = [balance(1, mint, wallet, target_post), balance(2, mint, WALLET_C, 100 + target_pre - target_post)]
    if quote_mint:
        keys.extend(
            [
                {"pubkey": QUOTE_A, "signer": False},
                {"pubkey": QUOTE_POOL, "signer": False},
            ]
        )
        pre_token.extend(
            [balance(3, quote_mint, wallet, quote_pre, quote_decimals), balance(4, quote_mint, WALLET_C, quote_post + 1_000, quote_decimals)]
        )
        post_token.extend(
            [balance(3, quote_mint, wallet, quote_post, quote_decimals), balance(4, quote_mint, WALLET_C, quote_pre + 1_000, quote_decimals)]
        )
    instructions = [{"programId": program, "parsed": {"type": "swap", "info": {}}} for program in programs]
    return {
        "blockTime": block_time,
        "transaction": {
            "signatures": [signature],
            "message": {"accountKeys": keys, "instructions": instructions},
        },
        "meta": {
            "err": {"InstructionError": [0, "failed"]} if failed else None,
            "fee": 5_000,
            "preBalances": [1_000_000_000, 0, 0] + ([0, 0] if quote_mint else []),
            "postBalances": [1_000_000_000 + native_received - 5_000, 0, 0] + ([0, 0] if quote_mint else []),
            "preTokenBalances": pre_token,
            "postTokenBalances": post_token,
            "innerInstructions": [],
            "logMessages": ["Program log: Instruction: Swap"],
        },
    }


def transfer_transaction(signature: str, source=WALLET_A, destination=WALLET_B, amount=500, block_time=1_700_000_000):
    source_start = max(1_000, amount)
    return {
        "blockTime": block_time,
        "transaction": {
            "signatures": [signature],
            "message": {
                "accountKeys": [
                    {"pubkey": source, "signer": True},
                    {"pubkey": TARGET_A_1, "signer": False},
                    {"pubkey": TARGET_B, "signer": False},
                ],
                "instructions": [
                    {
                        "programId": analyzer.TOKEN_PROGRAM,
                        "parsed": {
                            "type": "transferChecked",
                            "info": {
                                "source": TARGET_A_1,
                                "destination": TARGET_B,
                                "authority": source,
                                "mint": MINT,
                                "tokenAmount": {"amount": str(amount), "decimals": 6},
                            },
                        },
                    }
                ],
            },
        },
        "meta": {
            "err": None,
            "fee": 5_000,
            "preBalances": [1_000_000_000, 0, 0],
            "postBalances": [999_995_000, 0, 0],
            "preTokenBalances": [balance(1, MINT, source, source_start), balance(2, MINT, destination, 0)],
            "postTokenBalances": [
                balance(1, MINT, source, source_start - amount),
                balance(2, MINT, destination, amount),
            ],
            "innerInstructions": [],
            "logMessages": [],
        },
    }


def instruction_transaction(signature: str, parsed_type: str, pre: int, post: int):
    return {
        "blockTime": 1_700_000_000,
        "transaction": {
            "signatures": [signature],
            "message": {
                "accountKeys": [
                    {"pubkey": WALLET_A, "signer": True},
                    {"pubkey": TARGET_A_1, "signer": False},
                    {"pubkey": TARGET_POOL, "signer": False},
                ],
                "instructions": [{"programId": UNKNOWN_PROGRAM, "parsed": {"type": parsed_type, "info": {}}}],
            },
        },
        "meta": {
            "err": None,
            "fee": 5_000,
            "preBalances": [1_000_000_000, 0, 0],
            "postBalances": [999_995_000, 0, 0],
            "preTokenBalances": [balance(1, MINT, WALLET_A, pre), balance(2, MINT, WALLET_C, 100)],
            "postTokenBalances": [balance(1, MINT, WALLET_A, post), balance(2, MINT, WALLET_C, 100 + pre - post)],
            "innerInstructions": [],
            "logMessages": [],
        },
    }


def event(signature, wallet, event_type, amount, timestamp, destination=None):
    return history.NormalizedEvent(
        datetime_text(timestamp),
        timestamp,
        signature,
        MINT,
        event_type,
        wallet,
        -amount if event_type in {"SELL", "TRANSFER", "BURN", "LIQUIDITY_EVENT", "UNKNOWN"} else amount,
        amount,
        destination=destination,
        source_token_account=(f"source-{wallet}" if event_type == "TRANSFER" else None),
        destination_token_account=(f"destination-{destination}" if event_type == "TRANSFER" else None),
        evidence="mocked deterministic event",
    )


def datetime_text(timestamp):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


class StaticRpc:
    def __init__(self, supply=(1_000_000, 6), balances=None):
        self.supply = supply
        self.balances = balances or {}

    def get_token_supply(self, _mint):
        return self.supply

    def get_owner_token_accounts(self, wallet, _mint):
        return analyzer.TokenBalanceSnapshot({f"account-{wallet}": self.balances.get(wallet, 0)}, 100)

    def get_multiple_accounts(self, addresses):
        return {
            address: {"owner": analyzer.SYSTEM_PROGRAM, "executable": False, "data": ["", "base64"]}
            for address in addresses
        }


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}
        self.raw = io.BytesIO(json.dumps(self._body).encode("utf-8"))
        self.closed = False

    def json(self):
        return self._body

    def close(self):
        self.closed = True
        self.raw.close()


class ResponseSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    def post(self, _url, json, timeout, stream=False):
        del timeout, stream
        self.payloads.append(json)
        return self.responses.pop(0)


class TraceRpc:
    def __init__(self):
        self.snapshots = {
            WALLET_A: iter(
                [
                    analyzer.TokenBalanceSnapshot({TARGET_A_1: 100}, 100),
                    analyzer.TokenBalanceSnapshot({}, 101),
                    analyzer.TokenBalanceSnapshot({}, 102),
                ]
            ),
            WALLET_B: iter(
                [
                    analyzer.TokenBalanceSnapshot({TARGET_B: 100}, 101),
                    analyzer.TokenBalanceSnapshot({}, 102),
                ]
            ),
        }

    def get_owner_token_accounts(self, wallet, mint):
        return next(self.snapshots[wallet])

    def get_signatures(self, address, limit=20):
        return []

    def get_transaction(self, signature):
        return None


class TransferAttributor:
    def seed(self, addresses, max_slot):
        return None

    def attribute(self, delta, **kwargs):
        return analyzer.Attribution("TRANSFER", "transfer-sig", WALLET_B, "exact direct transfer")


class JeetAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.metadata = history.TokenMetadata(MINT, "GEN", "Generic Token", 6, 1_000_000)

    def test_arbitrary_spl_token_and_direct_sol_sale(self):
        events = history.normalize_transactions([swap_transaction("sol-sale", native_received=500_000_000)], MINT)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].mint, MINT)
        self.assertEqual(events[0].event_type, "SELL")
        self.assertEqual(events[0].quote_symbol, "SOL")
        self.assertEqual(events[0].quote_amount_raw, 500_000_000)

    def test_wsol_sale(self):
        events = history.normalize_transactions(
            [swap_transaction("wsol-sale", quote_mint=history.WSOL_MINT, quote_pre=10, quote_post=510)], MINT
        )
        self.assertEqual(events[0].event_type, "SELL")
        self.assertEqual(events[0].quote_symbol, "WSOL")
        self.assertEqual(events[0].quote_amount_raw, 500)

    def test_routed_multi_hop_swap(self):
        events = history.normalize_transactions(
            [swap_transaction("route", programs=("JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", UNKNOWN_PROGRAM))],
            MINT,
        )
        self.assertTrue(events[0].routed)
        self.assertEqual(events[0].router, "JUPITER_V6")

    def test_normal_transfer_is_not_sell(self):
        events = history.normalize_transactions([transfer_transaction("transfer")], MINT)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "TRANSFER")
        self.assertEqual(events[0].destination, WALLET_B)

    def test_custom_program_plus_exact_token_transfer_is_concrete(self):
        amount = 25_125_000_000_000
        transaction = transfer_transaction(
            "4iiK-live-structural-pattern",
            source=WALLET_A,
            destination=WALLET_B,
            amount=amount,
            block_time=1_700_000_100,
        )
        transaction["transaction"]["message"]["instructions"].insert(
            0,
            {
                "programId": UNKNOWN_PROGRAM,
                "accounts": [TARGET_A_1, TARGET_B],
                "data": "opaque-custom-program-data",
            },
        )
        rows = history.normalize_transactions(
            [transaction],
            MINT,
            include_unknown_increases=True,
        )
        self.assertEqual(len(rows), 1)
        transfer = rows[0]
        self.assertEqual(transfer.event_type, "TRANSFER")
        self.assertTrue(history.is_concrete_transfer_event(transfer))
        self.assertEqual(transfer.wallet, WALLET_A)
        self.assertEqual(transfer.destination, WALLET_B)
        self.assertEqual(transfer.token_amount_raw, amount)
        self.assertEqual(transfer.source_token_account, TARGET_A_1)
        self.assertEqual(transfer.destination_token_account, TARGET_B)

        report = history.verify_exit(
            rows,
            self.metadata,
            WALLET_A,
            {WALLET_A: 0, WALLET_B: amount, WALLET_C: 777},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
            window_start=1_700_000_000,
            window_end=1_700_000_200,
        )
        self.assertEqual(report["status"], "NOT_OUT")
        self.assertEqual(report["reacquisitions"]["amount_raw"], 0)
        self.assertEqual(report["linked_wallet_increases"]["amount_raw"], 0)
        self.assertEqual(report["reconstructed_starting_inventory_raw"], amount)
        self.assertEqual(report["reconciliation"]["linked_or_unrelated_wallet_balances_included_in_root_start_raw"], 0)
        self.assertEqual(report["reconciliation"]["excluded_unrelated_wallet_inventory_raw"], 777)
        self.assertEqual(len(report["trace_edges"]), 1)
        edge = report["trace_edges"][0]
        self.assertEqual(edge["relationship_type"], "IN_WINDOW_TRANSFER")
        self.assertEqual(edge["signature"], "4iiK-live-structural-pattern")
        self.assertEqual(edge["amount_raw"], amount)
        self.assertEqual(edge["timestamp"], datetime_text(1_700_000_100))

        output = io.StringIO()
        with redirect_stdout(output):
            analyzer._print_verification(report, decimals=6, window_label="mocked five days")
        terminal = output.getvalue()
        self.assertIn("signature=4iiK-live-structural-pattern", terminal)
        self.assertIn("amount=25125000", terminal)
        self.assertIn(f"timestamp={datetime_text(1_700_000_100)}", terminal)

    def test_balance_match_without_transfer_is_unproven(self):
        amount = 25_125_000_000_000
        transaction = transfer_transaction(
            "balance-match-only",
            source=WALLET_A,
            destination=WALLET_B,
            amount=amount,
            block_time=1_700_000_100,
        )
        transaction["transaction"]["message"]["instructions"] = [
            {
                "programId": UNKNOWN_PROGRAM,
                "accounts": [TARGET_A_1, TARGET_B],
                "data": "no-parsed-target-token-transfer",
            }
        ]
        rows = history.normalize_transactions(
            [transaction],
            MINT,
            include_unknown_increases=True,
        )
        root_decrease = next(row for row in rows if row.wallet == WALLET_A)
        self.assertEqual(root_decrease.event_type, "UNKNOWN")
        self.assertIsNone(root_decrease.destination)
        self.assertEqual(root_decrease.candidate_destination, WALLET_B)
        self.assertFalse(any(history.is_concrete_transfer_event(row) for row in rows))
        self.assertEqual(set(history.discover_traced_wallets(rows, WALLET_A, 3)), {WALLET_A})

        report = history.verify_exit(
            rows,
            self.metadata,
            WALLET_A,
            {WALLET_A: 0, WALLET_B: amount},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
            window_start=1_700_000_000,
            window_end=1_700_000_200,
        )
        self.assertEqual(report["status"], "UNRESOLVED")
        self.assertFalse(report["trace_edges"])
        self.assertFalse(report["traced_wallets"])
        self.assertEqual(report["reacquisitions"]["amount_raw"], 0)
        self.assertEqual(report["linked_wallet_increases"]["amount_raw"], 0)
        self.assertEqual(report["reconciliation"]["excluded_unrelated_wallet_inventory_raw"], amount)
        self.assertEqual(len(report["unproven_inventory_relationships"]), 1)
        self.assertFalse(report["unproven_inventory_relationships"][0]["creates_trace_edge"])

    def test_wallet_and_verifier_agree_on_exact_incoming_transfer(self):
        amount = 25_125_000_000_000
        transaction = transfer_transaction(
            "4iiK-wallet-consistency-pattern",
            source=WALLET_A,
            destination=WALLET_B,
            amount=amount,
            block_time=1_700_000_100,
        )
        transaction["transaction"]["message"]["instructions"].insert(
            0,
            {"programId": UNKNOWN_PROGRAM, "accounts": [TARGET_A_1, TARGET_B], "data": "opaque"},
        )

        class WalletProvider:
            def get_wallet_events(self, wallet, mint, start, end):
                self.request = (wallet, mint, start, end)
                return history.ProviderBatch(
                    [transaction],
                    True,
                    "mocked-provider",
                    "mocked complete wallet history",
                    "none",
                    1,
                )

            def get_wallet_token_balance(self, wallet, mint):
                return amount

            def get_metadata(self, mint):
                return {
                    "content": {"metadata": {"symbol": "GEN", "name": "generic token"}},
                    "token_info": {"decimals": 6, "supply": 999_992_977_719_609},
                }

        provider = WalletProvider()
        rpc = StaticRpc((999_992_977_719_609, 6))
        with tempfile.TemporaryDirectory() as temporary:
            args = analyzer.argparse.Namespace(
                wallet=WALLET_B,
                mint=MINT,
                days=5.0,
                max_pages=1000,
                trace_depth=3,
                output_dir=Path(temporary),
                provider="helius",
            )
            output = io.StringIO()
            with patch.object(analyzer, "_helius_provider", return_value=(provider, rpc)), redirect_stdout(output):
                result = analyzer._run_wallet(args, timeout_seconds=15.0)
            records = list(Path(temporary).glob("wallet-events-*.jsonl"))
            _header, normalized = history.read_normalized_jsonl(records[0])
        self.assertEqual(result, 0)
        self.assertIn("INCOMING TRANSFERS: 1", output.getvalue())
        self.assertIn("OUTGOING TRANSFERS: 0", output.getvalue())
        self.assertIn("CONFIRMED BUYS/REACQUISITIONS: 0", output.getvalue())
        self.assertIn("CURRENT BALANCE: 25125000", output.getvalue())
        self.assertEqual(len(normalized), 1)
        self.assertTrue(history.is_concrete_transfer_event(normalized[0]))

    def test_known_router_participation_never_becomes_transfer_edge(self):
        transaction = transfer_transaction("router-flow", amount=500, block_time=1_700_000_100)
        transaction["transaction"]["message"]["instructions"].insert(
            0,
            {
                "programId": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
                "accounts": [TARGET_A_1, TARGET_B],
                "data": "opaque-router-data",
            },
        )
        rows = history.normalize_transactions([transaction], MINT, include_unknown_increases=True)
        seller = next(row for row in rows if row.wallet == WALLET_A)
        self.assertEqual(seller.event_type, "SELL")
        self.assertIsNone(seller.destination)
        self.assertFalse(history.is_concrete_transfer_event(seller))
        self.assertEqual(set(history.discover_traced_wallets(rows, WALLET_A, 3)), {WALLET_A})

    def test_transfer_then_recipient_sell_is_linked_not_same_control(self):
        rows = history.normalize_transactions(
            [
                transfer_transaction("transfer", amount=500, block_time=1_700_000_000),
                swap_transaction("recipient-sale", wallet=WALLET_B, target_pre=500, target_post=100, block_time=1_700_000_100),
            ],
            MINT,
        )
        history.apply_transfer_links(rows, 1)
        sale = next(row for row in rows if row.event_type == "SELL")
        self.assertTrue(sale.linked_by_transfer)
        self.assertEqual(sale.linked_from, [WALLET_A])

    def test_linked_by_transfer_requires_complete_normalized_transfer_proof(self):
        unsupported = event("unsupported", WALLET_A, "TRANSFER", 100, 100, destination=WALLET_B)
        unsupported.source_token_account = None
        sale = event("recipient-sale", WALLET_B, "SELL", 100, 200)
        linked = history.apply_transfer_links([unsupported, sale], 1)
        self.assertFalse(sale.linked_by_transfer)
        self.assertFalse(sale.linked_from)
        self.assertFalse(linked)
        self.assertEqual(set(history.discover_traced_wallets([unsupported, sale], WALLET_A, 1)), {WALLET_A})

    def test_liquidity_event(self):
        events = history.normalize_transactions([instruction_transaction("lp", "addLiquidity", 1_000, 500)], MINT)
        self.assertEqual(events[0].event_type, "LIQUIDITY_EVENT")

    def test_mint_and_burn(self):
        minted = history.normalize_transactions([instruction_transaction("mint", "mintTo", 0, 500)], MINT)
        burned = history.normalize_transactions([instruction_transaction("burn", "burn", 500, 0)], MINT)
        self.assertIn("MINT", [row.event_type for row in minted])
        self.assertIn("BURN", [row.event_type for row in burned])

    def test_failed_transaction_and_duplicate_signature_are_ignored(self):
        failed = swap_transaction("failed", failed=True)
        duplicate = swap_transaction("same")
        events = history.normalize_transactions([failed, duplicate, duplicate], MINT)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].signature, "same")

    def test_multiple_token_accounts_are_aggregated_for_one_owner(self):
        tx = swap_transaction("multi")
        tx["transaction"]["message"]["accountKeys"].insert(2, {"pubkey": TARGET_A_2, "signer": False})
        # Rebuild balance indexes after inserting the second owned account.
        tx["meta"]["preTokenBalances"] = [
            balance(1, MINT, WALLET_A, 600),
            balance(2, MINT, WALLET_A, 400),
            balance(3, MINT, WALLET_C, 100),
        ]
        tx["meta"]["postTokenBalances"] = [
            balance(1, MINT, WALLET_A, 300),
            balance(2, MINT, WALLET_A, 200),
            balance(3, MINT, WALLET_C, 600),
        ]
        events = history.normalize_transactions([tx], MINT)
        self.assertEqual(events[0].token_amount_raw, 500)

    def test_inspect_aggregates_multiple_token_accounts_per_owner(self):
        accounts = [
            {"address": TARGET_A_1, "owner": WALLET_A, "amount": "400"},
            {"address": TARGET_A_2, "owner": WALLET_A, "amount": "600"},
            {"address": TARGET_B, "owner": WALLET_B, "amount": "500"},
        ]
        with redirect_stdout(io.StringIO()):
            rows = analyzer.inspect_holders(StaticRpc((2_000, 0)), MINT, limit=25, symbol="GEN", all_token_accounts=accounts)
        self.assertEqual(rows[0]["wallet"], WALLET_A)
        self.assertEqual(rows[0]["balance_raw"], 1_000)
        self.assertEqual(rows[0]["token_account_count"], 2)

    def test_wallet_buy_reacquisition(self):
        events = history.normalize_transactions(
            [swap_transaction("buy", target_pre=100, target_post=500, native_received=-100_000)], MINT
        )
        buy = next(row for row in events if row.event_type == "BUY")
        self.assertEqual(buy.token_delta_raw, 400)

    def test_signer_market_increase_remains_buy_when_transaction_also_mints(self):
        transaction = swap_transaction(
            "create-and-buy", target_pre=0, target_post=500, native_received=-100_000
        )
        transaction["transaction"]["message"]["instructions"].append(
            {
                "programId": history.TOKEN_2022_PROGRAM,
                "parsed": {
                    "type": "mintTo",
                    "info": {
                        "account": "SyntheticCurveTokenAccount",
                        "mint": MINT,
                        "amount": "1000",
                    },
                },
            }
        )

        events = history.normalize_transactions([transaction], MINT)

        signer_event = next(row for row in events if row.wallet == WALLET_A)
        self.assertEqual(signer_event.event_type, "BUY")
        self.assertIn("signer-owned token increase", signer_event.evidence)

    def test_repeated_sells_emit_dominant_seller(self):
        events = [
            event("a1", WALLET_A, "SELL", 300, 100),
            event("a2", WALLET_A, "SELL", 300, 200),
            event("a3", WALLET_A, "SELL", 300, 300),
            event("b1", WALLET_B, "SELL", 100, 400),
        ]
        summary = history.analyze_sellers(
            events,
            self.metadata,
            {WALLET_A: 100, WALLET_B: 100},
            coverage_complete=True,
            config=history.AnalysisConfig(dominant_threshold=Decimal("60")),
        )
        self.assertEqual(summary["large_sell_concentration"]["result"], "DOMINANT_SELLER")
        self.assertEqual(summary["sellers"][0]["wallet"], WALLET_A)

    def test_largest_seller_summary_uses_founder_row_not_market_totals(self):
        summary = {
            "token": analyzer._metadata_record(self.metadata),
            "confirmed_sells": 120,
            "tokens_sold_raw": 43_465_000_000_000,
            "sellers": [
                {
                    "rank": 1,
                    "wallet": WALLET_A,
                    "sells": 9,
                    "tokens_sold_raw": 13_824_303_368_705,
                    "quote_totals": {},
                    "largest_sale_raw": 2_000_000_000_000,
                    "current_balance_raw": 1_015_963_800_648,
                    "observed_inventory_remaining_percentage": "2.5",
                    "status": "DISTRIBUTING",
                    "transfer_linked_inventory_raw": 0,
                }
            ],
            "large_sell_concentration": {
                "individual_sells": [],
                "wallets": [],
                "result": "NO_SINGLE_DOMINANT_SELLER",
            },
        }
        output = io.StringIO()
        with redirect_stdout(output):
            history.print_analysis(summary, window_label="five days", mint=MINT)
        text = output.getvalue()
        self.assertIn("Largest seller confirmed sells: 9", text)
        self.assertIn("Largest seller tokens sold: 13824303.368705", text)
        self.assertIn("Market-wide confirmed sells: 120", text)
        self.assertIn("Market-wide tokens sold: 43465000", text)

    def test_distributed_selling_has_no_single_dominant_seller(self):
        events = [
            event("a", WALLET_A, "SELL", 100, 100),
            event("b", WALLET_B, "SELL", 100, 200),
            event("c", WALLET_C, "SELL", 100, 300),
        ]
        summary = history.analyze_sellers(
            events,
            self.metadata,
            {WALLET_A: 100, WALLET_B: 100, WALLET_C: 100},
            coverage_complete=True,
            config=history.AnalysisConfig(dominant_threshold=Decimal("60")),
        )
        self.assertEqual(summary["large_sell_concentration"]["result"], "NO_SINGLE_DOMINANT_SELLER")

    def test_incomplete_provider_coverage_fails_closed(self):
        with self.assertRaises(history.CoverageError):
            history.analyze_sellers(
                [event("a", WALLET_A, "SELL", 100, 100)],
                self.metadata,
                {WALLET_A: 0},
                coverage_complete=False,
                config=history.AnalysisConfig(),
            )

    def test_helius_rate_limit_exhaustion_is_redacted(self):
        session = ResponseSession([FakeResponse(429), FakeResponse(429)])
        provider = history.HeliusHistoricalProvider(
            "super-secret",
            StaticRpc(),
            session=session,
            max_retries=1,
            sleeper=lambda _seconds: None,
        )
        with self.assertRaises(history.ProviderRateLimitError) as raised:
            provider.get_token_events(MINT, 1_700_000_000, 1_700_000_100)
        self.assertNotIn("super-secret", str(raised.exception))
        self.assertNotIn("mainnet.helius", str(raised.exception))

    def test_helius_pagination_exhaustion_is_complete_and_deduplicated(self):
        tx1 = swap_transaction("one")
        tx2 = swap_transaction("two", block_time=1_700_000_100)
        session = ResponseSession(
            [
                FakeResponse(200, {"result": {"data": [tx1], "paginationToken": "next"}}),
                FakeResponse(200, {"result": {"data": [tx1, tx2], "paginationToken": None}}),
            ]
        )
        provider = history.HeliusHistoricalProvider("key", StaticRpc(), session=session, sleeper=lambda _s: None)
        batch = provider.get_token_events(MINT, 1_700_000_000, 2_000_000_000)
        self.assertTrue(batch.complete)
        self.assertEqual(len(batch.transactions), 2)
        self.assertEqual(session.payloads[0]["method"], "getTransactionsForAddress")
        self.assertEqual(session.payloads[0]["params"][0], MINT)
        self.assertEqual(session.payloads[0]["params"][1]["filters"]["status"], "succeeded")

    def test_helius_page_cap_marks_coverage_incomplete(self):
        session = ResponseSession(
            [FakeResponse(200, {"result": {"data": [swap_transaction("one")], "paginationToken": "more"}})]
        )
        provider = history.HeliusHistoricalProvider(
            "key", StaticRpc(), session=session, max_pages=1, sleeper=lambda _s: None
        )
        batch = provider.get_token_events(MINT, 1_700_000_000, 2_000_000_000)
        self.assertFalse(batch.complete)
        self.assertIn("max_pages", batch.limitation)

    def test_metadata_unavailable_falls_back_to_mint_and_onchain_values(self):
        class NoMetadata:
            def get_metadata(self, mint):
                return None

        metadata = analyzer.resolve_metadata(StaticRpc((123_456, 8)), MINT, NoMetadata())
        self.assertEqual(metadata.supply, 123_456)
        self.assertEqual(metadata.decimals, 8)
        self.assertEqual(metadata.symbol, "UNKNOWN")

    def test_jeet_status_is_suppressed_when_transfer_linked_inventory_remains(self):
        events = [
            event("sell", WALLET_A, "SELL", 10, 100),
            event("move", WALLET_A, "TRANSFER", 89, 200, destination=WALLET_B),
        ]
        summary = history.analyze_sellers(
            events,
            self.metadata,
            {WALLET_A: 0, WALLET_B: 89},
            coverage_complete=True,
            config=history.AnalysisConfig(trace_depth=1),
        )
        seller = summary["sellers"][0]
        self.assertNotEqual(seller["status"], "JEET_OUT")
        self.assertEqual(seller["transfer_linked_inventory_raw"], 89)

    def test_jeet_status_without_transferred_inventory(self):
        summary = history.analyze_sellers(
            [event("sell", WALLET_A, "SELL", 99, 100)],
            self.metadata,
            {WALLET_A: 1},
            coverage_complete=True,
            config=history.AnalysisConfig(trace_depth=1),
        )
        self.assertEqual(summary["sellers"][0]["status"], "JEET_OUT")

    def test_verify_exit_parser_accepts_trace_depth_three(self):
        args = analyzer.build_parser().parse_args(
            [
                "verify-exit",
                "--mint",
                MINT,
                "--wallet",
                WALLET_A,
                "--days",
                "7",
                "--trace-depth",
                "3",
            ]
        )
        self.assertEqual(args.mode, "verify-exit")
        self.assertEqual(args.days, 7)
        self.assertEqual(args.trace_depth, 3)

    def test_verify_exit_zero_original_but_linked_inventory_is_not_out(self):
        rows = [event("move", WALLET_A, "TRANSFER", 100, 100, destination=WALLET_B)]
        report = history.verify_exit(
            rows,
            self.metadata,
            WALLET_A,
            {WALLET_A: 0, WALLET_B: 100},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=1,
        )
        self.assertEqual(report["status"], "NOT_OUT")
        self.assertEqual(report["fresh_current_inventory_raw"], 0)
        self.assertEqual(report["all_traced_current_inventory_raw"], 100)

    def test_verify_exit_depth_three_follows_chain_to_confirmed_sale(self):
        rows = [
            event("a-move", WALLET_A, "TRANSFER", 100, 100, destination=WALLET_B),
            event("b-move", WALLET_B, "TRANSFER", 100, 200, destination=WALLET_C),
            event("c-move", WALLET_C, "TRANSFER", 100, 300, destination=WALLET_D),
            event("d-sell", WALLET_D, "SELL", 100, 400),
        ]
        report = history.verify_exit(
            rows,
            self.metadata,
            WALLET_A,
            {WALLET_A: 0, WALLET_B: 0, WALLET_C: 0, WALLET_D: 0},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
        )
        self.assertEqual(report["status"], "VERIFIED_OUT")
        self.assertEqual(report["confirmed_sales"]["transfer_linked_amount_raw"], 100)
        self.assertEqual([row["depth"] for row in report["traced_wallets"]], [1, 2, 3])
        self.assertFalse(report["unresolved_branches"])

    def test_verify_exit_depth_boundary_is_unresolved(self):
        rows = [
            event("a-move", WALLET_A, "TRANSFER", 100, 100, destination=WALLET_B),
            event("b-move", WALLET_B, "TRANSFER", 100, 200, destination=WALLET_C),
        ]
        report = history.verify_exit(
            rows,
            self.metadata,
            WALLET_A,
            {WALLET_A: 0, WALLET_B: 0},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=1,
        )
        self.assertEqual(report["status"], "UNRESOLVED")
        self.assertIn("TRACE_DEPTH_EXHAUSTED", {row["kind"] for row in report["unresolved_branches"]})

    def test_verify_exit_labels_only_evidenced_before_window_transfer_as_preexisting(self):
        rows = [event("older-transfer", WALLET_A, "TRANSFER", 100, 100, destination=WALLET_B)]
        report = history.verify_exit(
            rows,
            self.metadata,
            WALLET_A,
            {WALLET_A: 0, WALLET_B: 100},
            coverage_complete=True,
            coverage_scope="mocked history including pre-window proof",
            coverage_limitation="none",
            trace_depth=3,
            window_start=200,
            window_end=300,
        )
        self.assertEqual(report["trace_edges"][0]["relationship_type"], "PREEXISTING_RELATIONSHIP")
        self.assertEqual(len(report["preexisting_relationships"]), 1)
        self.assertFalse(report["in_window_transfer_edges"])

    def test_verify_exit_reacquisition_blocks_verified_out(self):
        rows = [
            event("a-sell", WALLET_A, "SELL", 100, 100),
            event("b-buy", WALLET_A, "BUY", 10, 200),
            event("c-sell", WALLET_A, "SELL", 10, 300),
        ]
        report = history.verify_exit(
            rows,
            self.metadata,
            WALLET_A,
            {WALLET_A: 0},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
        )
        self.assertEqual(report["status"], "UNRESOLVED")
        self.assertEqual(report["reacquisitions"]["amount_raw"], 10)
        self.assertEqual(report["linked_wallet_increases"]["amount_raw"], 0)

    def test_verify_exit_does_not_double_count_market_settlement_transfer(self):
        rows = [
            event("exit", WALLET_A, "SELL", 100, 100),
            event("reentry", WALLET_C, "TRANSFER", 25, 200, destination=WALLET_A),
            event("reentry", WALLET_A, "BUY", 25, 200),
        ]
        report = history.verify_exit(
            rows,
            self.metadata,
            WALLET_A,
            {WALLET_A: 25},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
        )
        self.assertEqual(report["status"], "NOT_OUT")
        self.assertEqual(report["reconstructed_starting_inventory_raw"], 100)
        self.assertEqual(report["incoming_transfers"]["count"], 0)
        self.assertEqual(report["reacquisitions"]["count"], 1)
        self.assertEqual(report["reacquisitions"]["amount_raw"], 25)
        self.assertEqual(report["excluded_market_settlement_transfers"]["count"], 1)
        self.assertEqual(
            report["reconciliation"]["market_settlement_transfer_in_excluded_raw"], 25
        )

    def test_verify_exit_keeps_unmatched_direct_transfer_in(self):
        rows = [
            event("exit", WALLET_A, "SELL", 100, 100),
            event("direct-transfer", WALLET_C, "TRANSFER", 25, 200, destination=WALLET_A),
        ]
        report = history.verify_exit(
            rows,
            self.metadata,
            WALLET_A,
            {WALLET_A: 25},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
        )
        self.assertEqual(report["reconstructed_starting_inventory_raw"], 100)
        self.assertEqual(report["incoming_transfers"]["count"], 1)
        self.assertEqual(report["incoming_transfers"]["amount_raw"], 25)
        self.assertEqual(report["reacquisitions"]["count"], 1)
        self.assertEqual(report["reacquisitions"]["events"][0]["event_type"], "TRANSFER_IN")
        self.assertEqual(report["excluded_market_settlement_transfers"]["count"], 0)

    def test_verify_exit_counts_linked_market_buy_once(self):
        rows = [
            event("root-transfer", WALLET_A, "TRANSFER", 100, 100, destination=WALLET_B),
            event("linked-buy", WALLET_C, "TRANSFER", 10, 200, destination=WALLET_B),
            event("linked-buy", WALLET_B, "BUY", 10, 200),
            event("linked-sale", WALLET_B, "SELL", 110, 300),
        ]
        report = history.verify_exit(
            rows,
            self.metadata,
            WALLET_A,
            {WALLET_A: 0, WALLET_B: 0},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
        )
        self.assertEqual(report["status"], "UNRESOLVED")
        self.assertEqual(report["linked_wallet_increases"]["count"], 1)
        self.assertEqual(report["linked_wallet_increases"]["amount_raw"], 10)
        self.assertEqual(report["linked_wallet_increases"]["events"][0]["event_type"], "BUY")

    def test_verify_exit_same_second_linked_buy_fails_closed(self):
        rows = [
            # Signature sorting places the buy first, but second-granularity
            # blockTime cannot prove that it preceded the traced transfer.
            event("a-buy", WALLET_B, "BUY", 10, 100),
            event("b-move", WALLET_A, "TRANSFER", 100, 100, destination=WALLET_B),
            event("c-sell", WALLET_B, "SELL", 110, 100),
        ]
        report = history.verify_exit(
            rows,
            self.metadata,
            WALLET_A,
            {WALLET_A: 0, WALLET_B: 0},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
        )
        self.assertEqual(report["status"], "UNRESOLVED")
        self.assertEqual(report["reacquisitions"]["amount_raw"], 0)
        self.assertEqual(report["linked_wallet_increases"]["amount_raw"], 10)

    def test_verify_exit_strict_normalization_retains_unknown_linked_increase(self):
        unknown_incoming = transfer_transaction(
            "unknown-incoming",
            source=WALLET_C,
            destination=WALLET_B,
            amount=10,
            block_time=200,
        )
        unknown_incoming["transaction"]["message"]["instructions"][0]["programId"] = UNKNOWN_PROGRAM
        rows = history.normalize_transactions(
            [
                transfer_transaction("root-move", amount=100, block_time=100),
                unknown_incoming,
                swap_transaction(
                    "recipient-sale",
                    wallet=WALLET_B,
                    target_pre=110,
                    target_post=0,
                    block_time=300,
                ),
            ],
            MINT,
            include_unknown_increases=True,
        )
        retained = [
            row
            for row in rows
            if row.wallet == WALLET_B and row.event_type == "UNKNOWN" and row.token_delta_raw == 10
        ]
        self.assertEqual(len(retained), 1)
        report = history.verify_exit(
            rows,
            self.metadata,
            WALLET_A,
            {WALLET_A: 0, WALLET_B: 0},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
        )
        self.assertEqual(report["status"], "UNRESOLVED")
        self.assertEqual(report["reacquisitions"]["amount_raw"], 0)
        self.assertEqual(report["linked_wallet_increases"]["amount_raw"], 10)

    def test_verify_exit_incomplete_coverage_is_insufficient_data(self):
        report = history.verify_exit(
            [event("sell", WALLET_A, "SELL", 100, 100)],
            self.metadata,
            WALLET_A,
            {WALLET_A: 0},
            coverage_complete=False,
            coverage_scope="mocked partial history",
            coverage_limitation="page cap",
            trace_depth=3,
        )
        self.assertEqual(report["status"], "INSUFFICIENT_DATA")

    def test_verify_exit_missing_fresh_balance_is_insufficient_data(self):
        report = history.verify_exit(
            [event("sell", WALLET_A, "SELL", 100, 100)],
            self.metadata,
            WALLET_A,
            {WALLET_A: None},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
        )
        self.assertEqual(report["status"], "INSUFFICIENT_DATA")

    def test_verify_exit_unknown_reduction_is_unresolved(self):
        report = history.verify_exit(
            [event("mystery", WALLET_A, "UNKNOWN", 100, 100)],
            self.metadata,
            WALLET_A,
            {WALLET_A: 0},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
        )
        self.assertEqual(report["status"], "UNRESOLVED")
        self.assertIn("AMBIGUOUS_ROOT_REDUCTION", {row["kind"] for row in report["unresolved_branches"]})

    def test_verify_exit_does_not_trace_balance_matched_unknown_flow(self):
        ambiguous = event("mystery", WALLET_A, "UNKNOWN", 100, 100)
        ambiguous.candidate_destination = WALLET_B
        report = history.verify_exit(
            [ambiguous],
            self.metadata,
            WALLET_A,
            {WALLET_A: 0, WALLET_B: 100},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
        )
        self.assertEqual(report["status"], "UNRESOLVED")
        self.assertFalse(report["traced_wallets"])
        self.assertFalse(report["trace_edges"])
        self.assertEqual(report["unproven_inventory_relationships"][0]["candidate_destination_owner"], WALLET_B)
        self.assertEqual(report["reacquisitions"]["amount_raw"], 0)
        self.assertEqual(report["confirmed_sales"]["count"], 0)
        self.assertIn("AMBIGUOUS_ROOT_REDUCTION", {row["kind"] for row in report["unresolved_branches"]})

    def test_verify_exit_material_original_inventory_is_not_out(self):
        report = history.verify_exit(
            [event("sell", WALLET_A, "SELL", 99, 100)],
            self.metadata,
            WALLET_A,
            {WALLET_A: 1},
            coverage_complete=True,
            coverage_scope="mocked complete mint history",
            coverage_limitation="none",
            trace_depth=3,
        )
        self.assertEqual(report["status"], "NOT_OUT")
        self.assertEqual(report["material_threshold_raw"], 1)

    def test_verify_exit_cli_uses_mocked_history_and_fresh_all_account_balances(self):
        class VerifyProvider:
            def get_token_events(self, mint, start, end):
                self.request = (mint, start, end)
                return history.ProviderBatch(
                    [
                        transfer_transaction("move", amount=100, block_time=100),
                        swap_transaction(
                            "recipient-sale",
                            wallet=WALLET_B,
                            target_pre=100,
                            target_post=0,
                            block_time=200,
                        ),
                    ],
                    True,
                    "mocked-provider",
                    "mocked complete mint history",
                    "none",
                    2,
                )

            def get_metadata(self, mint):
                return {
                    "content": {"metadata": {"symbol": "GEN", "name": "Generic Token"}},
                    "token_info": {"decimals": 6, "supply": 1_000_000},
                }

        class VerifyRpc:
            def __init__(self):
                self.calls = []

            def get_token_supply(self, mint):
                return 1_000_000, 6

            def get_owner_token_accounts(self, wallet, mint, *, commitment="confirmed"):
                self.calls.append((wallet, mint))
                self.assertion_commitment = commitment
                # Two accounts for the root prove that the CLI consumes the
                # aggregate all-owned-account snapshot rather than one ATA.
                accounts = {"root-one": 0, "root-two": 0} if wallet == WALLET_A else {"linked-one": 0}
                return analyzer.TokenBalanceSnapshot(accounts, 999)

            def get_multiple_accounts(self, addresses):
                return {
                    address: {"owner": analyzer.SYSTEM_PROGRAM, "executable": False, "data": ["", "base64"]}
                    for address in addresses
                }

        provider = VerifyProvider()
        fresh_rpc = VerifyRpc()
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with patch.object(analyzer, "_helius_provider", return_value=(provider, fresh_rpc)), patch.object(
                analyzer, "ReadOnlyRpcClient", return_value=fresh_rpc
            ), redirect_stdout(output):
                result = analyzer.main(
                    [
                        "verify-exit",
                        "--mint",
                        MINT,
                        "--wallet",
                        WALLET_A,
                        "--days",
                        "5",
                        "--trace-depth",
                        "3",
                        "--output-dir",
                        temporary,
                    ]
                )
            reports = list(Path(temporary).glob("verify-exit-*.json"))
            normalized = list(Path(temporary).glob("verify-exit-events-*.jsonl"))
            report = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(report["status"], "VERIFIED_OUT")
        self.assertEqual(len(reports), 1)
        self.assertEqual(len(normalized), 1)
        self.assertEqual({wallet for wallet, _mint in fresh_rpc.calls}, {WALLET_A, WALLET_B})
        self.assertEqual(fresh_rpc.assertion_commitment, "finalized")
        self.assertIn("STATUS: VERIFIED_OUT", output.getvalue())

    def test_verify_exit_cli_missing_provider_key_is_insufficient_and_redacted(self):
        output = io.StringIO()
        errors = io.StringIO()
        with patch.dict(
            analyzer.os.environ,
            {"SOLANA_RPC_URL": "https://rpc.example.invalid/path?api-key=must-not-print"},
            clear=True,
        ), redirect_stdout(output), redirect_stderr(errors):
            result = analyzer.main(
                ["verify-exit", "--mint", MINT, "--wallet", WALLET_A, "--days", "5", "--trace-depth", "3"]
            )
        combined = output.getvalue() + errors.getvalue()
        self.assertEqual(result, 2)
        self.assertIn("STATUS: INSUFFICIENT_DATA", output.getvalue())
        self.assertNotIn("must-not-print", combined)
        self.assertNotIn("rpc.example", combined)

    def test_live_watch_does_not_jeet_immediately_after_traced_transfer(self):
        rpc = TraceRpc()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "watch.jsonl"
            service = analyzer.WalletWatcher(
                rpc,
                rpc_url="https://example.invalid",
                wallet=WALLET_A,
                mint=MINT,
                decimals=0,
                symbol="GEN",
                trace_depth=1,
                writer=analyzer.BoundedJsonlWriter(path, max_bytes=100_000, backups=1),
            )
            service.attributor = TransferAttributor()
            output = io.StringIO()
            with redirect_stdout(output):
                service.reconcile("start")
                service.reconcile("transfer")
                after_transfer = output.getvalue()
                service.reconcile("linked_gone")
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertNotIn("JEET_OUT", after_transfer)
        self.assertEqual(sum(row["event_type"] == "JEET_OUT" for row in records), 1)

    def test_normalized_jsonl_round_trip_supports_offline_analysis(self):
        rows = [event("sell", WALLET_A, "SELL", 99, 100)]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            history.write_normalized_jsonl(
                path,
                {
                    "mint": MINT,
                    "coverage_complete": True,
                    "token": analyzer._metadata_record(self.metadata),
                    "current_balances": {WALLET_A: 1},
                },
                rows,
            )
            header, loaded = history.read_normalized_jsonl(path)
        self.assertTrue(header["coverage_complete"])
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].signature, "sell")

    def test_analyze_file_cli_runs_without_provider_or_network(self):
        rows = [event("sell", WALLET_A, "SELL", 99, 100)]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "events.jsonl"
            history.write_normalized_jsonl(
                path,
                {
                    "mint": MINT,
                    "coverage_complete": True,
                    "token": analyzer._metadata_record(self.metadata),
                    "current_balances": {WALLET_A: 1},
                },
                rows,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                result = analyzer.main(
                    [
                        "analyze-file",
                        "--mint",
                        MINT,
                        "--input",
                        str(path),
                        "--output-dir",
                        str(directory),
                    ]
                )
            self.assertEqual(result, 0)
            self.assertTrue((directory / "seller-leaderboard-events.csv").exists())
            self.assertTrue((directory / "summary-events.json").exists())
            self.assertIn("WATCH COMMAND", output.getvalue())

    def test_generic_source_has_only_read_network_surfaces(self):
        imported_roots = set()
        called_attributes = set()
        for module in (analyzer, history):
            tree = ast.parse(inspect.getsource(module))
            imported_roots.update(
                alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names
            )
            called_attributes.update(
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            )
        self.assertTrue({"solders", "solana", "nacl", "bottotrot_runner_hunter"}.isdisjoint(imported_roots))
        self.assertTrue(
            {
                "sign",
                "sign_message",
                "send_raw_transaction",
                "send_transaction",
                "swap",
            }.isdisjoint(called_attributes)
        )
        self.assertEqual(
            history.HeliusHistoricalProvider.ALLOWED_METHODS,
            {"getAsset", "getTokenAccounts", "getTransaction", "getTransactionsForAddress", "getTransfersByAddress"},
        )
        self.assertTrue(
            {"sendTransaction", "simulateTransaction", "requestAirdrop"}.isdisjoint(
                analyzer.ReadOnlyRpcClient.ALLOWED_METHODS
            )
        )


    def test_native_gain_does_not_turn_exact_direct_transfer_into_sell(self) -> None:
        tx = transfer_transaction("direct-transfer-native-gain")
        tx["meta"]["postBalances"][0] = (
            tx["meta"]["preBalances"][0] + 200_000_000 - tx["meta"]["fee"]
        )
        events = history.normalize_transaction(tx, MINT)
        outgoing = [
            event
            for event in events
            if event.wallet == WALLET_A and event.token_delta_raw < 0
        ]
        self.assertEqual(len(outgoing), 1)
        self.assertEqual(outgoing[0].event_type, "TRANSFER")
        self.assertEqual(outgoing[0].destination, WALLET_B)


if __name__ == "__main__":
    unittest.main()
