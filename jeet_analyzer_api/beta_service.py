"""Bounded beta job execution around the existing forensic engine adapter."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from jeet_analyzer.case_export import build_public_case, replay_cluster_receipt
from jeet_analyzer.progress import safe_message

from .beta_config import BetaConfig
from .beta_store import BetaStorage, BetaLimitError
from jeet_analyzer.analyzer import validate_public_address
from .models import InvestigationMode
from .service import EngineAdapter


def request_fingerprint(request: dict[str, Any]) -> str:
    encoded = json.dumps(request, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _recover_cluster_receipt(output_dir: Path) -> tuple[dict[str, Any], dict[str, str]] | None:
    """Return a usable engine receipt written before a late adapter failure."""

    if not output_dir.is_dir():
        return None
    candidates = sorted(
        output_dir.glob("cluster-audit-*.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for result_path in candidates:
        try:
            value = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        if value.get("schema") != "jeet-analyzer.result.v1" or value.get("common_control") != "NOT_PROVEN":
            continue
        try:
            # Replaying the receipt exercises the fields needed to reconstruct
            # the evidence conclusion without making another provider call.
            replay_cluster_receipt(value)
        except Exception:
            continue
        artifacts = {"result_json": str(result_path)}
        events = sorted(
            output_dir.glob("cluster-audit-events-*.jsonl"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        if events:
            artifacts["evidence_jsonl"] = str(events[0])
        return value, artifacts
    return None


def beta_view(record: dict[str, Any], *, duplicate: bool = False, include_result: bool = True) -> dict[str, Any]:
    result = record.get("result") if include_result else None
    return {
        "investigation_id": record["id"],
        "status": record["state"],
        "stage": record["stage"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "request": record["request"],
        "progress": record["progress"],
        "result": result,
        "error": record.get("error"),
        "termination_reason": record.get("termination_reason"),
        "safe_report_available": record.get("safe_result") is not None,
        "estimated_provider_credits_reserved": record.get("estimated_credits_reserved", 0),
        "estimated_provider_credits_used": record.get("estimated_credits_used", 0),
        "credit_cost_is_estimated": True,
        "deduplicated": duplicate,
    }


class BetaJobManager:
    def __init__(self, *, config: BetaConfig, storage: BetaStorage, engine: EngineAdapter) -> None:
        self.config = config
        self.storage = storage
        self.engine = engine
        self.member_config = lambda code_id: self.config
        self.executor = ThreadPoolExecutor(max_workers=config.max_concurrent, thread_name_prefix="jeet-beta")

    def submit(self, *, code_id: str, mint: str, wallet: str) -> tuple[dict[str, Any], bool]:
        if not self.config.enabled:
            raise BetaLimitError("BETA_DISABLED", "Investigations are paused.", 503)
        validate_public_address(mint)
        validate_public_address(wallet)
        config = self.member_config(code_id)
        request = config.investigation_request(mint, wallet)
        record, duplicate = self.storage.create_or_get(
            code_id=code_id,
            fingerprint=request_fingerprint(request),
            request=request,
            config=config,
        )
        if not duplicate:
            self.executor.submit(self._execute, record["id"])
        return record, duplicate

    def submit_scan(self, *, code_id: str, mint: str) -> tuple[dict[str, Any], bool]:
        """Expose the existing seller-scan engine through the same admission ledger."""
        if not self.config.enabled:
            raise BetaLimitError("BETA_DISABLED", "Investigations are paused.", 503)
        validate_public_address(mint)
        config = self.member_config(code_id)
        request = config.investigation_request(mint, "")
        request["investigation_mode"] = InvestigationMode.SELLER_SCAN.value
        record, duplicate = self.storage.create_or_get(
            code_id=code_id, fingerprint=request_fingerprint(request), request=request, config=config,
        )
        if not duplicate:
            self.executor.submit(self._execute, record["id"])
        return record, duplicate

    def _execute(self, identifier: str) -> None:
        record = self.storage.get(identifier)
        if record is None:
            return
        self.storage.mark_running(identifier)
        output_dir = self.config.output_root / identifier
        credit_ceiling = int(record["estimated_credits_reserved"])
        try:
            mode = InvestigationMode(record["request"].get("investigation_mode", InvestigationMode.CLUSTER_AUDIT.value))
            run = self.engine.run(mode, record["request"], output_dir)
            self.storage.mark_stage(identifier, "BUILDING_REPORT", "Evidence collection ended; the result contract is being finalized.")
            self.storage.complete(
                identifier,
                result=run.result,
                safe_result=self._safe_export(run.result, credit_ceiling),
                artifacts={name: str(path) for name, path in run.artifact_paths.items()},
            )
        except Exception as exc:  # boundary intentionally returns no traceback or credential-bearing URL
            # If the engine reached atomic receipt creation before a later
            # error, preserve that evidence and return it to the beta user.
            # The receipt's own coverage status is authoritative and is never
            # upgraded by this recovery path.
            recovered = _recover_cluster_receipt(output_dir)
            if recovered is not None:
                result, artifacts = recovered
                self.storage.mark_stage(
                    identifier,
                    "BUILDING_REPORT",
                    "A usable forensic receipt was recovered after a late engine error; its coverage limits remain unchanged.",
                )
                self.storage.complete(
                    identifier,
                    result=result,
                    safe_result=self._safe_export(result, credit_ceiling),
                    artifacts=artifacts,
                )
                return
            self.storage.fail(
                identifier,
                message=safe_message(exc) or "Investigation failed safely. No conclusion was strengthened.",
                reason="ENGINE_EXECUTION_FAILED",
            )

    @staticmethod
    def _safe_export(result: dict[str, Any], credit_ceiling: int) -> dict[str, Any] | None:
        try:
            replay = replay_cluster_receipt(result)
            telemetry = result.get("request_telemetry")
            estimated_credits = (
                telemetry.get("estimated_provider_credits", 0)
                if isinstance(telemetry, dict)
                else 0
            )
            safe_result = build_public_case(
                replay,
                estimated_provider_credits=int(estimated_credits),
                credit_ceiling=credit_ceiling,
            )
            safe_result["brand"] = "JEET ANALYZER"
            safe_result["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            return safe_result
        except Exception:
            # A share export is optional. Never weaken or replace the forensic result.
            return None

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
