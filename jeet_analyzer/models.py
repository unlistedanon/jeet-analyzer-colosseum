"""Shared evidence models for every Jeet Analyzer command.

The models describe observed public-chain facts.  Relationship records never
claim legal identity, beneficial ownership, or common human control.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class TokenTransfer:
    signature: str
    timestamp: str
    block_time: int
    mint: str
    token_program: str
    instruction_type: str
    source_token_account: str
    destination_token_account: str
    source_owner: str
    destination_owner: str
    authority: str
    amount_raw: int
    source_delta_raw: int | None
    destination_delta_raw: int | None
    decimals: int | None
    inner: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "timestamp": self.timestamp,
            "block_time": self.block_time,
            "mint": self.mint,
            "token_program": self.token_program,
            "instruction_type": self.instruction_type,
            "source_token_account": self.source_token_account,
            "destination_token_account": self.destination_token_account,
            "source_owner": self.source_owner,
            "destination_owner": self.destination_owner,
            "authority": self.authority,
            "amount_raw": self.amount_raw,
            "source_delta_raw": self.source_delta_raw,
            "destination_delta_raw": self.destination_delta_raw,
            "decimals": self.decimals,
            "inner": self.inner,
        }


@dataclass(frozen=True)
class NativeTransfer:
    signature: str
    timestamp: str
    block_time: int
    source: str
    destination: str
    lamports: int
    source_is_signer: bool
    source_is_fee_payer: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "timestamp": self.timestamp,
            "block_time": self.block_time,
            "source": self.source,
            "destination": self.destination,
            "lamports": self.lamports,
            "source_is_signer": self.source_is_signer,
            "source_is_fee_payer": self.source_is_fee_payer,
        }


@dataclass(frozen=True)
class AtaCreation:
    signature: str
    timestamp: str
    block_time: int
    payer: str
    owner: str
    token_account: str
    mint: str | None

    def to_record(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "timestamp": self.timestamp,
            "block_time": self.block_time,
            "payer": self.payer,
            "owner": self.owner,
            "token_account": self.token_account,
            "mint": self.mint,
        }


@dataclass
class TransactionAutopsy:
    signature: str
    timestamp: str
    block_time: int
    fee_payer: str | None
    signers: list[str]
    program_ids: list[str]
    unknown_program_ids: list[str]
    instruction_summaries: list[dict[str, Any]] = field(default_factory=list)
    token_transfers: list[TokenTransfer] = field(default_factory=list)
    native_transfers: list[NativeTransfer] = field(default_factory=list)
    ata_creations: list[AtaCreation] = field(default_factory=list)
    token_owner_deltas: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    native_deltas: dict[str, int] = field(default_factory=dict)
    fee_lamports: int = 0
    market_context: bool = False
    market_reasons: list[str] = field(default_factory=list)
    failed: bool = False

    def to_record(self) -> dict[str, Any]:
        """Return the stable JSON record without recursive deepcopy work.

        ``dataclasses.asdict`` recursively deep-copies every nested container.
        Full Forensic can serialize tens of thousands of autopsies, so that
        behavior multiplies memory and CPU at receipt finalization without
        changing any evidence value.  These containers hold JSON-safe parsed
        evidence and are never mutated by report writing, so direct records and
        shallow container copies preserve the existing contract exactly.
        """

        return {
            "signature": self.signature,
            "timestamp": self.timestamp,
            "block_time": self.block_time,
            "fee_payer": self.fee_payer,
            "signers": list(self.signers),
            "program_ids": list(self.program_ids),
            "unknown_program_ids": list(self.unknown_program_ids),
            "instruction_summaries": [dict(summary) for summary in self.instruction_summaries],
            "token_transfers": [transfer.to_record() for transfer in self.token_transfers],
            "native_transfers": [transfer.to_record() for transfer in self.native_transfers],
            "ata_creations": [creation.to_record() for creation in self.ata_creations],
            "token_owner_deltas": {
                owner: {
                    mint: {"delta_raw": amount, "decimals": decimals}
                    for mint, (amount, decimals) in assets.items()
                }
                for owner, assets in self.token_owner_deltas.items()
            },
            "native_deltas": dict(self.native_deltas),
            "fee_lamports": self.fee_lamports,
            "market_context": self.market_context,
            "market_reasons": list(self.market_reasons),
            "failed": self.failed,
        }


@dataclass
class RelationshipEdge:
    source: str
    destination: str
    relationship_type: str
    classification: str
    signature: str
    timestamp: str
    block_time: int
    asset: str
    amount_raw: int
    signers: list[str]
    fee_payer: str | None
    reason: str
    confidence: str
    source_depth: int = 0
    destination_depth: int = 0
    source_token_account: str | None = None
    destination_token_account: str | None = None
    recipient_independently_signed: bool = False
    relationship_context: str | None = None
    source_signed: bool | None = None
    source_paid_fee: bool | None = None
    market_context: bool | None = None
    common_control: str = "NOT_PROVEN"

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        # `reason` remains the stable internal field. `why_linked` is the
        # explicit user-facing alias so every discovered wallet can carry its
        # evidence trail without implying identity or common control.
        record["why_linked"] = self.reason
        return record


@dataclass(frozen=True)
class ProviderCoverage:
    complete: bool
    provider: str
    scope: str
    limitation: str
    pages: int
    retry_count: int = 0
    terminal_failure_reason: str | None = None
    failure_category: str | None = None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)
