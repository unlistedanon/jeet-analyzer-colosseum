"""Portable target and claim contracts. No provider calls or inferred intent."""
from dataclasses import dataclass, field
import re
from typing import Literal

from .analyzer import validate_public_address

TOKEN_PROGRAMS = frozenset({
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
})
SYSTEM_PROGRAM = "11111111111111111111111111111111"
ADDRESS = re.compile(r"(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9])")


def extract_addresses(text: str, *, limit: int = 8) -> list[str]:
    if not isinstance(text, str) or len(text) > 8192:
        raise ValueError("Message exceeds the supported length")
    found = []
    for match in ADDRESS.finditer(text):
        candidate = match.group()
        try:
            validate_public_address(candidate)
        except Exception:
            continue
        if candidate not in found:
            found.append(candidate)
        if len(found) > limit:
            raise ValueError("Too many addresses; send one target")
    return found


@dataclass(frozen=True)
class TargetClassification:
    address: str
    kind: Literal["mint", "wallet_account", "token_account", "unknown"] = "unknown"
    evidence: tuple[str, ...] = ()
    observed_at: str | None = None
    limitation: str = "No fresh account-type evidence is available. Payment purpose and human ownership are not proven."


def classify_target(address: str, account: dict | None = None, *, observed_at: str | None = None) -> TargetClassification:
    """Accept only a caller's budgeted, jsonParsed account-info receipt.

    Telegram context and command names are deliberately not classification evidence.
    An absent account cannot establish wallet ownership or payment purpose.
    """
    validate_public_address(address)
    if not account or not observed_at or account.get("executable") is not False:
        return TargetClassification(address)
    owner = account.get("owner")
    data = account.get("data")
    parsed = data.get("parsed") if isinstance(data, dict) else None
    if owner in TOKEN_PROGRAMS and isinstance(parsed, dict):
        kind = {"mint": "mint", "account": "token_account"}.get(parsed.get("type"))
        if kind:
            return TargetClassification(address, kind, ("Solana jsonParsed account owner and type",), observed_at,
                                        "Account type only; intent and ownership NOT_PROVEN.")
    if owner == SYSTEM_PROGRAM and account.get("space") == 0:
        return TargetClassification(address, "wallet_account", ("System-owned account with no data",), observed_at,
                                    "Wallet-like account; private-key control and payment intent NOT_PROVEN.")
    return TargetClassification(address, observed_at=observed_at)


@dataclass(frozen=True)
class ClaimObservedFlow:
    """Future engine-owned contract. MVP intentionally does not interpret claims."""
    target: str
    status: Literal["UNSUPPORTED"] = "UNSUPPORTED"
    observed_facts: tuple[str, ...] = ()
    inferences: tuple[str, ...] = ()
    risk_signals: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ("Natural-language intent and claimed use of funds are not verified.",)
    common_control: Literal["NOT_PROVEN"] = "NOT_PROVEN"
