"""Historical provider, normalization, tracing, and seller aggregation.

All network methods are explicitly allowlisted reads.  This module contains no
transaction construction, signing, or submission code.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlencode

import ijson
import requests
from ijson.common import JSONError, ObjectBuilder
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from . import tx_classifier
from .investigation import InvestigationContext, ProviderBudgetExhausted


SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
BENIGN_TRANSFER_PROGRAMS = {
    SYSTEM_PROGRAM,
    TOKEN_PROGRAM,
    TOKEN_2022_PROGRAM,
    ASSOCIATED_TOKEN_PROGRAM,
    COMPUTE_BUDGET_PROGRAM,
    MEMO_PROGRAM,
}
KNOWN_PROGRAMS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "JUPITER_V6",
    "675kPX9MHTjS2zt1qfr1NYHuzefVAvHGHbGDXfL4fGF": "RAYDIUM_AMM_V4",
    "whirLbMiicVdio4qvUfM5KAg6CtX9NAjEEQyF9jb": "ORCA_WHIRLPOOL",
}
ROUTER_PROGRAMS = {"JUPITER_V6"}
NORMALIZED_SCHEMA = "jeet-analyzer.events.v1"
TOKEN_ACCOUNT_INDEX_START = 1671062400  # Helius documents tokenAccounts coverage from December 2022.


class HistoryError(RuntimeError):
    pass


class CoverageError(HistoryError):
    category = "COVERAGE_EXHAUSTION"


class ProviderError(HistoryError):
    category = "PROVIDER_ERROR"

    def __init__(self, message: str, *, retry_count: int = 0) -> None:
        super().__init__(message)
        self.retry_count = int(retry_count)

    def to_record(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "retry_count": self.retry_count,
            "terminal_failure_reason": str(self),
        }


class ProviderRateLimitError(ProviderError):
    category = "RATE_LIMIT"


class ProviderTimeoutError(ProviderError):
    category = "TIMEOUT"


class ProviderHttpError(ProviderError):
    category = "HTTP_ERROR"


class ProviderMalformedResponseError(ProviderError):
    category = "MALFORMED_RESPONSE"


class ProviderTransportError(ProviderError):
    category = "TRANSPORT_ERROR"


@dataclass(frozen=True)
class TokenMetadata:
    mint: str
    symbol: str
    name: str | None
    decimals: int
    supply: int
    price_usd: Decimal | None = None
    metadata_source: str = "on-chain RPC"
    token_program: str | None = None


@dataclass(frozen=True)
class ProviderBatch:
    transactions: list[dict[str, Any]]
    complete: bool
    provider: str
    coverage_scope: str
    limitation: str
    pages: int = 0
    retry_count: int = 0
    terminal_failure_reason: str | None = None
    failure_category: str | None = None


@dataclass
class NormalizedEvent:
    timestamp: str
    block_time: int
    signature: str
    mint: str
    event_type: str
    wallet: str | None
    token_delta_raw: int
    token_amount_raw: int
    destination: str | None = None
    candidate_destination: str | None = None
    source_token_account: str | None = None
    destination_token_account: str | None = None
    quote_mint: str | None = None
    quote_symbol: str | None = None
    quote_amount_raw: int | None = None
    quote_decimals: int | None = None
    quote_usd: str | None = None
    venue: str | None = None
    router: str | None = None
    routed: bool | None = None
    direct: bool | None = None
    linked_by_transfer: bool = False
    linked_from: list[str] = field(default_factory=list)
    evidence: str | None = None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["record_type"] = "event"
        return record

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "NormalizedEvent":
        fields = {key: record.get(key) for key in cls.__dataclass_fields__}
        fields["block_time"] = int(fields["block_time"] or 0)
        fields["token_delta_raw"] = int(fields["token_delta_raw"] or 0)
        fields["token_amount_raw"] = int(fields["token_amount_raw"] or 0)
        if fields["quote_amount_raw"] is not None:
            fields["quote_amount_raw"] = int(fields["quote_amount_raw"])
        if fields["quote_decimals"] is not None:
            fields["quote_decimals"] = int(fields["quote_decimals"])
        fields["linked_from"] = list(fields["linked_from"] or [])
        return cls(**fields)


def normalized_target_transfer_event(
    row: Mapping[str, Any], mint: str
) -> NormalizedEvent | None:
    """Convert one parsed Helius target-token transfer into Jeet's canonical edge event.

    getTransfersByAddress already resolves Token and Token-2022 transfer ownership.
    Jeet still requires the same concrete source/destination token-account proof used
    by raw-transaction normalization before a row can become a trace edge.
    """

    if str(row.get("mint") or "") != mint:
        return None
    if str(row.get("type") or "").lower() != "transfer":
        return None
    signature = str(row.get("signature") or "")
    source = str(row.get("fromUserAccount") or "")
    destination = str(row.get("toUserAccount") or "")
    source_token_account = str(row.get("fromTokenAccount") or "")
    destination_token_account = str(row.get("toTokenAccount") or "")
    try:
        amount = int(row.get("amount") or 0)
        block_time = int(row.get("blockTime") or 0)
    except (TypeError, ValueError):
        return None
    if (
        not signature
        or not source
        or not destination
        or source == destination
        or not source_token_account
        or not destination_token_account
        or amount <= 0
        or block_time <= 0
    ):
        return None
    timestamp = datetime.fromtimestamp(block_time, timezone.utc).isoformat().replace("+00:00", "Z")
    return NormalizedEvent(
        timestamp=timestamp,
        block_time=block_time,
        signature=signature,
        mint=mint,
        event_type="TRANSFER",
        wallet=source,
        token_delta_raw=-amount,
        token_amount_raw=amount,
        destination=destination,
        source_token_account=source_token_account,
        destination_token_account=destination_token_account,
        direct=True,
        linked_by_transfer=True,
        evidence="HELIUS_GET_TRANSFERS_BY_ADDRESS_MINT_FILTER",
    )


class HistoricalProvider(ABC):
    """Read-only provider interface used by scan and wallet analysis."""

    @abstractmethod
    def get_token_events(self, mint: str, start: int, end: int) -> ProviderBatch:
        raise NotImplementedError

    @abstractmethod
    def get_wallet_events(self, wallet: str, mint: str, start: int, end: int) -> ProviderBatch:
        raise NotImplementedError

    @abstractmethod
    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def get_wallet_token_balance(self, wallet: str, mint: str) -> int:
        raise NotImplementedError

    def get_wallet_activity(self, wallet: str, start: int, end: int) -> ProviderBatch:
        """All address activity for cross-asset graph discovery.

        Providers should override this when they can query native-only activity.
        The conservative default reuses wallet history and marks its narrower
        coverage scope explicitly through the returned batch.
        """

        return self.get_wallet_events(wallet, "", start, end)

    def stream_token_events(
        self,
        mint: str,
        start: int,
        end: int,
        consumer: Callable[[dict[str, Any]], None],
    ) -> ProviderBatch:
        """Return a buffered fallback when incremental delivery is unavailable.

        Callers must process any transactions left on the returned batch.
        Providers with native streaming should override this method, call the
        consumer as pages arrive, and return coverage with an empty transaction
        list.  This preserves compatibility with deterministic fixture providers.
        """

        del consumer
        return self.get_token_events(mint, start, end)

    def stream_wallet_events(
        self,
        wallet: str,
        mint: str,
        start: int,
        end: int,
        consumer: Callable[[dict[str, Any]], None],
    ) -> ProviderBatch:
        """Return a buffered fallback for seed-wallet target-token history."""

        del consumer
        return self.get_wallet_events(wallet, mint, start, end)

    def stream_wallet_activity(
        self,
        wallet: str,
        start: int,
        end: int,
        consumer: Callable[[dict[str, Any]], None],
    ) -> ProviderBatch:
        """Return a buffered fallback for all-asset graph-wallet history."""

        del consumer
        return self.get_wallet_activity(wallet, start, end)

    def stream_wallet_events_bounded(
        self,
        wallet: str,
        mint: str,
        start: int,
        end: int,
        consumer: Callable[[dict[str, Any]], None],
        *,
        max_pages: int,
    ) -> ProviderBatch:
        """Bounded target-token-bearing wallet history fallback."""

        del max_pages
        return self.stream_wallet_events(wallet, mint, start, end, consumer)

    def stream_wallet_target_transfers_bounded(
        self,
        wallet: str,
        mint: str,
        start: int,
        end: int,
        consumer: Callable[[NormalizedEvent], None],
        *,
        max_pages: int,
    ) -> ProviderBatch:
        """Bounded target-mint transfer stream with a raw-history fallback.

        Helius overrides this with getTransfersByAddress. Other providers reuse
        their existing wallet history and normalize only concrete target-token
        transfer edges, preserving deterministic/offline compatibility.
        """

        def consume_transaction(transaction: dict[str, Any]) -> None:
            for event in normalize_transaction(
                transaction, mint, include_unknown_increases=True
            ):
                if (
                    is_concrete_transfer_event(event)
                    and (event.wallet == wallet or event.destination == wallet)
                ):
                    consumer(event)

        batch = self.stream_wallet_events_bounded(
            wallet, mint, start, end, consume_transaction, max_pages=max_pages
        )
        for transaction in batch.transactions:
            consume_transaction(transaction)
        return replace(
            batch,
            transactions=[],
            coverage_scope=(
                f"{batch.coverage_scope}; target-mint transfer fallback for wallet={wallet}"
            ),
        )


    def stream_wallet_activity_bounded(
        self,
        wallet: str,
        start: int,
        end: int,
        consumer: Callable[[dict[str, Any]], None],
        *,
        max_pages: int,
    ) -> ProviderBatch:
        """Bounded graph-wallet activity fallback for deterministic providers.

        Providers without native page-level control keep their existing semantics.
        Helius overrides this method so one pathological wallet cannot monopolize
        the investigation-wide signature/request budget.
        """

        del max_pages
        return self.stream_wallet_activity(wallet, start, end, consumer)


class HeliusHistoricalProvider(HistoricalProvider):
    """Metered, indexed Helius history with exhaustive pagination.

    Historical completeness is provider-contract completeness, not an
    independent ledger audit.  Any pagination ambiguity or exhausted retry
    budget fails the scan rather than returning a partial leaderboard.
    """

    ALLOWED_METHODS = frozenset(
        {"getAsset", "getTokenAccounts", "getTransaction", "getTransactionsForAddress", "getTransfersByAddress"}
    )
    DAS_METHODS = frozenset({"getAsset", "getTokenAccounts"})
    CACHEABLE_METHODS = frozenset({"getAsset", "getTransaction"})
    CACHE_NAMESPACES = {
        "getAsset": "mint_metadata",
        "getTransaction": "transaction",
    }

    def __init__(
        self,
        api_key: str,
        rpc: Any,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        max_pages: int = 1000,
        full_history_page_limit: int = 100,
        backoff_cap_seconds: float = 8.0,
        sleeper: Callable[[float], None] = time.sleep,
        investigation: InvestigationContext | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError("HELIUS_API_KEY is required for indexed historical scan and wallet modes")
        self.__url = "https://mainnet.helius-rpc.com/?" + urlencode({"api-key": api_key})
        self.rpc = rpc
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_pages = max_pages
        self.full_history_page_limit = int(full_history_page_limit)
        self.backoff_cap_seconds = backoff_cap_seconds
        self.sleeper = sleeper
        self.investigation = investigation
        self._request_id = 0
        self.total_retry_count = 0
        self.last_failure_reason: str | None = None
        self.last_failure_category: str | None = None
        if self.timeout_seconds <= 0:
            raise ProviderError("provider request timeout must be positive")
        if not 0 <= self.max_retries <= 10:
            raise ProviderError("provider retries must be between 0 and 10")
        if self.max_pages < 1:
            raise ProviderError("provider max_pages must be positive")
        if not 1 <= self.full_history_page_limit <= 100:
            raise ProviderError("provider full_history_page_limit must be between 1 and 100")
        if not 0 < self.backoff_cap_seconds <= 60:
            raise ProviderError("provider backoff cap must be greater than 0 and no more than 60 seconds")

    def _backoff(self, attempt: int, retry_after: object = None) -> None:
        try:
            requested = float(retry_after) if retry_after not in {None, ""} else 2**attempt
        except (TypeError, ValueError):
            requested = 2**attempt
        delay = min(max(requested, 0.1), self.backoff_cap_seconds)
        self.total_retry_count += 1
        self.sleeper(delay)

    def _terminal(self, error_type: type[ProviderError], message: str, retry_count: int) -> ProviderError:
        error = error_type(message, retry_count=retry_count)
        self.last_failure_reason = str(error)
        self.last_failure_category = error.category
        return error

    def telemetry(self) -> dict[str, Any]:
        record = {
            "retry_count": self.total_retry_count,
            "terminal_failure_reason": self.last_failure_reason,
            "failure_category": self.last_failure_category,
            "request_timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "full_history_page_limit": self.full_history_page_limit,
            "backoff_cap_seconds": self.backoff_cap_seconds,
        }
        if self.investigation is not None:
            record["investigation"] = self.investigation.to_record()
        return record

    def _call(self, method: str, params: Any) -> Any:
        if method not in self.ALLOWED_METHODS:
            raise ProviderError(f"Helius method blocked by read-only policy: {method}")
        cache_key = [method, params]
        if self.investigation is not None and method in self.CACHEABLE_METHODS:
            namespace = self.CACHE_NAMESPACES[method]
            hit, cached = self.investigation.cache_get(namespace, cache_key)
            if hit:
                self.investigation.record_provider_request_avoided()
                return cached
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        call_retries = 0
        for attempt in range(self.max_retries + 1):
            if self.investigation is not None:
                self.investigation.before_request(
                    "das" if method in self.DAS_METHODS else "rpc",
                    method,
                    retry=attempt > 0,
                )
            try:
                response = self.session.post(self.__url, json=payload, timeout=self.timeout_seconds)
            except requests.Timeout as exc:
                if attempt < self.max_retries:
                    call_retries += 1
                    self._backoff(attempt)
                    continue
                raise self._terminal(
                    ProviderTimeoutError,
                    f"Helius {method} timed out after {attempt + 1} attempts; URL redacted",
                    call_retries,
                ) from exc
            except (requests.RequestException, OSError) as exc:
                if attempt < self.max_retries:
                    call_retries += 1
                    self._backoff(attempt)
                    continue
                raise self._terminal(
                    ProviderTransportError,
                    f"Helius {method} transport failed after {attempt + 1} attempts ({type(exc).__name__}); URL redacted",
                    call_retries,
                ) from exc
            except Exception as exc:
                raise self._terminal(
                    ProviderTransportError,
                    f"Helius {method} transport failed ({type(exc).__name__}); URL redacted",
                    call_retries,
                ) from exc
            status = int(getattr(response, "status_code", 0))
            if status == 429:
                if attempt >= self.max_retries:
                    raise self._terminal(
                        ProviderRateLimitError,
                        f"Helius {method} rate limit persisted after {attempt + 1} attempts",
                        call_retries,
                    )
                retry_after = getattr(response, "headers", {}).get("Retry-After", "")
                call_retries += 1
                self._backoff(attempt, retry_after)
                continue
            if not 200 <= status < 300:
                if status >= 500 and attempt < self.max_retries:
                    call_retries += 1
                    self._backoff(attempt)
                    continue
                raise self._terminal(
                    ProviderHttpError,
                    f"Helius {method} returned HTTP {status}; provider message and URL redacted",
                    call_retries,
                )
            try:
                body = response.json()
            except Exception as exc:
                raise self._terminal(
                    ProviderMalformedResponseError,
                    f"Helius {method} returned invalid JSON; URL redacted",
                    call_retries,
                ) from exc
            if not isinstance(body, Mapping):
                raise self._terminal(
                    ProviderMalformedResponseError,
                    f"Helius {method} returned a non-object response; URL redacted",
                    call_retries,
                )
            error = body.get("error")
            if error:
                code = error.get("code") if isinstance(error, dict) else "unknown"
                if code == -32029:
                    if attempt >= self.max_retries:
                        raise self._terminal(
                            ProviderRateLimitError,
                            f"Helius {method} rate limit persisted after {attempt + 1} attempts",
                            call_retries,
                        )
                    call_retries += 1
                    self._backoff(attempt)
                    continue
                raise self._terminal(
                    ProviderError,
                    f"Helius {method} RPC error {code}; provider message and URL redacted",
                    call_retries,
                )
            if "result" not in body:
                raise self._terminal(
                    ProviderMalformedResponseError,
                    f"Helius {method} response omitted result",
                    call_retries,
                )
            result = body["result"]
            if self.investigation is not None and method == "getTransactionsForAddress":
                self.investigation.record_page()
            if self.investigation is not None and method in self.CACHEABLE_METHODS:
                self.investigation.cache_set(self.CACHE_NAMESPACES[method], cache_key, result)
            return result
        raise self._terminal(
            ProviderRateLimitError,
            f"Helius {method} retry budget exhausted",
            call_retries,
        )

    def _stream_history_page(
        self,
        params: Any,
        transaction_consumer: Callable[[dict[str, Any]], None],
    ) -> str | None:
        """Stream one full Helius history page without building its data list.

        The regular JSON-RPC path intentionally keeps response.json() for small
        methods. getTransactionsForAddress is different: one full jsonParsed page
        can be hundreds of megabytes for pathological transactions. R11 consumes
        result.data one transaction object at a time and returns only the compact
        pagination token needed by the caller.
        """

        method = "getTransactionsForAddress"
        cache_key = [method, params]
        if self.investigation is not None:
            hit, envelope, signatures = self.investigation.history_page_manifest_get(cache_key)
            if hit:
                self.investigation.record_provider_request_avoided()
                for signature in signatures:
                    transaction = self.investigation.history_page_transaction(signature)
                    if transaction is None:
                        raise ProviderMalformedResponseError(
                            "cached Helius history manifest lost a transaction payload"
                        )
                    transaction_consumer(transaction)
                token = (envelope or {}).get("paginationToken")
                return None if token is None else str(token)

        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        call_retries = 0

        for attempt in range(self.max_retries + 1):
            if self.investigation is not None:
                self.investigation.before_request("rpc", method, retry=attempt > 0)

            response: Any = None
            try:
                response = self.session.post(
                    self.__url,
                    json=payload,
                    timeout=self.timeout_seconds,
                    stream=True,
                )
            except requests.Timeout as exc:
                if attempt < self.max_retries:
                    call_retries += 1
                    self._backoff(attempt)
                    continue
                raise self._terminal(
                    ProviderTimeoutError,
                    f"Helius {method} timed out after {attempt + 1} attempts; URL redacted",
                    call_retries,
                ) from exc
            except (requests.RequestException, OSError) as exc:
                if attempt < self.max_retries:
                    call_retries += 1
                    self._backoff(attempt)
                    continue
                raise self._terminal(
                    ProviderTransportError,
                    f"Helius {method} transport failed after {attempt + 1} attempts ({type(exc).__name__}); URL redacted",
                    call_retries,
                ) from exc
            except Exception as exc:
                raise self._terminal(
                    ProviderTransportError,
                    f"Helius {method} transport failed ({type(exc).__name__}); URL redacted",
                    call_retries,
                ) from exc

            try:
                status = int(getattr(response, "status_code", 0))
                if status == 429:
                    if attempt >= self.max_retries:
                        raise self._terminal(
                            ProviderRateLimitError,
                            f"Helius {method} rate limit persisted after {attempt + 1} attempts",
                            call_retries,
                        )
                    retry_after = getattr(response, "headers", {}).get("Retry-After", "")
                    call_retries += 1
                    self._backoff(attempt, retry_after)
                    continue
                if not 200 <= status < 300:
                    if status >= 500 and attempt < self.max_retries:
                        call_retries += 1
                        self._backoff(attempt)
                        continue
                    raise self._terminal(
                        ProviderHttpError,
                        f"Helius {method} returned HTTP {status}; provider message and URL redacted",
                        call_retries,
                    )

                raw = getattr(response, "raw", None)
                if raw is None:
                    raise self._terminal(
                        ProviderMalformedResponseError,
                        f"Helius {method} streaming response omitted raw body",
                        call_retries,
                    )
                if hasattr(raw, "decode_content"):
                    raw.decode_content = True

                result_seen = False
                data_seen = False
                error_seen = False
                error_code: object = "unknown"
                pagination_token: str | None = None
                page_signatures: list[str] = []
                item_builder: ObjectBuilder | None = None
                item_depth = 0

                try:
                    events = ijson.parse(raw, use_float=True)
                    for prefix, event, value in events:
                        if item_builder is not None:
                            if not (
                                prefix == "result.data.item"
                                or prefix.startswith("result.data.item.")
                            ):
                                raise self._terminal(
                                    ProviderMalformedResponseError,
                                    "Helius history transaction stream ended unexpectedly",
                                    call_retries,
                                )
                            item_builder.event(event, value)
                            if event in {"start_map", "start_array"}:
                                item_depth += 1
                            elif event in {"end_map", "end_array"}:
                                item_depth -= 1
                            if item_depth == 0:
                                transaction = item_builder.value
                                item_builder = None
                                if not isinstance(transaction, dict):
                                    raise self._terminal(
                                        ProviderMalformedResponseError,
                                        "Helius history data item was not an object",
                                        call_retries,
                                    )
                                signature = transaction_signature(transaction)
                                if signature:
                                    page_signatures.append(signature)
                                transaction_consumer(transaction)
                            continue

                        if prefix == "result" and event == "start_map":
                            result_seen = True
                        elif prefix == "result.data" and event == "start_array":
                            data_seen = True
                        elif prefix == "result.data.item":
                            if event not in {"start_map", "start_array"}:
                                raise self._terminal(
                                    ProviderMalformedResponseError,
                                    "Helius history data item was not an object",
                                    call_retries,
                                )
                            item_builder = ObjectBuilder()
                            item_depth = 1
                            item_builder.event(event, value)
                        elif prefix == "result.paginationToken" and event in {
                            "string",
                            "number",
                            "null",
                        }:
                            pagination_token = None if value is None else str(value)
                        elif prefix == "error" and event == "start_map":
                            error_seen = True
                        elif prefix == "error.code" and event in {
                            "string",
                            "number",
                            "null",
                        }:
                            error_code = value
                except JSONError as exc:
                    raise self._terminal(
                        ProviderMalformedResponseError,
                        f"Helius {method} returned invalid JSON; URL redacted",
                        call_retries,
                    ) from exc

                if item_builder is not None:
                    raise self._terminal(
                        ProviderMalformedResponseError,
                        "Helius history transaction stream was truncated",
                        call_retries,
                    )
                if error_seen:
                    if error_code == -32029:
                        if attempt >= self.max_retries:
                            raise self._terminal(
                                ProviderRateLimitError,
                                f"Helius {method} rate limit persisted after {attempt + 1} attempts",
                                call_retries,
                            )
                        call_retries += 1
                        self._backoff(attempt)
                        continue
                    raise self._terminal(
                        ProviderError,
                        f"Helius {method} RPC error {error_code}; provider message and URL redacted",
                        call_retries,
                    )
                if not result_seen or not data_seen:
                    raise self._terminal(
                        ProviderMalformedResponseError,
                        "Helius history response shape was not recognized",
                        call_retries,
                    )
                if self.investigation is not None:
                    self.investigation.record_page()
                    self.investigation.history_page_manifest_set(
                        cache_key,
                        {"paginationToken": pagination_token},
                        page_signatures,
                    )
                return pagination_token
            except (requests.Timeout, requests.RequestException, OSError, Urllib3HTTPError) as exc:
                if attempt < self.max_retries:
                    call_retries += 1
                    self._backoff(attempt)
                    continue
                raise self._terminal(
                    ProviderTransportError,
                    f"Helius {method} transport failed after {attempt + 1} attempts ({type(exc).__name__}); URL redacted",
                    call_retries,
                ) from exc
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass

        raise self._terminal(
            ProviderRateLimitError,
            f"Helius {method} retry budget exhausted",
            call_retries,
        )

    def _history(
        self,
        address: str,
        start: int,
        end: int,
        *,
        scope: str,
        token_balance_only: bool = True,
        transaction_consumer: Callable[[dict[str, Any]], None] | None = None,
        retain_transactions: bool = True,
        page_limit: int | None = None,
        page_limit_failure_category: str | None = None,
    ) -> ProviderBatch:
        retries_at_start = self.total_retry_count
        if page_limit is not None and int(page_limit) < 1:
            raise ProviderError("history page_limit must be positive")
        effective_page_limit = (
            self.max_pages if page_limit is None else min(self.max_pages, int(page_limit))
        )

        def batch(
            transactions: list[dict[str, Any]],
            complete: bool,
            limitation: str,
            pages: int,
            *,
            failure_category: str | None = None,
        ) -> ProviderBatch:
            return ProviderBatch(
                transactions,
                complete,
                "helius",
                scope,
                limitation,
                pages,
                retry_count=self.total_retry_count - retries_at_start,
                terminal_failure_reason=None if complete else limitation,
                failure_category=failure_category,
            )

        if token_balance_only and start < TOKEN_ACCOUNT_INDEX_START:
            return batch(
                [],
                False,
                "requested window predates documented December 2022 token-account index coverage",
                0,
                failure_category="COVERAGE_EXHAUSTION",
            )
        transactions: dict[str, dict[str, Any]] = {}
        streamed_signatures: set[str] = set()
        pagination_token: str | None = None
        seen_tokens: set[str] = set()
        pages = 0

        def retained_transactions() -> list[dict[str, Any]]:
            return list(transactions.values()) if retain_transactions else []

        def consume_history_transaction(transaction: dict[str, Any]) -> None:
            signature = transaction_signature(transaction)
            if not signature:
                return
            if self.investigation is not None:
                self.investigation.observe_transaction(signature, transaction)
            if signature in streamed_signatures:
                return
            streamed_signatures.add(signature)
            if transaction_consumer is not None:
                transaction_consumer(transaction)
            if retain_transactions:
                transactions[signature] = transaction

        while True:
            if pages >= effective_page_limit:
                if page_limit is None:
                    limitation = f"pagination stopped at configured max_pages={self.max_pages}"
                    failure_category = "COVERAGE_EXHAUSTION"
                else:
                    limitation = (
                        f"graph-wallet history capped for wallet={address} at "
                        f"max_graph_wallet_pages={effective_page_limit}"
                    )
                    failure_category = page_limit_failure_category or "COVERAGE_EXHAUSTION"
                return batch(
                    retained_transactions(),
                    False,
                    limitation,
                    pages,
                    failure_category=failure_category,
                )
            options: dict[str, Any] = {
                "transactionDetails": "full",
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
                "sortOrder": "asc",
                # R11 parses this response incrementally, so the provider's
                # full 100-record page can be used without materializing the whole
                # jsonParsed response in Python memory.
                "limit": self.full_history_page_limit,
                "commitment": "finalized",
                "filters": {
                    "blockTime": {"gte": int(start), "lt": int(end)},
                    "status": "succeeded",
                },
            }
            if token_balance_only:
                options["filters"]["tokenAccounts"] = "balanceChanged"
            if pagination_token:
                options["paginationToken"] = pagination_token
            try:
                next_token = self._stream_history_page(
                    [address, options],
                    consume_history_transaction,
                )
            except ProviderBudgetExhausted as exc:
                # A signature/transaction budget can fire after part of the
                # current provider page has already been consumed. Request/credit
                # budgets fire before the network request and therefore do not
                # count an additional page in the coverage receipt.
                exhausted_pages = pages + (
                    1 if exc.budget in {"max_signatures", "max_transactions"} else 0
                )
                return batch(
                    retained_transactions(),
                    False,
                    str(exc),
                    exhausted_pages,
                    failure_category=exc.category,
                )
            pages += 1
            if not next_token:
                break
            next_token = str(next_token)
            if next_token in seen_tokens:
                return batch(
                    retained_transactions(),
                    False,
                    "provider repeated a pagination token",
                    pages,
                    failure_category="COVERAGE_EXHAUSTION",
                )
            seen_tokens.add(next_token)
            pagination_token = next_token
        final_transactions = (
            sorted(transactions.values(), key=lambda row: int(row.get("blockTime") or 0))
            if retain_transactions
            else []
        )
        return batch(
            final_transactions,
            True,
            "Complete only under Helius indexed-address coverage; not independently audited against every Solana block.",
            pages,
        )

    def get_token_events(self, mint: str, start: int, end: int) -> ProviderBatch:
        return self._history(mint, start, end, scope="Helius indexed mint-associated transaction history")

    def get_wallet_events(self, wallet: str, mint: str, start: int, end: int) -> ProviderBatch:
        del mint
        return self._history(wallet, start, end, scope="Helius wallet plus owned token-account history")

    def get_wallet_activity(self, wallet: str, start: int, end: int) -> ProviderBatch:
        return self._history(
            wallet,
            start,
            end,
            scope=(
                "Helius complete indexed address activity for cross-asset graph discovery "
                f"wallet={wallet}"
            ),
            token_balance_only=False,
        )

    def stream_token_events(
        self,
        mint: str,
        start: int,
        end: int,
        consumer: Callable[[dict[str, Any]], None],
    ) -> ProviderBatch:
        return self._history(
            mint,
            start,
            end,
            scope="Helius indexed mint-associated transaction history",
            transaction_consumer=consumer,
            retain_transactions=False,
        )

    def stream_wallet_events(
        self,
        wallet: str,
        mint: str,
        start: int,
        end: int,
        consumer: Callable[[dict[str, Any]], None],
    ) -> ProviderBatch:
        del mint
        return self._history(
            wallet,
            start,
            end,
            scope="Helius wallet plus owned token-account history",
            transaction_consumer=consumer,
            retain_transactions=False,
        )

    def stream_wallet_activity(
        self,
        wallet: str,
        start: int,
        end: int,
        consumer: Callable[[dict[str, Any]], None],
    ) -> ProviderBatch:
        return self._history(
            wallet,
            start,
            end,
            scope=(
                "Helius complete indexed address activity for cross-asset graph discovery "
                f"wallet={wallet}"
            ),
            token_balance_only=False,
            transaction_consumer=consumer,
            retain_transactions=False,
        )

    def stream_wallet_events_bounded(
        self,
        wallet: str,
        mint: str,
        start: int,
        end: int,
        consumer: Callable[[dict[str, Any]], None],
        *,
        max_pages: int,
    ) -> ProviderBatch:
        del mint
        return self._history(
            wallet,
            start,
            end,
            scope=(
                "Helius bounded token-balance-changing wallet history for target-lineage tracing "
                f"wallet={wallet}"
            ),
            token_balance_only=True,
            transaction_consumer=consumer,
            retain_transactions=False,
            page_limit=max_pages,
            page_limit_failure_category="HIGH_ACTIVITY_TARGET_WALLET",
        )

    def stream_wallet_target_transfers_bounded(
        self,
        wallet: str,
        mint: str,
        start: int,
        end: int,
        consumer: Callable[[NormalizedEvent], None],
        *,
        max_pages: int,
    ) -> ProviderBatch:
        """Stream only parsed transfers for the requested mint.

        This is the normal-product downstream lineage lane. It intentionally
        avoids full jsonParsed transaction history once the seed relationship
        scan has established a linked wallet.
        """

        if int(max_pages) < 1:
            raise ProviderError("target transfer max_pages must be positive")
        retries_at_start = self.total_retry_count
        effective_page_limit = min(self.max_pages, int(max_pages))
        pages = 0
        pagination_token: str | None = None
        seen_tokens: set[str] = set()
        seen_rows: set[tuple[str, str, str, str, str, str, str, str]] = set()

        def result_batch(
            complete: bool, limitation: str, *, failure_category: str | None = None
        ) -> ProviderBatch:
            return ProviderBatch(
                [],
                complete,
                "helius",
                f"Helius mint-filtered parsed target-token transfers wallet={wallet} mint={mint}",
                limitation,
                pages,
                retry_count=self.total_retry_count - retries_at_start,
                terminal_failure_reason=None if complete else limitation,
                failure_category=failure_category,
            )

        while True:
            if pages >= effective_page_limit:
                return result_batch(
                    False,
                    (
                        f"target-token transfer history capped for wallet={wallet} at "
                        f"max_graph_wallet_pages={effective_page_limit}"
                    ),
                    failure_category="HIGH_ACTIVITY_TARGET_WALLET",
                )
            options: dict[str, Any] = {
                "mint": mint,
                "limit": min(100, self.full_history_page_limit),
                "sortOrder": "asc",
                # getTransfersByAddress V1 excludes failed transactions already.
                # Keep the config to parameters accepted by the transfer-specific
                # RPC instead of reusing gTFA-only status/token-account filters.
                "filters": {
                    "blockTime": {"gte": int(start), "lt": int(end)},
                },
            }
            if pagination_token:
                options["paginationToken"] = pagination_token
            try:
                result = self._call("getTransfersByAddress", [wallet, options])
            except ProviderBudgetExhausted as exc:
                return result_batch(False, str(exc), failure_category=exc.category)
            if not isinstance(result, Mapping):
                raise self._terminal(
                    ProviderMalformedResponseError,
                    "Helius getTransfersByAddress response shape was not recognized",
                    self.total_retry_count - retries_at_start,
                )
            rows = result.get("data")
            if not isinstance(rows, list):
                raise self._terminal(
                    ProviderMalformedResponseError,
                    "Helius getTransfersByAddress data was not an array",
                    self.total_retry_count - retries_at_start,
                )
            pages += 1
            if self.investigation is not None:
                self.investigation.record_page()
            try:
                for row in rows:
                    if not isinstance(row, Mapping):
                        raise self._terminal(
                            ProviderMalformedResponseError,
                            "Helius getTransfersByAddress transfer row was not an object",
                            self.total_retry_count - retries_at_start,
                        )
                    signature = str(row.get("signature") or "")
                    if not signature:
                        raise self._terminal(
                            ProviderMalformedResponseError,
                            "Helius getTransfersByAddress transfer row omitted signature",
                            self.total_retry_count - retries_at_start,
                        )
                    if self.investigation is not None:
                        self.investigation.observe_signature(signature)
                    key = (
                        signature,
                        str(row.get("transactionIdx") or ""),
                        str(row.get("instructionIdx") or ""),
                        str(row.get("innerInstructionIdx") or ""),
                        str(row.get("fromUserAccount") or ""),
                        str(row.get("toUserAccount") or ""),
                        str(row.get("amount") or ""),
                        str(row.get("type") or ""),
                    )
                    if key in seen_rows:
                        continue
                    seen_rows.add(key)
                    event = normalized_target_transfer_event(row, mint)
                    if event is not None:
                        consumer(event)
            except ProviderBudgetExhausted as exc:
                return result_batch(False, str(exc), failure_category=exc.category)

            next_token = result.get("paginationToken")
            if next_token in {None, ""}:
                return result_batch(
                    True,
                    (
                        "Complete under Helius getTransfersByAddress mint/time/status filter coverage; "
                        "not independently audited against every Solana block."
                    ),
                )
            next_token = str(next_token)
            if next_token in seen_tokens:
                return result_batch(
                    False,
                    "Helius getTransfersByAddress repeated a pagination token",
                    failure_category="COVERAGE_EXHAUSTION",
                )
            seen_tokens.add(next_token)
            pagination_token = next_token

    def stream_wallet_activity_bounded(
        self,
        wallet: str,
        start: int,
        end: int,
        consumer: Callable[[dict[str, Any]], None],
        *,
        max_pages: int,
    ) -> ProviderBatch:
        return self._history(
            wallet,
            start,
            end,
            scope=(
                "Helius complete indexed address activity for cross-asset graph discovery "
                f"wallet={wallet}"
            ),
            token_balance_only=False,
            transaction_consumer=consumer,
            retain_transactions=False,
            page_limit=max_pages,
            page_limit_failure_category="HIGH_ACTIVITY_HUB",
        )

    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        if self.investigation is not None:
            hit, cached = self.investigation.transaction_get(signature)
            if hit:
                self.investigation.record_provider_request_avoided()
                return cached
        result = self._call(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0}],
        )
        if self.investigation is not None:
            self.investigation.observe_transaction(signature, result)
        return result

    def get_wallet_token_balance(self, wallet: str, mint: str) -> int:
        return int(self.rpc.get_owner_token_accounts(wallet, mint).total)

    def get_metadata(self, mint: str) -> dict[str, Any] | None:
        try:
            return self._call("getAsset", {"id": mint, "displayOptions": {"showFungible": True}})
        except ProviderError:
            return None

    def get_all_token_accounts(self, mint: str) -> list[dict[str, Any]]:
        accounts: list[dict[str, Any]] = []
        page = 1
        total: int | None = None
        while page <= self.max_pages:
            result = self._call("getTokenAccounts", {"mint": mint, "page": page, "limit": 1000})
            if self.investigation is not None:
                self.investigation.record_page()
            rows = result.get("token_accounts", result.get("items", [])) if isinstance(result, dict) else []
            if not isinstance(rows, list):
                raise self._terminal(
                    ProviderMalformedResponseError,
                    "Helius getTokenAccounts response shape was not recognized",
                    0,
                )
            accounts.extend(row for row in rows if isinstance(row, dict))
            if total is None and isinstance(result, dict) and result.get("total") is not None:
                total = int(result["total"])
            if not rows or (total is not None and len(accounts) >= total) or len(rows) < 1000:
                return accounts
            page += 1
        raise CoverageError(f"holder pagination exceeded max_pages={self.max_pages}")


def transaction_signature(transaction: Mapping[str, Any]) -> str | None:
    return tx_classifier.transaction_signature(transaction)


def account_keys(transaction: Mapping[str, Any]) -> list[str]:
    return tx_classifier.account_keys(transaction)


def signer_keys(transaction: Mapping[str, Any]) -> set[str]:
    return set(tx_classifier.signer_keys(transaction))


def all_instructions(transaction: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [instruction for instruction, _inner in tx_classifier.iter_instructions(transaction)]


def token_balance_rows(transaction: Mapping[str, Any], side: str) -> dict[tuple[str, str], tuple[str | None, int, int]]:
    return tx_classifier.token_balance_rows(transaction, side)


def owner_token_deltas(transaction: Mapping[str, Any]) -> dict[str, dict[str, tuple[int, int]]]:
    return tx_classifier.owner_token_deltas(transaction)


def _quote_for_wallet(
    transaction: Mapping[str, Any], wallet: str, target_mint: str, deltas: Mapping[str, Mapping[str, tuple[int, int]]]
) -> tuple[str | None, str | None, int | None, int | None, str | None]:
    keys = account_keys(transaction)
    meta = transaction.get("meta") or {}
    pre_balances = meta.get("preBalances", []) or []
    post_balances = meta.get("postBalances", []) or []
    if wallet in keys:
        index = keys.index(wallet)
        if index < len(pre_balances) and index < len(post_balances):
            native_delta = int(post_balances[index]) - int(pre_balances[index])
            if index == 0:
                native_delta += int(meta.get("fee") or 0)
            if native_delta > 0:
                return "SOL", "SOL", native_delta, 9, None
    positive_quotes = [
        (mint, amount, decimals)
        for mint, (amount, decimals) in deltas.get(wallet, {}).items()
        if mint != target_mint and amount > 0
    ]
    if not positive_quotes:
        return None, None, None, None, None
    priority = {WSOL_MINT: 0, USDC_MINT: 1, USDT_MINT: 2}
    mint, amount, decimals = sorted(positive_quotes, key=lambda row: (priority.get(row[0], 10), -row[1]))[0]
    symbol = {WSOL_MINT: "WSOL", USDC_MINT: "USDC", USDT_MINT: "USDT"}.get(mint, mint[:5])
    usd = None
    if mint in {USDC_MINT, USDT_MINT}:
        usd = format(Decimal(amount) / (Decimal(10) ** decimals), "f")
    return mint, symbol, amount, decimals, usd


def _destination_for_delta(
    target_deltas: Mapping[str, tuple[int, int]], wallet: str, negative_amount: int
) -> str | None:
    destinations = [owner for owner, (delta, _decimals) in target_deltas.items() if owner != wallet and delta == negative_amount]
    return destinations[0] if len(destinations) == 1 else None


@dataclass(frozen=True)
class ConcreteTokenTransfer:
    source_owner: str
    destination_owner: str
    amount_raw: int
    source_token_account: str
    destination_token_account: str
    token_program: str


def _parsed_transfer_amount(info: Mapping[str, Any]) -> int | None:
    value = info.get("amount")
    token_amount = info.get("tokenAmount")
    if value is None and isinstance(token_amount, Mapping):
        value = token_amount.get("amount")
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def concrete_token_transfer(
    transaction: Mapping[str, Any], mint: str, source_owner: str, source_decrease_raw: int
) -> ConcreteTokenTransfer | None:
    """Return one instruction-proven owner-to-owner target-token transfer.

    Balance correlation alone is never sufficient.  The parsed SPL instruction
    must identify the exact source/destination token accounts and amount, and
    the corresponding account and owner deltas must match without fees or
    unexplained co-mingled movement.
    """

    if source_decrease_raw <= 0:
        return None
    autopsy = tx_classifier.autopsy_transaction(transaction)
    if autopsy is None or autopsy.failed:
        return None
    candidates: set[ConcreteTokenTransfer] = set()
    for transfer in autopsy.token_transfers:
        if transfer.mint != mint or transfer.source_owner != source_owner:
            continue
        if transfer.amount_raw != source_decrease_raw:
            continue
        if transfer.source_delta_raw != -transfer.amount_raw:
            continue
        if transfer.destination_delta_raw != transfer.amount_raw:
            continue
        if transfer.destination_owner == source_owner:
            continue
        candidates.add(
            ConcreteTokenTransfer(
                transfer.source_owner,
                transfer.destination_owner,
                transfer.amount_raw,
                transfer.source_token_account,
                transfer.destination_token_account,
                transfer.token_program,
            )
        )
    return next(iter(candidates)) if len(candidates) == 1 else None


def is_concrete_transfer_event(event: NormalizedEvent) -> bool:
    """Whether an event contains the full normalized transfer-edge proof."""

    return bool(
        event.event_type == "TRANSFER"
        and event.wallet
        and event.destination
        and event.wallet != event.destination
        and event.token_amount_raw > 0
        and event.token_delta_raw == -event.token_amount_raw
        and event.signature
        and event.timestamp
        and event.block_time > 0
        and event.source_token_account
        and event.destination_token_account
    )


def _market_context(
    transaction: Mapping[str, Any],
) -> tuple[set[str], set[str], str, str | None, bool, bool]:
    autopsy = tx_classifier.autopsy_transaction(transaction)
    instructions = all_instructions(transaction)
    parsed_types = {
        str(item.get("parsed", {}).get("type", "")).lower()
        for item in instructions
        if isinstance(item.get("parsed"), dict)
    }
    program_ids = set(autopsy.program_ids) if autopsy else {
        str(item.get("programId")) for item in instructions if item.get("programId")
    }
    labels = [KNOWN_PROGRAMS[program] for program in program_ids if program in KNOWN_PROGRAMS]
    router = next((label for label in labels if label in ROUTER_PROGRAMS), None)
    market_programs = program_ids - BENIGN_TRANSFER_PROGRAMS
    venue = next((label for label in labels if label not in ROUTER_PROGRAMS), None)
    if venue is None and market_programs:
        venue = sorted(market_programs)[0]
    routed = router is not None or len(market_programs) > 1
    return parsed_types, program_ids, venue or "UNKNOWN", router, routed, bool(autopsy and autopsy.market_context)


def normalize_transaction(
    transaction: Mapping[str, Any], mint: str, *, include_unknown_increases: bool = False
) -> list[NormalizedEvent]:
    meta = transaction.get("meta") or {}
    if meta.get("err") is not None:
        return []
    signature = transaction_signature(transaction)
    block_time = int(transaction.get("blockTime") or 0)
    if not signature or not block_time:
        return []
    timestamp = datetime.fromtimestamp(block_time, timezone.utc).isoformat().replace("+00:00", "Z")
    deltas = owner_token_deltas(transaction)
    target_deltas = {owner: by_mint[mint] for owner, by_mint in deltas.items() if mint in by_mint}
    if not target_deltas:
        return []
    signers = signer_keys(transaction)
    instructions = all_instructions(transaction)
    parsed_types, program_ids, venue, router, routed, _behavioral_market = _market_context(transaction)
    logs = "\n".join(str(row) for row in (meta.get("logMessages") or []))
    # Preserve the historical proof standard: behavior-only inference is useful
    # context, but it cannot override an instruction-proven token transfer and
    # turn that transfer into a SELL without explicit market evidence.
    market = bool(
        parsed_types.intersection({"sell", "swap", "swapbasein", "swapbaseout", "route", "sharedaccountsroute"})
        or re.search(r"\binstruction:\s*(?:sell|swap|route)", logs, flags=re.IGNORECASE)
        or program_ids.intersection(KNOWN_PROGRAMS)
    )
    liquidity = bool(
        any("liquidity" in kind or kind in {"deposit", "withdraw"} for kind in parsed_types)
        or re.search(r"\binstruction:\s*(?:add|remove|increase|decrease).*liquidity", logs, flags=re.IGNORECASE)
    )
    mint_event = bool(parsed_types.intersection({"mintto", "minttochecked"}))
    burn_event = bool(parsed_types.intersection({"burn", "burnchecked"}))
    events: list[NormalizedEvent] = []

    for wallet, (delta, _decimals) in target_deltas.items():
        if delta >= 0:
            continue
        candidate_destination = _destination_for_delta(target_deltas, wallet, -delta)
        concrete_transfer = concrete_token_transfer(transaction, mint, wallet, -delta)
        quote_mint, quote_symbol, quote_amount, quote_decimals, quote_usd = _quote_for_wallet(
            transaction, wallet, mint, deltas
        )
        if burn_event:
            event_type, evidence = "BURN", "parsed SPL burn instruction"
        elif liquidity:
            event_type, evidence = "LIQUIDITY_EVENT", "liquidity instruction or log evidence"
        elif market and wallet in signers:
            event_type, evidence = "SELL", "explicit swap/sell/route evidence with signer-owned token decrease"
        elif concrete_transfer is not None:
            event_type, evidence = (
                "TRANSFER",
                "parsed SPL transfer instruction matched exact source/destination token-account and owner deltas",
            )
        else:
            event_type, evidence = (
                "UNKNOWN",
                "token decrease lacks signer-owned sale evidence or sufficient transfer, burn, or liquidity evidence",
            )
        events.append(
            NormalizedEvent(
                timestamp,
                block_time,
                signature,
                mint,
                event_type,
                wallet,
                delta,
                -delta,
                destination=(
                    concrete_transfer.destination_owner
                    if concrete_transfer is not None and event_type == "TRANSFER"
                    else None
                ),
                candidate_destination=(
                    candidate_destination if concrete_transfer is None and event_type in {"UNKNOWN", "LIQUIDITY_EVENT"} else None
                ),
                source_token_account=(
                    concrete_transfer.source_token_account
                    if concrete_transfer is not None and event_type == "TRANSFER"
                    else None
                ),
                destination_token_account=(
                    concrete_transfer.destination_token_account
                    if concrete_transfer is not None and event_type == "TRANSFER"
                    else None
                ),
                quote_mint=quote_mint if event_type == "SELL" else None,
                quote_symbol=quote_symbol if event_type == "SELL" else None,
                quote_amount_raw=quote_amount if event_type == "SELL" else None,
                quote_decimals=quote_decimals if event_type == "SELL" else None,
                quote_usd=quote_usd if event_type == "SELL" else None,
                venue=venue if event_type in {"SELL", "LIQUIDITY_EVENT"} else None,
                router=router if event_type == "SELL" else None,
                routed=routed if event_type == "SELL" else None,
                direct=(not routed) if event_type == "SELL" else (True if event_type == "TRANSFER" else None),
                evidence=evidence,
            )
        )

    deterministic_transfer_destinations = {
        event.destination for event in events if event.event_type == "TRANSFER" and event.destination
    }
    for wallet, (delta, _decimals) in target_deltas.items():
        if delta <= 0:
            continue
        if market and wallet in signers:
            event_type, evidence = "BUY", "explicit swap/route evidence with signer-owned token increase"
        elif mint_event:
            event_type, evidence = "MINT", "parsed SPL mint-to instruction"
        elif include_unknown_increases and wallet not in deterministic_transfer_destinations:
            event_type, evidence = (
                "UNKNOWN",
                "strict inventory mode retained an otherwise unclassified token increase",
            )
        else:
            continue
        events.append(
            NormalizedEvent(
                timestamp,
                block_time,
                signature,
                mint,
                event_type,
                wallet,
                delta,
                delta,
                venue=venue if event_type == "BUY" else None,
                router=router if event_type == "BUY" else None,
                routed=routed if event_type == "BUY" else None,
                direct=(not routed) if event_type == "BUY" else None,
                evidence=evidence,
            )
        )
    return events


def normalize_transactions(
    transactions: Iterable[Mapping[str, Any]], mint: str, *, include_unknown_increases: bool = False
) -> list[NormalizedEvent]:
    seen_signatures: set[str] = set()
    events: list[NormalizedEvent] = []
    for transaction in transactions:
        signature = transaction_signature(transaction)
        if not signature or signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        events.extend(normalize_transaction(transaction, mint, include_unknown_increases=include_unknown_increases))
    return sorted(events, key=lambda event: (event.block_time, event.signature, event.wallet or ""))


def apply_transfer_links(events: Sequence[NormalizedEvent], trace_depth: int) -> dict[str, dict[str, int]]:
    for event in events:
        event.linked_by_transfer = False
        event.linked_from = []
    if trace_depth == 0:
        return {}
    lots: dict[str, list[dict[str, Any]]] = {}

    def consume(wallet: str, amount: int) -> set[str]:
        linked_sources: set[str] = set()
        remaining = amount
        for lot in lots.get(wallet, []):
            if remaining <= 0:
                break
            used = min(int(lot["remaining"]), remaining)
            if used:
                lot["remaining"] -= used
                remaining -= used
                linked_sources.add(str(lot["source"]))
        return linked_sources

    for event in sorted(events, key=lambda row: (row.block_time, row.signature)):
        if not event.wallet:
            continue
        consumed_sources: set[str] = set()
        if event.token_delta_raw < 0:
            consumed_sources = consume(event.wallet, event.token_amount_raw)
            if event.event_type == "SELL" and consumed_sources:
                event.linked_by_transfer = True
                event.linked_from = sorted(consumed_sources)
        if is_concrete_transfer_event(event):
            lots.setdefault(event.destination, []).append(
                {"source": event.wallet, "remaining": event.token_amount_raw, "depth": 1}
            )
    linked: dict[str, dict[str, int]] = {}
    for wallet, wallet_lots in lots.items():
        for lot in wallet_lots:
            if lot["depth"] <= trace_depth and lot["remaining"] > 0:
                source = str(lot["source"])
                linked.setdefault(source, {})[wallet] = linked.setdefault(source, {}).get(wallet, 0) + int(
                    lot["remaining"]
                )
    return linked


VERIFY_EXIT_STATUSES = frozenset({"VERIFIED_OUT", "NOT_OUT", "UNRESOLVED", "INSUFFICIENT_DATA"})


@dataclass
class _InventoryLot:
    """A conservative FIFO lot known to originate at the seller wallet."""

    remaining: int
    depth: int
    path: list[str]
    source_signature: str


def discover_traced_wallets(
    events: Sequence[NormalizedEvent], root_wallet: str, trace_depth: int
) -> dict[str, dict[str, Any]]:
    """Discover deterministic, time-ordered transfer descendants.

    This intentionally over-approximates the wallets that need fresh balance
    reconciliation.  Attribution of token amounts is performed separately by
    :func:`verify_exit`; discovering a wallet never claims common control.
    """

    if not 0 <= trace_depth <= 3:
        raise HistoryError("--trace-depth must be between 0 and 3")
    discovered: dict[str, dict[str, Any]] = {
        root_wallet: {"depth": 0, "path": [root_wallet], "first_seen": None}
    }
    for event in sorted(events, key=lambda row: (row.block_time, row.signature, row.wallet or "")):
        if not is_concrete_transfer_event(event):
            continue
        source = discovered.get(event.wallet)
        if source is None or int(source["depth"]) >= trace_depth or event.destination == root_wallet:
            continue
        candidate_depth = int(source["depth"]) + 1
        current = discovered.get(event.destination)
        if current is None or candidate_depth < int(current["depth"]):
            discovered[event.destination] = {
                "depth": candidate_depth,
                "path": [*source["path"], event.destination],
                "first_seen": event.block_time,
            }
    return discovered


def _verification_metric(events: Sequence[NormalizedEvent], event_type: str) -> dict[str, int]:
    selected = [event for event in events if event.event_type == event_type]
    return {"count": len(selected), "amount_raw": sum(event.token_amount_raw for event in selected)}


def verify_exit(
    events: Sequence[NormalizedEvent],
    metadata: TokenMetadata,
    root_wallet: str,
    current_balances: Mapping[str, int | None],
    *,
    coverage_complete: bool,
    coverage_scope: str,
    coverage_limitation: str,
    trace_depth: int,
    window_start: int | None = None,
    window_end: int | None = None,
) -> dict[str, Any]:
    """Strictly verify whether observed seller inventory is gone.

    A raw amount greater than zero is material.  ``VERIFIED_OUT`` is available
    only when provider coverage is complete, every in-scope wallet has a fresh
    balance, all observed inventory reconciles, and no reacquisition or
    ambiguous/depth-truncated transfer branch remains.
    """

    if not 0 <= trace_depth <= 3:
        raise HistoryError("--trace-depth must be between 0 and 3")
    ordered = sorted(events, key=lambda row: (row.block_time, row.signature, row.wallet or ""))
    root_events = [event for event in ordered if event.wallet == root_wallet]
    buy_amounts_by_delivery: dict[tuple[str, str], int] = {}
    for event in ordered:
        if event.wallet and event.event_type == "BUY" and event.token_delta_raw > 0:
            key = (event.signature, event.wallet)
            buy_amounts_by_delivery[key] = buy_amounts_by_delivery.get(key, 0) + event.token_amount_raw
    incoming_amounts_by_delivery: dict[tuple[str, str], int] = {}
    for event in ordered:
        if is_concrete_transfer_event(event) and event.destination:
            key = (event.signature, event.destination)
            incoming_amounts_by_delivery[key] = (
                incoming_amounts_by_delivery.get(key, 0) + event.token_amount_raw
            )
    market_settlement_deliveries = {
        key
        for key, amount in incoming_amounts_by_delivery.items()
        if amount > 0 and amount == buy_amounts_by_delivery.get(key)
    }

    def is_market_settlement_transfer(event: NormalizedEvent) -> bool:
        return bool(
            is_concrete_transfer_event(event)
            and event.destination
            and (event.signature, event.destination) in market_settlement_deliveries
        )

    raw_root_incoming = [
        event
        for event in ordered
        if is_concrete_transfer_event(event)
        and event.destination == root_wallet
        and event.wallet != root_wallet
    ]
    excluded_market_settlement_transfers = [
        event for event in raw_root_incoming if is_market_settlement_transfer(event)
    ]
    root_incoming = [
        event for event in raw_root_incoming if not is_market_settlement_transfer(event)
    ]
    root_outgoing = [
        event
        for event in ordered
        if is_concrete_transfer_event(event)
        and event.wallet == root_wallet
        and event.destination != root_wallet
    ]
    root_sales = [event for event in root_events if event.event_type == "SELL"]
    root_burns = [event for event in root_events if event.event_type == "BURN"]
    root_reductions = [
        event
        for event in root_events
        if event.token_delta_raw < 0
        and event.event_type in {"SELL", "TRANSFER", "BURN", "LIQUIDITY_EVENT", "UNKNOWN"}
    ]
    first_reduction_key = (
        min((event.block_time, event.signature) for event in root_reductions) if root_reductions else None
    )

    root_current = current_balances.get(root_wallet)
    historical_net_delta = sum(event.token_delta_raw for event in root_events)
    historical_net_delta += sum(event.token_amount_raw for event in root_incoming)
    reconstructed_start = None if root_current is None else int(root_current) - historical_net_delta
    root_acquisitions = [event for event in root_events if event.token_delta_raw > 0]
    observed_inventory = (
        None
        if reconstructed_start is None
        else max(reconstructed_start, 0)
        + sum(event.token_amount_raw for event in root_acquisitions)
        + sum(event.token_amount_raw for event in root_incoming)
    )

    unresolved: list[dict[str, Any]] = []
    root_reacquisitions: list[dict[str, Any]] = []
    linked_wallet_increases: list[dict[str, Any]] = []
    unproven_relationships: list[dict[str, Any]] = []
    trace_edges: list[dict[str, Any]] = []

    def add_unresolved(kind: str, event: NormalizedEvent | None, detail: str, **extra: Any) -> None:
        row: dict[str, Any] = {"kind": kind, "detail": detail}
        if event is not None:
            row.update(
                {
                    "signature": event.signature,
                    "block_time": event.block_time,
                    "wallet": event.wallet,
                    "destination": event.destination,
                    "candidate_destination": event.candidate_destination,
                    "amount_raw": event.token_amount_raw,
                }
            )
        row.update(extra)
        unresolved.append(row)

    def add_inventory_increase(
        target: list[dict[str, Any]],
        event: NormalizedEvent,
        wallet: str,
        amount: int,
        kind: str,
        *,
        path: Sequence[str] | None = None,
    ) -> None:
        target.append(
            {
                "wallet": wallet,
                "amount_raw": int(amount),
                "event_type": kind,
                "signature": event.signature,
                "block_time": event.block_time,
                "timestamp": event.timestamp,
                "source": event.wallet if event.destination == wallet else None,
                "path": list(path or []),
            }
        )

    def relationship_type(event: NormalizedEvent) -> str:
        if window_start is not None and event.block_time < window_start:
            return "PREEXISTING_RELATIONSHIP"
        return "IN_WINDOW_TRANSFER"

    def add_trace_edge(
        event: NormalizedEvent, attributable_amount: int, depth: int, path: Sequence[str]
    ) -> None:
        if not is_concrete_transfer_event(event):
            raise AssertionError("trace edge requires concrete normalized transfer evidence")
        trace_edges.append(
            {
                "relationship_type": relationship_type(event),
                "source_owner": event.wallet,
                "destination_owner": event.destination,
                "amount_raw": int(attributable_amount),
                "transaction_transfer_amount_raw": event.token_amount_raw,
                "signature": event.signature,
                "timestamp": event.timestamp,
                "block_time": event.block_time,
                "source_token_account": event.source_token_account,
                "destination_token_account": event.destination_token_account,
                "depth": depth,
                "path": list(path),
                "evidence": event.evidence,
                "linked_by_transfer": True,
                "common_control_inferred": False,
            }
        )

    for event in ordered:
        if (
            event.candidate_destination
            and event.event_type in {"UNKNOWN", "LIQUIDITY_EVENT"}
            and event.token_delta_raw < 0
        ):
            unproven_relationships.append(
                {
                    "source_owner": event.wallet,
                    "candidate_destination_owner": event.candidate_destination,
                    "amount_raw": event.token_amount_raw,
                    "signature": event.signature,
                    "timestamp": event.timestamp,
                    "block_time": event.block_time,
                    "reason": "matching owner balance delta without one exact normalized target-token transfer instruction",
                    "creates_trace_edge": False,
                }
            )

    if reconstructed_start is not None and reconstructed_start < 0:
        add_unresolved(
            "ROOT_RECONCILIATION_MISMATCH",
            None,
            "fresh balance minus observed net change implies a negative window-start inventory",
            reconstructed_start_raw=reconstructed_start,
        )

    if first_reduction_key is not None:
        for event in root_acquisitions:
            # blockTime is second-granularity and does not prove ordering
            # within the second.  Treat a same-second acquisition as a
            # potential reacquisition rather than choosing a favorable order.
            if event.block_time >= first_reduction_key[0]:
                add_inventory_increase(
                    root_reacquisitions, event, root_wallet, event.token_amount_raw, event.event_type
                )
        for event in root_incoming:
            if event.block_time >= first_reduction_key[0]:
                add_inventory_increase(
                    root_reacquisitions, event, root_wallet, event.token_amount_raw, "TRANSFER_IN"
                )

    for event in root_events:
        if event.token_delta_raw >= 0:
            continue
        if event.event_type == "UNKNOWN":
            add_unresolved(
                "AMBIGUOUS_ROOT_REDUCTION",
                event,
                "root-wallet decrease could not be proven as a sale, transfer, or burn",
            )
        elif event.event_type == "LIQUIDITY_EVENT":
            add_unresolved(
                "ROOT_LIQUIDITY_POSITION",
                event,
                "liquidity movement is not proof that beneficial inventory was sold or destroyed",
            )
        elif event.event_type == "TRANSFER" and not is_concrete_transfer_event(event):
            add_unresolved(
                "INVALID_TRANSFER_EVIDENCE",
                event,
                "normalized transfer is missing source/destination owner, token accounts, amount, signature, or timestamp",
            )

    lots: dict[str, list[_InventoryLot]] = {}
    traced: dict[str, dict[str, Any]] = {}
    linked_sales_count = 0
    linked_sales_amount = 0
    linked_burn_count = 0
    linked_burn_amount = 0
    linked_transfer_count = 0
    linked_transfer_amount = 0
    propagated_by_signature: dict[tuple[str, str], int] = {}

    def remember_trace(wallet: str, lot: _InventoryLot, block_time: int) -> None:
        current = traced.get(wallet)
        if current is None or lot.depth < int(current["depth"]):
            traced[wallet] = {
                "depth": lot.depth,
                "path": list(lot.path),
                "first_seen": block_time,
            }

    def add_lot(wallet: str, lot: _InventoryLot, block_time: int) -> None:
        lots.setdefault(wallet, []).append(lot)
        remember_trace(wallet, lot, block_time)

    def consume(wallet: str, amount: int) -> list[tuple[_InventoryLot, int]]:
        remaining = amount
        consumed: list[tuple[_InventoryLot, int]] = []
        for lot in lots.get(wallet, []):
            if remaining <= 0:
                break
            used = min(lot.remaining, remaining)
            if used > 0:
                lot.remaining -= used
                remaining -= used
                consumed.append((lot, used))
        return consumed

    for event in ordered:
        if not event.wallet:
            continue
        if is_market_settlement_transfer(event):
            continue
        if event.wallet == root_wallet:
            if event.event_type != "TRANSFER" or event.token_delta_raw >= 0:
                continue
            if not is_concrete_transfer_event(event):
                continue
            if window_end is not None and event.block_time >= window_end:
                add_unresolved(
                    "OUT_OF_WINDOW_TRANSFER",
                    event,
                    "transfer evidence is newer than the analyzed interval and cannot be represented as in-window",
                )
                continue
            if trace_depth == 0:
                add_unresolved(
                    "TRACE_DEPTH_EXHAUSTED",
                    event,
                    "root transfer leaves the requested trace boundary",
                    next_depth=1,
                )
                continue
            add_lot(
                event.destination,
                _InventoryLot(event.token_amount_raw, 1, [root_wallet, event.destination], event.signature),
                event.block_time,
            )
            add_trace_edge(event, event.token_amount_raw, 1, [root_wallet, event.destination])
            propagation_key = (event.signature, event.destination)
            propagated_by_signature[propagation_key] = (
                propagated_by_signature.get(propagation_key, 0) + event.token_amount_raw
            )
            if event.event_type == "TRANSFER":
                linked_transfer_count += 1
                linked_transfer_amount += event.token_amount_raw
            continue

        if event.token_delta_raw >= 0:
            continue
        consumed = consume(event.wallet, event.token_amount_raw)
        if not consumed:
            # This event did not consume inventory attributable to the seller.
            continue
        consumed_total = sum(amount for _lot, amount in consumed)
        if event.event_type == "SELL":
            linked_sales_count += 1
            linked_sales_amount += consumed_total
            continue
        if event.event_type == "BURN":
            linked_burn_count += 1
            linked_burn_amount += consumed_total
            continue
        if event.event_type == "TRANSFER":
            if not is_concrete_transfer_event(event):
                add_unresolved(
                    "INVALID_TRANSFER_EVIDENCE",
                    event,
                    "transfer-linked event lacks complete normalized transfer proof",
                    attributable_amount_raw=consumed_total,
                )
                continue
            if window_end is not None and event.block_time >= window_end:
                add_unresolved(
                    "OUT_OF_WINDOW_TRANSFER",
                    event,
                    "transfer-linked evidence is newer than the analyzed interval",
                    attributable_amount_raw=consumed_total,
                )
                continue
            if event.destination == root_wallet:
                # The incoming transfer was already recorded as a root reacquisition.
                for lot, amount in consumed:
                    add_trace_edge(
                        event,
                        amount,
                        lot.depth + 1,
                        [*lot.path, root_wallet],
                    )
                continue
            for lot, amount in consumed:
                next_depth = lot.depth + 1
                if next_depth > trace_depth:
                    add_unresolved(
                        "TRACE_DEPTH_EXHAUSTED",
                        event,
                        "transfer-linked inventory leaves the requested trace boundary",
                        attributable_amount_raw=amount,
                        next_depth=next_depth,
                        path=[*lot.path, event.destination],
                    )
                    continue
                add_lot(
                    event.destination,
                    _InventoryLot(amount, next_depth, [*lot.path, event.destination], event.signature),
                    event.block_time,
                )
                add_trace_edge(event, amount, next_depth, [*lot.path, event.destination])
                propagation_key = (event.signature, event.destination)
                propagated_by_signature[propagation_key] = (
                    propagated_by_signature.get(propagation_key, 0) + amount
                )
                linked_transfer_count += 1
                linked_transfer_amount += amount
            continue
        add_unresolved(
            "AMBIGUOUS_LINKED_REDUCTION",
            event,
            "transfer-linked inventory was consumed by an unproven disposition",
            attributable_amount_raw=consumed_total,
            disposition=event.event_type,
        )

    # Detect same-second and later acquisitions even when signature sorting
    # placed the acquisition before the transaction that first traced a
    # wallet.  The provider does not supply a reliable intra-second order here,
    # so strict verification resolves the ambiguity against VERIFIED_OUT.
    for event in ordered:
        if (
            event.wallet in traced
            and event.token_delta_raw > 0
            and event.block_time >= int(traced[event.wallet]["first_seen"])
        ):
            add_inventory_increase(
                linked_wallet_increases,
                event,
                str(event.wallet),
                event.token_amount_raw,
                event.event_type,
                path=traced[str(event.wallet)]["path"],
            )

    # Detect material transfers into an already-traced wallet that were not
    # inventory lots propagated from the seller.  This is conservative by
    # design: commingled reacquisition prevents a verified exit.
    for event in ordered:
        if (
            not is_concrete_transfer_event(event)
            or is_market_settlement_transfer(event)
            or event.destination == root_wallet
            or event.destination not in traced
            or event.wallet == event.destination
        ):
            continue
        trace = traced[event.destination]
        if event.block_time < int(trace["first_seen"]):
            continue
        # Use the amount actually propagated by the FIFO lot ledger.  This
        # keeps unrelated inventory moved by a traced wallet from being
        # mistaken for seller-origin inventory.
        attributable = propagated_by_signature.get((event.signature, event.destination), 0)
        excess = event.token_amount_raw - attributable
        if excess > 0:
            add_inventory_increase(
                linked_wallet_increases,
                event,
                event.destination,
                excess,
                "TRANSFER_IN",
                path=trace["path"],
            )

    traced_rows: list[dict[str, Any]] = []
    missing_balances: list[str] = []
    all_traced_current = 0
    attributable_remaining = 0
    for wallet, trace in sorted(traced.items(), key=lambda item: (int(item[1]["depth"]), item[0])):
        current = current_balances.get(wallet)
        lot_remaining = sum(lot.remaining for lot in lots.get(wallet, []))
        if current is None:
            missing_balances.append(wallet)
            attributable_current = None
        else:
            current = int(current)
            all_traced_current += max(current, 0)
            attributable_current = min(max(current, 0), lot_remaining)
            attributable_remaining += attributable_current
            if lot_remaining > current:
                add_unresolved(
                    "LINKED_RECONCILIATION_MISMATCH",
                    None,
                    "historical linked lot exceeds the fresh wallet balance without a proven disposition",
                    wallet=wallet,
                    historical_lot_remaining_raw=lot_remaining,
                    fresh_balance_raw=current,
                    path=trace["path"],
                )
        traced_rows.append(
            {
                "wallet": wallet,
                "depth": int(trace["depth"]),
                "path": list(trace["path"]),
                "fresh_current_inventory_raw": current,
                "historical_attributable_lot_raw": lot_remaining,
                "attributable_current_inventory_raw": attributable_current,
                "common_control_inferred": False,
            }
        )

    root_material = root_current is not None and int(root_current) > 0
    linked_material = any(
        row["fresh_current_inventory_raw"] is not None and int(row["fresh_current_inventory_raw"]) > 0
        for row in traced_rows
    )
    missing_root_balance = root_current is None
    has_observed_disposition = bool(root_reductions)
    traced_wallet_names = set(traced)
    unrelated_balance_wallets = sorted(set(current_balances) - {root_wallet} - traced_wallet_names)
    excluded_unrelated_balance = sum(
        max(int(current_balances[wallet] or 0), 0) for wallet in unrelated_balance_wallets
    )
    root_equation_balanced = bool(
        reconstructed_start is not None
        and root_current is not None
        and reconstructed_start + historical_net_delta == int(root_current)
    )
    unproven_relationships = [
        row
        for row in unproven_relationships
        if row.get("source_owner") == root_wallet
        or row.get("source_owner") in traced_wallet_names
        or row.get("candidate_destination_owner") == root_wallet
        or row.get("candidate_destination_owner") in traced_wallet_names
    ]
    if root_current is not None and not root_equation_balanced:
        add_unresolved(
            "ROOT_RECONCILIATION_EQUATION_FAILED",
            None,
            "root starting inventory equation did not reconcile using root-owned deltas only",
        )
    trace_edges.sort(
        key=lambda row: (int(row["block_time"]), str(row["signature"]), int(row["depth"]))
    )
    reasons: list[str] = []
    if root_material:
        reasons.append("material current inventory remains in the supplied wallet")
    if linked_material:
        reasons.append("material current inventory remains in a traced transfer-linked wallet")

    if root_material or linked_material:
        status = "NOT_OUT"
    elif not coverage_complete:
        status = "INSUFFICIENT_DATA"
        reasons.append("provider coverage is incomplete for the requested interval")
    elif missing_root_balance or missing_balances:
        status = "INSUFFICIENT_DATA"
        reasons.append("one or more required fresh current-chain balances are unavailable")
    elif observed_inventory is None or observed_inventory <= 0 or not has_observed_disposition:
        status = "INSUFFICIENT_DATA"
        reasons.append("the interval does not contain enough observed inventory and disposition evidence")
    elif root_reacquisitions:
        status = "UNRESOLVED"
        reasons.append("material target-token reacquisition entered the supplied root wallet")
    elif linked_wallet_increases:
        status = "UNRESOLVED"
        reasons.append("a traced wallet received separate inventory that is not attributable to the root transfer lot")
    elif unresolved:
        status = "UNRESOLVED"
        reasons.append("an unresolved transfer or disposition branch can invalidate an exit conclusion")
    else:
        status = "VERIFIED_OUT"
        reasons.append("all strict exit invariants passed for the analyzed interval and trace boundary")

    result = {
        "schema": "jeet-analyzer.verify-exit.v2",
        "status": status,
        "mint": metadata.mint,
        "token": asdict(metadata),
        "wallet": root_wallet,
        "trace_depth": trace_depth,
        "material_threshold_raw": 1,
        "coverage": {
            "complete": bool(coverage_complete),
            "scope": coverage_scope,
            "limitation": coverage_limitation,
        },
        "reconstructed_starting_inventory_raw": reconstructed_start,
        "observed_inventory_raw": observed_inventory,
        "historical_net_delta_raw": historical_net_delta,
        "fresh_current_inventory_raw": root_current,
        "confirmed_sales": {
            "count": len(root_sales) + linked_sales_count,
            "amount_raw": sum(event.token_amount_raw for event in root_sales) + linked_sales_amount,
            "root_count": len(root_sales),
            "root_amount_raw": sum(event.token_amount_raw for event in root_sales),
            "transfer_linked_count": linked_sales_count,
            "transfer_linked_amount_raw": linked_sales_amount,
        },
        "incoming_transfers": _verification_metric(root_incoming, "TRANSFER"),
        "excluded_market_settlement_transfers": _verification_metric(
            excluded_market_settlement_transfers, "TRANSFER"
        ),
        "outgoing_transfers": _verification_metric(root_outgoing, "TRANSFER"),
        "transfer_linked_movements": {
            "count": linked_transfer_count,
            "amount_raw": linked_transfer_amount,
        },
        "reacquisitions": {
            "scope": "ROOT_WALLET_ONLY",
            "count": len(root_reacquisitions),
            "amount_raw": sum(int(row["amount_raw"]) for row in root_reacquisitions),
            "events": root_reacquisitions,
        },
        "linked_wallet_increases": {
            "scope": "TRACED_WALLETS_ONLY_NOT_ROOT_REACQUISITION",
            "count": len(linked_wallet_increases),
            "amount_raw": sum(int(row["amount_raw"]) for row in linked_wallet_increases),
            "events": linked_wallet_increases,
        },
        "burns": {
            "count": len(root_burns) + linked_burn_count,
            "amount_raw": sum(event.token_amount_raw for event in root_burns) + linked_burn_amount,
            "root_count": len(root_burns),
            "root_amount_raw": sum(event.token_amount_raw for event in root_burns),
            "transfer_linked_count": linked_burn_count,
            "transfer_linked_amount_raw": linked_burn_amount,
        },
        "traced_wallets": traced_rows,
        "trace_edges": trace_edges,
        "in_window_transfer_edges": [
            row for row in trace_edges if row["relationship_type"] == "IN_WINDOW_TRANSFER"
        ],
        "preexisting_relationships": [
            row for row in trace_edges if row["relationship_type"] == "PREEXISTING_RELATIONSHIP"
        ],
        "unproven_inventory_relationships": unproven_relationships,
        "all_traced_current_inventory_raw": all_traced_current,
        "transfer_linked_inventory_raw": attributable_remaining,
        "reconciliation": {
            "root_equation": "window_start + observed_net_delta = fresh_current",
            "root_equation_balanced": root_equation_balanced,
            "root_start_nonnegative": reconstructed_start is not None and reconstructed_start >= 0,
            "root_owned_event_net_delta_raw": sum(event.token_delta_raw for event in root_events),
            "concrete_transfer_in_to_root_raw": sum(event.token_amount_raw for event in root_incoming),
            "market_settlement_transfer_in_excluded_raw": sum(
                event.token_amount_raw for event in excluded_market_settlement_transfers
            ),
            "linked_or_unrelated_wallet_balances_included_in_root_start_raw": 0,
            "excluded_unrelated_balance_wallets": unrelated_balance_wallets,
            "excluded_unrelated_wallet_inventory_raw": excluded_unrelated_balance,
            "missing_current_balance_wallets": ([root_wallet] if missing_root_balance else []) + missing_balances,
        },
        "unresolved_branches": unresolved,
        "reasons": reasons,
        "verified_out_invariants": {
            "provider_coverage_complete": bool(coverage_complete),
            "fresh_balances_complete": not missing_root_balance and not missing_balances,
            "no_material_root_inventory": root_current == 0,
            "no_material_traced_inventory": not linked_material,
            "no_material_reacquisition": not root_reacquisitions,
            "no_unattributed_linked_wallet_increase": not linked_wallet_increases,
            "no_unresolved_branch": not unresolved,
            "all_trace_edges_have_concrete_transfer_evidence": all(
                row.get("source_owner")
                and row.get("destination_owner")
                and row.get("amount_raw")
                and row.get("signature")
                and row.get("timestamp")
                and row.get("source_token_account")
                and row.get("destination_token_account")
                for row in trace_edges
            ),
            "root_start_excludes_linked_and_unrelated_balances": True,
            "observed_inventory_and_disposition_sufficient": bool(
                observed_inventory is not None and observed_inventory > 0 and has_observed_disposition
            ),
        },
    }
    if result["status"] not in VERIFY_EXIT_STATUSES:
        raise AssertionError("invalid verify-exit terminal status")
    return result


@dataclass(frozen=True)
class AnalysisConfig:
    top: int = 25
    concentration_top_n: int = 10
    dominant_threshold: Decimal = Decimal("60")
    still_loaded_pct: Decimal = Decimal("50")
    nearly_out_pct: Decimal = Decimal("10")
    jeet_out_pct: Decimal = Decimal("1")
    trace_depth: int = 1


def _status(remaining: Decimal | None, sells: int, config: AnalysisConfig) -> str:
    if remaining is None:
        return "UNKNOWN"
    if remaining <= config.jeet_out_pct:
        return "JEET_OUT"
    if remaining <= config.nearly_out_pct:
        return "NEARLY_OUT"
    if sells >= 2 and remaining < config.still_loaded_pct:
        return "DISTRIBUTING"
    return "STILL_LOADED"


def _interval_stats(block_times: Sequence[int]) -> tuple[str | None, str | None]:
    ordered = sorted(set(block_times))
    if len(ordered) < 2:
        return None, None
    intervals = [ordered[index] - ordered[index - 1] for index in range(1, len(ordered))]
    return f"{statistics.mean(intervals):.1f}", f"{statistics.median(intervals):.1f}"


def _quote_totals(events: Sequence[NormalizedEvent]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for event in events:
        if not event.quote_symbol or event.quote_amount_raw is None or event.quote_decimals is None:
            continue
        key = f"{event.quote_symbol}:{event.quote_mint or event.quote_symbol}"
        item = totals.setdefault(
            key,
            {"symbol": event.quote_symbol, "mint": event.quote_mint, "amount_raw": 0, "decimals": event.quote_decimals},
        )
        item["amount_raw"] += event.quote_amount_raw
    return totals


def _wallet_net_delta(events: Sequence[NormalizedEvent], wallet: str) -> int:
    net = sum(event.token_delta_raw for event in events if event.wallet == wallet)
    net += sum(
        event.token_amount_raw
        for event in events
        if event.event_type == "TRANSFER" and event.destination == wallet
    )
    return net


def analyze_sellers(
    events: Sequence[NormalizedEvent],
    metadata: TokenMetadata,
    current_balances: Mapping[str, int | None],
    *,
    coverage_complete: bool,
    config: AnalysisConfig,
) -> dict[str, Any]:
    if not coverage_complete:
        raise CoverageError("historical coverage is incomplete; seller rankings and status claims are disabled")
    linked = apply_transfer_links(events, config.trace_depth)
    sells = [event for event in events if event.event_type == "SELL" and event.wallet]
    by_wallet: dict[str, list[NormalizedEvent]] = {}
    for event in sells:
        by_wallet.setdefault(str(event.wallet), []).append(event)
    rows: list[dict[str, Any]] = []
    for wallet, wallet_sells in by_wallet.items():
        current = current_balances.get(wallet)
        linked_wallets = {
            linked_wallet: min(amount, int(current_balances[linked_wallet]))
            for linked_wallet, amount in linked.get(wallet, {}).items()
            if current_balances.get(linked_wallet) is not None and int(current_balances[linked_wallet]) > 0
        }
        linked_remaining = sum(linked_wallets.values())
        starting: int | None = None
        remaining_pct: Decimal | None = None
        effective_current: int | None = None
        if current is not None:
            starting_candidate = int(current) - _wallet_net_delta(events, wallet)
            if starting_candidate > 0:
                starting = starting_candidate
                effective_current = int(current) + linked_remaining
                remaining_pct = Decimal(effective_current) * Decimal(100) / Decimal(starting)
        interval_mean, interval_median = _interval_stats([event.block_time for event in wallet_sells])
        tokens_sold = sum(event.token_amount_raw for event in wallet_sells)
        largest = max(event.token_amount_raw for event in wallet_sells)
        row = {
            "wallet": wallet,
            "sells": len(wallet_sells),
            "tokens_sold_raw": tokens_sold,
            "quote_totals": _quote_totals(wallet_sells),
            "largest_sale_raw": largest,
            "first_sell": min(event.timestamp for event in wallet_sells),
            "most_recent_sell": max(event.timestamp for event in wallet_sells),
            "current_balance_raw": current,
            "current_supply_percentage": None
            if current is None or metadata.supply <= 0
            else str(Decimal(current) * Decimal(100) / Decimal(metadata.supply)),
            "reconstructed_starting_inventory_raw": starting,
            "effective_current_with_linked_raw": effective_current,
            "observed_inventory_remaining_percentage": None if remaining_pct is None else str(remaining_pct),
            "average_sell_interval_seconds": interval_mean,
            "median_sell_interval_seconds": interval_median,
            "transfer_linked_inventory_raw": linked_remaining,
            "transfer_linked_wallets": linked_wallets,
            "status": _status(remaining_pct, len(wallet_sells), config),
        }
        wallet_remaining = (
            None
            if starting is None or current is None
            else Decimal(int(current)) * Decimal(100) / Decimal(starting)
        )
        if wallet_remaining is None:
            wallet_status = "UNRESOLVED"
        elif wallet_remaining <= config.jeet_out_pct:
            wallet_status = "WALLET_VERIFIED_OUT"
        elif wallet_remaining < Decimal(100):
            wallet_status = "PARTIAL_EXIT"
        else:
            wallet_status = "STILL_HOLDING"
        row["wallet_status"] = wallet_status
        row["wallet_inventory_remaining_percentage"] = (
            None if wallet_remaining is None else str(wallet_remaining)
        )
        row["target_token_sold_raw"] = tokens_sold
        rows.append(row)
    rows.sort(key=lambda row: (-int(row["tokens_sold_raw"]), str(row["wallet"])))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank

    top_sells = sorted(sells, key=lambda event: event.token_amount_raw, reverse=True)[: config.concentration_top_n]
    top_volume = sum(event.token_amount_raw for event in top_sells)
    all_volume = sum(event.token_amount_raw for event in sells)
    for row in rows:
        row["percentage_of_observed_sell_volume"] = (
            "0"
            if not all_volume
            else str(Decimal(int(row["tokens_sold_raw"])) * Decimal(100) / Decimal(all_volume))
        )
    concentration_wallets: list[dict[str, Any]] = []
    for wallet in sorted({str(event.wallet) for event in top_sells}):
        top_wallet = sum(event.token_amount_raw for event in top_sells if event.wallet == wallet)
        all_wallet = sum(event.token_amount_raw for event in sells if event.wallet == wallet)
        concentration_wallets.append(
            {
                "wallet": wallet,
                "top_n_tokens_raw": top_wallet,
                "share_of_top_n_percentage": "0"
                if not top_volume
                else str(Decimal(top_wallet) * Decimal(100) / Decimal(top_volume)),
                "share_of_all_observed_percentage": "0"
                if not all_volume
                else str(Decimal(all_wallet) * Decimal(100) / Decimal(all_volume)),
            }
        )
    concentration_wallets.sort(key=lambda row: Decimal(row["share_of_top_n_percentage"]), reverse=True)
    largest_share = Decimal(concentration_wallets[0]["share_of_top_n_percentage"]) if concentration_wallets else Decimal(0)
    dominant = bool(concentration_wallets and largest_share >= config.dominant_threshold)
    return {
        "schema": "jeet-analyzer.summary.v1",
        "token": asdict(metadata),
        "confirmed_sells": len(sells),
        "tokens_sold_raw": all_volume,
        "target_token_sold_raw": all_volume,
        "sellers": rows[: config.top],
        "large_sell_concentration": {
            "top_n": config.concentration_top_n,
            "dominant_threshold_percentage": str(config.dominant_threshold),
            "result": "DOMINANT_SELLER" if dominant else "NO_SINGLE_DOMINANT_SELLER",
            "wallets": concentration_wallets,
            "individual_sells": [event.to_record() for event in top_sells],
        },
        "event_counts": {
            event_type: sum(1 for event in events if event.event_type == event_type)
            for event_type in ("SELL", "BUY", "TRANSFER", "LIQUIDITY_EVENT", "MINT", "BURN", "UNKNOWN")
        },
    }


def write_normalized_jsonl(path: Path, metadata_record: Mapping[str, Any], events: Sequence[NormalizedEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    header = dict(metadata_record)
    header.update({"record_type": "metadata", "schema": NORMALIZED_SCHEMA})
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(header, separators=(",", ":"), sort_keys=True) + "\n")
        for event in events:
            handle.write(json.dumps(event.to_record(), separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def read_normalized_jsonl(path: Path) -> tuple[dict[str, Any], list[NormalizedEvent]]:
    metadata: dict[str, Any] | None = None
    events: list[NormalizedEvent] = []
    seen: set[tuple[str, str | None, str]] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise HistoryError(f"invalid JSONL at line {line_number}") from exc
            if record.get("record_type") == "metadata":
                if metadata is not None:
                    raise HistoryError("normalized JSONL contains more than one metadata record")
                metadata = record
                continue
            if record.get("record_type") != "event":
                raise HistoryError(f"unknown JSONL record_type at line {line_number}")
            event = NormalizedEvent.from_record(record)
            key = (event.signature, event.wallet, event.event_type)
            if key not in seen:
                seen.add(key)
                events.append(event)
    if metadata is None or metadata.get("schema") != NORMALIZED_SCHEMA:
        raise HistoryError("normalized JSONL metadata/schema is missing")
    return metadata, events


def _decimal_json(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value


def write_reports(csv_path: Path, summary_path: Path, summary: Mapping[str, Any]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rank",
        "wallet",
        "sells",
        "tokens_sold_raw",
        "quote_received",
        "largest_sale_raw",
        "first_sell",
        "most_recent_sell",
        "current_balance_raw",
        "observed_inventory_remaining_percentage",
        "average_sell_interval_seconds",
        "median_sell_interval_seconds",
        "transfer_linked_inventory_raw",
        "status",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for source in summary.get("sellers", []):
            row = {key: source.get(key) for key in fields}
            row["quote_received"] = json.dumps(source.get("quote_totals", {}), separators=(",", ":"))
            writer.writerow(row)
    with summary_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True, default=_decimal_json)
        handle.write("\n")


def display_amount(raw: int | None, decimals: int) -> str:
    if raw is None:
        return "UNKNOWN"
    value = Decimal(raw) / (Decimal(10) ** decimals)
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def quote_display(quote_totals: Mapping[str, Mapping[str, Any]]) -> str:
    if not quote_totals:
        return "UNKNOWN"
    return ";".join(
        f"{display_amount(int(item['amount_raw']), int(item['decimals']))} {item['symbol']}"
        for _key, item in sorted(quote_totals.items())
    )


def print_analysis(summary: Mapping[str, Any], *, window_label: str, mint: str) -> None:
    token = summary["token"]
    decimals = int(token["decimals"])
    symbol = token["symbol"]
    print("RANK | WALLET | SELLS | TOKENS SOLD | SOL/QUOTE RECEIVED | LARGEST SALE | CURRENT BALANCE | REMAINING % | STATUS")
    for row in summary.get("sellers", []):
        print(
            f"{row['rank']} | {row['wallet']} | {row['sells']} | "
            f"{display_amount(row['tokens_sold_raw'], decimals)} | {quote_display(row['quote_totals'])} | "
            f"{display_amount(row['largest_sale_raw'], decimals)} | "
            f"{display_amount(row['current_balance_raw'], decimals)} | "
            f"{row['observed_inventory_remaining_percentage'] or 'UNKNOWN'} | {row['status']}"
        )
    concentration = summary["large_sell_concentration"]
    print("\nLARGE_SELL_CONCENTRATION")
    for index, event in enumerate(concentration["individual_sells"], 1):
        print(
            f"top_sell={index} wallet={event.get('wallet') or 'UNRESOLVED'} "
            f"tokens={display_amount(int(event['token_amount_raw']), decimals)} "
            f"signature={event['signature']}"
        )
    for row in concentration["wallets"]:
        print(
            f"wallet={row['wallet']} top_n_share={row['share_of_top_n_percentage']}% "
            f"all_observed_share={row['share_of_all_observed_percentage']}%"
        )
    print(concentration["result"])
    largest = summary.get("sellers", [{}])[0] if summary.get("sellers") else {}
    print(f"\nTOKEN: {symbol}")
    print(f"MINT: {mint}")
    print(f"WINDOW: {window_label}")
    print(f"Largest seller: {largest.get('wallet', 'NONE CONFIRMED')}")
    print(f"Largest seller confirmed sells: {largest.get('sells', 0)}")
    print(f"Largest seller tokens sold: {display_amount(largest.get('tokens_sold_raw', 0), decimals)}")
    print(f"Market-wide confirmed sells: {summary['confirmed_sells']}")
    print(f"Market-wide tokens sold: {display_amount(summary['tokens_sold_raw'], decimals)}")
    if largest:
        print(f"Current balance: {display_amount(largest.get('current_balance_raw'), decimals)}")
        remaining = largest.get("observed_inventory_remaining_percentage")
        print(f"Observed inventory remaining: {remaining + '%' if remaining is not None else 'UNKNOWN'}")
        print(f"WALLET_STATUS: {largest.get('wallet_status', 'UNRESOLVED')}")
        print(f"LEGACY_DISTRIBUTION_STATUS: {largest['status']}")
        print(f"Transfer-linked inventory: {display_amount(largest['transfer_linked_inventory_raw'], decimals)}")
        print(f"WATCH COMMAND: jeet-analyzer watch --mint {mint} --wallet {largest['wallet']}")
