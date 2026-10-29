"""Durable SQLite transactional storage for Server-Authoritative Cross-Bank Multi-Counterparty Batches.

Adheres strictly to .agents/AGENTS.md, runtime truth, and M6.5 Task 2 requirements:
- Server-authoritative batch-to-signal membership index.
- ACID-compliant transactional persistence using SQLite with WAL mode and busy timeouts.
- Concurrency-safe access to prevent race conditions during multi-worker queries and case assembly.
- Bounded retention with explicit cleanup for expired records.
- Strict prohibition on persisting bank-private raw identifiers, worker private keys,
  matching tokens, or credentials.
- Records original requesting institution, execution principal, query entity, target scope,
  case binding, execution outcomes, successful signals, and cryptographic receipts.
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
from pathlib import Path
from typing import Any

from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)

DEFAULT_BATCH_RETENTION_SECONDS = 86_400  # 24 hours standard retention


class DurableCrossBankBatchStore:
    """ACID-compliant SQLite store for server-authoritative multi-counterparty batch execution records."""

    _instance: DurableCrossBankBatchStore | None = None
    _singleton_lock = threading.Lock()

    def __init__(
        self,
        db_path: str | None = None,
        retention_seconds: int = DEFAULT_BATCH_RETENTION_SECONDS,
        cleanup_interval_seconds: float = 60.0,
    ) -> None:
        if db_path is None:
            env_path = os.environ.get("CFI_BATCH_DB_PATH") or os.environ.get("CFI_BATCH_STORE_PATH")
            if env_path:
                db_path = env_path
            else:
                storage_dir = get_storage_dir()
                db_path = os.path.join(storage_dir, "cfi_cross_bank_batches.sqlite")
        self.db_path = db_path
        self.retention_seconds = retention_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._last_cleanup_monotonic: float = 0.0
        self._lock = threading.RLock()
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: str | None = None) -> DurableCrossBankBatchStore:
        """Returns the singleton instance of the durable batch store."""
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls(db_path=db_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Resets the singleton instance (for test isolation)."""
        with cls._singleton_lock:
            cls._instance = None

    def _get_connection(self) -> sqlite3.Connection:
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cross_bank_batch_records (
                    query_id TEXT PRIMARY KEY,
                    requesting_bank_id TEXT NOT NULL,
                    consortium_id TEXT NOT NULL,
                    query_entity_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    principal_tenant_id TEXT NOT NULL,
                    case_id TEXT,
                    target_bank_ids_json TEXT NOT NULL,
                    execution_outcomes_json TEXT NOT NULL,
                    successful_signal_ids_json TEXT NOT NULL,
                    receipt_ids_json TEXT NOT NULL,
                    overall_outcome TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_batch_records_expires
                ON cross_bank_batch_records (expires_at);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_batch_records_req_bank
                ON cross_bank_batch_records (requesting_bank_id);
                """
            )
            conn.commit()

    def _maybe_cleanup_expired(self) -> None:
        if self.cleanup_interval_seconds <= 0.0:
            return
        now_mono = time.monotonic()
        if now_mono - self._last_cleanup_monotonic > self.cleanup_interval_seconds:
            self._last_cleanup_monotonic = now_mono
            try:
                self.cleanup_expired()
            except Exception as e:
                logger.warning("Error during periodic batch store cleanup: %s", e)

    def record_batch(
        self,
        query_id: str,
        requesting_bank_id: str,
        consortium_id: str,
        query_entity_id: str,
        principal_id: str,
        principal_tenant_id: str,
        target_bank_ids: list[str],
        execution_outcomes: dict[str, Any],
        successful_signal_ids: dict[str, str],
        receipt_ids: dict[str, str],
        overall_outcome: str,
        case_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Atomically record server-authoritative multi-counterparty batch execution details."""
        effective_ttl = ttl_seconds if ttl_seconds is not None else self.retention_seconds
        now = datetime.datetime.now(UTC)
        expires = now + datetime.timedelta(seconds=effective_ttl)

        now_iso = now.isoformat()
        expires_iso = expires.isoformat()

        target_bank_ids_json = json.dumps(sorted(list(set(t.lower().strip() for t in target_bank_ids))))
        execution_outcomes_json = json.dumps(execution_outcomes, sort_keys=True)
        successful_signal_ids_json = json.dumps(successful_signal_ids, sort_keys=True)
        receipt_ids_json = json.dumps(receipt_ids, sort_keys=True)

        with self._lock:
            self._maybe_cleanup_expired()
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO cross_bank_batch_records (
                        query_id,
                        requesting_bank_id,
                        consortium_id,
                        query_entity_id,
                        principal_id,
                        principal_tenant_id,
                        case_id,
                        target_bank_ids_json,
                        execution_outcomes_json,
                        successful_signal_ids_json,
                        receipt_ids_json,
                        overall_outcome,
                        created_at,
                        expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(query_id) DO UPDATE SET
                        execution_outcomes_json = excluded.execution_outcomes_json,
                        successful_signal_ids_json = excluded.successful_signal_ids_json,
                        receipt_ids_json = excluded.receipt_ids_json,
                        overall_outcome = excluded.overall_outcome,
                        expires_at = excluded.expires_at;
                    """,
                    (
                        query_id.strip(),
                        requesting_bank_id.lower().strip(),
                        consortium_id.lower().strip(),
                        query_entity_id.strip(),
                        principal_id.strip(),
                        principal_tenant_id.lower().strip(),
                        case_id.strip() if case_id else None,
                        target_bank_ids_json,
                        execution_outcomes_json,
                        successful_signal_ids_json,
                        receipt_ids_json,
                        overall_outcome.strip(),
                        now_iso,
                        expires_iso,
                    ),
                )
                conn.commit()

    def get_batch(self, query_id: str) -> dict[str, Any] | None:
        """Retrieve authoritative multi-counterparty batch record by query_id.

        Fails closed (returns None) if record does not exist or has expired.
        """
        clean_qid = query_id.strip()
        now_iso = datetime.datetime.now(UTC).isoformat()

        with self._lock:
            self._maybe_cleanup_expired()
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT * FROM cross_bank_batch_records
                    WHERE query_id = ?;
                    """,
                    (clean_qid,),
                )
                row = cursor.fetchone()
                if not row:
                    return None

                row_dict = dict(row)
                if row_dict["expires_at"] <= now_iso:
                    logger.warning("Batch record '%s' has expired (expired_at=%s).", clean_qid, row_dict["expires_at"])
                    return None

                return {
                    "query_id": row_dict["query_id"],
                    "requesting_bank_id": row_dict["requesting_bank_id"],
                    "consortium_id": row_dict["consortium_id"],
                    "query_entity_id": row_dict["query_entity_id"],
                    "principal_id": row_dict["principal_id"],
                    "principal_tenant_id": row_dict["principal_tenant_id"],
                    "case_id": row_dict["case_id"],
                    "target_bank_ids": json.loads(row_dict["target_bank_ids_json"]),
                    "execution_outcomes": json.loads(row_dict["execution_outcomes_json"]),
                    "successful_signal_ids": json.loads(row_dict["successful_signal_ids_json"]),
                    "receipt_ids": json.loads(row_dict["receipt_ids_json"]),
                    "overall_outcome": row_dict["overall_outcome"],
                    "created_at": row_dict["created_at"],
                    "expires_at": row_dict["expires_at"],
                }

    def cleanup_expired(self) -> int:
        """Deletes all batch records whose expires_at is before current UTC time."""
        now_iso = datetime.datetime.now(UTC).isoformat()
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM cross_bank_batch_records
                WHERE expires_at <= ?;
                """,
                (now_iso,),
            )
            deleted_count = cursor.rowcount
            conn.commit()
            if deleted_count > 0:
                logger.info("Purged %d expired cross-bank batch records.", deleted_count)
            return deleted_count
