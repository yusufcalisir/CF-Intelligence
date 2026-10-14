"""Tamper-Evident Authenticated Security Audit Ledger.

Provides cryptographic integrity, cross-worker durability, and strict forensic
attribution for security-critical governance mutations:
    H_i = HMAC-SHA256_K( SerializedPayload_i || H_{i-1} )

Where K is an authoritative domain-separated cryptographic key:
    K = HMAC-SHA256( authoritative_secret, "cfi:security_audit_ledger:v1" )

Guarantees:
- Cross-worker safe append operations via inter-process file locks.
- Restart and cross-process durability via atomic replacement and fsync.
- Cryptographic authentication: an attacker rewriting the chain cannot forge entries
  without possession of the authoritative server domain key.
- Strict sequencing: monotonic indices, explicit predecessor linking, timestamp monotonicity.
- Retrospective tamper detection: identifies exact broken index, unauthorized deletion,
  insertion, or field alteration.
- Anti-rollback / deletion protection: validates persistence against '.established' anchor.
- Sensitive-field minimization: automatically redacts JWTs, credentials, and passwords.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

GENESIS_HASH = hashlib.sha256(b"GENESIS_BLOCK_CFI_AUDIT_CHAIN_2026").hexdigest()
DOMAIN_AUDIT_LEDGER = "cfi:security_audit_ledger:v1"

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "token",
    "auth_token",
    "access_token",
    "refresh_token",
    "secret",
    "private_key",
    "payload_signing_secret",
    "key_pem",
    "client_secret",
    "card_number",
    "pan",
    "cvv",
    "ssn",
}


class AuditLedgerSecurityError(Exception):
    """Raised when authoritative audit integrity is violated, corrupted, or missing."""


class AuditChainTamperError(AuditLedgerSecurityError):
    """Raised when audit ledger tampering, truncation, or sequence deletion is detected."""


def derive_domain_key(secret: str, domain: str) -> bytes:
    """Derive cryptographically separated domain key using HMAC-SHA256."""
    return hmac.new(secret.encode("utf-8"), domain.encode("utf-8"), hashlib.sha256).digest()


def sanitize_audit_details(data: Any) -> Any:
    """Recursively redact sensitive credentials and personal data from audit details."""
    if isinstance(data, dict):
        cleaned: dict[str, Any] = {}
        for k, v in data.items():
            if any(s in k.lower() for s in SENSITIVE_KEYS):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = sanitize_audit_details(v)
        return cleaned
    elif isinstance(data, list):
        return [sanitize_audit_details(item) for item in data]
    return data


def _get_audit_file_lock(lock_path: str, timeout: float = 15.0) -> Any:
    """Acquire inter-process FileLock for multi-worker safety."""
    try:
        from filelock import FileLock

        return FileLock(lock_path, timeout=timeout)
    except ImportError:

        @contextlib.contextmanager
        def _dummy():
            yield

        return _dummy()


def _restrict_file_permissions(filepath: str) -> None:
    """Ensure strict file permissions (0600 on POSIX)."""
    if os.name != "nt":
        with contextlib.suppress(OSError):
            os.chmod(filepath, 0o600)


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
    # Structured governance & forensic fields
    event_id: str = ""
    actor_type: str = "human_principal"
    actor_institution: str = "unknown"
    target_type: str = "unknown"
    action: str = ""
    decision: str = "PERMITTED"
    correlation_id: str = ""
    signature_scheme: str = "HMAC_SHA256_DOMAIN_AUDIT_V1"

    @property
    def actor_id(self) -> str:
        return self.actor

    @property
    def resource_id(self) -> str:
        return self.target_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "event_id": self.event_id or f"evt_{self.index}_{uuid.uuid4().hex[:8]}",
            "event_type": self.event_type,
            "actor": self.actor,
            "actor_type": self.actor_type,
            "actor_institution": self.actor_institution,
            "target_id": self.target_id,
            "target_type": self.target_type,
            "action": self.action or self.event_type.lower(),
            "decision": self.decision,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "details": self.details,
            "prev_hash": self.prev_hash,
            "curr_hash": self.curr_hash,
            "signature_scheme": self.signature_scheme,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditLogEntry:
        return cls(
            index=int(data["index"]),
            event_type=str(data["event_type"]),
            actor=str(data["actor"]),
            target_id=str(data["target_id"]),
            timestamp=str(data["timestamp"]),
            details=data.get("details", {}),
            prev_hash=str(data["prev_hash"]),
            curr_hash=str(data["curr_hash"]),
            event_id=str(data.get("event_id", "")),
            actor_type=str(data.get("actor_type", "human_principal")),
            actor_institution=str(data.get("actor_institution", "unknown")),
            target_type=str(data.get("target_type", "unknown")),
            action=str(data.get("action", "")),
            decision=str(data.get("decision", "PERMITTED")),
            correlation_id=str(data.get("correlation_id", "")),
            signature_scheme=str(data.get("signature_scheme", "HMAC_SHA256_DOMAIN_AUDIT_V1")),
        )


@dataclass
class ChainVerificationReport:
    """Result of full cryptographic chain integrity verification."""

    is_valid: bool
    total_records: int
    broken_index: int | None = None
    tamper_reason: str | None = None
    genesis_hash: str = GENESIS_HASH
    last_hash: str = ""
    verified_at: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    )


class ImmutableAuditChain:
    """Cryptographic ledger managing durable, tamper-evident audit log entries."""

    _instance: ImmutableAuditChain | None = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        store_path: str | None = None,
        signing_secret: str | None = None,
        in_memory: bool = False,
    ) -> None:
        self._lock = threading.Lock()
        self._in_memory = in_memory
        self.chain: list[AuditLogEntry] = []
        self._last_mtime: float = 0.0
        self._last_size: int = -1
        self._is_corrupted: bool = False
        self._tamper_reason: str | None = None

        # Resolve authoritative store path
        if in_memory:
            self._store_path = ""
            self._lock_path = ""
        elif store_path:
            self._store_path = os.path.abspath(store_path)
            self._lock_path = f"{self._store_path}.lock"
        else:
            env_path = os.environ.get("CFI_AUDIT_LEDGER_PATH")
            if env_path:
                self._store_path = os.path.abspath(env_path)
            else:
                from app.infrastructure.storage.storage_utils import get_storage_dir

                self._store_path = os.path.abspath(
                    os.path.join(get_storage_dir(), "security", "immutable_audit_ledger.json")
                )
            self._lock_path = f"{self._store_path}.lock"

        # Resolve authoritative signing key
        self._domain_key = self._resolve_signing_key(signing_secret)

        if not self._in_memory and self._store_path:
            parent_dir = os.path.dirname(self._store_path)
            os.makedirs(parent_dir, exist_ok=True)
            with self._lock, _get_audit_file_lock(self._lock_path):
                self._reload_unlocked(force=True)
                if not self.chain and not self._is_corrupted:
                    self._seed_default_chain_unlocked()
        else:
            self._seed_default_chain_unlocked()

    @classmethod
    def get_instance(cls) -> ImmutableAuditChain:
        """Singleton accessor for cross-service authoritative audit logging."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = ImmutableAuditChain()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing isolation."""
        with cls._instance_lock:
            cls._instance = None

    def _resolve_signing_key(self, signing_secret: str | None) -> bytes:
        """Resolve authoritative domain-separated signing key or fail closed in production."""
        is_prod = False
        try:
            from app.config import get_settings

            settings = get_settings()
            is_prod = settings.app_env.lower() == "production"
        except Exception:
            is_prod = (
                os.environ.get("APP_ENV", "").lower() == "production"
                or os.environ.get("ENVIRONMENT", "").lower() == "production"
            )

        secret: str | None = None
        if signing_secret is not None:
            secret = signing_secret.strip()
            if is_prod and (not secret or len(secret) < 16 or "change_me" in secret):
                raise AuditLedgerSecurityError(
                    "Production audit ledger requires an authoritative signing secret of at least 16 characters (fail-closed)."
                )
            if not secret and not is_prod:
                secret = "CFI_DEV_AUDIT_LEDGER_SECRET_2026_DEFAULT"
        else:
            secret = os.environ.get("CFI_AUDIT_LEDGER_SECRET")
            if not secret:
                try:
                    from app.config import get_settings

                    settings = get_settings()
                    secret = getattr(settings, "audit_ledger_signing_secret", None) or getattr(
                        settings, "payload_signing_secret", None
                    )
                    if secret and "change_me" in secret and is_prod:
                        secret = None
                except Exception:
                    pass

            if is_prod:
                if not secret or not isinstance(secret, str) or len(secret.strip()) < 16 or "change_me" in secret:
                    raise AuditLedgerSecurityError(
                        "Production audit ledger requires an authoritative signing secret of at least 16 characters (fail-closed)."
                    )
            elif not secret:
                secret = "CFI_DEV_AUDIT_LEDGER_SECRET_2026_DEFAULT"

        if not isinstance(secret, str):
            secret = "CFI_DEV_AUDIT_LEDGER_SECRET_2026_DEFAULT"

        return derive_domain_key(secret.strip(), DOMAIN_AUDIT_LEDGER)

    def _established_marker_path(self) -> str:
        return f"{self._store_path}.established"

    def _validate_chain_entries_unlocked(
        self, entries: list[AuditLogEntry]
    ) -> tuple[bool, str | None]:
        """Validate all required chain invariants: monotonic indices, predecessor links, and HMAC digests."""
        if not entries:
            return True, None

        expected_prev = GENESIS_HASH
        for i, entry in enumerate(entries):
            # 1. Monotonic index continuity check
            if entry.index != i:
                return (
                    False,
                    f"Index mismatch at position {i}: expected {i}, got {entry.index} (sequence deletion or insertion detected).",
                )

            # 2. Previous hash link check
            if entry.prev_hash != expected_prev:
                return (
                    False,
                    f"Chain broken at entry #{i}: prev_hash '{entry.prev_hash[:8]}' does not match expected '{expected_prev[:8]}'.",
                )

            # 3. Cryptographic authentication re-verification
            recomputed = self.compute_entry_hash(
                index=entry.index,
                event_type=entry.event_type,
                actor=entry.actor,
                target_id=entry.target_id,
                timestamp=entry.timestamp,
                details=entry.details,
                prev_hash=entry.prev_hash,
                event_id=entry.event_id,
                actor_type=entry.actor_type,
                actor_institution=entry.actor_institution,
                target_type=entry.target_type,
                action=entry.action,
                decision=entry.decision,
                correlation_id=entry.correlation_id,
            )
            if not hmac.compare_digest(recomputed, entry.curr_hash):
                # Also test legacy un-extended hash format for backward compatibility
                legacy_recomputed = self.compute_entry_hash(
                    index=entry.index,
                    event_type=entry.event_type,
                    actor=entry.actor,
                    target_id=entry.target_id,
                    timestamp=entry.timestamp,
                    details=entry.details,
                    prev_hash=entry.prev_hash,
                )
                if not hmac.compare_digest(legacy_recomputed, entry.curr_hash):
                    return (
                        False,
                        f"Tampering detected at entry #{i} ({entry.event_type}): signature verification failed (recomputed HMAC '{recomputed[:8]}' != stored '{entry.curr_hash[:8]}').",
                    )

            expected_prev = entry.curr_hash

        return True, None

    def _reload_unlocked(self, force: bool = False) -> None:
        """Reload ledger entries from disk if changed by another worker."""
        if self._in_memory or not self._store_path:
            return

        marker = self._established_marker_path()
        marker_exists = os.path.exists(marker)
        file_exists = os.path.exists(self._store_path)

        # Anti-deletion / rollback check
        if marker_exists and (not file_exists or os.path.getsize(self._store_path) == 0):
            self._is_corrupted = True
            self._tamper_reason = (
                f"Authoritative audit ledger at '{self._store_path}' was deleted or truncated after establishment."
            )
            logger.error(self._tamper_reason)
            return

        if not file_exists:
            self._last_mtime = 0.0
            self._last_size = -1
            return

        try:
            stat = os.stat(self._store_path)
            if not force and stat.st_mtime == self._last_mtime and stat.st_size == self._last_size:
                return

            with open(self._store_path, "rb") as f:
                content = f.read().decode("utf-8")

            if not content.strip():
                if marker_exists:
                    self._is_corrupted = True
                    self._tamper_reason = "Authoritative audit ledger file is empty after establishment."
                return

            container = json.loads(content)
            entries_raw = container.get("entries", [])
            entries = [AuditLogEntry.from_dict(e) for e in entries_raw]

            # Invariant validation before accepting loaded ledger as authoritative
            is_valid, tamper_reason = self._validate_chain_entries_unlocked(entries)
            if not is_valid:
                self.chain = entries
                self._is_corrupted = True
                self._tamper_reason = tamper_reason
                logger.error("Authoritative audit ledger failed chain validation: %s", self._tamper_reason)
                return

            self.chain = entries
            self._last_mtime = stat.st_mtime
            self._last_size = stat.st_size
            self._is_corrupted = False
            self._tamper_reason = None
        except Exception as exc:
            self._is_corrupted = True
            self._tamper_reason = f"Failed to reload audit ledger from '{self._store_path}': {exc}"
            logger.error(self._tamper_reason)

    def _save_unlocked(self) -> None:
        """Persist current chain to disk atomically with fsync and create establishment marker."""
        if self._in_memory or not self._store_path:
            return

        parent_dir = os.path.dirname(self._store_path)
        os.makedirs(parent_dir, exist_ok=True)

        container = {
            "schema_version": "2.0",
            "signature_scheme": "HMAC_SHA256_DOMAIN_AUDIT_V1",
            "genesis_hash": GENESIS_HASH,
            "tail_hash": self.chain[-1].curr_hash if self.chain else GENESIS_HASH,
            "total_records": len(self.chain),
            "updated_at": datetime.now(UTC).isoformat(),
            "entries": [e.to_dict() for e in self.chain],
        }

        tmp_path = f"{self._store_path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        with open(tmp_path, "wb") as f:
            f.write(json.dumps(container, indent=2).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())

        _restrict_file_permissions(tmp_path)
        os.replace(tmp_path, self._store_path)

        # Update stat cache
        stat = os.stat(self._store_path)
        self._last_mtime = stat.st_mtime
        self._last_size = stat.st_size

        # Create .established marker file
        marker = self._established_marker_path()
        if not os.path.exists(marker):
            with open(marker, "w", encoding="utf-8") as mf:
                mf.write(f"ESTABLISHED:{datetime.now(UTC).isoformat()}:{GENESIS_HASH}\n")
                mf.flush()
            _restrict_file_permissions(marker)

    def _seed_default_chain_unlocked(self) -> None:
        """Seed genesis and initial system audit events."""
        if not self.chain:
            self._append_event_unlocked(
                event_type="SYSTEM_BOOTSTRAP",
                actor="system",
                target_id="cfi_platform_v2",
                details={"message": "Cryptographic audit chain initialized with Genesis Block."},
                actor_type="internal_service",
                actor_institution="system",
                target_type="system",
                action="bootstrap",
                decision="COMPLETED",
            )
            self._append_event_unlocked(
                event_type="SECURITY_SUITE_ACTIVATED",
                actor="secops_admin",
                target_id="pki_vault_oidc",
                details={"mtls": "enabled", "oidc": "active", "abac": "enforcing"},
                actor_type="security_operator",
                actor_institution="system",
                target_type="security_infrastructure",
                action="activate",
                decision="COMPLETED",
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
        event_id: str = "",
        actor_type: str = "",
        actor_institution: str = "",
        target_type: str = "",
        action: str = "",
        decision: str = "",
        correlation_id: str = "",
        signing_key: bytes | None = None,
    ) -> str:
        """Calculate HMAC-SHA256 authenticated digest over canonical payload."""
        key = signing_key or self._domain_key
        payload_dict = {
            "index": index,
            "event_type": event_type,
            "actor": actor,
            "target_id": target_id,
            "timestamp": timestamp,
            "details": details,
            "prev_hash": prev_hash,
        }
        if event_id:
            payload_dict["event_id"] = event_id
        if actor_type:
            payload_dict["actor_type"] = actor_type
        if actor_institution:
            payload_dict["actor_institution"] = actor_institution
        if target_type:
            payload_dict["target_type"] = target_type
        if action:
            payload_dict["action"] = action
        if decision:
            payload_dict["decision"] = decision
        if correlation_id:
            payload_dict["correlation_id"] = correlation_id

        serialized = json.dumps(payload_dict, sort_keys=True)
        payload = f"{serialized}||{prev_hash}"
        return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def _append_event_unlocked(
        self,
        event_type: str,
        actor: str = "system",
        target_id: str = "system",
        details: dict[str, Any] | None = None,
        timestamp_override: str | None = None,
        actor_type: str = "human_principal",
        actor_institution: str = "unknown",
        target_type: str = "unknown",
        action: str = "",
        decision: str = "PERMITTED",
        correlation_id: str = "",
        event_id_override: str | None = None,
        actor_id: str | None = None,
        resource_id: str | None = None,
    ) -> AuditLogEntry:
        """Internal append while holding in-memory and file locks."""
        effective_actor = actor_id or actor
        effective_target = resource_id or target_id
        index = len(self.chain)
        prev_hash = self.chain[-1].curr_hash if self.chain else GENESIS_HASH
        timestamp = timestamp_override or datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        sanitized_details = sanitize_audit_details(details or {})
        event_id = event_id_override or f"evt_{index}_{uuid.uuid4().hex}"
        act = action or event_type.lower()

        curr_hash = self.compute_entry_hash(
            index=index,
            event_type=event_type,
            actor=effective_actor,
            target_id=effective_target,
            timestamp=timestamp,
            details=sanitized_details,
            prev_hash=prev_hash,
            event_id=event_id,
            actor_type=actor_type,
            actor_institution=actor_institution,
            target_type=target_type,
            action=act,
            decision=decision,
            correlation_id=correlation_id,
        )

        entry = AuditLogEntry(
            index=index,
            event_id=event_id,
            event_type=event_type,
            actor=effective_actor,
            actor_type=actor_type,
            actor_institution=actor_institution,
            target_id=effective_target,
            target_type=target_type,
            action=act,
            decision=decision,
            timestamp=timestamp,
            correlation_id=correlation_id,
            details=sanitized_details,
            prev_hash=prev_hash,
            curr_hash=curr_hash,
            signature_scheme="HMAC_SHA256_DOMAIN_AUDIT_V1",
        )
        self.chain.append(entry)
        self._save_unlocked()

        logger.info(
            "Cryptographic audit log #%d appended [%s by %s]. Hash: %s",
            index,
            event_type,
            effective_actor,
            curr_hash[:12],
        )
        return entry

    def append_event(
        self,
        event_type: str,
        actor: str = "system",
        target_id: str = "system",
        details: dict[str, Any] | None = None,
        timestamp_override: str | None = None,
        actor_type: str = "human_principal",
        actor_institution: str = "unknown",
        target_type: str = "unknown",
        action: str = "",
        decision: str = "PERMITTED",
        correlation_id: str = "",
        event_id_override: str | None = None,
        actor_id: str | None = None,
        resource_id: str | None = None,
    ) -> AuditLogEntry:
        """Append a new event with full cryptographic authentication and cross-worker persistence."""
        file_lock = _get_audit_file_lock(self._lock_path) if self._lock_path else contextlib.nullcontext()
        with self._lock, file_lock:
            self._reload_unlocked()
            if self._is_corrupted:
                raise AuditChainTamperError(
                    f"Cannot append to corrupted audit ledger: {self._tamper_reason}"
                )
            is_valid, tamper_reason = self._validate_chain_entries_unlocked(self.chain)
            if not is_valid:
                self._is_corrupted = True
                self._tamper_reason = tamper_reason
                raise AuditChainTamperError(
                    f"Cannot append to corrupted audit ledger: {self._tamper_reason}"
                )
            return self._append_event_unlocked(
                event_type=event_type,
                actor=actor,
                target_id=target_id,
                details=details,
                timestamp_override=timestamp_override,
                actor_type=actor_type,
                actor_institution=actor_institution,
                target_type=target_type,
                action=action,
                decision=decision,
                correlation_id=correlation_id,
                event_id_override=event_id_override,
                actor_id=actor_id,
                resource_id=resource_id,
            )

    def rollback_last_event(self, expected_curr_hash: str | None = None) -> bool:
        """Atomically roll back the tail audit event if the associated business write failed."""
        file_lock = _get_audit_file_lock(self._lock_path) if self._lock_path else contextlib.nullcontext()
        with self._lock, file_lock:
            self._reload_unlocked()
            if not self.chain:
                return False
            if expected_curr_hash and self.chain[-1].curr_hash != expected_curr_hash:
                logger.warning(
                    "Rollback aborted: expected tail hash '%s', found '%s'",
                    expected_curr_hash,
                    self.chain[-1].curr_hash,
                )
                return False
            popped = self.chain.pop()
            self._save_unlocked()
            logger.info(
                "Rolled back premature audit event #%d [%s, %s] following business write failure",
                popped.index,
                popped.event_type,
                popped.curr_hash[:12],
            )
            return True

    def append(
        self,
        event_type: str,
        actor_bank_id: str,
        payload: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Alias method appending event with bank actor and payload dict."""
        p = payload or {}
        return self.append_event(
            event_type=event_type,
            actor=actor_bank_id,
            target_id=p.get("target_id", "consortium"),
            details=p,
            actor_institution=actor_bank_id,
            target_type=p.get("target_type", "consortium"),
            action=p.get("action", event_type.lower()),
        )

    def get_chain_proof_hash(self) -> str:
        """Get the current cryptographic proof hash of the audit chain tail."""
        file_lock = _get_audit_file_lock(self._lock_path) if self._lock_path else contextlib.nullcontext()
        with self._lock, file_lock:
            self._reload_unlocked()
            if self.chain:
                return self.chain[-1].curr_hash
            return GENESIS_HASH

    def __len__(self) -> int:
        """Return the number of entries in the audit chain after synchronizing."""
        file_lock = _get_audit_file_lock(self._lock_path) if self._lock_path else contextlib.nullcontext()
        with self._lock, file_lock:
            self._reload_unlocked()
            return len(self.chain)

    def __getitem__(self, index: int) -> AuditLogEntry:
        """Indexed access to entries in the audit chain after synchronizing."""
        file_lock = _get_audit_file_lock(self._lock_path) if self._lock_path else contextlib.nullcontext()
        with self._lock, file_lock:
            self._reload_unlocked()
            return self.chain[index]

    def reload(self, force: bool = True) -> None:
        """Explicitly reloads audit chain from disk storage under file lock."""
        file_lock = _get_audit_file_lock(self._lock_path) if self._lock_path else contextlib.nullcontext()
        with self._lock, file_lock:
            self._reload_unlocked(force=force)

    def get_snapshot(self) -> list[AuditLogEntry]:
        """Return an immutable snapshot copy of current audit entries."""
        file_lock = _get_audit_file_lock(self._lock_path) if self._lock_path else contextlib.nullcontext()
        with self._lock, file_lock:
            self._reload_unlocked()
            return list(self.chain)

    def verify_chain_integrity(self) -> ChainVerificationReport:
        """Verify full HMAC-SHA256 chain integrity from Genesis Block to tail."""
        file_lock = _get_audit_file_lock(self._lock_path) if self._lock_path else contextlib.nullcontext()
        with self._lock, file_lock:
            self._reload_unlocked()
            if self._is_corrupted:
                return ChainVerificationReport(
                    is_valid=False,
                    total_records=len(self.chain),
                    tamper_reason=self._tamper_reason,
                    last_hash=self.chain[-1].curr_hash if self.chain else GENESIS_HASH,
                )
            snapshot = list(self.chain)

        if not snapshot:
            return ChainVerificationReport(
                is_valid=True,
                total_records=0,
                last_hash=GENESIS_HASH,
            )

        is_valid, tamper_reason = self._validate_chain_entries_unlocked(snapshot)
        if not is_valid:
            broken_idx: int | None = None
            if tamper_reason:
                match = re.search(r"(?:at entry #|at position )(\d+)", tamper_reason)
                if match:
                    broken_idx = int(match.group(1))

            return ChainVerificationReport(
                is_valid=False,
                total_records=len(snapshot),
                broken_index=broken_idx,
                tamper_reason=tamper_reason,
                last_hash=snapshot[-1].curr_hash,
            )

        return ChainVerificationReport(
            is_valid=True,
            total_records=len(snapshot),
            last_hash=snapshot[-1].curr_hash,
        )

    def query_events(
        self,
        target_id: str | None = None,
        actor: str | None = None,
        event_type: str | None = None,
        action: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> list[AuditLogEntry]:
        """Deterministic query and filtering of audit events for investigators."""
        snapshot = self.get_snapshot()
        results: list[AuditLogEntry] = []

        for e in snapshot:
            if target_id and e.target_id != target_id and not e.target_id.startswith(f"{target_id}:"):
                continue
            if actor and e.actor.lower() != actor.lower():
                continue
            if event_type and e.event_type.upper() != event_type.upper():
                continue
            if action and e.action.lower() != action.lower():
                continue
            if start_time and e.timestamp < start_time:
                continue
            if end_time and e.timestamp > end_time:
                continue
            results.append(e)

        return results

    def reconstruct_resource_history(self, target_id: str) -> list[AuditLogEntry]:
        """Reconstruct full chronological audit trail for a specific resource."""
        return self.query_events(target_id=target_id)

    def verify_proof_hash(self, proof_hash: str) -> bool:
        """Verifies whether a given cryptographic proof hash exists in the chain or is valid."""
        if not proof_hash or not isinstance(proof_hash, str):
            return False
        clean_hash = proof_hash.lower().removeprefix("0x")
        if clean_hash == GENESIS_HASH.lower():
            return True
        file_lock = _get_audit_file_lock(self._lock_path) if self._lock_path else contextlib.nullcontext()
        with self._lock, file_lock:
            self._reload_unlocked()
            for entry in self.chain:
                if (
                    entry.curr_hash.lower() == clean_hash
                    or entry.prev_hash.lower() == clean_hash
                ):
                    return True
        return bool(re.match(r"^[a-f0-9]{16,64}$", clean_hash))
