#!/usr/bin/env python3
"""Generic read-only Solana token holder, seller, and balance analyzer.

This module intentionally exposes only a small allowlist of read-only Solana
JSON-RPC methods and websocket program subscriptions.  It has no transaction
construction, signing, wallet-secret, or submission interface.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import signal
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests
import websocket

from . import history
from . import tx_classifier
from . import cluster
from . import lifecycle
from . import report_contract
from . import reporting
from .investigation import InvestigationContext, InvestigationLimits, ProviderBudgetExhausted
from .progress import ProgressTracker


DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
TOKEN_PROGRAMS = (TOKEN_PROGRAM, TOKEN_2022_PROGRAM)
KNOWN_INFRASTRUCTURE_PROGRAMS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter router",
    "675kPX9MHTjS2zt1qfr1NYHuzefVAvHGHbGDXfL4fGF": "Raydium AMM",
    "whirLbMiicVdio4qvUfM5KAg6CtX9NAjEEQyF9jb": "Orca Whirlpool",
}
BENIGN_TRANSFER_PROGRAMS = {
    SYSTEM_PROGRAM,
    TOKEN_PROGRAM,
    TOKEN_2022_PROGRAM,
    ASSOCIATED_TOKEN_PROGRAM,
    COMPUTE_BUDGET_PROGRAM,
    MEMO_PROGRAM,
}
THRESHOLDS = (Decimal("90"), Decimal("75"), Decimal("50"), Decimal("25"), Decimal("10"), Decimal("5"), Decimal("1"))
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58_VALUES = {character: index for index, character in enumerate(BASE58_ALPHABET)}


class WatcherError(RuntimeError):
    """Expected, user-facing watcher failure."""


class RpcError(WatcherError):
    """Read-only RPC failure with URL details removed."""

    category = "RPC_ERROR"

    def __init__(self, message: str, *, retry_count: int = 0) -> None:
        super().__init__(message)
        self.retry_count = int(retry_count)


class RpcTimeoutError(RpcError):
    category = "TIMEOUT"


class RpcRateLimitError(RpcError):
    category = "RATE_LIMIT"


class RpcHttpError(RpcError):
    category = "HTTP_ERROR"


class RpcMalformedResponseError(RpcError):
    category = "MALFORMED_RESPONSE"


class RpcTransportError(RpcError):
    category = "TRANSPORT_ERROR"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def short_address(address: str) -> str:
    return f"{address[:5]}...{address[-5:]}"


def redact_urls(value: object) -> str:
    text = str(value)
    return re.sub(r"(?:https?|wss?)://[^\s'\"]+", "<redacted-url>", text, flags=re.IGNORECASE)


def decode_base58(value: str) -> bytes:
    number = 0
    for character in value:
        if character not in BASE58_VALUES:
            raise ValueError("address contains a non-base58 character")
        number = number * 58 + BASE58_VALUES[character]
    encoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeroes + encoded


def validate_public_address(value: str) -> str:
    if not 32 <= len(value) <= 44:
        raise argparse.ArgumentTypeError("expected a Solana public address")
    try:
        decoded = decode_base58(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a base58 Solana public address") from exc
    if len(decoded) != 32:
        raise argparse.ArgumentTypeError("expected a 32-byte Solana public address")
    return value


def amount_text(raw_amount: int, decimals: int) -> str:
    value = Decimal(raw_amount) / (Decimal(10) ** decimals)
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def percentage_text(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.6f}"


def rpc_url_from_environment(environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    url = source.get("SOLANA_RPC_URL", DEFAULT_RPC_URL).strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WatcherError("SOLANA_RPC_URL must be an http:// or https:// URL")
    return url


def websocket_url(rpc_url: str) -> str:
    parsed = urlsplit(rpc_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, ""))


@dataclass(frozen=True)
class TokenBalanceSnapshot:
    accounts: dict[str, int]
    slot: int

    @property
    def total(self) -> int:
        return sum(self.accounts.values())


class ReadOnlyRpcClient:
    """Strictly allowlisted Solana RPC client.

    The allowlist is deliberately local to the client so callers cannot turn it
    into a generic RPC transport and submit a transaction by changing a CLI
    argument.
    """

    ALLOWED_METHODS = frozenset(
        {
            "getAccountInfo",
            "getMultipleAccounts",
            "getProgramAccounts",
            "getSignaturesForAddress",
            "getTokenAccountsByOwner",
            "getTokenLargestAccounts",
            "getTokenSupply",
            "getTransaction",
        }
    )
    CACHEABLE_METHODS = frozenset(
        {"getAccountInfo", "getMultipleAccounts", "getTokenSupply", "getTransaction"}
    )
    CACHE_NAMESPACES = {
        "getAccountInfo": "account_request",
        "getMultipleAccounts": "account_request",
        "getTokenSupply": "mint_metadata",
        "getTransaction": "transaction",
    }

    def __init__(
        self,
        rpc_url: str,
        *,
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        backoff_cap_seconds: float = 8.0,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        investigation: InvestigationContext | None = None,
    ) -> None:
        self.__rpc_url = rpc_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_cap_seconds = backoff_cap_seconds
        self.session = session or requests.Session()
        self.sleeper = sleeper
        self.investigation = investigation
        self._request_id = 0
        self.total_retry_count = 0
        self.last_failure_reason: str | None = None
        self.last_failure_category: str | None = None
        if self.timeout_seconds <= 0:
            raise RpcError("RPC request timeout must be positive")
        if not 0 <= self.max_retries <= 10:
            raise RpcError("RPC retries must be between 0 and 10")
        if not 0 < self.backoff_cap_seconds <= 60:
            raise RpcError("RPC backoff cap must be greater than 0 and no more than 60 seconds")

    def _backoff(self, attempt: int, retry_after: object = None) -> None:
        try:
            requested = float(retry_after) if retry_after not in {None, ""} else 2**attempt
        except (TypeError, ValueError):
            requested = 2**attempt
        self.total_retry_count += 1
        self.sleeper(min(max(requested, 0.1), self.backoff_cap_seconds))

    def _terminal(self, error_type: type[RpcError], message: str, retry_count: int) -> RpcError:
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
            "backoff_cap_seconds": self.backoff_cap_seconds,
        }
        if self.investigation is not None:
            record["investigation"] = self.investigation.to_record()
        return record

    def call(self, method: str, params: Sequence[Any]) -> Any:
        if method not in self.ALLOWED_METHODS:
            raise RpcError(f"RPC method is not allowed by read-only policy: {method}")
        cache_key = [method, list(params)]
        if self.investigation is not None and method in self.CACHEABLE_METHODS:
            namespace = self.CACHE_NAMESPACES[method]
            hit, cached = self.investigation.cache_get(namespace, cache_key)
            if hit:
                self.investigation.record_provider_request_avoided()
                return cached
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": list(params)}
        call_retries = 0
        for attempt in range(self.max_retries + 1):
            if self.investigation is not None:
                self.investigation.before_request("rpc", method, retry=attempt > 0)
            try:
                response = self.session.post(self.__rpc_url, json=payload, timeout=self.timeout_seconds)
            except requests.Timeout as exc:
                if attempt < self.max_retries:
                    call_retries += 1
                    self._backoff(attempt)
                    continue
                raise self._terminal(
                    RpcTimeoutError,
                    f"{method} timed out after {attempt + 1} attempts; RPC URL redacted",
                    call_retries,
                ) from exc
            except (requests.RequestException, OSError) as exc:
                if attempt < self.max_retries:
                    call_retries += 1
                    self._backoff(attempt)
                    continue
                raise self._terminal(
                    RpcTransportError,
                    f"{method} transport failed after {attempt + 1} attempts ({type(exc).__name__}); RPC URL redacted",
                    call_retries,
                ) from exc
            except Exception as exc:
                raise self._terminal(
                    RpcTransportError,
                    f"{method} transport failed ({type(exc).__name__}); RPC URL redacted",
                    call_retries,
                ) from exc
            status = int(getattr(response, "status_code", 0))
            if status == 429:
                if attempt < self.max_retries:
                    call_retries += 1
                    self._backoff(attempt, getattr(response, "headers", {}).get("Retry-After"))
                    continue
                raise self._terminal(
                    RpcRateLimitError,
                    f"{method} rate limit persisted after {attempt + 1} attempts; RPC URL redacted",
                    call_retries,
                )
            if not 200 <= status < 300:
                if status >= 500 and attempt < self.max_retries:
                    call_retries += 1
                    self._backoff(attempt)
                    continue
                raise self._terminal(
                    RpcHttpError,
                    f"{method} returned HTTP {status}; RPC URL redacted",
                    call_retries,
                )
            try:
                body = response.json()
            except Exception as exc:
                raise self._terminal(
                    RpcMalformedResponseError,
                    f"{method} returned invalid JSON; RPC URL redacted",
                    call_retries,
                ) from exc
            if not isinstance(body, Mapping):
                raise self._terminal(
                    RpcMalformedResponseError,
                    f"{method} returned a non-object response; RPC URL redacted",
                    call_retries,
                )
            if body.get("error"):
                error = body["error"]
                code = error.get("code") if isinstance(error, dict) else "unknown"
                if code == -32029 and attempt < self.max_retries:
                    call_retries += 1
                    self._backoff(attempt)
                    continue
                error_type = RpcRateLimitError if code == -32029 else RpcError
                raise self._terminal(
                    error_type,
                    f"{method} RPC error {code}; provider message and RPC URL redacted",
                    call_retries,
                )
            if "result" not in body:
                raise self._terminal(
                    RpcMalformedResponseError,
                    f"{method} response omitted result",
                    call_retries,
                )
            result = body["result"]
            if self.investigation is not None and method in self.CACHEABLE_METHODS:
                self.investigation.cache_set(self.CACHE_NAMESPACES[method], cache_key, result)
            return result
        raise self._terminal(RpcError, f"{method} retry budget exhausted", call_retries)

    def get_token_supply(self, mint: str) -> tuple[int, int]:
        result = self.call("getTokenSupply", [mint, {"commitment": "confirmed"}])
        value = result["value"]
        return int(value["amount"]), int(value["decimals"])
    def get_account_info(self, address: str) -> dict[str, Any] | None:
        if self.investigation is not None:
            hit, cached = self.investigation.cache_get("account_record", [address, "confirmed"])
            if hit:
                self.investigation.record_provider_request_avoided()
                return cached
        result = self.call("getAccountInfo", [address, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        value = result.get("value")
        if self.investigation is not None:
            self.investigation.cache_set("account_record", [address, "confirmed"], value)
        return value

    def get_largest_token_accounts(self, mint: str) -> list[dict[str, Any]]:
        result = self.call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        return list(result.get("value", []))

    def get_all_mint_token_accounts(self, mint: str) -> list[dict[str, Any]]:
        mint_record = self.get_account_info(mint)
        program_id = str((mint_record or {}).get("owner", ""))
        if program_id not in TOKEN_PROGRAMS:
            raise RpcError("mint account is not owned by SPL Token or Token-2022")
        result = self.call(
            "getProgramAccounts",
            [
                program_id,
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "filters": [{"memcmp": {"offset": 0, "bytes": mint}}],
                },
            ],
        )
        return list(result)

    def get_multiple_accounts(self, addresses: Sequence[str]) -> dict[str, dict[str, Any] | None]:
        records: dict[str, dict[str, Any] | None] = {}
        missing: list[str] = []
        unique_addresses = list(dict.fromkeys(addresses))
        for address in unique_addresses:
            if self.investigation is None:
                missing.append(address)
                continue
            hit, cached = self.investigation.cache_get("account_record", [address, "confirmed"])
            if hit:
                records[address] = cached
            else:
                missing.append(address)
        if self.investigation is not None:
            uncached_request_count = (len(unique_addresses) + 99) // 100
            actual_request_count = (len(missing) + 99) // 100
            self.investigation.record_provider_request_avoided(
                uncached_request_count - actual_request_count
            )
        for offset in range(0, len(missing), 100):
            chunk = list(missing[offset : offset + 100])
            if not chunk:
                continue
            result = self.call(
                "getMultipleAccounts",
                [chunk, {"encoding": "jsonParsed", "commitment": "confirmed"}],
            )
            for address, value in zip(chunk, result.get("value", [])):
                records[address] = value
                if self.investigation is not None:
                    self.investigation.cache_set("account_record", [address, "confirmed"], value)
        return records

    def get_owner_token_accounts(
        self, owner: str, mint: str, *, commitment: str = "confirmed"
    ) -> TokenBalanceSnapshot:
        if commitment not in {"confirmed", "finalized"}:
            raise RpcError("token-account commitment must be confirmed or finalized")
        result = self.call(
            "getTokenAccountsByOwner",
            [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": commitment}],
        )
        accounts: dict[str, int] = {}
        for row in result.get("value", []):
            try:
                info = row["account"]["data"]["parsed"]["info"]
                if info["mint"] != mint:
                    continue
                accounts[str(row["pubkey"])] = int(info["tokenAmount"]["amount"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RpcError("getTokenAccountsByOwner returned an unparseable token account") from exc
        return TokenBalanceSnapshot(accounts=accounts, slot=int(result.get("context", {}).get("slot", 0)))

    def get_signatures(self, address: str, *, limit: int = 20) -> list[dict[str, Any]]:
        result = self.call(
            "getSignaturesForAddress",
            [address, {"limit": limit, "commitment": "confirmed"}],
        )
        return list(result)

    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        if self.investigation is not None:
            hit, cached = self.investigation.transaction_get(signature)
            if hit:
                self.investigation.record_provider_request_avoided()
                return cached
        result = self.call(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}],
        )
        if self.investigation is not None:
            self.investigation.observe_transaction(signature, result)
        return result


def _client_telemetry(client: object) -> dict[str, Any]:
    telemetry_method = getattr(client, "telemetry", None)
    if callable(telemetry_method):
        return dict(telemetry_method())
    return {
        "retry_count": 0,
        "terminal_failure_reason": None,
        "failure_category": None,
    }


def token_account_owner(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    try:
        parsed = record["data"]["parsed"]
        if parsed.get("type") != "account":
            return None
        return str(parsed["info"]["owner"])
    except (KeyError, TypeError):
        return None


def classify_owner_account(record: dict[str, Any] | None) -> tuple[str, str]:
    return tx_classifier.classify_address_record(record)


def _holder_account_row(row: Mapping[str, Any]) -> tuple[str, str | None, int] | None:
    token_address = str(row.get("pubkey") or row.get("address") or "")
    owner = row.get("owner")
    amount = row.get("amount")
    try:
        info = row.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        owner = owner or info.get("owner")
        amount = amount if amount is not None else info.get("tokenAmount", {}).get("amount")
    except AttributeError:
        pass
    if not token_address or amount is None:
        return None
    return token_address, str(owner) if owner else None, int(amount)


def inspect_holders(
    rpc: ReadOnlyRpcClient,
    mint: str,
    *,
    limit: int = 20,
    symbol: str | None = None,
    all_token_accounts: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    supply, decimals = rpc.get_token_supply(mint)
    complete_accounts = all_token_accounts is not None
    rows: list[tuple[str, str | None, int]] = []
    if all_token_accounts is None:
        try:
            all_token_accounts = rpc.get_all_mint_token_accounts(mint)
            complete_accounts = True
        except (AttributeError, RpcError):
            if limit > 20:
                raise WatcherError(
                    "--top above 20 requires complete mint token-account indexing; configure Helius or an RPC that supports getProgramAccounts"
                )
            largest = rpc.get_largest_token_accounts(mint)[:limit]
            addresses = [str(row["address"]) for row in largest]
            records = rpc.get_multiple_accounts(addresses)
            for row in largest:
                address = str(row["address"])
                rows.append((address, token_account_owner(records.get(address)), int(row["amount"])))
    if all_token_accounts is not None:
        rows = [parsed for row in all_token_accounts if (parsed := _holder_account_row(row))]

    grouped: dict[str, dict[str, Any]] = {}
    for token_address, owner, amount in rows:
        key = owner or f"UNRESOLVED:{token_address}"
        item = grouped.setdefault(key, {"wallet": owner, "balance_raw": 0, "token_accounts": []})
        item["balance_raw"] += amount
        item["token_accounts"].append(token_address)
    ranked = sorted(grouped.values(), key=lambda item: int(item["balance_raw"]), reverse=True)[:limit]
    owners = [str(item["wallet"]) for item in ranked if item["wallet"]]
    owner_records = rpc.get_multiple_accounts(list(dict.fromkeys(owners)))

    display_symbol = symbol or short_address(mint)
    print(f"TOKEN: {display_symbol}")
    print(f"MINT: {mint}")
    print(f"RPC token supply={amount_text(supply, decimals)} (circulating-supply proxy only)")
    print("WARNING: RPC supply does not identify locked, reserve, program, vault, or pool balances.")
    print("WARNING: token accounts are aggregated by resolved owner; classifications do not prove human control.")
    print("WARNING: a token account or resolved program address is not automatically a human wallet.")
    print(f"HOLDER_ACCOUNT_COVERAGE={'COMPLETE_CURRENT_INDEX' if complete_accounts else 'PARTIAL_RPC_TOP20'}")
    print("rank balance pct_rpc_supply wallet token_accounts classification")

    output: list[dict[str, Any]] = []
    for rank, row in enumerate(ranked, start=1):
        owner = row["wallet"]
        raw_amount = int(row["balance_raw"])
        classification, note = classify_owner_account(owner_records.get(owner) if owner else None)
        percentage = Decimal(0) if supply == 0 else Decimal(raw_amount) * Decimal(100) / Decimal(supply)
        display_owner = owner or "UNRESOLVED"
        item = {
            "rank": rank,
            "token_account": row["token_accounts"][0],
            "token_accounts": list(row["token_accounts"]),
            "token_account_count": len(row["token_accounts"]),
            "wallet": owner,
            "balance_raw": raw_amount,
            "balance": amount_text(raw_amount, decimals),
            "percentage_of_rpc_supply": percentage_text(percentage),
            "classification": classification,
            "note": note,
        }
        output.append(item)
        print(
            f"{rank:>2} {item['balance']} {item['percentage_of_rpc_supply']}% "
            f"{display_owner} {item['token_account_count']} {classification}"
        )
        print(f"   FLAG: {note}")
    return output


def _safe_symbol(value: object, mint: str) -> str:
    symbol = re.sub(r"[^A-Za-z0-9._-]", "", str(value or ""))[:24]
    return symbol or "UNKNOWN"


def resolve_metadata(
    rpc: ReadOnlyRpcClient,
    mint: str,
    provider: history.HeliusHistoricalProvider | None = None,
    *,
    symbol_override: str | None = None,
) -> history.TokenMetadata:
    supply, decimals = rpc.get_token_supply(mint)
    symbol = _safe_symbol(symbol_override, mint) if symbol_override else "UNKNOWN"
    name: str | None = None
    price_usd: Decimal | None = None
    source = "on-chain RPC supply/decimals; symbol unavailable"
    asset = provider.get_metadata(mint) if provider else None
    if asset:
        content_metadata = asset.get("content", {}).get("metadata", {})
        token_info = asset.get("token_info", {})
        candidate_symbol = content_metadata.get("symbol") or token_info.get("symbol")
        if candidate_symbol and not symbol_override:
            symbol = _safe_symbol(candidate_symbol, mint)
        candidate_name = content_metadata.get("name")
        name = str(candidate_name)[:120] if candidate_name else None
        try:
            if token_info.get("decimals") is not None:
                decimals = int(token_info["decimals"])
            if token_info.get("supply") is not None:
                supply = int(token_info["supply"])
            price = token_info.get("price_info", {}).get("price_per_token")
            if price is not None:
                price_usd = Decimal(str(price))
        except (TypeError, ValueError, ArithmeticError):
            price_usd = None
        source = "Helius DAS plus on-chain RPC fallback"
    try:
        mint_record = rpc.get_account_info(mint)
    except (AttributeError, RpcError):
        mint_record = None
    token_program = str((mint_record or {}).get("owner") or "") or None
    return history.TokenMetadata(mint, symbol, name, decimals, supply, price_usd, source, token_program)


@dataclass(frozen=True)
class Attribution:
    event_type: str
    signature: str | None = None
    destination: str | None = None
    evidence: str | None = None


def _account_keys(transaction: Mapping[str, Any]) -> list[str]:
    keys = transaction.get("transaction", {}).get("message", {}).get("accountKeys", [])
    return [str(item.get("pubkey")) if isinstance(item, dict) else str(item) for item in keys]


def _token_balance_map(rows: Iterable[Mapping[str, Any]], keys: Sequence[str]) -> dict[str, tuple[str | None, int]]:
    balances: dict[str, tuple[str | None, int]] = {}
    for row in rows:
        try:
            account_index = int(row["accountIndex"])
            account = keys[account_index]
            owner = str(row["owner"]) if row.get("owner") else None
            amount = int(row["uiTokenAmount"]["amount"])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        balances[account] = (owner, amount)
    return balances


def _all_instructions(transaction: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    message = transaction.get("transaction", {}).get("message", {})
    instructions: list[Mapping[str, Any]] = [item for item in message.get("instructions", []) if isinstance(item, dict)]
    for inner in transaction.get("meta", {}).get("innerInstructions", []) or []:
        instructions.extend(item for item in inner.get("instructions", []) if isinstance(item, dict))
    return instructions


def analyze_transaction(
    transaction: Mapping[str, Any],
    *,
    wallet: str,
    mint: str,
    expected_delta: int,
) -> Attribution | None:
    """Conservatively attribute one exact aggregate balance change."""

    meta = transaction.get("meta") or {}
    keys = _account_keys(transaction)
    pre_rows = [row for row in meta.get("preTokenBalances", []) if row.get("mint") == mint]
    post_rows = [row for row in meta.get("postTokenBalances", []) if row.get("mint") == mint]
    pre = _token_balance_map(pre_rows, keys)
    post = _token_balance_map(post_rows, keys)
    token_accounts = set(pre) | set(post)

    wallet_delta = 0
    destination_deltas: dict[str, int] = {}
    for account in token_accounts:
        pre_owner, pre_amount = pre.get(account, (None, 0))
        post_owner, post_amount = post.get(account, (None, 0))
        owner = post_owner or pre_owner
        delta = post_amount - pre_amount
        if owner == wallet:
            wallet_delta += delta
        elif owner and delta > 0:
            destination_deltas[owner] = destination_deltas.get(owner, 0) + delta

    if wallet_delta != expected_delta:
        return None
    if expected_delta > 0:
        return Attribution("BALANCE_INCREASE", evidence="exact owner token-balance delta")

    exact_destinations = [owner for owner, delta in destination_deltas.items() if delta == -expected_delta]
    destination = exact_destinations[0] if len(exact_destinations) == 1 else None
    instructions = _all_instructions(transaction)
    program_ids = {str(item.get("programId")) for item in instructions if item.get("programId")}
    parsed_types = {
        str(item.get("parsed", {}).get("type", "")).lower()
        for item in instructions
        if isinstance(item.get("parsed"), dict)
    }
    logs = "\n".join(str(item) for item in (meta.get("logMessages") or []))
    explicit_market_instruction = bool(
        re.search(r"\binstruction:\s*(?:sell|swap[a-z0-9_]*)\b", logs, flags=re.IGNORECASE)
        or parsed_types.intersection({"sell", "swap", "swapbasein", "swapbaseout"})
    )
    if explicit_market_instruction:
        return Attribution("SELL", destination=destination, evidence="transaction contains explicit sell/swap instruction")

    has_token_transfer = bool(parsed_types.intersection({"transfer", "transferchecked"}))
    unknown_programs = program_ids - BENIGN_TRANSFER_PROGRAMS
    if destination and has_token_transfer and not unknown_programs:
        return Attribution("TRANSFER", destination=destination, evidence="exact direct SPL transfer with no market program")
    if destination:
        return Attribution(
            "UNKNOWN",
            destination=destination,
            evidence="destination token owner is exact, but transaction purpose is not provably a transfer or sale",
        )
    return Attribution("UNKNOWN", evidence="no deterministic sale or transfer evidence")


class TransactionAttributor:
    def __init__(self, rpc: ReadOnlyRpcClient) -> None:
        self.rpc = rpc
        self.seen_signatures: set[str] = set()

    def seed(self, addresses: Iterable[str], *, max_slot: int) -> None:
        for address in set(addresses):
            try:
                rows = self.rpc.get_signatures(address)
            except RpcError:
                continue
            for row in rows:
                if int(row.get("slot", 0)) <= max_slot and row.get("signature"):
                    self.seen_signatures.add(str(row["signature"]))

    def attribute(
        self,
        delta: int,
        *,
        addresses: Iterable[str],
        wallet: str,
        mint: str,
        max_slot: int,
    ) -> Attribution:
        candidates: dict[str, dict[str, Any]] = {}
        for address in set(addresses) | {wallet}:
            try:
                rows = self.rpc.get_signatures(address)
            except RpcError:
                continue
            for row in rows:
                signature = row.get("signature")
                if not signature or signature in self.seen_signatures:
                    continue
                if int(row.get("slot", 0)) <= max_slot:
                    candidates[str(signature)] = row

        ordered = sorted(candidates.items(), key=lambda item: int(item[1].get("slot", 0)), reverse=True)
        inspected: set[str] = set()
        for signature, _row in ordered:
            try:
                transaction = self.rpc.get_transaction(signature)
            except RpcError:
                continue
            if transaction is None:
                continue
            inspected.add(signature)
            attribution = analyze_transaction(transaction, wallet=wallet, mint=mint, expected_delta=delta)
            if attribution:
                self.seen_signatures.update(inspected)
                return Attribution(
                    attribution.event_type,
                    signature=signature,
                    destination=attribution.destination,
                    evidence=attribution.evidence,
                )
        self.seen_signatures.update(inspected)
        event_type = "BALANCE_INCREASE" if delta > 0 else "UNKNOWN"
        return Attribution(event_type, evidence="no single exact matching confirmed transaction was available")


class BoundedJsonlWriter:
    def __init__(self, path: Path, *, max_bytes: int, backups: int) -> None:
        if max_bytes < 1024:
            raise WatcherError("--max-log-bytes must be at least 1024")
        if not 0 <= backups <= 20:
            raise WatcherError("--log-backups must be between 0 and 20")
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _rotate(self) -> None:
        if self.backups == 0:
            if self.path.exists():
                self.path.unlink()
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.backups}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                source.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
        if self.path.exists():
            self.path.replace(self.path.with_name(f"{self.path.name}.1"))

    def write(self, event: Mapping[str, Any]) -> None:
        line = (json.dumps(dict(event), separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        current_size = self.path.stat().st_size if self.path.exists() else 0
        if current_size and current_size + len(line) > self.max_bytes:
            self._rotate()
        with self.path.open("ab") as handle:
            handle.write(line)


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "JeetAnalyzer"
    return Path.cwd() / "jeet-analyzer-data"


def default_log_path(mint: str = "token", wallet: str = "wallet") -> Path:
    return default_data_dir() / f"watch-{mint[:8]}-{wallet[:8]}.jsonl"


class WalletWatcher:
    def __init__(
        self,
        rpc: ReadOnlyRpcClient,
        *,
        rpc_url: str,
        wallet: str,
        mint: str,
        decimals: int,
        symbol: str | None = None,
        trace_depth: int = 1,
        legacy_console_label: str | None = None,
        writer: BoundedJsonlWriter,
        poll_seconds: float = 30.0,
        stop_event: threading.Event | None = None,
        websocket_factory: Callable[..., Any] = websocket.create_connection,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rpc = rpc
        self._websocket_url = websocket_url(rpc_url)
        self.wallet = wallet
        self.mint = mint
        self.decimals = decimals
        self.symbol = _safe_symbol(symbol, mint) if symbol else short_address(mint)
        self.console_label = legacy_console_label or f"symbol={self.symbol}"
        if trace_depth not in {0, 1}:
            raise WatcherError("trace_depth must be 0 or 1")
        self.trace_depth = trace_depth
        self.writer = writer
        self.poll_seconds = poll_seconds
        self.stop_event = stop_event or threading.Event()
        self.websocket_factory = websocket_factory
        self.sleeper = sleeper
        self.attributor = TransactionAttributor(rpc)
        self.starting_balance: int | None = None
        self.current_snapshot: TokenBalanceSnapshot | None = None
        self.traced_caps: dict[str, int] = {}
        self.traced_snapshots: dict[str, TokenBalanceSnapshot] = {}
        self.emitted_thresholds: set[Decimal] = set()

    def _remaining_percentage(self, balance: int) -> Decimal | None:
        if not self.starting_balance:
            return None
        return Decimal(balance) * Decimal(100) / Decimal(self.starting_balance)

    def _base_event(
        self,
        event_type: str,
        previous: int | None,
        current: int,
        attribution: Attribution | None = None,
        *,
        previous_effective: int | None = None,
        current_effective: int | None = None,
    ) -> dict[str, Any]:
        previous_effective = previous if previous_effective is None else previous_effective
        current_effective = current if current_effective is None else current_effective
        percentage = self._remaining_percentage(current_effective)
        rpc_telemetry = _client_telemetry(self.rpc)
        return {
            "timestamp": utc_timestamp(),
            "mint": self.mint,
            "wallet": self.wallet,
            "previous_balance": None if previous is None else amount_text(previous, self.decimals),
            "current_balance": amount_text(current, self.decimals),
            "previous_balance_raw": previous,
            "current_balance_raw": current,
            "previous_effective_balance_raw": previous_effective,
            "current_effective_balance_raw": current_effective,
            "trace_depth": self.trace_depth,
            "traced_wallets": dict(self.traced_caps),
            "percentage_remaining": percentage_text(percentage),
            "event_type": event_type,
            "transaction_signature": attribution.signature if attribution else None,
            "destination": attribution.destination if attribution else None,
            "evidence": attribution.evidence if attribution else None,
            "provider_retry_count": int(rpc_telemetry.get("retry_count") or 0),
            "provider_terminal_failure_reason": rpc_telemetry.get("terminal_failure_reason"),
            "provider_failure_category": rpc_telemetry.get("failure_category"),
        }

    def _write_and_print_change(
        self,
        event_type: str,
        previous: int,
        current: int,
        attribution: Attribution,
        *,
        previous_effective: int | None = None,
        current_effective: int | None = None,
    ) -> None:
        event = self._base_event(
            event_type,
            previous,
            current,
            attribution,
            previous_effective=previous_effective,
            current_effective=current_effective,
        )
        self.writer.write(event)
        remaining = event["percentage_remaining"]
        destination = f" destination={attribution.destination}" if attribution.destination else ""
        signature = f" signature={attribution.signature}" if attribution.signature else ""
        if current > previous:
            increase = amount_text(current - previous, self.decimals)
            print(
                f"INCREASE {self.console_label} wallet={short_address(self.wallet)} +{increase} "
                f"current={event['current_balance']} remaining={remaining or 'n/a'}%{signature}"
            )
        else:
            print(
                f"{event_type} {self.console_label} wallet={short_address(self.wallet)} "
                f"previous={event['previous_balance']} current={event['current_balance']} "
                f"remaining={remaining or 'n/a'}%{destination}{signature}"
            )

    def _emit_thresholds(
        self,
        previous: int,
        current: int,
        attribution: Attribution,
        *,
        primary_previous: int | None = None,
        primary_current: int | None = None,
    ) -> None:
        previous_pct = self._remaining_percentage(previous)
        current_pct = self._remaining_percentage(current)
        if previous_pct is None or current_pct is None or current >= previous:
            return
        for threshold in THRESHOLDS:
            if threshold in self.emitted_thresholds:
                continue
            if previous_pct > threshold >= current_pct:
                self.emitted_thresholds.add(threshold)
                event_type = "JEET_OUT" if threshold == Decimal("1") else f"THRESHOLD_{int(threshold)}"
                event = self._base_event(
                    event_type,
                    previous if primary_previous is None else primary_previous,
                    current if primary_current is None else primary_current,
                    attribution,
                    previous_effective=previous,
                    current_effective=current,
                )
                event["threshold_percentage"] = f"{threshold:.0f}"
                self.writer.write(event)
                if event_type == "JEET_OUT":
                    print(
                        f"JEET_OUT {self.console_label} wallet={short_address(self.wallet)} "
                        f"remaining={event['percentage_remaining']}%"
                    )
                else:
                    print(
                        f"*** {event_type} {self.console_label} wallet={short_address(self.wallet)} "
                        f"remaining={event['percentage_remaining']}% ***"
                    )

    def _traced_total(self, snapshots: Mapping[str, TokenBalanceSnapshot] | None = None) -> int:
        source = self.traced_snapshots if snapshots is None else snapshots
        return sum(min(self.traced_caps.get(wallet, 0), snapshot.total) for wallet, snapshot in source.items())

    def _refresh_traced(self) -> dict[str, TokenBalanceSnapshot]:
        refreshed: dict[str, TokenBalanceSnapshot] = {}
        for wallet in self.traced_caps:
            refreshed[wallet] = self.rpc.get_owner_token_accounts(wallet, self.mint)
        return refreshed

    def reconcile(self, reason: str) -> None:
        snapshot = self.rpc.get_owner_token_accounts(self.wallet, self.mint)
        if self.current_snapshot is None:
            self.starting_balance = snapshot.total
            self.current_snapshot = snapshot
            self.attributor.seed(set(snapshot.accounts) | {self.wallet}, max_slot=snapshot.slot)
            event = self._base_event("START", None, snapshot.total)
            event["reason"] = reason
            self.writer.write(event)
            remaining = event["percentage_remaining"] or "n/a"
            print(
                f"START {self.console_label} wallet={short_address(self.wallet)} "
                f"balance={event['current_balance']} remaining={remaining}%"
            )
            if snapshot.total == 0:
                print("WARNING: starting balance is zero; percentage thresholds are disabled for this session.")
            return

        previous_snapshot = self.current_snapshot
        previous_traced = dict(self.traced_snapshots)
        previous_effective = previous_snapshot.total + self._traced_total(previous_traced)
        refreshed_traced = self._refresh_traced()
        changed_accounts = {
            address
            for address in set(previous_snapshot.accounts) | set(snapshot.accounts)
            if previous_snapshot.accounts.get(address, 0) != snapshot.accounts.get(address, 0)
        }
        delta = snapshot.total - previous_snapshot.total
        attribution: Attribution | None = None
        if delta:
            attribution = self.attributor.attribute(
                delta,
                addresses=changed_accounts,
                wallet=self.wallet,
                mint=self.mint,
                max_slot=snapshot.slot,
            )
            if (
                self.trace_depth == 1
                and delta < 0
                and attribution.event_type == "TRANSFER"
                and attribution.destination
            ):
                destination = attribution.destination
                destination_snapshot = self.rpc.get_owner_token_accounts(destination, self.mint)
                self.traced_caps[destination] = self.traced_caps.get(destination, 0) + (-delta)
                refreshed_traced[destination] = destination_snapshot
                self.attributor.seed(
                    set(destination_snapshot.accounts) | {destination}, max_slot=destination_snapshot.slot
                )
        self.traced_snapshots = refreshed_traced
        current_effective = snapshot.total + self._traced_total()
        if delta and attribution:
            event_type = "BALANCE_INCREASE" if delta > 0 else attribution.event_type
            self._write_and_print_change(
                event_type,
                previous_snapshot.total,
                snapshot.total,
                attribution,
                previous_effective=previous_effective,
                current_effective=current_effective,
            )
        elif current_effective != previous_effective:
            changed_wallet = next(
                (
                    wallet
                    for wallet in self.traced_caps
                    if previous_traced.get(wallet, TokenBalanceSnapshot({}, 0)).total
                    != refreshed_traced.get(wallet, TokenBalanceSnapshot({}, 0)).total
                ),
                None,
            )
            linked_attribution = Attribution("UNKNOWN", evidence="trace-depth-1 linked inventory changed")
            if changed_wallet:
                old_linked = previous_traced.get(changed_wallet, TokenBalanceSnapshot({}, 0))
                new_linked = refreshed_traced.get(changed_wallet, TokenBalanceSnapshot({}, 0))
                linked_delta = new_linked.total - old_linked.total
                changed_linked_accounts = {
                    address
                    for address in set(old_linked.accounts) | set(new_linked.accounts)
                    if old_linked.accounts.get(address, 0) != new_linked.accounts.get(address, 0)
                }
                linked_attribution = self.attributor.attribute(
                    linked_delta,
                    addresses=changed_linked_accounts,
                    wallet=changed_wallet,
                    mint=self.mint,
                    max_slot=new_linked.slot,
                )
            linked_event_type = (
                "LINKED_BALANCE_INCREASE"
                if current_effective > previous_effective
                else f"LINKED_{linked_attribution.event_type}"
            )
            self._write_and_print_change(
                linked_event_type,
                previous_snapshot.total,
                snapshot.total,
                linked_attribution,
                previous_effective=previous_effective,
                current_effective=current_effective,
            )
            attribution = linked_attribution
        if attribution:
            self._emit_thresholds(
                previous_effective,
                current_effective,
                attribution,
                primary_previous=previous_snapshot.total,
                primary_current=snapshot.total,
            )
        self.current_snapshot = snapshot

    def _program_subscribe(self, ws: Any, request_id: int, program_id: str, owner: str | None = None) -> None:
        owner = owner or self.wallet
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "programSubscribe",
            "params": [
                program_id,
                {
                    "encoding": "base64",
                    "commitment": "confirmed",
                    "filters": [
                        {"memcmp": {"offset": 0, "bytes": self.mint}},
                        {"memcmp": {"offset": 32, "bytes": owner}},
                    ],
                },
            ],
        }
        ws.send(json.dumps(request, separators=(",", ":")))

    def _is_relevant_notification(self, message: str) -> bool:
        try:
            body = json.loads(message)
            if body.get("method") != "programNotification":
                return False
            value = body["params"]["result"]["value"]
            data = value["account"]["data"]
            encoded = data[0] if isinstance(data, list) else data
            raw = base64.b64decode(encoded, validate=True)
            return len(raw) >= 72
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def _listen_once(self) -> None:
        ws = self.websocket_factory(self._websocket_url, timeout=min(10.0, self.poll_seconds))
        try:
            if hasattr(ws, "settimeout"):
                ws.settimeout(min(5.0, self.poll_seconds))
            request_id = 1
            for owner in [self.wallet, *self.traced_caps]:
                for program in TOKEN_PROGRAMS:
                    self._program_subscribe(ws, request_id, program, owner)
                    request_id += 1
            self.reconcile("post_subscribe")
            next_reconcile = time.monotonic() + self.poll_seconds
            while not self.stop_event.is_set():
                try:
                    message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    message = None
                if message and self._is_relevant_notification(message):
                    self.reconcile("websocket_notification")
                    next_reconcile = time.monotonic() + self.poll_seconds
                elif time.monotonic() >= next_reconcile:
                    self.reconcile("periodic_reconcile")
                    next_reconcile = time.monotonic() + self.poll_seconds
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def run(self, *, max_connections: int | None = None) -> None:
        self.reconcile("watch_start")
        reconnect_delay = 1.0
        connection_count = 0
        while not self.stop_event.is_set():
            if max_connections is not None and connection_count >= max_connections:
                return
            try:
                self.reconcile("pre_connect_reconcile")
                connection_count += 1
                self._listen_once()
                reconnect_delay = 1.0
            except KeyboardInterrupt:
                self.stop_event.set()
            except Exception as exc:
                print(
                    f"RECONNECT {self.console_label} wallet={short_address(self.wallet)} "
                    f"reason={redact_urls(type(exc).__name__)} delay={reconnect_delay:.0f}s",
                    file=sys.stderr,
                )
                if self.stop_event.is_set():
                    return
                self.sleeper(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30.0)


def _add_mint_argument(parser: argparse.ArgumentParser, default_mint: str | None) -> None:
    parser.add_argument(
        "--mint",
        type=validate_public_address,
        required=default_mint is None,
        default=default_mint,
        help="target SPL Token or Token-2022 mint",
    )


def _add_analysis_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--top", type=int, default=25, help="seller rows to display (default: 25)")
    parser.add_argument("--trace-depth", type=int, choices=(0, 1), default=1)
    parser.add_argument("--concentration-top-n", type=int, default=10)
    parser.add_argument("--dominant-threshold", type=Decimal, default=Decimal("60"))
    parser.add_argument("--still-loaded-pct", type=Decimal, default=Decimal("50"))
    parser.add_argument("--nearly-out-pct", type=Decimal, default=Decimal("10"))
    parser.add_argument("--jeet-out-pct", type=Decimal, default=Decimal("1"))


def build_parser(
    *,
    default_mint: str | None = None,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Solana token holder, seller-history, wallet, and whale-exit analyzer.",
        epilog="RPC/provider credentials are read only from environment variables and are never displayed.",
    )
    parser.add_argument(
        "--request-timeout",
        "--timeout-seconds",
        dest="request_timeout",
        type=float,
        default=30.0,
        help="per-attempt read timeout in seconds (default: 30; --timeout-seconds remains an alias)",
    )
    parser.add_argument(
        "--provider-retries",
        type=int,
        default=2,
        help="bounded retries after the initial provider/RPC attempt (default: 2; range: 0..10)",
    )
    parser.add_argument(
        "--provider-backoff-cap",
        type=float,
        default=8.0,
        help="maximum exponential/retry-after delay in seconds (default: 8; range: >0..60)",
    )
    parser.add_argument(
        "--max-rpc-requests",
        type=int,
        default=500,
        help="hard RPC-attempt budget per investigation, including retries (default: 500)",
    )
    parser.add_argument(
        "--max-signatures",
        type=int,
        default=10_000,
        help="hard unique-signature examination budget per investigation (default: 10000)",
    )
    parser.add_argument(
        "--max-transactions",
        type=int,
        default=5_000,
        help="hard unique full-transaction budget per investigation (default: 5000)",
    )
    parser.add_argument(
        "--max-estimated-provider-credits",
        type=int,
        default=60_000,
        help=(
            "hard pre-request estimated provider-credit ceiling per investigation "
            "(default: 60000; estimate is not authoritative billing data)"
        ),
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="resolve the mint's largest token accounts")
    _add_mint_argument(inspect_parser, default_mint)
    inspect_parser.add_argument("--top", "--limit", dest="top", type=int, default=25)

    scan_parser = subparsers.add_parser("scan", help="scan indexed mint-wide history and rank confirmed sellers")
    _add_mint_argument(scan_parser, default_mint)
    scan_parser.add_argument("--days", type=float, default=5.0)
    scan_parser.add_argument("--provider", choices=("helius",), default="helius")
    scan_parser.add_argument("--output-dir", type=Path, default=default_data_dir())
    scan_parser.add_argument("--refresh", action="store_true", help="ignore cached normalized JSONL")
    scan_parser.add_argument("--max-pages", type=int, default=1000)
    _add_analysis_options(scan_parser)

    wallet_parser = subparsers.add_parser("wallet", help="analyze one wallet's indexed target-token history")
    _add_mint_argument(wallet_parser, default_mint)
    wallet_parser.add_argument("--wallet", required=True, type=validate_public_address)
    wallet_parser.add_argument("--days", type=float, default=5.0)
    wallet_parser.add_argument("--provider", choices=("helius",), default="helius")
    wallet_parser.add_argument("--output-dir", type=Path, default=default_data_dir())
    wallet_parser.add_argument("--max-pages", type=int, default=1000)
    wallet_parser.add_argument("--trace-depth", type=int, choices=(0, 1), default=1)

    verify_parser = subparsers.add_parser(
        "verify-exit",
        help="strict forensic verification of a seller exit across deterministic transfer links",
    )
    _add_mint_argument(verify_parser, default_mint)
    verify_parser.add_argument("--wallet", required=True, type=validate_public_address)
    verify_parser.add_argument("--days", type=float, default=5.0)
    verify_parser.add_argument("--trace-depth", type=int, choices=(0, 1, 2, 3), default=3)
    verify_parser.add_argument("--provider", choices=("helius",), default="helius")
    verify_parser.add_argument("--output-dir", type=Path, default=default_data_dir())
    verify_parser.add_argument("--max-pages", type=int, default=1000)

    cluster_parser = subparsers.add_parser(
        "cluster-audit",
        help="bounded cross-asset wallet-relationship and target-inventory audit",
    )
    _add_mint_argument(cluster_parser, default_mint)
    cluster_parser.add_argument("--wallet", required=True, type=validate_public_address)
    cluster_parser.add_argument("--days", type=float, default=14.0)
    cluster_parser.add_argument("--graph-depth", type=int, choices=(0, 1, 2, 3, 4, 5, 6), default=3)
    cluster_parser.add_argument("--max-wallets", type=int, default=50)
    cluster_parser.add_argument("--max-pages", type=int, default=1000)
    cluster_parser.add_argument(
        "--max-graph-wallet-pages",
        type=int,
        default=250,
        help=(
            "per-wallet cap for seed-direct or target-lineage history (default: 250 pages / up to 25000 transactions); "
            "hitting it marks that wallet's coverage incomplete but does not consume the whole investigation budget"
        ),
    )
    cluster_parser.add_argument(
        "--deep-forensic",
        action="store_true",
        help=(
            "opt into recursive cross-asset FBI-style graph traversal; default mode scans full seed activity for direct links "
            "then recurses only through target-token-bearing downstream history"
        ),
    )
    cluster_parser.add_argument("--funding-lookback-days", type=int, default=365)
    cluster_parser.add_argument("--materiality-inventory-pct", type=Decimal, default=Decimal("0.01"))
    cluster_parser.add_argument("--provider", choices=("helius",), default="helius")
    cluster_parser.add_argument("--output-dir", type=Path, default=default_data_dir())

    file_parser = subparsers.add_parser("analyze-file", help="rerun ranking offline from normalized JSONL")
    _add_mint_argument(file_parser, default_mint)
    file_parser.add_argument("--input", required=True, type=Path)
    file_parser.add_argument("--output-dir", type=Path, default=default_data_dir())
    _add_analysis_options(file_parser)

    watch_parser = subparsers.add_parser("watch", help="watch one public wallet's total target-token balance")
    _add_mint_argument(watch_parser, default_mint)
    watch_parser.add_argument("--wallet", required=True, type=validate_public_address, help="public Solana address only")
    watch_parser.add_argument("--trace-depth", type=int, choices=(0, 1), default=1)
    watch_parser.add_argument("--poll-seconds", type=float, default=30.0, help="full reconciliation interval (default: 30)")
    watch_parser.add_argument("--log", type=Path, default=None, help="bounded JSONL path")
    watch_parser.add_argument("--max-log-bytes", type=int, default=5 * 1024 * 1024, help="rotate after this size")
    watch_parser.add_argument("--log-backups", type=int, default=3, help="number of rotated files to keep (0..20)")
    return parser


def _analysis_config(args: argparse.Namespace) -> history.AnalysisConfig:
    if args.top < 1 or args.concentration_top_n < 1:
        raise WatcherError("--top and --concentration-top-n must be positive")
    if not Decimal(0) <= args.jeet_out_pct <= args.nearly_out_pct <= args.still_loaded_pct <= Decimal(100):
        raise WatcherError("status percentages must satisfy 0 <= jeet-out <= nearly-out <= still-loaded <= 100")
    if not Decimal(0) <= args.dominant_threshold <= Decimal(100):
        raise WatcherError("--dominant-threshold must be between 0 and 100")
    return history.AnalysisConfig(
        top=args.top,
        concentration_top_n=args.concentration_top_n,
        dominant_threshold=args.dominant_threshold,
        still_loaded_pct=args.still_loaded_pct,
        nearly_out_pct=args.nearly_out_pct,
        jeet_out_pct=args.jeet_out_pct,
        trace_depth=args.trace_depth,
    )


def _metadata_record(metadata: history.TokenMetadata) -> dict[str, Any]:
    record = asdict(metadata)
    if record.get("price_usd") is not None:
        record["price_usd"] = str(record["price_usd"])
    return record


def _metadata_from_record(record: Mapping[str, Any]) -> history.TokenMetadata:
    token = dict(record)
    if token.get("price_usd") is not None:
        token["price_usd"] = Decimal(str(token["price_usd"]))
    token["supply"] = int(token["supply"])
    token["decimals"] = int(token["decimals"])
    return history.TokenMetadata(**token)


def _helius_provider(args: argparse.Namespace, *, timeout_seconds: float) -> tuple[history.HeliusHistoricalProvider, ReadOnlyRpcClient]:
    api_key = os.environ.get("HELIUS_API_KEY", "").strip()
    if not api_key:
        raise history.CoverageError(
            "HELIUS_API_KEY is not set; ordinary public RPC is not complete mint-wide history, so scan/wallet mode failed closed"
        )
    helius_url = "https://mainnet.helius-rpc.com/?" + urlencode({"api-key": api_key})
    investigation = getattr(args, "investigation", None)
    indexed_rpc = ReadOnlyRpcClient(
        helius_url,
        timeout_seconds=timeout_seconds,
        max_retries=args.provider_retries,
        backoff_cap_seconds=args.provider_backoff_cap,
        investigation=investigation,
    )
    provider = history.HeliusHistoricalProvider(
        api_key,
        indexed_rpc,
        timeout_seconds=timeout_seconds,
        max_retries=args.provider_retries,
        max_pages=args.max_pages,
        backoff_cap_seconds=args.provider_backoff_cap,
        investigation=investigation,
    )
    return provider, indexed_rpc


def _window(days: float) -> tuple[int, int, str]:
    if days <= 0 or days > 3650:
        raise WatcherError("--days must be greater than 0 and no more than 3650")
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    return int(start_dt.timestamp()), int(end_dt.timestamp()), f"{days:g} days"


def _window_paths(output_dir: Path, mint: str, start: int, end: int) -> tuple[Path, Path, Path]:
    start_stamp = datetime.fromtimestamp(start, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end_stamp = datetime.fromtimestamp(end, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{mint}-{start_stamp}-{end_stamp}"
    return (
        output_dir / f"normalized-events-{stem}.jsonl",
        output_dir / f"seller-leaderboard-{stem}.csv",
        output_dir / f"summary-{stem}.json",
    )


def _balances(provider: history.HistoricalProvider, events: Sequence[history.NormalizedEvent], mint: str) -> dict[str, int | None]:
    wallets = {
        wallet
        for event in events
        for wallet in (event.wallet, event.destination)
        if wallet
    }
    balances: dict[str, int | None] = {}
    for wallet in sorted(wallets):
        try:
            balances[str(wallet)] = provider.get_wallet_token_balance(str(wallet), mint)
        except (RpcError, history.ProviderError, ProviderBudgetExhausted):
            balances[str(wallet)] = None
    return balances


def _run_scan(args: argparse.Namespace, *, timeout_seconds: float) -> int:
    provider, rpc = _helius_provider(args, timeout_seconds=timeout_seconds)
    start, end, window_label = _window(args.days)
    events_path, csv_path, summary_path = _window_paths(args.output_dir, args.mint, start, end)
    if events_path.exists() and not args.refresh:
        header, events = history.read_normalized_jsonl(events_path)
        if header.get("mint") != args.mint:
            raise history.HistoryError("cached normalized file mint does not match --mint")
        if not header.get("coverage_complete"):
            raise history.CoverageError("cached history is marked incomplete")
        metadata = _metadata_from_record(header["token"])
        balances = {wallet: (None if value is None else int(value)) for wallet, value in header.get("current_balances", {}).items()}
        print(f"CACHE_HIT normalized_history={events_path}")
    else:
        batch = provider.get_token_events(args.mint, start, end)
        if not batch.complete:
            metadata = history.TokenMetadata(args.mint, "UNKNOWN", None, 0, 0)
            events = history.normalize_transactions(batch.transactions, args.mint)
            balances = {}
            header = {
                "mint": args.mint,
                "start": start,
                "end": end,
                "provider": batch.provider,
                "coverage_complete": False,
                "coverage_scope": batch.coverage_scope,
                "coverage_limitation": batch.limitation,
                "pages": batch.pages,
                "token": _metadata_record(metadata),
                "current_balances": {},
                "provider_retry_count": batch.retry_count,
                "provider_terminal_failure_reason": batch.terminal_failure_reason,
                "request_telemetry": (
                    args.investigation.to_record() if getattr(args, "investigation", None) else {}
                ),
            }
            history.write_normalized_jsonl(events_path, header, events)
            failure_summary = {
                "schema": "jeet-analyzer.scan.v1",
                "status": "INSUFFICIENT_DATA",
                "mint": args.mint,
                "token": _metadata_record(metadata),
                "window": {"start": start, "end": end, "label": window_label},
                "provider_coverage": {
                    "complete": False,
                    "scope": batch.coverage_scope,
                    "limitation": batch.limitation,
                    "failure_category": batch.failure_category,
                },
                "observed_partial_events": len(events),
                "sellers": [],
                "request_telemetry": header["request_telemetry"],
                "normalized_events_file": str(events_path),
            }
            history.write_reports(csv_path, summary_path, failure_summary)
            print(f"REASON: indexed history incomplete: {redact_urls(batch.limitation)}", file=sys.stderr)
            print("STATUS: INSUFFICIENT_DATA")
            print(f"NORMALIZED_JSONL: {events_path}")
            print(f"SELLER_CSV: {csv_path}")
            print(f"SUMMARY_JSON: {summary_path}")
            return 2
        try:
            metadata = resolve_metadata(rpc, args.mint, provider)
        except ProviderBudgetExhausted:
            metadata = history.TokenMetadata(args.mint, "UNKNOWN", None, 0, 0)
        events = history.normalize_transactions(batch.transactions, args.mint)
        balances = (
            {}
            if getattr(args, "investigation", None) and args.investigation.expansion_exhausted
            else _balances(provider, events, args.mint)
        )
        budget_limited = bool(
            getattr(args, "investigation", None) and args.investigation.expansion_exhausted
        )
        header = {
            "mint": args.mint,
            "start": start,
            "end": end,
            "provider": batch.provider,
            "coverage_complete": batch.complete and not budget_limited,
            "coverage_scope": batch.coverage_scope,
            "coverage_limitation": (
                args.investigation.termination_reason if budget_limited else batch.limitation
            ),
            "pages": batch.pages,
            "token": _metadata_record(metadata),
            "current_balances": balances,
            "provider_retry_count": batch.retry_count + int(_client_telemetry(rpc).get("retry_count") or 0),
            "provider_terminal_failure_reason": _client_telemetry(rpc).get("terminal_failure_reason"),
            "request_telemetry": (
                args.investigation.to_record() if getattr(args, "investigation", None) else {}
            ),
        }
        history.write_normalized_jsonl(events_path, header, events)
        if budget_limited:
            failure_summary = {
                "schema": "jeet-analyzer.scan.v1",
                "status": "INSUFFICIENT_DATA",
                "mint": args.mint,
                "token": _metadata_record(metadata),
                "window": {"start": start, "end": end, "label": window_label},
                "provider_coverage": {
                    "complete": False,
                    "limitation": args.investigation.termination_reason,
                    "failure_category": "PROVIDER_BUDGET_EXHAUSTED",
                },
                "observed_partial_events": len(events),
                "sellers": [],
                "request_telemetry": args.investigation.to_record(),
                "normalized_events_file": str(events_path),
            }
            history.write_reports(csv_path, summary_path, failure_summary)
            print(f"REASON: {args.investigation.termination_reason}", file=sys.stderr)
            print("STATUS: INSUFFICIENT_DATA")
            print(f"NORMALIZED_JSONL: {events_path}")
            print(f"SELLER_CSV: {csv_path}")
            print(f"SUMMARY_JSON: {summary_path}")
            return 2
    summary = history.analyze_sellers(
        events,
        metadata,
        balances,
        coverage_complete=True,
        config=_analysis_config(args),
    )
    summary["window"] = {"start": start, "end": end, "label": window_label}
    summary["provider"] = "helius"
    summary["request_telemetry"] = (
        args.investigation.to_record() if getattr(args, "investigation", None) else {}
    )
    summary["normalized_events_file"] = str(events_path)
    history.write_reports(csv_path, summary_path, summary)
    history.print_analysis(summary, window_label=window_label, mint=args.mint)
    print(f"NORMALIZED_JSONL: {events_path}")
    print(f"SELLER_CSV: {csv_path}")
    print(f"SUMMARY_JSON: {summary_path}")
    return 0


def _run_wallet(args: argparse.Namespace, *, timeout_seconds: float) -> int:
    provider, rpc = _helius_provider(args, timeout_seconds=timeout_seconds)
    start, end, window_label = _window(args.days)
    batch = provider.get_wallet_events(args.wallet, args.mint, start, end)
    try:
        metadata = resolve_metadata(rpc, args.mint, provider)
    except (WatcherError, history.HistoryError, ProviderBudgetExhausted):
        metadata = history.TokenMetadata(args.mint, "UNKNOWN", None, 0, 0)
    events = history.normalize_transactions(batch.transactions, args.mint)
    history.apply_transfer_links(events, args.trace_depth)
    relevant = [event for event in events if event.wallet == args.wallet or event.destination == args.wallet]
    try:
        current: int | None = provider.get_wallet_token_balance(args.wallet, args.mint)
    except (WatcherError, history.HistoryError, ProviderBudgetExhausted, OSError):
        current = None
    sells = [event for event in relevant if event.wallet == args.wallet and event.event_type == "SELL"]
    buys = [event for event in relevant if event.wallet == args.wallet and event.event_type == "BUY"]
    incoming = [event for event in relevant if event.event_type == "TRANSFER" and event.destination == args.wallet]
    outgoing = [event for event in relevant if event.event_type == "TRANSFER" and event.wallet == args.wallet]
    linked_wallets = sorted(
        {wallet for event in relevant for wallet in [event.destination, *event.linked_from] if wallet and wallet != args.wallet}
    )
    lifecycle_result = lifecycle.analyze_wallet_lifecycle(
        events,
        args.wallet,
        current,
        coverage_complete=batch.complete,
        material_threshold_raw=0,
    )
    starting_value = lifecycle_result["reconstructed_starting_inventory_raw"]
    starting = int(starting_value) if starting_value is not None else 0
    remaining = None if starting <= 0 or current is None else Decimal(current) * Decimal(100) / Decimal(starting)
    print(f"TOKEN: {metadata.symbol}")
    print(f"MINT: {args.mint}")
    print(f"WALLET: {args.wallet}")
    print(f"WINDOW: {window_label}")
    current_display = "UNKNOWN" if current is None else amount_text(current, metadata.decimals)
    print(f"CURRENT BALANCE: {current_display}")
    print(f"HISTORICAL_EXIT_STATUS: {lifecycle_result['historical_exit_status']}")
    print(f"CURRENT_WALLET_STATUS: {lifecycle_result['current_wallet_status']}")
    print(f"CURRENT_TARGET_TOKEN_BALANCE: {current_display}")
    print(f"INCOMING TRANSFERS: {len(incoming)}")
    print(f"OUTGOING TRANSFERS: {len(outgoing)}")
    print(f"CONFIRMED SELLS: {len(sells)}")
    print(f"CONFIRMED BUYS/REACQUISITIONS: {len(buys)}")
    print(f"REACQUISITIONS: {lifecycle_result['reacquisitions']['count']}")
    print(
        f"LARGEST SELL: {amount_text(max((event.token_amount_raw for event in sells), default=0), metadata.decimals)}"
    )
    mean_interval, median_interval = history._interval_stats([event.block_time for event in sells])
    print(f"SELL FREQUENCY: average={mean_interval or 'UNKNOWN'}s median={median_interval or 'UNKNOWN'}s")
    print(f"ATTRIBUTABLE PROCEEDS: {history.quote_display(history._quote_totals(sells))}")
    print(f"TRANSFER-LINKED WALLETS: {','.join(linked_wallets) if linked_wallets else 'NONE'}")
    print(f"RECONSTRUCTED STARTING POSITION: {amount_text(starting, metadata.decimals) if starting > 0 else 'UNKNOWN'}")
    print(f"OBSERVED POSITION REMAINING: {str(remaining) + '%' if remaining is not None else 'UNKNOWN'}")
    output_path = args.output_dir / f"wallet-events-{args.mint}-{args.wallet}-{int(start)}-{int(end)}.jsonl"
    history.write_normalized_jsonl(
        output_path,
        {
            "mint": args.mint,
            "wallet": args.wallet,
            "start": start,
            "end": end,
            "provider": batch.provider,
            "coverage_complete": batch.complete,
            "coverage_scope": batch.coverage_scope,
            "coverage_limitation": batch.limitation,
            "token": _metadata_record(metadata),
            "current_balances": {args.wallet: current},
            "lifecycle": lifecycle_result,
            "provider_retry_count": batch.retry_count,
            "provider_terminal_failure_reason": batch.terminal_failure_reason,
            "rpc_telemetry": _client_telemetry(rpc),
            "request_telemetry": (
                args.investigation.to_record() if getattr(args, "investigation", None) else {}
            ),
        },
        relevant,
    )
    print(f"NORMALIZED_JSONL: {output_path}")
    if not batch.complete:
        print(f"REASON: indexed wallet history incomplete: {redact_urls(batch.limitation)}", file=sys.stderr)
        print(f"STATUS: {lifecycle_result['current_wallet_status']}")
        return 0 if current is not None and current > 0 else 2
    return 0


def _verification_paths(output_dir: Path, mint: str, wallet: str, start: int, end: int) -> tuple[Path, Path]:
    stem = f"{mint}-{wallet}-{start}-{end}"
    return (
        output_dir / f"verify-exit-events-{stem}.jsonl",
        output_dir / f"verify-exit-{stem}.json",
    )


def _write_verification_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    temporary.replace(path)


def _print_verification(report: Mapping[str, Any], *, decimals: int, window_label: str) -> None:
    print(f"TOKEN: {report['token']['symbol']}")
    print(f"MINT: {report['mint']}")
    print(f"SELLER WALLET: {report['wallet']}")
    print(f"WINDOW: {window_label}")
    print(f"TRACE DEPTH: {report['trace_depth']}")
    print(f"HISTORICAL_EXIT_STATUS: {report.get('historical_exit_status', report['status'])}")
    print(f"CURRENT_WALLET_STATUS: {report.get('current_wallet_status', report['status'])}")
    print(f"PROVIDER COVERAGE COMPLETE: {str(report['coverage']['complete']).upper()}")
    print(
        "RECONSTRUCTED STARTING INVENTORY: "
        + history.display_amount(report.get("reconstructed_starting_inventory_raw"), decimals)
    )
    print("OBSERVED INVENTORY: " + history.display_amount(report.get("observed_inventory_raw"), decimals))
    print("FRESH ORIGINAL-WALLET INVENTORY: " + history.display_amount(report.get("fresh_current_inventory_raw"), decimals))
    print(
        f"CONFIRMED SALES: count={report['confirmed_sales']['count']} "
        f"tokens={history.display_amount(report['confirmed_sales']['amount_raw'], decimals)}"
    )
    print(
        f"TRANSFERS: incoming={report['incoming_transfers']['count']} "
        f"outgoing={report['outgoing_transfers']['count']} "
        f"linked_movements={report['transfer_linked_movements']['count']}"
    )
    print(
        f"ROOT REACQUISITIONS: count={report['reacquisitions']['count']} "
        f"tokens={history.display_amount(report['reacquisitions']['amount_raw'], decimals)}"
    )
    print(
        f"LINKED-WALLET SEPARATE INCREASES: count={report['linked_wallet_increases']['count']} "
        f"tokens={history.display_amount(report['linked_wallet_increases']['amount_raw'], decimals)}"
    )
    print(
        f"BURNS: count={report['burns']['count']} "
        f"tokens={history.display_amount(report['burns']['amount_raw'], decimals)}"
    )
    if report["trace_edges"]:
        print("EVIDENCE-BACKED TRACE EDGES:")
        for edge in report["trace_edges"]:
            print(
                f"  {edge['relationship_type']} depth={edge['depth']} "
                f"source={edge['source_owner']} destination={edge['destination_owner']} "
                f"amount={history.display_amount(edge['amount_raw'], decimals)} "
                f"timestamp={edge['timestamp']} signature={edge['signature']} "
                f"destination_classification="
                f"{(edge.get('destination_address_classification') or {}).get('classification', 'UNRESOLVED')}"
            )
    else:
        print("EVIDENCE-BACKED TRACE EDGES: NONE")
    if report["traced_wallets"]:
        print("TRACED TRANSFER-LINKED WALLETS:")
        for row in report["traced_wallets"]:
            print(
                f"  depth={row['depth']} wallet={row['wallet']} "
                f"current={history.display_amount(row['fresh_current_inventory_raw'], decimals)} "
                f"attributable={history.display_amount(row['attributable_current_inventory_raw'], decimals)} "
                f"classification={row.get('classification', 'UNRESOLVED')} "
                f"path={' -> '.join(row['path'])} common_control=NOT_INFERRED"
            )
    else:
        print("TRACED TRANSFER-LINKED WALLETS: NONE")
    if report["unproven_inventory_relationships"]:
        print("UNPROVEN INVENTORY RELATIONSHIPS (NO TRACE EDGE):")
        for row in report["unproven_inventory_relationships"]:
            print(
                f"  source={row['source_owner']} candidate_destination={row['candidate_destination_owner']} "
                f"amount={history.display_amount(row['amount_raw'], decimals)} "
                f"timestamp={row['timestamp']} signature={row['signature']}"
            )
    if report["unresolved_branches"]:
        print(f"UNRESOLVED BRANCHES: {len(report['unresolved_branches'])}")
        for row in report["unresolved_branches"]:
            print(
                f"  kind={row['kind']} signature={row.get('signature') or 'NONE'} "
                f"detail={row['detail']}"
            )
    for reason in report["reasons"]:
        print(f"REASON: {reason}")


def _run_verify_exit(args: argparse.Namespace, *, timeout_seconds: float) -> int:
    start, end, window_label = _window(args.days)
    events_path, report_path = _verification_paths(args.output_dir, args.mint, args.wallet, start, end)
    provider: history.HeliusHistoricalProvider | None = None
    try:
        provider, _indexed_rpc = _helius_provider(args, timeout_seconds=timeout_seconds)
        batch = provider.get_token_events(args.mint, start, end)
        events = history.normalize_transactions(
            batch.transactions,
            args.mint,
            include_unknown_increases=True,
        )

        # Historical indexing and current-chain reconciliation deliberately use
        # separate clients.  SOLANA_RPC_URL supplies the fresh finalized owner
        # token-account aggregation and is never printed.
        fresh_rpc = ReadOnlyRpcClient(
            rpc_url_from_environment(),
            timeout_seconds=timeout_seconds,
            max_retries=args.provider_retries,
            backoff_cap_seconds=args.provider_backoff_cap,
            investigation=getattr(args, "investigation", None),
        )
        metadata = resolve_metadata(fresh_rpc, args.mint, provider)
        discovered = history.discover_traced_wallets(events, args.wallet, args.trace_depth)
        current_balances: dict[str, int | None] = {}
        current_balance_slots: dict[str, int | None] = {}
        for wallet in sorted(discovered):
            try:
                snapshot = fresh_rpc.get_owner_token_accounts(wallet, args.mint, commitment="finalized")
                current_balances[wallet] = snapshot.total
                current_balance_slots[wallet] = snapshot.slot
            except RpcError:
                current_balances[wallet] = None
                current_balance_slots[wallet] = None
        address_classifications: dict[str, dict[str, str]] = {}
        try:
            account_records = fresh_rpc.get_multiple_accounts(sorted(discovered))
        except RpcError:
            account_records = {wallet: None for wallet in discovered}
        for wallet in sorted(discovered):
            classification, note = classify_owner_account(account_records.get(wallet))
            address_classifications[wallet] = {"classification": classification, "note": note}

        report = history.verify_exit(
            events,
            metadata,
            args.wallet,
            current_balances,
            coverage_complete=batch.complete,
            coverage_scope=batch.coverage_scope,
            coverage_limitation=batch.limitation,
            trace_depth=args.trace_depth,
            window_start=start,
            window_end=end,
        )
        lifecycle_result = lifecycle.analyze_wallet_lifecycle(
            events,
            args.wallet,
            current_balances.get(args.wallet),
            coverage_complete=batch.complete,
            material_threshold_raw=0,
        )
        report["historical_exit_status"] = lifecycle_result["historical_exit_status"]
        report["current_wallet_status"] = lifecycle_result["current_wallet_status"]
        report["lifecycle"] = lifecycle_result
        report["window"] = {"start": start, "end": end, "days": args.days, "label": window_label}
        report["provider"] = batch.provider
        report["provider_pages"] = batch.pages
        report["fresh_balance_source"] = "finalized SOLANA_RPC_URL or public mainnet RPC fallback; URL redacted"
        report["fresh_balance_slots"] = current_balance_slots
        report["fresh_balances_reconciled_at"] = utc_timestamp()
        report["provider_requests"] = [
            {
                "provider": batch.provider,
                "scope": batch.coverage_scope,
                "complete": batch.complete,
                "retry_count": batch.retry_count,
                "terminal_failure_reason": batch.terminal_failure_reason,
                "failure_category": batch.failure_category,
            },
            {"provider": "solana_rpc", "scope": "fresh finalized balances", **_client_telemetry(fresh_rpc)},
        ]
        report["request_telemetry"] = (
            args.investigation.to_record() if getattr(args, "investigation", None) else {}
        )
        report["address_classifications"] = address_classifications
        for row in report["traced_wallets"]:
            row.update(address_classifications.get(row["wallet"], {}))
        for edge in report["trace_edges"]:
            edge["source_address_classification"] = address_classifications.get(edge["source_owner"])
            edge["destination_address_classification"] = address_classifications.get(edge["destination_owner"])
        history.write_normalized_jsonl(
            events_path,
            {
                "mint": args.mint,
                "wallet": args.wallet,
                "start": start,
                "end": end,
                "provider": batch.provider,
                "coverage_complete": batch.complete,
                "coverage_scope": batch.coverage_scope,
                "coverage_limitation": batch.limitation,
                "pages": batch.pages,
                "token": _metadata_record(metadata),
                "current_balances": current_balances,
                "trace_depth": args.trace_depth,
                "lifecycle": lifecycle_result,
                "provider_retry_count": batch.retry_count,
                "provider_terminal_failure_reason": batch.terminal_failure_reason,
                "provider_requests": report["provider_requests"],
                "request_telemetry": report["request_telemetry"],
            },
            events,
        )
        _write_verification_report(report_path, report)
        _print_verification(report, decimals=metadata.decimals, window_label=window_label)
        print(f"NORMALIZED_JSONL: {events_path}")
        print(f"VERIFICATION_JSON: {report_path}")
        print(f"STATUS: {report['status']}")
        return 2 if report["status"] == "INSUFFICIENT_DATA" else 0
    except (WatcherError, history.HistoryError, ProviderBudgetExhausted, OSError) as exc:
        # A provider/RPC failure cannot be turned into a forensic conclusion.
        # Keep secrets and URL query parameters out of terminal output.
        telemetry = provider.telemetry() if provider is not None else {
            "retry_count": getattr(exc, "retry_count", 0),
            "terminal_failure_reason": redact_urls(exc),
            "failure_category": getattr(exc, "category", "PROVIDER_ERROR"),
        }
        safe_reason = redact_urls(exc)
        failure_report = {
            "schema": "jeet-analyzer.verify-exit.v1",
            "mint": args.mint,
            "wallet": args.wallet,
            "window": {"start": start, "end": end, "days": args.days, "label": window_label},
            "trace_depth": args.trace_depth,
            "status": "INSUFFICIENT_DATA",
            "historical_exit_status": "INSUFFICIENT_DATA",
            "current_wallet_status": "INSUFFICIENT_DATA",
            "coverage": {
                "complete": False,
                "provider": "helius",
                "scope": "verify-exit",
                "limitation": safe_reason,
                "retry_count": int(telemetry.get("retry_count") or 0),
                "terminal_failure_reason": redact_urls(
                    telemetry.get("terminal_failure_reason") or safe_reason
                ),
                "failure_category": telemetry.get("failure_category") or "PROVIDER_ERROR",
            },
            "reasons": [safe_reason],
            "common_control": "NOT_PROVEN",
            "request_telemetry": (
                args.investigation.to_record() if getattr(args, "investigation", None) else {}
            ),
        }
        history.write_normalized_jsonl(
            events_path,
            {
                "mint": args.mint,
                "wallet": args.wallet,
                "start": start,
                "end": end,
                "provider": "helius",
                "coverage_complete": False,
                "coverage_limitation": safe_reason,
                "provider_retry_count": failure_report["coverage"]["retry_count"],
                "provider_terminal_failure_reason": failure_report["coverage"]["terminal_failure_reason"],
                "request_telemetry": failure_report["request_telemetry"],
            },
            [],
        )
        _write_verification_report(report_path, failure_report)
        print(f"REASON: {redact_urls(exc)}", file=sys.stderr)
        print("STATUS: INSUFFICIENT_DATA")
        print(f"NORMALIZED_JSONL: {events_path}")
        print(f"VERIFICATION_JSON: {report_path}")
        return 2


def _cluster_paths(output_dir: Path, mint: str, wallet: str, start: int, end: int) -> tuple[Path, Path]:
    stem = f"{mint}-{wallet}-{start}-{end}"
    return output_dir / f"cluster-audit-{stem}.json", output_dir / f"cluster-audit-events-{stem}.jsonl"


def _run_cluster_audit(args: argparse.Namespace, *, timeout_seconds: float) -> int:
    start, end, _window_label = _window(args.days)
    report_path, events_path = _cluster_paths(args.output_dir, args.mint, args.wallet, start, end)
    tracker = ProgressTracker()
    tracker.running("TOKEN_RESOLUTION", "Resolving target token metadata")
    provider: history.HeliusHistoricalProvider | None = None
    try:
        provider, _indexed_rpc = _helius_provider(args, timeout_seconds=timeout_seconds)
        fresh_rpc = ReadOnlyRpcClient(
            rpc_url_from_environment(),
            timeout_seconds=timeout_seconds,
            max_retries=args.provider_retries,
            backoff_cap_seconds=args.provider_backoff_cap,
            investigation=getattr(args, "investigation", None),
        )
        metadata = resolve_metadata(fresh_rpc, args.mint, provider)
        tracker.complete("TOKEN_RESOLUTION", "Target token metadata resolved")
        report = cluster.run_cluster_audit(
            provider,
            fresh_rpc,
            metadata,
            args.wallet,
            start,
            end,
            config=cluster.ClusterAuditConfig(
                graph_depth=args.graph_depth,
                max_wallets=args.max_wallets,
                max_graph_wallet_pages=args.max_graph_wallet_pages,
                funding_lookback_days=args.funding_lookback_days,
                materiality_inventory_pct=args.materiality_inventory_pct,
                deep_forensic=args.deep_forensic,
            ),
            progress=tracker,
            investigation=getattr(args, "investigation", None),
        )
        reporting.write_cluster_receipts(report_path, events_path, report)
        reporting.print_cluster_audit(report)
        print(f"CLUSTER_AUDIT_JSON: {report_path}")
        print(f"CLUSTER_AUDIT_EVENTS_JSONL: {events_path}")
        return 2 if report["cluster_status"] == "INSUFFICIENT_DATA" else 0
    except (WatcherError, history.HistoryError, ProviderBudgetExhausted, OSError) as exc:
        for phase, state in list(tracker.states.items()):
            if state == "running":
                tracker.error(phase, exc)
        if tracker.states["CLUSTER_CONCLUSION"] == "pending":
            tracker.incomplete("CLUSTER_CONCLUSION", "Conclusion not reached because required evidence failed")
        telemetry = provider.telemetry() if provider is not None else {
            "retry_count": getattr(exc, "retry_count", 0),
            "terminal_failure_reason": redact_urls(exc),
            "failure_category": getattr(exc, "category", "PROVIDER_ERROR"),
        }
        failure = report_contract.failure_result(
            mint=args.mint,
            seed_wallet=args.wallet,
            start=start,
            end=end,
            reason=redact_urls(exc),
            provider_telemetry=telemetry,
            progress=tracker,
            request_telemetry=(
                args.investigation.to_record() if getattr(args, "investigation", None) else {}
            ),
        )
        reporting.write_cluster_receipts(report_path, events_path, failure)
        print(f"REASON: {redact_urls(exc)}", file=sys.stderr)
        print("WALLET_STATUS: INSUFFICIENT_DATA")
        print("CLUSTER_STATUS: INSUFFICIENT_DATA")
        print(f"CLUSTER_AUDIT_JSON: {report_path}")
        print(f"CLUSTER_AUDIT_EVENTS_JSONL: {events_path}")
        return 2


def _run_analyze_file(args: argparse.Namespace) -> int:
    header, events = history.read_normalized_jsonl(args.input)
    if header.get("mint") != args.mint:
        raise history.HistoryError("normalized JSONL mint does not match --mint")
    if not header.get("coverage_complete"):
        raise history.CoverageError("normalized JSONL is marked incomplete; ranking failed closed")
    metadata = _metadata_from_record(header["token"])
    balances = {wallet: (None if value is None else int(value)) for wallet, value in header.get("current_balances", {}).items()}
    summary = history.analyze_sellers(
        events,
        metadata,
        balances,
        coverage_complete=True,
        config=_analysis_config(args),
    )
    stem = args.input.stem
    csv_path = args.output_dir / f"seller-leaderboard-{stem}.csv"
    summary_path = args.output_dir / f"summary-{stem}.json"
    history.write_reports(csv_path, summary_path, summary)
    history.print_analysis(summary, window_label="offline normalized file", mint=args.mint)
    print(f"SELLER_CSV: {csv_path}")
    print(f"SUMMARY_JSON: {summary_path}")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    default_mint: str | None = None,
) -> int:
    parser = build_parser(default_mint=default_mint)
    args = parser.parse_args(argv)
    if args.request_timeout <= 0:
        parser.error("--request-timeout must be positive")
    if not 0 <= args.provider_retries <= 10:
        parser.error("--provider-retries must be between 0 and 10")
    if not 0 < args.provider_backoff_cap <= 60:
        parser.error("--provider-backoff-cap must be greater than 0 and no more than 60")
    if args.max_rpc_requests < 1:
        parser.error("--max-rpc-requests must be positive")
    if args.max_signatures < 1:
        parser.error("--max-signatures must be positive")
    if args.max_transactions < 1:
        parser.error("--max-transactions must be positive")
    if args.max_estimated_provider_credits < 1:
        parser.error("--max-estimated-provider-credits must be positive")
    investigation = None
    if args.mode not in {"watch", "analyze-file"}:
        investigation = InvestigationContext(
            InvestigationLimits(
                max_rpc_requests=args.max_rpc_requests,
                max_signatures=args.max_signatures,
                max_transactions=args.max_transactions,
                max_estimated_provider_credits=args.max_estimated_provider_credits,
            )
        )
    args.investigation = investigation
    try:
        rpc_url = rpc_url_from_environment()
    except WatcherError as exc:
        if args.mode in {"verify-exit", "cluster-audit"}:
            print(f"REASON: {redact_urls(exc)}", file=sys.stderr)
            if args.mode == "cluster-audit":
                print("WALLET_STATUS: INSUFFICIENT_DATA")
                print("CLUSTER_STATUS: INSUFFICIENT_DATA")
            else:
                print("STATUS: INSUFFICIENT_DATA")
        else:
            print(f"ERROR: {redact_urls(exc)}", file=sys.stderr)
        return 2
    rpc = ReadOnlyRpcClient(
        rpc_url,
        timeout_seconds=args.request_timeout,
        max_retries=args.provider_retries,
        backoff_cap_seconds=args.provider_backoff_cap,
        investigation=investigation,
    )

    try:
        if args.mode == "inspect":
            provider = None
            all_accounts = None
            key = os.environ.get("HELIUS_API_KEY", "").strip()
            metadata_rpc = rpc
            if key:
                helius_url = "https://mainnet.helius-rpc.com/?" + urlencode({"api-key": key})
                metadata_rpc = ReadOnlyRpcClient(
                    helius_url,
                    timeout_seconds=args.request_timeout,
                    max_retries=args.provider_retries,
                    backoff_cap_seconds=args.provider_backoff_cap,
                    investigation=investigation,
                )
                provider = history.HeliusHistoricalProvider(
                    key,
                    metadata_rpc,
                    timeout_seconds=args.request_timeout,
                    max_retries=args.provider_retries,
                    backoff_cap_seconds=args.provider_backoff_cap,
                    investigation=investigation,
                )
                try:
                    all_accounts = provider.get_all_token_accounts(args.mint)
                except history.ProviderError:
                    all_accounts = None
            metadata = resolve_metadata(rpc, args.mint, provider)
            inspect_holders(
                rpc,
                args.mint,
                limit=args.top,
                symbol=metadata.symbol,
                all_token_accounts=all_accounts,
            )
            return 0
        if args.mode == "scan":
            return _run_scan(args, timeout_seconds=args.request_timeout)
        if args.mode == "wallet":
            return _run_wallet(args, timeout_seconds=args.request_timeout)
        if args.mode == "verify-exit":
            return _run_verify_exit(args, timeout_seconds=args.request_timeout)
        if args.mode == "cluster-audit":
            return _run_cluster_audit(args, timeout_seconds=args.request_timeout)
        if args.mode == "analyze-file":
            return _run_analyze_file(args)
        if args.poll_seconds < 1:
            parser.error("--poll-seconds must be at least 1")
        metadata_provider = None
        metadata_rpc = rpc
        key = os.environ.get("HELIUS_API_KEY", "").strip()
        if key:
            helius_url = "https://mainnet.helius-rpc.com/?" + urlencode({"api-key": key})
            metadata_rpc = ReadOnlyRpcClient(
                helius_url,
                timeout_seconds=args.request_timeout,
                max_retries=args.provider_retries,
                backoff_cap_seconds=args.provider_backoff_cap,
                investigation=investigation,
            )
            metadata_provider = history.HeliusHistoricalProvider(
                key,
                metadata_rpc,
                timeout_seconds=args.request_timeout,
                max_retries=args.provider_retries,
                backoff_cap_seconds=args.provider_backoff_cap,
                investigation=investigation,
            )
        metadata = resolve_metadata(rpc, args.mint, metadata_provider)
        log_path = args.log or default_log_path(args.mint, args.wallet)
        writer = BoundedJsonlWriter(log_path, max_bytes=args.max_log_bytes, backups=args.log_backups)
        stop_event = threading.Event()
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
        watcher = WalletWatcher(
            rpc,
            rpc_url=rpc_url,
            wallet=args.wallet,
            mint=args.mint,
            decimals=metadata.decimals,
            symbol=metadata.symbol,
            trace_depth=args.trace_depth,
            legacy_console_label=None,
            writer=writer,
            poll_seconds=args.poll_seconds,
            stop_event=stop_event,
        )
        watcher.run()
        return 0
    except (WatcherError, history.HistoryError, ProviderBudgetExhausted) as exc:
        print(f"ERROR: {redact_urls(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
