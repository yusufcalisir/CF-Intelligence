"""Durable SQLite transactional storage for Cross-Bank Execution Budgets and Quota Enforcement.

Adheres strictly to .agents/AGENTS.md, runtime truth, and M6.4 Task 3 requirements:
- Scoped to consortium_id, requesting_bank_id, and counterpart_bank_id.
- ACID-compliant transactional persistence using SQLite with WAL mode, busy timeouts, and BEGIN IMMEDIATE locks.
- Concurrency-safe atomic multi-target reservation before dispatch to prevent oversubscription.
- Fail-closed semantics: quota-exhausted target rejects the entire batch before dispatch.
- Zero process-local in-memory fallbacks or unverified check-then-write sequences.
- Bounded retention with explicit cleanup for expired records.
- Strict prohibition on persisting bank-private raw identifiers, worker private keys,
  matching tokens, credentials, or response payloads.
- Default behavior preserves unconstrained execution unless explicitly configured or required by governance.
"""

from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import UTC
from typing import Any

from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)

# Standard retention window for budget reservation audit records (7 days = 604,800 seconds)
DEFAULT_BUDGET_RETENTION_SECONDS = 604_800
DEFAULT_RESERVATION_TTL_SECONDS = 300.0

# Bounded, fixed reason categories (strictly prevents raw exception or secret interpolation)
REASON_EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
REASON_PILOT_PREFLIGHT_REJECTED = "PILOT_PREFLIGHT_REJECTED"
REASON_EXECUTION_INDETERMINATE = "EXECUTION_INDETERMINATE"
REASON_BATCH_ABORTED_BEFORE_DISPATCH = "BATCH_ABORTED_BEFORE_DISPATCH"
REASON_PRE_DISPATCH_RELEASED = "PRE_DISPATCH_RELEASED"
REASON_PRE_DISPATCH_DEADLINE_EXCEEDED = "PRE_DISPATCH_DEADLINE_EXCEEDED"
REASON_CIRCUIT_BREAKER_BLOCKED = "CIRCUIT_BREAKER_BLOCKED"
REASON_EXPIRED_RESERVATION_FAILED_CLOSED = "EXPIRED_RESERVATION_FAILED_CLOSED"

ALLOWED_REASONS = frozenset(
    {
        REASON_EXECUTION_COMPLETED,
        REASON_PILOT_PREFLIGHT_REJECTED,
        REASON_EXECUTION_INDETERMINATE,
        REASON_BATCH_ABORTED_BEFORE_DISPATCH,
        REASON_PRE_DISPATCH_RELEASED,
        REASON_PRE_DISPATCH_DEADLINE_EXCEEDED,
        REASON_CIRCUIT_BREAKER_BLOCKED,
        REASON_EXPIRED_RESERVATION_FAILED_CLOSED,
    }
)


def sanitize_budget_reason(reason: str | None, default: str = REASON_EXECUTION_INDETERMINATE) -> str:
    """Sanitizes reservation resolution reasons to strictly bounded static categories.

    Prevents interpolation of exceptions, response payloads, hostnames, secrets,
    or customer identifiers into persistent database storage.
    """
    if reason in ALLOWED_REASONS:
        return reason
    if reason:
        r_upper = reason.upper()
        if "PILOT" in r_upper or "PREFLIGHT" in r_upper:
            return REASON_PILOT_PREFLIGHT_REJECTED
        if "DEADLINE" in r_upper:
            return REASON_PRE_DISPATCH_DEADLINE_EXCEEDED
        if "CIRCUIT" in r_upper or "BREAKER" in r_upper:
            return REASON_CIRCUIT_BREAKER_BLOCKED
        if "ABORT" in r_upper or "PRE-DISPATCH" in r_upper or "UNDISPATCHED" in r_upper:
            return REASON_BATCH_ABORTED_BEFORE_DISPATCH
        if "RELEASE" in r_upper:
            return REASON_PRE_DISPATCH_RELEASED
        if "COMPLET" in r_upper or "SUCCESS" in r_upper:
            return REASON_EXECUTION_COMPLETED
        if "EXPIRE" in r_upper:
            return REASON_EXPIRED_RESERVATION_FAILED_CLOSED
    return default


class DurableCrossBankExecutionBudgetStore:
    """ACID-compliant SQLite store for cross-bank pairwise execution budgets and quota reservations."""

    _instance: DurableCrossBankExecutionBudgetStore | None = None
    _singleton_lock = threading.Lock()

    def __init__(
        self,
        db_path: str | None = None,
        retention_seconds: int = DEFAULT_BUDGET_RETENTION_SECONDS,
        cleanup_interval_seconds: float = 60.0,
    ) -> None:
        if db_path is None:
            env_path = os.environ.get("CFI_EXECUTION_BUDGET_DB_PATH")
            if env_path:
                db_path = env_path
            else:
                storage_dir = get_storage_dir()
                db_path = os.path.join(storage_dir, "cfi_cross_bank_execution_budgets.sqlite")
        self.db_path = db_path
        self.retention_seconds = retention_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._last_cleanup_monotonic: float = 0.0
        self._lock = threading.RLock()
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: str | None = None) -> DurableCrossBankExecutionBudgetStore:
        """Returns the singleton instance of the durable execution budget store."""
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls(db_path=db_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Resets the singleton instance (for testing isolation)."""
        with cls._singleton_lock:
            cls._instance = None

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cross_bank_execution_budgets (
                    consortium_id TEXT NOT NULL,
                    requesting_bank_id TEXT NOT NULL,
                    counterpart_bank_id TEXT NOT NULL,
                    max_executions INTEGER NOT NULL,
                    window_seconds INTEGER,
                    window_start TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (consortium_id, requesting_bank_id, counterpart_bank_id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cross_bank_budget_consumption (
                    consortium_id TEXT NOT NULL,
                    requesting_bank_id TEXT NOT NULL,
                    counterpart_bank_id TEXT NOT NULL,
                    total_consumed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (consortium_id, requesting_bank_id, counterpart_bank_id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cross_bank_budget_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    consortium_id TEXT NOT NULL,
                    requesting_bank_id TEXT NOT NULL,
                    counterpart_bank_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reserved_at TEXT NOT NULL,
                    resolved_at TEXT,
                    expires_at TEXT NOT NULL,
                    reason TEXT
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_budget_res_batch ON cross_bank_budget_reservations (batch_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_budget_res_pair_status ON cross_bank_budget_reservations (consortium_id, requesting_bank_id, counterpart_bank_id, status);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_budget_res_expires ON cross_bank_budget_reservations (expires_at);"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cross_bank_budget_window_executions (
                    execution_id TEXT PRIMARY KEY,
                    consortium_id TEXT NOT NULL,
                    requesting_bank_id TEXT NOT NULL,
                    counterpart_bank_id TEXT NOT NULL,
                    executed_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_budget_window_pair_time ON cross_bank_budget_window_executions (
                    consortium_id, requesting_bank_id, counterpart_bank_id, executed_at
                );
                """
            )
            # Backfill consumption ledger from existing historical reservations if upgrading
            conn.execute(
                """
                INSERT OR IGNORE INTO cross_bank_budget_consumption (
                    consortium_id, requesting_bank_id, counterpart_bank_id, total_consumed
                )
                SELECT consortium_id, requesting_bank_id, counterpart_bank_id, COUNT(*)
                FROM cross_bank_budget_reservations
                WHERE status IN ('COMMITTED', 'FAILED_CLOSED')
                GROUP BY consortium_id, requesting_bank_id, counterpart_bank_id;
                """
            )
            # Backfill window execution history from existing historical reservations if upgrading
            conn.execute(
                """
                INSERT OR IGNORE INTO cross_bank_budget_window_executions (
                    execution_id, consortium_id, requesting_bank_id, counterpart_bank_id, executed_at
                )
                SELECT reservation_id, consortium_id, requesting_bank_id, counterpart_bank_id, COALESCE(resolved_at, reserved_at)
                FROM cross_bank_budget_reservations
                WHERE status IN ('COMMITTED', 'FAILED_CLOSED');
                """
            )
            conn.commit()

    def _increment_consumption(
        self,
        conn: sqlite3.Connection,
        consortium_id: str,
        requesting_bank_id: str,
        counterpart_bank_id: str,
        count: int = 1,
    ) -> None:
        """Increments durable cumulative consumption for total budgets."""
        if count <= 0:
            return
        conn.execute(
            """
            INSERT INTO cross_bank_budget_consumption (
                consortium_id, requesting_bank_id, counterpart_bank_id, total_consumed
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(consortium_id, requesting_bank_id, counterpart_bank_id) DO UPDATE SET
                total_consumed = total_consumed + excluded.total_consumed;
            """,
            (consortium_id, requesting_bank_id, counterpart_bank_id, count),
        )

    def _record_window_execution(
        self,
        conn: sqlite3.Connection,
        reservation_id: str,
        consortium_id: str,
        requesting_bank_id: str,
        counterpart_bank_id: str,
        executed_at: str,
    ) -> None:
        """Records an authoritative execution event into the durable window execution history."""
        conn.execute(
            """
            INSERT OR REPLACE INTO cross_bank_budget_window_executions (
                execution_id, consortium_id, requesting_bank_id, counterpart_bank_id, executed_at
            ) VALUES (?, ?, ?, ?, ?);
            """,
            (reservation_id, consortium_id, requesting_bank_id, counterpart_bank_id, executed_at),
        )

    def _reconcile_expired_reservations(self, conn: sqlite3.Connection, now_iso: str) -> int:
        """Atomically sweeps expired active reservations to FAILED_CLOSED without throttling.

        Ensures that expired unresolved reservations cannot create a temporary oversubscription
        gap before periodic cleanup runs.
        """
        cur = conn.execute(
            """
            SELECT reservation_id, consortium_id, requesting_bank_id, counterpart_bank_id
            FROM cross_bank_budget_reservations
            WHERE status = 'RESERVED' AND expires_at <= ?;
            """,
            (now_iso,),
        )
        expired_rows = cur.fetchall()
        if not expired_rows:
            return 0

        transitioned_count = 0
        for r in expired_rows:
            cur_up = conn.execute(
                """
                UPDATE cross_bank_budget_reservations
                SET status = 'FAILED_CLOSED', resolved_at = ?, reason = ?
                WHERE reservation_id = ? AND status = 'RESERVED' AND expires_at <= ?;
                """,
                (now_iso, REASON_EXPIRED_RESERVATION_FAILED_CLOSED, r["reservation_id"], now_iso),
            )
            if cur_up.rowcount > 0:
                transitioned_count += cur_up.rowcount
                self._increment_consumption(
                    conn,
                    r["consortium_id"],
                    r["requesting_bank_id"],
                    r["counterpart_bank_id"],
                    1,
                )
                self._record_window_execution(
                    conn,
                    r["reservation_id"],
                    r["consortium_id"],
                    r["requesting_bank_id"],
                    r["counterpart_bank_id"],
                    now_iso,
                )
        return transitioned_count

    def _execute_cleanup_in_conn(
        self,
        conn: sqlite3.Connection,
        now_dt: datetime.datetime,
        now_iso: str,
    ) -> None:
        """Sweeps expired uncommitted reservations to FAILED_CLOSED and prunes records beyond retention."""
        # 1. Sweep expired active reservations to FAILED_CLOSED
        self._reconcile_expired_reservations(conn, now_iso)

        # 2. Prune records older than bounded retention (does not affect durable consumption ledger)
        cutoff_dt = now_dt - datetime.timedelta(seconds=self.retention_seconds)
        cutoff_iso = cutoff_dt.isoformat()
        conn.execute(
            """
            DELETE FROM cross_bank_budget_reservations
            WHERE status IN ('COMMITTED', 'RELEASED', 'FAILED_CLOSED') AND resolved_at < ?;
            """,
            (cutoff_iso,),
        )

        # 3. Prune window executions older than each configured budget's window
        cur_w = conn.execute(
            """
            SELECT consortium_id, requesting_bank_id, counterpart_bank_id, window_seconds
            FROM cross_bank_execution_budgets
            WHERE window_seconds IS NOT NULL AND window_seconds > 0;
            """
        )
        for w in cur_w.fetchall():
            w_cutoff = (now_dt - datetime.timedelta(seconds=int(w["window_seconds"]))).isoformat()
            conn.execute(
                """
                DELETE FROM cross_bank_budget_window_executions
                WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ? AND executed_at < ?;
                """,
                (w["consortium_id"], w["requesting_bank_id"], w["counterpart_bank_id"], w_cutoff),
            )
        # Prune window executions for pairs that have no active window budget older than default retention
        conn.execute(
            """
            DELETE FROM cross_bank_budget_window_executions
            WHERE executed_at < ? AND NOT EXISTS (
                SELECT 1 FROM cross_bank_execution_budgets b
                WHERE b.consortium_id = cross_bank_budget_window_executions.consortium_id
                  AND b.requesting_bank_id = cross_bank_budget_window_executions.requesting_bank_id
                  AND b.counterpart_bank_id = cross_bank_budget_window_executions.counterpart_bank_id
                  AND b.window_seconds IS NOT NULL AND b.window_seconds > 0
            );
            """,
            (cutoff_iso,),
        )

    def _maybe_cleanup(self, conn: sqlite3.Connection, now_dt: datetime.datetime, now_iso: str) -> None:
        """Opportunistically runs cleanup if interval has passed."""
        now_mono = time.monotonic()
        if (now_mono - self._last_cleanup_monotonic) >= self.cleanup_interval_seconds:
            self._execute_cleanup_in_conn(conn, now_dt, now_iso)
            self._last_cleanup_monotonic = now_mono

    def set_budget(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        counterpart_bank_id: str,
        max_executions: int,
        window_seconds: int | None = None,
    ) -> None:
        """Sets or updates the pairwise execution budget for a directional pair."""
        if max_executions <= 0:
            raise ValueError("max_executions must be greater than zero.")
        if window_seconds is not None and window_seconds <= 0:
            raise ValueError("window_seconds must be positive if specified.")

        clean_c = consortium_id.lower().strip()
        clean_req = requesting_bank_id.lower().strip()
        clean_target = counterpart_bank_id.lower().strip()
        now_iso = datetime.datetime.now(UTC).isoformat()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO cross_bank_execution_budgets (
                    consortium_id, requesting_bank_id, counterpart_bank_id,
                    max_executions, window_seconds, window_start, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(consortium_id, requesting_bank_id, counterpart_bank_id) DO UPDATE SET
                    max_executions = excluded.max_executions,
                    window_seconds = excluded.window_seconds,
                    window_start = excluded.window_start,
                    updated_at = excluded.updated_at;
                """,
                (clean_c, clean_req, clean_target, max_executions, window_seconds, now_iso, now_iso, now_iso),
            )
            conn.commit()

    def get_budget(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        counterpart_bank_id: str,
        now_dt: datetime.datetime | None = None,
    ) -> dict[str, Any] | None:
        """Returns budget configuration and current consumption statistics for a directional pair."""
        clean_c = consortium_id.lower().strip()
        clean_req = requesting_bank_id.lower().strip()
        clean_target = counterpart_bank_id.lower().strip()
        now_resolved = now_dt or datetime.datetime.now(UTC)
        now_iso = now_resolved.isoformat()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            # Reconcile expired reservations to get accurate current count
            self._reconcile_expired_reservations(conn, now_iso)

            cur = conn.execute(
                """
                SELECT max_executions, window_seconds, window_start, created_at, updated_at
                FROM cross_bank_execution_budgets
                WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
                """,
                (clean_c, clean_req, clean_target),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None

            max_exec = int(row["max_executions"])
            win_sec = int(row["window_seconds"]) if row["window_seconds"] is not None else None

            if win_sec and win_sec > 0:
                cutoff_dt = now_resolved - datetime.timedelta(seconds=win_sec)
                cutoff_iso = cutoff_dt.isoformat()
                cur_inf = conn.execute(
                    """
                    SELECT COUNT(*) as c FROM cross_bank_budget_reservations
                    WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?
                    AND status = 'RESERVED' AND expires_at > ? AND reserved_at >= ?;
                    """,
                    (clean_c, clean_req, clean_target, now_iso, cutoff_iso),
                )
                cur_comm = conn.execute(
                    """
                    SELECT COUNT(*) as c FROM cross_bank_budget_window_executions
                    WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?
                    AND executed_at >= ?;
                    """,
                    (clean_c, clean_req, clean_target, cutoff_iso),
                )
                in_flight = cur_inf.fetchone()["c"]
                consumed = cur_comm.fetchone()["c"]
            else:
                cur_inf = conn.execute(
                    """
                    SELECT COUNT(*) as c FROM cross_bank_budget_reservations
                    WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?
                    AND status = 'RESERVED' AND expires_at > ?;
                    """,
                    (clean_c, clean_req, clean_target, now_iso),
                )
                cur_consumed = conn.execute(
                    """
                    SELECT total_consumed FROM cross_bank_budget_consumption
                    WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
                    """,
                    (clean_c, clean_req, clean_target),
                )
                in_flight = cur_inf.fetchone()["c"]
                r_cons = cur_consumed.fetchone()
                consumed = r_cons["total_consumed"] if r_cons else 0

            remaining = max(0, max_exec - (in_flight + consumed))
            conn.commit()

            return {
                "consortium_id": clean_c,
                "requesting_bank_id": clean_req,
                "counterpart_bank_id": clean_target,
                "max_executions": max_exec,
                "window_seconds": win_sec,
                "window_start": row["window_start"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "in_flight_reservations": in_flight,
                "consumed_executions": consumed,
                "remaining_executions": remaining,
            }

    def delete_budget(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        counterpart_bank_id: str,
    ) -> bool:
        """Deletes the configured budget for a pair, returning it to unconstrained."""
        clean_c = consortium_id.lower().strip()
        clean_req = requesting_bank_id.lower().strip()
        clean_target = counterpart_bank_id.lower().strip()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                DELETE FROM cross_bank_budget_consumption
                WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
                """,
                (clean_c, clean_req, clean_target),
            )
            conn.execute(
                """
                DELETE FROM cross_bank_budget_window_executions
                WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
                """,
                (clean_c, clean_req, clean_target),
            )
            cur = conn.execute(
                """
                DELETE FROM cross_bank_execution_budgets
                WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
                """,
                (clean_c, clean_req, clean_target),
            )
            count = cur.rowcount
            conn.commit()
            return count > 0

    def reset_budget_consumption(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        counterpart_bank_id: str,
    ) -> None:
        """Resets consumption for testing or administrative realignment."""
        clean_c = consortium_id.lower().strip()
        clean_req = requesting_bank_id.lower().strip()
        clean_target = counterpart_bank_id.lower().strip()
        now_iso = datetime.datetime.now(UTC).isoformat()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                DELETE FROM cross_bank_budget_reservations
                WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
                """,
                (clean_c, clean_req, clean_target),
            )
            conn.execute(
                """
                UPDATE cross_bank_budget_consumption
                SET total_consumed = 0
                WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
                """,
                (clean_c, clean_req, clean_target),
            )
            conn.execute(
                """
                DELETE FROM cross_bank_budget_window_executions
                WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
                """,
                (clean_c, clean_req, clean_target),
            )
            conn.execute(
                """
                UPDATE cross_bank_execution_budgets
                SET window_start = ?, updated_at = ?
                WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
                """,
                (now_iso, now_iso, clean_c, clean_req, clean_target),
            )
            conn.commit()

    def reserve_batch(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        target_bank_ids: list[str],
        batch_id: str | None = None,
        ttl_seconds: float = DEFAULT_RESERVATION_TTL_SECONDS,
        grant_budgets: dict[str, int] | None = None,
        now_dt: datetime.datetime | None = None,
    ) -> tuple[bool, str, list[str]]:
        """Atomically checks and reserves budget slots for all targets in a multi-counterparty batch.

        Enforces:
        - All-or-nothing atomicity under SQLite BEGIN IMMEDIATE lock.
        - If any target's configured budget is exhausted, rolls back and rejects the entire batch.
        - Bounded TTL on reservations to prevent permanent locking under unexpected failures.
        - Preserves unconstrained execution for targets without configured budgets.

        Returns:
            (success: bool, batch_id: str, exhausted_targets: list[str])
        """
        clean_c = consortium_id.lower().strip()
        clean_req = requesting_bank_id.lower().strip()
        clean_targets = [t.lower().strip() for t in target_bank_ids]
        bid = batch_id or str(uuid.uuid4())
        now_resolved = now_dt or datetime.datetime.now(UTC)
        now_iso = now_resolved.isoformat()
        expires_dt = now_resolved + datetime.timedelta(seconds=ttl_seconds)
        expires_iso = expires_dt.isoformat()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            # Atomically reconcile any expired reservations before quota checks
            self._reconcile_expired_reservations(conn, now_iso)
            self._maybe_cleanup(conn, now_resolved, now_iso)

            exhausted_targets: list[str] = []

            for t in clean_targets:
                cur = conn.execute(
                    """
                    SELECT max_executions, window_seconds, window_start
                    FROM cross_bank_execution_budgets
                    WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
                    """,
                    (clean_c, clean_req, t),
                )
                row = cur.fetchone()
                max_exec: int | None = None
                win_sec: int | None = None

                if row is not None:
                    max_exec = int(row["max_executions"])
                    win_sec = int(row["window_seconds"]) if row["window_seconds"] is not None else None
                elif grant_budgets and t in grant_budgets:
                    max_exec = grant_budgets[t]

                if max_exec is not None:
                    if win_sec and win_sec > 0:
                        cutoff_dt = now_resolved - datetime.timedelta(seconds=win_sec)
                        cutoff_iso = cutoff_dt.isoformat()
                        cur_inflight = conn.execute(
                            """
                            SELECT COUNT(*) as c FROM cross_bank_budget_reservations
                            WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?
                            AND status = 'RESERVED' AND expires_at > ? AND reserved_at >= ?;
                            """,
                            (clean_c, clean_req, t, now_iso, cutoff_iso),
                        )
                        in_flight = cur_inflight.fetchone()["c"]
                        cur_win = conn.execute(
                            """
                            SELECT COUNT(*) as c FROM cross_bank_budget_window_executions
                            WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?
                            AND executed_at >= ?;
                            """,
                            (clean_c, clean_req, t, cutoff_iso),
                        )
                        win_consumed = cur_win.fetchone()["c"]
                        current_consumed = in_flight + win_consumed
                    else:
                        cur_inflight = conn.execute(
                            """
                            SELECT COUNT(*) as c FROM cross_bank_budget_reservations
                            WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?
                            AND status = 'RESERVED' AND expires_at > ?;
                            """,
                            (clean_c, clean_req, t, now_iso),
                        )
                        in_flight = cur_inflight.fetchone()["c"]
                        cur_consumed = conn.execute(
                            """
                            SELECT total_consumed FROM cross_bank_budget_consumption
                            WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
                            """,
                            (clean_c, clean_req, t),
                        )
                        r_cons = cur_consumed.fetchone()
                        consumed = r_cons["total_consumed"] if r_cons else 0
                        current_consumed = in_flight + consumed

                    if current_consumed + 1 > max_exec:
                        exhausted_targets.append(t)

            if exhausted_targets:
                conn.rollback()
                return False, bid, exhausted_targets

            # All targets have sufficient budget or are unconstrained: atomically insert reservations
            for t in clean_targets:
                res_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO cross_bank_budget_reservations (
                        reservation_id, batch_id, consortium_id, requesting_bank_id,
                        counterpart_bank_id, status, reserved_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, 'RESERVED', ?, ?);
                    """,
                    (res_id, bid, clean_c, clean_req, t, now_iso, expires_iso),
                )

            conn.commit()
            return True, bid, []

    def commit_reservations(
        self,
        batch_id: str,
        completed_targets: list[str] | None = None,
        reason: str = REASON_EXECUTION_COMPLETED,
    ) -> int:
        """Transitions reserved slots to COMMITTED, permanently charging the budget."""
        clean_bid = batch_id.strip()
        now_iso = datetime.datetime.now(UTC).isoformat()
        safe_reason = sanitize_budget_reason(reason, default=REASON_EXECUTION_COMPLETED)

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            count = 0
            if completed_targets is not None:
                clean_targets = [t.lower().strip() for t in completed_targets]
                for target in clean_targets:
                    cur_info = conn.execute(
                        """
                        SELECT reservation_id, consortium_id, requesting_bank_id
                        FROM cross_bank_budget_reservations
                        WHERE batch_id = ? AND counterpart_bank_id = ? AND status = 'RESERVED';
                        """,
                        (clean_bid, target),
                    ).fetchall()
                    for r in cur_info:
                        cur = conn.execute(
                            """
                            UPDATE cross_bank_budget_reservations
                            SET status = 'COMMITTED', resolved_at = ?, reason = ?
                            WHERE reservation_id = ? AND status = 'RESERVED';
                            """,
                            (now_iso, safe_reason, r["reservation_id"]),
                        )
                        if cur.rowcount > 0:
                            count += cur.rowcount
                            self._increment_consumption(
                                conn,
                                r["consortium_id"],
                                r["requesting_bank_id"],
                                target,
                                1,
                            )
                            self._record_window_execution(
                                conn,
                                r["reservation_id"],
                                r["consortium_id"],
                                r["requesting_bank_id"],
                                target,
                                now_iso,
                            )
            else:
                cur_info = conn.execute(
                    """
                    SELECT reservation_id, consortium_id, requesting_bank_id, counterpart_bank_id
                    FROM cross_bank_budget_reservations
                    WHERE batch_id = ? AND status = 'RESERVED';
                    """,
                    (clean_bid,),
                ).fetchall()
                for r in cur_info:
                    cur = conn.execute(
                        """
                        UPDATE cross_bank_budget_reservations
                        SET status = 'COMMITTED', resolved_at = ?, reason = ?
                        WHERE reservation_id = ? AND status = 'RESERVED';
                        """,
                        (now_iso, safe_reason, r["reservation_id"]),
                    )
                    if cur.rowcount > 0:
                        count += cur.rowcount
                        self._increment_consumption(
                            conn,
                            r["consortium_id"],
                            r["requesting_bank_id"],
                            r["counterpart_bank_id"],
                            1,
                        )
                        self._record_window_execution(
                            conn,
                            r["reservation_id"],
                            r["consortium_id"],
                            r["requesting_bank_id"],
                            r["counterpart_bank_id"],
                            now_iso,
                        )
            conn.commit()
            return count

    def release_reservations(
        self,
        batch_id: str,
        targets: list[str] | None = None,
        reason: str = REASON_PRE_DISPATCH_RELEASED,
    ) -> int:
        """Releases reserved slots when execution demonstrably did not occur."""
        clean_bid = batch_id.strip()
        now_iso = datetime.datetime.now(UTC).isoformat()
        safe_reason = sanitize_budget_reason(reason, default=REASON_PRE_DISPATCH_RELEASED)

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            count = 0
            if targets is not None:
                clean_targets = [t.lower().strip() for t in targets]
                for target in clean_targets:
                    cur = conn.execute(
                        """
                        UPDATE cross_bank_budget_reservations
                        SET status = 'RELEASED', resolved_at = ?, reason = ?
                        WHERE batch_id = ? AND counterpart_bank_id = ? AND status = 'RESERVED';
                        """,
                        (now_iso, safe_reason, clean_bid, target),
                    )
                    count += cur.rowcount
            else:
                cur = conn.execute(
                    """
                    UPDATE cross_bank_budget_reservations
                    SET status = 'RELEASED', resolved_at = ?, reason = ?
                    WHERE batch_id = ? AND status = 'RESERVED';
                    """,
                    (now_iso, safe_reason, clean_bid),
                )
                count = cur.rowcount
            conn.commit()
            return count

    def fail_closed_reservations(
        self,
        batch_id: str,
        targets: list[str] | None = None,
        reason: str = REASON_EXECUTION_INDETERMINATE,
    ) -> int:
        """Transitions reserved slots to FAILED_CLOSED when execution was indeterminate.

        Crucially, this does NOT release the slot back into available quota,
        strictly preventing unsafe quota reuse under ambiguous crashes.
        """
        clean_bid = batch_id.strip()
        now_iso = datetime.datetime.now(UTC).isoformat()
        safe_reason = sanitize_budget_reason(reason, default=REASON_EXECUTION_INDETERMINATE)

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            count = 0
            if targets is not None:
                clean_targets = [t.lower().strip() for t in targets]
                for target in clean_targets:
                    cur_info = conn.execute(
                        """
                        SELECT reservation_id, consortium_id, requesting_bank_id
                        FROM cross_bank_budget_reservations
                        WHERE batch_id = ? AND counterpart_bank_id = ? AND status = 'RESERVED';
                        """,
                        (clean_bid, target),
                    ).fetchall()
                    for r in cur_info:
                        cur = conn.execute(
                            """
                            UPDATE cross_bank_budget_reservations
                            SET status = 'FAILED_CLOSED', resolved_at = ?, reason = ?
                            WHERE reservation_id = ? AND status = 'RESERVED';
                            """,
                            (now_iso, safe_reason, r["reservation_id"]),
                        )
                        if cur.rowcount > 0:
                            count += cur.rowcount
                            self._increment_consumption(
                                conn,
                                r["consortium_id"],
                                r["requesting_bank_id"],
                                target,
                                1,
                            )
                            self._record_window_execution(
                                conn,
                                r["reservation_id"],
                                r["consortium_id"],
                                r["requesting_bank_id"],
                                target,
                                now_iso,
                            )
            else:
                cur_info = conn.execute(
                    """
                    SELECT reservation_id, consortium_id, requesting_bank_id, counterpart_bank_id
                    FROM cross_bank_budget_reservations
                    WHERE batch_id = ? AND status = 'RESERVED';
                    """,
                    (clean_bid,),
                ).fetchall()
                for r in cur_info:
                    cur = conn.execute(
                        """
                        UPDATE cross_bank_budget_reservations
                        SET status = 'FAILED_CLOSED', resolved_at = ?, reason = ?
                        WHERE reservation_id = ? AND status = 'RESERVED';
                        """,
                        (now_iso, safe_reason, r["reservation_id"]),
                    )
                    if cur.rowcount > 0:
                        count += cur.rowcount
                        self._increment_consumption(
                            conn,
                            r["consortium_id"],
                            r["requesting_bank_id"],
                            r["counterpart_bank_id"],
                            1,
                        )
                        self._record_window_execution(
                            conn,
                            r["reservation_id"],
                            r["consortium_id"],
                            r["requesting_bank_id"],
                            r["counterpart_bank_id"],
                            now_iso,
                        )
            conn.commit()
            return count
