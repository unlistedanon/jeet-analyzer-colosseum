"""Token-agnostic Solana transaction autopsy and behavior classification.

Parsed SPL Token and Token-2022 instructions are authoritative.  This module
does not use positional outer-program accounts to overwrite parsed transfer
source, destination, authority, mint, or amount.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .models import AtaCreation, NativeTransfer, TokenTransfer, TransactionAutopsy


SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
TOKEN_PROGRAMS = frozenset({TOKEN_PROGRAM, TOKEN_2022_PROGRAM})

# Labels are optional hints.  Behavior and parsed asset flows remain
# authoritative, and unrecognized programs are preserved explicitly.
KNOWN_MARKET_PROGRAMS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "JUPITER_V6",
    "675kPX9MHTjS2zt1qfr1NYHuzefVAvHGHbGDXfL4fGF": "RAYDIUM_AMM_V4",
    "whirLbMiicVdio4qvUfM5KAg6CtX9NAjEEQyF9jb": "ORCA_WHIRLPOOL",
}
BENIGN_PROGRAMS = frozenset(
    {
        SYSTEM_PROGRAM,
        TOKEN_PROGRAM,
        TOKEN_2022_PROGRAM,
        ASSOCIATED_TOKEN_PROGRAM,
        COMPUTE_BUDGET_PROGRAM,
        MEMO_PROGRAM,
    }
)


def classify_address_record(record: Mapping[str, Any] | None) -> tuple[str, str]:
    """Conservatively classify an address before wallet-graph expansion."""

    if record is None:
        return "UNRESOLVED", "account unavailable; graph expansion blocked"
    if bool(record.get("executable")):
        return "PROGRAM_ACCOUNT", "executable program; not wallet-like"
    controlling_program = str(record.get("owner") or "")
    if controlling_program == SYSTEM_PROGRAM:
        return "WALLET_LIKE", "system-owned address; common control and human identity are not proven"
    if controlling_program in TOKEN_PROGRAMS:
        return "TOKEN_ACCOUNT_NOT_WALLET", "token account is inventory plumbing, not a wallet authority"
    if controlling_program in KNOWN_MARKET_PROGRAMS:
        return "KNOWN_INFRASTRUCTURE", "controlled by known market infrastructure"
    return "PROGRAM_PDA_OR_POOL_CANDIDATE", "program-controlled address; pool, vault, PDA, escrow, or multisig possible"


def transaction_signature(transaction: Mapping[str, Any]) -> str | None:
    if transaction.get("signature"):
        return str(transaction["signature"])
    signatures = transaction.get("transaction", {}).get("signatures", []) or []
    return str(signatures[0]) if signatures else None


def account_keys(transaction: Mapping[str, Any]) -> list[str]:
    message = transaction.get("transaction", {}).get("message", {})
    rows = message.get("accountKeys", []) or []
    keys = [str(row.get("pubkey")) if isinstance(row, Mapping) else str(row) for row in rows]
    loaded = (transaction.get("meta") or {}).get("loadedAddresses") or {}
    keys.extend(str(value) for value in loaded.get("writable", []) or [])
    keys.extend(str(value) for value in loaded.get("readonly", []) or [])
    return keys


def signer_keys(transaction: Mapping[str, Any]) -> list[str]:
    message = transaction.get("transaction", {}).get("message", {})
    rows = message.get("accountKeys", []) or []
    explicit = [
        str(row.get("pubkey"))
        for row in rows
        if isinstance(row, Mapping) and row.get("signer") and row.get("pubkey")
    ]
    if explicit:
        return list(dict.fromkeys(explicit))
    header = message.get("header", {}) or {}
    required = int(header.get("numRequiredSignatures", 1))
    return account_keys(transaction)[:required]


def iter_instructions(transaction: Mapping[str, Any]) -> Iterable[tuple[Mapping[str, Any], bool]]:
    message = transaction.get("transaction", {}).get("message", {})
    for instruction in message.get("instructions", []) or []:
        if isinstance(instruction, Mapping):
            yield instruction, False
    for group in (transaction.get("meta") or {}).get("innerInstructions", []) or []:
        for instruction in group.get("instructions", []) or []:
            if isinstance(instruction, Mapping):
                yield instruction, True


def _program_id(instruction: Mapping[str, Any], keys: Sequence[str]) -> str:
    if instruction.get("programId"):
        return str(instruction["programId"])
    try:
        return keys[int(instruction["programIdIndex"])]
    except (KeyError, IndexError, TypeError, ValueError):
        return ""


def _timestamp(block_time: int) -> str:
    return datetime.fromtimestamp(block_time, timezone.utc).isoformat().replace("+00:00", "Z")


def token_balance_rows(
    transaction: Mapping[str, Any], side: str
) -> dict[tuple[str, str], tuple[str | None, int, int]]:
    keys = account_keys(transaction)
    output: dict[tuple[str, str], tuple[str | None, int, int]] = {}
    for row in (transaction.get("meta") or {}).get(f"{side}TokenBalances", []) or []:
        try:
            account = keys[int(row["accountIndex"])]
            mint = str(row["mint"])
            owner = str(row["owner"]) if row.get("owner") else None
            amount = int(row["uiTokenAmount"]["amount"])
            decimals = int(row["uiTokenAmount"].get("decimals", 0))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        output[(account, mint)] = (owner, amount, decimals)
    return output


def owner_token_deltas(transaction: Mapping[str, Any]) -> dict[str, dict[str, tuple[int, int]]]:
    pre = token_balance_rows(transaction, "pre")
    post = token_balance_rows(transaction, "post")
    output: dict[str, dict[str, tuple[int, int]]] = {}
    for key in set(pre) | set(post):
        pre_owner, pre_amount, pre_decimals = pre.get(key, (None, 0, 0))
        post_owner, post_amount, post_decimals = post.get(key, (None, 0, 0))
        owner = post_owner or pre_owner
        if not owner:
            continue
        mint = key[1]
        current, decimals = output.setdefault(owner, {}).get(mint, (0, post_decimals or pre_decimals))
        output[owner][mint] = (current + post_amount - pre_amount, post_decimals or pre_decimals or decimals)
    return output


def _amount(info: Mapping[str, Any]) -> int | None:
    value = info.get("amount")
    if value is None and isinstance(info.get("tokenAmount"), Mapping):
        value = info["tokenAmount"].get("amount")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _mint_for_accounts(
    info: Mapping[str, Any],
    source: str,
    destination: str,
    pre: Mapping[tuple[str, str], tuple[str | None, int, int]],
    post: Mapping[tuple[str, str], tuple[str | None, int, int]],
) -> str | None:
    if info.get("mint"):
        return str(info["mint"])
    source_mints = {mint for account, mint in set(pre) | set(post) if account == source}
    destination_mints = {mint for account, mint in set(pre) | set(post) if account == destination}
    shared = source_mints.intersection(destination_mints)
    return next(iter(shared)) if len(shared) == 1 else None


def _parse_token_transfers(
    transaction: Mapping[str, Any], signature: str, block_time: int
) -> list[TokenTransfer]:
    keys = account_keys(transaction)
    pre = token_balance_rows(transaction, "pre")
    post = token_balance_rows(transaction, "post")
    output: list[TokenTransfer] = []
    seen: set[tuple[str, str, str, int]] = set()
    for instruction, inner in iter_instructions(transaction):
        program_id = _program_id(instruction, keys)
        if program_id not in TOKEN_PROGRAMS:
            continue
        parsed = instruction.get("parsed")
        if not isinstance(parsed, Mapping):
            continue
        instruction_type = str(parsed.get("type") or "").lower()
        if instruction_type not in {"transfer", "transferchecked"}:
            continue
        info = parsed.get("info")
        if not isinstance(info, Mapping):
            continue
        source = str(info.get("source") or "")
        destination = str(info.get("destination") or "")
        amount = _amount(info)
        if not source or not destination or amount is None:
            continue
        mint = _mint_for_accounts(info, source, destination, pre, post)
        if not mint:
            continue
        source_pre = pre.get((source, mint), (None, 0, 0))
        source_post = post.get((source, mint), (None, 0, 0))
        destination_pre = pre.get((destination, mint), (None, 0, 0))
        destination_post = post.get((destination, mint), (None, 0, 0))
        source_owner = source_post[0] or source_pre[0]
        destination_owner = destination_post[0] or destination_pre[0]
        if not source_owner or not destination_owner:
            continue
        authority = str(
            info.get("authority")
            or info.get("multisigAuthority")
            or source_owner
        )
        key = (source, destination, mint, amount)
        if key in seen:
            continue
        seen.add(key)
        decimals = destination_post[2] or destination_pre[2] or source_post[2] or source_pre[2]
        output.append(
            TokenTransfer(
                signature=signature,
                timestamp=_timestamp(block_time),
                block_time=block_time,
                mint=mint,
                token_program=program_id,
                instruction_type=instruction_type,
                source_token_account=source,
                destination_token_account=destination,
                source_owner=str(source_owner),
                destination_owner=str(destination_owner),
                authority=authority,
                amount_raw=amount,
                source_delta_raw=source_post[1] - source_pre[1],
                destination_delta_raw=destination_post[1] - destination_pre[1],
                decimals=decimals,
                inner=inner,
            )
        )
    return output


def _parse_native_transfers(
    transaction: Mapping[str, Any], signature: str, block_time: int, signers: Sequence[str], fee_payer: str | None
) -> list[NativeTransfer]:
    keys = account_keys(transaction)
    output: list[NativeTransfer] = []
    for instruction, _inner in iter_instructions(transaction):
        if _program_id(instruction, keys) != SYSTEM_PROGRAM:
            continue
        parsed = instruction.get("parsed")
        if not isinstance(parsed, Mapping) or str(parsed.get("type") or "").lower() not in {
            "transfer",
            "transferwithseed",
        }:
            continue
        info = parsed.get("info")
        if not isinstance(info, Mapping):
            continue
        source = str(info.get("source") or "")
        destination = str(info.get("destination") or "")
        try:
            lamports = int(info.get("lamports"))
        except (TypeError, ValueError):
            continue
        if source and destination and lamports > 0:
            output.append(
                NativeTransfer(
                    signature,
                    _timestamp(block_time),
                    block_time,
                    source,
                    destination,
                    lamports,
                    source in signers,
                    source == fee_payer,
                )
            )
    return output


def _parse_ata_creations(
    transaction: Mapping[str, Any], signature: str, block_time: int
) -> list[AtaCreation]:
    keys = account_keys(transaction)
    output: list[AtaCreation] = []
    for instruction, _inner in iter_instructions(transaction):
        if _program_id(instruction, keys) != ASSOCIATED_TOKEN_PROGRAM:
            continue
        parsed = instruction.get("parsed")
        if not isinstance(parsed, Mapping) or str(parsed.get("type") or "").lower() not in {
            "create",
            "createidempotent",
        }:
            continue
        info = parsed.get("info")
        if not isinstance(info, Mapping):
            continue
        payer = str(info.get("payer") or info.get("fundingAddress") or info.get("source") or "")
        owner = str(info.get("wallet") or info.get("owner") or "")
        token_account = str(info.get("account") or info.get("associatedAccount") or "")
        mint = str(info.get("mint")) if info.get("mint") else None
        if payer and owner and token_account:
            output.append(AtaCreation(signature, _timestamp(block_time), block_time, payer, owner, token_account, mint))
    return output


def _native_deltas(transaction: Mapping[str, Any], keys: Sequence[str]) -> dict[str, int]:
    meta = transaction.get("meta") or {}
    pre = meta.get("preBalances", []) or []
    post = meta.get("postBalances", []) or []
    return {
        key: int(post[index]) - int(pre[index])
        for index, key in enumerate(keys)
        if index < len(pre) and index < len(post) and int(post[index]) != int(pre[index])
    }


def autopsy_transaction(transaction: Mapping[str, Any]) -> TransactionAutopsy | None:
    """Normalize one successful raw transaction into generic evidence."""

    signature = transaction_signature(transaction)
    block_time = int(transaction.get("blockTime") or 0)
    if not signature or block_time <= 0:
        return None
    meta = transaction.get("meta") or {}
    if meta.get("err") is not None:
        return TransactionAutopsy(signature, _timestamp(block_time), block_time, None, [], [], [], failed=True)
    keys = account_keys(transaction)
    signers = signer_keys(transaction)
    fee_payer = signers[0] if signers else (keys[0] if keys else None)
    program_ids = sorted(
        {
            program_id
            for instruction, _inner in iter_instructions(transaction)
            if (program_id := _program_id(instruction, keys))
        }
    )
    token_deltas = owner_token_deltas(transaction)
    native_deltas = _native_deltas(transaction, keys)
    transfers = _parse_token_transfers(transaction, signature, block_time)
    native_transfers = _parse_native_transfers(transaction, signature, block_time, signers, fee_payer)
    ata_creations = _parse_ata_creations(transaction, signature, block_time)

    reasons: list[str] = []
    encountered = [KNOWN_MARKET_PROGRAMS[program] for program in program_ids if program in KNOWN_MARKET_PROGRAMS]
    if encountered:
        reasons.append("known market infrastructure: " + ",".join(encountered))
    logs = " ".join(str(row).lower() for row in meta.get("logMessages", []) or [])
    if any(term in logs for term in ("instruction: swap", "route", "swap2")):
        reasons.append("runtime swap/route log behavior")
    parsed_market_types = {
        str(instruction.get("parsed", {}).get("type") or "").lower()
        for instruction, _inner in iter_instructions(transaction)
        if isinstance(instruction.get("parsed"), Mapping)
    }
    if parsed_market_types.intersection({"swap", "buy", "sell", "route", "sharedaccountsroute"}):
        reasons.append("parsed market instruction behavior")
    for owner, assets in token_deltas.items():
        positive = {mint for mint, (amount, _decimals) in assets.items() if amount > 0}
        negative = {mint for mint, (amount, _decimals) in assets.items() if amount < 0}
        if positive and negative and positive != negative:
            reasons.append(f"reciprocal token assets for {owner}")
        native = native_deltas.get(owner, 0)
        if native > 0 and negative:
            reasons.append(f"token-out/native-in behavior for {owner}")
        if native < -int(meta.get("fee") or 0) and positive:
            reasons.append(f"native-out/token-in behavior for {owner}")
    unknown = [program for program in program_ids if program not in BENIGN_PROGRAMS and program not in KNOWN_MARKET_PROGRAMS]
    instruction_summaries: list[dict[str, Any]] = []
    for instruction, inner in iter_instructions(transaction):
        parsed = instruction.get("parsed")
        info = parsed.get("info") if isinstance(parsed, Mapping) else None
        instruction_summaries.append(
            {
                "program_id": _program_id(instruction, keys),
                "inner": inner,
                "parsed_type": str(parsed.get("type") or "") if isinstance(parsed, Mapping) else None,
                "source": str(info.get("source") or "") if isinstance(info, Mapping) else None,
                "destination": str(info.get("destination") or "") if isinstance(info, Mapping) else None,
                "authority": str(info.get("authority") or "") if isinstance(info, Mapping) else None,
            }
        )
    return TransactionAutopsy(
        signature=signature,
        timestamp=_timestamp(block_time),
        block_time=block_time,
        fee_payer=fee_payer,
        signers=signers,
        program_ids=program_ids,
        unknown_program_ids=unknown,
        instruction_summaries=instruction_summaries,
        token_transfers=transfers,
        native_transfers=native_transfers,
        ata_creations=ata_creations,
        token_owner_deltas=token_deltas,
        native_deltas=native_deltas,
        fee_lamports=int(meta.get("fee") or 0),
        market_context=bool(reasons),
        market_reasons=list(dict.fromkeys(reasons)),
    )
