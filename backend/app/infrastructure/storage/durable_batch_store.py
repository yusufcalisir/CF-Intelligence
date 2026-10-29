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

from app.domain.consortium_governance import ConsortiumSecurityError
from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)

DEFAULT_BATCH_RETENTION_SECONDS = 86_400  # 24 hours standard retention


class BatchStorageError(Exception):
    """Base exception for durable batch store errors."""


class BatchStorageConflictError(BatchStorageError):
    """Raised when an attempt is made to overwrite or alter an immutable batch record."""


class BatchNotFoundError(BatchStorageError, ValueError):
    """Raised when an authoritative batch is not found or has expired."""


class BatchCaseBindingConflictError(ConsortiumSecurityError, BatchStorageConflictError):
    """Raised when an attempt is made to bind an already-bound batch to a differing destination case."""


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
                    bound_case_id TEXT,
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
            # Schema evolution check for existing sqlite instances
            cursor = conn.execute("PRAGMA table_info(cross_bank_batch_records);")
            columns = {row["name"] for row in cursor.fetchall()}
            if "bound_case_id" not in columns:
                conn.execute("ALTER TABLE cross_bank_batch_records ADD COLUMN bound_case_id TEXT;")

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
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_batch_records_bound_case
                ON cross_bank_batch_records (bound_case_id);
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
        """Atomically record server-authoritative multi-counterparty batch execution details.

        Enforces strict insert-only immutability. If a record with the same query_id already exists:
        - If all execution fields match identically, it succeeds as an idempotent replay (no-op).
        - If any field differs, it fails closed with BatchStorageConflictError.
        """
        clean_qid = query_id.strip()
        clean_req_bank = requesting_bank_id.lower().strip()
        clean_consortium = consortium_id.lower().strip()
        clean_query_entity = query_entity_id.strip()
        clean_principal = principal_id.strip()
        clean_tenant = principal_tenant_id.lower().strip()
        clean_case_id = case_id.strip() if case_id else None
        clean_overall = overall_outcome.strip()

        norm_target_ids = sorted(list(set(t.lower().strip() for t in target_bank_ids)))
        target_bank_ids_json = json.dumps(norm_target_ids)
        execution_outcomes_json = json.dumps(execution_outcomes, sort_keys=True)
        successful_signal_ids_json = json.dumps(successful_signal_ids, sort_keys=True)
        receipt_ids_json = json.dumps(receipt_ids, sort_keys=True)

        effective_ttl = ttl_seconds if ttl_seconds is not None else self.retention_seconds
        now = datetime.datetime.now(UTC)
        expires = now + datetime.timedelta(seconds=effective_ttl)
        now_iso = now.isoformat()
        expires_iso = expires.isoformat()

        with self._lock:
            self._maybe_cleanup_expired()
            with self._get_connection() as conn:
                try:
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
                            bound_case_id,
                            target_bank_ids_json,
                            execution_outcomes_json,
                            successful_signal_ids_json,
                            receipt_ids_json,
                            overall_outcome,
                            created_at,
                            expires_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            clean_qid,
                            clean_req_bank,
                            clean_consortium,
                            clean_query_entity,
                            clean_principal,
                            clean_tenant,
                            clean_case_id,
                            clean_case_id,  # Initially bound to query case_id if specified
                            target_bank_ids_json,
                            execution_outcomes_json,
                            successful_signal_ids_json,
                            receipt_ids_json,
                            clean_overall,
                            now_iso,
                            expires_iso,
                        ),
                    )
                    conn.commit()
                except sqlite3.IntegrityError as err:
                    # Query ID already exists: verify strict idempotent immutability
                    cursor = conn.execute(
                        "SELECT * FROM cross_bank_batch_records WHERE query_id = ?;",
                        (clean_qid,),
                    )
                    existing = cursor.fetchone()
                    if not existing:
                        raise BatchStorageConflictError(
                            f"Integrity conflict for batch '{clean_qid}': {err}"
                        ) from err

                    existing_dict = dict(existing)
                    mismatches: list[str] = []

                    if existing_dict["requesting_bank_id"] != clean_req_bank:
                        mismatches.append(
                            f"requesting_bank_id ('{existing_dict['requesting_bank_id']}' != '{clean_req_bank}')"
                        )
                    if existing_dict["consortium_id"] != clean_consortium:
                        mismatches.append(
                            f"consortium_id ('{existing_dict['consortium_id']}' != '{clean_consortium}')"
                        )
                    if existing_dict["query_entity_id"] != clean_query_entity:
                        mismatches.append(
                            f"query_entity_id ('{existing_dict['query_entity_id']}' != '{clean_query_entity}')"
                        )
                    if existing_dict["principal_id"] != clean_principal:
                        mismatches.append(
                            f"principal_id ('{existing_dict['principal_id']}' != '{clean_principal}')"
                        )
                    if existing_dict["principal_tenant_id"] != clean_tenant:
                        mismatches.append(
                            f"principal_tenant_id ('{existing_dict['principal_tenant_id']}' != '{clean_tenant}')"
                        )
                    if existing_dict["case_id"] != clean_case_id:
                        mismatches.append(
                            f"case_id ('{existing_dict['case_id']}' != '{clean_case_id}')"
                        )
                    if existing_dict["overall_outcome"] != clean_overall:
                        mismatches.append(
                            f"overall_outcome ('{existing_dict['overall_outcome']}' != '{clean_overall}')"
                        )

                    existing_targets = json.loads(existing_dict["target_bank_ids_json"])
                    if existing_targets != norm_target_ids:
                        mismatches.append(
                            f"target_bank_ids ({existing_targets} != {norm_target_ids})"
                        )

                    existing_signals = json.loads(existing_dict["successful_signal_ids_json"])
                    if existing_signals != successful_signal_ids:
                        mismatches.append(
                            f"successful_signal_ids ({existing_signals} != {successful_signal_ids})"
                        )

                    existing_receipts = json.loads(existing_dict["receipt_ids_json"])
                    if existing_receipts != receipt_ids:
                        mismatches.append(
                            f"receipt_ids ({existing_receipts} != {receipt_ids})"
                        )

                    existing_outcomes = json.loads(existing_dict["execution_outcomes_json"])
                    if existing_outcomes != execution_outcomes:
                        mismatches.append(
                            "execution_outcomes mismatch"
                        )

                    if mismatches:
                        raise BatchStorageConflictError(
                            f"Immutable batch record conflict for query '{clean_qid}': "
                            f"Cannot overwrite authoritative record with differing execution details: {', '.join(mismatches)}."
                        )

                    logger.debug("Idempotent record_batch replay for '%s'. Existing immutable record preserved.", clean_qid)

    def bind_case(self, query_id: str, case_id: str) -> str:
        """Atomically establish destination case binding for an authoritative batch.

        Enforces Single-Case Binding:
        1. If the batch was issued with a case_id at query execution, that binding is immutable.
        2. If the batch was un-bound, the first authorized destination case is atomically bound.
        3. Subsequent attempts to assemble the same batch into a differing case raise BatchCaseBindingConflictError.
        4. Replays for the same case succeed idempotently.
        """
        clean_qid = (query_id or "").strip()
        clean_case_id = (case_id or "").strip()
        if not clean_qid or not clean_case_id:
            raise ValueError("Both query_id and case_id are required for case binding.")

        with self._lock:
            self._maybe_cleanup_expired()
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT case_id, bound_case_id, expires_at FROM cross_bank_batch_records WHERE query_id = ?;",
                    (clean_qid,),
                )
                row = cursor.fetchone()
                if not row:
                    raise BatchNotFoundError(f"Authoritative batch '{clean_qid}' not found or never issued.")

                now_iso = datetime.datetime.now(UTC).isoformat()
                if row["expires_at"] <= now_iso:
                    raise BatchNotFoundError(f"Authoritative batch '{clean_qid}' has expired.")

                initial_case_id = row["case_id"]
                existing_bound = row["bound_case_id"]

                # 1. If batch was explicitly bound to a case at query execution time
                if initial_case_id:
                    if initial_case_id != clean_case_id:
                        raise BatchCaseBindingConflictError(
                            f"Cross-case binding violation: Multi-counterparty query '{clean_qid}' was bound to case "
                            f"'{initial_case_id}', cannot be assembled into differing case '{clean_case_id}'."
                        )
                    if existing_bound != initial_case_id:
                        conn.execute(
                            "UPDATE cross_bank_batch_records SET bound_case_id = ? WHERE query_id = ?;",
                            (initial_case_id, clean_qid),
                        )
                        conn.commit()
                    return initial_case_id

                # 2. If already bound to a first destination case
                if existing_bound:
                    if existing_bound != clean_case_id:
                        raise BatchCaseBindingConflictError(
                            f"Cross-case binding violation: Multi-counterparty query '{clean_qid}' is already bound to destination case "
                            f"'{existing_bound}', cannot be assembled into differing case '{clean_case_id}'."
                        )
                    return existing_bound

                # 3. Atomically establish the first destination case binding under lock
                cursor = conn.execute(
                    """
                    UPDATE cross_bank_batch_records
                    SET bound_case_id = ?
                    WHERE query_id = ? AND (bound_case_id IS NULL OR bound_case_id = ?);
                    """,
                    (clean_case_id, clean_qid, clean_case_id),
                )
                if cursor.rowcount == 0:
                    c2 = conn.execute(
                        "SELECT bound_case_id FROM cross_bank_batch_records WHERE query_id = ?;",
                        (clean_qid,),
                    )
                    r2 = c2.fetchone()
                    winning = r2["bound_case_id"] if r2 else "unknown"
                    raise BatchCaseBindingConflictError(
                        f"Cross-case binding violation: Concurrent investigator bound query '{clean_qid}' to case '{winning}'; cannot bind to '{clean_case_id}'."
                    )
                conn.commit()
                return clean_case_id

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
                    "bound_case_id": row_dict.get("bound_case_id") or row_dict["case_id"],
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
