"""Tamper-Proof Immutable Cryptographic Audit Log Chain.

Chains every system action (alert reviews, case assignments, model rollbacks, user logins)
cryptographically using SHA-256 hash chaining:
    H_i = SHA-256( LogContent_i || H_{i-1} )

Includes retrospective integrity verification (verify_chain_integrity) to detect any
unauthorized deletion, insertion, or retroactive modification of historical log records.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

GENESIS_HASH = hashlib.sha256(b"GENESIS_BLOCK_CFI_AUDIT_CHAIN_2026").hexdigest()


@dataclass
class AuditLogEntry:
    """An individual entry in the immutable cryptographic audit chain."""

    index: int
    event_type: str
    actor: str
    target_id: str
    timestamp: str
    details: dict[str, Any]
    prev_hash: str
    curr_hash: str


@dataclass
class ChainVerificationReport:
    """Result of full SHA-256 cryptographic chain integrity verification."""

    is_valid: bool
    total_records: int
    broken_index: int | None = None
    tamper_reason: str | None = None
    genesis_hash: str = GENESIS_HASH
    last_hash: str = ""
    verified_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
    )


class ImmutableAuditChain:
    """Cryptographic ledger managing tamper-evident audit log entries."""

    _instance: ImmutableAuditChain | None = None

    def __init__(self) -> None:
        self.chain: list[AuditLogEntry] = []
        self._lock = threading.Lock()
        self._seed_default_chain()

    @classmethod
    def get_instance(cls) -> ImmutableAuditChain:
        if cls._instance is None:
            cls._instance = ImmutableAuditChain()
        return cls._instance

    def get_chain_proof_hash(self) -> str:
        """Get the current cryptographic proof hash of the audit chain tail."""
        if self.chain:
            return self.chain[-1].curr_hash
        return GENESIS_HASH

    def _seed_default_chain(self) -> None:
        """Seed genesis and initial system audit events."""
        if not self.chain:
            self.append_event(
                event_type="SYSTEM_BOOTSTRAP",
                actor="system",
                target_id="cfi_platform_v2",
                details={"message": "Cryptographic audit chain initialized with Genesis Block."},
            )
            self.append_event(
                event_type="SECURITY_SUITE_ACTIVATED",
                actor="secops_admin",
                target_id="pki_vault_oidc",
                details={"mtls": "enabled", "oidc": "active", "abac": "enforcing"},
            )

    def compute_entry_hash(
        self,
        index: int,
        event_type: str,
        actor: str,
        target_id: str,
        timestamp: str,
        details: dict[str, Any],
        prev_hash: str,
    ) -> str:
        """Calculate SHA-256 hash: H_i = SHA-256( L_i || H_{i-1} )."""
        serialized = json.dumps(
            {
                "index": index,
                "event_type": event_type,
                "actor": actor,
                "target_id": target_id,
                "timestamp": timestamp,
                "details": details,
                "prev_hash": prev_hash,
            },
            sort_keys=True,
        )
        payload = f"{serialized}||{prev_hash}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def append_event(
        self,
        event_type: str,
        actor: str,
        target_id: str,
        details: dict[str, Any] | None = None,
        timestamp_override: str | None = None,
    ) -> AuditLogEntry:
        """Append a new event to the cryptographic audit chain."""
        with self._lock:
            index = len(self.chain)
            prev_hash = self.chain[-1].curr_hash if self.chain else GENESIS_HASH
            timestamp = timestamp_override or time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
            evt_details = details or {}

            curr_hash = self.compute_entry_hash(
                index=index,
                event_type=event_type,
                actor=actor,
                target_id=target_id,
                timestamp=timestamp,
                details=evt_details,
                prev_hash=prev_hash,
            )

            entry = AuditLogEntry(
                index=index,
                event_type=event_type,
                actor=actor,
                target_id=target_id,
                timestamp=timestamp,
                details=evt_details,
                prev_hash=prev_hash,
                curr_hash=curr_hash,
            )
            self.chain.append(entry)
            logger.info(
                "Cryptographic audit log #%d appended [%s]. Hash: %s",
                index,
                event_type,
                curr_hash[:12],
            )

            # SIEM Real-Time Stream Forwarding
            try:
                from app.infrastructure.logging.siem_exporter import siem_exporter

                siem_exporter.export(
                    {
                        "event": event_type,
                        "actor": actor,
                        "target_id": target_id,
                        "timestamp": timestamp,
                        "details": evt_details,
                        "curr_hash": curr_hash,
                    }
                )
            except Exception as exc:
                logger.warning("SIEM dispatch failed for audit entry #%d: %s", index, exc)

            return entry

    def append(
        self,
        event_type: str,
        actor_bank_id: str,
        payload: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Alias method appending event with bank actor and payload dict."""
        return self.append_event(
            event_type=event_type,
            actor=actor_bank_id,
            target_id=payload.get("target_id", "consortium") if payload else "consortium",
            details=payload or {},
        )

    def verify_chain_integrity(self) -> ChainVerificationReport:
        """Verify full SHA-256 chain integrity from Genesis Block to tail."""
        if not self.chain:
            return ChainVerificationReport(
                is_valid=True,
                total_records=0,
                last_hash=GENESIS_HASH,
            )

        expected_prev = GENESIS_HASH

        for i, entry in enumerate(self.chain):
            # 1. Index sequence check
            if entry.index != i:
                return ChainVerificationReport(
                    is_valid=False,
                    total_records=len(self.chain),
                    broken_index=i,
                    tamper_reason=f"Index mismatch at position {i}: expected {i}, got {entry.index}.",
                    last_hash=entry.curr_hash,
                )

            # 2. Previous hash link check
            if entry.prev_hash != expected_prev:
                return ChainVerificationReport(
                    is_valid=False,
                    total_records=len(self.chain),
                    broken_index=i,
                    tamper_reason=f"Chain broken at entry #{i}: prev_hash '{entry.prev_hash[:8]}' does not match expected '{expected_prev[:8]}'.",
                    last_hash=entry.curr_hash,
                )

            # 3. Hash computation re-verification
            recomputed = self.compute_entry_hash(
                index=entry.index,
                event_type=entry.event_type,
                actor=entry.actor,
                target_id=entry.target_id,
                timestamp=entry.timestamp,
                details=entry.details,
                prev_hash=entry.prev_hash,
            )
            if recomputed != entry.curr_hash:
                return ChainVerificationReport(
                    is_valid=False,
                    total_records=len(self.chain),
                    broken_index=i,
                    tamper_reason=f"Tampering detected at entry #{i} ({entry.event_type}): recomputed hash '{recomputed[:8]}' != stored '{entry.curr_hash[:8]}'.",
                    last_hash=entry.curr_hash,
                )

            expected_prev = entry.curr_hash

        return ChainVerificationReport(
            is_valid=True,
            total_records=len(self.chain),
            last_hash=self.chain[-1].curr_hash,
        )
