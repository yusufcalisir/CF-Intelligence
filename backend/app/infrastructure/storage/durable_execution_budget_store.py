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
            conn.commit()

    def _execute_cleanup_in_conn(
        self,
        conn: sqlite3.Connection,
        now_dt: datetime.datetime,
        now_iso: str,
    ) -> None:
        """Sweeps stale uncommitted reservations to FAILED_CLOSED and prunes records beyond retention."""
        # 1. Sweep expired active reservations to FAILED_CLOSED to prevent quota leaks under unhandled crashes
        conn.execute(
            """
            UPDATE cross_bank_budget_reservations
            SET status = 'FAILED_CLOSED', resolved_at = ?, reason = 'Reservation TTL expired without resolution (failed closed)'
            WHERE status = 'RESERVED' AND expires_at < ?;
            """,
            (now_iso, now_iso),
        )

        # 2. Prune records older than bounded retention
        cutoff_dt = now_dt - datetime.timedelta(seconds=self.retention_seconds)
        cutoff_iso = cutoff_dt.isoformat()
        conn.execute(
            """
            DELETE FROM cross_bank_budget_reservations
            WHERE status IN ('COMMITTED', 'RELEASED', 'FAILED_CLOSED') AND resolved_at < ?;
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
    ) -> dict[str, Any] | None:
        """Returns budget configuration and current consumption statistics for a directional pair."""
        clean_c = consortium_id.lower().strip()
        clean_req = requesting_bank_id.lower().strip()
        clean_target = counterpart_bank_id.lower().strip()
        now_dt = datetime.datetime.now(UTC)
        now_iso = now_dt.isoformat()

        with self._lock, self._get_connection() as conn:
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
                return None

            max_exec = int(row["max_executions"])
            win_sec = int(row["window_seconds"]) if row["window_seconds"] is not None else None

            if win_sec and win_sec > 0:
                cutoff_dt = now_dt - datetime.timedelta(seconds=win_sec)
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
                    SELECT COUNT(*) as c FROM cross_bank_budget_reservations
                    WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?
                    AND status IN ('COMMITTED', 'FAILED_CLOSED') AND (resolved_at >= ? OR reserved_at >= ?);
                    """,
                    (clean_c, clean_req, clean_target, cutoff_iso, cutoff_iso),
                )
            else:
                cur_inf = conn.execute(
                    """
                    SELECT COUNT(*) as c FROM cross_bank_budget_reservations
                    WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?
                    AND status = 'RESERVED' AND expires_at > ?;
                    """,
                    (clean_c, clean_req, clean_target, now_iso),
                )
                cur_comm = conn.execute(
                    """
                    SELECT COUNT(*) as c FROM cross_bank_budget_reservations
                    WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?
                    AND status IN ('COMMITTED', 'FAILED_CLOSED');
                    """,
                    (clean_c, clean_req, clean_target),
                )

            in_flight = cur_inf.fetchone()["c"]
            consumed = cur_comm.fetchone()["c"]
            remaining = max(0, max_exec - (in_flight + consumed))

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
        now_dt = datetime.datetime.now(UTC)
        now_iso = now_dt.isoformat()
        expires_dt = now_dt + datetime.timedelta(seconds=ttl_seconds)
        expires_iso = expires_dt.isoformat()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            self._maybe_cleanup(conn, now_dt, now_iso)

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
                        cutoff_dt = now_dt - datetime.timedelta(seconds=win_sec)
                        cutoff_iso = cutoff_dt.isoformat()
                        cur_count = conn.execute(
                            """
                            SELECT COUNT(*) as c FROM cross_bank_budget_reservations
                            WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?
                            AND (
                                (status = 'RESERVED' AND expires_at > ? AND reserved_at >= ?)
                                OR
                                (status IN ('COMMITTED', 'FAILED_CLOSED') AND (resolved_at >= ? OR reserved_at >= ?))
                            );
                            """,
                            (clean_c, clean_req, t, now_iso, cutoff_iso, cutoff_iso, cutoff_iso),
                        )
                    else:
                        cur_count = conn.execute(
                            """
                            SELECT COUNT(*) as c FROM cross_bank_budget_reservations
                            WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?
                            AND (
                                (status = 'RESERVED' AND expires_at > ?)
                                OR
                                (status IN ('COMMITTED', 'FAILED_CLOSED'))
                            );
                            """,
                            (clean_c, clean_req, t, now_iso),
                        )

                    current_consumed = cur_count.fetchone()["c"]
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
        reason: str = "Execution completed",
    ) -> int:
        """Transitions reserved slots to COMMITTED, permanently charging the budget."""
        clean_bid = batch_id.strip()
        now_iso = datetime.datetime.now(UTC).isoformat()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            if completed_targets is not None:
                clean_targets = [t.lower().strip() for t in completed_targets]
                count = 0
                for target in clean_targets:
                    cur = conn.execute(
                        """
                        UPDATE cross_bank_budget_reservations
                        SET status = 'COMMITTED', resolved_at = ?, reason = ?
                        WHERE batch_id = ? AND counterpart_bank_id = ? AND status = 'RESERVED';
                        """,
                        (now_iso, reason, clean_bid, target),
                    )
                    count += cur.rowcount
            else:
                cur = conn.execute(
                    """
                    UPDATE cross_bank_budget_reservations
                    SET status = 'COMMITTED', resolved_at = ?, reason = ?
                    WHERE batch_id = ? AND status = 'RESERVED';
                    """,
                    (now_iso, reason, clean_bid),
                )
            count = cur.rowcount
            conn.commit()
            return count

    def release_reservations(
        self,
        batch_id: str,
        targets: list[str] | None = None,
        reason: str = "Pre-dispatch failure",
    ) -> int:
        """Releases reserved slots when execution demonstrably did not occur."""
        clean_bid = batch_id.strip()
        now_iso = datetime.datetime.now(UTC).isoformat()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            if targets is not None:
                clean_targets = [t.lower().strip() for t in targets]
                count = 0
                for target in clean_targets:
                    cur = conn.execute(
                        """
                        UPDATE cross_bank_budget_reservations
                        SET status = 'RELEASED', resolved_at = ?, reason = ?
                        WHERE batch_id = ? AND counterpart_bank_id = ? AND status = 'RESERVED';
                        """,
                        (now_iso, reason, clean_bid, target),
                    )
                    count += cur.rowcount
            else:
                cur = conn.execute(
                    """
                    UPDATE cross_bank_budget_reservations
                    SET status = 'RELEASED', resolved_at = ?, reason = ?
                    WHERE batch_id = ? AND status = 'RESERVED';
                    """,
                    (now_iso, reason, clean_bid),
                )
            count = cur.rowcount
            conn.commit()
            return count

    def fail_closed_reservations(
        self,
        batch_id: str,
        targets: list[str] | None = None,
        reason: str = "Indeterminate execution failure",
    ) -> int:
        """Transitions reserved slots to FAILED_CLOSED when execution was indeterminate.

        Crucially, this does NOT release the slot back into available quota,
        strictly preventing unsafe quota reuse under ambiguous crashes.
        """
        clean_bid = batch_id.strip()
        now_iso = datetime.datetime.now(UTC).isoformat()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            if targets is not None:
                clean_targets = [t.lower().strip() for t in targets]
                count = 0
                for target in clean_targets:
                    cur = conn.execute(
                        """
                        UPDATE cross_bank_budget_reservations
                        SET status = 'FAILED_CLOSED', resolved_at = ?, reason = ?
                        WHERE batch_id = ? AND counterpart_bank_id = ? AND status = 'RESERVED';
                        """,
                        (now_iso, reason, clean_bid, target),
                    )
                    count += cur.rowcount
            else:
                cur = conn.execute(
                    """
                    UPDATE cross_bank_budget_reservations
                    SET status = 'FAILED_CLOSED', resolved_at = ?, reason = ?
                    WHERE batch_id = ? AND status = 'RESERVED';
                    """,
                    (now_iso, reason, clean_bid),
                )
            count = cur.rowcount
            conn.commit()
            return count
