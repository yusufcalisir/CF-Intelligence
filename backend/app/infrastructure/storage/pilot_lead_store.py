"""Durable SQLite storage for Design Partner commercial pilot leads."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import Any

from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)


class PilotLeadStore:
    """ACID-compliant SQLite store for design partner POC and pilot leads."""

    _instance: PilotLeadStore | None = None
    _singleton_lock = threading.Lock()

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            storage_dir = get_storage_dir()
            db_path = os.path.join(storage_dir, "cfi_pilot_leads.sqlite")
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: str | None = None) -> PilotLeadStore:
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
                CREATE TABLE IF NOT EXISTS pilot_leads (
                    lead_id TEXT PRIMARY KEY,
                    institution_name TEXT NOT NULL,
                    contact_name TEXT NOT NULL,
                    contact_email TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assigned_tier TEXT NOT NULL,
                    sandbox_provisioned INTEGER NOT NULL,
                    jurisdiction TEXT NOT NULL,
                    monthly_tx_volume INTEGER NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pilot_leads_created ON pilot_leads(created_at);")

            # Seed demo baseline leads only if table is completely empty
            cur = conn.execute("SELECT COUNT(*) as cnt FROM pilot_leads;")
            count = cur.fetchone()["cnt"]
            if count == 0:
                conn.executemany("""
                    INSERT INTO pilot_leads (
                        lead_id, institution_name, contact_name, contact_email,
                        status, assigned_tier, sandbox_provisioned, jurisdiction,
                        monthly_tx_volume, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, [
                    (
                        "lead-tier1-alpha-001",
                        "EuroClear Bank Consortium",
                        "Securities Operations",
                        "pilot@euroclear.internal",
                        "APPROVED_FOR_PILOT",
                        "TIER_1",
                        1,
                        "EU",
                        5000000,
                        "Core clearing and settlement consortium sandbox",
                        "2026-08-15T10:00:00Z",
                    ),
                    (
                        "lead-tier1-beta-002",
                        "Nordic Cross-Border Payment Rail",
                        "Infrastructure Lead",
                        "pilot@nordicrail.internal",
                        "SANDBOX_ACTIVE",
                        "TIER_1",
                        1,
                        "EU",
                        3500000,
                        "Cross-border payment corridor pilot",
                        "2026-09-01T14:30:00Z",
                    ),
                ])
                conn.commit()

    def insert_lead(self, record: dict[str, Any]) -> dict[str, Any]:
        """Durably persist a new pilot lead."""
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                INSERT INTO pilot_leads (
                    lead_id, institution_name, contact_name, contact_email,
                    status, assigned_tier, sandbox_provisioned, jurisdiction,
                    monthly_tx_volume, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                record["lead_id"],
                record["institution_name"],
                record.get("contact_name", ""),
                record.get("contact_email", ""),
                record["status"],
                record["assigned_tier"],
                1 if record.get("sandbox_provisioned") else 0,
                record.get("jurisdiction", "US"),
                int(record.get("monthly_tx_volume", 0)),
                record.get("notes"),
                record["created_at"],
            ))
            conn.commit()
        return record

    def list_leads(self) -> list[dict[str, Any]]:
        """Return all leads from persistent storage ordered by created_at."""
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM pilot_leads ORDER BY created_at ASC;")
            rows = cur.fetchall()
            return [
                {
                    "lead_id": row["lead_id"],
                    "institution_name": row["institution_name"],
                    "contact_name": row["contact_name"],
                    "contact_email": row["contact_email"],
                    "status": row["status"],
                    "assigned_tier": row["assigned_tier"],
                    "sandbox_provisioned": bool(row["sandbox_provisioned"]),
                    "jurisdiction": row["jurisdiction"],
                    "monthly_tx_volume": row["monthly_tx_volume"],
                    "notes": row["notes"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def get_overview_counts(self) -> tuple[int, int]:
        """Returns (total_leads, active_sandboxes)."""
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("SELECT COUNT(*) as total, SUM(sandbox_provisioned) as active FROM pilot_leads;")
            row = cur.fetchone()
            total = row["total"] or 0
            active = row["active"] or 0
            return int(total), int(active)
