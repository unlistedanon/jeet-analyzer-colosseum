from __future__ import annotations

import copy
from dataclasses import asdict

from jeet_analyzer.models import AtaCreation, NativeTransfer, TokenTransfer, TransactionAutopsy


def _autopsy() -> TransactionAutopsy:
    token = TokenTransfer(
        signature="sig-1",
        timestamp="2026-09-03T20:00:00Z",
        block_time=1_800_000_000,
        mint="Mint111111111111111111111111111111111111",
        token_program="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        instruction_type="transferChecked",
        source_token_account="SourceAta11111111111111111111111111111111",
        destination_token_account="DestAta111111111111111111111111111111111",
        source_owner="WalletA11111111111111111111111111111111111",
        destination_owner="WalletB11111111111111111111111111111111111",
        authority="WalletA11111111111111111111111111111111111",
        amount_raw=42,
        source_delta_raw=-42,
        destination_delta_raw=42,
        decimals=6,
        inner=False,
    )
    native = NativeTransfer(
        signature="sig-1",
        timestamp="2026-09-03T20:00:00Z",
        block_time=1_800_000_000,
        source="WalletA11111111111111111111111111111111111",
        destination="WalletB11111111111111111111111111111111111",
        lamports=12345,
        source_is_signer=True,
        source_is_fee_payer=True,
    )
    ata = AtaCreation(
        signature="sig-1",
        timestamp="2026-09-03T20:00:00Z",
        block_time=1_800_000_000,
        payer="WalletA11111111111111111111111111111111111",
        owner="WalletB11111111111111111111111111111111111",
        token_account="DestAta111111111111111111111111111111111",
        mint="Mint111111111111111111111111111111111111",
    )
    return TransactionAutopsy(
        signature="sig-1",
        timestamp="2026-09-03T20:00:00Z",
        block_time=1_800_000_000,
        fee_payer="WalletA11111111111111111111111111111111111",
        signers=["WalletA11111111111111111111111111111111111"],
        program_ids=["11111111111111111111111111111111"],
        unknown_program_ids=[],
        instruction_summaries=[{"program": "system", "type": "transfer", "nested": {"x": 1}}],
        token_transfers=[token],
        native_transfers=[native],
        ata_creations=[ata],
        token_owner_deltas={
            "WalletA11111111111111111111111111111111111": {
                "Mint111111111111111111111111111111111111": (-42, 6)
            }
        },
        native_deltas={"WalletA11111111111111111111111111111111111": -12345},
        fee_lamports=5000,
        market_context=False,
        market_reasons=[],
        failed=False,
    )


def _legacy_record(autopsy: TransactionAutopsy) -> dict:
    record = asdict(autopsy)
    record["token_owner_deltas"] = {
        owner: {
            mint: {"delta_raw": amount, "decimals": decimals}
            for mint, (amount, decimals) in assets.items()
        }
        for owner, assets in autopsy.token_owner_deltas.items()
    }
    return record


def test_autopsy_record_matches_legacy_asdict_contract() -> None:
    autopsy = _autopsy()
    assert autopsy.to_record() == _legacy_record(autopsy)


def test_autopsy_record_does_not_require_recursive_deepcopy(monkeypatch) -> None:
    autopsy = _autopsy()
    expected = autopsy.to_record()

    def fail_deepcopy(*_args, **_kwargs):
        raise AssertionError("autopsy serialization must not recursively deepcopy evidence")

    monkeypatch.setattr(copy, "deepcopy", fail_deepcopy)
    assert autopsy.to_record() == expected
