from __future__ import annotations

from jeet_analyzer.report_contract import apply_result_contract


WALLET_A = "WalletA11111111111111111111111111111111111"
WALLET_B = "WalletB11111111111111111111111111111111111"


def _edge(relationship_type: str, signature: str, classification: str) -> dict:
    return {
        "source": WALLET_A,
        "destination": WALLET_B,
        "relationship_type": relationship_type,
        "classification": classification,
        "signature": signature,
        "timestamp": "2027-01-15T08:00:00Z",
        "block_time": 1_800_000_000 if signature == "sig-control" else 1_800_000_100,
        "asset": "SIGNATURE" if relationship_type == "NON_MARKET_CO_SIGNER" else "TargetMint",
        "amount_raw": 0 if relationship_type == "NON_MARKET_CO_SIGNER" else 1_000_000,
        "reason": f"evidence for {relationship_type}",
        "why_linked": f"evidence for {relationship_type}",
        "common_control": "NOT_PROVEN",
    }


def test_result_contract_surfaces_likely_control_without_claiming_proof() -> None:
    report = {
        "wallet_graph": {
            "nodes": [],
            "edges": [
                _edge("NON_MARKET_CO_SIGNER", "sig-control", "COORDINATED_BEHAVIOR"),
                _edge("DIRECT_TOKEN_TRANSFER", "sig-resource", "DIRECT_TOKEN_TRANSFER"),
            ],
            "shared_funders": [],
            "common_control": "NOT_PROVEN",
        },
        "seed_wallet_accounting": {},
        "unresolved_relationships": [],
    }

    result = apply_result_contract(report)

    assert result["common_control"] == "NOT_PROVEN"
    assert result["control_summary"] == {
        "pairs_assessed": 1,
        "likely_common_control_pairs": 1,
        "strongly_linked_pairs": 1,
        "proof_status": "NOT_PROVEN",
    }
    assert result["control_assessments"] == result["wallet_graph"]["control_assessments"]
    assessment = result["control_assessments"][0]
    assert assessment["control_assessment"] == "LIKELY_COMMON_CONTROL"
    assert assessment["link_status"] == "STRONGLY_LINKED"
    assert assessment["common_control"] == "NOT_PROVEN"
    assert assessment["legal_identity"] == "NOT_ESTABLISHED"
    assert assessment["distinct_transaction_count"] == 2


def test_result_contract_emits_empty_control_summary_on_failure_shape() -> None:
    result = apply_result_contract(
        {
            "wallet_graph": {
                "nodes": [],
                "edges": [],
                "shared_funders": [],
                "common_control": "NOT_PROVEN",
            },
            "seed_wallet_accounting": {},
            "unresolved_relationships": [],
        }
    )

    assert result["control_assessments"] == []
    assert result["control_summary"]["pairs_assessed"] == 0
    assert result["control_summary"]["likely_common_control_pairs"] == 0
    assert result["common_control"] == "NOT_PROVEN"
