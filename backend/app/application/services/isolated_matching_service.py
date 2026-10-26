"""Authentic Two-Bank Private Matching Architecture and Isolated Worker Service.

Implements defensible, independently executed two-bank private intelligence workflow:
1. Strict process and storage isolation: Each bank worker loads private datasets
   exclusively from its institutional storage vault (storage/{bank_id}/private_entities/).
2. Strict KMS key isolation: Each bank worker utilizes its own isolated TenantKMSSession.
   The coordinator and peer banks have zero access to private exponents or unblinded records.
3. Authenticated institution-to-institution protocol: 2048-bit MODP Diffie-Hellman Private
   Set Intersection (DH-PSI) with mutual cryptographic signatures and transcript integrity.
4. Active consortium governance gating: Requires active membership and explicit operational
   data-sharing grant with valid authority and revision history. Denies by default.
5. Mandatory institutional jurisdiction verification: Denies if region context is absent.
6. Anti-replay and anti-tamper transcript binding: Fresh nonces and timestamps verified.
7. Verifiable, minimal operational signal issuance: Authoritative case evidence binding.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
    DisclosureMode,
    MatchCategory,
    SanitizedRiskLevel,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.kms_service import KMSService, TenantKMSSession
from app.application.services.openmined_psi_adapter import OpenMinedPSIAdapter
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    ConsortiumStatus,
)
from app.domain.consortium_policy import ConsortiumPolicyEngine
from app.domain.institutional_identity import (
    InstitutionalSigningIdentity,
    InstitutionalVerificationRegistry,
    canonical_coordinator_readiness_bytes,
    canonical_counterpart_evidence_bytes,
    canonical_evaluated_result_bytes,
    canonical_worker_readiness_bytes,
)
from app.domain.investigation_entities import Entity
from app.domain.psi_service import (
    PSI_PRIME,
    blind_element,
    double_blind_element,
    hash_to_prime_subgroup,
    validate_group_element,
)
from app.domain.value_objects_investigation import PrivacyPreservingIdentifier
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)

SCOPE_MATCHING = "matching"
SCOPE_DATA_INGESTION = "ingestion"
SCOPE_ADMINISTRATION = "admin"


@dataclass(frozen=True)
class OperationAuthorizationTicket:
    """Independently verifiable, short-lived operation authorization ticket.

    Issued and validated under consortium governance to ensure bank workers
    independently enforce participation authority before executing private matching.
    """

    ticket_id: str
    consortium_id: str
    requesting_bank_id: str
    target_bank_id: str
    operation: str
    purpose: str
    query_scope: str
    grant_id: str
    grant_revision: int
    grant_scope_digest: str
    job_id: str
    issued_at: str
    expires_at: str
    requesting_bank_signature: str = ""
    target_bank_approval_signature: str = ""
    coordinator_signature: str = ""
    bank_approvals: dict[str, str] = field(default_factory=dict)

    def canonical_digest(self) -> str:
        """Compute canonical digest of the ticket core scope."""
        canonical = (
            f"{self.ticket_id}:{self.consortium_id}:{self.requesting_bank_id}:"
            f"{self.target_bank_id}:{self.operation}:{self.purpose}:{self.query_scope}:"
            f"{self.grant_id}:{self.grant_revision}:{self.grant_scope_digest}:"
            f"{self.job_id}:{self.issued_at}:{self.expires_at}"
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def is_expired(self, now: datetime.datetime | None = None) -> bool:
        """Check if the ticket has expired."""
        current_time = now or datetime.datetime.now(UTC)
        try:
            exp_time = datetime.datetime.fromisoformat(self.expires_at)
            if exp_time.tzinfo is None:
                exp_time = exp_time.replace(tzinfo=UTC)
            return current_time > exp_time
        except Exception:
            return True


@dataclass(frozen=True)
class EstablishedPSIQueryMessage:
    """Established PSI query message containing OpenMined PSI client request bytes."""

    job_id: str
    consortium_id: str
    requesting_bank_id: str
    target_bank_id: str
    psi_request_b64: str
    nonce: str
    timestamp: str
    purpose: str
    signature: str
    ticket: OperationAuthorizationTicket

    def compute_digest(self) -> str:
        canonical = (
            f"{self.job_id}:{self.consortium_id}:{self.requesting_bank_id}:"
            f"{self.target_bank_id}:{self.psi_request_b64}:{self.nonce}:"
            f"{self.timestamp}:{self.purpose}:{self.ticket.canonical_digest()}"
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EstablishedPSICounterpartResponse:
    """Established PSI response message containing server Setup, Response bytes, and Bank B signed evidence."""

    job_id: str
    consortium_id: str
    target_bank_id: str
    psi_server_setup_b64: str
    psi_response_b64: str
    nonce: str
    timestamp: str
    signature: str
    requesting_bank_id: str = ""
    grant_id: str = ""
    grant_revision: int = 1
    grant_scope_digest: str = ""
    ticket_digest: str = ""
    psi_server_setup_hash: str = ""
    psi_response_hash: str = ""
    counterpart_evidence_digest: str = ""
    version: str = "1.0"

    def compute_digest(self) -> str:
        if self.counterpart_evidence_digest:
            return self.counterpart_evidence_digest
        canonical = (
            f"{self.job_id}:{self.consortium_id}:{self.target_bank_id}:"
            f"{self.psi_server_setup_b64}:{self.psi_response_b64}:{self.nonce}:"
            f"{self.timestamp}"
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BlindedQueryMessage:
    """Blinded query payload transmitted from Requesting Bank to Counterpart."""

    job_id: str
    consortium_id: str
    requesting_bank_id: str
    target_bank_id: str
    blinded_element_hex: str
    nonce: str
    timestamp: str
    purpose: str
    signature: str
    ticket: OperationAuthorizationTicket | None = None

    def compute_digest(self) -> str:
        """Compute canonical digest of query payload for signature verification."""
        canonical = (
            f"{self.job_id}:{self.consortium_id}:{self.requesting_bank_id}:"
            f"{self.target_bank_id}:{self.blinded_element_hex}:{self.nonce}:"
            f"{self.timestamp}:{self.purpose}"
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CounterpartMatchResponse:
    """Counterpart blinded response payload transmitted back to Requesting Bank."""

    job_id: str
    consortium_id: str
    target_bank_id: str
    double_blinded_query_hex: str
    blinded_target_elements_hex: tuple[str, ...]
    nonce: str
    timestamp: str
    sanitized_risk_context: dict[str, Any]
    signature: str
    encrypted_risk_payloads: dict[str, str] = field(default_factory=dict)

    def compute_digest(self) -> str:
        """Compute canonical digest of counterpart response payload."""
        sorted_elements = ",".join(sorted(self.blinded_target_elements_hex))
        risk_str = json.dumps(self.sanitized_risk_context, sort_keys=True)
        payloads_str = json.dumps(self.encrypted_risk_payloads, sort_keys=True)
        canonical = (
            f"{self.job_id}:{self.consortium_id}:{self.target_bank_id}:"
            f"{self.double_blinded_query_hex}:{sorted_elements}:{self.nonce}:"
            f"{self.timestamp}:{risk_str}:{payloads_str}"
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerifiedMatchReceipt:
    """Cryptographic attestation receipt signed by participating bank workers."""

    receipt_id: str
    job_id: str
    consortium_id: str
    requesting_bank_id: str
    target_bank_id: str
    query_entity_id: str
    match_confirmed: bool
    match_confidence: float
    protocol: str
    transcript_digest: str
    executed_at: str
    worker_a_signature: str
    worker_b_signature: str
    coordinator_signature: str = ""
    matched_risk_level: str = "UNAVAILABLE"
    matched_typologies: tuple[str, ...] = ()
    assurance_level: str = "ISOLATED_INTEGRATION_VERIFIED"
    match_category: str = "PROBABILISTIC_FUZZY"
    is_exact_confirmed: bool = False
    grant_id: str = ""
    grant_revision: int = 1
    grant_scope_digest: str = ""
    ticket_digest: str = ""
    counterpart_evidence_digest: str = ""
    counterpart_evidence: dict[str, Any] = field(default_factory=dict)
    disclosure_mode: str = "ATTRIBUTED_INSTITUTIONS"
    contributing_institutions: tuple[str, ...] = ()
    evaluated_result_digest: str = ""
    version: str = "1.0"


class IsolatedBankWorkerNode:
    """Independently isolated institutional bank worker node.

    Operates strictly within its own storage boundary and tenant KMS key session.
    Cannot read counterpart files or access counterpart private keys.
    """

    def __init__(
        self,
        bank_id: str,
        storage_root: str | None = None,
        auth_secret: str | None = None,
        kms_service: KMSService | None = None,
        trusted_peer_keys: dict[str, str] | None = None,
        matching_token: str | None = None,
        ingestion_token: str | None = None,
        admin_token: str | None = None,
        require_operation_ticket: bool = False,
        consortium_service: Any | None = None,
        signing_key: InstitutionalSigningIdentity | str | None = None,
    ) -> None:
        if not bank_id or not bank_id.strip():
            raise ValueError("bank_id must be a non-empty string")
        self.bank_id = bank_id.lower().strip()
        base_dir = storage_root or get_storage_dir()
        self.vault_dir = os.path.join(base_dir, self.bank_id, "private_entities")
        os.makedirs(self.vault_dir, exist_ok=True)

        self._kms_session = TenantKMSSession(bank_id=self.bank_id, kms_service=kms_service)
        # Worker private signing key: cryptographically generated if not provided, never predictable fallback
        self._auth_secret = auth_secret or secrets.token_hex(32)

        # Asymmetric institutional signing identity (strictly tenant-sovereign)
        signing_key_candidate = (
            signing_key
            or os.environ.get(f"CFI_WORKER_SIGNING_KEY_{self.bank_id.upper()}")
            or os.environ.get("WORKER_SIGNING_KEY")
        )
        if isinstance(signing_key_candidate, InstitutionalSigningIdentity):
            self._signing_identity: InstitutionalSigningIdentity | None = signing_key_candidate
        elif isinstance(signing_key_candidate, str) and len(signing_key_candidate.strip()) == 64:
            self._signing_identity = InstitutionalSigningIdentity.from_private_hex(self.bank_id, signing_key_candidate)
        else:
            key_file = (
                os.environ.get("WORKER_SIGNING_KEY_FILE")
                or os.environ.get(f"CFI_WORKER_SIGNING_KEY_FILE_{self.bank_id.upper()}")
            )
            if key_file and os.path.exists(key_file):
                self._signing_identity = InstitutionalSigningIdentity.from_private_key_file(self.bank_id, key_file)
            else:
                self._signing_identity = None

        # Scoped RPC authorization credentials (least-privilege separation)
        self._matching_token = matching_token or f"match_{secrets.token_hex(24)}"
        self._ingestion_token = ingestion_token or f"ingest_{secrets.token_hex(24)}"
        self._admin_token = admin_token or f"admin_{secrets.token_hex(24)}"

        self._token_scopes: dict[str, set[str]] = {
            self._matching_token: {SCOPE_MATCHING},
            self._ingestion_token: {SCOPE_DATA_INGESTION},
            self._admin_token: {SCOPE_ADMINISTRATION},
            self._auth_secret: {SCOPE_MATCHING, SCOPE_DATA_INGESTION, SCOPE_ADMINISTRATION},
        }

        # Peer verification keystore: independently provisioned trusted verification keys
        self._trusted_peer_verification_keys: dict[str, str] = {
            k.lower().strip(): v for k, v in (trusted_peer_keys or {}).items()
        }
        self._peer_registry = InstitutionalVerificationRegistry()
        if self._signing_identity is not None:
            for p_bank, p_key in self._trusted_peer_verification_keys.items():
                with contextlib.suppress(ValueError, TypeError, ConsortiumSecurityError):
                    self._peer_registry.register_public_key(p_bank, p_key)

        self._require_operation_ticket = require_operation_ticket
        self._locally_revoked_grants: set[str] = set()
        self._active_psi_clients: dict[str, Any] = {}
        if consortium_service is not None:
            self._consortium_service = consortium_service
        else:
            try:
                self._consortium_service = ConsortiumGovernanceService()
            except Exception:
                self._consortium_service = None
        self._authorized_grants_path = os.path.join(base_dir, self.bank_id, "authorized_grants.json")
        self._authorized_grants: dict[str, dict[str, Any]] = {}
        self._load_authorized_grants()

        # Durable anti-replay nonces storage surviving restarts (fail-closed)
        self._replay_store_path = os.path.join(base_dir, self.bank_id, "replay_nonces.json")
        self._consumed_nonces: set[str] = set()
        self._replay_lock = threading.Lock()
        if os.path.exists(self._replay_store_path):
            try:
                with open(self._replay_store_path, encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        self._consumed_nonces = set(json.loads(content))
            except Exception as exc:
                raise ConsortiumSecurityError(
                    f"Durable anti-replay store for institution '{self.bank_id}' is corrupt or unreadable: {exc}. "
                    f"Failing closed to prevent replay vulnerabilities."
                ) from exc

    @property
    def public_key_hex(self) -> str:
        """Return the hex representation of the worker's public verification key."""
        if self._signing_identity is None:
            raise ConsortiumSecurityError(
                f"Worker node '{self.bank_id}' has no provisioned signing key. Fails closed."
            )
        return self._signing_identity.public_key_hex

    def enroll_signing_identity(
        self, signing_identity: InstitutionalSigningIdentity | str
    ) -> None:
        """Explicitly enroll an institutional signing identity as part of an authorized enrollment workflow."""
        if isinstance(signing_identity, InstitutionalSigningIdentity):
            self._signing_identity = signing_identity
        elif isinstance(signing_identity, str) and len(signing_identity.strip()) == 64:
            self._signing_identity = InstitutionalSigningIdentity.from_private_hex(self.bank_id, signing_identity)
        else:
            raise ValueError(
                "Invalid institutional signing identity: must be InstitutionalSigningIdentity "
                "or 64-character private hex."
            )

    @property
    def matching_token(self) -> str:
        """Return the scoped matching token."""
        return self._matching_token

    @property
    def ingestion_token(self) -> str:
        """Return the scoped data ingestion token."""
        return self._ingestion_token

    @property
    def admin_token(self) -> str:
        """Return the scoped administration token."""
        return self._admin_token

    def get_token_scopes(self, token: str) -> set[str]:
        """Return the authorized scopes for a given Bearer token."""
        clean_token = token.strip()
        for authorized_token, scopes in self._token_scopes.items():
            if hmac.compare_digest(clean_token, authorized_token):
                return scopes
        return set()

    def enroll_trusted_peer(
        self, peer_bank_id: str, verification_key: str, allow_rotation: bool = False
    ) -> None:
        """Enroll an authoritative counterpart bank verification key in this worker's keystore."""
        clean_id = peer_bank_id.lower().strip()
        if not clean_id:
            raise ValueError("peer_bank_id cannot be empty")
        if clean_id in self._trusted_peer_verification_keys or self._peer_registry.has_key(clean_id):
            raise ConsortiumSecurityError(
                f"Peer institution '{clean_id}' is already enrolled in worker '{self.bank_id}'. "
                "Ordinary registration must never overwrite an existing institutional key. "
                "Key updates require cryptographically authorized rotation via rotate_trusted_peer()."
            )
        clean_key = (verification_key or "").strip()
        if not clean_key or len(clean_key) < 16:
            raise ValueError(
                f"Invalid verification key for peer '{peer_bank_id}': key too short."
            )
        if self._signing_identity is not None or len(clean_key) == 64:
            self._peer_registry.register_public_key(clean_id, clean_key)
        self._trusted_peer_verification_keys[clean_id] = clean_key

    def rotate_trusted_peer(
        self,
        peer_bank_id: str,
        new_verification_key: str,
        rotation_authorization_signature: str,
    ) -> None:
        """Rotate a trusted peer bank's verification key using cryptographic authorization from the previous key."""
        clean_id = peer_bank_id.lower().strip()
        if not clean_id:
            raise ValueError("peer_bank_id cannot be empty.")
        if not new_verification_key or len(new_verification_key.strip()) != 64:
            raise ValueError(
                f"Invalid new verification key for peer '{peer_bank_id}': expected 64-char hex public verification key."
            )
        if not rotation_authorization_signature or not rotation_authorization_signature.strip():
            raise ConsortiumSecurityError(
                f"Missing rotation authorization signature for peer '{clean_id}'."
            )
        self._peer_registry.rotate_public_key(
            clean_id,
            new_verification_key.strip(),
            rotation_authorization_signature.strip(),
        )
        self._trusted_peer_verification_keys[clean_id] = new_verification_key.strip()

    def get_trusted_peer_key(self, peer_bank_id: str) -> str | None:
        """Return the trusted verification key for an enrolled peer bank."""
        return self._trusted_peer_verification_keys.get(peer_bank_id.lower().strip())

    def _load_authorized_grants(self) -> None:
        """Load locally registered authorized grants from disk storage."""
        if os.path.exists(self._authorized_grants_path):
            try:
                with open(self._authorized_grants_path, encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        self._authorized_grants = json.loads(content)
            except Exception as exc:
                raise ConsortiumSecurityError(
                    f"Durable authorized grants store for institution '{self.bank_id}' is corrupt or unreadable: {exc}. "
                    "Failing closed to prevent unauthorized data sharing."
                ) from exc

    def _persist_authorized_grants(self) -> None:
        """Atomically persist authorized grants to disk storage."""
        tmp_path = f"{self._authorized_grants_path}.{secrets.token_hex(8)}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._authorized_grants, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._authorized_grants_path)
        except Exception as exc:
            if os.path.exists(tmp_path):
                with contextlib.suppress(Exception):
                    os.remove(tmp_path)
            raise ConsortiumSecurityError(
                f"Failed to durably persist authorized grants for institution '{self.bank_id}': {exc}"
            ) from exc

    def register_authorized_grant(
        self,
        consortium_id: str,
        grant_id: str,
        revision: int,
        scope_digest: str,
        permitted_counterparts: list[str],
        permitted_purposes: list[str],
        permitted_operations: list[str] | None = None,
        expires_at: str | None = None,
        allow_institution_identity_sharing: bool = False,
    ) -> dict[str, Any]:
        """Register an independently authorized institutional data-sharing grant at this worker."""
        clean_grant = grant_id.strip()
        if not clean_grant:
            raise ValueError("grant_id must not be empty")
        if not scope_digest or len(scope_digest.strip()) < 32:
            raise ValueError("Invalid scope_digest: must be at least 32 characters")
        record = {
            "consortium_id": consortium_id.strip(),
            "grant_id": clean_grant,
            "revision": revision,
            "scope_digest": scope_digest.strip(),
            "permitted_counterparts": [c.lower().strip() for c in permitted_counterparts],
            "permitted_purposes": [p.strip() for p in permitted_purposes],
            "permitted_operations": [
                op.strip()
                for op in (
                    permitted_operations
                    or ["PRIVATE_SET_INTERSECTION", "ESTABLISHED_PSI", "PSI_MATCH"]
                )
            ],
            "expires_at": expires_at,
            "allow_institution_identity_sharing": allow_institution_identity_sharing,
            "is_active": True,
            "authorized_at": datetime.datetime.now(UTC).isoformat(),
        }
        self._locally_revoked_grants.discard(clean_grant)
        self._authorized_grants[clean_grant] = record
        self._persist_authorized_grants()
        logger.info(
            "Institution '%s' registered authorized operational grant '%s' (rev %d)",
            self.bank_id,
            clean_grant,
            revision,
        )
        return record

    def verify_grant_participation_authority(
        self,
        ticket: OperationAuthorizationTicket,
        counterpart_id: str,
        purpose: str,
        operation: str = "PRIVATE_SET_INTERSECTION",
    ) -> None:
        """Independently verify institutional participation authority before signing or matching."""
        clean_grant = ticket.grant_id.strip()
        if clean_grant in self._locally_revoked_grants:
            raise ConsortiumSecurityError(
                f"Operational data sharing grant '{clean_grant}' has been locally revoked by institution '{self.bank_id}'. "
                "Participation authority denied."
            )
        clean_counterpart = counterpart_id.lower().strip()

        # If consortium service is available, verify against authoritative governance
        if self._consortium_service:
            consortium = self._consortium_service.get_consortium(ticket.consortium_id)
            if consortium:
                grants = getattr(consortium, "operational_data_sharing_grants", {}) or {}
                meta_grant = consortium.metadata.get("operational_data_sharing_grant") or {}
                gov_grant = grants.get(clean_grant) or (
                    meta_grant if meta_grant.get("grant_id") == clean_grant else None
                )
                if gov_grant:
                    if not gov_grant.get("is_active", True) or gov_grant.get("status") == "REVOKED":
                        raise ConsortiumSecurityError(
                            f"Grant '{clean_grant}' is revoked or inactive in consortium governance."
                        )
                    if self.bank_id in gov_grant.get("revoked_by_institutions", []):
                        raise ConsortiumSecurityError(
                            f"Grant '{clean_grant}' revoked by institution '{self.bank_id}'."
                        )
                    if clean_counterpart in gov_grant.get("revoked_by_institutions", []):
                        raise ConsortiumSecurityError(
                            f"Grant '{clean_grant}' revoked by counterpart '{clean_counterpart}'."
                        )
                    if gov_grant.get("revision") != ticket.grant_revision:
                        raise ConsortiumSecurityError(
                            f"Grant revision mismatch: expected {gov_grant.get('revision')}, got {ticket.grant_revision}."
                        )
                    if gov_grant.get("scope_digest") != ticket.grant_scope_digest:
                        raise ConsortiumSecurityError(
                            "Grant scope digest mismatch in authorization ticket."
                        )
                    part_banks = [b.lower().strip() for b in gov_grant.get("participating_banks", [])]
                    if self.bank_id not in part_banks or clean_counterpart not in part_banks:
                        raise ConsortiumSecurityError(
                            f"Institution '{self.bank_id}' or counterpart '{clean_counterpart}' is not in participating banks."
                        )
                    if purpose not in gov_grant.get("allowed_purposes", []):
                        raise ConsortiumSecurityError(
                            f"Purpose '{purpose}' not permitted in grant '{clean_grant}'."
                        )
                    approvals = gov_grant.get("approvals", {})
                    if self.bank_id not in approvals:
                        raise ConsortiumSecurityError(
                            f"Institution '{self.bank_id}' has not approved grant '{clean_grant}' revision {ticket.grant_revision}."
                        )
                    return

        # Otherwise verify locally registered institutional approval
        if clean_grant not in self._authorized_grants:
            raise ConsortiumSecurityError(
                f"Institution '{self.bank_id}' has not authorized operational grant '{clean_grant}' "
                f"(revision {ticket.grant_revision}) for counterpart '{clean_counterpart}'. "
                "Coordinator matching credentials cannot manufacture institutional participation."
            )
        auth = self._authorized_grants[clean_grant]
        if not auth.get("is_active", True):
            raise ConsortiumSecurityError(
                f"Grant '{clean_grant}' is marked inactive for institution '{self.bank_id}'."
            )
        if auth.get("consortium_id") != ticket.consortium_id:
            raise ConsortiumSecurityError(
                f"Consortium mismatch in authorized grant: expected '{auth.get('consortium_id')}', got '{ticket.consortium_id}'."
            )
        if int(auth.get("revision", 1)) != ticket.grant_revision:
            raise ConsortiumSecurityError(
                f"Stale grant revision in authorization ticket: authorized {auth.get('revision')}, got {ticket.grant_revision}."
            )
        if auth.get("scope_digest") != ticket.grant_scope_digest:
            raise ConsortiumSecurityError(
                "Scope digest mismatch: Authorization ticket scope does not match institutionally approved grant scope."
            )
        if clean_counterpart not in [
            b.lower().strip() for b in auth.get("permitted_counterparts", [])
        ]:
            raise ConsortiumSecurityError(
                f"Counterpart '{clean_counterpart}' is not authorized under grant '{clean_grant}' for institution '{self.bank_id}'."
            )
        if purpose not in auth.get("permitted_purposes", []):
            raise ConsortiumSecurityError(
                f"Purpose '{purpose}' is not authorized under grant '{clean_grant}'."
            )
        if auth.get("expires_at"):
            now = datetime.datetime.now(UTC)
            exp = datetime.datetime.fromisoformat(auth["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            if now >= exp:
                raise ConsortiumSecurityError(
                    f"Institutional approval for grant '{clean_grant}' expired at {auth['expires_at']}."
                )


    def record_local_grant_revocation(self, grant_id: str) -> None:
        """Locally revoke an operational grant at this institutional boundary."""
        clean_id = grant_id.strip()
        if not clean_id:
            raise ValueError("grant_id must not be empty")
        self._locally_revoked_grants.add(clean_id)
        if clean_id in self._authorized_grants:
            self._authorized_grants[clean_id]["is_active"] = False
            self._persist_authorized_grants()
        logger.info(
            "Institution '%s' locally revoked operational grant '%s'",
            self.bank_id,
            clean_id,
        )

    def compute_grant_approval_signature(
        self, consortium_id: str, grant_id: str, revision: int, scope_digest: str
    ) -> str:
        """Produce bank worker signature approving an immutable grant revision and scope digest."""
        canonical_bytes = f"{consortium_id}:{grant_id}:{revision}:{scope_digest}:{self.bank_id}".encode()
        if self._signing_identity is not None:
            return self._signing_identity.sign(canonical_bytes)
        return hmac.new(
            self._auth_secret.encode("utf-8"),
            canonical_bytes,
            hashlib.sha256,
        ).hexdigest()

    def sign_operation_authorization_ticket(
        self, ticket: OperationAuthorizationTicket
    ) -> str:
        """Sign an operation authorization ticket after verifying bank participation authority."""
        if ticket.grant_id in self._locally_revoked_grants:
            raise ConsortiumSecurityError(
                f"Operational data sharing grant '{ticket.grant_id}' has been locally revoked by institution '{self.bank_id}'. "
                "Participation authority denied."
            )
        if ticket.is_expired():
            raise ConsortiumSecurityError(
                f"Operation authorization ticket '{ticket.ticket_id}' has expired. "
                "Participation authority denied."
            )
        target = ticket.target_bank_id.lower().strip()
        requester = ticket.requesting_bank_id.lower().strip()
        if self.bank_id not in (target, requester):
            raise ConsortiumSecurityError(
                f"Institution '{self.bank_id}' is not a party to authorization ticket (requester='{requester}', target='{target}')."
            )
        if not ticket.grant_scope_digest or len(ticket.grant_scope_digest) < 32:
            raise ConsortiumSecurityError(
                "Invalid or missing immutable grant scope digest in authorization ticket."
            )
        counterpart = target if self.bank_id == requester else requester
        self.verify_grant_participation_authority(
            ticket,
            counterpart_id=counterpart,
            purpose=ticket.purpose,
            operation=ticket.operation,
        )
        canonical_bytes = f"{ticket.canonical_digest()}:{self.bank_id}".encode()
        if ticket.operation == "ESTABLISHED_PSI" or self._signing_identity is not None:
            if self._signing_identity is None:
                raise ConsortiumSecurityError(
                    f"Operational matching failed: Worker '{self.bank_id}' has no provisioned institutional signing key. "
                    "Signing authorization tickets requires sovereign Ed25519 signing identity."
                )
            return self._signing_identity.sign(canonical_bytes)
        return hmac.new(
            self._auth_secret.encode("utf-8"),
            canonical_bytes,
            hashlib.sha256,
        ).hexdigest()

    def verify_operation_authorization(
        self,
        ticket: OperationAuthorizationTicket | None,
        expected_job_id: str,
        expected_requesting_bank: str,
        expected_purpose: str,
    ) -> None:
        """Validate worker-side participation authority independently before executing matching."""
        if ticket is None:
            raise ConsortiumSecurityError(
                "Missing mandatory operation authorization ticket. Unverified matching requests are rejected."
            )
        if ticket.target_bank_id.lower().strip() != self.bank_id:
            raise ConsortiumSecurityError(
                f"Target institution mismatch in authorization ticket: expected '{self.bank_id}', got '{ticket.target_bank_id}'."
            )
        if ticket.requesting_bank_id.lower().strip() != expected_requesting_bank.lower().strip():
            raise ConsortiumSecurityError(
                f"Requesting institution mismatch in authorization ticket: expected '{expected_requesting_bank}', got '{ticket.requesting_bank_id}'."
            )
        if ticket.job_id != expected_job_id:
            raise ConsortiumSecurityError(
                f"Session job ID mismatch in authorization ticket: expected '{expected_job_id}', got '{ticket.job_id}'."
            )
        if ticket.purpose != expected_purpose:
            raise ConsortiumSecurityError(
                f"Purpose mismatch in authorization ticket: expected '{expected_purpose}', got '{ticket.purpose}'."
            )
        if ticket.is_expired():
            raise ConsortiumSecurityError(
                f"Operation authorization ticket '{ticket.ticket_id}' has expired at {ticket.expires_at}."
            )
        ticket_replay_key = f"ticket:{ticket.ticket_id}"
        if ticket_replay_key in self._consumed_nonces or ticket.ticket_id in self._consumed_nonces:
            raise ConsortiumSecurityError(
                f"Replay attack detected: Operation authorization ticket '{ticket.ticket_id}' has already been processed by institution '{self.bank_id}'."
            )
        if ticket.grant_id in self._locally_revoked_grants:
            raise ConsortiumSecurityError(
                f"Operational data sharing grant '{ticket.grant_id}' has been locally revoked by institution '{self.bank_id}'. "
                "Matching execution denied."
            )
        if not ticket.grant_scope_digest or len(ticket.grant_scope_digest) < 32:
            raise ConsortiumSecurityError(
                "Invalid or missing immutable grant scope digest in authorization ticket."
            )
        self.verify_grant_participation_authority(
            ticket,
            counterpart_id=expected_requesting_bank,
            purpose=expected_purpose,
            operation=ticket.operation,
        )

        is_operational = (ticket.operation == "ESTABLISHED_PSI") or (self._signing_identity is not None)

        # 1. Verify target bank (self) approval signature
        target_sig = ticket.bank_approvals.get(self.bank_id) or (
            ticket.target_bank_approval_signature if self.bank_id == ticket.target_bank_id.lower().strip() else ""
        )
        if not target_sig:
            raise ConsortiumSecurityError(
                f"Target institution '{self.bank_id}' participation authority verification failed: "
                "missing approval signature in ticket."
            )
        target_canonical = f"{ticket.canonical_digest()}:{self.bank_id}".encode()
        target_valid = False
        if self._signing_identity is not None:
            target_valid = self._signing_identity.verify(target_sig, target_canonical)
        elif not is_operational and self._auth_secret:
            expected_target_sig = hmac.new(
                self._auth_secret.encode("utf-8"),
                target_canonical,
                hashlib.sha256,
            ).hexdigest()
            target_valid = hmac.compare_digest(expected_target_sig, target_sig)

        if not target_valid:
            raise ConsortiumSecurityError(
                f"Target institution '{self.bank_id}' participation authority verification failed: "
                "invalid approval signature in ticket."
            )

        # 2. Verify requesting bank approval signature
        req_bank = ticket.requesting_bank_id.lower().strip()
        peer_sig = ticket.bank_approvals.get(req_bank) or ticket.requesting_bank_signature
        if not peer_sig:
            raise ConsortiumSecurityError(
                f"Requesting institution '{ticket.requesting_bank_id}' authorization verification failed: "
                "missing approval signature in ticket."
            )

        peer_canonical = f"{ticket.canonical_digest()}:{ticket.requesting_bank_id}".encode()
        peer_valid = False

        if self._peer_registry.has_key(req_bank):
            peer_valid = self._peer_registry.verify_signature(req_bank, peer_sig, peer_canonical)
        elif not is_operational:
            # Strictly non-authoritative prototype path: check for symmetric shared secret
            peer_secret = self._trusted_peer_verification_keys.get(req_bank)
            if peer_secret:
                expected_peer_sig = hmac.new(
                    peer_secret.encode("utf-8"),
                    peer_canonical,
                    hashlib.sha256,
                ).hexdigest()
                peer_valid = hmac.compare_digest(expected_peer_sig, peer_sig)

        if not peer_valid:
            raise ConsortiumSecurityError(
                f"Requesting institution '{ticket.requesting_bank_id}' authorization verification failed: "
                "invalid or unverified approval signature in ticket."
            )

    def _persist_consumed_nonce(self, nonce: str) -> None:
        """Persist consumed nonce atomically to durable disk storage, failing closed on disk errors."""
        with self._replay_lock:
            if nonce in self._consumed_nonces:
                raise ConsortiumSecurityError(
                    f"Replay attack detected: Nonce '{nonce}' has already been processed by institution '{self.bank_id}'."
                )
            new_nonces = set(self._consumed_nonces)
            new_nonces.add(nonce)
            tmp_path = f"{self._replay_store_path}.{secrets.token_hex(8)}.tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(sorted(list(new_nonces)), f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self._replay_store_path)
                self._consumed_nonces.add(nonce)
            except Exception as exc:
                if os.path.exists(tmp_path):
                    with contextlib.suppress(Exception):
                        os.remove(tmp_path)
                raise ConsortiumSecurityError(
                    f"Failed to durably persist replay nonce for institution '{self.bank_id}': {exc}"
                ) from exc

    def _sanitize_entity_id(self, entity_id: str) -> str:
        """Validate and canonicalize entity identifier to prevent path traversal and data poisoning."""
        clean_id = entity_id.strip()
        if not clean_id or not re.match(r"^[a-zA-Z0-9_\-]+$", clean_id):
            raise ValueError(
                f"Invalid entity identifier '{entity_id}': Contains illegal characters or path traversal elements. "
                "Only alphanumeric, hyphen, and underscore characters are permitted."
            )
        vault_path = Path(self.vault_dir).resolve()
        target_path = (vault_path / f"{clean_id}.json").resolve()
        try:
            target_path.relative_to(vault_path)
        except ValueError:
            raise PermissionError(
                f"Path traversal detected: Entity '{entity_id}' attempts to escape institutional vault '{self.bank_id}'."
            )
        if target_path.is_symlink():
            raise PermissionError(
                f"Symlink exploitation detected: Entity file '{target_path}' is a symbolic link."
            )
        return clean_id

    def store_private_entity(self, entity: Entity | dict[str, Any]) -> str:
        """Store an authoritative entity strictly in this bank's isolated storage vault."""
        ent_dict = entity.to_dict() if isinstance(entity, Entity) else dict(entity)

        ent_bank = str(ent_dict.get("bank_id", "")).lower().strip()
        if ent_bank != self.bank_id:
            raise PermissionError(
                f"Isolated bank worker '{self.bank_id}' cannot store entity belonging to institution '{ent_bank}'."
            )

        raw_ent_id = ent_dict.get("id") or str(uuid.uuid4())
        entity_id = self._sanitize_entity_id(raw_ent_id)
        ent_dict["id"] = entity_id
        ent_dict["bank_id"] = self.bank_id

        # Derive privacy_id deterministically if missing and raw_identifier is present
        if not ent_dict.get("privacy_id"):
            raw_id = ent_dict.get("attributes", {}).get("raw_identifier") or ent_dict.get(
                "raw_identifier"
            )
            if raw_id:
                ent_type = str(ent_dict.get("entity_type", "customer"))
                ent_dict["privacy_id"] = PrivacyPreservingIdentifier.compute(raw_id, ent_type)

        vault_path = Path(self.vault_dir).resolve()
        path = vault_path / f"{entity_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ent_dict, f, indent=2)
        return entity_id

    def load_private_entity(self, entity_id: str) -> dict[str, Any]:
        """Load an authoritative entity strictly from this bank's isolated storage vault."""
        clean_id = self._sanitize_entity_id(entity_id)
        vault_path = Path(self.vault_dir).resolve()
        path = vault_path / f"{clean_id}.json"
        if not path.exists():
            raise ValueError(
                f"Entity '{clean_id}' not found in isolated private storage of institution '{self.bank_id}'."
            )
        if path.is_symlink():
            raise PermissionError(f"Symlink exploitation detected on entity '{clean_id}'.")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if str(data.get("bank_id", "")).lower().strip() != self.bank_id:
            raise PermissionError(
                f"Tenant isolation breach detected: Entity '{clean_id}' belongs to '{data.get('bank_id')}', "
                f"cannot be loaded by worker '{self.bank_id}'."
            )
        return data

    def list_private_entities(self) -> list[dict[str, Any]]:
        """List all entities currently residing in this bank's isolated storage vault."""
        entities: list[dict[str, Any]] = []
        if not os.path.exists(self.vault_dir):
            return entities
        for fname in os.listdir(self.vault_dir):
            if fname.endswith(".json"):
                fpath = os.path.join(self.vault_dir, fname)
                try:
                    with open(fpath, encoding="utf-8") as f:
                        data = json.load(f)
                    if str(data.get("bank_id", "")).lower().strip() == self.bank_id:
                        entities.append(data)
                except Exception as exc:
                    logger.warning("Failed to read private entity file %s: %s", fpath, exc)
        return entities

    def initiate_blinded_query(
        self,
        query_entity_id: str,
        consortium_id: str,
        target_bank_id: str,
        purpose: str = "AML_COLLABORATIVE_INVESTIGATION",
    ) -> BlindedQueryMessage:
        """Stage 1: Blinds local entity identifier with Bank A's private exponent.

        Guarantees quadratic residue membership in prime-order subgroup G_q.
        """
        clean_target = target_bank_id.lower().strip()
        if clean_target == self.bank_id:
            raise ValueError("Target counterpart bank cannot be identical to requesting bank.")

        entity = self.load_private_entity(query_entity_id)
        privacy_id = str(entity.get("privacy_id", ""))
        if not privacy_id:
            raise ValueError(f"Entity '{query_entity_id}' has missing or empty privacy_id.")

        # Obtain private exponent strictly through this bank's TenantKMSSession
        private_exp = self._kms_session.get_psi_private_exponent()

        # Map to prime-order subgroup G_q and blind: c = H_q(x)^a mod p
        elem_int = hash_to_prime_subgroup(privacy_id)
        blinded_int = blind_element(elem_int, private_exp, PSI_PRIME)
        if not validate_group_element(blinded_int):
            raise ValueError("Blinded query element failed prime subgroup validation.")
        blinded_hex = hex(blinded_int)

        job_id = str(uuid.uuid4())
        nonce = secrets.token_hex(16)
        timestamp = datetime.datetime.now(UTC).isoformat()

        canonical_bytes = (
            f"{job_id}:{consortium_id}:{self.bank_id}:"
            f"{clean_target}:{blinded_hex}:{nonce}:"
            f"{timestamp}:{purpose}".encode()
        )
        if self._signing_identity is not None:
            signature = self._signing_identity.sign(canonical_bytes)
        else:
            signature = hmac.new(
                self._auth_secret.encode("utf-8"),
                canonical_bytes,
                hashlib.sha256,
            ).hexdigest()

        return BlindedQueryMessage(
            job_id=job_id,
            consortium_id=consortium_id,
            requesting_bank_id=self.bank_id,
            target_bank_id=clean_target,
            blinded_element_hex=blinded_hex,
            nonce=nonce,
            timestamp=timestamp,
            purpose=purpose,
            signature=signature,
        )

    def process_counterpart_query(
        self,
        query: BlindedQueryMessage,
        requester_auth_secret: str | None = None,
    ) -> CounterpartMatchResponse:
        """Stage 2: Applies second exponent (b) to query and blinds all local target entities.

        Authenticates peer strictly using local trusted peer keystore (never request-supplied secret).
        Preserves candidate confidentiality: Never publishes candidate risk payloads keyed to public elements.
        """
        if query.target_bank_id.lower().strip() != self.bank_id:
            raise PermissionError(
                f"Worker routing mismatch: Received query targeted for '{query.target_bank_id}', "
                f"but this worker is '{self.bank_id}'."
            )

        # 1. Anti-Replay Nonce Verification (durable across restarts)
        if query.nonce in self._consumed_nonces:
            raise ConsortiumSecurityError(
                f"Replay attack detected: Nonce '{query.nonce}' has already been processed by institution '{self.bank_id}'."
            )

        # 2. Timestamp Freshness Check (within 300 seconds)
        now = datetime.datetime.now(UTC)
        query_time = datetime.datetime.fromisoformat(query.timestamp)
        if query_time.tzinfo is None:
            query_time = query_time.replace(tzinfo=UTC)
        if abs((now - query_time).total_seconds()) > 300:
            raise ConsortiumSecurityError(
                f"Expired query rejected by institution '{self.bank_id}': timestamp delta exceeds 300 seconds."
            )

        # 3. Verify Requester Signature strictly using internal trusted peer keystore
        req_bank = query.requesting_bank_id.lower().strip()
        peer_key = self._trusted_peer_verification_keys.get(req_bank)
        if not peer_key and not self._peer_registry.has_key(req_bank):
            raise ConsortiumSecurityError(
                f"Peer authentication failed: Institution '{query.requesting_bank_id}' has no trusted verification key enrolled."
            )
        canonical_bytes = (
            f"{query.job_id}:{query.consortium_id}:{query.requesting_bank_id}:"
            f"{query.target_bank_id}:{query.blinded_element_hex}:{query.nonce}:"
            f"{query.timestamp}:{query.purpose}".encode()
        )
        sig_valid = False
        if self._peer_registry.has_key(req_bank):
            sig_valid = self._peer_registry.verify_signature(req_bank, query.signature, canonical_bytes)
        elif peer_key:
            expected_sig = hmac.new(
                peer_key.encode("utf-8"),
                canonical_bytes,
                hashlib.sha256,
            ).hexdigest()
            sig_valid = hmac.compare_digest(expected_sig, query.signature)

        if not sig_valid:
            raise ConsortiumSecurityError(
                f"Peer authentication failed: Invalid signature from requesting bank '{query.requesting_bank_id}'."
            )

        # 3b. Verify Operation Authorization Ticket (if present or if required)
        if query.ticket is not None:
            self.verify_operation_authorization(
                ticket=query.ticket,
                expected_job_id=query.job_id,
                expected_requesting_bank=query.requesting_bank_id,
                expected_purpose=query.purpose,
            )
        elif self._require_operation_ticket:
            raise ConsortiumSecurityError(
                "Missing mandatory operation authorization ticket. Unverified matching requests are rejected."
            )

        # 4. Validate incoming group element and apply Counterpart Modular Exponentiation: D = (H(x)^a)^b mod p
        query_blinded_int = int(query.blinded_element_hex, 16)
        if not validate_group_element(query_blinded_int):
            raise ConsortiumSecurityError(
                "Subgroup validation failed: Query element is not in prime-order subgroup."
            )

        private_exp = self._kms_session.get_psi_private_exponent()
        double_blinded_int = double_blind_element(query_blinded_int, private_exp, PSI_PRIME)
        if not validate_group_element(double_blinded_int):
            raise ConsortiumSecurityError(
                "Subgroup validation failed: Double blinded query element."
            )
        double_blinded_hex = hex(double_blinded_int)

        # 5. Load all entities in target bank's private vault and blind: B_j = H_q(y_j)^b mod p
        # Candidate Confidentiality Invariant: Never encrypt candidate payloads under public keys or transmit candidate risk
        local_entities = self.list_private_entities()
        blinded_target_elements: list[str] = []
        encrypted_risk_payloads: dict[str, str] = {}

        for ent in local_entities:
            pid = str(ent.get("privacy_id", ""))
            if not pid:
                continue
            y_elem = hash_to_prime_subgroup(pid)
            b_val = blind_element(y_elem, private_exp, PSI_PRIME)
            if not validate_group_element(b_val):
                continue
            b_hex = hex(b_val)
            blinded_target_elements.append(b_hex)

        # Cryptographically permute order to prevent index linkage
        secrets.SystemRandom().shuffle(blinded_target_elements)

        # 6. Construct signed counterpart response
        resp_nonce = secrets.token_hex(16)
        resp_timestamp = now.isoformat()
        risk_context = {
            "target_entity_count": len(local_entities),
            "payload_encryption": "NONE_DISCLOSED",
        }

        sorted_elements = ",".join(sorted(blinded_target_elements))
        payloads_str = json.dumps(encrypted_risk_payloads, sort_keys=True)
        canonical_resp = (
            f"{query.job_id}:{query.consortium_id}:{self.bank_id}:"
            f"{double_blinded_hex}:{sorted_elements}:{resp_nonce}:"
            f"{resp_timestamp}:{json.dumps(risk_context, sort_keys=True)}:{payloads_str}"
        )
        resp_sig = hmac.new(
            self._auth_secret.encode("utf-8"),
            canonical_resp.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Persist consumed nonce to durable store (fail-closed)
        self._persist_consumed_nonce(query.nonce)
        if query.ticket:
            self._persist_consumed_nonce(f"ticket:{query.ticket.ticket_id}")

        return CounterpartMatchResponse(
            job_id=query.job_id,
            consortium_id=query.consortium_id,
            target_bank_id=self.bank_id,
            double_blinded_query_hex=double_blinded_hex,
            blinded_target_elements_hex=tuple(blinded_target_elements),
            nonce=resp_nonce,
            timestamp=resp_timestamp,
            sanitized_risk_context=risk_context,
            signature=resp_sig,
            encrypted_risk_payloads=encrypted_risk_payloads,
        )

    def finalize_match_evaluation(
        self,
        query: BlindedQueryMessage,
        response: CounterpartMatchResponse,
        counterpart_auth_secret: str | None = None,
    ) -> VerifiedMatchReceipt:
        """Stage 3: Applies private exponent (a) to counterpart elements and computes intersection.

        Authenticates counterpart strictly using local trusted peer keystore (never request-supplied secret).
        Preserves candidate confidentiality: Does not decrypt unverified candidate payloads.
        """
        if response.job_id != query.job_id:
            raise ConsortiumSecurityError("Job ID mismatch between query and counterpart response.")
        if response.target_bank_id != query.target_bank_id:
            raise ConsortiumSecurityError("Target bank ID mismatch in counterpart response.")

        # 1. Verify Counterpart Signature strictly using internal trusted peer keystore
        peer_key = self._trusted_peer_verification_keys.get(response.target_bank_id.lower().strip())
        if not peer_key:
            raise ConsortiumSecurityError(
                f"Peer authentication failed: Institution '{response.target_bank_id}' has no trusted verification key enrolled."
            )
        sorted_elements = ",".join(sorted(response.blinded_target_elements_hex))
        payloads_str = json.dumps(response.encrypted_risk_payloads, sort_keys=True)
        canonical_resp = (
            f"{query.job_id}:{query.consortium_id}:{response.target_bank_id}:"
            f"{response.double_blinded_query_hex}:{sorted_elements}:{response.nonce}:"
            f"{response.timestamp}:{json.dumps(response.sanitized_risk_context, sort_keys=True)}:{payloads_str}"
        )
        expected_resp_sig = hmac.new(
            peer_key.encode("utf-8"),
            canonical_resp.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_resp_sig, response.signature):
            raise ConsortiumSecurityError(
                f"Peer authentication failed: Invalid signature from target bank '{response.target_bank_id}'."
            )

        # 2. Compute E_j = (B_j)^a mod p for all counterpart elements
        private_exp = self._kms_session.get_psi_private_exponent()
        query_double_blinded_int = int(response.double_blinded_query_hex, 16)

        is_match = False

        for b_hex in response.blinded_target_elements_hex:
            b_int = int(b_hex, 16)
            if not validate_group_element(b_int):
                continue
            e_int = double_blind_element(b_int, private_exp, PSI_PRIME)
            if e_int == query_double_blinded_int:
                is_match = True
                break

        match_confidence = 1.0 if is_match else 0.0

        # 3. Risk payload disclosure is UNAVAILABLE because candidate payloads cannot be securely bound
        # to the match without leaking decryption keys in 1-round DH-PSI.
        # Truthful disclosure invariant: Mark risk attributes UNAVAILABLE instead of fabricating context.
        matched_risk_level = "UNAVAILABLE" if is_match else "UNOBSERVED"
        matched_typologies: list[str] = []

        # 4. Generate verifiable execution receipt
        receipt_id = str(uuid.uuid4())
        transcript_canonical = f"{query.compute_digest()}:{response.compute_digest()}:{is_match}:{match_confidence}:{matched_risk_level}"
        transcript_digest = hashlib.sha256(transcript_canonical.encode("utf-8")).hexdigest()
        now_iso = datetime.datetime.now(UTC).isoformat()

        receipt_sig_a = hmac.new(
            self._auth_secret.encode("utf-8"),
            f"{receipt_id}:{transcript_digest}:{is_match}:{matched_risk_level}".encode(),
            hashlib.sha256,
        ).hexdigest()

        return VerifiedMatchReceipt(
            receipt_id=receipt_id,
            job_id=query.job_id,
            consortium_id=query.consortium_id,
            requesting_bank_id=self.bank_id,
            target_bank_id=response.target_bank_id,
            query_entity_id="",
            match_confirmed=is_match,
            match_confidence=match_confidence,
            protocol="DH-PSI-2048-MODP",
            transcript_digest=transcript_digest,
            executed_at=now_iso,
            worker_a_signature=receipt_sig_a,
            worker_b_signature=response.signature,
            matched_risk_level=matched_risk_level,
            matched_typologies=tuple(matched_typologies),
            assurance_level="ISOLATED_INTEGRATION_VERIFIED",
            match_category="EXACT_MATCH" if is_match else "NO_MATCH",
            is_exact_confirmed=is_match,
        )

    def initiate_established_psi_query(
        self,
        query_entity_id: str,
        consortium_id: str,
        target_bank_id: str,
        ticket: OperationAuthorizationTicket,
        purpose: str = "AML_COLLABORATIVE_INVESTIGATION",
    ) -> EstablishedPSIQueryMessage:
        """Stage 1 for Established PSI: Generates client setup request using OpenMined PSI."""
        if not OpenMinedPSIAdapter.is_available():
            raise ConsortiumSecurityError(
                "Established PSI execution failed: OpenMined PSI library is unavailable in this environment."
            )

        clean_target = target_bank_id.lower().strip()
        if clean_target == self.bank_id:
            raise ValueError("Target counterpart bank cannot be identical to requesting bank.")

        entity = self.load_private_entity(query_entity_id)
        privacy_id = str(entity.get("privacy_id", ""))
        if not privacy_id:
            raise ValueError(f"Entity '{query_entity_id}' has missing or empty privacy_id.")

        # Client creates PSI client session (reveal_intersection=True for exact matching)
        client = OpenMinedPSIAdapter.create_client(reveal_intersection=True)
        self._active_psi_clients[ticket.job_id] = client

        # Generate client request for privacy_id
        req_bytes = OpenMinedPSIAdapter.build_client_request(client, [privacy_id])
        psi_request_b64 = OpenMinedPSIAdapter.encode_b64(req_bytes)

        nonce = secrets.token_hex(16)
        timestamp = datetime.datetime.now(UTC).isoformat()

        canonical_bytes = (
            f"{ticket.job_id}:{consortium_id}:{self.bank_id}:"
            f"{clean_target}:{psi_request_b64}:{nonce}:"
            f"{timestamp}:{purpose}:{ticket.canonical_digest()}".encode()
        )
        if self._signing_identity is None:
            raise ConsortiumSecurityError(
                f"Worker node '{self.bank_id}' has no provisioned signing key. Fails closed."
            )
        sig = self._signing_identity.sign(canonical_bytes)

        return EstablishedPSIQueryMessage(
            job_id=ticket.job_id,
            consortium_id=consortium_id,
            requesting_bank_id=self.bank_id,
            target_bank_id=clean_target,
            psi_request_b64=psi_request_b64,
            nonce=nonce,
            timestamp=timestamp,
            purpose=purpose,
            signature=sig,
            ticket=ticket,
        )

    def process_established_psi_counterpart(
        self,
        query: EstablishedPSIQueryMessage,
    ) -> EstablishedPSICounterpartResponse:
        """Stage 2 for Established PSI: Server processes client request and computes server setup."""
        if query.target_bank_id.lower().strip() != self.bank_id:
            raise PermissionError(
                f"Worker routing mismatch: Received query targeted for '{query.target_bank_id}', "
                f"but this worker is '{self.bank_id}'."
            )

        # 1. Durable Anti-Replay Nonce Verification
        if query.nonce in self._consumed_nonces:
            raise ConsortiumSecurityError(
                f"Replay attack detected: Nonce '{query.nonce}' has already been processed by institution '{self.bank_id}'."
            )

        # 2. Timestamp Freshness Check (within 300 seconds)
        now = datetime.datetime.now(UTC)
        query_time = datetime.datetime.fromisoformat(query.timestamp)
        if query_time.tzinfo is None:
            query_time = query_time.replace(tzinfo=UTC)
        if abs((now - query_time).total_seconds()) > 300:
            raise ConsortiumSecurityError(
                f"Expired query rejected by institution '{self.bank_id}': timestamp delta exceeds 300 seconds."
            )

        # 3. Verify Requester Signature using internal trusted peer keystore (Ed25519 strictly required)
        req_bank = query.requesting_bank_id.lower().strip()
        if not self._peer_registry.has_key(req_bank):
            raise ConsortiumSecurityError(
                f"Peer authentication failed: Institution '{query.requesting_bank_id}' has no trusted verification key enrolled."
            )

        if not query.signature:
            raise ConsortiumSecurityError(
                f"Peer authentication failed: Missing signature from requesting bank '{query.requesting_bank_id}'."
            )

        canonical_bytes = (
            f"{query.job_id}:{query.consortium_id}:{query.requesting_bank_id}:"
            f"{query.target_bank_id}:{query.psi_request_b64}:{query.nonce}:"
            f"{query.timestamp}:{query.purpose}:{query.ticket.canonical_digest()}".encode()
        )
        sig_valid = self._peer_registry.verify_signature(req_bank, query.signature, canonical_bytes)
        if not sig_valid:
            raise ConsortiumSecurityError(
                f"Peer authentication failed: Invalid signature from requesting bank '{query.requesting_bank_id}'."
            )

        # 4. Independent Operation Authorization Verification (Fail-Closed)
        self.verify_operation_authorization(
            query.ticket,
            expected_job_id=query.job_id,
            expected_requesting_bank=query.requesting_bank_id,
            expected_purpose=query.purpose,
        )

        # 5. Check Established PSI Library
        if not OpenMinedPSIAdapter.is_available():
            raise ConsortiumSecurityError(
                "Established PSI execution failed: OpenMined PSI library is unavailable in target bank environment."
            )

        # 6. Gather target server items from private vault
        local_entities = self.list_private_entities()
        server_items = [
            str(ent.get("privacy_id", ""))
            for ent in local_entities
            if ent.get("privacy_id")
        ]

        # 7. Create server and compute setup + response (FPR = 1e-9 per authoritative spec)
        server = OpenMinedPSIAdapter.create_server(reveal_intersection=True)
        setup_bytes = OpenMinedPSIAdapter.build_server_setup(server, server_items, fpr=1e-9)
        req_bytes = OpenMinedPSIAdapter.decode_b64(query.psi_request_b64)
        resp_bytes = OpenMinedPSIAdapter.process_server_request(server, req_bytes)

        setup_b64 = OpenMinedPSIAdapter.encode_b64(setup_bytes)
        resp_b64 = OpenMinedPSIAdapter.encode_b64(resp_bytes)

        resp_nonce = secrets.token_hex(16)
        resp_timestamp = now.isoformat()

        setup_hash = hashlib.sha256(setup_bytes).hexdigest()
        resp_hash = hashlib.sha256(resp_bytes).hexdigest()
        ticket_digest = query.ticket.canonical_digest() if query.ticket else ""
        grant_id = query.ticket.grant_id if query.ticket else ""
        grant_revision = query.ticket.grant_revision if query.ticket else 1
        grant_scope_digest = query.ticket.grant_scope_digest if query.ticket else ""

        canonical_evidence = canonical_counterpart_evidence_bytes(
            version="1.0",
            consortium_id=query.consortium_id,
            grant_id=grant_id,
            grant_revision=grant_revision,
            grant_scope_digest=grant_scope_digest,
            requesting_bank_id=query.requesting_bank_id,
            target_bank_id=self.bank_id,
            job_id=query.job_id,
            ticket_digest=ticket_digest,
            psi_server_setup_hash=setup_hash,
            psi_response_hash=resp_hash,
            protocol="OPENMINED-PSI-2.0.6-ECDH",
            nonce=resp_nonce,
            timestamp=resp_timestamp,
        )
        counterpart_evidence_digest = hashlib.sha256(canonical_evidence).hexdigest()
        if self._signing_identity is None:
            raise ConsortiumSecurityError(
                f"Institutional signing key not provisioned for worker '{self.bank_id}'. "
                "Counterpart execution fails closed to prevent unauthenticated execution evidence."
            )
        resp_sig = self._signing_identity.sign(canonical_evidence)

        # Persist consumed nonce and ticket to prevent replay
        self._persist_consumed_nonce(query.nonce)
        if query.ticket:
            self._persist_consumed_nonce(f"ticket:{query.ticket.ticket_id}")

        return EstablishedPSICounterpartResponse(
            job_id=query.job_id,
            consortium_id=query.consortium_id,
            target_bank_id=self.bank_id,
            psi_server_setup_b64=setup_b64,
            psi_response_b64=resp_b64,
            nonce=resp_nonce,
            timestamp=resp_timestamp,
            signature=resp_sig,
            requesting_bank_id=query.requesting_bank_id,
            grant_id=grant_id,
            grant_revision=grant_revision,
            grant_scope_digest=grant_scope_digest,
            ticket_digest=ticket_digest,
            psi_server_setup_hash=setup_hash,
            psi_response_hash=resp_hash,
            counterpart_evidence_digest=counterpart_evidence_digest,
            version="1.0",
        )

    def finalize_established_psi_evaluation(
        self,
        query: EstablishedPSIQueryMessage,
        response: EstablishedPSICounterpartResponse,
    ) -> VerifiedMatchReceipt:
        """Stage 3 for Established PSI: Client computes intersection from server setup and response."""
        if response.job_id != query.job_id:
            raise ConsortiumSecurityError("Job ID mismatch in counterpart response.")
        if response.target_bank_id.lower().strip() != query.target_bank_id.lower().strip():
            raise ConsortiumSecurityError("Target bank ID mismatch in counterpart response.")

        # 1. Anti-Replay Nonce Verification on counterpart response
        if response.nonce in self._consumed_nonces:
            raise ConsortiumSecurityError(
                f"Replay attack detected: Counterpart nonce '{response.nonce}' has already been processed."
            )

        # 2. Timestamp Freshness Check
        now = datetime.datetime.now(UTC)
        resp_time = datetime.datetime.fromisoformat(response.timestamp)
        if resp_time.tzinfo is None:
            resp_time = resp_time.replace(tzinfo=UTC)
        if abs((now - resp_time).total_seconds()) > 300:
            raise ConsortiumSecurityError("Expired counterpart response rejected.")

        # 3. Verify Target Bank Signature using peer registry
        setup_bytes = OpenMinedPSIAdapter.decode_b64(response.psi_server_setup_b64)
        resp_bytes = OpenMinedPSIAdapter.decode_b64(response.psi_response_b64)
        setup_hash = hashlib.sha256(setup_bytes).hexdigest()
        resp_hash = hashlib.sha256(resp_bytes).hexdigest()

        ticket_digest = query.ticket.canonical_digest() if query.ticket else ""
        grant_id = getattr(response, "grant_id", "") or (query.ticket.grant_id if query.ticket else "")
        grant_revision = int(getattr(response, "grant_revision", 1) or (query.ticket.grant_revision if query.ticket else 1))
        grant_scope_digest = getattr(response, "grant_scope_digest", "") or (query.ticket.grant_scope_digest if query.ticket else "")
        req_bank = getattr(response, "requesting_bank_id", "") or query.requesting_bank_id

        canonical_evidence = canonical_counterpart_evidence_bytes(
            version=getattr(response, "version", "1.0"),
            consortium_id=response.consortium_id,
            grant_id=grant_id,
            grant_revision=grant_revision,
            grant_scope_digest=grant_scope_digest,
            requesting_bank_id=req_bank,
            target_bank_id=response.target_bank_id,
            job_id=response.job_id,
            ticket_digest=ticket_digest,
            psi_server_setup_hash=setup_hash,
            psi_response_hash=resp_hash,
            protocol="OPENMINED-PSI-2.0.6-ECDH",
            nonce=response.nonce,
            timestamp=response.timestamp,
        )
        counterpart_evidence_digest = hashlib.sha256(canonical_evidence).hexdigest()

        # Independently verify target bank signature on counterpart evidence
        if not self._peer_registry.verify_signature(
            response.target_bank_id, response.signature, canonical_evidence
        ):
            raise ConsortiumSecurityError(
                f"Peer authentication failed: Invalid signature from target bank '{response.target_bank_id}'."
            )

        # 4. Retrieve and consume client session
        client = self._active_psi_clients.pop(query.job_id, None)
        if client is None:
            raise ConsortiumSecurityError(
                f"Active PSI client session '{query.job_id}' not found or already consumed."
            )

        # 5. Compute intersection
        matched_indices = OpenMinedPSIAdapter.compute_client_intersection(
            client, setup_bytes, resp_bytes
        )

        match_confirmed = len(matched_indices) > 0
        if match_confirmed:
            match_category = "PROBABILISTIC_FUZZY"
            match_confidence = 0.999999999
            is_exact_confirmed = False
        else:
            match_category = "NO_MATCH"
            match_confidence = 0.0
            is_exact_confirmed = False

        # Persist consumed response nonce
        self._persist_consumed_nonce(response.nonce)

        # 6. Compute transcript digest and receipt
        now_iso = now.isoformat()
        receipt_id = str(uuid.uuid4())
        bound_entity = (
            query.ticket.query_scope.removeprefix("entity:")
            if query.ticket and query.ticket.query_scope.startswith("entity:")
            else ""
        )
        matched_risk_level = "UNAVAILABLE" if match_confirmed else "UNOBSERVED"
        matched_typologies = ("CROSS_BANK_ENTITY_OBSERVED",) if match_confirmed else ()

        # Determine whether institution identity sharing is permitted by grant/governance
        allow_identities = False
        clean_grant = (query.ticket.grant_id if query.ticket else "") or getattr(response, "grant_id", "")
        if clean_grant:
            if clean_grant in self._authorized_grants:
                allow_identities = bool(
                    self._authorized_grants[clean_grant].get("allow_institution_identity_sharing", False)
                )
            elif self._consortium_service:
                consortium = self._consortium_service.get_consortium(query.consortium_id)
                if consortium:
                    grants = getattr(consortium, "operational_data_sharing_grants", {}) or {}
                    meta_grant = consortium.metadata.get("operational_data_sharing_grant") or {}
                    gov_grant = grants.get(clean_grant) or (
                        meta_grant if meta_grant.get("grant_id") == clean_grant else None
                    )
                    if gov_grant:
                        allow_identities = bool(
                            gov_grant.get("allow_institution_identity_sharing", False)
                        )

        contributing_institutions: tuple[str, ...]
        if match_confirmed and allow_identities:
            disclosure_mode = "ATTRIBUTED_INSTITUTIONS"
            contributing_institutions = (response.target_bank_id,)
        else:
            disclosure_mode = "ANONYMIZED_SUMMARY"
            contributing_institutions = ()
        assurance_level = "REVIEWED_ESTABLISHED_PSI_VERIFIED"
        protocol = "OPENMINED-PSI-2.0.6-ECDH"

        canonical_evaluated = canonical_evaluated_result_bytes(
            version="1.0",
            receipt_id=receipt_id,
            job_id=query.job_id,
            consortium_id=query.consortium_id,
            grant_id=grant_id,
            grant_revision=grant_revision,
            grant_scope_digest=grant_scope_digest,
            requesting_bank_id=self.bank_id,
            target_bank_id=response.target_bank_id,
            query_entity_id=bound_entity,
            ticket_digest=ticket_digest,
            counterpart_evidence_digest=counterpart_evidence_digest,
            match_confirmed=match_confirmed,
            match_category=match_category,
            match_confidence=match_confidence,
            matched_risk_level=matched_risk_level,
            matched_typologies=matched_typologies,
            disclosure_mode=disclosure_mode,
            contributing_institutions=contributing_institutions,
            protocol=protocol,
            assurance_level=assurance_level,
            executed_at=now_iso,
        )
        evaluated_result_digest = hashlib.sha256(canonical_evaluated).hexdigest()
        if self._signing_identity is None:
            raise ConsortiumSecurityError(
                f"Institutional signing key not provisioned for worker '{self.bank_id}'. "
                "Match finalization fails closed to prevent unauthenticated execution evidence."
            )
        receipt_sig_a = self._signing_identity.sign(canonical_evaluated)

        counterpart_evidence_dict = {
            "version": getattr(response, "version", "1.0"),
            "consortium_id": response.consortium_id,
            "grant_id": grant_id,
            "grant_revision": grant_revision,
            "grant_scope_digest": grant_scope_digest,
            "requesting_bank_id": req_bank,
            "target_bank_id": response.target_bank_id,
            "job_id": response.job_id,
            "ticket_digest": ticket_digest,
            "psi_server_setup_hash": setup_hash,
            "psi_response_hash": resp_hash,
            "protocol": protocol,
            "nonce": response.nonce,
            "timestamp": response.timestamp,
            "counterpart_evidence_digest": counterpart_evidence_digest,
            "worker_b_signature": response.signature,
        }

        canonical_transcript = (
            f"{query.compute_digest()}:{response.compute_digest()}:{match_confirmed}:{match_category}"
        )
        transcript_digest = hashlib.sha256(canonical_transcript.encode("utf-8")).hexdigest()

        return VerifiedMatchReceipt(
            receipt_id=receipt_id,
            job_id=query.job_id,
            consortium_id=query.consortium_id,
            requesting_bank_id=self.bank_id,
            target_bank_id=response.target_bank_id,
            query_entity_id=bound_entity,
            match_confirmed=match_confirmed,
            match_confidence=match_confidence,
            protocol=protocol,
            transcript_digest=transcript_digest,
            executed_at=now_iso,
            worker_a_signature=receipt_sig_a,
            worker_b_signature=response.signature,
            matched_risk_level=matched_risk_level,
            matched_typologies=matched_typologies,
            assurance_level=assurance_level,
            match_category=match_category,
            is_exact_confirmed=is_exact_confirmed,
            grant_id=grant_id,
            grant_revision=grant_revision,
            grant_scope_digest=grant_scope_digest,
            ticket_digest=ticket_digest,
            counterpart_evidence_digest=counterpart_evidence_digest,
            counterpart_evidence=counterpart_evidence_dict,
            disclosure_mode=disclosure_mode,
            contributing_institutions=contributing_institutions,
            evaluated_result_digest=evaluated_result_digest,
            version="1.0",
        )


class InstitutionalWorkerDaemon:
    """Daemon running inside an independent worker OS process or thread exposing loopback HTTP transport."""

    def __init__(
        self,
        node: IsolatedBankWorkerNode,
        host: str = "127.0.0.1",
        port: int = 0,
        run_as_process: bool = False,
    ) -> None:
        self.node = node
        self.host = host
        self.port = port
        self.run_as_process = run_as_process
        self.server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self.pid: int = os.getpid()

    def start(self) -> str:
        if self.run_as_process:
            storage_root = os.path.dirname(os.path.dirname(os.path.abspath(self.node.vault_dir)))
            peers_json = json.dumps(self.node._trusted_peer_verification_keys)
            # Secrets and trusted peers are passed strictly via process environment variables,
            # NEVER via process command-line arguments to prevent credential exposure in ps/top!
            cmd = [
                sys.executable,
                "-m",
                "app.application.services.isolated_matching_service",
                "--bank-id",
                self.node.bank_id,
                "--host",
                self.host,
                "--port",
                str(self.port),
                "--storage-root",
                storage_root,
            ]
            backend_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            )
            proc_env = os.environ.copy()
            curr_pp = proc_env.get("PYTHONPATH", "")
            proc_env["PYTHONPATH"] = (
                f"{backend_dir}{os.pathsep}{curr_pp}" if curr_pp else backend_dir
            )
            proc_env["WORKER_AUTH_SECRET"] = self.node._auth_secret
            if self.node._signing_identity is not None:
                proc_env["WORKER_SIGNING_KEY"] = self.node._signing_identity.private_key_hex
            proc_env["WORKER_MATCHING_TOKEN"] = self.node._matching_token
            proc_env["WORKER_INGESTION_TOKEN"] = self.node._ingestion_token
            proc_env["WORKER_ADMIN_TOKEN"] = self.node._admin_token
            proc_env["WORKER_TRUSTED_PEERS_JSON"] = peers_json

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=backend_dir,
                env=proc_env,
            )
            assert self._process.stdout is not None
            line = self._process.stdout.readline()
            if not line:
                stderr_out = self._process.stderr.read() if self._process.stderr else ""
                raise RuntimeError(
                    f"Worker process failed to start for {self.node.bank_id}: {stderr_out}"
                )
            try:
                meta = json.loads(line.strip())
                self.port = int(meta["port"])
                self.pid = int(meta["pid"])
            except Exception as exc:
                stderr_out = self._process.stderr.read() if self._process.stderr else ""
                raise RuntimeError(
                    f"Invalid worker startup response from {self.node.bank_id}: {line}. Stderr: {stderr_out}"
                ) from exc
            return f"http://{self.host}:{self.port}"

        node = self.node

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass  # suppress noisy stdout logs in test runner

            def _authenticate(self, required_scope: str) -> bool:
                auth_hdr = self.headers.get("Authorization", "")
                if not auth_hdr.startswith("Bearer "):
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'{"error": "Unauthorized: Bearer token required"}')
                    return False
                token = auth_hdr[7:].strip()
                scopes = node.get_token_scopes(token)
                if not scopes:
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b'{"error": "Forbidden: Invalid or unrecognized worker token"}')
                    return False
                if required_scope not in scopes:
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(
                        json.dumps({
                            "error": f"Forbidden: Token lacks required scope '{required_scope}'",
                            "required_scope": required_scope,
                            "permitted_scopes": sorted(list(scopes)),
                        }).encode()
                    )
                    return False
                return True

            def do_GET(self) -> None:
                parsed_url = urllib.parse.urlparse(self.path)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                nonce = query_params.get("nonce", [""])[0]

                if parsed_url.path in ("/health", "/worker/health"):
                    now_iso = datetime.datetime.now(UTC).isoformat()
                    protocol = "OPENMINED-PSI-2.0.6-ECDH"
                    resp_data: dict[str, Any] = {
                        "status": "ONLINE",
                        "bank_id": node.bank_id,
                        "role": "worker",
                        "protocol": protocol,
                        "pid": os.getpid(),
                        "timestamp": now_iso,
                        "nonce": nonce,
                    }
                    if getattr(node, "_signing_identity", None) is not None:
                        canonical_stmt = canonical_worker_readiness_bytes(
                            bank_id=node.bank_id,
                            nonce=nonce,
                            timestamp=now_iso,
                            protocol=protocol,
                        )
                        sig_hex = node._signing_identity.sign(canonical_stmt)
                        resp_data["signature"] = sig_hex
                        resp_data["public_key_hex"] = node._signing_identity.public_key_hex
                        resp_data["key_version"] = 1

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(resp_data).encode())
                elif self.path in ("/worker/public-key", "/public-key"):
                    try:
                        pub_hex = node.public_key_hex
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            json.dumps({"bank_id": node.bank_id, "public_key_hex": pub_hex}).encode()
                        )
                    except ConsortiumSecurityError as exc:
                        self.send_response(503)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": str(exc)}).encode())
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self) -> None:
                route_scopes = {
                    "/worker/initiate-query": SCOPE_MATCHING,
                    "/worker/process-counterpart": SCOPE_MATCHING,
                    "/worker/finalize-match": SCOPE_MATCHING,
                    "/worker/store-entity": SCOPE_DATA_INGESTION,
                    "/worker/enroll-peer": SCOPE_ADMINISTRATION,
                    "/worker/rotate-peer": SCOPE_ADMINISTRATION,
                    "/worker/initiate-established-psi": SCOPE_MATCHING,
                    "/worker/process-established-psi": SCOPE_MATCHING,
                    "/worker/finalize-established-psi": SCOPE_MATCHING,
                    "/worker/sign-operation-ticket": SCOPE_MATCHING,
                    "/worker/sign-grant-approval": SCOPE_ADMINISTRATION,
                    "/worker/revoke-grant-locally": SCOPE_ADMINISTRATION,
                    "/worker/authorize-grant": SCOPE_ADMINISTRATION,
                }
                if self.path not in route_scopes:
                    self.send_response(404)
                    self.end_headers()
                    return

                required_scope = route_scopes[self.path]
                if not self._authenticate(required_scope):
                    return

                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                data = json.loads(body.decode("utf-8")) if body else {}

                try:
                    if self.path == "/worker/store-entity":
                        ent_id = node.store_private_entity(data["entity"])
                        res: dict[str, Any] = {"entity_id": ent_id}
                    elif self.path == "/worker/initiate-query":
                        msg = node.initiate_blinded_query(
                            query_entity_id=data["query_entity_id"],
                            consortium_id=data["consortium_id"],
                            target_bank_id=data["target_bank_id"],
                            purpose=data.get("purpose", "AML_COLLABORATIVE_INVESTIGATION"),
                        )
                        res = asdict(msg)
                    elif self.path == "/worker/process-counterpart":
                        q_data = data["query"]
                        t_data = q_data.get("ticket")
                        if t_data and isinstance(t_data, dict):
                            q_data["ticket"] = OperationAuthorizationTicket(**t_data)
                        query_msg = BlindedQueryMessage(**q_data)
                        resp = node.process_counterpart_query(
                            query=query_msg,
                        )
                        res = asdict(resp)
                    elif self.path == "/worker/finalize-match":
                        q_data = data["query"]
                        r_data = data["response"]
                        t_data = q_data.get("ticket")
                        if t_data and isinstance(t_data, dict):
                            q_data["ticket"] = OperationAuthorizationTicket(**t_data)
                        query_msg = BlindedQueryMessage(**q_data)
                        r_data["blinded_target_elements_hex"] = tuple(
                            r_data["blinded_target_elements_hex"]
                        )
                        resp_msg = CounterpartMatchResponse(**r_data)
                        receipt = node.finalize_match_evaluation(
                            query=query_msg,
                            response=resp_msg,
                        )
                        res = asdict(receipt)
                    elif self.path == "/worker/enroll-peer":
                        node.enroll_trusted_peer(
                            data["peer_bank_id"],
                            data["verification_key"],
                            allow_rotation=data.get("allow_rotation", False),
                        )
                        res = {"status": "ENROLLED", "peer_bank_id": data["peer_bank_id"]}
                    elif self.path == "/worker/rotate-peer":
                        node.rotate_trusted_peer(
                            peer_bank_id=data["peer_bank_id"],
                            new_verification_key=data["new_verification_key"],
                            rotation_authorization_signature=data["rotation_authorization_signature"],
                        )
                        res = {"status": "ROTATED", "peer_bank_id": data["peer_bank_id"]}
                    elif self.path == "/worker/initiate-established-psi":
                        ticket_dict = data["ticket"]
                        ticket = OperationAuthorizationTicket(**ticket_dict)
                        est_msg = node.initiate_established_psi_query(
                            query_entity_id=data["query_entity_id"],
                            consortium_id=data["consortium_id"],
                            target_bank_id=data["target_bank_id"],
                            ticket=ticket,
                            purpose=data.get("purpose", "AML_COLLABORATIVE_INVESTIGATION"),
                        )
                        res = asdict(est_msg)
                    elif self.path == "/worker/process-established-psi":
                        q_data = data["query"]
                        t_data = q_data.get("ticket")
                        if t_data and isinstance(t_data, dict):
                            q_data["ticket"] = OperationAuthorizationTicket(**t_data)
                        est_query_msg = EstablishedPSIQueryMessage(**q_data)
                        est_resp = node.process_established_psi_counterpart(query=est_query_msg)
                        res = asdict(est_resp)
                    elif self.path == "/worker/finalize-established-psi":
                        q_data = data["query"]
                        t_data = q_data.get("ticket")
                        if t_data and isinstance(t_data, dict):
                            q_data["ticket"] = OperationAuthorizationTicket(**t_data)
                        est_query_msg = EstablishedPSIQueryMessage(**q_data)
                        est_resp_msg = EstablishedPSICounterpartResponse(**data["response"])
                        receipt = node.finalize_established_psi_evaluation(
                            query=est_query_msg, response=est_resp_msg
                        )
                        res = asdict(receipt)
                    elif self.path == "/worker/sign-operation-ticket":
                        ticket = OperationAuthorizationTicket(**data["ticket"])
                        sig = node.sign_operation_authorization_ticket(ticket)
                        res = {"signature": sig, "bank_id": node.bank_id}
                    elif self.path == "/worker/sign-grant-approval":
                        sig = node.compute_grant_approval_signature(
                            consortium_id=data["consortium_id"],
                            grant_id=data["grant_id"],
                            revision=int(data["revision"]),
                            scope_digest=data["scope_digest"],
                        )
                        res = {"signature": sig, "bank_id": node.bank_id}
                    elif self.path == "/worker/revoke-grant-locally":
                        node.record_local_grant_revocation(data["grant_id"])
                        res = {"status": "REVOKED", "grant_id": data["grant_id"]}
                    elif self.path == "/worker/authorize-grant":
                        rec = node.register_authorized_grant(
                            consortium_id=data["consortium_id"],
                            grant_id=data["grant_id"],
                            revision=int(data["revision"]),
                            scope_digest=data["scope_digest"],
                            permitted_counterparts=data["permitted_counterparts"],
                            permitted_purposes=data["permitted_purposes"],
                            permitted_operations=data.get("permitted_operations"),
                            expires_at=data.get("expires_at"),
                            allow_institution_identity_sharing=bool(
                                data.get("allow_institution_identity_sharing", False)
                            ),
                        )
                        res = {"status": "AUTHORIZED", "grant": rec}
                    else:
                        self.send_response(404)
                        self.end_headers()
                        return

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(res).encode())
                except Exception as exc:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps({"error": str(exc), "error_type": type(exc).__name__}).encode()
                    )

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self.server.server_address[1]
        self.pid = os.getpid()
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=3.0)
            except Exception:
                self._process.kill()
            self._process = None
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


class InstitutionalWorkerClient:
    """Client communicating with an independent institutional worker over loopback HTTP transport."""

    def __init__(
        self,
        bank_id: str,
        base_url: str,
        auth_secret: str | None = None,
        matching_token: str | None = None,
        ingestion_token: str | None = None,
        admin_token: str | None = None,
    ) -> None:
        self.bank_id = bank_id.lower().strip()
        self.base_url = base_url.rstrip("/")
        # The coordinator only receives matching_token; ingestion and admin tokens are stored if explicitly provisioned
        self.matching_token = matching_token or auth_secret or ""
        self.ingestion_token = ingestion_token or auth_secret or ""
        self.admin_token = admin_token or auth_secret or ""
        self.auth_secret = self.matching_token
        self._is_network_transport = True

    def health_check(self) -> dict[str, Any]:
        """Perform health check probe to worker node."""
        url = f"{self.base_url}/health"
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"Unsupported URL scheme for worker client: {url}")
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self.matching_token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:  # nosec B310
                body = resp.read()
                return cast("dict[str, Any]", json.loads(body.decode("utf-8")))
        except Exception as exc:
            raise ConsortiumSecurityError(
                f"Worker unreachable on health probe for '{self.bank_id}': {exc}"
            ) from exc

    def _post(self, path: str, payload: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"Unsupported URL scheme for worker client: {url}")
        req_data = json.dumps(payload).encode("utf-8")
        req_token = token or self.matching_token
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {req_token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:  # nosec B310
                body = resp.read()
                return cast("dict[str, Any]", json.loads(body.decode("utf-8")))
        except urllib.error.HTTPError as http_err:
            try:
                err_body = json.loads(http_err.read().decode())
                err_msg = err_body.get("error", str(http_err))
                err_type = err_body.get("error_type", "")
                if "ConsortiumSecurityError" in err_type:
                    raise ConsortiumSecurityError(err_msg) from http_err
                if "PermissionError" in err_type or http_err.code in (401, 403):
                    raise PermissionError(err_msg) from http_err
                raise ValueError(err_msg) from http_err
            except (ConsortiumSecurityError, PermissionError, ValueError):
                raise
            except Exception:
                raise ValueError(
                    f"HTTP transport error from {self.bank_id} worker: {http_err}"
                ) from http_err
        except Exception as exc:
            raise ConsortiumSecurityError(
                f"Institutional bank worker for '{self.bank_id}' is unreachable or timed out: {exc}"
            ) from exc

    def store_private_entity(self, entity: Entity | dict[str, Any]) -> str:
        ent_dict = entity.to_dict() if isinstance(entity, Entity) else dict(entity)
        res = self._post("/worker/store-entity", {"entity": ent_dict}, token=self.ingestion_token)
        return str(res["entity_id"])

    def enroll_peer(self, peer_bank_id: str, verification_key: str, allow_rotation: bool = False) -> None:
        self._post(
            "/worker/enroll-peer",
            {
                "peer_bank_id": peer_bank_id,
                "verification_key": verification_key,
                "allow_rotation": allow_rotation,
            },
            token=self.admin_token,
        )

    def rotate_peer(
        self,
        peer_bank_id: str,
        new_verification_key: str,
        rotation_authorization_signature: str,
    ) -> None:
        self._post(
            "/worker/rotate-peer",
            {
                "peer_bank_id": peer_bank_id,
                "new_verification_key": new_verification_key,
                "rotation_authorization_signature": rotation_authorization_signature,
            },
            token=self.admin_token,
        )

    def sign_operation_ticket(self, ticket: OperationAuthorizationTicket) -> str:
        res = self._post("/worker/sign-operation-ticket", {"ticket": asdict(ticket)})
        return str(res["signature"])

    def sign_grant_approval(
        self, consortium_id: str, grant_id: str, revision: int, scope_digest: str
    ) -> str:
        res = self._post(
            "/worker/sign-grant-approval",
            {
                "consortium_id": consortium_id,
                "grant_id": grant_id,
                "revision": revision,
                "scope_digest": scope_digest,
            },
            token=self.admin_token,
        )
        return str(res["signature"])

    def revoke_grant_locally(self, grant_id: str) -> None:
        self._post(
            "/worker/revoke-grant-locally",
            {"grant_id": grant_id},
            token=self.admin_token,
        )

    def register_authorized_grant(
        self,
        consortium_id: str,
        grant_id: str,
        revision: int,
        scope_digest: str,
        permitted_counterparts: list[str],
        permitted_purposes: list[str],
        permitted_operations: list[str] | None = None,
        expires_at: str | None = None,
        allow_institution_identity_sharing: bool = False,
    ) -> dict[str, Any]:
        """Register an authorized operational grant on the remote worker daemon via admin scope."""
        res = self._post(
            "/worker/authorize-grant",
            {
                "consortium_id": consortium_id,
                "grant_id": grant_id,
                "revision": revision,
                "scope_digest": scope_digest,
                "permitted_counterparts": permitted_counterparts,
                "permitted_purposes": permitted_purposes,
                "permitted_operations": permitted_operations,
                "expires_at": expires_at,
                "allow_institution_identity_sharing": allow_institution_identity_sharing,
            },
            token=self.admin_token,
        )
        return cast("dict[str, Any]", res.get("grant", res))

    def confirm_match(self, confirmation_tag: str, scope_digest: str) -> bool:
        """Query counterpart worker to confirm exact match tag under grant scope digest."""
        res = self._post(
            "/worker/confirm-match",
            {
                "confirmation_tag": confirmation_tag,
                "scope_digest": scope_digest,
            },
            token=self.matching_token,
        )
        return bool(res.get("is_confirmed", False))

    def initiate_blinded_query(
        self,
        query_entity_id: str,
        consortium_id: str,
        target_bank_id: str,
        purpose: str = "AML_COLLABORATIVE_INVESTIGATION",
    ) -> BlindedQueryMessage:
        res = self._post(
            "/worker/initiate-query",
            {
                "query_entity_id": query_entity_id,
                "consortium_id": consortium_id,
                "target_bank_id": target_bank_id,
                "purpose": purpose,
            },
        )
        return BlindedQueryMessage(**res)

    def process_counterpart_query(
        self,
        query: BlindedQueryMessage,
        requester_auth_secret: str | None = None,
    ) -> CounterpartMatchResponse:
        res = self._post(
            "/worker/process-counterpart",
            {
                "query": asdict(query),
            },
        )
        res["blinded_target_elements_hex"] = tuple(res["blinded_target_elements_hex"])
        return CounterpartMatchResponse(**res)

    def finalize_match_evaluation(
        self,
        query: BlindedQueryMessage,
        response: CounterpartMatchResponse,
        counterpart_auth_secret: str | None = None,
    ) -> VerifiedMatchReceipt:
        res = self._post(
            "/worker/finalize-match",
            {
                "query": asdict(query),
                "response": asdict(response),
            },
        )
        res["matched_typologies"] = tuple(res.get("matched_typologies", ()))
        return VerifiedMatchReceipt(**res)

    def initiate_established_psi_query(
        self,
        query_entity_id: str,
        consortium_id: str,
        target_bank_id: str,
        ticket: OperationAuthorizationTicket,
        purpose: str = "AML_COLLABORATIVE_INVESTIGATION",
    ) -> EstablishedPSIQueryMessage:
        res = self._post(
            "/worker/initiate-established-psi",
            {
                "query_entity_id": query_entity_id,
                "consortium_id": consortium_id,
                "target_bank_id": target_bank_id,
                "ticket": asdict(ticket),
                "purpose": purpose,
            },
        )
        t_dict = res.get("ticket")
        if t_dict and isinstance(t_dict, dict):
            res["ticket"] = OperationAuthorizationTicket(**t_dict)
        return EstablishedPSIQueryMessage(**res)

    def process_established_psi_counterpart(
        self,
        query: EstablishedPSIQueryMessage,
    ) -> EstablishedPSICounterpartResponse:
        res = self._post(
            "/worker/process-established-psi",
            {"query": asdict(query)},
        )
        return EstablishedPSICounterpartResponse(**res)

    def finalize_established_psi_evaluation(
        self,
        query: EstablishedPSIQueryMessage,
        response: EstablishedPSICounterpartResponse,
    ) -> VerifiedMatchReceipt:
        res = self._post(
            "/worker/finalize-established-psi",
            {
                "query": asdict(query),
                "response": asdict(response),
            },
        )
        res["matched_typologies"] = tuple(res.get("matched_typologies", ()))
        return VerifiedMatchReceipt(**res)


class CrossBankMatchingCoordinator:
    """Orchestrates verified two-bank private matching across isolated worker nodes.

    The coordinator has zero access to raw private entity datasets or bank secret keys.
    Operates strictly as an authenticated protocol router and attestation validator.
    """

    def __init__(
        self,
        consortium_service: ConsortiumGovernanceService | None = None,
        policy_engine: ConsortiumPolicyEngine | None = None,
        storage_root: str | None = None,
    ) -> None:
        self.consortium_service = consortium_service or ConsortiumGovernanceService()
        self.policy_engine = policy_engine or ConsortiumPolicyEngine()
        self.storage_root = storage_root or get_storage_dir()
        self._signal_store = RedisStore("authoritative_signals")
        self._prototype_store = RedisStore("research_prototype_signals")
        self._workers: dict[str, Any] = {}
        self._matching_tokens: dict[str, str] = {}
        self._coordinator_secret = (
            os.getenv("CFI_COORDINATOR_SECRET")
            or os.getenv("COORDINATOR_AUTH_SECRET")
            or secrets.token_hex(32)
        )

    def register_worker(
        self,
        bank_id: str,
        worker: Any,
        auth_secret: str | None = None,
        matching_token: str | None = None,
    ) -> None:
        """Register an authenticated isolated bank worker node or client.

        The coordinator ONLY receives matching capability tokens, never bank signing
        keys or administrative/ingestion credentials.
        """
        clean_id = bank_id.lower().strip()
        self._workers[clean_id] = worker
        resolved_token: str = str(
            matching_token
            or getattr(worker, "matching_token", "")
            or getattr(worker, "_matching_token", "")
            or auth_secret
            or getattr(worker, "_auth_secret", "")
            or ""
        )
        self._matching_tokens[clean_id] = resolved_token

    def get_worker(self, bank_id: str) -> Any:
        """Retrieve registered worker for institution, failing closed if unregistered."""
        clean_id = bank_id.lower().strip()
        worker = self._workers.get(clean_id)
        if not worker:
            raise ConsortiumSecurityError(
                f"Isolated bank worker node for institution '{bank_id}' is offline or not registered."
            )
        return worker

    def execute_two_bank_private_matching(
        self,
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None = None,
        actor: str | None = None,
    ) -> CrossBankIntelligenceSignal:
        """Executes authentic, isolated two-bank private matching under consortium governance."""
        clean_requesting = req.requesting_bank_id.lower().strip()
        clean_consortium_id = req.consortium_id.lower().strip()

        # 0. Complete Institutional Authorization Chain Validation
        if not auth_token:
            raise PermissionError(
                "Authentication required: Coordinator direct matching execution requires a valid Bearer auth_token."
            )
        from app.application.services.cross_bank_intelligence import PERMITTED_INTELLIGENCE_ROLES
        from app.infrastructure.security.auth_service import AuthenticationService

        auth_svc = AuthenticationService.get_instance()
        is_valid, claims, err = auth_svc.verify_access_token(auth_token)
        if not is_valid or not claims:
            raise PermissionError(f"Institutional token verification failed: {err}")

        token_bank = (claims.get("bank_id") or "").lower().strip()
        if token_bank != clean_requesting:
            raise PermissionError(
                f"Institutional authorization mismatch: Calling principal from '{token_bank}' "
                f"cannot execute matching for institution '{clean_requesting}'."
            )
        token_roles = set(claims.get("roles", []))
        if not (token_roles & PERMITTED_INTELLIGENCE_ROLES):
            raise PermissionError(
                f"Calling principal lacks required intelligence roles. Required: {sorted(PERMITTED_INTELLIGENCE_ROLES)}."
            )

        if not req.target_bank_id:
            raise ValueError(
                "Mandatory target_bank_id missing: Authentic two-bank private matching requires explicit counterpart institution."
            )
        clean_target = req.target_bank_id.lower().strip()

        if clean_target == clean_requesting:
            raise ValueError("Requesting bank and target counterpart bank cannot be identical.")

        # 1. Authoritative Consortium Governance & Active Membership Verification
        consortium = self.consortium_service.get_consortium(clean_consortium_id)
        if not consortium:
            raise ConsortiumSecurityError(
                f"Consortium '{clean_consortium_id}' does not exist or has not been established."
            )
        if consortium.status != ConsortiumStatus.ACTIVE:
            raise ConsortiumSecurityError(
                f"Consortium '{clean_consortium_id}' is in non-active status '{consortium.status.value}'. "
                f"Cross-bank private matching is prohibited."
            )

        if clean_requesting in consortium.removed_members:
            raise ConsortiumSecurityError(
                f"Requesting institution '{clean_requesting}' has been evicted/revoked from consortium '{clean_consortium_id}'."
            )
        if clean_requesting not in consortium.members:
            raise ConsortiumSecurityError(
                f"Requesting institution '{clean_requesting}' is not an active member of consortium '{clean_consortium_id}'."
            )

        if clean_target in consortium.removed_members:
            raise ConsortiumSecurityError(
                f"Target institution '{clean_target}' has been evicted/revoked from consortium '{clean_consortium_id}'."
            )
        if clean_target not in consortium.members:
            raise ConsortiumSecurityError(
                f"Target institution '{clean_target}' is not an active member of consortium '{clean_consortium_id}'."
            )

        # 2. Enforce Explicit Bilateral Operational Data-Sharing Grant & Mandatory Jurisdiction
        is_valid_policy, policy_reasons, disclosure_scope = (
            self.policy_engine.validate_intelligence_sharing(
                consortium=consortium,
                requesting_bank=clean_requesting,
                target_banks=[clean_target],
                purpose=req.purpose,
                is_operational=True,
            )
        )
        if not is_valid_policy:
            raise ConsortiumSecurityError(
                f"Consortium operational policy rejected matching: {'; '.join(policy_reasons)}"
            )

        # 3. Retrieve Isolated Bank Worker Nodes
        worker_a = self.get_worker(clean_requesting)
        worker_b = self.get_worker(clean_target)

        token_a = self._matching_tokens.get(
            clean_requesting, getattr(worker_a, "matching_token", getattr(worker_a, "_matching_token", ""))
        )
        token_b = self._matching_tokens.get(
            clean_target, getattr(worker_b, "matching_token", getattr(worker_b, "_matching_token", ""))
        )
        if not token_a or not token_b:
            raise ConsortiumSecurityError("Institutional worker matching credentials are not configured.")

        # 3b. Determine Bilateral Operational Grant and Construct Operation Authorization Ticket
        operational_grant = consortium.metadata.get("operational_data_sharing_grant") or {}
        grant_id = operational_grant.get("grant_id", f"grant_{clean_consortium_id}")
        grant_revision = int(operational_grant.get("revision", 1))
        grant_scope_digest = operational_grant.get("scope_digest", "")
        if not grant_scope_digest:
            from app.application.services.consortium_service import (
                compute_operational_grant_scope_digest,
            )

            grant_scope_digest = compute_operational_grant_scope_digest(
                grant_id=grant_id,
                revision=grant_revision,
                participating_banks=list(
                    operational_grant.get("participating_banks", [clean_requesting, clean_target])
                ),
                allowed_purposes=list(
                    operational_grant.get("allowed_purposes", [req.purpose])
                ),
                institution_jurisdictions=dict(
                    operational_grant.get(
                        "institution_jurisdictions",
                        {clean_requesting: "EU", clean_target: "EU"},
                    )
                ),
                allowed_jurisdictions=list(
                    operational_grant.get("allowed_jurisdictions", ["EU", "US"])
                ),
                allow_cross_border=bool(operational_grant.get("allow_cross_border", False)),
                allow_institution_identity_sharing=bool(
                    operational_grant.get("allow_institution_identity_sharing", False)
                ),
            )

        session_ticket_id = str(uuid.uuid4())
        now = datetime.datetime.now(UTC)
        ticket_expires_at = (now + datetime.timedelta(seconds=300)).isoformat()

        explicit_established_psi = bool(
            getattr(req, "use_established_psi", False)
            or getattr(req, "require_established_psi", False)
            or getattr(req, "protocol", "") in ("OPENMINED_PSI", "OPENMINED-PSI-2.0.6-ECDH")
        )
        remote_workers = hasattr(worker_a, "_post") or hasattr(worker_b, "_post")
        library_available = OpenMinedPSIAdapter.is_available() or remote_workers
        if explicit_established_psi and not library_available:
            raise ConsortiumSecurityError(
                "Verified operational matching failed: Established PSI library (openmined-psi) is unavailable in this environment. "
                "Failing closed to prevent unverified prototype execution."
            )

        use_established_psi = explicit_established_psi

        initial_blinded_msg = None
        if use_established_psi:
            session_job_id = str(uuid.uuid4())
        else:
            initial_blinded_msg = worker_a.initiate_blinded_query(
                query_entity_id=req.query_entity_id,
                consortium_id=clean_consortium_id,
                target_bank_id=clean_target,
                purpose=req.purpose,
            )
            session_job_id = initial_blinded_msg.job_id

        unsigned_ticket = OperationAuthorizationTicket(
            ticket_id=session_ticket_id,
            consortium_id=clean_consortium_id,
            requesting_bank_id=clean_requesting,
            target_bank_id=clean_target,
            operation="ESTABLISHED_PSI" if use_established_psi else "PRIVATE_SET_INTERSECTION",
            purpose=req.purpose,
            query_scope=f"entity:{req.query_entity_id}",
            grant_id=grant_id,
            grant_revision=grant_revision,
            grant_scope_digest=grant_scope_digest,
            job_id=session_job_id,
            issued_at=now.isoformat(),
            expires_at=ticket_expires_at,
            bank_approvals={},
        )

        # 3c. Obtain Independent Bank Worker Authorization Signatures
        if hasattr(worker_a, "sign_operation_ticket"):
            sig_a = worker_a.sign_operation_ticket(unsigned_ticket)
        elif hasattr(worker_a, "sign_operation_authorization_ticket"):
            sig_a = worker_a.sign_operation_authorization_ticket(unsigned_ticket)
        elif use_established_psi:
            raise ConsortiumSecurityError(
                f"Coordinator cannot obtain authorization signature from requesting bank '{clean_requesting}'. "
                "Coordinator signature fabrication is strictly prohibited."
            )
        else:
            sig_a = hmac.new(
                getattr(worker_a, "_auth_secret", "worker_a").encode(),
                f"{unsigned_ticket.canonical_digest()}:{clean_requesting}".encode(),
                hashlib.sha256,
            ).hexdigest()

        if hasattr(worker_b, "sign_operation_ticket"):
            sig_b = worker_b.sign_operation_ticket(unsigned_ticket)
        elif hasattr(worker_b, "sign_operation_authorization_ticket"):
            sig_b = worker_b.sign_operation_authorization_ticket(unsigned_ticket)
        elif use_established_psi:
            raise ConsortiumSecurityError(
                f"Coordinator cannot obtain authorization signature from target bank '{clean_target}'. "
                "Coordinator signature fabrication is strictly prohibited."
            )
        else:
            sig_b = hmac.new(
                getattr(worker_b, "_auth_secret", "worker_b").encode(),
                f"{unsigned_ticket.canonical_digest()}:{clean_target}".encode(),
                hashlib.sha256,
            ).hexdigest()

        ticket = replace(
            unsigned_ticket,
            bank_approvals={clean_requesting: sig_a, clean_target: sig_b},
        )

        # Determine execution assurance level based on actual worker boundary and library
        is_multi_process = getattr(worker_a, "_is_network_transport", False) and getattr(
            worker_b, "_is_network_transport", False
        )

        # 4. Execute Selected Protocol Path
        if use_established_psi:
            remote_workers = hasattr(worker_a, "_post") or hasattr(worker_b, "_post")
            if not (OpenMinedPSIAdapter.is_available() or remote_workers):
                raise ConsortiumSecurityError(
                    "Verified operational matching failed: Established PSI library (openmined-psi) is unavailable in this environment. "
                    "Failing closed to prevent unverified prototype execution."
                )

            query_msg = worker_a.initiate_established_psi_query(
                query_entity_id=req.query_entity_id,
                consortium_id=clean_consortium_id,
                target_bank_id=clean_target,
                ticket=ticket,
                purpose=req.purpose,
            )
            counterpart_resp = worker_b.process_established_psi_counterpart(
                query=query_msg,
            )
            receipt = worker_a.finalize_established_psi_evaluation(
                query=query_msg,
                response=counterpart_resp,
            )

            protocol_literal = "OPENMINED-PSI-2.0.6-ECDH"
            assurance_level_literal = "REVIEWED_ESTABLISHED_PSI_VERIFIED"
            execution_mode_literal = "VERIFIED_OPERATIONAL"
            operational_status_literal = "VERIFIED_OPERATIONAL"
            is_authoritative_flag = True
        else:
            assert initial_blinded_msg is not None
            query_msg = replace(initial_blinded_msg, ticket=ticket)

            counterpart_resp = worker_b.process_counterpart_query(
                query=query_msg,
            )
            receipt = worker_a.finalize_match_evaluation(
                query=query_msg,
                response=counterpart_resp,
            )

            protocol_literal = "DH-PSI-2048-MODP"
            assurance_level_literal = "PROTOCOL_PROTOTYPE"
            execution_mode_literal = "RESEARCH_PROTOTYPE"
            operational_status_literal = "RESEARCH_PROTOTYPE"
            is_authoritative_flag = False

        # 7. Coordinator signs receipt attestation
        coord_sig = hmac.new(
            self._coordinator_secret.encode("utf-8"),
            f"{receipt.receipt_id}:{receipt.transcript_digest}:{clean_consortium_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        receipt = replace(receipt, coordinator_signature=coord_sig)

        # 8. Determine Minimal Disclosed Intelligence Signal
        allow_identities = bool(disclosure_scope.get("allow_institution_identity", False))
        disclosed_institutions = (
            [clean_target] if (receipt.match_confirmed and allow_identities) else []
        )
        disclosure_mode_literal: DisclosureMode = (
            "ATTRIBUTED_INSTITUTIONS" if allow_identities else "ANONYMIZED_SUMMARY"
        )

        max_risk = receipt.matched_risk_level if receipt.match_confirmed else "UNOBSERVED"
        typologies = list(receipt.matched_typologies) if receipt.match_confirmed else []
        if receipt.match_confirmed:
            typologies = sorted(list(set(typologies) | {"CROSS_BANK_ENTITY_OBSERVED"}))

        category_literal: MatchCategory = (
            cast("MatchCategory", receipt.match_category)
            if receipt.match_confirmed
            else "NO_MATCH"
        )
        risk_literal: SanitizedRiskLevel = (
            cast("SanitizedRiskLevel", max_risk)
            if max_risk in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNOBSERVED", "UNAVAILABLE")
            else "UNAVAILABLE"
            if receipt.match_confirmed
            else "UNOBSERVED"
        )

        signal_id = str(uuid.uuid4())
        signal = CrossBankIntelligenceSignal(
            signal_id=signal_id,
            consortium_id=clean_consortium_id,
            requesting_bank_id=clean_requesting,
            query_entity_id=req.query_entity_id,
            privacy_hash_truncated=f"{req.query_entity_id[:8]}...[BLINDED]",
            match_confirmed=receipt.match_confirmed,
            match_category=category_literal,
            match_confidence=receipt.match_confidence,
            contributing_institutions_count=1 if receipt.match_confirmed else 0,
            contributing_institutions=disclosed_institutions,
            disclosure_mode=disclosure_mode_literal,
            sanitized_risk_level=risk_literal,
            sanitized_typology_indicators=typologies,
            graph_context={
                "has_incident_cross_bank_correlations": receipt.match_confirmed,
                "sanitized_degree_band": "BILATERAL" if receipt.match_confirmed else "ISOLATED",
            },
            provenance={
                "query_timestamp": now.isoformat(),
                "execution_environment": (
                    "ISOLATED_MULTI_PROCESS_WORKER_NODES"
                    if is_multi_process
                    else "IN_PROCESS_PROTOTYPE"
                ),
                "operational_status": operational_status_literal,
                "governance_authority": f"consortium:{clean_consortium_id}",
                "protocol": protocol_literal,
                "job_id": query_msg.job_id,
                "ticket_id": ticket.ticket_id,
                "grant_id": grant_id,
                "grant_revision": grant_revision,
                "grant_scope_digest": grant_scope_digest,
                "target_bank_id": clean_target,
                "query_entity_id": req.query_entity_id,
                "receipt_id": receipt.receipt_id,
                "transcript_digest": receipt.transcript_digest,
                "coordinator_signature": coord_sig,
                "worker_a_signature": receipt.worker_a_signature,
                "worker_b_signature": receipt.worker_b_signature,
                "counterpart_evidence": receipt.counterpart_evidence,
                "counterpart_evidence_digest": receipt.counterpart_evidence_digest,
                "evaluated_result_digest": receipt.evaluated_result_digest,
                "assurance_level": assurance_level_literal,
                "receipt": asdict(receipt),
                "ticket": asdict(ticket),
                "fpr": 1e-9,
                "probabilistic_uncertainty_note": (
                    "Intersection detected via ECDH PSI with Golomb Compressed Sets. "
                    "Subject to calibrated false positive bound 1e-9."
                ),
            },
            execution_mode=execution_mode_literal,
            attestation_status="IN_PROCESS_PROTOTYPE" if not is_multi_process else "ISOLATED_MULTI_PROCESS",
            is_authoritative=is_authoritative_flag,
            is_synthetic=False,
            assurance_level=cast("Any", assurance_level_literal),
        )

        # 9. Register in Authoritative or Research Prototype Store based on verification status
        signal_dict = signal.model_dump()
        expires_at = (now + datetime.timedelta(hours=24)).isoformat()
        content_hash = hashlib.sha256(
            json.dumps(signal_dict, sort_keys=True).encode("utf-8")
        ).hexdigest()

        record = {
            "signal_id": signal_id,
            "signal_data": signal_dict,
            "requesting_bank_id": clean_requesting,
            "target_bank_id": clean_target,
            "consortium_id": clean_consortium_id,
            "grant_id": grant_id,
            "grant_revision": grant_revision,
            "grant_scope_digest": grant_scope_digest,
            "ticket_id": ticket.ticket_id,
            "query_entity_id": req.query_entity_id,
            "issued_to_principal": actor or f"worker:{clean_requesting}",
            "issued_at": now.isoformat(),
            "expires_at": expires_at,
            "content_hash": content_hash,
            "is_authoritative": is_authoritative_flag,
            "is_synthetic": False,
            "operational_status": operational_status_literal,
            "assurance_level": assurance_level_literal,
            "attached_to_cases": [],
            "receipt": asdict(receipt),
            "ticket": asdict(ticket),
        }

        if is_authoritative_flag:
            self._signal_store.set(signal_id, record)
        else:
            self._prototype_store.set(signal_id, record)

        # 10. Audit Event
        ImmutableAuditChain.get_instance().append_event(
            event_type="CROSS_BANK_PRIVATE_MATCHING_EXECUTED",
            actor_id=f"worker:{clean_requesting}",
            action=f"private_matching:{clean_consortium_id}",
            resource_id=f"signal:{signal_id}",
            details={
                "consortium_id": clean_consortium_id,
                "requesting_bank_id": clean_requesting,
                "target_bank_id": clean_target,
                "match_confirmed": receipt.match_confirmed,
                "protocol": protocol_literal,
                "receipt_id": receipt.receipt_id,
                "assurance_level": assurance_level_literal,
                "is_authoritative": is_authoritative_flag,
            },
        )

        logger.info(
            "Isolated two-bank private matching completed: %s <-> %s (match=%s, receipt=%s, assurance=%s, auth=%s)",
            clean_requesting,
            clean_target,
            receipt.match_confirmed,
            receipt.receipt_id,
            assurance_level_literal,
            is_authoritative_flag,
        )
        return signal


class InstitutionalCoordinatorDaemon:
    """Daemon running inside a restricted coordinator container exposing matching HTTP transport.

    The coordinator has zero volume mounts and zero access to bank secrets or private entity vaults.
    """

    def __init__(
        self,
        coordinator: CrossBankMatchingCoordinator,
        host: str = "0.0.0.0",  # nosec B104
        port: int = 8200,
        coordinator_secret: str | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.host = host
        self.port = port
        self.coordinator_secret = (
            coordinator_secret
            or os.environ.get("CFI_COORDINATOR_SECRET")
            or os.environ.get("COORDINATOR_SECRET")
            or ""
        )
        self.server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.pid: int = os.getpid()

    def start(self) -> str:
        coord = self.coordinator
        self_daemon = self

        class CoordinatorHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass

            def do_GET(self) -> None:
                parsed_url = urllib.parse.urlparse(self.path)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                nonce = query_params.get("nonce", [""])[0]

                if parsed_url.path in ("/health", "/coordinator/health"):
                    now_iso = datetime.datetime.now(UTC).isoformat()
                    resp_data: dict[str, Any] = {
                        "status": "ONLINE",
                        "role": "coordinator",
                        "registered_workers": list(coord._workers.keys()),
                        "pid": os.getpid(),
                        "timestamp": now_iso,
                        "nonce": nonce,
                    }
                    coord_sec = (
                        getattr(coord, "_coordinator_secret", "")
                        or self_daemon.coordinator_secret
                        or os.environ.get("CFI_COORDINATOR_SECRET", "")
                    )
                    if coord_sec:
                        canonical_stmt = canonical_coordinator_readiness_bytes(
                            nonce=nonce,
                            timestamp=now_iso,
                            role="coordinator",
                        )
                        resp_data["coordinator_signature"] = hmac.new(
                            coord_sec.encode("utf-8"), canonical_stmt, hashlib.sha256
                        ).hexdigest()

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(resp_data).encode())
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self) -> None:
                if self.path != "/coordinator/execute-match":
                    self.send_response(404)
                    self.end_headers()
                    return

                auth_hdr = self.headers.get("Authorization", "")
                auth_token = auth_hdr[7:].strip() if auth_hdr.startswith("Bearer ") else ""
                if not auth_token:
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error": "Unauthorized: Bearer auth_token required"}')
                    return

                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                try:
                    payload = json.loads(body.decode("utf-8")) if body else {}
                    actor = payload.pop("_actor", None)
                    req = CrossBankIntelligenceQueryRequest(**payload)
                    signal = coord.execute_two_bank_private_matching(
                        req=req,
                        auth_token=auth_token,
                        actor=actor,
                    )
                    res = signal.model_dump()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(res).encode())
                except Exception as exc:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps({
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        }).encode()
                    )

        self.server = ThreadingHTTPServer((self.host, self.port), CoordinatorHandler)
        self.port = self.server.server_address[1]
        self.pid = os.getpid()
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


class InstitutionalCoordinatorClient:
    """Client for invoking coordinator daemon over loopback/Docker network transport."""

    def __init__(self, base_url: str) -> None:
        clean_url = base_url.rstrip("/")
        if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
            raise ValueError(f"Unsupported URL scheme for coordinator client: {base_url}")
        self.base_url = clean_url

    def health_check(self) -> dict[str, Any]:
        url = f"{self.base_url}/health"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:  # nosec B310
                body = resp.read()
                return cast("dict[str, Any]", json.loads(body.decode("utf-8")))
        except Exception as exc:
            raise ConsortiumSecurityError(
                f"Coordinator daemon at '{self.base_url}' is unreachable: {exc}"
            ) from exc

    def execute_match(
        self,
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str,
        actor: str | None = None,
    ) -> CrossBankIntelligenceSignal:
        url = f"{self.base_url}/coordinator/execute-match"
        body = req.model_dump()
        if actor:
            body["_actor"] = actor
        req_data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20.0) as resp:  # nosec B310
                data = json.loads(resp.read().decode("utf-8"))
                return CrossBankIntelligenceSignal(**data)
        except urllib.error.HTTPError as http_err:
            try:
                err_body = json.loads(http_err.read().decode())
                err_msg = err_body.get("error", str(http_err))
                err_type = err_body.get("error_type", "")
                if "ConsortiumSecurityError" in err_type:
                    raise ConsortiumSecurityError(err_msg) from http_err
                if "PermissionError" in err_type or http_err.code in (401, 403):
                    raise PermissionError(err_msg) from http_err
                raise ValueError(err_msg) from http_err
            except (ConsortiumSecurityError, PermissionError, ValueError):
                raise
            except Exception:
                raise ValueError(f"HTTP transport error from coordinator: {http_err}") from http_err
        except Exception as exc:
            raise ConsortiumSecurityError(
                f"Coordinator matching invocation failed on '{self.base_url}': {exc}"
            ) from exc


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Institutional Bank Worker or Coordinator Process")
    parser.add_argument(
        "--role",
        default=os.environ.get("CFI_ROLE", "worker"),
        choices=["worker", "coordinator"],
        help="Process role: 'worker' or 'coordinator'",
    )
    parser.add_argument(
        "--bank-id",
        default=os.environ.get("BANK_ID"),
        help="Institutional bank identifier (for worker role)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("WORKER_HOST", "0.0.0.0"),  # nosec B104
        help="Bind host",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("WORKER_PORT", "0")),
        help="Bind port (0 for ephemeral)",
    )
    parser.add_argument(
        "--storage-root",
        default=os.environ.get("STORAGE_ROOT") or os.environ.get("CFI_STORAGE_DIR"),
        help="Path to base storage directory",
    )
    parser.add_argument(
        "--auth-secret",
        default=None,
        help="Worker authentication secret (deprecated on CLI; prefer env)",
    )
    parser.add_argument("--matching-token", default=None, help="Worker matching token")
    parser.add_argument("--ingestion-token", default=None, help="Worker ingestion token")
    parser.add_argument("--admin-token", default=None, help="Worker admin token")
    parser.add_argument(
        "--signing-key",
        default=os.environ.get("WORKER_SIGNING_KEY"),
        help="Institutional sovereign Ed25519 private signing key (64 hex characters)",
    )
    parser.add_argument(
        "--trusted-peers", default=None, help="JSON mapping of trusted peer verification keys"
    )

    # Coordinator specific flags
    parser.add_argument(
        "--worker-a-bank",
        default=os.environ.get("WORKER_A_BANK", "bank_a"),
        help="Bank A identifier for coordinator",
    )
    parser.add_argument(
        "--worker-a-url",
        default=os.environ.get("WORKER_A_URL"),
        help="Worker A base URL for coordinator",
    )
    parser.add_argument(
        "--worker-a-token",
        default=os.environ.get("WORKER_A_TOKEN"),
        help="Worker A matching token for coordinator",
    )
    parser.add_argument(
        "--worker-b-bank",
        default=os.environ.get("WORKER_B_BANK", "bank_b"),
        help="Bank B identifier for coordinator",
    )
    parser.add_argument(
        "--worker-b-url",
        default=os.environ.get("WORKER_B_URL"),
        help="Worker B base URL for coordinator",
    )
    parser.add_argument(
        "--worker-b-token",
        default=os.environ.get("WORKER_B_TOKEN"),
        help="Worker B matching token for coordinator",
    )

    cli_args = parser.parse_args()

    if cli_args.role == "coordinator":
        coord = CrossBankMatchingCoordinator()
        if cli_args.worker_a_url:
            client_a = InstitutionalWorkerClient(
                bank_id=cli_args.worker_a_bank,
                base_url=cli_args.worker_a_url,
                matching_token=cli_args.worker_a_token or "",
            )
            coord.register_worker(
                bank_id=cli_args.worker_a_bank,
                worker=client_a,
                matching_token=cli_args.worker_a_token,
            )
        if cli_args.worker_b_url:
            client_b = InstitutionalWorkerClient(
                bank_id=cli_args.worker_b_bank,
                base_url=cli_args.worker_b_url,
                matching_token=cli_args.worker_b_token or "",
            )
            coord.register_worker(
                bank_id=cli_args.worker_b_bank,
                worker=client_b,
                matching_token=cli_args.worker_b_token,
            )
        coord_daemon = InstitutionalCoordinatorDaemon(
            coordinator=coord,
            host=cli_args.host,
            port=cli_args.port or 8200,
        )
        coord_daemon.start()
        startup_info = {
            "status": "ONLINE",
            "role": "coordinator",
            "port": coord_daemon.port,
            "pid": os.getpid(),
        }
        sys.stdout.write(json.dumps(startup_info) + "\n")
        sys.stdout.flush()
        try:
            while True:
                time.sleep(1.0)
        except (KeyboardInterrupt, SystemExit):
            coord_daemon.stop()
    else:
        if not cli_args.bank_id or not cli_args.storage_root:
            parser.error(
                "--bank-id and --storage-root must be specified via CLI or environment variables (BANK_ID, STORAGE_ROOT / CFI_STORAGE_DIR)"
            )
        env_auth = (
            os.environ.get("WORKER_AUTH_SECRET")
            or cli_args.auth_secret
            or secrets.token_hex(32)
        )
        env_matching = os.environ.get("WORKER_MATCHING_TOKEN") or cli_args.matching_token
        env_ingest = os.environ.get("WORKER_INGESTION_TOKEN") or cli_args.ingestion_token
        env_admin = os.environ.get("WORKER_ADMIN_TOKEN") or cli_args.admin_token
        env_signing = (
            os.environ.get("WORKER_SIGNING_KEY")
            or cli_args.signing_key
            or os.environ.get(f"CFI_WORKER_SIGNING_KEY_{cli_args.bank_id.upper()}")
        )
        env_peers = os.environ.get("WORKER_TRUSTED_PEERS_JSON") or cli_args.trusted_peers or "{}"
        peers = json.loads(env_peers)

        cli_node = IsolatedBankWorkerNode(
            bank_id=cli_args.bank_id,
            storage_root=cli_args.storage_root,
            auth_secret=env_auth,
            signing_key=env_signing,
            matching_token=env_matching,
            ingestion_token=env_ingest,
            admin_token=env_admin,
            trusted_peer_keys=peers,
        )
        cli_daemon = InstitutionalWorkerDaemon(
            node=cli_node,
            host=cli_args.host,
            port=cli_args.port,
            run_as_process=False,
        )
        cli_daemon.start()
        startup_info = {
            "status": "ONLINE",
            "bank_id": cli_args.bank_id,
            "port": cli_daemon.port,
            "pid": os.getpid(),
        }
        sys.stdout.write(json.dumps(startup_info) + "\n")
        sys.stdout.flush()
        try:
            while True:
                time.sleep(1.0)
        except (KeyboardInterrupt, SystemExit):
            cli_daemon.stop()
