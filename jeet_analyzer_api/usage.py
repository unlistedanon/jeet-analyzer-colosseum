"""Small first-party daily usage counters; no addresses, IPs or free text."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UsageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: Literal["visit", "example_opened", "scan_attempt", "result_viewed"]
    investigation_id: str | None = Field(default=None, max_length=100)


class UsageStore:
    def __init__(self, path: Path, secret: str):
        self.path = path
        self.secret = secret.encode()
        self.available = True
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path, timeout=1)) as db, db:
                db.execute("""CREATE TABLE IF NOT EXISTS usage_daily (
                    day TEXT NOT NULL, visitor TEXT NOT NULL, event TEXT NOT NULL,
                    PRIMARY KEY(day, visitor, event))""")
        except (sqlite3.Error, OSError):
            self.available = False

    def record(self, subject: str, event: str) -> bool:
        if not self.available:
            return False
        now = datetime.now(timezone.utc)
        day = now.date().isoformat()
        visitor = hmac.new(self.secret, f"usage:{day}:{subject}".encode(), hashlib.sha256).hexdigest()
        try:
            with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
                db.execute("DELETE FROM usage_daily WHERE day < ?", ((now.date() - timedelta(days=29)).isoformat(),))
                db.execute("INSERT OR IGNORE INTO usage_daily VALUES(?,?,?)", (day, visitor, event))
            return True
        except sqlite3.Error:
            # Optional counters must never strand a scan or feedback submission.
            return False

    def summary(self) -> dict | None:
        if not self.available:
            return None
        day = datetime.now(timezone.utc).date().isoformat()
        try:
            with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
                rows = dict(db.execute("SELECT event, COUNT(*) FROM usage_daily WHERE day=? GROUP BY event", (day,)))
        except sqlite3.Error:
            return None
        events = ("visit", "example_opened", "scan_attempt", "scan_admitted", "result_viewed", "feedback_submitted")
        return {"day": day, "counts": {event: rows.get(event, 0) for event in events}}
