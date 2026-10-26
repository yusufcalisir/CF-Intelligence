"""Durable SQLite transactional storage for Controlled Pilot Operations.

Adheres strictly to .agents/AGENTS.md and Milestone 5 Section 3:
- Durable transactional persistence using SQLite with WAL mode.
- Process restart preserves exact pilot state (PAUSED, ABORTED, ACTIVE, quotas, approvals).
- Atomic query reservations and quota enforcement under concurrent processes.
- Optimistic concurrency control (state_version) to prevent race condition overwrites.
- Irreversible terminal state enforcement.
- Thread-safe and process-safe.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sqlite3
import threading
from datetime import UTC
from typing import Any

from app.domain.pilot_operations import (
    InvalidPilotTransitionError,
    PilotEntity,
    PilotExecutionReceipt,
    PilotExpiredError,
    PilotGovernanceError,
    PilotQuotaExceededError,
    PilotStatus,
)
from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)


class DurablePilotStore:
    """ACID-compliant SQLite store for controlled pilot operations with atomic reservations."""

    _instance: DurablePilotStore | None = None
    _singleton_lock = threading.Lock()

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            storage_dir = get_storage_dir()
            db_path = os.path.join(storage_dir, "cfi_pilot_operations.sqlite")
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: str | None = None) -> DurablePilotStore:
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls(db_path=db_path)
        return cls._instance

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pilot_operations (
                    pilot_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    consortium_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scope_digest TEXT NOT NULL,
                    queries_executed INTEGER NOT NULL DEFAULT 0,
                    queries_denied INTEGER NOT NULL DEFAULT 0,
                    state_version INTEGER NOT NULL DEFAULT 1,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            # Check if state_version column exists on pre-existing tables
            cur = conn.execute("PRAGMA table_info(pilot_operations);")
            cols = {row["name"] for row in cur.fetchall()}
            if "state_version" not in cols:
                conn.execute("ALTER TABLE pilot_operations ADD COLUMN state_version INTEGER NOT NULL DEFAULT 1;")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS pilot_query_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    pilot_id TEXT NOT NULL,
                    requesting_bank_id TEXT NOT NULL,
                    target_bank_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reserved_at TEXT NOT NULL,
                    finalized_at TEXT,
                    receipt_id TEXT,
                    signal_id TEXT,
                    FOREIGN KEY(pilot_id) REFERENCES pilot_operations(pilot_id)
                );
            """)
            cur_res = conn.execute("PRAGMA table_info(pilot_query_reservations);")
            res_cols = {row["name"] for row in cur_res.fetchall()}
            if "signal_id" not in res_cols:
                conn.execute("ALTER TABLE pilot_query_reservations ADD COLUMN signal_id TEXT;")
            if "published_at" not in res_cols:
                conn.execute("ALTER TABLE pilot_query_reservations ADD COLUMN published_at TEXT;")
            if "rejection_reason" not in res_cols:
                conn.execute("ALTER TABLE pilot_query_reservations ADD COLUMN rejection_reason TEXT;")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_pilot_status ON pilot_operations(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pilot_consortium ON pilot_operations(consortium_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_res_pilot ON pilot_query_reservations(pilot_id, status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_res_bank ON pilot_query_reservations(pilot_id, requesting_bank_id, status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_res_signal ON pilot_query_reservations(signal_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_res_published ON pilot_query_reservations(status, signal_id);")
            conn.commit()

    def save_pilot(self, entity: PilotEntity) -> PilotEntity:
        """Atomically persist or update a pilot entity with optimistic concurrency."""
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("SELECT status, state_version FROM pilot_operations WHERE pilot_id = ?;", (entity.config.pilot_id,))
            row = cur.fetchone()

            if row is not None:
                existing_status = row["status"]
                curr_v = row["state_version"]

                # Irreversible terminal states
                terminal_states = {PilotStatus.ABORTED.value, PilotStatus.COMPLETED.value, PilotStatus.FAILED.value}
                if existing_status in terminal_states and entity.status.value not in terminal_states:
                    raise InvalidPilotTransitionError(
                        f"Cannot transition pilot '{entity.config.pilot_id}' from terminal status '{existing_status}' "
                        f"to non-terminal status '{entity.status.value}'. Terminal state transitions are permanent."
                    )

                new_v = curr_v + 1
                entity.state_version = new_v
                entity.updated_at = datetime.datetime.now(UTC).isoformat()
                data_json = json.dumps(entity.to_dict(), separators=(",", ":"))

                up_cur = conn.execute("""
                    UPDATE pilot_operations SET
                        revision = ?,
                        consortium_id = ?,
                        status = ?,
                        scope_digest = ?,
                        queries_executed = ?,
                        queries_denied = ?,
                        state_version = ?,
                        data_json = ?,
                        updated_at = ?
                    WHERE pilot_id = ? AND state_version = ?;
                """, (
                    entity.config.revision,
                    entity.config.consortium_id,
                    entity.status.value,
                    entity.scope_digest,
                    entity.queries_executed,
                    entity.queries_denied,
                    new_v,
                    data_json,
                    entity.updated_at,
                    entity.config.pilot_id,
                    curr_v,
                ))
                if up_cur.rowcount == 0:
                    raise PilotGovernanceError(
                        f"Optimistic concurrency violation: Pilot '{entity.config.pilot_id}' state was concurrently modified."
                    )
            else:
                entity.state_version = 1
                data_json = json.dumps(entity.to_dict(), separators=(",", ":"))
                conn.execute("""
                    INSERT INTO pilot_operations (
                        pilot_id, revision, consortium_id, status, scope_digest,
                        queries_executed, queries_denied, state_version, data_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?);
                """, (
                    entity.config.pilot_id,
                    entity.config.revision,
                    entity.config.consortium_id,
                    entity.status.value,
                    entity.scope_digest,
                    entity.queries_executed,
                    entity.queries_denied,
                    data_json,
                    entity.created_at,
                    entity.updated_at,
                ))
            conn.commit()
        return entity

    def reserve_query_slot(
        self,
        pilot_id: str,
        requesting_bank_id: str,
        target_bank_id: str,
        purpose: str,
        reservation_id: str,
    ) -> tuple[PilotEntity, str]:
        """Atomically reserve a durable execution slot against quotas under immediate database lock."""
        clean_pilot_id = pilot_id.strip()
        clean_req = requesting_bank_id.lower().strip()
        clean_target = target_bank_id.lower().strip()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                "SELECT data_json, status, state_version FROM pilot_operations WHERE pilot_id = ?;",
                (clean_pilot_id,),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                raise ValueError(f"Pilot '{clean_pilot_id}' not found.")

            status_val = row["status"]
            if status_val != PilotStatus.ACTIVE.value:
                conn.rollback()
                raise PilotGovernanceError(
                    f"Pilot '{clean_pilot_id}' is in non-active status '{status_val}'. Query reservation denied."
                )

            entity = PilotEntity.from_dict(json.loads(row["data_json"]))
            entity.state_version = int(row["state_version"])

            if entity.has_expired():
                conn.rollback()
                raise PilotExpiredError(
                    f"Pilot '{clean_pilot_id}' expired at {entity.config.expires_at}. Query reservation denied."
                )

            # Count total in-flight active reservations
            cur_tot = conn.execute(
                "SELECT COUNT(*) as c FROM pilot_query_reservations WHERE pilot_id = ? AND status = 'RESERVED';",
                (clean_pilot_id,),
            )
            tot_in_flight = cur_tot.fetchone()["c"]
            if entity.queries_executed + tot_in_flight >= entity.config.query_quota_total:
                conn.rollback()
                raise PilotQuotaExceededError(
                    f"Total query quota exhausted for pilot '{clean_pilot_id}' "
                    f"({entity.queries_executed + tot_in_flight} >= {entity.config.query_quota_total})."
                )

            # Count per-institution in-flight active reservations
            cur_inst = conn.execute(
                "SELECT COUNT(*) as c FROM pilot_query_reservations WHERE pilot_id = ? AND requesting_bank_id = ? AND status = 'RESERVED';",
                (clean_pilot_id, clean_req),
            )
            inst_in_flight = cur_inst.fetchone()["c"]
            inst_executed = entity.institution_query_counts.get(clean_req, 0)
            if inst_executed + inst_in_flight >= entity.config.query_quota_per_institution:
                conn.rollback()
                raise PilotQuotaExceededError(
                    f"Requesting bank '{clean_req}' query quota exhausted for pilot '{clean_pilot_id}' "
                    f"({inst_executed + inst_in_flight} >= {entity.config.query_quota_per_institution})."
                )

            now_iso = datetime.datetime.now(UTC).isoformat()
            conn.execute("""
                INSERT INTO pilot_query_reservations (
                    reservation_id, pilot_id, requesting_bank_id, target_bank_id,
                    purpose, status, reserved_at
                ) VALUES (?, ?, ?, ?, ?, 'RESERVED', ?);
            """, (reservation_id, clean_pilot_id, clean_req, clean_target, purpose, now_iso))
            conn.commit()
            return entity, reservation_id

    def commit_query_reservation(
        self,
        reservation_id: str,
        pilot_id: str,
        receipt_id: str,
        latency_ms: float,
        match_confirmed: bool | None,
        signal_id: str | None = None,
        case_id: str | None = None,
    ) -> tuple[PilotEntity, PilotExecutionReceipt]:
        """Atomically commit an in-flight reservation upon successful PSI execution and result admission."""
        clean_pilot_id = pilot_id.strip()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur_res = conn.execute(
                "SELECT status, requesting_bank_id, target_bank_id, purpose FROM pilot_query_reservations WHERE reservation_id = ?;",
                (reservation_id,),
            )
            res_row = cur_res.fetchone()
            if not res_row or res_row["status"] != "RESERVED":
                conn.rollback()
                raise PilotGovernanceError(
                    f"Reservation '{reservation_id}' is not in active RESERVED state (status={res_row['status'] if res_row else 'NOT_FOUND'})."
                )

            cur_p = conn.execute(
                "SELECT data_json, status, state_version FROM pilot_operations WHERE pilot_id = ?;",
                (clean_pilot_id,),
            )
            p_row = cur_p.fetchone()
            if not p_row:
                conn.rollback()
                raise ValueError(f"Pilot '{clean_pilot_id}' not found.")

            # Kill switch check: fail closed if pilot was paused/aborted mid-flight!
            if p_row["status"] != PilotStatus.ACTIVE.value:
                now_iso = datetime.datetime.now(UTC).isoformat()
                conn.execute(
                    "UPDATE pilot_query_reservations SET status = 'ABORTED', finalized_at = ? WHERE reservation_id = ?;",
                    (now_iso, reservation_id),
                )
                conn.commit()
                raise PilotGovernanceError(
                    f"Kill switch activated: Pilot '{clean_pilot_id}' status transitioned to '{p_row['status']}' "
                    "during in-flight execution. Authoritative result admission rejected."
                )

            entity = PilotEntity.from_dict(json.loads(p_row["data_json"]))
            curr_v = int(p_row["state_version"])
            receipt = entity.record_query_outcome(
                requesting_bank_id=res_row["requesting_bank_id"],
                target_bank_id=res_row["target_bank_id"],
                purpose=res_row["purpose"],
                status="COMPLETED",
                latency_ms=latency_ms,
                match_confirmed=match_confirmed,
                receipt_id=receipt_id,
                signal_id=signal_id,
                case_id=case_id,
            )
            new_v = curr_v + 1
            entity.state_version = new_v
            data_json = json.dumps(entity.to_dict(), separators=(",", ":"))
            now_iso = datetime.datetime.now(UTC).isoformat()

            up_cur = conn.execute("""
                UPDATE pilot_operations SET
                    queries_executed = ?,
                    data_json = ?,
                    updated_at = ?,
                    state_version = ?
                WHERE pilot_id = ? AND state_version = ?;
            """, (entity.queries_executed, data_json, now_iso, new_v, clean_pilot_id, curr_v))
            if up_cur.rowcount == 0:
                conn.rollback()
                raise PilotGovernanceError("Concurrent pilot modification conflict during reservation commit.")

            conn.execute("""
                UPDATE pilot_query_reservations SET
                    status = 'COMMITTED',
                    finalized_at = ?,
                    receipt_id = ?,
                    signal_id = ?
                WHERE reservation_id = ?;
            """, (now_iso, receipt_id, signal_id, reservation_id))
            conn.commit()
            return entity, receipt

    def authorize_and_record_publication(
        self,
        reservation_id: str,
        pilot_id: str,
        signal_id: str,
        receipt_id: str,
    ) -> None:
        """Atomically authorize and record authoritative signal publication under exclusive store lock.

        Enforces post-commit publication boundary:
        - Re-checks pilot state is ACTIVE.
        - If pilot has transitioned to PAUSED/ABORTED/TERMINATED, publication is rejected,
          reservation status transitions to 'PUBLICATION_REJECTED', and PilotGovernanceError is raised.
        - If pilot is ACTIVE, reservation status transitions to 'PUBLISHED' with exact
          bindings to signal_id and receipt_id.
        """
        clean_pilot_id = pilot_id.strip()
        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur_res = conn.execute(
                "SELECT status, signal_id, receipt_id FROM pilot_query_reservations WHERE reservation_id = ?;",
                (reservation_id,),
            )
            res_row = cur_res.fetchone()
            if not res_row:
                conn.rollback()
                raise PilotGovernanceError(f"Reservation '{reservation_id}' not found for publication.")

            res_status = res_row["status"]
            if res_status == "PUBLICATION_REJECTED":
                conn.rollback()
                raise PilotGovernanceError(
                    f"Publication rejected: Reservation '{reservation_id}' was previously rejected."
                )
            if res_status not in ("COMMITTED", "PUBLISHED"):
                conn.rollback()
                raise PilotGovernanceError(
                    f"Cannot publish reservation '{reservation_id}' in status '{res_status}'. Must be 'COMMITTED'."
                )

            # Re-check pilot governance state
            cur_p = conn.execute(
                "SELECT status FROM pilot_operations WHERE pilot_id = ?;",
                (clean_pilot_id,),
            )
            p_row = cur_p.fetchone()
            if not p_row:
                conn.rollback()
                raise ValueError(f"Pilot '{clean_pilot_id}' not found.")

            curr_status = p_row["status"]
            now_iso = datetime.datetime.now(UTC).isoformat()
            if curr_status != PilotStatus.ACTIVE.value:
                # Pilot transitioned away from ACTIVE before publication authorization!
                rejection_msg = (
                    f"Pilot '{clean_pilot_id}' transitioned to non-active status '{curr_status}' "
                    f"prior to authoritative signal publication. Publication rejected."
                )
                conn.execute(
                    """UPDATE pilot_query_reservations SET
                        status = 'PUBLICATION_REJECTED',
                        finalized_at = ?,
                        rejection_reason = ?
                    WHERE reservation_id = ?;""",
                    (now_iso, rejection_msg, reservation_id),
                )
                conn.commit()
                raise PilotGovernanceError(rejection_msg)

            # Pilot is ACTIVE: finalize publication
            conn.execute(
                """UPDATE pilot_query_reservations SET
                    status = 'PUBLISHED',
                    published_at = ?,
                    signal_id = ?,
                    receipt_id = ?
                WHERE reservation_id = ?;""",
                (now_iso, signal_id, receipt_id, reservation_id),
            )
            conn.commit()

    def record_publication_status(
        self,
        reservation_id: str,
        pilot_id: str,
        status: str,
        rejection_reason: str | None = None,
    ) -> None:
        """Explicitly set publication status on reservation."""
        now_iso = datetime.datetime.now(UTC).isoformat()
        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            if status == "PUBLISHED":
                conn.execute(
                    "UPDATE pilot_query_reservations SET status = 'PUBLISHED', published_at = ? WHERE reservation_id = ?;",
                    (now_iso, reservation_id),
                )
            elif status == "PUBLICATION_REJECTED":
                conn.execute(
                    """UPDATE pilot_query_reservations SET
                        status = 'PUBLICATION_REJECTED',
                        finalized_at = ?,
                        rejection_reason = ?
                    WHERE reservation_id = ?;""",
                    (now_iso, rejection_reason or "Publication rejected", reservation_id),
                )
            else:
                conn.execute(
                    "UPDATE pilot_query_reservations SET status = ? WHERE reservation_id = ?;",
                    (status, reservation_id),
                )
            conn.commit()

    def release_query_reservation(
        self,
        reservation_id: str,
        pilot_id: str,
        reason: str = "FAILED",
        is_denied: bool = True,
    ) -> None:
        """Release an in-flight reservation to prevent quota leaks on error or mid-flight abort."""
        clean_pilot_id = pilot_id.strip()

        with self._lock, self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur_res = conn.execute(
                "SELECT status, requesting_bank_id FROM pilot_query_reservations WHERE reservation_id = ?;",
                (reservation_id,),
            )
            res_row = cur_res.fetchone()
            if res_row and res_row["status"] == "RESERVED":
                now_iso = datetime.datetime.now(UTC).isoformat()
                conn.execute("""
                    UPDATE pilot_query_reservations SET
                        status = 'RELEASED',
                        finalized_at = ?
                    WHERE reservation_id = ?;
                """, (now_iso, reservation_id))

                if is_denied:
                    cur_p = conn.execute(
                        "SELECT data_json, state_version FROM pilot_operations WHERE pilot_id = ?;",
                        (clean_pilot_id,),
                    )
                    p_row = cur_p.fetchone()
                    if p_row:
                        entity = PilotEntity.from_dict(json.loads(p_row["data_json"]))
                        curr_v = int(p_row["state_version"])
                        entity.queries_denied += 1
                        new_v = curr_v + 1
                        entity.state_version = new_v
                        data_json = json.dumps(entity.to_dict(), separators=(",", ":"))
                        conn.execute("""
                            UPDATE pilot_operations SET
                                queries_denied = ?,
                                data_json = ?,
                                updated_at = ?,
                                state_version = ?
                            WHERE pilot_id = ? AND state_version = ?;
                        """, (entity.queries_denied, data_json, now_iso, new_v, clean_pilot_id, curr_v))
            conn.commit()

    def get_pilot(self, pilot_id: str) -> PilotEntity | None:
        """Fetch a pilot entity from durable storage."""
        clean_id = pilot_id.strip()
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("SELECT data_json FROM pilot_operations WHERE pilot_id = ?;", (clean_id,))
            row = cur.fetchone()
            if not row:
                return None
            data = json.loads(row["data_json"])
            return PilotEntity.from_dict(data)

    def list_pilots(self) -> list[PilotEntity]:
        """List all pilots in storage ordered by updated_at desc."""
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("SELECT data_json FROM pilot_operations ORDER BY updated_at DESC;")
            rows = cur.fetchall()
            return [PilotEntity.from_dict(json.loads(row["data_json"])) for row in rows]

    def get_active_pilots(self) -> list[PilotEntity]:
        """List currently ACTIVE pilots."""
        with self._lock, self._get_connection() as conn:
            cur = conn.execute(
                "SELECT data_json FROM pilot_operations WHERE status = ? ORDER BY updated_at DESC;",
                (PilotStatus.ACTIVE.value,),
            )
            rows = cur.fetchall()
            return [PilotEntity.from_dict(json.loads(row["data_json"])) for row in rows]

    def delete_pilot(self, pilot_id: str) -> bool:
        """Delete a pilot record (for testing teardown only)."""
        clean_id = pilot_id.strip()
        with self._lock, self._get_connection() as conn:
            conn.execute("DELETE FROM pilot_query_reservations WHERE pilot_id = ?;", (clean_id,))
            cur = conn.execute("DELETE FROM pilot_operations WHERE pilot_id = ?;", (clean_id,))
            conn.commit()
            return cur.rowcount > 0

    def get_query_reservation(self, reservation_id: str) -> dict[str, Any] | None:
        """Fetch reservation record by ID."""
        with self._lock, self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM pilot_query_reservations WHERE reservation_id = ?;",
                (reservation_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return dict(row)
