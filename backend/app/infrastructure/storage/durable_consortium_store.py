"""durable_consortium_store.py

Durable, multi-process synchronized storage for federated consortium governance.

Guarantees:
1. Multi-Process Consistency: Workers synchronize proposals, votes, and membership
   mutations via kernel/file locks and atomic file replacement.
2. Zero Memory Divergence: Changes executed by one worker are immediately observed
   by other workers on subsequent queries via stat freshness validation without restarts.
3. Restart Durability: Consortium creations, policy updates, proposals, votes,
   and member evictions persist across full service and worker restarts.
4. Fail-Closed Integrity: Corrupted, empty, or tampered storage payloads strictly
   fail closed and reject unauthorized or unverified governance state.
5. Protection Against Bootstrap Reversion: Prevents reinitialization of default demo
   members if a consortium was previously established or members were evicted.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import hmac
import json
import logging
import os
import threading
import uuid
from collections.abc import Callable, Generator
from datetime import UTC
from typing import Any, TypeVar

from filelock import FileLock

from app.config import get_settings
from app.domain.consortium_governance import (
    Consortium,
    ConsortiumMember,
    ConsortiumSecurityError,
    ConsortiumStatus,
    MemberRole,
    MembershipProposal,
    ProposalAction,
    ProposalStatus,
)
from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)

T = TypeVar("T")

FORBIDDEN_AUTHORITY_SECRETS = {
    "",
    "secret",
    "change_me",
    "default",
    "password",
    "cfi_dev_consortium_store_authority_secret_2026_default",
    "cfi_dev_identity_store_authority_secret_2026_default",
}

_CONSORTIUM_FILE_LOCKS: dict[str, FileLock] = {}
_CONSORTIUM_LOCKS_GUARD = threading.Lock()


def _get_consortium_file_lock(store_path: str) -> FileLock:
    """Return shared FileLock instance for store path to ensure process-local reentrancy."""
    abs_lock_path = os.path.abspath(store_path + ".lock")
    with _CONSORTIUM_LOCKS_GUARD:
        if abs_lock_path not in _CONSORTIUM_FILE_LOCKS:
            _CONSORTIUM_FILE_LOCKS[abs_lock_path] = FileLock(abs_lock_path, timeout=15)
        return _CONSORTIUM_FILE_LOCKS[abs_lock_path]


class DurableConsortiumStore:
    """Authoritative, durable storage backend for multi-bank consortium governance."""

    _instance: DurableConsortiumStore | None = None

    def __init__(
        self,
        store_path: str | None = None,
        in_memory: bool = False,
        authority_secret: str | None = None,
        app_env: str | None = None,
    ) -> None:
        settings = get_settings()
        raw_env = (
            app_env
            or os.environ.get("APP_ENV")
            or os.environ.get("ENVIRONMENT")
            or getattr(settings, "app_env", "")
            or ""
        )
        self._app_env = str(raw_env).lower()
        is_prod = self._is_prod

        if is_prod and in_memory:
            raise ConsortiumSecurityError(
                "In-memory consortium store is strictly forbidden in production mode: durable persistence is required."
            )

        self._in_memory = in_memory
        if self._in_memory:
            self._store_path = ""
        else:
            configured_path = (
                store_path
                or os.environ.get("CFI_CONSORTIUM_STORE_PATH")
                or getattr(settings, "consortium_store_path", None)
            )
            if not configured_path:
                configured_path = os.path.join(get_storage_dir(), "consortium_governance_store.json")
            self._store_path = os.path.abspath(configured_path)
            os.makedirs(os.path.dirname(self._store_path), exist_ok=True)

        # Authenticated integrity secret
        self._authority_secret = (
            authority_secret
            or os.environ.get("CFI_CONSORTIUM_STORE_SECRET")
            or os.environ.get("CFI_CONSORTIUM_STORE_HMAC_SECRET")
            or os.environ.get("CFI_IDENTITY_STORE_SECRET")
            or getattr(settings, "consortium_store_secret", None)
            or (None if is_prod else "cfi_dev_consortium_store_authority_secret_2026_default")
        )

        if is_prod and (
            not self._authority_secret
            or self._authority_secret in FORBIDDEN_AUTHORITY_SECRETS
            or "change_me" in self._authority_secret
        ):
            raise ConsortiumSecurityError(
                "Production consortium store requires independently configured authority secret "
                "(CFI_CONSORTIUM_STORE_SECRET) and rejects empty, default, or placeholder secrets."
            )

        if self._authority_secret:
            self._hmac_key: bytes | None = hashlib.sha256(
                b"CFI-CONSORTIUM-STORE-HMAC-KEY-V1:" + self._authority_secret.encode("utf-8")
            ).digest()
        else:
            self._hmac_key = None

        self._lock = threading.RLock()
        self._consortia: dict[str, Consortium] = {}
        self._proposals: dict[str, MembershipProposal] = {}

        self._last_mtime_ns: int = 0
        self._last_size: int = -1
        self._is_corrupted: bool = False
        self._is_established: bool = False

        if not self._in_memory:
            self.reload()

    @classmethod
    def get_instance(cls) -> DurableConsortiumStore:
        """Retrieve or construct global singleton store instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def set_instance(cls, store: DurableConsortiumStore | None) -> None:
        """Explicitly override global store instance (primarily for testing isolation)."""
        cls._instance = store

    @property
    def _is_prod(self) -> bool:
        """Whether this store operates in production mode."""
        active_env = str(
            self._app_env
            or os.environ.get("APP_ENV")
            or os.environ.get("ENVIRONMENT")
            or getattr(get_settings(), "app_env", "")
            or ""
        ).lower()
        return active_env == "production"

    @property
    def store_path(self) -> str:
        """Return absolute path to store file."""
        return self._store_path

    @property
    def is_established(self) -> bool:
        """Return True if store file exists on disk and has been loaded."""
        return self._is_established

    @contextlib.contextmanager
    def _file_lock_context(self) -> Generator[None, None, None]:
        """Acquire inter-process file lock if operating against durable filesystem backend."""
        if self._in_memory or not self._store_path:
            yield
            return
        lock = _get_consortium_file_lock(self._store_path)
        with lock:
            yield

    def _serialize_consortium(self, c: Consortium) -> dict[str, Any]:
        """Serialize Consortium domain model to canonical dictionary representation."""
        members_dict = {}
        for b_id, m in c.members.items():
            members_dict[b_id] = {
                "bank_id": m.bank_id,
                "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                "voting_power": float(m.voting_power),
                "joined_at": m.joined_at.isoformat() if hasattr(m.joined_at, "isoformat") else str(m.joined_at),
            }

        return {
            "consortium_id": c.consortium_id,
            "name": c.name,
            "quorum_ratio": float(c.quorum_ratio),
            "min_members_n": c.min_members_n,
            "max_epsilon": float(c.max_epsilon),
            "status": c.status.value if hasattr(c.status, "value") else str(c.status),
            "members": members_dict,
            "removed_members": c.removed_members,
            "created_at": c.created_at.isoformat() if hasattr(c.created_at, "isoformat") else str(c.created_at),
        }

    def _deserialize_consortium(self, d: dict[str, Any]) -> Consortium:
        """Deserialize dictionary representation to Consortium domain model."""
        members = {}
        for b_id, m_data in d.get("members", {}).items():
            members[b_id] = ConsortiumMember(
                bank_id=m_data["bank_id"],
                role=MemberRole(m_data["role"]),
                voting_power=float(m_data["voting_power"]),
                joined_at=m_data.get("joined_at", datetime.datetime.now(UTC)),
            )

        c = Consortium(
            consortium_id=d["consortium_id"],
            name=d["name"],
            quorum_ratio=float(d.get("quorum_ratio", 0.51)),
            min_members_n=int(d.get("min_members_n", 2)),
            max_epsilon=float(d.get("max_epsilon", 5.0)),
            status=ConsortiumStatus(d.get("status", ConsortiumStatus.ACTIVE)),
            members=members,
            removed_members=d.get("removed_members", {}),
            created_at=d.get("created_at", datetime.datetime.now(UTC)),
        )
        return c

    def _serialize_proposal(self, p: MembershipProposal) -> dict[str, Any]:
        """Serialize MembershipProposal domain model to canonical dictionary representation."""
        return {
            "proposal_id": p.proposal_id,
            "consortium_id": p.consortium_id,
            "creator_bank_id": p.creator_bank_id,
            "target_bank_id": p.target_bank_id,
            "action": p.action.value if hasattr(p.action, "value") else str(p.action),
            "required_quorum_ratio": float(p.required_quorum_ratio),
            "votes_for": sorted(list(p.votes_for)),
            "votes_against": sorted(list(p.votes_against)),
            "status": p.status.value if hasattr(p.status, "value") else str(p.status),
            "created_at": p.created_at.isoformat() if hasattr(p.created_at, "isoformat") else str(p.created_at),
            "ttl_seconds": p.ttl_seconds,
            "expires_at": p.expires_at.isoformat() if p.expires_at else None,
            "cancelled_at": p.cancelled_at.isoformat() if p.cancelled_at else None,
            "rejection_reason": p.rejection_reason,
            "metadata": p.metadata,
        }

    def _deserialize_proposal(self, d: dict[str, Any]) -> MembershipProposal:
        """Deserialize dictionary representation to MembershipProposal domain model."""
        p = MembershipProposal(
            proposal_id=d["proposal_id"],
            consortium_id=d["consortium_id"],
            creator_bank_id=d["creator_bank_id"],
            target_bank_id=d["target_bank_id"],
            action=ProposalAction(d["action"]),
            required_quorum_ratio=float(d.get("required_quorum_ratio", 0.51)),
            votes_for=set(d.get("votes_for", [])),
            votes_against=set(d.get("votes_against", [])),
            status=ProposalStatus(d.get("status", ProposalStatus.PENDING)),
            created_at=d.get("created_at", datetime.datetime.now(UTC)),
            ttl_seconds=int(d.get("ttl_seconds", 86400)),
            expires_at=d.get("expires_at"),
            cancelled_at=d.get("cancelled_at"),
            rejection_reason=d.get("rejection_reason"),
            metadata=d.get("metadata", {}),
        )
        return p

    def _compute_digest(self, data: dict[str, Any]) -> str:
        """Compute authoritative SHA-256 checksum over canonical consortium governance payload."""
        canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest()

    def _reload_unlocked(self, force: bool = False) -> None:
        """Internal reload without acquiring locks (must be called with locks held)."""
        if self._in_memory or not self._store_path:
            return

        if not os.path.exists(self._store_path):
            marker = self._store_path + ".established"
            if self._is_prod and (os.path.exists(marker) or self._is_established or self._last_mtime_ns > 0 or len(self._consortia) > 0):
                self._is_corrupted = True
                self._consortia.clear()
                self._proposals.clear()
                self._last_mtime_ns = 0
                self._last_size = -1
                raise ConsortiumSecurityError(
                    f"Authoritative consortium store at '{self._store_path}' disappeared: "
                    f"persistence file is missing or inaccessible."
                )
            self._last_mtime_ns = 0
            self._last_size = -1
            self._is_corrupted = False
            return

        try:
            stat = os.stat(self._store_path)
            if not force and stat.st_mtime_ns == self._last_mtime_ns and stat.st_size == self._last_size:
                return

            if stat.st_size == 0:
                self._is_corrupted = True
                raise ConsortiumSecurityError(
                    f"Durable consortium store at '{self._store_path}' is corrupted: file is empty (0 bytes)."
                )

            with open(self._store_path, "rb") as f:
                raw_bytes = f.read()

            try:
                container = json.loads(raw_bytes.decode("utf-8"))
            except Exception as parse_err:
                self._is_corrupted = True
                raise ConsortiumSecurityError(
                    f"Durable consortium store at '{self._store_path}' is corrupted: invalid JSON ({parse_err})."
                ) from parse_err

            if not isinstance(container, dict):
                self._is_corrupted = True
                raise ConsortiumSecurityError(
                    f"Durable consortium store at '{self._store_path}' is malformed: root JSON structure must be an object."
                )

            data = container.get("data")
            if not isinstance(data, dict):
                self._is_corrupted = True
                raise ConsortiumSecurityError(
                    f"Durable consortium store at '{self._store_path}' is malformed: 'data' object missing."
                )

            raw_consortia = data.get("consortia")
            raw_proposals = data.get("proposals")
            if not isinstance(raw_consortia, dict) or not isinstance(raw_proposals, dict):
                self._is_corrupted = True
                raise ConsortiumSecurityError(
                    f"Durable consortium store at '{self._store_path}' is malformed: consortia or proposals schema invalid."
                )

            canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
            computed_sha = hashlib.sha256(canonical_bytes).hexdigest()

            # Layer 1: Check SHA-256 digest
            stored_sha = container.get("integrity_sha256") or container.get("sha256")
            if self._is_prod and not stored_sha:
                self._is_corrupted = True
                raise ConsortiumSecurityError(
                    f"Durable consortium store at '{self._store_path}' rejected: missing required integrity digest in production."
                )
            if stored_sha and not hmac.compare_digest(stored_sha.lower(), computed_sha.lower()):
                self._is_corrupted = True
                raise ConsortiumSecurityError(
                    f"Durable consortium store at '{self._store_path}' checksum verification failed: "
                    f"expected {computed_sha}, got {stored_sha}."
                )

            # Layer 2: Cryptographic Authenticated Integrity Verification (HMAC-SHA256)
            stored_hmac = container.get("integrity_hmac_sha256")
            if self._is_prod:
                if not stored_hmac:
                    self._is_corrupted = True
                    raise ConsortiumSecurityError(
                        f"Durable consortium store at '{self._store_path}' rejected: unauthenticated persistence format "
                        f"lacking required 'integrity_hmac_sha256' cryptographic authentication in production."
                    )
                if not self._hmac_key:
                    self._is_corrupted = True
                    raise ConsortiumSecurityError(
                        f"Durable consortium store at '{self._store_path}' cannot authenticate persistence: missing authority HMAC key."
                    )
                actual_hmac = hmac.new(self._hmac_key, canonical_bytes, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(stored_hmac.lower(), actual_hmac.lower()):
                    self._is_corrupted = True
                    raise ConsortiumSecurityError(
                        f"Durable consortium store at '{self._store_path}' failed cryptographic HMAC authentication: "
                        f"authentication tag mismatch."
                    )
            elif self._hmac_key and stored_hmac:
                actual_hmac = hmac.new(self._hmac_key, canonical_bytes, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(stored_hmac.lower(), actual_hmac.lower()):
                    self._is_corrupted = True
                    raise ConsortiumSecurityError(
                        f"Durable consortium store at '{self._store_path}' failed cryptographic HMAC authentication: tag mismatch."
                    )

            # Reconstruct domain models in place where existing, preserving object references
            new_consortia: dict[str, Consortium] = {}
            for cid_raw, c_dict in raw_consortia.items():
                cid = cid_raw.lower().strip()
                deserialized_c = self._deserialize_consortium(c_dict)
                if cid in self._consortia:
                    c = self._consortia[cid]
                    c.name = deserialized_c.name
                    c.quorum_ratio = deserialized_c.quorum_ratio
                    c.min_members_n = deserialized_c.min_members_n
                    c.max_epsilon = deserialized_c.max_epsilon
                    c.status = deserialized_c.status
                    c.members.clear()
                    c.members.update(deserialized_c.members)
                    c.removed_members.clear()
                    c.removed_members.update(deserialized_c.removed_members)
                    c.created_at = deserialized_c.created_at
                    new_consortia[cid] = c
                else:
                    new_consortia[cid] = deserialized_c

            new_proposals: dict[str, MembershipProposal] = {}
            for pid_raw, p_dict in raw_proposals.items():
                pid = pid_raw.strip()
                deserialized_p = self._deserialize_proposal(p_dict)
                if pid in self._proposals:
                    p = self._proposals[pid]
                    p.consortium_id = deserialized_p.consortium_id
                    p.creator_bank_id = deserialized_p.creator_bank_id
                    p.target_bank_id = deserialized_p.target_bank_id
                    p.action = deserialized_p.action
                    p.required_quorum_ratio = deserialized_p.required_quorum_ratio
                    p.votes_for.clear()
                    p.votes_for.update(deserialized_p.votes_for)
                    p.votes_against.clear()
                    p.votes_against.update(deserialized_p.votes_against)
                    p.status = deserialized_p.status
                    p.created_at = deserialized_p.created_at
                    p.ttl_seconds = deserialized_p.ttl_seconds
                    p.expires_at = deserialized_p.expires_at
                    p.cancelled_at = deserialized_p.cancelled_at
                    p.rejection_reason = deserialized_p.rejection_reason
                    p.metadata.clear()
                    p.metadata.update(deserialized_p.metadata)
                    new_proposals[pid] = p
                else:
                    new_proposals[pid] = deserialized_p

            self._consortia = new_consortia
            self._proposals = new_proposals
            self._last_mtime_ns = stat.st_mtime_ns
            self._last_size = stat.st_size
            self._is_corrupted = False
            self._is_established = True

            marker = self._store_path + ".established"
            if not os.path.exists(marker):
                with open(marker, "w", encoding="utf-8") as mf:
                    mf.write("established\n")

        except ConsortiumSecurityError:
            raise
        except Exception as e:
            self._is_corrupted = True
            raise ConsortiumSecurityError(
                f"Failed to reload durable consortium store at '{self._store_path}': {e}"
            ) from e

    def _save_unlocked(self) -> None:
        """Internal atomic save without acquiring locks (must be called with locks held)."""
        if self._in_memory or not self._store_path:
            return

        serialized_consortia = {
            cid: self._serialize_consortium(c)
            for cid, c in self._consortia.items()
        }
        serialized_proposals = {
            pid: self._serialize_proposal(p)
            for pid, p in self._proposals.items()
        }

        data = {
            "consortia": serialized_consortia,
            "proposals": serialized_proposals,
        }

        canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        container: dict[str, Any] = {
            "version": 1,
            "data": data,
            "sha256": digest,
            "integrity_sha256": digest,
        }
        if self._hmac_key:
            container["integrity_hmac_sha256"] = hmac.new(
                self._hmac_key, canonical_bytes, hashlib.sha256
            ).hexdigest()

        raw_bytes = json.dumps(container, indent=2, sort_keys=True).encode("utf-8")
        tmp_path = self._store_path + f".tmp.{uuid.uuid4().hex}"
        try:
            with open(tmp_path, "wb") as f:
                f.write(raw_bytes)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, self._store_path)
            stat = os.stat(self._store_path)
            self._last_mtime_ns = stat.st_mtime_ns
            self._last_size = stat.st_size
            self._is_established = True
            self._is_corrupted = False

            marker = self._store_path + ".established"
            if not os.path.exists(marker):
                with open(marker, "w", encoding="utf-8") as mf:
                    mf.write("established\n")
        finally:
            if os.path.exists(tmp_path):
                with contextlib.suppress(Exception):
                    os.remove(tmp_path)

    def reload(self, force: bool = False) -> None:
        """Public thread-safe and process-locked reload."""
        with self._lock, self._file_lock_context():
            self._reload_unlocked(force=force)

    def atomic_mutate(
        self,
        mutation_fn: Callable[[dict[str, Consortium], dict[str, MembershipProposal]], T],
    ) -> T:
        """Execute a read-modify-write mutation atomically across processes and threads."""
        with self._lock, self._file_lock_context():
            if self._is_corrupted:
                raise ConsortiumSecurityError("Cannot mutate corrupted consortium store.")
            self._reload_unlocked(force=False)
            result = mutation_fn(self._consortia, self._proposals)
            self._save_unlocked()
            return result

    def get_consortium(self, consortium_id: str) -> Consortium | None:
        """Retrieve a consortium by ID after validating freshness."""
        with self._lock, self._file_lock_context():
            self._reload_unlocked()
            return self._consortia.get(consortium_id.lower().strip())

    def list_consortia(self) -> list[Consortium]:
        """List all consortia after validating freshness."""
        with self._lock, self._file_lock_context():
            self._reload_unlocked()
            return list(self._consortia.values())

    def get_proposal(self, proposal_id: str) -> MembershipProposal | None:
        """Retrieve a proposal by ID after validating freshness."""
        with self._lock, self._file_lock_context():
            self._reload_unlocked()
            return self._proposals.get(proposal_id.strip())

    def list_proposals(
        self,
        consortium_id: str | None = None,
        status: ProposalStatus | None = None,
    ) -> list[MembershipProposal]:
        """List proposals, optionally filtered by consortium and status."""
        with self._lock, self._file_lock_context():
            self._reload_unlocked()
            results: list[MembershipProposal] = []
            clean_cid = consortium_id.lower().strip() if consortium_id else None
            for p in self._proposals.values():
                if clean_cid and p.consortium_id != clean_cid:
                    continue
                if status is not None and p.status != status:
                    continue
                results.append(p)
            return sorted(results, key=lambda x: x.created_at, reverse=True)

    def clear(self) -> None:
        """Clear store contents (non-production only)."""
        if self._is_prod:
            raise ConsortiumSecurityError("Clear operation is strictly prohibited in production mode.")
        with self._lock, self._file_lock_context():
            self._consortia.clear()
            self._proposals.clear()
            if not self._in_memory and self._store_path:
                if os.path.exists(self._store_path):
                    with contextlib.suppress(Exception):
                        os.remove(self._store_path)
                marker = self._store_path + ".established"
                if os.path.exists(marker):
                    with contextlib.suppress(Exception):
                        os.remove(marker)
            self._last_mtime_ns = 0
            self._last_size = -1
            self._is_established = False
            self._is_corrupted = False
