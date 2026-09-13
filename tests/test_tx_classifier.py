from __future__ import annotations

import unittest

from tests.fixtures.builders import (
    OTHER_MINT,
    POOL,
    ROOT,
    ROUTER,
    WALLET_B,
    sale_tx,
    sol_transfer_tx,
    token_transfer_tx,
)
from jeet_analyzer import graph, tx_classifier


class TransactionClassifierTests(unittest.TestCase):
    def test_inner_token_2022_transfer_beats_outer_positional_accounts(self):
        transaction = token_transfer_tx(
            "inner-authoritative",
            outer_program="StreamLikeProgram11111111111111111111111111",
            inner=True,
        )
        autopsy = tx_classifier.autopsy_transaction(transaction)
        self.assertIsNotNone(autopsy)
        transfer = autopsy.token_transfers[0]
        self.assertEqual(transfer.destination_owner, WALLET_B)
        self.assertTrue(transfer.destination_token_account.startswith("DestinationToken"))
        self.assertNotIn("WrongPositionalDestination", transfer.destination_token_account)
        self.assertEqual(transfer.authority, ROOT)
        self.assertTrue(transfer.inner)

    def test_legacy_and_token_2022_programs_are_discovered_from_instructions(self):
        legacy = tx_classifier.autopsy_transaction(
            token_transfer_tx("legacy", token_program=tx_classifier.TOKEN_PROGRAM)
        )
        token_2022 = tx_classifier.autopsy_transaction(
            token_transfer_tx("token-2022", token_program=tx_classifier.TOKEN_2022_PROGRAM)
        )
        self.assertEqual(legacy.token_transfers[0].token_program, tx_classifier.TOKEN_PROGRAM)
        self.assertEqual(token_2022.token_transfers[0].token_program, tx_classifier.TOKEN_2022_PROGRAM)

    def test_token_account_authorities_resolve_from_balance_owners(self):
        autopsy = tx_classifier.autopsy_transaction(token_transfer_tx("authority-resolution"))
        transfer = autopsy.token_transfers[0]
        self.assertEqual(transfer.source_owner, ROOT)
        self.assertEqual(transfer.destination_owner, WALLET_B)
        self.assertNotEqual(transfer.source_owner, transfer.source_token_account)
        self.assertNotEqual(transfer.destination_owner, transfer.destination_token_account)

    def test_token_2022_extension_net_amount_does_not_override_parsed_amount(self):
        transaction = token_transfer_tx("fee-extension", amount=100)
        transaction["meta"]["postTokenBalances"][1]["uiTokenAmount"]["amount"] = "98"
        autopsy = tx_classifier.autopsy_transaction(transaction)
        transfer = autopsy.token_transfers[0]
        self.assertEqual(transfer.amount_raw, 100)
        self.assertEqual(transfer.destination_delta_raw, 98)

    def test_ata_creation_and_rent_are_separate_from_asset_transfer(self):
        autopsy = tx_classifier.autopsy_transaction(token_transfer_tx("ata", create_ata=True))
        classifications = {ROOT: "WALLET_LIKE", WALLET_B: "WALLET_LIKE"}
        evidence = graph.extract_relationships([autopsy], classifications)
        self.assertEqual(len(evidence.edges), 1)
        self.assertEqual(evidence.edges[0].relationship_type, "CONFIRMED_DIRECT_LINK")
        self.assertEqual(len(evidence.ata_rent), 1)
        self.assertEqual(evidence.ata_rent[0]["classification"], "ATA_RENT")
        self.assertFalse(evidence.ata_rent[0]["creates_graph_edge"])

    def test_ata_creation_cpi_rent_is_classified_without_parsed_system_transfer(self):
        transaction = token_transfer_tx("ata-cpi", create_ata=True)
        transaction["transaction"]["message"]["instructions"] = [
            instruction
            for instruction in transaction["transaction"]["message"]["instructions"]
            if instruction.get("programId") != tx_classifier.SYSTEM_PROGRAM
        ]
        autopsy = tx_classifier.autopsy_transaction(transaction)
        evidence = graph.extract_relationships(
            [autopsy], {ROOT: "WALLET_LIKE", WALLET_B: "WALLET_LIKE"}
        )
        self.assertEqual(len(evidence.ata_rent), 1)
        self.assertEqual(evidence.ata_rent[0]["amount_raw"], 2_039_280)

    def test_clean_system_transfer_is_plain_direct_sol(self):
        autopsy = tx_classifier.autopsy_transaction(
            sol_transfer_tx("plain-sol", source=ROOT, destination=WALLET_B)
        )
        evidence = graph.extract_relationships(
            [autopsy], {ROOT: "WALLET_LIKE", WALLET_B: "WALLET_LIKE"}
        )
        self.assertEqual(evidence.edges[0].relationship_type, "PLAIN_DIRECT_SOL_TRANSFER")
        self.assertEqual(evidence.edges[0].classification, "DIRECT_SIGNED_SOL_FUNDING")
        self.assertEqual(evidence.edges[0].relationship_context, "DIRECT_SIGNED_SOL_FUNDING")
        self.assertTrue(evidence.edges[0].source_signed)
        self.assertTrue(evidence.edges[0].source_paid_fee)
        self.assertFalse(evidence.edges[0].market_context)
        self.assertEqual(evidence.edges[0].common_control, "NOT_PROVEN")
        self.assertEqual(evidence.shared_funders, [])

    def test_router_transaction_is_market_context(self):
        autopsy = tx_classifier.autopsy_transaction(sale_tx("router-sale"))
        self.assertTrue(autopsy.market_context)
        self.assertIn(ROUTER, autopsy.program_ids)
        self.assertTrue(any("market infrastructure" in reason for reason in autopsy.market_reasons))

    def test_exact_native_delta_keeps_fee_separate(self):
        autopsy = tx_classifier.autopsy_transaction(
            sale_tx("native-accounting", proceeds_lamports=200_000_000, fee_lamports=5_000)
        )
        self.assertEqual(autopsy.native_deltas[ROOT], 199_995_000)
        self.assertEqual(autopsy.fee_lamports, 5_000)

    def test_pool_or_program_address_does_not_become_wallet_edge(self):
        autopsy = tx_classifier.autopsy_transaction(
            token_transfer_tx("pool", destination_owner=POOL, outer_program=None)
        )
        evidence = graph.extract_relationships(
            [autopsy], {ROOT: "WALLET_LIKE", POOL: "PROGRAM_PDA_OR_POOL_CANDIDATE"}
        )
        self.assertEqual(evidence.edges, [])
        self.assertEqual(evidence.excluded_infrastructure[0]["classification"], "UNKNOWN_COUNTERPARTY")

    def test_swap_transaction_is_excluded_from_wallet_graph(self):
        autopsy = tx_classifier.autopsy_transaction(sale_tx("swap-exclusion"))
        evidence = graph.extract_relationships(
            [autopsy], {ROOT: "WALLET_LIKE", POOL: "WALLET_LIKE"}
        )
        self.assertEqual(evidence.edges, [])
        self.assertEqual(evidence.excluded_infrastructure[0]["classification"], "EXCLUDED_SWAP_INFRASTRUCTURE")


if __name__ == "__main__":
    unittest.main()
