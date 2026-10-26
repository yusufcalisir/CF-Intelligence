"""Durable SQLite transactional storage for Controlled Pilot Operations.

Adheres strictly to .agents/AGENTS.md and Milestone 5 Section 3:
- Durable transactional persistence using SQLite with WAL mode.
- Process restart preserves exact pilot state (PAUSED, ABORTED, ACTIVE, quotas, approvals).
- No silent reauthorization upon restart.
- Thread-safe and process-safe.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from typing import Any

from app.domain.pilot_operations import PilotEntity, PilotStatus
from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)


class DurablePilotStore:
    """ACID-compliant SQLite store for controlled pilot operations."""

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
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pilot_status ON pilot_operations(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pilot_consortium ON pilot_operations(consortium_id);")
            conn.commit()

    def save_pilot(self, entity: PilotEntity) -> PilotEntity:
        """Atomically persist or update a pilot entity."""
        with self._lock, self._get_connection() as conn:
            data_json = json.dumps(entity.to_dict(), separators=(",", ":"))
            conn.execute("""
                INSERT INTO pilot_operations (
                    pilot_id, revision, consortium_id, status, scope_digest,
                    queries_executed, queries_denied, data_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pilot_id) DO UPDATE SET
                    revision = excluded.revision,
                    consortium_id = excluded.consortium_id,
                    status = excluded.status,
                    scope_digest = excluded.scope_digest,
                    queries_executed = excluded.queries_executed,
                    queries_denied = excluded.queries_denied,
                    data_json = excluded.data_json,
                    updated_at = excluded.updated_at;
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
            cur = conn.execute("DELETE FROM pilot_operations WHERE pilot_id = ?;", (clean_id,))
            conn.commit()
            return cur.rowcount > 0
