"""Request events in SQLite (spec 05, MON-01).

One event per /v1/recommend request, including 4xx/5xx. No raw user_id and no
request body is ever stored (C-09): only the HMAC user key. A failed write never
affects the response; it is counted in-process and logged as a warning.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reviewpulse import paths

log = logging.getLogger("reviewpulse.events")

EVENTS_DB = "state/events.db"
COLUMNS = (
    "schema_version",
    "event_id",
    "request_id",
    "time_utc",
    "loaded_release_id",
    "source",
    "user_key_hash",
    "top_k",
    "candidate_count",
    "returned_item_count",
    "fallback_used",
    "fusion_used",
    "latency_ms",
    "http_status",
    "error_code",
)
BOOL_COLUMNS = ("fallback_used", "fusion_used")
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
  schema_version TEXT NOT NULL, event_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
  time_utc TEXT NOT NULL, loaded_release_id TEXT, source TEXT NOT NULL,
  user_key_hash TEXT, top_k INTEGER, candidate_count INTEGER, returned_item_count INTEGER,
  fallback_used INTEGER, fusion_used INTEGER, latency_ms REAL NOT NULL,
  http_status INTEGER NOT NULL, error_code TEXT
);
CREATE INDEX IF NOT EXISTS events_time ON events (time_utc);
"""


def _now_ms() -> str:
    """UTC time with millisecond precision, e.g. 2026-11-23T07:28:51.123Z."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class EventStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.failures = 0
        self._lock = threading.Lock()
        self._initialised = False

    @classmethod
    def default(cls) -> EventStore:
        return cls(paths.resolve(EVENTS_DB))

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        if not self._initialised:
            conn.executescript(SCHEMA_SQL)
            self._initialised = True
        return conn

    def write(self, fields: dict[str, Any]) -> bool:
        event = {
            "schema_version": "1",
            "event_id": str(uuid.uuid4()),
            "time_utc": _now_ms(),
            **fields,
        }
        row = [event.get(c) for c in COLUMNS]
        try:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        f"INSERT INTO events ({', '.join(COLUMNS)})"
                        f" VALUES ({', '.join('?' * len(COLUMNS))})",
                        row,
                    )
            finally:
                conn.close()
            return True
        except Exception as exc:  # never let monitoring break serving
            with self._lock:
                self.failures += 1
            log.warning("event write failed (%d so far): %s", self.failures, type(exc).__name__)
            return False


def read_events(path: Path | None = None, since: str | None = None) -> list[dict[str, Any]]:
    path = path or paths.resolve(EVENTS_DB)
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        sql = f"SELECT {', '.join(COLUMNS)} FROM events"
        args: list[Any] = []
        if since:
            sql += " WHERE time_utc >= ?"
            args.append(since)
        rows = conn.execute(sql + " ORDER BY rowid", args).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        ev = dict(zip(COLUMNS, r, strict=True))
        for c in BOOL_COLUMNS:
            if ev[c] is not None:
                ev[c] = bool(ev[c])
        out.append(ev)
    return out
