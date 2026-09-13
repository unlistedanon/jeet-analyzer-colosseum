from __future__ import annotations

from jeet_analyzer.investigation import InvestigationContext


def _transaction(signature: str = "sig") -> dict:
    return {
        "signature": signature,
        "blockTime": 1_800_000_000,
        "meta": {
            "err": None,
            "logMessages": ["Program log: test"],
            "preBalances": [10, 20],
            "postBalances": [9, 21],
        },
        "transaction": {
            "signatures": [signature],
            "message": {
                "accountKeys": [
                    {"pubkey": "WalletA", "signer": True, "writable": True},
                    {"pubkey": "WalletB", "signer": False, "writable": True},
                ],
                "instructions": [],
            },
        },
    }


def test_transaction_snapshot_preserves_copy_isolation_without_nested_object_graph() -> None:
    context = InvestigationContext()
    transaction = _transaction()

    assert context.observe_transaction("sig", transaction)
    stored = context._transactions["sig"]
    assert isinstance(stored, str)

    transaction["meta"]["logMessages"].append("caller mutation")
    hit, first = context.transaction_get("sig")
    assert hit
    assert first is not None
    assert first["meta"]["logMessages"] == ["Program log: test"]

    first["meta"]["logMessages"].append("returned mutation")
    hit, second = context.transaction_get("sig")
    assert hit
    assert second is not None
    assert second["meta"]["logMessages"] == ["Program log: test"]


def test_provider_cache_snapshot_preserves_copy_isolation() -> None:
    context = InvestigationContext()
    value = {"data": [{"nested": [1, 2, 3]}], "paginationToken": None}

    context.cache_set("mint_metadata", ["mint", 1], value)
    stored = next(iter(context._cache.values()))
    assert isinstance(stored, str)

    value["data"][0]["nested"].append(4)
    hit, first = context.cache_get("mint_metadata", ["mint", 1])
    assert hit
    assert first == {"data": [{"nested": [1, 2, 3]}], "paginationToken": None}

    first["data"][0]["nested"].append(5)
    hit, second = context.cache_get("mint_metadata", ["mint", 1])
    assert hit
    assert second == {"data": [{"nested": [1, 2, 3]}], "paginationToken": None}


def test_history_page_cache_uses_compact_signature_manifest_not_full_payload() -> None:
    context = InvestigationContext()
    transactions = [_transaction(f"sig-{index}") for index in range(100)]
    value = {"data": transactions, "paginationToken": "next"}

    context.cache_set("history_page", ["wallet", 1], value)

    assert context._cache == {}
    assert len(context._history_page_manifests) == 1
    envelope_snapshot, signatures = next(iter(context._history_page_manifests.values()))
    assert isinstance(envelope_snapshot, str)
    assert len(signatures) == 100
    assert "Program log: test" not in envelope_snapshot

    # A manifest cannot be served until the transaction ledger owns the
    # immutable evidence snapshots. This is the normal ordering inside _history.
    hit, _restored = context.cache_get("history_page", ["wallet", 1])
    assert not hit

    for transaction in transactions:
        context.observe_transaction(transaction["signature"], transaction)

    hit, restored = context.cache_get("history_page", ["wallet", 1])
    assert hit
    assert restored["paginationToken"] == "next"
    assert len(restored["data"]) == 100
    assert restored["data"][0]["signature"] == "sig-0"

    restored["data"][0]["meta"]["logMessages"].append("returned mutation")
    hit, restored_again = context.cache_get("history_page", ["wallet", 1])
    assert hit
    assert restored_again["data"][0]["meta"]["logMessages"] == ["Program log: test"]


def test_duplicate_transaction_observation_does_not_replace_snapshot() -> None:
    context = InvestigationContext()
    original = _transaction("same")
    replacement = _transaction("same")
    replacement["blockTime"] = 1

    assert context.observe_transaction("same", original)
    assert not context.observe_transaction("same", replacement)

    hit, restored = context.transaction_get("same")
    assert hit
    assert restored is not None
    assert restored["blockTime"] == 1_800_000_000
    assert context.to_record()["duplicate_evidence_observations"] == 1


def test_transaction_payload_cache_is_bounded_without_weakening_evidence_counts() -> None:
    context = InvestigationContext()

    for index in range(300):
        signature = f"sig-{index}"
        assert context.observe_transaction(signature, _transaction(signature))

    telemetry = context.to_record()
    assert telemetry["transactions_fetched"] == 300
    assert telemetry["signatures_examined"] == 300
    assert telemetry["transaction_payload_cache_limit"] == 256
    assert telemetry["transaction_payload_cache_entries"] == 256
    assert telemetry["transaction_payloads_evicted"] == 44

    old_hit, _old = context.transaction_get("sig-0")
    recent_hit, recent = context.transaction_get("sig-299")
    assert not old_hit
    assert recent_hit
    assert recent is not None
    assert recent["signature"] == "sig-299"
