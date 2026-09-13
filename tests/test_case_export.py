from __future__ import annotations

from copy import deepcopy
import json
import unittest

from jeet_analyzer.case_export import build_public_case, replay_cluster_receipt


MINT = "CaseMint1111111111111111111111111111111111"
ROOT = "CaseRoot1111111111111111111111111111111111"
POOL = "CasePool1111111111111111111111111111111111"
RELATED = "CaseRelated1111111111111111111111111111111"
TOKEN_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


def autopsy(signature, timestamp, delta, *, transfer=None):
    return {
        "signature": signature,
        "timestamp": f"2026-01-01T00:0{timestamp}:00Z",
        "block_time": 100 + timestamp,
        "token_owner_deltas": {ROOT: {MINT: {"delta_raw": delta, "decimals": 6}}},
        "token_transfers": ([] if transfer is None else [transfer]),
        "instruction_summaries": [],
    }


def transfer(signature, timestamp, source, destination, amount):
    return {
        "signature": signature,
        "timestamp": f"2026-01-01T00:0{timestamp}:00Z",
        "block_time": 100 + timestamp,
        "mint": MINT,
        "source_owner": source,
        "destination_owner": destination,
        "source_token_account": f"source-{signature}",
        "destination_token_account": f"destination-{signature}",
        "amount_raw": amount,
    }


def report_fixture():
    sale_signature = "SaleSignature1111111111111111111111111111111111111111111111111111"
    buy_signature = "BuySignature11111111111111111111111111111111111111111111111111111"
    relation_signature = "RelationSignature111111111111111111111111111111111111111111111111"
    return {
        "schema": "jeet-analyzer.cluster-audit.v1",
        "mint": MINT,
        "seed_wallet": ROOT,
        "token": {
            "mint": MINT, "symbol": "SECRET_SYMBOL", "name": "Secret Token Name",
            "decimals": 6, "supply": 1_000_000_000, "price_usd": None,
            "metadata_source": "https://identifying.invalid/metadata.json", "token_program": TOKEN_PROGRAM,
        },
        "window": {"start": 1, "end": 999},
        "transaction_autopsies": [
            autopsy(sale_signature, 1, -100),
            autopsy(buy_signature, 2, 25, transfer=transfer(buy_signature, 2, POOL, ROOT, 25)),
        ],
        "seed_wallet_accounting": {
            "current_target_token_balance_raw": 25,
            "lifecycle": {"material_threshold_raw": 1},
            "inventory_increases": {"events": [{"signature": buy_signature, "classification": "CONFIRMED_BUY"}]},
        },
        "buys_reacquisitions": {"events": [{"signature": buy_signature, "classification": "CONFIRMED_BUY"}]},
        "proceeds": {"transactions": [{"signature": sale_signature}]},
        "provider_coverage": {
            "complete": False, "seed_wallet_complete": True, "downstream_complete": False,
            "seed_wallet_history": {"complete": True, "scope": "mock complete seed history", "limitation": "none"},
        },
        "request_telemetry": {"terminated_by_budget": "max_transactions"},
        "wallet_relationships": [{
            "source": ROOT, "destination": RELATED, "relationship_type": "PLAIN_DIRECT_SOL_TRANSFER",
            "relationship_context": "DIRECT_SIGNED_SOL_FUNDING", "asset": "SOL", "amount_raw": 1_000_000,
            "block_time": 103, "timestamp": "2026-01-01T00:03:00Z", "signature": relation_signature,
            "source_signed": True, "source_paid_fee": True, "market_context": False,
        }],
        "related_target_token_inventory": [{
            "wallet": RELATED, "depth": 1, "current_target_token_balance_raw": 0,
            "relationship_path": [ROOT, RELATED],
        }],
        "cluster_status": "NOT_OUT",
        "common_control": "NOT_PROVEN",
        "unresolved_evidence": [{"kind": "PROVIDER_BUDGET_EXHAUSTED"}],
    }


class CaseExportTests(unittest.TestCase):
    def test_offline_replay_excludes_market_settlement_and_uses_zero_provider_calls(self):
        replay = replay_cluster_receipt(report_fixture())
        self.assertEqual(replay["provider_calls_during_replay"], 0)
        self.assertEqual(replay["historical_transactions_reacquired"], 0)
        self.assertEqual(replay["lifecycle"]["reconstructed_starting_inventory_raw"], 100)
        self.assertEqual(replay["lifecycle"]["current_wallet_status"], "RE_ENTERED")
        self.assertEqual(replay["verification"]["incoming_transfers"]["count"], 0)
        self.assertEqual(replay["verification"]["excluded_market_settlement_transfers"]["count"], 1)

    def test_public_export_is_allowlisted_aliased_and_does_not_mutate_internal_evidence(self):
        source = report_fixture()
        before = deepcopy(source)
        replay = replay_cluster_receipt(source)
        public = build_public_case(replay, estimated_provider_credits=120, credit_ceiling=500)
        text = json.dumps(public, sort_keys=True)
        for forbidden in (MINT, ROOT, POOL, RELATED, "SECRET_SYMBOL", "Secret Token Name", "https://"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(source, before)
        self.assertEqual(public["token"], "[REDACTED TOKEN]")
        self.assertEqual(public["primary_seller"], "Wallet A")
        self.assertEqual(public["relationship_evidence"][0]["source"], "Wallet A")
        self.assertEqual(public["relationship_evidence"][0]["destination"], "Wallet B")
        self.assertEqual(public["relationship_evidence"][0]["relationship_type"], "PLAIN_DIRECT_SOL_TRANSFER")
        self.assertEqual(public["verdict"]["common_control"], "NOT_PROVEN")
        self.assertTrue(all(row["transaction"].startswith("TX-") for row in public["evidence_timeline"]))


if __name__ == "__main__":
    unittest.main()
