"""SQLite registry schema and read access (spec 04, PROM-01).

Writes (promote / rollback) live in champion.py; the gate only reads.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from reviewpulse import paths

REGISTRY_DB = "state/registry.db"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS champion (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  release_id TEXT, version_counter INTEGER NOT NULL,
  previous_release_id TEXT, promoted_at_utc TEXT, actor TEXT, gate_decision_sha256 TEXT
);
INSERT OR IGNORE INTO champion (id, version_counter) VALUES (1, 0);
CREATE TABLE IF NOT EXISTS promotion_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, time_utc TEXT, action TEXT,
  release_id TEXT, expected_version INTEGER, status TEXT, reason TEXT, actor TEXT
);
"""
CHAMPION_COLUMNS = (
    "release_id",
    "version_counter",
    "previous_release_id",
    "promoted_at_utc",
    "actor",
    "gate_decision_sha256",
)


def registry_path() -> Path:
    return paths.resolve(REGISTRY_DB)


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (and initialise if needed) the registry database."""
    path = db_path or registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)  # explicit transactions
    conn.executescript(SCHEMA_SQL)
    return conn


def read_champion(db_path: Path | None = None) -> dict[str, Any]:
    """Champion state as in schemas/champion-state.v1. Missing db = no champion yet."""
    path = db_path or registry_path()
    empty = {"schema_version": "1", **{c: None for c in CHAMPION_COLUMNS}, "version_counter": 0}
    if not path.exists():
        return empty
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        row = conn.execute(
            f"SELECT {', '.join(CHAMPION_COLUMNS)} FROM champion WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return empty
    finally:
        conn.close()
    if row is None:
        return empty
    return {"schema_version": "1", **dict(zip(CHAMPION_COLUMNS, row, strict=True))}
