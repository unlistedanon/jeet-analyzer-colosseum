"""Durable SQLite storage and atomic quota enforcement for the hosted beta."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from typing import Any, Iterator, Protocol

from .beta_config import BetaConfig


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class BetaLimitError(RuntimeError):
    def __init__(self, kind: str, message: str, status_code: int = 429) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


class BetaStorage(Protocol):
    def initialize(self) -> None: ...
    def recover_interrupted(self) -> int: ...
    def create_or_get(self, *, code_id: str, fingerprint: str, request: dict[str, Any], config: BetaConfig) -> tuple[dict[str, Any], bool]: ...
    def get(self, identifier: str, *, code_id: str | None = None) -> dict[str, Any] | None: ...
    def mark_running(self, identifier: str) -> None: ...
    def mark_stage(self, identifier: str, stage: str, message: str) -> None: ...
    def complete(self, identifier: str, *, result: dict[str, Any], safe_result: dict[str, Any] | None, artifacts: dict[str, str]) -> None: ...
    def fail(self, identifier: str, *, message: str, reason: str) -> None: ...
    def add_feedback(self, *, identifier: str, code_id: str, useful: bool | None, comment: str) -> str: ...
    def admin_summary(self) -> dict[str, Any]: ...


SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS beta_investigations (
        id TEXT PRIMARY KEY,
        code_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        state TEXT NOT NULL,
        stage TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        request_json TEXT NOT NULL,
        progress_json TEXT NOT NULL,
        result_json TEXT,
        safe_result_json TEXT,
        artifacts_json TEXT NOT NULL DEFAULT '{}',
        error TEXT,
        termination_reason TEXT,
        estimated_credits_reserved INTEGER NOT NULL,
        estimated_credits_used INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_beta_investigations_code_created ON beta_investigations(code_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_beta_investigations_state ON beta_investigations(state)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_beta_active_duplicate ON beta_investigations(code_id, fingerprint) WHERE state IN ('queued', 'running')",
    """
    CREATE TABLE IF NOT EXISTS beta_feedback (
        id TEXT PRIMARY KEY,
        investigation_id TEXT NOT NULL,
        code_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        useful INTEGER,
        comment TEXT NOT NULL,
        FOREIGN KEY(investigation_id) REFERENCES beta_investigations(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_beta_feedback_created ON beta_feedback(created_at)",
)


class SQLiteBetaStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            for statement in SCHEMA:
                connection.execute(statement)
            connection.execute("PRAGMA optimize")
            connection.commit()

    def ready(self) -> bool:
        try:
            with self._connection() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def recover_interrupted(self) -> int:
        timestamp = utc_now()
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE beta_investigations
                SET state='failed', stage='FAILED', updated_at=?, completed_at=?,
                    error='The beta process restarted before this investigation finished.',
                    termination_reason='SERVER_RESTARTED',
                    estimated_credits_used=(
                        CASE WHEN state='running' THEN estimated_credits_reserved ELSE 0 END
                    )
                WHERE state IN ('queued', 'running')
                """,
                (timestamp, timestamp),
            )
            connection.commit()
            return int(cursor.rowcount)

    @staticmethod
    def _initial_progress(timestamp: str) -> dict[str, Any]:
        return {
            "stage": "QUEUED",
            "events": [
                {"stage": "VALIDATING", "state": "complete", "timestamp": timestamp, "message": "Invite, request, and provider budgets validated."},
                {"stage": "QUEUED", "state": "running", "timestamp": timestamp, "message": "Investigation admitted to the bounded worker queue."},
            ],
        }

    def create_or_get(
        self,
        *,
        code_id: str,
        fingerprint: str,
        request: dict[str, Any],
        config: BetaConfig,
    ) -> tuple[dict[str, Any], bool]:
        timestamp = utc_now()
        day = timestamp[:10]
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT * FROM beta_investigations WHERE code_id=? AND fingerprint=? AND state IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
                (code_id, fingerprint),
            ).fetchone()
            if duplicate is not None:
                connection.commit()
                return self._record(duplicate), True
            active = int(connection.execute("SELECT COUNT(*) FROM beta_investigations WHERE state IN ('queued','running')").fetchone()[0])
            if active >= config.max_concurrent:
                connection.rollback()
                raise BetaLimitError("BETA_CONCURRENCY_LIMIT", "The beta is at its investigation concurrency limit. Try again shortly.", 503)
            runs = int(connection.execute(
                "SELECT COUNT(*) FROM beta_investigations WHERE code_id=? AND substr(created_at,1,10)=?",
                (code_id, day),
            ).fetchone()[0])
            if config.max_runs_per_code_per_day > 0 and runs >= config.max_runs_per_code_per_day:
                connection.rollback()
                raise BetaLimitError("BETA_CODE_DAILY_QUOTA", "This invite has reached its daily investigation quota.")
            reserved = int(connection.execute(
                """
                SELECT COALESCE(SUM(
                    CASE WHEN state IN ('queued','running')
                         THEN estimated_credits_reserved
                         ELSE estimated_credits_used END
                ),0)
                FROM beta_investigations WHERE substr(created_at,1,10)=?
                """,
                (day,),
            ).fetchone()[0])
            per_run = config.max_estimated_credits_per_run
            if config.max_estimated_credits_per_code_per_day is not None:
                member_committed = int(connection.execute(
                    "SELECT COALESCE(SUM(CASE WHEN state IN ('queued','running') THEN estimated_credits_reserved ELSE estimated_credits_used END),0) FROM beta_investigations WHERE code_id=? AND substr(created_at,1,10)=?",
                    (code_id, day),
                ).fetchone()[0])
                remaining = config.max_estimated_credits_per_code_per_day - member_committed
                if remaining <= 0:
                    connection.rollback()
                    raise BetaLimitError("BETA_MEMBER_DAILY_CREDITS", "Your daily credit allowance is used up. It resets at midnight UTC.")
                per_run = min(per_run, remaining, max(0, config.global_daily_credit_cap - reserved))
                request = {**request, "max_estimated_provider_credits": per_run}
            if per_run <= 0 or reserved + per_run > config.global_daily_credit_cap:
                connection.rollback()
                raise BetaLimitError("BETA_GLOBAL_DAILY_CREDIT_CAP", "The beta has reached its configured daily provider budget.", 503)
            identifier = str(uuid.uuid4())
            progress = self._initial_progress(timestamp)
            connection.execute(
                """
                INSERT INTO beta_investigations(
                    id, code_id, fingerprint, state, stage, created_at, updated_at,
                    request_json, progress_json, estimated_credits_reserved
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    identifier, code_id, fingerprint, "queued", "QUEUED", timestamp, timestamp,
                    json.dumps(request, separators=(",", ":"), sort_keys=True),
                    json.dumps(progress, separators=(",", ":")), per_run,
                ),
            )
            row = connection.execute("SELECT * FROM beta_investigations WHERE id=?", (identifier,)).fetchone()
            connection.commit()
            return self._record(row), False

    @staticmethod
    def _json(value: str | None, default: Any) -> Any:
        return json.loads(value) if value else default

    def _record(self, row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["request"] = self._json(value.pop("request_json"), {})
        value["progress"] = self._json(value.pop("progress_json"), {"stage": value.get("stage"), "events": []})
        value["result"] = self._json(value.pop("result_json"), None)
        value["safe_result"] = self._json(value.pop("safe_result_json"), None)
        value["artifacts"] = self._json(value.pop("artifacts_json"), {})
        return value

    def get(self, identifier: str, *, code_id: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM beta_investigations WHERE id=?"
        parameters: tuple[Any, ...] = (identifier,)
        if code_id is not None:
            query += " AND code_id=?"
            parameters = (identifier, code_id)
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
            return self._record(row) if row is not None else None

    def _stage(self, identifier: str, *, state: str | None, stage: str, message: str) -> None:
        timestamp = utc_now()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT progress_json FROM beta_investigations WHERE id=?", (identifier,)).fetchone()
            if row is None:
                connection.rollback()
                return
            progress = self._json(row[0], {"stage": stage, "events": []})
            progress["stage"] = stage
            event_state = "running" if state in {None, "running"} else "complete"
            progress.setdefault("events", []).append({"stage": stage, "state": event_state, "timestamp": timestamp, "message": message})
            assignments = ["stage=?", "updated_at=?", "progress_json=?"]
            values: list[Any] = [stage, timestamp, json.dumps(progress, separators=(",", ":"))]
            if state is not None:
                assignments.append("state=?")
                values.append(state)
            if state == "running":
                assignments.append("started_at=COALESCE(started_at,?)")
                values.append(timestamp)
            values.append(identifier)
            connection.execute(f"UPDATE beta_investigations SET {', '.join(assignments)} WHERE id=?", values)
            connection.commit()

    def mark_running(self, identifier: str) -> None:
        self._stage(
            identifier,
            state="running",
            stage="COLLECTING_HISTORY",
            message="The existing read-only forensic pipeline is collecting and classifying bounded evidence.",
        )

    def mark_stage(self, identifier: str, stage: str, message: str) -> None:
        self._stage(identifier, state=None, stage=stage, message=message)

    def complete(
        self,
        identifier: str,
        *,
        result: dict[str, Any],
        safe_result: dict[str, Any] | None,
        artifacts: dict[str, str],
    ) -> None:
        timestamp = utc_now()
        telemetry = result.get("request_telemetry") if isinstance(result.get("request_telemetry"), dict) else {}
        terminated = telemetry.get("termination_reason") or telemetry.get("terminated_by_budget")
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT progress_json, estimated_credits_reserved FROM beta_investigations WHERE id=?",
                (identifier,),
            ).fetchone()
            if row is None:
                return
            progress = self._json(row[0], {"events": []})
            reserved = int(row[1])
            raw_estimate = telemetry.get("estimated_provider_credits")
            try:
                estimated_used = max(0, min(int(raw_estimate), reserved))
            except (TypeError, ValueError):
                # A complete legacy/malformed result without usage telemetry is
                # charged conservatively rather than silently releasing quota.
                estimated_used = reserved
            progress["stage"] = "COMPLETE"
            progress.setdefault("events", []).extend([
                {"stage": "BUILDING_REPORT", "state": "complete", "timestamp": timestamp, "message": "The forensic receipt and public-safe report were finalized."},
                {"stage": "COMPLETE", "state": "complete", "timestamp": timestamp, "message": "Investigation complete. Coverage limitations remain part of the result."},
            ])
            connection.execute(
                """
                UPDATE beta_investigations
                SET state='complete', stage='COMPLETE', updated_at=?, completed_at=?,
                    progress_json=?, result_json=?, safe_result_json=?, artifacts_json=?,
                    termination_reason=?, estimated_credits_used=?
                WHERE id=?
                """,
                (
                    timestamp, timestamp, json.dumps(progress, separators=(",", ":")),
                    json.dumps(result, separators=(",", ":")),
                    json.dumps(safe_result, separators=(",", ":")) if safe_result is not None else None,
                    json.dumps(artifacts, separators=(",", ":")), str(terminated) if terminated else None,
                    estimated_used, identifier,
                ),
            )
            connection.commit()

    def fail(self, identifier: str, *, message: str, reason: str) -> None:
        timestamp = utc_now()
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT progress_json FROM beta_investigations WHERE id=?", (identifier,)).fetchone()
            if row is None:
                return
            progress = self._json(row[0], {"events": []})
            progress["stage"] = "FAILED"
            progress.setdefault("events", []).append({"stage": "FAILED", "state": "error", "timestamp": timestamp, "message": message})
            connection.execute(
                """
                UPDATE beta_investigations
                SET state='failed', stage='FAILED', updated_at=?, completed_at=?,
                    progress_json=?, error=?, termination_reason=?,
                    estimated_credits_used=estimated_credits_reserved
                WHERE id=?
                """,
                (timestamp, timestamp, json.dumps(progress, separators=(",", ":")), message, reason, identifier),
            )
            connection.commit()

    def add_feedback(self, *, identifier: str, code_id: str, useful: bool | None, comment: str) -> str:
        feedback_id = str(uuid.uuid4())
        with self._lock, self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM beta_investigations WHERE id=? AND code_id=?",
                (identifier, code_id),
            ).fetchone()
            if exists is None:
                raise KeyError(identifier)
            connection.execute(
                "INSERT INTO beta_feedback(id, investigation_id, code_id, created_at, useful, comment) VALUES(?,?,?,?,?,?)",
                (feedback_id, identifier, code_id, utc_now(), None if useful is None else int(useful), comment),
            )
            connection.commit()
        return feedback_id

    def admin_summary(self) -> dict[str, Any]:
        day = utc_day()
        with self._connection() as connection:
            totals = connection.execute(
                """
                SELECT COUNT(*) AS investigations,
                       COUNT(DISTINCT CASE WHEN state='complete' AND result_json IS NOT NULL THEN code_id END) AS completed_visitors,
                       COALESCE(SUM(CASE WHEN state='running' THEN 1 ELSE 0 END),0) AS running,
                       COALESCE(SUM(CASE WHEN state='queued' THEN 1 ELSE 0 END),0) AS queued,
                       COALESCE(SUM(CASE WHEN state='complete' THEN 1 ELSE 0 END),0) AS complete,
                       COALESCE(SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END),0) AS failed,
                       COALESCE(SUM(estimated_credits_used),0) AS estimated_credits,
                       COALESCE(SUM(CASE WHEN state IN ('queued','running') THEN estimated_credits_reserved ELSE 0 END),0) AS estimated_credits_reserved_active,
                       COALESCE(SUM(CASE WHEN state IN ('queued','running') THEN estimated_credits_reserved ELSE estimated_credits_used END),0) AS estimated_daily_budget_committed,
                       COALESCE(SUM(CASE WHEN termination_reason LIKE '%BUDGET%' OR termination_reason LIKE '%max_%' THEN 1 ELSE 0 END),0) AS budget_exhausted,
                       AVG(CASE WHEN started_at IS NOT NULL AND completed_at IS NOT NULL THEN (julianday(completed_at)-julianday(started_at))*86400 END) AS average_duration_seconds
                FROM beta_investigations WHERE substr(created_at,1,10)=?
                """,
                (day,),
            ).fetchone()
            by_code = [dict(row) for row in connection.execute(
                "SELECT code_id, COUNT(*) AS investigations, COALESCE(SUM(estimated_credits_used),0) AS estimated_credits FROM beta_investigations WHERE substr(created_at,1,10)=? GROUP BY code_id ORDER BY investigations DESC",
                (day,),
            ).fetchall()]
            feedback_count = int(connection.execute("SELECT COUNT(*) FROM beta_feedback").fetchone()[0])
            recent_feedback = [dict(row) for row in connection.execute(
                "SELECT id, investigation_id, code_id, created_at, useful, comment FROM beta_feedback ORDER BY created_at DESC LIMIT 50"
            ).fetchall()]
        result = dict(totals)
        result["day"] = day
        result["by_code"] = by_code
        result["feedback_count"] = feedback_count
        result["recent_feedback"] = recent_feedback
        result["estimated_credit_note"] = (
            "Counts are non-authoritative method-weighted estimates. Daily committed "
            "budget combines terminal estimated use with full active reservations."
        )
        return result
