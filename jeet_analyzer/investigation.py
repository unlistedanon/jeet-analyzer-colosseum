"""Investigation-scoped request budgets, safe caches, and factual telemetry."""

from __future__ import annotations

import json
from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass
from typing import Any, Mapping


class ProviderBudgetExhausted(RuntimeError):
    """A hard investigation budget stopped additional provider work."""

    category = "PROVIDER_BUDGET_EXHAUSTED"

    def __init__(self, budget: str, limit: int) -> None:
        self.budget = budget
        self.limit = int(limit)
        super().__init__(f"PROVIDER_BUDGET_EXHAUSTED budget={budget} limit={limit}")


@dataclass(frozen=True)
class InvestigationLimits:
    max_rpc_requests: int = 500
    max_signatures: int = 10_000
    max_transactions: int = 5_000
    max_estimated_provider_credits: int = 60_000

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if int(value) < 1:
                raise ValueError(f"{name} must be positive")


_MISSING = object()
_HISTORY_PAGE_NAMESPACE = "history_page"
# Full jsonParsed Solana transactions can be very large.  The investigation
# must remember every observed transaction signature for budgets and evidence
# accounting, but it does not need every raw provider payload resident in RAM.
# Keep a small LRU payload window for request reuse while the signature ledger
# remains complete for the whole investigation.
_MAX_TRANSACTION_PAYLOAD_CACHE = 256


# Conservative, explicitly estimated weights for the read-only methods used by
# this project.  They are isolated here so provider pricing changes cannot be
# mistaken for authoritative billing data.  Unknown future methods receive the
# most conservative fallback instead of bypassing the investigation ceiling.
_RPC_ESTIMATED_CREDITS = {
    "getProgramAccounts": 10,
    # Helius currently documents getTransactionsForAddress at 100 credits/call.
    "getTransactionsForAddress": 100,
    # Helius currently documents getTransfersByAddress at 10 credits/call.
    "getTransfersByAddress": 10,
}
_DAS_ESTIMATED_CREDITS = 10
_UNKNOWN_METHOD_ESTIMATED_CREDITS = 100


def estimated_provider_credit_weight(channel: str, method: str) -> int:
    if channel == "das":
        return _DAS_ESTIMATED_CREDITS
    if channel == "rpc":
        if method in _RPC_ESTIMATED_CREDITS:
            return _RPC_ESTIMATED_CREDITS[method]
        # Every currently allowlisted non-special RPC method is a standard
        # JSON-RPC request.  A future unknown method fails conservatively.
        if method in {
            "getAccountInfo",
            "getMultipleAccounts",
            "getSignaturesForAddress",
            "getTokenAccountsByOwner",
            "getTokenLargestAccounts",
            "getTokenSupply",
            "getTransaction",
        }:
            return 1
        return _UNKNOWN_METHOD_ESTIMATED_CREDITS
    raise ValueError("provider channel must be rpc or das")


def canonical_request_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_snapshot(value: Any) -> str:
    """Capture provider JSON without recursively cloning its Python object graph.

    Provider/RPC payloads are JSON data.  Keeping the immutable snapshot as a
    compact JSON string preserves the old copy-isolation contract while avoiding
    a second nested dict/list graph for every cached transaction.
    """

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_restore(snapshot: str) -> Any:
    return json.loads(snapshot)


def _history_manifest_signature(transaction: Any) -> str | None:
    """Extract the stable transaction id needed for a compact history manifest."""

    if not isinstance(transaction, Mapping):
        return None
    direct = transaction.get("signature")
    if direct:
        return str(direct)
    payload = transaction.get("transaction")
    if isinstance(payload, Mapping):
        signatures = payload.get("signatures")
        if isinstance(signatures, list) and signatures:
            return str(signatures[0])
    return None


class InvestigationContext:
    """One command invocation's shared immutable evidence and work ledger."""

    def __init__(self, limits: InvestigationLimits | None = None) -> None:
        self.limits = limits or InvestigationLimits()
        self.limits.validate()
        self.rpc_requests_by_method: Counter[str] = Counter()
        self.das_requests_by_method: Counter[str] = Counter()
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_hits_by_namespace: Counter[str] = Counter()
        self.cache_misses_by_namespace: Counter[str] = Counter()
        self.provider_requests_avoided = 0
        self.duplicate_evidence_observations = 0
        self.wallet_traversals_suppressed = 0
        self.deduplicated_requests = 0
        self.retry_attempts = 0
        self.pages_fetched = 0
        self.estimated_provider_credits = 0
        self.estimated_provider_credits_by_method: Counter[str] = Counter()
        self._cache: dict[tuple[str, str], str] = {}
        # History pages can contain 100 large jsonParsed transactions.  Store
        # only the small non-data envelope plus transaction signatures.  A page
        # can be reconstructed only while all of its payloads remain in the
        # bounded transaction LRU below; otherwise the provider is queried
        # again and normal fail-closed budgets still apply.
        self._history_page_manifests: dict[
            tuple[str, str], tuple[str, tuple[str, ...]]
        ] = {}
        self._transactions: OrderedDict[str, str | None] = OrderedDict()
        self._transaction_signatures: set[str] = set()
        self.transaction_payloads_evicted = 0
        self._signatures: set[str] = set()
        self._wallet_remaining_depth: dict[str, int] = {}
        self.terminated_by: str | None = None
        self.termination_reason: str | None = None

    @property
    def rpc_requests(self) -> int:
        return sum(self.rpc_requests_by_method.values())

    @property
    def das_requests(self) -> int:
        return sum(self.das_requests_by_method.values())

    @property
    def total_provider_requests(self) -> int:
        return self.rpc_requests + self.das_requests

    @property
    def expansion_exhausted(self) -> bool:
        return self.terminated_by is not None

    def _terminate(self, budget: str, limit: int) -> ProviderBudgetExhausted:
        error = ProviderBudgetExhausted(budget, limit)
        if self.terminated_by is None:
            self.terminated_by = budget
            self.termination_reason = str(error)
        return error

    def before_request(self, channel: str, method: str, *, retry: bool = False) -> None:
        if channel not in {"rpc", "das"}:
            raise ValueError("provider channel must be rpc or das")
        if channel == "rpc" and self.rpc_requests >= self.limits.max_rpc_requests:
            raise self._terminate("max_rpc_requests", self.limits.max_rpc_requests)
        estimated_weight = estimated_provider_credit_weight(channel, method)
        if (
            self.estimated_provider_credits + estimated_weight
            > self.limits.max_estimated_provider_credits
        ):
            raise self._terminate(
                "max_estimated_provider_credits",
                self.limits.max_estimated_provider_credits,
            )
        if channel == "rpc":
            self.rpc_requests_by_method[method] += 1
        else:
            self.das_requests_by_method[method] += 1
        self.estimated_provider_credits += estimated_weight
        self.estimated_provider_credits_by_method[f"{channel}.{method}"] += estimated_weight
        if retry:
            self.retry_attempts += 1

    def record_page(self) -> None:
        self.pages_fetched += 1

    def cache_get(self, namespace: str, key: Any) -> tuple[bool, Any]:
        composite = (namespace, canonical_request_key(key))
        if namespace == _HISTORY_PAGE_NAMESPACE:
            manifest = self._history_page_manifests.get(composite)
            if manifest is not None:
                envelope_snapshot, signatures = manifest
                restored_transactions: list[Any] = []
                for signature in signatures:
                    snapshot = self._transactions.get(signature, _MISSING)
                    if snapshot is _MISSING or snapshot is None:
                        break
                    self._transactions.move_to_end(signature)
                    restored_transactions.append(_json_restore(snapshot))
                else:
                    envelope = _json_restore(envelope_snapshot)
                    if isinstance(envelope, dict):
                        envelope["data"] = restored_transactions
                        self.cache_hits += 1
                        self.cache_hits_by_namespace[namespace] += 1
                        self.deduplicated_requests += 1
                        return True, envelope
            self.cache_misses += 1
            self.cache_misses_by_namespace[namespace] += 1
            return False, _MISSING
        if composite not in self._cache:
            self.cache_misses += 1
            self.cache_misses_by_namespace[namespace] += 1
            return False, _MISSING
        self.cache_hits += 1
        self.cache_hits_by_namespace[namespace] += 1
        self.deduplicated_requests += 1
        return True, _json_restore(self._cache[composite])

    def cache_set(self, namespace: str, key: Any, value: Any) -> None:
        composite = (namespace, canonical_request_key(key))
        if namespace == _HISTORY_PAGE_NAMESPACE:
            if not isinstance(value, Mapping):
                return
            rows = value.get("data")
            if not isinstance(rows, list):
                return
            signatures: list[str] = []
            for transaction in rows:
                signature = _history_manifest_signature(transaction)
                if signature is None:
                    return
                signatures.append(signature)
            envelope = {name: item for name, item in value.items() if name != "data"}
            self._history_page_manifests[composite] = (
                _json_snapshot(envelope),
                tuple(signatures),
            )
            return
        self._cache[composite] = _json_snapshot(value)

    def history_page_manifest_get(
        self, key: Any
    ) -> tuple[bool, dict[str, Any] | None, tuple[str, ...]]:
        """Return a compact cached history page without rebuilding its data list."""

        composite = (_HISTORY_PAGE_NAMESPACE, canonical_request_key(key))
        manifest = self._history_page_manifests.get(composite)
        if manifest is not None:
            envelope_snapshot, signatures = manifest
            for signature in signatures:
                snapshot = self._transactions.get(signature, _MISSING)
                if snapshot is _MISSING or snapshot is None:
                    break
            else:
                envelope = _json_restore(envelope_snapshot)
                if isinstance(envelope, dict):
                    for signature in signatures:
                        self._transactions.move_to_end(signature)
                    self.cache_hits += 1
                    self.cache_hits_by_namespace[_HISTORY_PAGE_NAMESPACE] += 1
                    self.deduplicated_requests += 1
                    return True, envelope, signatures
        self.cache_misses += 1
        self.cache_misses_by_namespace[_HISTORY_PAGE_NAMESPACE] += 1
        return False, None, ()

    def history_page_transaction(self, signature: str) -> dict[str, Any] | None:
        """Restore one cached history transaction without whole-page reconstruction."""

        snapshot = self._transactions.get(str(signature), _MISSING)
        if snapshot is _MISSING or snapshot is None:
            return None
        self._transactions.move_to_end(str(signature))
        restored = _json_restore(snapshot)
        return restored if isinstance(restored, dict) else None

    def history_page_manifest_set(
        self, key: Any, envelope: Mapping[str, Any], signatures: list[str] | tuple[str, ...]
    ) -> None:
        """Store only a history page envelope and its ordered transaction ids."""

        composite = (_HISTORY_PAGE_NAMESPACE, canonical_request_key(key))
        normalized = tuple(str(signature) for signature in signatures)
        if any(signature not in self._transactions for signature in normalized):
            return
        self._history_page_manifests[composite] = (
            _json_snapshot(dict(envelope)),
            normalized,
        )

    def record_provider_request_avoided(self, count: int = 1) -> None:
        count = int(count)
        if count < 0:
            raise ValueError("avoided provider request count cannot be negative")
        self.provider_requests_avoided += count

    def transaction_get(self, signature: str) -> tuple[bool, dict[str, Any] | None]:
        if signature not in self._transactions:
            self.cache_misses += 1
            self.cache_misses_by_namespace["transaction"] += 1
            return False, None
        self.cache_hits += 1
        self.cache_hits_by_namespace["transaction"] += 1
        self.deduplicated_requests += 1
        snapshot = self._transactions[signature]
        self._transactions.move_to_end(signature)
        return True, None if snapshot is None else _json_restore(snapshot)

    def _remember_transaction_payload(
        self, signature: str, transaction: Mapping[str, Any] | None
    ) -> None:
        self._transactions[signature] = (
            _json_snapshot(dict(transaction)) if transaction is not None else None
        )
        self._transactions.move_to_end(signature)
        while len(self._transactions) > _MAX_TRANSACTION_PAYLOAD_CACHE:
            self._transactions.popitem(last=False)
            self.transaction_payloads_evicted += 1

    def observe_transaction(
        self, signature: str, transaction: Mapping[str, Any] | None
    ) -> bool:
        signature = str(signature)
        new_signature = signature not in self._signatures
        new_transaction = signature not in self._transaction_signatures
        if new_signature and len(self._signatures) >= self.limits.max_signatures:
            raise self._terminate("max_signatures", self.limits.max_signatures)
        if new_transaction and len(self._transaction_signatures) >= self.limits.max_transactions:
            raise self._terminate("max_transactions", self.limits.max_transactions)
        duplicate = not new_signature and not new_transaction
        if new_signature:
            self._signatures.add(signature)
        if new_transaction:
            self._transaction_signatures.add(signature)
        # Preserve the first retained immutable snapshot.  If an older payload
        # was evicted from the bounded window, a later duplicate may repopulate
        # that cache slot without changing the unique evidence/budget count.
        if not duplicate or signature not in self._transactions:
            self._remember_transaction_payload(signature, transaction)
        if duplicate:
            self.duplicate_evidence_observations += 1
            self.deduplicated_requests += 1
            return False
        return True

    def observe_signature(self, signature: str) -> bool:
        signature = str(signature)
        if signature in self._signatures:
            self.duplicate_evidence_observations += 1
            self.deduplicated_requests += 1
            return False
        if len(self._signatures) >= self.limits.max_signatures:
            raise self._terminate("max_signatures", self.limits.max_signatures)
        self._signatures.add(signature)
        return True

    def should_traverse_wallet(self, wallet: str, remaining_depth: int) -> bool:
        remaining_depth = int(remaining_depth)
        previous = self._wallet_remaining_depth.get(wallet)
        if previous is not None and previous >= remaining_depth:
            self.wallet_traversals_suppressed += 1
            self.deduplicated_requests += 1
            return False
        self._wallet_remaining_depth[wallet] = remaining_depth
        return True

    def to_record(self) -> dict[str, Any]:
        return {
            "total_provider_requests": self.total_provider_requests,
            "rpc_requests": self.rpc_requests,
            "das_requests": self.das_requests,
            "rpc_requests_by_method": dict(sorted(self.rpc_requests_by_method.items())),
            "das_requests_by_method": dict(sorted(self.das_requests_by_method.items())),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hits_by_namespace": dict(sorted(self.cache_hits_by_namespace.items())),
            "cache_misses_by_namespace": dict(sorted(self.cache_misses_by_namespace.items())),
            "provider_requests_avoided": self.provider_requests_avoided,
            "duplicate_evidence_observations": self.duplicate_evidence_observations,
            "wallet_traversals_suppressed": self.wallet_traversals_suppressed,
            "deduplicated_skipped_requests": self.deduplicated_requests,
            "deduplicated_skipped_requests_deprecated": True,
            "retry_attempts": self.retry_attempts,
            "wallets_traversed": len(self._wallet_remaining_depth),
            "signatures_examined": len(self._signatures),
            "transactions_fetched": len(self._transaction_signatures),
            "transaction_payload_cache_entries": len(self._transactions),
            "transaction_payload_cache_limit": _MAX_TRANSACTION_PAYLOAD_CACHE,
            "transaction_payloads_evicted": self.transaction_payloads_evicted,
            "pages_fetched": self.pages_fetched,
            "history_page_manifests": len(self._history_page_manifests),
            "estimated_provider_credits": self.estimated_provider_credits,
            "estimated_provider_credits_by_method": dict(
                sorted(self.estimated_provider_credits_by_method.items())
            ),
            "estimated_provider_credit_ceiling": self.limits.max_estimated_provider_credits,
            "estimated_provider_credit_cost_is_authoritative": False,
            "estimated_provider_credit_model": (
                "method-weighted read-only request estimate; cache hits and deduplicated "
                "evidence cost zero; retries count as provider attempts"
            ),
            "budget_limits": asdict(self.limits),
            "terminated_by_budget": self.terminated_by,
            "termination_reason": self.termination_reason,
            "provider_credit_cost": None,
            "provider_credit_cost_note": "not reported because authoritative provider cost data was not supplied",
        }
