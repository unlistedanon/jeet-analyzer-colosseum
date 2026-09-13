from __future__ import annotations

import gc
import weakref

from jeet_analyzer import graph
from jeet_analyzer.graph_accumulator import GraphEvidenceAccumulator, compact_graph_autopsy
from jeet_analyzer.models import AtaCreation, NativeTransfer, TokenTransfer, TransactionAutopsy


A = "WalletA11111111111111111111111111111111111"
B = "WalletB11111111111111111111111111111111111"
C = "WalletC11111111111111111111111111111111111"
ATA_B = "AtaB1111111111111111111111111111111111111"
MINT = "Mint1111111111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SYSTEM_PROGRAM = "11111111111111111111111111111111"


def _token(signature: str, source: str = A, destination: str = B, amount: int = 42) -> TokenTransfer:
    return TokenTransfer(
        signature=signature,
        timestamp="2026-09-04T20:00:00Z",
        block_time=1_800_000_000,
        mint=MINT,
        token_program=TOKEN_PROGRAM,
        instruction_type="transferChecked",
        source_token_account=f"source-{signature}",
        destination_token_account=f"destination-{signature}",
        source_owner=source,
        destination_owner=destination,
        authority=source,
        amount_raw=amount,
        source_delta_raw=-amount,
        destination_delta_raw=amount,
        decimals=6,
        inner=False,
    )


def _autopsy(
    signature: str,
    *,
    signers: list[str] | None = None,
    token_transfers: list[TokenTransfer] | None = None,
    native_transfers: list[NativeTransfer] | None = None,
    ata_creations: list[AtaCreation] | None = None,
    market_context: bool = False,
    unknown_program_ids: list[str] | None = None,
) -> TransactionAutopsy:
    ata_creations = list(ata_creations or [])
    native_deltas = {creation.token_account: 2_039_280 for creation in ata_creations}
    return TransactionAutopsy(
        signature=signature,
        timestamp="2026-09-04T20:00:00Z",
        block_time=1_800_000_000,
        fee_payer=(signers or [A])[0],
        signers=list(signers or [A]),
        program_ids=[SYSTEM_PROGRAM, TOKEN_PROGRAM],
        unknown_program_ids=list(unknown_program_ids or []),
        instruction_summaries=[
            {
                "program_id": TOKEN_PROGRAM,
                "parsed_type": "transferChecked",
                "payload": "x" * 2048,
            }
        ],
        token_transfers=list(token_transfers or []),
        native_transfers=list(native_transfers or []),
        ata_creations=ata_creations,
        token_owner_deltas={A: {MINT: (-42, 6)}, B: {MINT: (42, 6)}},
        native_deltas=native_deltas,
        fee_lamports=5_000,
        market_context=market_context,
        market_reasons=["synthetic market context"] if market_context else [],
        failed=False,
    )


def _native(signature: str, source: str, destination: str, lamports: int = 10_000_000) -> NativeTransfer:
    return NativeTransfer(
        signature=signature,
        timestamp="2026-09-04T20:00:00Z",
        block_time=1_800_000_000,
        source=source,
        destination=destination,
        lamports=lamports,
        source_is_signer=True,
        source_is_fee_payer=True,
    )


def _snapshot(evidence: graph.GraphEvidence) -> dict:
    return {
        "edges": [edge.to_record() for edge in evidence.edges],
        "excluded_infrastructure": evidence.excluded_infrastructure,
        "ata_rent": evidence.ata_rent,
        "unresolved_relationships": evidence.unresolved_relationships,
        "shared_funders": evidence.shared_funders,
    }


def test_compact_graph_autopsy_drops_parse_only_payload() -> None:
    creation = AtaCreation(
        signature="compact",
        timestamp="2026-09-04T20:00:00Z",
        block_time=1_800_000_000,
        payer=A,
        owner=B,
        token_account=ATA_B,
        mint=MINT,
    )
    full = _autopsy("compact", token_transfers=[_token("compact")], ata_creations=[creation])
    full.native_deltas["unrelated-system-account"] = 999999

    compact = compact_graph_autopsy(full)

    assert not hasattr(compact, "instruction_summaries")
    assert not hasattr(compact, "token_owner_deltas")
    assert not hasattr(compact, "fee_lamports")
    assert set(compact.native_deltas) == {ATA_B}
    assert compact.token_transfers[0].amount_raw == 42
    assert not hasattr(compact.token_transfers[0], "source_delta_raw")
    assert not hasattr(compact.token_transfers[0], "decimals")


def test_accumulator_does_not_retain_full_autopsy_objects() -> None:
    accumulator = GraphEvidenceAccumulator()
    references: list[weakref.ReferenceType[TransactionAutopsy]] = []

    for index in range(2_000):
        signature = f"stream-{index}"
        autopsy = _autopsy(signature, token_transfers=[_token(signature)])
        references.append(weakref.ref(autopsy))
        assert accumulator.ingest(autopsy)
    del autopsy
    gc.collect()

    assert len(accumulator) == 2_000
    assert all(reference() is None for reference in references)
    assert len(accumulator.candidates_for(A)) == 1
    assert B in accumulator.candidates_for(A)


def test_candidate_index_matches_legacy_candidate_discovery() -> None:
    direct = _autopsy("direct", signers=[A, C], token_transfers=[_token("direct")])
    native = _autopsy("native", native_transfers=[_native("native", B, C)])
    creation = AtaCreation(
        signature="ata",
        timestamp="2026-09-04T20:00:00Z",
        block_time=1_800_000_000,
        payer=B,
        owner=C,
        token_account=ATA_B,
        mint=MINT,
    )
    ata = _autopsy("ata", signers=[B], ata_creations=[creation])
    market = _autopsy(
        "market",
        signers=[A, C],
        token_transfers=[_token("market", source=A, destination=B)],
        market_context=True,
    )
    autopsies = [direct, native, ata, market]

    accumulator = GraphEvidenceAccumulator()
    accumulator.extend(autopsies)

    for wallet in (A, B, C):
        legacy = {
            candidate
            for autopsy in autopsies
            for candidate in graph.candidate_addresses(autopsy, wallet)
        }
        assert accumulator.candidates_for(wallet) == legacy


def test_compact_evidence_matches_legacy_relationship_engine() -> None:
    direct = _autopsy("direct", token_transfers=[_token("direct")])
    later = _autopsy("later", signers=[B])
    fund_a = _autopsy("fund-a", signers=[C], native_transfers=[_native("fund-a", C, A)])
    fund_b = _autopsy("fund-b", signers=[C], native_transfers=[_native("fund-b", C, B)])
    market = _autopsy(
        "market",
        signers=[A],
        token_transfers=[_token("market", source=A, destination=B)],
        market_context=True,
    )
    autopsies = [direct, later, fund_a, fund_b, market]
    classifications = {A: "WALLET_LIKE", B: "WALLET_LIKE", C: "WALLET_LIKE"}

    accumulator = GraphEvidenceAccumulator()
    accumulator.extend(autopsies)

    legacy = graph.extract_relationships(autopsies, classifications)
    compact = accumulator.evidence(classifications)

    assert _snapshot(compact) == _snapshot(legacy)


def test_late_wallet_classification_preserves_legacy_semantics() -> None:
    autopsy = _autopsy("late", token_transfers=[_token("late")])
    accumulator = GraphEvidenceAccumulator()
    accumulator.ingest(autopsy)

    first_classifications = {A: "WALLET_LIKE"}
    assert _snapshot(accumulator.evidence(first_classifications)) == _snapshot(
        graph.extract_relationships([autopsy], first_classifications)
    )
    assert accumulator.evidence(first_classifications).edges == []

    later_classifications = {A: "WALLET_LIKE", B: "WALLET_LIKE"}
    compact = accumulator.evidence(later_classifications)
    legacy = graph.extract_relationships([autopsy], later_classifications)

    assert _snapshot(compact) == _snapshot(legacy)
    assert len(compact.edges) == 1
    assert compact.edges[0].relationship_type == "DIRECT_TOKEN_TRANSFER"


def test_later_distinct_signature_updates_recipient_independence() -> None:
    transfer = _autopsy("transfer", signers=[A], token_transfers=[_token("transfer")])
    later = _autopsy("recipient-later", signers=[B])
    classifications = {A: "WALLET_LIKE", B: "WALLET_LIKE"}

    accumulator = GraphEvidenceAccumulator()
    accumulator.ingest(transfer)
    first = accumulator.evidence(classifications)
    first_edge = next(edge for edge in first.edges if edge.relationship_type == "DIRECT_TOKEN_TRANSFER")
    assert first_edge.recipient_independently_signed is False

    accumulator.ingest(later)
    final = accumulator.evidence(classifications)
    legacy = graph.extract_relationships([transfer, later], classifications)
    final_edge = next(edge for edge in final.edges if edge.relationship_type == "DIRECT_TOKEN_TRANSFER")

    assert final_edge.recipient_independently_signed is True
    assert _snapshot(final) == _snapshot(legacy)



def test_accumulator_spools_graph_facts_instead_of_retaining_python_autopsy_corpus() -> None:
    accumulator = GraphEvidenceAccumulator()
    for index in range(2_000):
        signature = f"spool-{index}"
        assert accumulator.ingest(_autopsy(signature, token_transfers=[_token(signature)]))

    telemetry = accumulator.telemetry()
    assert len(accumulator) == 2_000
    assert accumulator.spooled_autopsies == 2_000
    assert accumulator.storage_bytes > 0
    assert telemetry["storage"] == "compressed_temporary_spool"
    assert telemetry["autopsies_observed"] == 2_000
    assert not hasattr(accumulator, "_autopsies")


def test_accumulator_bounds_only_exclusion_noise_and_preserves_exact_total() -> None:
    autopsies = [
        _autopsy(
            f"market-{index}",
            signers=[A],
            token_transfers=[_token(f"market-{index}", source=A, destination=B)],
            market_context=True,
        )
        for index in range(25)
    ]
    classifications = {A: "WALLET_LIKE", B: "WALLET_LIKE"}
    accumulator = GraphEvidenceAccumulator(exclusion_sample_limit=5)
    accumulator.extend(autopsies)

    legacy = graph.extract_relationships(autopsies, classifications)
    bounded = accumulator.evidence(classifications)

    assert len(legacy.excluded_infrastructure) == 25
    assert legacy.excluded_infrastructure_total == 25
    assert len(bounded.excluded_infrastructure) == 5
    assert bounded.excluded_infrastructure_total == 25
    assert bounded.edges == legacy.edges == []
    assert bounded.unresolved_relationships == legacy.unresolved_relationships == []


def test_spool_can_render_then_accept_more_evidence_without_losing_late_semantics() -> None:
    accumulator = GraphEvidenceAccumulator()
    first = _autopsy("first-spooled", token_transfers=[_token("first-spooled")])
    later = _autopsy("later-spooled", signers=[B])
    classifications = {A: "WALLET_LIKE", B: "WALLET_LIKE"}

    accumulator.ingest(first)
    initial = accumulator.evidence(classifications)
    assert len(initial.edges) == 1
    assert initial.edges[0].recipient_independently_signed is False

    accumulator.ingest(later)
    final = accumulator.evidence(classifications)
    edge = next(row for row in final.edges if row.relationship_type == "DIRECT_TOKEN_TRANSFER")
    assert edge.recipient_independently_signed is True



def test_accumulator_bounds_unresolved_rows_but_preserves_exact_truth() -> None:
    autopsies = [
        _autopsy(
            f"unresolved-{index}",
            signers=[C],
            token_transfers=[_token(f"unresolved-{index}", source=A, destination=B)],
        )
        for index in range(25)
    ]
    classifications = {A: "WALLET_LIKE", B: "WALLET_LIKE", C: "WALLET_LIKE"}
    accumulator = GraphEvidenceAccumulator(unresolved_sample_limit=5)
    accumulator.extend(autopsies)

    legacy = graph.extract_relationships(autopsies, classifications)
    bounded = accumulator.evidence(classifications)

    assert len(legacy.unresolved_relationships) == 25
    assert legacy.unresolved_relationships_total == 25
    assert len(bounded.unresolved_relationships) == 5
    assert bounded.unresolved_relationships_total == 25
    assert bounded.unresolved_relationship_counts_by_classification == {
        "UNKNOWN_COUNTERPARTY": 25
    }
    assert sum(bounded.unresolved_relationship_counts_by_reason.values()) == 25
    assert bounded.edges == legacy.edges == []
