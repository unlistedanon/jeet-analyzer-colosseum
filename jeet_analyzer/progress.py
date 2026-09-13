"""Stable machine-readable investigation progress events."""

from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping


PHASES = (
    "TOKEN_RESOLUTION",
    "SELLER_ANALYSIS",
    "WALLET_HISTORY",
    "TRANSACTION_CLASSIFICATION",
    "RELATIONSHIP_GRAPH",
    "FUNDING_ANCESTRY",
    "RELATED_INVENTORY",
    "CLUSTER_CONCLUSION",
    "RECEIPT_FINALIZATION",
)
STATES = ("pending", "running", "complete", "incomplete", "error")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def safe_message(value: object) -> str:
    text = str(value)
    # Never transport provider URLs because credentials are commonly embedded in
    # their query strings. Then redact common header/env credential spellings.
    text = re.sub(r"https?://[^\s]+", "[URL_REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?i)\b(api[-_ ]?key|x-api-key|token|secret|authorization)\b\s*[:=]\s*(?:bearer\s+)?([^\s,&;]+)",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/]+=*", "Bearer [REDACTED]", text)
    return text[:500]


@dataclass(frozen=True)
class ProgressEvent:
    sequence: int
    timestamp: str
    phase: str
    state: str
    message: str
    count: int | None = None
    progress: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None

    def to_record(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class ProgressTracker:
    def __init__(
        self,
        clock: Callable[[], str] = _timestamp,
        *,
        live_terminal: bool | None = None,
    ) -> None:
        self.clock = clock
        self.live_terminal = (
            bool(live_terminal) if live_terminal is not None else bool(sys.stderr.isatty())
        )
        self.events: list[ProgressEvent] = []
        self.states = {phase: "pending" for phase in PHASES}
        for phase in PHASES:
            self._append(phase, "pending", "Awaiting phase")

    def _emit_terminal(self, event: ProgressEvent) -> None:
        if not self.live_terminal or event.state == "pending":
            return
        details: list[str] = []
        if event.count is not None:
            details.append(f"count={event.count}")
        if event.progress:
            details.extend(f"{key}={value}" for key, value in sorted(event.progress.items()))
        suffix = f" | {' '.join(details)}" if details else ""
        print(
            f"[JEET] {event.phase} {event.state.upper()} | {event.message}{suffix}",
            file=sys.stderr,
            flush=True,
        )

    def _append(
        self,
        phase: str,
        state: str,
        message: object,
        *,
        count: int | None = None,
        progress: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if phase not in PHASES or state not in STATES:
            raise ValueError("unknown progress phase or state")
        self.states[phase] = state
        event = ProgressEvent(
            len(self.events) + 1,
            self.clock(),
            phase,
            state,
            safe_message(message),
            count,
            dict(progress) if progress is not None else None,
            dict(metadata) if metadata is not None else None,
        )
        self.events.append(event)
        self._emit_terminal(event)

    def update(self, phase: str, state: str, message: object, **kwargs: Any) -> None:
        self._append(phase, state, message, **kwargs)

    def running(self, phase: str, message: object, **kwargs: Any) -> None:
        self._append(phase, "running", message, **kwargs)

    def complete(self, phase: str, message: object, **kwargs: Any) -> None:
        self._append(phase, "complete", message, **kwargs)

    def incomplete(self, phase: str, message: object, **kwargs: Any) -> None:
        self._append(phase, "incomplete", message, **kwargs)

    def error(self, phase: str, message: object, **kwargs: Any) -> None:
        self._append(phase, "error", message, **kwargs)

    def to_record(self) -> dict[str, Any]:
        return {
            "phases": dict(self.states),
            "events": [event.to_record() for event in self.events],
        }
