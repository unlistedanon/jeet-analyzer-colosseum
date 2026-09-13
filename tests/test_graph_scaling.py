from __future__ import annotations

from jeet_analyzer import graph
from jeet_analyzer.models import TokenTransfer, TransactionAutopsy


SOURCE = "SourceWallet1111111111111111111111111111111"
DESTINATION = "DestinationWallet111111111111111111111111111"
MINT = "Mint111111111111111111111111111111111111111"


def _autopsy(signature: str, *, signers: list[str], transfer: bool = False) -> TransactionAutopsy:
    autopsy = TransactionAutopsy(
        signature=signature,
        timestamp="2026-09-03T18:00:00Z",
        block_time=1_800_000_000,
        fee_payer=signers[0] if signers else None,
        signers=signers,
        program_ids=[],
        unknown_program_ids=[],
    )
    if transfer:
        autopsy.token_transfers.append(
            TokenTransfer(
                signature=signature,
                timestamp=autopsy.timestamp,
                block_time=autopsy.block_time,
                mint=MINT,
                token_program="TokenProgram111111111111111111111111111111",
                instruction_type="transferChecked",
                source_token_account="SourceAta11111111111111111111111111111111",
                destination_token_account="DestinationAta1111111111111111111111111111",
                source_owner=SOURCE,
                destination_owner=DESTINATION,
                authority=SOURCE,
                amount_raw=100,
                source_delta_raw=-100,
                destination_delta_raw=100,
                decimals=6,
                inner=False,
            )
        )
    return autopsy


def _classifications() -> dict[str, str]:
    return {SOURCE: "WALLET_LIKE", DESTINATION: "WALLET_LIKE"}


def test_recipient_independently_signed_requires_a_distinct_signature() -> None:
    transfer = _autopsy("transfer-signature", signers=[SOURCE, DESTINATION], transfer=True)

    evidence = graph.extract_relationships([transfer], _classifications())
    token_edge = next(edge for edge in evidence.edges if edge.relationship_type == "DIRECT_TOKEN_TRANSFER")
    assert token_edge.recipient_independently_signed is False

    later_signature = _autopsy("destination-later-signature", signers=[DESTINATION])
    evidence = graph.extract_relationships([transfer, later_signature], _classifications())
    token_edge = next(edge for edge in evidence.edges if edge.relationship_type == "DIRECT_TOKEN_TRANSFER")
    assert token_edge.recipient_independently_signed is True


def test_signer_activity_index_answers_exactly_with_two_signature_slots() -> None:
    index = graph.SignerActivityIndex()
    index.observe(_autopsy("sig-a", signers=[DESTINATION]))
    assert index.has_other_signature(DESTINATION, "sig-a") is False

    index.observe(_autopsy("sig-b", signers=[DESTINATION]))
    index.observe(_autopsy("sig-c", signers=[DESTINATION]))

    assert index.has_other_signature(DESTINATION, "sig-a") is True
    assert index.has_other_signature(DESTINATION, "sig-b") is True
    assert index.has_other_signature(DESTINATION, "sig-c") is True
    assert index.signatures[DESTINATION] == ("sig-a", "sig-b")


def test_large_repeated_signer_history_keeps_constant_signature_state() -> None:
    autopsies = [
        _autopsy(f"history-{index}", signers=[DESTINATION])
        for index in range(10_000)
    ]

    signer_index = graph.SignerActivityIndex.from_autopsies(autopsies)

    assert len(signer_index.signatures) == 1
    assert signer_index.signatures[DESTINATION] == ("history-0", "history-1")
    assert signer_index.has_other_signature(DESTINATION, "history-9999") is True
