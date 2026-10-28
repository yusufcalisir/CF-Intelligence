"""Durable SQLite transactional storage for Cross-Bank Per-Counterparty Circuit Breaker.

Adheres strictly to .agents/AGENTS.md, runtime truth, and M6.4 Task 5 requirements:
- Scoped to consortium_id, requesting_bank_id, and target_bank_id.
- ACID-compliant transactional persistence using SQLite with WAL mode, busy timeouts, and BEGIN IMMEDIATE locks.
- Concurrency-safe atomic state transitions across independent service instances (CLOSED, OPEN, HALF_OPEN).
- Enforces single recovery-probe admission gate in HALF_OPEN state across concurrent replicas.
- Counts ONLY verified eligible transport, timeout, and worker-unavailability failures.
- Cryptographic, governance, authorization, consent, ticket/receipt integrity, and admission failures
  are never treated as availability failures or recorded as transport errors.
- Bounded retention and strictly sanitized diagnostic categories.
- Absolute prohibition on persisting customer IDs, privacy hashes, private keys, matching tokens,
  credentials, response payloads, or internal hostnames/URLs.
- Default behavior preserves existing deployments unless explicitly enabled.
"""

from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import threading
import uuid
from datetime import UTC
from typing import Any

from app.domain.circuit_breaker_policy import (
    DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    DEFAULT_HALF_OPEN_PROBE_TIMEOUT_SECONDS,
    CircuitState,
    CounterpartyCircuitBreakerPolicy,
)
from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)

# Strictly bounded, fixed diagnostic failure categories
ALLOWED_CIRCUIT_BREAKER_REASONS = frozenset(
    {
        "TIMEOUT",
        "WORKER_UNAVAILABLE",
        "TRANSPORT_FAILURE",
        "SECURITY_REJECTED",
        "PROBE_FAILED",
        "INDETERMINATE",
    }
)

# Eligible failure categories that count toward circuit opening
ELIGIBLE_FAILURE_CATEGORIES = frozenset(
    {
        "TIMEOUT",
        "WORKER_UNAVAILABLE",
        "TRANSPORT_FAILURE",
    }
)


def sanitize_circuit_breaker_reason(reason: str | None, default: str = "INDETERMINATE") -> str:
    """Sanitizes circuit breaker failure reasons to strictly bounded static categories.

    Prevents raw exceptions, stack traces, hostnames, tokens, or identifiers from
    being written to persistent storage.
    """
    if not reason:
        return default
    r_upper = reason.upper().strip()
    if r_upper in ALLOWED_CIRCUIT_BREAKER_REASONS:
        return r_upper
    if "TIMEOUT" in r_upper or "DEADLINE" in r_upper:
        return "TIMEOUT"
    if "UNAVAILABLE" in r_upper or "REFUSED" in r_upper or "OFFLINE" in r_upper:
        return "WORKER_UNAVAILABLE"
    if "TRANSPORT" in r_upper or "GATEWAY" in r_upper or "CONNECTION" in r_upper:
        return "TRANSPORT_FAILURE"
    if "PROBE" in r_upper:
        return "PROBE_FAILED"
    if "SECURITY" in r_upper or "GOVERNANCE" in r_upper or "AUTH" in r_upper:
        return "SECURITY_REJECTED"
    return default


class DurableCrossBankCircuitBreakerStore:
    """ACID-compliant SQLite store for durable cross-bank per-counterparty circuit breakers."""

    _instance: DurableCrossBankCircuitBreakerStore | None = None
    _singleton_lock = threading.Lock()

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            env_path = os.environ.get("CFI_CIRCUIT_BREAKER_DB_PATH")
            if env_path:
                db_path = env_path
            else:
                storage_dir = get_storage_dir()
                db_path = os.path.join(storage_dir, "cfi_cross_bank_circuit_breaker.sqlite")
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: str | None = None) -> DurableCrossBankCircuitBreakerStore:
        """Returns the singleton instance of the durable circuit breaker store."""
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
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cross_bank_circuit_breaker_states (
                    lookup_key TEXT PRIMARY KEY,
                    consortium_id TEXT NOT NULL,
                    requesting_bank_id TEXT NOT NULL,
                    target_bank_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'CLOSED',
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    failure_threshold INTEGER NOT NULL DEFAULT 3,
                    cooldown_seconds REAL NOT NULL DEFAULT 60.0,
                    last_failure_at TEXT,
                    last_failure_reason TEXT,
                    opened_at TEXT,
                    cooldown_expires_at TEXT,
                    half_open_probe_id TEXT,
                    half_open_probe_started_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_circuit_breaker_pair ON cross_bank_circuit_breaker_states (
                    consortium_id, requesting_bank_id, target_bank_id
                );
                """
            )

    @staticmethod
    def _make_lookup_key(consortium_id: str, requesting_bank_id: str, target_bank_id: str) -> str:
        return f"{consortium_id.lower().strip()}:{requesting_bank_id.lower().strip()}:{target_bank_id.lower().strip()}"

    def evaluate_and_acquire_dispatch(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        target_bank_id: str,
        policy: CounterpartyCircuitBreakerPolicy | None = None,
    ) -> tuple[bool, str, str | None]:
        """Atomically evaluate circuit state and optionally admit a single recovery probe.

        Returns:
            (allowed: bool, state: str, probe_id_or_rejection_reason: str | None)
            - If allowed is True and state is 'CLOSED': normal execution permitted.
            - If allowed is True and state is 'HALF_OPEN': this caller is granted the single recovery probe lease.
            - If allowed is False and state is 'OPEN': dispatch is rejected due to active cooldown.
            - If allowed is False and state is 'HALF_OPEN': dispatch is rejected because another recovery probe is in flight.
        """
        lookup_key = self._make_lookup_key(consortium_id, requesting_bank_id, target_bank_id)
        probe_timeout = policy.half_open_probe_timeout_seconds if policy else DEFAULT_HALF_OPEN_PROBE_TIMEOUT_SECONDS

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM cross_bank_circuit_breaker_states WHERE lookup_key = ?",
                    (lookup_key,),
                ).fetchone()

                now_dt = datetime.datetime.now(UTC)
                now_iso = now_dt.isoformat()

                if row is None:
                    # No record exists: implicitly CLOSED
                    conn.commit()
                    return True, CircuitState.CLOSED.value, None

                current_state = str(row["state"])

                if current_state == CircuitState.CLOSED.value:
                    conn.commit()
                    return True, CircuitState.CLOSED.value, None

                if current_state == CircuitState.OPEN.value:
                    expires_at_str = row["cooldown_expires_at"]
                    expires_at_dt = None
                    if expires_at_str:
                        try:
                            expires_at_dt = datetime.datetime.fromisoformat(expires_at_str)
                        except (ValueError, TypeError):
                            expires_at_dt = None

                    if expires_at_dt is not None and now_dt < expires_at_dt:
                        # Cooldown is still active
                        remaining = max(0.0, (expires_at_dt - now_dt).total_seconds())
                        conn.commit()
                        return (
                            False,
                            CircuitState.OPEN.value,
                            f"cooldown in progress ({remaining:.1f}s remaining)",
                        )

                    # Cooldown has expired: atomically transition to HALF_OPEN and admit this single probe
                    probe_id = str(uuid.uuid4())
                    conn.execute(
                        """
                        UPDATE cross_bank_circuit_breaker_states
                        SET state = ?,
                            half_open_probe_id = ?,
                            half_open_probe_started_at = ?,
                            updated_at = ?
                        WHERE lookup_key = ?
                        """,
                        (CircuitState.HALF_OPEN.value, probe_id, now_iso, now_iso, lookup_key),
                    )
                    conn.commit()
                    return True, CircuitState.HALF_OPEN.value, probe_id

                if current_state == CircuitState.HALF_OPEN.value:
                    existing_probe_id = row["half_open_probe_id"]
                    probe_started_at_str = row["half_open_probe_started_at"]

                    if not existing_probe_id:
                        # No active probe lease: admit this caller
                        probe_id = str(uuid.uuid4())
                        conn.execute(
                            """
                            UPDATE cross_bank_circuit_breaker_states
                            SET half_open_probe_id = ?,
                                half_open_probe_started_at = ?,
                                updated_at = ?
                            WHERE lookup_key = ?
                            """,
                            (probe_id, now_iso, now_iso, lookup_key),
                        )
                        conn.commit()
                        return True, CircuitState.HALF_OPEN.value, probe_id

                    # A probe is already active: check if it timed out / was abandoned
                    probe_started_dt = None
                    if probe_started_at_str:
                        try:
                            probe_started_dt = datetime.datetime.fromisoformat(probe_started_at_str)
                        except (ValueError, TypeError):
                            probe_started_dt = None

                    if probe_started_dt is not None and (now_dt - probe_started_dt).total_seconds() > probe_timeout:
                        # Stale probe lease reclaimed: admit this caller
                        new_probe_id = str(uuid.uuid4())
                        conn.execute(
                            """
                            UPDATE cross_bank_circuit_breaker_states
                            SET half_open_probe_id = ?,
                                half_open_probe_started_at = ?,
                                updated_at = ?
                            WHERE lookup_key = ?
                            """,
                            (new_probe_id, now_iso, now_iso, lookup_key),
                        )
                        conn.commit()
                        return True, CircuitState.HALF_OPEN.value, new_probe_id

                    # Active recovery probe currently in flight: reject concurrent callers
                    conn.commit()
                    return False, CircuitState.HALF_OPEN.value, "recovery probe currently in flight"

                # Unknown state fallback
                conn.commit()
                return True, CircuitState.CLOSED.value, None
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def record_success(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        target_bank_id: str,
        probe_id: str | None = None,
    ) -> None:
        """Record an authentic, cryptographically verified healthy execution.

        Resets consecutive failures to 0 and transitions HALF_OPEN -> CLOSED.
        """
        lookup_key = self._make_lookup_key(consortium_id, requesting_bank_id, target_bank_id)
        now_iso = datetime.datetime.now(UTC).isoformat()

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM cross_bank_circuit_breaker_states WHERE lookup_key = ?",
                    (lookup_key,),
                ).fetchone()

                if row is not None:
                    conn.execute(
                        """
                        UPDATE cross_bank_circuit_breaker_states
                        SET state = ?,
                            consecutive_failures = 0,
                            half_open_probe_id = NULL,
                            half_open_probe_started_at = NULL,
                            cooldown_expires_at = NULL,
                            opened_at = NULL,
                            last_failure_reason = NULL,
                            updated_at = ?
                        WHERE lookup_key = ?
                        """,
                        (CircuitState.CLOSED.value, now_iso, lookup_key),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def record_failure(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        target_bank_id: str,
        error_category: str,
        probe_id: str | None = None,
        policy: CounterpartyCircuitBreakerPolicy | None = None,
    ) -> None:
        """Record an eligible transport, timeout, or worker-unavailability failure.

        If consecutive failures reach threshold, or if in HALF_OPEN, transitions to OPEN.
        Security, authorization, or integrity errors must NEVER be passed here.
        """
        sanitized_category = sanitize_circuit_breaker_reason(error_category)
        if sanitized_category not in ELIGIBLE_FAILURE_CATEGORIES:
            logger.warning(
                "Ineligible failure category '%s' ignored for circuit breaker counting (target: %s).",
                error_category,
                target_bank_id,
            )
            return

        lookup_key = self._make_lookup_key(consortium_id, requesting_bank_id, target_bank_id)
        clean_consortium = consortium_id.lower().strip()
        clean_requesting = requesting_bank_id.lower().strip()
        clean_target = target_bank_id.lower().strip()

        effective_threshold = policy.failure_threshold if policy else DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD
        effective_cooldown = policy.cooldown_seconds if policy else DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS

        now_dt = datetime.datetime.now(UTC)
        now_iso = now_dt.isoformat()
        expires_at_iso = (now_dt + datetime.timedelta(seconds=effective_cooldown)).isoformat()

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM cross_bank_circuit_breaker_states WHERE lookup_key = ?",
                    (lookup_key,),
                ).fetchone()

                if row is None:
                    failures = 1
                    should_open = failures >= effective_threshold
                    state = CircuitState.OPEN.value if should_open else CircuitState.CLOSED.value
                    opened_at_val = now_iso if should_open else None
                    expires_at_val = expires_at_iso if should_open else None

                    conn.execute(
                        """
                        INSERT INTO cross_bank_circuit_breaker_states (
                            lookup_key, consortium_id, requesting_bank_id, target_bank_id,
                            state, consecutive_failures, failure_threshold, cooldown_seconds,
                            last_failure_at, last_failure_reason, opened_at, cooldown_expires_at,
                            half_open_probe_id, half_open_probe_started_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                        """,
                        (
                            lookup_key,
                            clean_consortium,
                            clean_requesting,
                            clean_target,
                            state,
                            failures,
                            effective_threshold,
                            effective_cooldown,
                            now_iso,
                            sanitized_category,
                            opened_at_val,
                            expires_at_val,
                            now_iso,
                            now_iso,
                        ),
                    )
                else:
                    failures = int(row["consecutive_failures"]) + 1
                    threshold = policy.failure_threshold if policy else int(row["failure_threshold"])
                    is_half_open = row["state"] == CircuitState.HALF_OPEN.value
                    # Any eligible failure during HALF_OPEN probe immediately re-opens the circuit
                    should_open = is_half_open or (failures >= threshold)
                    new_state = CircuitState.OPEN.value if should_open else CircuitState.CLOSED.value
                    opened_at_val = now_iso if should_open else row["opened_at"]
                    expires_at_val = expires_at_iso if should_open else None

                    conn.execute(
                        """
                        UPDATE cross_bank_circuit_breaker_states
                        SET state = ?,
                            consecutive_failures = ?,
                            failure_threshold = ?,
                            cooldown_seconds = ?,
                            last_failure_at = ?,
                            last_failure_reason = ?,
                            opened_at = ?,
                            cooldown_expires_at = ?,
                            half_open_probe_id = NULL,
                            half_open_probe_started_at = NULL,
                            updated_at = ?
                        WHERE lookup_key = ?
                        """,
                        (
                            new_state,
                            failures,
                            threshold,
                            effective_cooldown,
                            now_iso,
                            sanitized_category,
                            opened_at_val,
                            expires_at_val,
                            now_iso,
                            lookup_key,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def record_probe_aborted(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        target_bank_id: str,
        probe_id: str | None = None,
        reason: str = "SECURITY_REJECTED",
        policy: CounterpartyCircuitBreakerPolicy | None = None,
    ) -> None:
        """Handle an aborted recovery probe due to a non-recoverable security or indeterminate error.

        Clears the probe lease and ensures the circuit remains/re-enters OPEN without claiming recovery.
        Does NOT count toward eligible transport failure accumulation.
        """
        lookup_key = self._make_lookup_key(consortium_id, requesting_bank_id, target_bank_id)
        sanitized_reason = sanitize_circuit_breaker_reason(reason)
        effective_cooldown = policy.cooldown_seconds if policy else DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS

        now_dt = datetime.datetime.now(UTC)
        now_iso = now_dt.isoformat()
        expires_at_iso = (now_dt + datetime.timedelta(seconds=effective_cooldown)).isoformat()

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM cross_bank_circuit_breaker_states WHERE lookup_key = ?",
                    (lookup_key,),
                ).fetchone()

                if row is not None and row["state"] == CircuitState.HALF_OPEN.value:
                    conn.execute(
                        """
                        UPDATE cross_bank_circuit_breaker_states
                        SET state = ?,
                            cooldown_expires_at = ?,
                            half_open_probe_id = NULL,
                            half_open_probe_started_at = NULL,
                            last_failure_reason = ?,
                            updated_at = ?
                        WHERE lookup_key = ?
                        """,
                        (
                            CircuitState.OPEN.value,
                            expires_at_iso,
                            sanitized_reason,
                            now_iso,
                            lookup_key,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def get_circuit_state(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        target_bank_id: str,
    ) -> dict[str, Any]:
        """Inspect current circuit breaker state for a specific counterparty pair."""
        lookup_key = self._make_lookup_key(consortium_id, requesting_bank_id, target_bank_id)
        with self._lock, self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM cross_bank_circuit_breaker_states WHERE lookup_key = ?",
                (lookup_key,),
            ).fetchone()

            if row is None:
                return {
                    "consortium_id": consortium_id.lower().strip(),
                    "requesting_bank_id": requesting_bank_id.lower().strip(),
                    "target_bank_id": target_bank_id.lower().strip(),
                    "state": CircuitState.CLOSED.value,
                    "consecutive_failures": 0,
                    "failure_threshold": DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
                    "cooldown_seconds": DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
                    "last_failure_at": None,
                    "last_failure_reason": None,
                    "opened_at": None,
                    "cooldown_expires_at": None,
                    "cooldown_remaining_seconds": 0.0,
                    "half_open_probe_id": None,
                    "updated_at": None,
                }

            now_dt = datetime.datetime.now(UTC)
            expires_at_str = row["cooldown_expires_at"]
            remaining = 0.0
            evaluated_state = str(row["state"])

            if evaluated_state == CircuitState.OPEN.value and expires_at_str:
                try:
                    expires_at_dt = datetime.datetime.fromisoformat(expires_at_str)
                    if now_dt >= expires_at_dt:
                        evaluated_state = CircuitState.HALF_OPEN.value
                    else:
                        remaining = max(0.0, (expires_at_dt - now_dt).total_seconds())
                except (ValueError, TypeError):
                    pass

            return {
                "consortium_id": str(row["consortium_id"]),
                "requesting_bank_id": str(row["requesting_bank_id"]),
                "target_bank_id": str(row["target_bank_id"]),
                "state": evaluated_state,
                "consecutive_failures": int(row["consecutive_failures"]),
                "failure_threshold": int(row["failure_threshold"]),
                "cooldown_seconds": float(row["cooldown_seconds"]),
                "last_failure_at": row["last_failure_at"],
                "last_failure_reason": row["last_failure_reason"],
                "opened_at": row["opened_at"],
                "cooldown_expires_at": row["cooldown_expires_at"],
                "cooldown_remaining_seconds": remaining,
                "half_open_probe_id": row["half_open_probe_id"],
                "updated_at": row["updated_at"],
            }

    def reset_circuit(
        self,
        consortium_id: str,
        requesting_bank_id: str,
        target_bank_id: str,
    ) -> None:
        """Administrative / testing helper to unconditionally reset a counterparty circuit to CLOSED."""
        lookup_key = self._make_lookup_key(consortium_id, requesting_bank_id, target_bank_id)
        now_iso = datetime.datetime.now(UTC).isoformat()
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    UPDATE cross_bank_circuit_breaker_states
                    SET state = ?,
                        consecutive_failures = 0,
                        half_open_probe_id = NULL,
                        half_open_probe_started_at = NULL,
                        cooldown_expires_at = NULL,
                        opened_at = NULL,
                        last_failure_reason = NULL,
                        updated_at = ?
                    WHERE lookup_key = ?
                    """,
                    (CircuitState.CLOSED.value, now_iso, lookup_key),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
