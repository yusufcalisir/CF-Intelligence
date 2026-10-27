"""Durable SQLite transactional storage for Cross-Bank Intelligence query idempotency.

Adheres strictly to .agents/AGENTS.md, runtime truth, and M6.4 Task 2 requirements:
- ACID-compliant transactional persistence using SQLite with WAL mode and busy timeouts.
- Concurrency-safe atomic key acquisition to prevent simultaneous duplicate executions.
- Zero process-local dictionary fallbacks or unverified check-then-write sequences.
- Bounded retention (24-hour default TTL) with explicit cleanup for expired records.
- Strict prohibition on persisting bank-private raw identifiers, worker private keys,
  matching tokens, or credentials.
- Distinguishes IN_PROGRESS, COMPLETED, and indeterminate interrupted (FAILED_AMBIGUOUS) states.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import UTC
from typing import Any

from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)

# Standard 24-hour retention window for request idempotency (86,400 seconds)
DEFAULT_IDEMPOTENCY_RETENTION_SECONDS = 86_400


class DurableCrossBankIdempotencyStore:
    """ACID-compliant SQLite store for cross-bank intelligence query idempotency."""

    _instance: DurableCrossBankIdempotencyStore | None = None
    _singleton_lock = threading.Lock()

    def __init__(
        self,
        db_path: str | None = None,
        retention_seconds: int = DEFAULT_IDEMPOTENCY_RETENTION_SECONDS,
        cleanup_interval_seconds: float = 0.0,
    ) -> None:
        if db_path is None:
            env_path = os.environ.get("CFI_IDEMPOTENCY_DB_PATH")
            if env_path:
                db_path = env_path
            else:
                storage_dir = get_storage_dir()
                db_path = os.path.join(storage_dir, "cfi_cross_bank_idempotency.sqlite")
        self.db_path = db_path
        self.retention_seconds = retention_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._last_cleanup_monotonic: float = 0.0
        self._lock = threading.RLock()
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: str | None = None) -> DurableCrossBankIdempotencyStore:
        """Returns the singleton instance of the durable idempotency store."""
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
                CREATE TABLE IF NOT EXISTS cross_bank_idempotency_records (
                    lookup_key TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL,
                    requesting_bank_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    consortium_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    execution_owner_id TEXT NOT NULL,
                    response_json TEXT,
                    governance_snapshot_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_idem_lookup ON cross_bank_idempotency_records (idempotency_key, requesting_bank_id, consortium_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_idem_expires ON cross_bank_idempotency_records (expires_at);"
            )
            conn.commit()

    @staticmethod
    def _build_lookup_key(consortium_id: str, requesting_bank_id: str, idempotency_key: str) -> str:
        """Builds an institutional and consortium scoped primary key."""
        return f"{consortium_id.lower().strip()}:{requesting_bank_id.lower().strip()}:{idempotency_key.strip()}"

    def _execute_cleanup_in_conn(
        self,
        conn: sqlite3.Connection,
        now_dt: datetime.datetime,
        now_iso: str,
        in_progress_grace_seconds: float = 300.0,
    ) -> int:
        """Atomically deletes expired records while strictly preserving active IN_PROGRESS reservations.

        Guarantees:
        - Only records with expires_at < now_iso are eligible for deletion.
        - Active IN_PROGRESS reservations (updated_at within grace period) are never purged.
        - Preserves fail-closed behavior for indeterminate executions (FAILED_AMBIGUOUS) within retention.
        """
        stale_in_progress_dt = now_dt - datetime.timedelta(seconds=in_progress_grace_seconds)
        stale_in_progress_iso = stale_in_progress_dt.isoformat()
        cur = conn.execute(
            """
            DELETE FROM cross_bank_idempotency_records
            WHERE expires_at < ?
              AND (status != 'IN_PROGRESS' OR updated_at < ?);
            """,
            (now_iso, stale_in_progress_iso),
        )
        return cur.rowcount

    def _maybe_cleanup_expired(
        self,
        conn: sqlite3.Connection,
        now_dt: datetime.datetime,
        now_iso: str,
    ) -> int:
        """Opportunistic rate-limited cleanup executed during normal store transactions."""
        now_mono = time.monotonic()
        if (
            self.cleanup_interval_seconds > 0.0
            and (now_mono - self._last_cleanup_monotonic) < self.cleanup_interval_seconds
        ):
            return 0
        self._last_cleanup_monotonic = now_mono
        return self._execute_cleanup_in_conn(conn, now_dt, now_iso)

    def acquire(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        principal_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        governance_snapshot: dict[str, Any],
        owner_id: str,
        ttl_seconds: int | None = None,
        in_progress_timeout: float = 60.0,
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        """Atomically acquires an idempotency reservation or returns existing state.

        Returns:
            ("ACQUIRED", None, None): New lock acquired. Caller must proceed to execution.
            ("COMPLETED", response_data, governance_snapshot): Identical query previously completed.
            ("IN_PROGRESS", None, None): Another concurrent execution is currently in flight.
            ("FINGERPRINT_MISMATCH", None, None): Key reused with a different request fingerprint.
            ("PRINCIPAL_MISMATCH", None, None): Key reused by another principal.
            ("INSTITUTION_MISMATCH", None, None): Key reused by another institution.
            ("CONSORTIUM_MISMATCH", None, None): Key reused across consortium boundaries.
            ("AMBIGUOUS_EXECUTION", None, None): Previous execution was interrupted or ambiguous.
        """
        clean_c_id = consortium_id.lower().strip()
        clean_bank = requesting_bank_id.lower().strip()
        clean_key = idempotency_key.strip()
        lookup_key = self._build_lookup_key(clean_c_id, clean_bank, clean_key)

        effective_ttl = ttl_seconds or self.retention_seconds
        now_dt = datetime.datetime.now(UTC)
        now_iso = now_dt.isoformat()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            # Opportunistic rate-limited operational cleanup of expired records
            self._maybe_cleanup_expired(conn, now_dt, now_iso)

            cur = conn.execute(
                "SELECT * FROM cross_bank_idempotency_records WHERE lookup_key = ?;",
                (lookup_key,),
            )
            row = cur.fetchone()

            if row is None:
                expires_at_iso = (now_dt + datetime.timedelta(seconds=effective_ttl)).isoformat()
                conn.execute(
                    """
                    INSERT INTO cross_bank_idempotency_records (
                        lookup_key, idempotency_key, requesting_bank_id, principal_id,
                        consortium_id, request_fingerprint, status, execution_owner_id,
                        governance_snapshot_json, created_at, updated_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'IN_PROGRESS', ?, ?, ?, ?, ?);
                    """,
                    (
                        lookup_key,
                        clean_key,
                        clean_bank,
                        principal_id.strip(),
                        clean_c_id,
                        request_fingerprint,
                        owner_id,
                        json.dumps(governance_snapshot, sort_keys=True, default=str),
                        now_iso,
                        now_iso,
                        expires_at_iso,
                    ),
                )
                conn.commit()
                return ("ACQUIRED", None, None)

            # Record exists. Check if expired:
            try:
                exp_dt = datetime.datetime.fromisoformat(row["expires_at"])
                is_expired = exp_dt <= now_dt
            except Exception:
                is_expired = False

            if is_expired:
                # Re-claim expired reservation with fresh lease
                expires_at_iso = (now_dt + datetime.timedelta(seconds=effective_ttl)).isoformat()
                conn.execute(
                    """
                    UPDATE cross_bank_idempotency_records
                    SET principal_id = ?,
                        request_fingerprint = ?,
                        status = 'IN_PROGRESS',
                        execution_owner_id = ?,
                        response_json = NULL,
                        governance_snapshot_json = ?,
                        created_at = ?,
                        updated_at = ?,
                        expires_at = ?
                    WHERE lookup_key = ?;
                    """,
                    (
                        principal_id.strip(),
                        request_fingerprint,
                        owner_id,
                        json.dumps(governance_snapshot, sort_keys=True, default=str),
                        now_iso,
                        now_iso,
                        expires_at_iso,
                        lookup_key,
                    ),
                )
                conn.commit()
                return ("ACQUIRED", None, None)

            # Active record exists. Validate boundaries:
            if row["requesting_bank_id"].lower() != clean_bank:
                conn.rollback()
                return ("INSTITUTION_MISMATCH", None, None)

            if row["consortium_id"].lower() != clean_c_id:
                conn.rollback()
                return ("CONSORTIUM_MISMATCH", None, None)

            if row["principal_id"] != principal_id.strip():
                conn.rollback()
                return ("PRINCIPAL_MISMATCH", None, None)

            if row["request_fingerprint"] != request_fingerprint:
                conn.rollback()
                return ("FINGERPRINT_MISMATCH", None, None)

            status = row["status"]
            if status == "COMPLETED":
                resp_data = json.loads(row["response_json"]) if row["response_json"] else None
                gov_data = json.loads(row["governance_snapshot_json"]) if row["governance_snapshot_json"] else None
                conn.commit()
                return ("COMPLETED", resp_data, gov_data)

            if status == "FAILED_AMBIGUOUS":
                conn.rollback()
                return ("AMBIGUOUS_EXECUTION", None, None)

            if status == "IN_PROGRESS":
                try:
                    upd_dt = datetime.datetime.fromisoformat(row["updated_at"])
                    lease_expired = (now_dt - upd_dt).total_seconds() > in_progress_timeout
                except Exception:
                    lease_expired = False

                if lease_expired:
                    # In-flight execution interrupted/crashed: fail closed to prevent silent retry
                    conn.execute(
                        """
                        UPDATE cross_bank_idempotency_records
                        SET status = 'FAILED_AMBIGUOUS', updated_at = ?
                        WHERE lookup_key = ?;
                        """,
                        (now_iso, lookup_key),
                    )
                    conn.commit()
                    return ("AMBIGUOUS_EXECUTION", None, None)

                conn.commit()
                return ("IN_PROGRESS", None, None)

            conn.rollback()
            return ("AMBIGUOUS_EXECUTION", None, None)

    def await_completion(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        principal_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        timeout: float = 5.0,
        poll_interval: float = 0.05,
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        """Awaits resolution of an in-progress idempotency reservation."""
        import time

        clean_c_id = consortium_id.lower().strip()
        clean_bank = requesting_bank_id.lower().strip()
        clean_key = idempotency_key.strip()
        lookup_key = self._build_lookup_key(clean_c_id, clean_bank, clean_key)

        start = time.perf_counter()
        while time.perf_counter() - start < timeout:
            time.sleep(poll_interval)
            with self._lock, self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT * FROM cross_bank_idempotency_records WHERE lookup_key = ?;",
                    (lookup_key,),
                )
                row = cur.fetchone()
                if not row:
                    return ("NOT_FOUND", None, None)
                if row["principal_id"] != principal_id.strip():
                    return ("PRINCIPAL_MISMATCH", None, None)
                if row["request_fingerprint"] != request_fingerprint:
                    return ("FINGERPRINT_MISMATCH", None, None)
                if row["status"] == "COMPLETED":
                    resp_data = json.loads(row["response_json"]) if row["response_json"] else None
                    gov_data = json.loads(row["governance_snapshot_json"]) if row["governance_snapshot_json"] else None
                    return ("COMPLETED", resp_data, gov_data)
                if row["status"] == "FAILED_AMBIGUOUS":
                    return ("AMBIGUOUS_EXECUTION", None, None)

        return ("IN_PROGRESS", None, None)

    def complete(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        idempotency_key: str,
        owner_id: str,
        response_data: dict[str, Any],
        governance_snapshot: dict[str, Any],
    ) -> bool:
        """Atomically marks reservation as COMPLETED with serialized sanitized response."""
        lookup_key = self._build_lookup_key(consortium_id, requesting_bank_id, idempotency_key)
        now_iso = datetime.datetime.now(UTC).isoformat()
        resp_json = json.dumps(response_data, sort_keys=True, default=str)
        gov_json = json.dumps(governance_snapshot, sort_keys=True, default=str)

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            # Opportunistic operational cleanup on query completion
            self._maybe_cleanup_expired(conn, datetime.datetime.now(UTC), now_iso)
            cur = conn.execute(
                """
                UPDATE cross_bank_idempotency_records
                SET status = 'COMPLETED',
                    response_json = ?,
                    governance_snapshot_json = ?,
                    updated_at = ?
                WHERE lookup_key = ? AND execution_owner_id = ? AND status = 'IN_PROGRESS';
                """,
                (resp_json, gov_json, now_iso, lookup_key, owner_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def fail_or_abort(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        idempotency_key: str,
        owner_id: str,
        error_message: str = "",
        indeterminate: bool = False,
    ) -> bool:
        """Releases reservation or transitions to FAILED_AMBIGUOUS on execution interruption."""
        lookup_key = self._build_lookup_key(consortium_id, requesting_bank_id, idempotency_key)
        now_iso = datetime.datetime.now(UTC).isoformat()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            if indeterminate:
                # Mark as ambiguous failure: never repeat PSI silently
                cur = conn.execute(
                    """
                    UPDATE cross_bank_idempotency_records
                    SET status = 'FAILED_AMBIGUOUS',
                        updated_at = ?
                    WHERE lookup_key = ? AND execution_owner_id = ?;
                    """,
                    (now_iso, lookup_key, owner_id),
                )
            else:
                # Clean abort before any execution side-effects: delete to permit clean retry
                cur = conn.execute(
                    """
                    DELETE FROM cross_bank_idempotency_records
                    WHERE lookup_key = ? AND execution_owner_id = ? AND status = 'IN_PROGRESS';
                    """,
                    (lookup_key, owner_id),
                )
            conn.commit()
            return cur.rowcount > 0

    def cleanup_expired(self, in_progress_grace_seconds: float = 300.0) -> int:
        """Deletes expired idempotency records to ensure bounded storage retention."""
        now_dt = datetime.datetime.now(UTC)
        now_iso = now_dt.isoformat()
        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            deleted = self._execute_cleanup_in_conn(conn, now_dt, now_iso, in_progress_grace_seconds)
            conn.commit()
            return deleted

    def clear(self) -> None:
        """Clears all records (for test isolation only)."""
        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("DELETE FROM cross_bank_idempotency_records;")
            conn.commit()
