"""Read-only operator views over the hosted beta SQLite ledger.

This module deliberately opens the beta database in SQLite read-only mode and
exposes only admin-session-protected inspection routes. Raw invite/admin
credentials, request fingerprints, and local artifact paths are never returned.
"""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .beta_auth import ADMIN_COOKIE, read_session
from .beta_config import BetaConfig


def _decode(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


def _open_read_only(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _require_admin(config: BetaConfig, request: Request) -> None:
    payload = read_session(config, request.cookies.get(ADMIN_COOKIE), kind="admin")
    if payload is None:
        raise HTTPException(status_code=401, detail="admin access required")


def _result_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "historical_exit_status": None,
            "current_wallet_status": None,
            "target_cluster_status": None,
            "relationship_status": None,
            "cluster_status": None,
            "common_control": None,
            "provider_complete": None,
            "relationship_count": 0,
        }
    coverage = result.get("provider_coverage") if isinstance(result.get("provider_coverage"), dict) else {}
    relationships = result.get("wallet_relationships") if isinstance(result.get("wallet_relationships"), list) else []
    return {
        "historical_exit_status": result.get("historical_exit_status"),
        "current_wallet_status": result.get("current_wallet_status"),
        "target_cluster_status": result.get("target_cluster_status"),
        "relationship_status": result.get("relationship_status"),
        "cluster_status": result.get("cluster_status"),
        "common_control": result.get("common_control"),
        "provider_complete": coverage.get("complete"),
        "relationship_count": len(relationships),
    }


def _operator_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the forensic result without exposing server-local receipt paths."""
    if not isinstance(result, dict):
        return result
    sanitized = dict(result)
    receipts = sanitized.get("evidence_receipts")
    if isinstance(receipts, dict):
        sanitized["evidence_receipts"] = {
            str(name): "available via the operator receipt download"
            for name in receipts
        }
    return sanitized


def recent_cases(path: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 200))
    try:
        with closing(_open_read_only(path)) as connection:
            rows = connection.execute(
                """
                SELECT id, code_id, state, stage, created_at, updated_at, completed_at,
                       request_json, result_json, error, termination_reason,
                       estimated_credits_reserved, estimated_credits_used
                FROM beta_investigations
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (bounded,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="beta operator ledger is unavailable") from exc

    records: list[dict[str, Any]] = []
    for row in rows:
        request = _decode(row["request_json"], {})
        result = _decode(row["result_json"], None)
        records.append(
            {
                "investigation_id": row["id"],
                "code_id": row["code_id"],
                "status": row["state"],
                "stage": row["stage"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "completed_at": row["completed_at"],
                "request": request,
                "error": row["error"],
                "termination_reason": row["termination_reason"],
                "estimated_provider_credits_reserved": int(row["estimated_credits_reserved"] or 0),
                "estimated_provider_credits_used": int(row["estimated_credits_used"] or 0),
                **_result_summary(result),
            }
        )
    return records


def case_detail(path: Path, identifier: str) -> dict[str, Any] | None:
    try:
        with closing(_open_read_only(path)) as connection:
            row = connection.execute(
                """
                SELECT id, code_id, state, stage, created_at, updated_at, started_at,
                       completed_at, request_json, progress_json, result_json,
                       safe_result_json, artifacts_json, error, termination_reason,
                       estimated_credits_reserved, estimated_credits_used
                FROM beta_investigations
                WHERE id=?
                """,
                (identifier,),
            ).fetchone()
            if row is None:
                return None
            feedback = [
                dict(item)
                for item in connection.execute(
                    """
                    SELECT id, created_at, useful, comment
                    FROM beta_feedback
                    WHERE investigation_id=?
                    ORDER BY created_at DESC
                    """,
                    (identifier,),
                ).fetchall()
            ]
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="beta operator ledger is unavailable") from exc

    artifacts = _decode(row["artifacts_json"], {})
    result = _decode(row["result_json"], None)
    return {
        "investigation_id": row["id"],
        "code_id": row["code_id"],
        "status": row["state"],
        "stage": row["stage"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "request": _decode(row["request_json"], {}),
        "progress": _decode(row["progress_json"], {"stage": row["stage"], "events": []}),
        "result": _operator_result(result),
        "safe_result": _decode(row["safe_result_json"], None),
        "safe_report_available": row["safe_result_json"] is not None,
        "artifact_names": sorted(artifacts.keys()) if isinstance(artifacts, dict) else [],
        "error": row["error"],
        "termination_reason": row["termination_reason"],
        "estimated_provider_credits_reserved": int(row["estimated_credits_reserved"] or 0),
        "estimated_provider_credits_used": int(row["estimated_credits_used"] or 0),
        "feedback": feedback,
    }


def create_operator_router(config: BetaConfig) -> APIRouter:
    router = APIRouter(prefix="/api/beta/admin", tags=["beta-admin"])

    @router.get("/investigations")
    def list_investigations(request: Request, limit: int = 50) -> JSONResponse:
        _require_admin(config, request)
        return JSONResponse(
            {"investigations": recent_cases(config.database_path, limit=limit)},
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/investigations/{identifier}")
    def get_investigation(identifier: str, request: Request) -> JSONResponse:
        _require_admin(config, request)
        record = case_detail(config.database_path, identifier)
        if record is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        return JSONResponse(record, headers={"Cache-Control": "no-store"})

    return router
