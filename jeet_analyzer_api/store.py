"""Thread-safe, process-local investigation registry."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import InvestigationMode, InvestigationState, InvestigationView


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def pending_progress() -> dict[str, Any]:
    return {"phases": {phase: "pending" for phase in PHASES}, "events": []}


def _append_progress_event(record: "InvestigationRecord", phase: str, state: str, message: str) -> None:
    record.progress["phases"][phase] = state
    record.progress["events"].append({
        "sequence": len(record.progress["events"]) + 1,
        "timestamp": _now(),
        "phase": phase,
        "state": state,
        "message": message,
    })


@dataclass
class InvestigationRecord:
    id: str
    mode: InvestigationMode
    state: InvestigationState
    created_at: str
    updated_at: str
    request: dict[str, Any]
    progress: dict[str, Any] = field(default_factory=pending_progress)
    result: dict[str, Any] | None = None
    error: str | None = None
    artifact_paths: dict[str, Path] = field(default_factory=dict)

    def view(self) -> InvestigationView:
        return InvestigationView(
            id=self.id,
            mode=self.mode,
            state=self.state,
            created_at=self.created_at,
            updated_at=self.updated_at,
            request=self.request,
            progress=self.progress,
            result=self.result,
            error=self.error,
            artifacts={name: f"/api/v1/investigations/{self.id}/artifacts/{name}" for name in self.artifact_paths},
        )


class InvestigationStore:
    def __init__(self) -> None:
        self._records: dict[str, InvestigationRecord] = {}
        self._lock = threading.RLock()

    def create(self, mode: InvestigationMode, request: dict[str, Any]) -> InvestigationRecord:
        identifier = str(uuid.uuid4())
        timestamp = _now()
        record = InvestigationRecord(identifier, mode, InvestigationState.QUEUED, timestamp, timestamp, request)
        with self._lock:
            self._records[identifier] = record
        return record

    def get(self, identifier: str) -> InvestigationRecord | None:
        with self._lock:
            return self._records.get(identifier)

    def mark_running(self, identifier: str) -> None:
        with self._lock:
            record = self._records[identifier]
            record.state = InvestigationState.RUNNING
            record.updated_at = _now()
            _append_progress_event(
                record,
                "TOKEN_RESOLUTION",
                "running",
                "Forensic engine execution started; detailed phase evidence follows from its receipt",
            )

    def complete(self, identifier: str, result: dict[str, Any], artifact_paths: dict[str, Path]) -> None:
        with self._lock:
            record = self._records[identifier]
            record.state = InvestigationState.COMPLETE
            record.result = result
            record.artifact_paths = artifact_paths
            record.updated_at = _now()
            receipt_progress = result.get("progress")
            if isinstance(receipt_progress, dict) and receipt_progress.get("phases"):
                record.progress = receipt_progress
            else:
                coverage = result.get("provider_coverage")
                complete_coverage = bool(coverage.get("complete")) if isinstance(coverage, dict) else False
                if record.mode is InvestigationMode.SELLER_SCAN:
                    _append_progress_event(record, "TOKEN_RESOLUTION", "complete", "Target token resolution receipt emitted")
                    _append_progress_event(
                        record,
                        "SELLER_ANALYSIS",
                        "complete" if complete_coverage else "incomplete",
                        "Seller leaderboard finalized" if complete_coverage else "Seller ranking failed closed because provider coverage is incomplete",
                    )
                elif record.mode is InvestigationMode.WALLET_AUDIT:
                    _append_progress_event(record, "TOKEN_RESOLUTION", "complete", "Target token resolution receipt emitted")
                    _append_progress_event(
                        record,
                        "WALLET_HISTORY",
                        "complete" if complete_coverage else "incomplete",
                        "Wallet history coverage completed" if complete_coverage else "Wallet history is incomplete; partial evidence was preserved",
                    )
                    _append_progress_event(record, "TRANSACTION_CLASSIFICATION", "complete", "Retrieved wallet evidence normalized and classified")
                _append_progress_event(record, "RECEIPT_FINALIZATION", "complete", "Machine-readable investigation receipt finalized")

    def fail(self, identifier: str, message: str) -> None:
        with self._lock:
            record = self._records[identifier]
            record.state = InvestigationState.ERROR
            record.error = message
            record.updated_at = _now()
            running = next((phase for phase, state in record.progress["phases"].items() if state == "running"), None)
            if running:
                _append_progress_event(record, running, "error", message)
            else:
                _append_progress_event(record, "RECEIPT_FINALIZATION", "error", message)
