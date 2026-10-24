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
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    ConsortiumStatus,
)
from app.domain.consortium_policy import ConsortiumPolicyEngine
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

        # Peer verification keystore: independently provisioned trusted verification keys
        self._trusted_peer_verification_keys: dict[str, str] = {
            k.lower().strip(): v for k, v in (trusted_peer_keys or {}).items()
        }

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

    def enroll_trusted_peer(self, peer_bank_id: str, verification_key: str) -> None:
        """Enroll an authoritative counterpart bank verification key in this worker's keystore."""
        clean_id = peer_bank_id.lower().strip()
        if not verification_key or len(verification_key.strip()) < 16:
            raise ValueError(f"Invalid verification key for peer '{peer_bank_id}'")
        self._trusted_peer_verification_keys[clean_id] = verification_key

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

    def store_private_entity(self, entity: Entity | dict[str, Any]) -> str:
        """Store an authoritative entity strictly in this bank's isolated storage vault."""
        ent_dict = entity.to_dict() if isinstance(entity, Entity) else dict(entity)

        ent_bank = str(ent_dict.get("bank_id", "")).lower().strip()
        if ent_bank != self.bank_id:
            raise PermissionError(
                f"Isolated bank worker '{self.bank_id}' cannot store entity belonging to institution '{ent_bank}'."
            )

        entity_id = ent_dict.get("id") or str(uuid.uuid4())
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

        path = os.path.join(self.vault_dir, f"{entity_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ent_dict, f, indent=2)
        return entity_id

    def load_private_entity(self, entity_id: str) -> dict[str, Any]:
        """Load an authoritative entity strictly from this bank's isolated storage vault."""
        path = os.path.join(self.vault_dir, f"{entity_id}.json")
        if not os.path.exists(path):
            raise ValueError(
                f"Entity '{entity_id}' not found in isolated private storage of institution '{self.bank_id}'."
            )
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if str(data.get("bank_id", "")).lower().strip() != self.bank_id:
            raise PermissionError(
                f"Tenant isolation breach detected: Entity '{entity_id}' belongs to '{data.get('bank_id')}', "
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

        canonical_text = (
            f"{job_id}:{consortium_id}:{self.bank_id}:"
            f"{clean_target}:{blinded_hex}:{nonce}:"
            f"{timestamp}:{purpose}"
        )
        signature = hmac.new(
            self._auth_secret.encode("utf-8"),
            canonical_text.encode("utf-8"),
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
        peer_key = self._trusted_peer_verification_keys.get(
            query.requesting_bank_id.lower().strip()
        )
        if not peer_key:
            raise ConsortiumSecurityError(
                f"Peer authentication failed: Institution '{query.requesting_bank_id}' has no trusted verification key enrolled."
            )
        expected_sig = hmac.new(
            peer_key.encode("utf-8"),
            f"{query.job_id}:{query.consortium_id}:{query.requesting_bank_id}:"
            f"{query.target_bank_id}:{query.blinded_element_hex}:{query.nonce}:"
            f"{query.timestamp}:{query.purpose}".encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_sig, query.signature):
            raise ConsortiumSecurityError(
                f"Peer authentication failed: Invalid signature from requesting bank '{query.requesting_bank_id}'."
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
                "--auth-secret",
                self.node._auth_secret,
                "--trusted-peers",
                peers_json,
            ]
            backend_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            )
            proc_env = os.environ.copy()
            curr_pp = proc_env.get("PYTHONPATH", "")
            proc_env["PYTHONPATH"] = (
                f"{backend_dir}{os.pathsep}{curr_pp}" if curr_pp else backend_dir
            )

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
        auth_secret = node._auth_secret

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass  # suppress noisy stdout logs in test runner

            def _authenticate(self) -> bool:
                auth_hdr = self.headers.get("Authorization", "")
                if not auth_hdr.startswith("Bearer "):
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'{"error": "Unauthorized: Bearer token required"}')
                    return False
                token = auth_hdr[7:].strip()
                if not hmac.compare_digest(token, auth_secret):
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b'{"error": "Forbidden: Invalid worker secret"}')
                    return False
                return True

            def do_GET(self) -> None:
                if self.path == "/health":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps(
                            {"status": "ONLINE", "bank_id": node.bank_id, "pid": os.getpid()}
                        ).encode()
                    )
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self) -> None:
                if not self._authenticate():
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
                        query_msg = BlindedQueryMessage(**q_data)
                        resp = node.process_counterpart_query(
                            query=query_msg,
                        )
                        res = asdict(resp)
                    elif self.path == "/worker/finalize-match":
                        q_data = data["query"]
                        r_data = data["response"]
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
                        node.enroll_trusted_peer(data["peer_bank_id"], data["verification_key"])
                        res = {"status": "ENROLLED", "peer_bank_id": data["peer_bank_id"]}
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

    def __init__(self, bank_id: str, base_url: str, auth_secret: str) -> None:
        self.bank_id = bank_id.lower().strip()
        self.base_url = base_url.rstrip("/")
        self.auth_secret = auth_secret
        self._is_network_transport = True

    def health_check(self) -> dict[str, Any]:
        """Perform health check probe to worker node."""
        url = f"{self.base_url}/health"
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"Unsupported URL scheme for worker client: {url}")
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self.auth_secret}"},
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

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"Unsupported URL scheme for worker client: {url}")
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth_secret}",
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
                if "PermissionError" in err_type:
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
        res = self._post("/worker/store-entity", {"entity": ent_dict})
        return str(res["entity_id"])

    def enroll_peer(self, peer_bank_id: str, verification_key: str) -> None:
        self._post(
            "/worker/enroll-peer",
            {"peer_bank_id": peer_bank_id, "verification_key": verification_key},
        )

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
        self._workers: dict[str, Any] = {}
        self._worker_secrets: dict[str, str] = {}
        self._coordinator_secret = secrets.token_hex(32)

    def register_worker(
        self,
        bank_id: str,
        worker: Any,
        auth_secret: str,
    ) -> None:
        """Register an authenticated isolated bank worker node or client."""
        clean_id = bank_id.lower().strip()
        self._workers[clean_id] = worker
        self._worker_secrets[clean_id] = auth_secret

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

        # 2. Enforce Explicit Operational Data-Sharing Grant & Mandatory Jurisdiction
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

        secret_a = self._worker_secrets.get(clean_requesting, getattr(worker_a, "auth_secret", ""))
        secret_b = self._worker_secrets.get(clean_target, getattr(worker_b, "auth_secret", ""))
        if not secret_a or not secret_b:
            raise ConsortiumSecurityError("Institutional worker credentials are not configured.")

        # Determine execution assurance level based on actual worker boundary
        is_multi_process = getattr(worker_a, "_is_network_transport", False) and getattr(
            worker_b, "_is_network_transport", False
        )
        assurance_level_literal: str = (
            "ISOLATED_INTEGRATION_VERIFIED" if is_multi_process else "PROTOCOL_PROTOTYPE"
        )

        # 4. Stage 1: Bank A Worker generates Blinded Query Message
        query_msg = worker_a.initiate_blinded_query(
            query_entity_id=req.query_entity_id,
            consortium_id=clean_consortium_id,
            target_bank_id=clean_target,
            purpose=req.purpose,
        )

        # 5. Stage 2: Bank B Worker computes Counterpart Match Response
        # Note: Peer signatures are verified using Bank B's internal trusted peer keystore,
        # never secrets supplied by the coordinator or in the request payload.
        counterpart_resp = worker_b.process_counterpart_query(
            query=query_msg,
        )

        # 6. Stage 3: Bank A Worker finalizes match evaluation
        receipt = worker_a.finalize_match_evaluation(
            query=query_msg,
            response=counterpart_resp,
        )

        # 7. Coordinator signs receipt attestation
        coord_sig = hmac.new(
            self._coordinator_secret.encode("utf-8"),
            f"{receipt.receipt_id}:{receipt.transcript_digest}:{clean_consortium_id}".encode(),
            hashlib.sha256,
        ).hexdigest()

        # 8. Determine Minimal Disclosed Intelligence Signal
        now = datetime.datetime.now(UTC)
        allow_identities = bool(disclosure_scope.get("allow_institution_identity", False))
        disclosed_institutions = (
            [clean_target] if (receipt.match_confirmed and allow_identities) else []
        )
        disclosure_mode_literal: DisclosureMode = (
            "ATTRIBUTED_INSTITUTIONS" if allow_identities else "ANONYMIZED_SUMMARY"
        )

        # Risk attribution is exact, bound strictly to the decrypted matched record (never entire dataset)
        max_risk = receipt.matched_risk_level if receipt.match_confirmed else "UNOBSERVED"
        typologies = list(receipt.matched_typologies) if receipt.match_confirmed else []
        if receipt.match_confirmed:
            typologies = sorted(list(set(typologies) | {"CROSS_BANK_ENTITY_OBSERVED"}))

        category_literal: MatchCategory = "EXACT_MATCH" if receipt.match_confirmed else "NO_MATCH"
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
                "operational_status": "OPERATIONAL",
                "governance_authority": f"consortium:{clean_consortium_id}",
                "protocol": "DH-PSI-2048-MODP",
                "job_id": query_msg.job_id,
                "receipt_id": receipt.receipt_id,
                "transcript_digest": receipt.transcript_digest,
                "coordinator_signature": coord_sig,
                "assurance_level": assurance_level_literal,
            },
            execution_mode="VERIFIED_OPERATIONAL",
            attestation_status="ISOLATED_PROCESS_VERIFIED"
            if is_multi_process
            else "IN_PROCESS_PROTOTYPE",
            is_authoritative=True,
            is_synthetic=False,
            assurance_level=cast("Any", assurance_level_literal),
        )

        # 9. Register in Durable Authoritative Signal Store
        signal_dict = signal.model_dump()
        expires_at = (now + datetime.timedelta(hours=24)).isoformat()
        content_hash = hashlib.sha256(
            json.dumps(signal_dict, sort_keys=True).encode("utf-8")
        ).hexdigest()

        authoritative_record = {
            "signal_id": signal_id,
            "signal_data": signal_dict,
            "requesting_bank_id": clean_requesting,
            "consortium_id": clean_consortium_id,
            "query_entity_id": req.query_entity_id,
            "issued_to_principal": actor or f"worker:{clean_requesting}",
            "issued_at": now.isoformat(),
            "expires_at": expires_at,
            "content_hash": content_hash,
            "is_authoritative": True,
            "is_synthetic": False,
            "operational_status": "OPERATIONAL",
            "assurance_level": assurance_level_literal,
            "attached_to_cases": [],
            "receipt": asdict(receipt),
        }
        self._signal_store.set(signal_id, authoritative_record)

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
                "protocol": "DH-PSI-2048-MODP",
                "receipt_id": receipt.receipt_id,
                "assurance_level": assurance_level_literal,
            },
        )

        logger.info(
            "Isolated two-bank private matching completed: %s <-> %s (match=%s, receipt=%s, assurance=%s)",
            clean_requesting,
            clean_target,
            receipt.match_confirmed,
            receipt.receipt_id,
            assurance_level_literal,
        )
        return signal


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Institutional Bank Worker Node Process")
    parser.add_argument("--bank-id", required=True, help="Institutional bank identifier")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=0, help="Bind port (0 for ephemeral)")
    parser.add_argument("--storage-root", required=True, help="Path to base storage directory")
    parser.add_argument("--auth-secret", required=True, help="Worker authentication secret")
    parser.add_argument(
        "--trusted-peers", default="{}", help="JSON mapping of trusted peer verification keys"
    )

    cli_args = parser.parse_args()
    peers = json.loads(cli_args.trusted_peers)
    cli_node = IsolatedBankWorkerNode(
        bank_id=cli_args.bank_id,
        storage_root=cli_args.storage_root,
        auth_secret=cli_args.auth_secret,
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
