from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from jeet_analyzer import cluster, graph, history, tx_classifier
from jeet_analyzer.analyzer import build_parser
from jeet_analyzer_api.models import ClusterAuditRequest
from jeet_analyzer.graph_accumulator import GraphEvidenceAccumulator
from tests.fixtures.builders import (
    POOL,
    ROOT,
    ROUTER,
    TARGET_MINT,
    WALLET_B,
    WALLET_C,
    account_record,
    sale_tx,
    token_transfer_tx,
)


SYSTEM_PROGRAM = tx_classifier.SYSTEM_PROGRAM
ASSOCIATED_TOKEN_PROGRAM = tx_classifier.ASSOCIATED_TOKEN_PROGRAM


def _add_wallet_signer(transaction: dict, wallet: str) -> dict:
    tx = deepcopy(transaction)
    tx["transaction"]["message"]["accountKeys"].append(
        {"pubkey": wallet, "signer": True}
    )
    tx["meta"]["preBalances"].append(1_000_000)
    tx["meta"]["postBalances"].append(1_000_000)
    return tx


def _ata_preparation_only_tx(signature: str = "ata-preparation-only") -> dict:
    token_account = "PreparedAta111111111111111111111111111111111"
    rent = 2_039_280
    fee = 5_000
    return {
        "blockTime": 1_800_000_350,
        "transaction": {
            "signatures": [signature],
            "message": {
                "accountKeys": [
                    {"pubkey": ROOT, "signer": True},
                    {"pubkey": WALLET_B, "signer": False},
                    {"pubkey": token_account, "signer": False},
                    {"pubkey": TARGET_MINT, "signer": False},
                    {"pubkey": ASSOCIATED_TOKEN_PROGRAM, "signer": False},
                    {"pubkey": SYSTEM_PROGRAM, "signer": False},
                ],
                "instructions": [
                    {
                        "programId": ASSOCIATED_TOKEN_PROGRAM,
                        "parsed": {
                            "type": "createIdempotent",
                            "info": {
                                "payer": ROOT,
                                "wallet": WALLET_B,
                                "account": token_account,
                                "mint": TARGET_MINT,
                            },
                        },
                    },
                    {
                        "programId": SYSTEM_PROGRAM,
                        "parsed": {
                            "type": "transfer",
                            "info": {
                                "source": ROOT,
                                "destination": token_account,
                                "lamports": rent,
                            },
                        },
                    },
                ],
            },
        },
        "meta": {
            "err": None,
            "fee": fee,
            "preBalances": [1_000_000_000, 1_000_000, 0, 0, 0, 0],
            "postBalances": [
                1_000_000_000 - rent - fee,
                1_000_000,
                rent,
                0,
                0,
                0,
            ],
            "preTokenBalances": [],
            "postTokenBalances": [],
            "innerInstructions": [],
            "logMessages": [],
        },
    }


def _wallet_classifications(*wallets: str) -> dict[str, str]:
    # Match the real RPC account classifier outcome for ordinary system-owned,
    # non-executable wallet accounts without adding provider behavior to these
    # graph-unit regressions.
    assert all(account_record()["owner"] == SYSTEM_PROGRAM for _ in wallets)
    return {wallet: "WALLET_LIKE" for wallet in wallets}


def _delegated_transfer_tx(signature: str = "delegated-authority", *, mint: str = TARGET_MINT) -> dict:
    transaction = token_transfer_tx(
        signature,
        source_owner=ROOT,
        destination_owner=WALLET_C,
        mint=mint,
        inner=False,
    )
    keys = transaction["transaction"]["message"]["accountKeys"]
    keys[0]["signer"] = False
    keys.append({"pubkey": WALLET_B, "signer": True})
    transaction["meta"]["preBalances"].append(1_000_000)
    transaction["meta"]["postBalances"].append(1_000_000)
    instruction = transaction["transaction"]["message"]["instructions"][-1]
    instruction["parsed"]["info"]["authority"] = WALLET_B
    return transaction


def test_external_delegated_token_authority_is_discovered_and_accepted() -> None:
    autopsy = tx_classifier.autopsy_transaction(_delegated_transfer_tx())
    assert autopsy is not None
    assert WALLET_B in graph.candidate_addresses(autopsy, ROOT)

    accumulator = GraphEvidenceAccumulator()
    accumulator.ingest(autopsy)
    assert WALLET_B in accumulator.candidates_for(ROOT)

    evidence = graph.extract_relationships(
        [autopsy], _wallet_classifications(ROOT, WALLET_B, WALLET_C)
    )
    edge = next(row for row in evidence.edges if row.relationship_type == "DELEGATED_TOKEN_AUTHORITY")
    assert {edge.source, edge.destination} == {ROOT, WALLET_B}
    assert edge.asset == TARGET_MINT
    assert edge.source_token_account.startswith("SourceToken")
    assert edge.destination_token_account.startswith("DestinationToken")
    assert edge.classification == "DELEGATED_TOKEN_AUTHORITY"
    assert edge.relationship_context == "DELEGATED_TOKEN_AUTHORITY"
    assert edge.signers == [WALLET_B]
    assert "signed and moved target tokens" in edge.reason
    assert edge.to_record()["why_linked"] == edge.reason
    assert edge.common_control == "NOT_PROVEN"


def test_delegated_authority_does_not_require_source_owner_signature() -> None:
    autopsy = tx_classifier.autopsy_transaction(_delegated_transfer_tx("delegated-no-source-signature"))
    assert autopsy is not None
    assert ROOT not in autopsy.signers
    assert WALLET_B in autopsy.signers
    evidence = graph.extract_relationships(
        [autopsy], _wallet_classifications(ROOT, WALLET_B, WALLET_C)
    )
    assert any(row.relationship_type == "DELEGATED_TOKEN_AUTHORITY" for row in evidence.edges)


def test_owner_authority_does_not_create_delegated_edge() -> None:
    autopsy = tx_classifier.autopsy_transaction(token_transfer_tx("owner-authority"))
    assert autopsy is not None
    evidence = graph.extract_relationships(
        [autopsy], _wallet_classifications(ROOT, WALLET_B)
    )
    assert not any(row.relationship_type == "DELEGATED_TOKEN_AUTHORITY" for row in evidence.edges)


def test_market_context_blocks_delegated_authority_edge() -> None:
    transaction = _delegated_transfer_tx("delegated-market")
    transaction["transaction"]["message"]["instructions"].insert(
        0,
        {"programId": ROUTER, "accounts": [], "data": "opaque"},
    )
    transaction["meta"]["logMessages"] = ["Program log: Instruction: Swap"]
    autopsy = tx_classifier.autopsy_transaction(transaction)
    assert autopsy is not None and autopsy.market_context
    evidence = graph.extract_relationships(
        [autopsy], _wallet_classifications(ROOT, WALLET_B, WALLET_C)
    )
    assert WALLET_B not in graph.candidate_addresses(autopsy, ROOT)
    assert not any(row.relationship_type == "DELEGATED_TOKEN_AUTHORITY" for row in evidence.edges)


def test_non_wallet_delegated_authority_is_not_expanded() -> None:
    autopsy = tx_classifier.autopsy_transaction(_delegated_transfer_tx("delegated-pool"))
    assert autopsy is not None
    transfer = autopsy.token_transfers[0]
    autopsy.token_transfers[0] = transfer.__class__(
        **{**transfer.to_record(), "authority": POOL}
    )
    evidence = graph.extract_relationships(
        [autopsy],
        {ROOT: "WALLET_LIKE", WALLET_B: "WALLET_LIKE", WALLET_C: "WALLET_LIKE", POOL: "PROGRAM_PDA_OR_POOL_CANDIDATE"},
    )
    assert not any(row.relationship_type == "DELEGATED_TOKEN_AUTHORITY" for row in evidence.edges)
    assert any(row["classification"] == "DELEGATED_AUTHORITY_NOT_WALLET" for row in evidence.unresolved_relationships)


def test_delegated_authority_is_not_a_common_control_signal() -> None:
    autopsy = tx_classifier.autopsy_transaction(_delegated_transfer_tx("delegated-control"))
    assert autopsy is not None
    evidence = graph.extract_relationships(
        [autopsy], _wallet_classifications(ROOT, WALLET_B, WALLET_C)
    )
    from jeet_analyzer.control import assess_control_likelihood

    delegated = [
        edge for edge in evidence.edges
        if edge.relationship_type == "DELEGATED_TOKEN_AUTHORITY"
    ]
    assert len(delegated) == 1
    assert assess_control_likelihood(delegated) == []


def test_non_market_cosigner_is_discovered_and_labeled_as_coordination() -> None:
    transaction = _add_wallet_signer(
        token_transfer_tx(
            "hunt-cosigner",
            destination_owner=WALLET_C,
            block_time=1_800_000_300,
        ),
        WALLET_B,
    )
    autopsy = tx_classifier.autopsy_transaction(transaction)
    assert autopsy is not None
    assert autopsy.market_context is False

    candidates = graph.candidate_addresses(autopsy, ROOT)
    assert WALLET_B in candidates
    assert WALLET_C in candidates

    evidence = graph.extract_relationships(
        [autopsy], _wallet_classifications(ROOT, WALLET_B, WALLET_C)
    )
    edge = next(
        row for row in evidence.edges if row.relationship_type == "NON_MARKET_CO_SIGNER"
    )
    assert {edge.source, edge.destination} == {ROOT, WALLET_B}
    assert edge.classification == "COORDINATED_BEHAVIOR"
    assert edge.relationship_context == "NON_MARKET_CO_SIGNATURE"
    assert edge.market_context is False
    assert edge.common_control == "NOT_PROVEN"
    assert "signed the same non-market transaction" in edge.reason
    assert edge.to_record()["why_linked"] == edge.reason


def test_market_router_context_blocks_cosigner_hunt_expansion() -> None:
    transaction = _add_wallet_signer(sale_tx("market-cosigner"), WALLET_B)
    autopsy = tx_classifier.autopsy_transaction(transaction)
    assert autopsy is not None
    assert autopsy.market_context is True

    candidates = graph.candidate_addresses(autopsy, ROOT)
    assert WALLET_B not in candidates

    evidence = graph.extract_relationships(
        [autopsy], _wallet_classifications(ROOT, WALLET_B)
    )
    assert not any(
        row.relationship_type == "NON_MARKET_CO_SIGNER" for row in evidence.edges
    )


def test_ata_payer_owner_preparation_discovers_wallet_without_token_transfer() -> None:
    autopsy = tx_classifier.autopsy_transaction(_ata_preparation_only_tx())
    assert autopsy is not None
    assert autopsy.market_context is False
    assert len(autopsy.ata_creations) == 1

    assert WALLET_B in graph.candidate_addresses(autopsy, ROOT)

    evidence = graph.extract_relationships(
        [autopsy], _wallet_classifications(ROOT, WALLET_B)
    )
    edge = next(
        row for row in evidence.edges if row.relationship_type == "ATA_PREPARATION"
    )
    assert edge.source == ROOT
    assert edge.destination == WALLET_B
    assert edge.classification == "DIRECTLY_LINKED"
    assert edge.relationship_context == "ATA_PAYER_TO_OWNER"
    assert edge.source_signed is True
    assert edge.market_context is False
    assert edge.common_control == "NOT_PROVEN"
    assert "preparation link" in edge.reason
    assert edge.to_record()["why_linked"] == edge.reason


def test_ata_preparation_does_not_duplicate_stronger_same_tx_direct_link() -> None:
    autopsy = tx_classifier.autopsy_transaction(
        token_transfer_tx(
            "ata-direct-link",
            destination_owner=WALLET_B,
            create_ata=True,
            block_time=1_800_000_300,
        )
    )
    assert autopsy is not None

    evidence = graph.extract_relationships(
        [autopsy], _wallet_classifications(ROOT, WALLET_B)
    )
    relationship_types = [row.relationship_type for row in evidence.edges]
    assert "CONFIRMED_DIRECT_LINK" in relationship_types
    assert "ATA_PREPARATION" not in relationship_types


def test_deep_hunt_engine_accepts_six_and_rejects_seven() -> None:
    cluster.ClusterAuditConfig(graph_depth=6).validate()
    with pytest.raises(history.HistoryError):
        cluster.ClusterAuditConfig(graph_depth=7).validate()


def test_deep_hunt_api_accepts_six_while_default_remains_three() -> None:
    valid_address = "11111111111111111111111111111111"
    default_request = ClusterAuditRequest(mint=valid_address, wallet=valid_address)
    deep_request = ClusterAuditRequest(
        mint=valid_address,
        wallet=valid_address,
        graph_depth=6,
    )
    assert default_request.graph_depth == 3
    assert default_request.deep_forensic is False
    assert deep_request.graph_depth == 6
    assert deep_request.deep_forensic is False
    forensic_request = ClusterAuditRequest(
        mint=valid_address,
        wallet=valid_address,
        deep_forensic=True,
    )
    assert forensic_request.deep_forensic is True

    with pytest.raises(ValidationError):
        ClusterAuditRequest(
            mint=valid_address,
            wallet=valid_address,
            graph_depth=7,
        )

def test_deep_hunt_cli_accepts_six_while_default_remains_three() -> None:
    valid_address = "11111111111111111111111111111111"
    parser = build_parser()

    default_request = parser.parse_args(
        [
            "cluster-audit",
            "--mint",
            valid_address,
            "--wallet",
            valid_address,
        ]
    )
    deep_request = parser.parse_args(
        [
            "cluster-audit",
            "--mint",
            valid_address,
            "--wallet",
            valid_address,
            "--graph-depth",
            "6",
        ]
    )

    assert default_request.graph_depth == 3
    assert default_request.deep_forensic is False
    assert deep_request.graph_depth == 6
    assert deep_request.deep_forensic is False
    forensic_request = parser.parse_args(
        [
            "cluster-audit",
            "--mint",
            valid_address,
            "--wallet",
            valid_address,
            "--deep-forensic",
        ]
    )
    assert forensic_request.deep_forensic is True

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "cluster-audit",
                "--mint",
                valid_address,
                "--wallet",
                valid_address,
                "--graph-depth",
                "7",
            ]
        )
