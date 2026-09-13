from pathlib import Path

from jsonschema import Draft202012Validator

from jeet_analyzer.case_export import build_public_case
from tests.test_cluster import run_audit


def test_v1_schema_accepts_required_mint_enrichment_and_split_statuses():
    report, _provider, _rpc = run_audit(deep_forensic=False)
    enrichment = report["provider_coverage"]["optional_mint_enrichment"]
    assert enrichment["attempted"] is True
    assert enrichment["required_for_conclusion"] is True
    assert report["target_cluster_status"] == "VERIFIED_OUT"
    assert report["relationship_status"] == "RESOLVED_WITHIN_SCOPE"

    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "jeet-analyzer-result-v1.schema.json"
    schema = __import__("json").loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(report)


def test_public_safe_verdict_preserves_target_and_relationship_statuses():
    replay = {
        "seed_wallet": "RootWalletForPublicCase",
        "mint": "TargetMintForPublicCase",
        "token": {"decimals": 0},
        "relationships": [],
        "related_inventory": [],
        "lifecycle": {
            "historical_exit_status": "VERIFIED_OUT",
            "current_wallet_status": "VERIFIED_OUT",
            "reconstructed_starting_inventory_raw": 1000,
            "reacquisitions": {"events": [], "amount_raw": 0},
            "reconciled": True,
        },
        "verification": {
            "confirmed_sales": {"amount_raw": 1000},
            "fresh_current_inventory_raw": 0,
            "incoming_transfers": 0,
            "outgoing_transfers": 0,
            "burns": 0,
            "excluded_market_settlement_transfers": 0,
            "reconciliation": {"root_equation_balanced": True},
        },
        "root_events": [],
        "provider_coverage": {
            "complete": True,
            "seed_wallet_complete": True,
            "downstream_complete": True,
        },
        "source_request_telemetry": {},
        "provider_calls_during_replay": 0,
        "unresolved_evidence_count": 1,
        "target_cluster_status": "VERIFIED_OUT",
        "relationship_status": "UNRESOLVED",
        "cluster_status": "UNRESOLVED",
    }
    public = build_public_case(replay, estimated_provider_credits=100, credit_ceiling=60_000)
    assert public["verdict"]["target_cluster_status"] == "VERIFIED_OUT"
    assert public["verdict"]["relationship_status"] == "UNRESOLVED"
    assert public["verdict"]["cluster_status"] == "UNRESOLVED"
    assert public["verdict"]["common_control"] == "NOT_PROVEN"
