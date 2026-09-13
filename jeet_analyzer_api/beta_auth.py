"""Constant-time invite verification and short-lived signed beta sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from .beta_config import BetaConfig, hash_secret


BETA_COOKIE = "jeet_beta_session"
ADMIN_COOKIE = "jeet_beta_admin"
MAX_SESSION_TOKEN_LENGTH = 4096


def verify_invite(code: str, configured: dict[str, str]) -> str | None:
    candidate = hash_secret(code)
    match: str | None = None
    for identifier, expected in configured.items():
        if hmac.compare_digest(candidate, expected):
            match = identifier
    return match


def verify_admin(code: str, expected_hash: str | None) -> bool:
    if not expected_hash:
        return False
    return hmac.compare_digest(hash_secret(code), expected_hash)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_session(config: BetaConfig, *, subject: str, kind: str, now: int | None = None) -> str:
    issued = int(time.time() if now is None else now)
    payload = {"sub": subject, "kind": kind, "iat": issued, "exp": issued + config.session_ttl_seconds}
    encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(config.session_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_encode(signature)}"


def read_session(config: BetaConfig, token: str | None, *, kind: str, now: int | None = None) -> dict[str, Any] | None:
    if not token or not config.session_secret or len(token) > MAX_SESSION_TOKEN_LENGTH:
        return None
    encoded, separator, supplied_signature = token.partition(".")
    if not separator or len(encoded) > 3072 or len(supplied_signature) > 512:
        return None
    expected = hmac.new(config.session_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    try:
        supplied = _decode(supplied_signature)
        if not hmac.compare_digest(expected, supplied):
            return None
        payload = json.loads(_decode(encoded))
    except (ValueError, json.JSONDecodeError):
        return None
    current = int(time.time() if now is None else now)
    if payload.get("kind") != kind or int(payload.get("exp") or 0) <= current:
        return None
    subject = str(payload.get("sub") or "")
    if not subject:
        return None
    if kind == "beta" and subject not in config.code_hashes:
        if not (config.public_access and subject.startswith("visitor-") and len(subject) == 40):
            return None
    return payload
