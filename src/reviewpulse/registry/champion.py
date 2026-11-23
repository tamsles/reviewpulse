"""Champion pointer with compare-and-swap on version_counter (spec 04, PROM-01..03).

- promote / rollback are dry-run unless execute=True (C-10); a dry run writes nothing.
- The pointer only moves inside one IMMEDIATE transaction:
  UPDATE ... WHERE id = 1 AND version_counter = expected. 0 rows -> CONFLICT (C-06).
- The version counter only ever grows, so after A -> B -> A a caller holding an old
  version number is still rejected even though release_id is A again.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from reviewpulse.io_utils import utc_now_iso
from reviewpulse.registry.gate import gate
from reviewpulse.registry.state import connect, connect_ro, read_champion
from reviewpulse.release.loader import BundleInvalid, load_release
from reviewpulse.release.manifest import check_release_id

DEFAULT_ACTOR = "local-cli"


class PromotionError(RuntimeError):
    """Raised when a promotion cannot even be attempted (e.g. thresholds unset)."""


@dataclass
class PromotionResult:
    action: str  # PROMOTE | ROLLBACK
    status: str  # DRY_RUN | REJECTED | CONFLICT | PROMOTED | ROLLED_BACK
    release_id: str
    expected_version: int
    version_counter: int  # champion version after this call
    reason: str | None = None
    detail: str = ""
    gate: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _log(conn: sqlite3.Connection, action, release_id, expected, status, reason, actor, sha):
    conn.execute(
        "INSERT INTO promotion_log (time_utc, action, release_id, expected_version, status,"
        " reason, actor, gate_decision_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (utc_now_iso(), action, release_id, expected, status, reason, actor, sha),
    )


def _cas(
    action: str,
    release_id: str,
    expected_version: int,
    actor: str,
    gate_sha: str,
    success_status: str,
) -> PromotionResult:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE champion SET previous_release_id = release_id, release_id = ?,"
            " version_counter = version_counter + 1, promoted_at_utc = ?, actor = ?,"
            " gate_decision_sha256 = ? WHERE id = 1 AND version_counter = ?",
            (release_id, utc_now_iso(), actor, gate_sha, expected_version),
        )
        if cur.rowcount == 0:
            status, reason = "CONFLICT", "version_counter changed"
        else:
            status, reason = success_status, None
        _log(conn, action, release_id, expected_version, status, reason, actor, gate_sha)
        conn.execute("COMMIT")
        version = conn.execute("SELECT version_counter FROM champion WHERE id = 1").fetchone()[0]
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return PromotionResult(action, status, release_id, expected_version, version, reason)


def _log_only(action, release_id, expected, status, reason, actor, sha=None) -> None:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _log(conn, action, release_id, expected, status, reason, actor, sha)
        conn.execute("COMMIT")
    finally:
        conn.close()


def promote(
    release_id: str, expected_version: int, execute: bool = False, actor: str = DEFAULT_ACTOR
) -> PromotionResult:
    check_release_id(release_id)
    decision = gate(release_id)
    current = read_champion()["version_counter"]
    gate_info = decision.to_dict()
    if not decision.passed:
        if execute:
            _log_only("PROMOTE", release_id, expected_version, "REJECTED", decision.reason, actor)
        return PromotionResult(
            "PROMOTE", "REJECTED", release_id, expected_version, current,
            decision.reason, decision.detail, gate_info,
        )  # fmt: skip
    if not execute:
        return PromotionResult(
            "PROMOTE", "DRY_RUN", release_id, expected_version, current,
            None, "gate passed; re-run with --execute to move the pointer", gate_info,
        )  # fmt: skip
    if not decision.thresholds_configured:
        raise PromotionError("quality.yaml thresholds are not all configured; refusing --execute")
    if decision.inputs.get("champion_version") != expected_version:
        # the gate compared against a different champion than the caller expects
        reason = "gate ran against a different champion version"
        _log_only("PROMOTE", release_id, expected_version, "CONFLICT", reason, actor)
        return PromotionResult(
            "PROMOTE", "CONFLICT", release_id, expected_version, current, reason, reason, gate_info
        )
    result = _cas(
        "PROMOTE", release_id, expected_version, actor, decision.decision_sha256, "PROMOTED"
    )
    result.gate = gate_info
    return result


def _promoted_gate_sha(release_id: str) -> str | None:
    conn = connect_ro()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT gate_decision_sha256 FROM promotion_log WHERE release_id = ?"
            " AND action = 'PROMOTE' AND status = 'PROMOTED' ORDER BY id DESC LIMIT 1",
            (release_id,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def rollback(
    release_id: str, expected_version: int, execute: bool = False, actor: str = DEFAULT_ACTOR
) -> PromotionResult:
    """Point back to a release that was promoted before. The gate is not re-run."""
    check_release_id(release_id)
    current = read_champion()["version_counter"]
    gate_sha = _promoted_gate_sha(release_id)
    reason = detail = None
    if gate_sha is None:
        reason, detail = "NEVER_PROMOTED", "target was never PROMOTED"
    else:
        try:
            load_release(release_id)
        except BundleInvalid as exc:
            reason, detail = "BUNDLE_INVALID", str(exc)
    if reason is not None:
        if execute:
            _log_only("ROLLBACK", release_id, expected_version, "REJECTED", reason, actor)
        return PromotionResult(
            "ROLLBACK", "REJECTED", release_id, expected_version, current, reason, detail or ""
        )
    if not execute:
        return PromotionResult(
            "ROLLBACK", "DRY_RUN", release_id, expected_version, current,
            None, "target is rollback-eligible; re-run with --execute",
        )  # fmt: skip
    return _cas("ROLLBACK", release_id, expected_version, actor, gate_sha, "ROLLED_BACK")


def show() -> dict[str, Any]:
    return read_champion()


def history(limit: int = 20) -> list[dict[str, Any]]:
    conn = connect_ro()
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT id, time_utc, action, release_id, expected_version, status, reason, actor"
            " FROM promotion_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    finally:
        conn.close()
