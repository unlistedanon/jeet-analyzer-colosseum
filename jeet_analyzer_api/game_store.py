"""Small SQLite ledger for the free-to-enter paper prediction contest."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class GameStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS game_players (
                    id TEXT PRIMARY KEY, points INTEGER NOT NULL DEFAULT 100,
                    last_refill_day TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS game_rounds (
                    id TEXT PRIMARY KEY, mint TEXT NOT NULL UNIQUE, creator TEXT NOT NULL,
                    completed_at INTEGER NOT NULL, completion_signature TEXT NOT NULL,
                    jeet_at INTEGER, jeet_signature TEXT, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS game_predictions (
                    id TEXT PRIMARY KEY, player_id TEXT NOT NULL, round_id TEXT NOT NULL,
                    bucket TEXT NOT NULL, stake INTEGER NOT NULL, result TEXT,
                    delta INTEGER, created_at TEXT NOT NULL, settled_at TEXT,
                    UNIQUE(player_id, round_id), FOREIGN KEY(player_id) REFERENCES game_players(id),
                    FOREIGN KEY(round_id) REFERENCES game_rounds(id)
                );
                CREATE INDEX IF NOT EXISTS idx_game_predictions_round ON game_predictions(round_id);
                CREATE INDEX IF NOT EXISTS idx_game_players_points ON game_players(points DESC);
            """)

    def player(self, player_id: str | None) -> dict[str, Any]:
        identifier = player_id or uuid.uuid4().hex
        with self.lock, sqlite3.connect(self.path) as db:
            row = db.execute("SELECT * FROM game_players WHERE id=?", (identifier,)).fetchone()
            if row is None:
                timestamp = now()
                db.execute("INSERT INTO game_players VALUES(?,?,?,?,?)", (identifier, 100, day(), timestamp, timestamp))
                db.commit()
                return {"id": identifier, "points": 100, "last_refill_day": day()}
            columns = ["id", "points", "last_refill_day", "created_at", "updated_at"]
            value = dict(zip(columns, row))
            if value["last_refill_day"] != day():
                points = max(100, int(value["points"]))
                db.execute("UPDATE game_players SET points=?, last_refill_day=?, updated_at=? WHERE id=?", (points, day(), now(), identifier))
                db.commit()
                value.update(points=points, last_refill_day=day())
            return value

    def add_round(self, round_data: dict[str, Any]) -> None:
        with self.lock, sqlite3.connect(self.path) as db:
            db.execute("INSERT OR IGNORE INTO game_rounds VALUES(?,?,?,?,?,?,?,?)", (
                round_data["mint"], round_data["mint"], round_data["creator"], round_data["completed_at"],
                round_data["completion_signature"], None, None, "ACTIVE",
            ))
            db.commit()

    def settle_jeet(self, mint: str, timestamp: int, signature: str) -> None:
        with self.lock, sqlite3.connect(self.path) as db:
            row = db.execute("SELECT id, completed_at FROM game_rounds WHERE mint=? AND status='ACTIVE'", (mint,)).fetchone()
            if row is None:
                return
            round_id, completed_at = row
            db.execute("UPDATE game_rounds SET status='JEET_CONFIRMED', jeet_at=?, jeet_signature=? WHERE id=?", (timestamp, signature, round_id))
            predictions = db.execute("SELECT id, player_id, bucket, stake FROM game_predictions WHERE round_id=? AND result IS NULL", (round_id,)).fetchall()
            elapsed = max(0, timestamp - completed_at)
            actual = self.bucket_for_seconds(elapsed)
            for prediction_id, player_id, bucket, stake in predictions:
                correct = bucket == actual
                delta = stake if correct else -stake
                result = "CORRECT" if correct else "WRONG"
                db.execute("UPDATE game_predictions SET result=?, delta=?, settled_at=? WHERE id=?", (result, delta, now(), prediction_id))
                db.execute("UPDATE game_players SET points=MAX(0, points + ?), updated_at=? WHERE id=?", (delta, now(), player_id))
            db.commit()

    @staticmethod
    def bucket_for_seconds(seconds: int) -> str:
        if seconds < 5 * 60: return "under-5m"
        if seconds < 30 * 60: return "5-30m"
        if seconds < 60 * 60: return "30-60m"
        if seconds < 6 * 3600: return "1-6h"
        if seconds < 24 * 3600: return "6-24h"
        if seconds < 3 * 86400: return "1-3d"
        return "3-7d"

    def predict(self, player_id: str, mint: str, bucket: str) -> dict[str, Any]:
        allowed = {"under-5m", "5-30m", "30-60m", "1-6h", "6-24h", "1-3d", "3-7d"}
        if bucket not in allowed:
            raise ValueError("invalid prediction bucket")
        player = self.player(player_id)
        with self.lock, sqlite3.connect(self.path) as db:
            round_row = db.execute("SELECT id, status FROM game_rounds WHERE mint=?", (mint,)).fetchone()
            if round_row is None or round_row[1] != "ACTIVE":
                raise ValueError("round is not active")
            if int(player["points"]) < 25:
                raise ValueError("not enough points")
            try:
                db.execute("INSERT INTO game_predictions VALUES(?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, player_id, round_row[0], bucket, 25, None, None, now(), None))
                db.execute("UPDATE game_players SET points=points-25, updated_at=? WHERE id=?", (now(), player_id))
                db.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError("prediction already locked for this round") from exc
        return self.player(player_id)

    def state(self, player_id: str) -> dict[str, Any]:
        player = self.player(player_id)
        with self.lock, sqlite3.connect(self.path) as db:
            active = db.execute("SELECT id,mint,creator,completed_at,completion_signature,status FROM game_rounds WHERE status='ACTIVE' ORDER BY completed_at DESC LIMIT 1").fetchone()
            prediction = None
            if active:
                prediction = db.execute("SELECT bucket,result,delta FROM game_predictions WHERE player_id=? AND round_id=?", (player_id, active[0])).fetchone()
            leaders = db.execute("SELECT id, points FROM game_players ORDER BY points DESC, created_at ASC LIMIT 20").fetchall()
            ended = db.execute("SELECT mint,jeet_at,jeet_signature FROM game_rounds WHERE status='JEET_CONFIRMED' AND jeet_signature IS NOT NULL ORDER BY jeet_at DESC LIMIT 1").fetchone()
        return {
            "player": {"points": player["points"], "daily_refill": 100},
            "active_round": None if not active else {"mint": active[1], "creator": active[2], "completed_at": active[3], "completion_signature": active[4], "status": active[5]},
            "prediction": None if not prediction else {"bucket": prediction[0], "result": prediction[1], "delta": prediction[2]},
            "last_ended_round": None if not ended else {"mint": ended[0], "jeet_at": ended[1], "jeet_signature": ended[2]},
            "leaderboard": [{"rank": index + 1, "player": f"PLAYER-{row[0][:6].upper()}", "points": row[1]} for index, row in enumerate(leaders)],
        }
