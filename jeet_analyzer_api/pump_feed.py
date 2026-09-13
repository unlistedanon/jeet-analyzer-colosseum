"""Fail-closed, read-only Pump bonding-curve event feed for the paper game."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import base64
import json
import os
import threading
from typing import Any, Callable

import certifi
import websocket

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
CREATE_DISC = bytes([27, 114, 169, 77, 222, 235, 99, 118])
COMPLETE_DISC = bytes([95, 114, 97, 156, 212, 46, 152, 8])
SELL_DISC = bytes([62, 47, 55, 10, 165, 3, 220, 42])
BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    result = ""
    while number:
        number, remainder = divmod(number, 58)
        result = BASE58[remainder] + result
    return BASE58[0] * (len(raw) - len(raw.lstrip(b"\0"))) + (result or BASE58[0])


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    length = int.from_bytes(data[offset:offset + 4], "little")
    offset += 4
    end = offset + length
    return data[offset:end].decode("utf-8", errors="replace"), end


@dataclass
class PumpRound:
    mint: str
    creator: str
    bonding_curve: str
    completed_at: int
    completion_signature: str
    status: str = "ACTIVE"
    source: str = "Solana confirmed logs / Pump program"


class PumpRoundFeed:
    def __init__(self, rpc_url: str | None = None, *, on_round: Callable[[dict[str, Any]], None] | None = None, on_jeet: Callable[[str, int, str], None] | None = None) -> None:
        self.rpc_url = rpc_url or os.environ.get("SOLANA_RPC_URL", "")
        self._lock = threading.RLock()
        self._creator_by_mint: dict[str, str] = {}
        self._pending: list[dict[str, Any]] = []
        self._active: PumpRound | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None
        self._on_round = on_round
        self._on_jeet = on_jeet

    @property
    def enabled(self) -> bool:
        return bool(self.rpc_url)

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._run, name="jeet-pump-round-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "status": "live" if self._active else "waiting_for_confirmed_bonding_completion",
                "active_round": asdict(self._active) if self._active else None,
                "queued_rounds": len(self._pending),
                "last_error": self._last_error,
                "program": PUMP_PROGRAM_ID,
                "capability": "read-only",
            }

    def _run(self) -> None:
        ws_url = self.rpc_url.replace("https://", "wss://").replace("http://", "ws://", 1)
        while not self._stop.is_set():
            try:
                self._listen(ws_url)
            except Exception as exc:
                with self._lock:
                    self._last_error = type(exc).__name__
                self._stop.wait(5)

    def _listen(self, ws_url: str) -> None:
        socket = websocket.create_connection(
            ws_url,
            timeout=20,
            enable_multithread=True,
            sslopt={"cert_reqs": 2, "ca_certs": certifi.where()},
        )
        try:
            subscription_id = 1
            socket.send(json.dumps({
                "jsonrpc": "2.0", "id": subscription_id, "method": "logsSubscribe",
                "params": [{"mentions": [PUMP_PROGRAM_ID]}, {"commitment": "confirmed"}],
            }))
            socket.settimeout(20)
            subscribed_creator: str | None = None
            while not self._stop.is_set():
                with self._lock:
                    creator = self._active.creator if self._active else None
                if creator and creator != subscribed_creator:
                    subscription_id += 1
                    socket.send(json.dumps({
                        "jsonrpc": "2.0", "id": subscription_id, "method": "logsSubscribe",
                        "params": [{"mentions": [creator]}, {"commitment": "confirmed"}],
                    }))
                    subscribed_creator = creator
                message = json.loads(socket.recv())
                self._handle_message(message)
        finally:
            socket.close()

    def _handle_message(self, message: dict[str, Any]) -> None:
        value = message.get("params", {}).get("result", {}).get("value", {})
        if value.get("err") is not None:
            return
        signature = value.get("signature")
        for log in value.get("logs", []):
            if not isinstance(log, str) or not log.startswith("Program data: "):
                continue
            try:
                data = base64.b64decode(log.split(": ", 1)[1])
            except Exception:
                continue
            if data.startswith(CREATE_DISC):
                self._handle_create(data[8:])
            elif data.startswith(COMPLETE_DISC) and signature:
                self._handle_complete(data[8:], signature)
            elif data.startswith(SELL_DISC):
                self._handle_creator_sell(data[8:], signature)

    def _handle_create(self, data: bytes) -> None:
        try:
            _name, offset = _read_string(data, 0)
            _symbol, offset = _read_string(data, offset)
            _uri, offset = _read_string(data, offset)
            mint = _base58(data[offset:offset + 32]); offset += 32
            offset += 32  # bonding curve
            offset += 32  # user
            creator = _base58(data[offset:offset + 32])
        except (IndexError, UnicodeError, ValueError):
            return
        with self._lock:
            self._creator_by_mint[mint] = creator

    def _handle_complete(self, data: bytes, signature: str) -> None:
        if len(data) < 104:
            return
        mint = _base58(data[32:64])
        bonding_curve = _base58(data[64:96])
        timestamp = int.from_bytes(data[96:104], "little", signed=True)
        with self._lock:
            creator = self._creator_by_mint.get(mint)
            if not creator:
                self._last_error = "creator_missing_for_completion"
                return
            round_value = PumpRound(mint, creator, bonding_curve, timestamp, signature)
            self._pending.append(asdict(round_value))
            if self._active is None:
                self._active = round_value
            if self._on_round:
                self._on_round(asdict(round_value))

    def _handle_creator_sell(self, data: bytes, signature: str | None) -> None:
        # PumpSwap SellEvent places user at byte 144 and coin_creator at byte 304.
        if len(data) < 336 or not signature:
            return
        user = _base58(data[144:176])
        coin_creator = _base58(data[304:336])
        with self._lock:
            if self._active is None or user != self._active.creator or coin_creator != self._active.creator:
                return
            self._active.status = "JEET_CONFIRMED"
            current_mint = self._active.mint
            if self._on_jeet:
                self._on_jeet(current_mint, int.from_bytes(data[0:8], "little", signed=True), signature)
            self._pending = [row for row in self._pending if row["mint"] != current_mint]
            next_round = self._pending.pop(0) if self._pending else None
            self._active = PumpRound(**next_round) if next_round else None
