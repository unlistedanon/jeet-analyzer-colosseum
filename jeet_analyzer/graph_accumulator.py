"""Bounded graph-evidence retention for Full Forensic traversal.

The graph walk needs exact relationship facts long after each provider transaction
has been released, but it does not need a Python object graph for every transaction
resident in RAM. Compact graph facts are therefore serialized one record at a time
to a compressed temporary spool. Candidate discovery and exact signer activity stay
in small in-memory indexes.

The forensic relationship engine remains ``graph.extract_relationships``. This
module changes storage, not relationship interpretation.
"""

from __future__ import annotations

import json
import struct
import tempfile
import zlib
from dataclasses import asdict, dataclass
from typing import Iterable, Iterator, Mapping

from . import graph
from .models import TransactionAutopsy


@dataclass(frozen=True, slots=True)
class GraphTokenTransferFact:
    mint: str
    source_token_account: str
    destination_token_account: str
    source_owner: str
    destination_owner: str
    authority: str
    amount_raw: int


@dataclass(frozen=True, slots=True)
class GraphNativeTransferFact:
    source: str
    destination: str
    lamports: int
    source_is_signer: bool
    source_is_fee_payer: bool


@dataclass(frozen=True, slots=True)
class GraphAtaCreationFact:
    payer: str
    owner: str
    token_account: str
    mint: str | None


@dataclass(slots=True)
class CompactGraphAutopsy:
    """Small duck-typed view containing exactly what ``graph`` consumes."""

    signature: str
    timestamp: str
    block_time: int
    fee_payer: str | None
    signers: list[str]
    program_ids: list[str]
    unknown_program_ids: list[str]
    token_transfers: tuple[GraphTokenTransferFact, ...]
    native_transfers: tuple[GraphNativeTransferFact, ...]
    ata_creations: tuple[GraphAtaCreationFact, ...]
    native_deltas: dict[str, int]
    market_context: bool
    market_reasons: list[str]
    failed: bool


def compact_graph_autopsy(autopsy: TransactionAutopsy) -> CompactGraphAutopsy:
    """Copy only graph-relevant evidence from a full transaction autopsy."""

    token_transfers = tuple(
        GraphTokenTransferFact(
            mint=transfer.mint,
            source_token_account=transfer.source_token_account,
            destination_token_account=transfer.destination_token_account,
            source_owner=transfer.source_owner,
            destination_owner=transfer.destination_owner,
            authority=transfer.authority,
            amount_raw=int(transfer.amount_raw),
        )
        for transfer in autopsy.token_transfers
    )
    native_transfers = tuple(
        GraphNativeTransferFact(
            source=transfer.source,
            destination=transfer.destination,
            lamports=int(transfer.lamports),
            source_is_signer=bool(transfer.source_is_signer),
            source_is_fee_payer=bool(transfer.source_is_fee_payer),
        )
        for transfer in autopsy.native_transfers
    )
    ata_creations = tuple(
        GraphAtaCreationFact(
            payer=creation.payer,
            owner=creation.owner,
            token_account=creation.token_account,
            mint=creation.mint,
        )
        for creation in autopsy.ata_creations
    )

    # Relationship extraction reads native deltas only for ATA token-account rent.
    ata_accounts = {creation.token_account for creation in ata_creations}
    native_deltas = {
        account: int(amount)
        for account, amount in autopsy.native_deltas.items()
        if account in ata_accounts
    }

    return CompactGraphAutopsy(
        signature=autopsy.signature,
        timestamp=autopsy.timestamp,
        block_time=int(autopsy.block_time),
        fee_payer=autopsy.fee_payer,
        signers=list(autopsy.signers),
        program_ids=list(autopsy.program_ids),
        unknown_program_ids=list(autopsy.unknown_program_ids),
        token_transfers=token_transfers,
        native_transfers=native_transfers,
        ata_creations=ata_creations,
        native_deltas=native_deltas,
        market_context=bool(autopsy.market_context),
        market_reasons=list(autopsy.market_reasons),
        failed=bool(autopsy.failed),
    )


def _compact_to_bytes(autopsy: CompactGraphAutopsy) -> bytes:
    payload = json.dumps(
        asdict(autopsy), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    # Level 1 keeps CPU modest while collapsing repeated program ids, addresses,
    # field names, and market-reason strings that otherwise dominate RAM.
    return zlib.compress(payload, level=1)


def _compact_from_bytes(payload: bytes) -> CompactGraphAutopsy:
    row = json.loads(zlib.decompress(payload).decode("utf-8"))
    return CompactGraphAutopsy(
        signature=str(row["signature"]),
        timestamp=str(row["timestamp"]),
        block_time=int(row["block_time"]),
        fee_payer=None if row.get("fee_payer") is None else str(row["fee_payer"]),
        signers=[str(value) for value in row.get("signers", [])],
        program_ids=[str(value) for value in row.get("program_ids", [])],
        unknown_program_ids=[str(value) for value in row.get("unknown_program_ids", [])],
        token_transfers=tuple(GraphTokenTransferFact(**item) for item in row.get("token_transfers", [])),
        native_transfers=tuple(GraphNativeTransferFact(**item) for item in row.get("native_transfers", [])),
        ata_creations=tuple(GraphAtaCreationFact(**item) for item in row.get("ata_creations", [])),
        native_deltas={str(key): int(value) for key, value in row.get("native_deltas", {}).items()},
        market_context=bool(row.get("market_context")),
        market_reasons=[str(value) for value in row.get("market_reasons", [])],
        failed=bool(row.get("failed")),
    )


class GraphEvidenceAccumulator:
    """Spool compact graph facts to disk while retaining only bounded indexes."""

    def __init__(
        self, *, exclusion_sample_limit: int = 512, unresolved_sample_limit: int = 512
    ) -> None:
        if exclusion_sample_limit < 0:
            raise ValueError("exclusion_sample_limit cannot be negative")
        if unresolved_sample_limit < 0:
            raise ValueError("unresolved_sample_limit cannot be negative")
        self._seen_signatures: set[str] = set()
        self._candidates_by_wallet: dict[str, set[str]] = {}
        self._signer_activity = graph.SignerActivityIndex()
        self._store = tempfile.TemporaryFile(mode="w+b")
        self._spooled_bytes = 0
        self._spooled_autopsies = 0
        self._exclusion_sample_limit = int(exclusion_sample_limit)
        self._unresolved_sample_limit = int(unresolved_sample_limit)
        self._closed = False

    def __len__(self) -> int:
        return len(self._seen_signatures)

    @property
    def signatures(self) -> frozenset[str]:
        return frozenset(self._seen_signatures)

    @property
    def signers(self) -> frozenset[str]:
        return frozenset(self._signer_activity.signatures)

    @property
    def storage_bytes(self) -> int:
        return int(self._spooled_bytes)

    @property
    def spooled_autopsies(self) -> int:
        return int(self._spooled_autopsies)

    def telemetry(self) -> dict[str, int | str]:
        return {
            "storage": "compressed_temporary_spool",
            "autopsies_observed": len(self),
            "autopsies_spooled": self._spooled_autopsies,
            "spooled_bytes": self._spooled_bytes,
            "candidate_wallet_indexes": len(self._candidates_by_wallet),
            "signers_indexed": len(self._signer_activity.signatures),
            "excluded_infrastructure_sample_limit": self._exclusion_sample_limit,
            "unresolved_relationship_sample_limit": self._unresolved_sample_limit,
        }

    def ingest(self, autopsy: TransactionAutopsy) -> bool:
        """Persist one compact graph fact record and release the full autopsy."""

        if self._closed:
            raise RuntimeError("graph evidence accumulator is closed")
        if autopsy.signature in self._seen_signatures:
            return False
        self._seen_signatures.add(autopsy.signature)
        self._signer_activity.observe(autopsy)

        compact = compact_graph_autopsy(autopsy)
        self._index_candidates(compact)
        encoded = _compact_to_bytes(compact)
        self._store.seek(0, 2)
        self._store.write(struct.pack(">I", len(encoded)))
        self._store.write(encoded)
        self._spooled_bytes += 4 + len(encoded)
        self._spooled_autopsies += 1
        return True

    def extend(self, autopsies: Iterable[TransactionAutopsy]) -> int:
        accepted = 0
        for autopsy in autopsies:
            accepted += int(self.ingest(autopsy))
        return accepted

    def candidates_for(self, wallet: str) -> set[str]:
        return set(self._candidates_by_wallet.get(wallet, ()))

    def _iter_autopsies(self) -> Iterator[CompactGraphAutopsy]:
        if self._closed:
            raise RuntimeError("graph evidence accumulator is closed")
        self._store.flush()
        self._store.seek(0)
        while True:
            header = self._store.read(4)
            if not header:
                return
            if len(header) != 4:
                raise RuntimeError("graph evidence spool header is truncated")
            size = struct.unpack(">I", header)[0]
            payload = self._store.read(size)
            if len(payload) != size:
                raise RuntimeError("graph evidence spool record is truncated")
            yield _compact_from_bytes(payload)

    def evidence(self, classifications: Mapping[str, str]) -> graph.GraphEvidence:
        """Render exact relationships from the disk spool.

        Accepted edges remain exact. Infrastructure/rent exclusions and unresolved
        relationship rows are represented by bounded samples plus exact totals;
        unresolved reason/classification counts preserve fail-closed evidence truth
        without making report noise an unbounded in-memory corpus.
        """

        return graph.extract_relationships(
            self._iter_autopsies(),
            classifications,
            signer_activity=self._signer_activity,
            exclusion_limit=self._exclusion_sample_limit,
            unresolved_limit=self._unresolved_sample_limit,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._store.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _remember_candidate(self, wallet: str, candidate: str) -> None:
        if not wallet or not candidate or wallet == candidate:
            return
        self._candidates_by_wallet.setdefault(wallet, set()).add(candidate)

    def _index_candidates(self, autopsy: CompactGraphAutopsy) -> None:
        if autopsy.failed:
            return
        for transfer in autopsy.token_transfers:
            self._remember_candidate(transfer.source_owner, transfer.destination_owner)
            self._remember_candidate(transfer.destination_owner, transfer.source_owner)
            if not autopsy.market_context and transfer.authority != transfer.source_owner:
                self._remember_candidate(transfer.source_owner, transfer.authority)
                self._remember_candidate(transfer.destination_owner, transfer.authority)
        for transfer in autopsy.native_transfers:
            self._remember_candidate(transfer.source, transfer.destination)
            self._remember_candidate(transfer.destination, transfer.source)

        # Match graph.candidate_addresses exactly: co-signers and ATA
        # preparation are discovery signals only outside market/router context.
        if autopsy.market_context:
            return
        for wallet in autopsy.signers:
            for signer in autopsy.signers:
                self._remember_candidate(wallet, signer)
        for creation in autopsy.ata_creations:
            self._remember_candidate(creation.payer, creation.owner)
            self._remember_candidate(creation.owner, creation.payer)
