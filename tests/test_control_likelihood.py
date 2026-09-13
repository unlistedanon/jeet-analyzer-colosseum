from __future__ import annotations

from jeet_analyzer.control import assess_control_likelihood
from jeet_analyzer.models import RelationshipEdge


WALLET_A = "WalletA11111111111111111111111111111111111"
WALLET_B = "WalletB11111111111111111111111111111111111"


def _edge(
    relationship_type: str,
    signature: str,
    *,
    source: str = WALLET_A,
    destination: str = WALLET_B,
    classification: str | None = None,
    block_time: int = 1_800_000_000,
) -> RelationshipEdge:
    return RelationshipEdge(
        source=source,
        destination=destination,
        relationship_type=relationship_type,
        classification=classification or relationship_type,
        signature=signature,
        timestamp="2027-01-15T08:00:00Z",
        block_time=block_time,
        asset="SOL" if relationship_type == "PLAIN_DIRECT_SOL_TRANSFER" else "TargetMint",
        amount_raw=1_000_000,
        signers=[source],
        fee_payer=source,
        reason=f"test evidence for {relationship_type}",
        confidence="HIGH",
        market_context=False,
        common_control="NOT_PROVEN",
    )


def test_single_cosign_reports_coordination_but_not_likely_control() -> None:
    rows = assess_control_likelihood(
        [_edge("NON_MARKET_CO_SIGNER", "sig-cosign", classification="COORDINATED_BEHAVIOR")]
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["link_status"] == "LINKED"
    assert row["control_assessment"] == "COORDINATED_BEHAVIOR"
    assert row["common_control"] == "NOT_PROVEN"
    assert row["distinct_transaction_count"] == 1


def test_composite_ata_transfer_is_one_signal_not_two() -> None:
    row = assess_control_likelihood(
        [_edge("CONFIRMED_DIRECT_LINK", "sig-composite")]
    )[0]
    assert row["independent_evidence_classes"] == ["ATA_TOKEN_COMPOSITE"]
    assert row["independent_signal_count"] == 1
    assert row["control_assessment"] == "NOT_ESTABLISHED"


def test_cosign_plus_token_flow_across_separate_transactions_is_likely_common_control() -> None:
    row = assess_control_likelihood(
        [
            _edge("NON_MARKET_CO_SIGNER", "sig-cosign", classification="COORDINATED_BEHAVIOR"),
            _edge("DIRECT_TOKEN_TRANSFER", "sig-token", block_time=1_800_000_100),
        ]
    )[0]
    assert row["link_status"] == "STRONGLY_LINKED"
    assert row["control_assessment"] == "LIKELY_COMMON_CONTROL"
    assert row["common_control"] == "NOT_PROVEN"
    assert row["legal_identity"] == "NOT_ESTABLISHED"
    assert row["independent_signal_count"] == 2
    assert row["distinct_transaction_count"] == 2
    assert row["has_control_signal"] is True
    assert row["has_resource_signal"] is True


def test_ata_preparation_plus_direct_sol_funding_is_likely_common_control() -> None:
    row = assess_control_likelihood(
        [
            _edge("ATA_PREPARATION", "sig-ata", classification="DIRECTLY_LINKED"),
            _edge(
                "PLAIN_DIRECT_SOL_TRANSFER",
                "sig-funding",
                classification="DIRECT_SIGNED_SOL_FUNDING",
                block_time=1_800_000_100,
            ),
        ]
    )[0]
    assert row["control_assessment"] == "LIKELY_COMMON_CONTROL"
    assert set(row["independent_evidence_classes"]) == {"ATA_PREPARATION", "SOL_FUNDING"}


def test_multiple_classes_in_same_transaction_do_not_become_likely_control() -> None:
    row = assess_control_likelihood(
        [
            _edge("NON_MARKET_CO_SIGNER", "sig-same", classification="COORDINATED_BEHAVIOR"),
            _edge("DIRECT_TOKEN_TRANSFER", "sig-same"),
        ]
    )[0]
    assert row["independent_signal_count"] == 2
    assert row["distinct_transaction_count"] == 1
    assert row["control_assessment"] == "COORDINATED_BEHAVIOR"


def test_repeated_token_transfers_alone_do_not_become_likely_control() -> None:
    row = assess_control_likelihood(
        [
            _edge("DIRECT_TOKEN_TRANSFER", "sig-token-1"),
            _edge("DIRECT_TOKEN_TRANSFER", "sig-token-2", block_time=1_800_000_100),
        ]
    )[0]
    assert row["independent_signal_count"] == 1
    assert row["distinct_transaction_count"] == 2
    assert row["control_assessment"] == "NOT_ESTABLISHED"


def test_pair_aggregation_is_direction_independent() -> None:
    rows = assess_control_likelihood(
        [
            _edge("ATA_PREPARATION", "sig-ata", classification="DIRECTLY_LINKED"),
            _edge(
                "DIRECT_TOKEN_TRANSFER",
                "sig-token",
                source=WALLET_B,
                destination=WALLET_A,
                block_time=1_800_000_100,
            ),
        ]
    )
    assert len(rows) == 1
    row = rows[0]
    assert {row["wallet_a"], row["wallet_b"]} == {WALLET_A, WALLET_B}
    assert row["control_assessment"] == "LIKELY_COMMON_CONTROL"
