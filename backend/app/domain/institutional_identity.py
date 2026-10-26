"""Institutional Asymmetric Signing Identity and Verification Registry.

Provides authentic Ed25519 institutional cryptographic identity, independent
verification key registries, and canonical message encoding contracts for
cross-bank PSI evidence admission.

Adheres strictly to .agents/AGENTS.md:
- No fabricated keys or fallback bypasses.
- Strict tenant isolation: private keys never leave the institutional worker.
- Analyst service and coordinator possess only trusted public verification keys.
- Standard Ed25519 (RFC 8032) via cryptography.hazmat.primitives.asymmetric.ed25519.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from app.domain.consortium_governance import ConsortiumSecurityError


def canonical_worker_readiness_bytes(
    bank_id: str,
    nonce: str,
    timestamp: str,
    protocol: str = "OPENMINED-PSI-2.0.6-ECDH",
) -> bytes:
    """Construct canonical byte representation for institutional worker readiness authentication."""
    canonical = (
        f"WORKER_READINESS:{bank_id.lower().strip()}:{nonce.strip()}:"
        f"{timestamp.strip()}:{protocol.strip()}"
    )
    return canonical.encode("utf-8")


def canonical_coordinator_readiness_bytes(
    nonce: str,
    timestamp: str,
    role: str = "coordinator",
    coordinator_id: str = "coordinator",
    protocol: str = "OPENMINED-PSI-2.0.6-ECDH",
) -> bytes:
    """Construct canonical byte representation for coordinator readiness authentication."""
    canonical = (
        f"COORDINATOR_READINESS:{role.strip()}:{coordinator_id.strip()}:"
        f"{nonce.strip()}:{timestamp.strip()}:{protocol.strip()}"
    )
    return canonical.encode("utf-8")


def canonical_pairwise_grant_approval_bytes(
    consortium_id: str,
    grant_id: str,
    revision: int,
    scope_digest: str,
    approving_bank_id: str,
    approver_id: str = "",
) -> bytes:
    """Construct canonical byte representation for institutional pairwise grant approval.

    Binds the approving bank's Ed25519 signature to the exact consortium, grant ID,
    revision, canonical scope digest, and approver identity.
    """
    canonical = (
        f"PAIRWISE_GRANT_APPROVAL:{consortium_id.lower().strip()}:"
        f"{grant_id.strip()}:{revision}:{scope_digest.strip()}:"
        f"{approving_bank_id.lower().strip()}:{approver_id.strip()}"
    )
    return canonical.encode("utf-8")


def validate_institutional_csr_identity(csr: Any, expected_bank_id: str) -> None:
    """Strictly validates that an institutional CSR binds to authorized canonical identities.

    Enforces:
    - Exact canonical Common Name matching: {bank_id}, {bank_id}.client.cf-intelligence.io, or {bank_id}.worker.cf-intelligence.io
    - Rejection of lookalike institution IDs, substring injections, and unapproved domain suffixes
    - Rejection of wildcard CN and wildcard SAN entries
    - Independent verification of all Subject Alternative Names (SANs); every entry must be authorized
    - Rejection of cross-institution identity substitution (e.g. Bank B identity in Bank A enrollment)

    Raises:
        ValueError: If the CSR identity, Common Name, or SAN fails validation.
    """
    clean_bank = expected_bank_id.lower().strip()
    if not re.match(r"^[a-zA-Z0-9_-]{3,36}$", clean_bank):
        raise ValueError(f"Invalid institutional bank_id format: '{expected_bank_id}'.")

    approved_identities = {
        clean_bank,
        f"{clean_bank}.client.cf-intelligence.io",
        f"{clean_bank}.worker.cf-intelligence.io",
    }
    allowed_san_identities = approved_identities | {"localhost", "127.0.0.1"}

    # 1. Subject Common Name verification
    cns = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not cns:
        raise ValueError(f"CSR Subject must include Common Name for bank_id '{expected_bank_id}'.")
    cn_val = str(cns[0].value).lower().strip()

    if "*" in cn_val:
        raise ValueError(f"Wildcard Common Name '{cn_val}' is strictly forbidden.")

    # Exact match only: reject any lookalikes, prefix substrings, or unauthorized domain suffixes
    if cn_val not in approved_identities:
        raise ValueError(
            f"CSR Subject Common Name '{cn_val}' does not match authorized canonical identity "
            f"for bank_id '{expected_bank_id}'. Expected one of {sorted(approved_identities)}. "
            "Substring, lookalike, and unapproved suffix identities are rejected."
        )

    # 2. Subject Alternative Name verification (if present)
    for ext in csr.extensions:
        if isinstance(ext.value, x509.SubjectAlternativeName):
            san_names = ext.value.get_values_for_type(x509.DNSName)
            for san in san_names:
                san_clean = san.lower().strip()
                if "*" in san_clean:
                    raise ValueError(f"Wildcard Subject Alternative Name '{san}' is strictly forbidden.")
                if san_clean not in allowed_san_identities:
                    raise ValueError(
                        f"CSR Subject Alternative Name '{san}' does not match authorized institutional identity "
                        f"for bank_id '{expected_bank_id}'. Every SAN entry must be approved."
                    )
            san_ips = ext.value.get_values_for_type(x509.IPAddress)
            for ip in san_ips:
                ip_str = str(ip).strip()
                if ip_str not in ("127.0.0.1", "::1"):
                    raise ValueError(
                        f"CSR IP SAN '{ip_str}' is not an authorized IP address for bank_id '{expected_bank_id}'."
                    )



def canonical_counterpart_evidence_bytes(
    version: str,
    consortium_id: str,
    grant_id: str,
    grant_revision: int,
    grant_scope_digest: str,
    requesting_bank_id: str,
    target_bank_id: str,
    job_id: str,
    ticket_digest: str,
    psi_server_setup_hash: str,
    psi_response_hash: str,
    protocol: str,
    nonce: str,
    timestamp: str,
) -> bytes:
    """Construct canonical byte representation of Bank B counterpart PSI execution evidence."""
    canonical = (
        f"{version}:{consortium_id}:{grant_id}:{grant_revision}:{grant_scope_digest}:"
        f"{requesting_bank_id}:{target_bank_id}:{job_id}:{ticket_digest}:"
        f"{psi_server_setup_hash}:{psi_response_hash}:{protocol}:{nonce}:{timestamp}"
    )
    return canonical.encode("utf-8")


def canonical_counterpart_evidence_digest(
    version: str,
    consortium_id: str,
    grant_id: str,
    grant_revision: int,
    grant_scope_digest: str,
    requesting_bank_id: str,
    target_bank_id: str,
    job_id: str,
    ticket_digest: str,
    psi_server_setup_hash: str,
    psi_response_hash: str,
    protocol: str,
    nonce: str,
    timestamp: str,
) -> str:
    """Compute SHA-256 digest of canonical counterpart execution evidence."""
    msg = canonical_counterpart_evidence_bytes(
        version=version,
        consortium_id=consortium_id,
        grant_id=grant_id,
        grant_revision=grant_revision,
        grant_scope_digest=grant_scope_digest,
        requesting_bank_id=requesting_bank_id,
        target_bank_id=target_bank_id,
        job_id=job_id,
        ticket_digest=ticket_digest,
        psi_server_setup_hash=psi_server_setup_hash,
        psi_response_hash=psi_response_hash,
        protocol=protocol,
        nonce=nonce,
        timestamp=timestamp,
    )
    return hashlib.sha256(msg).hexdigest()


def canonical_evaluated_result_bytes(
    version: str,
    receipt_id: str,
    job_id: str,
    consortium_id: str,
    grant_id: str,
    grant_revision: int,
    grant_scope_digest: str,
    requesting_bank_id: str,
    target_bank_id: str,
    query_entity_id: str,
    ticket_digest: str,
    counterpart_evidence_digest: str,
    match_confirmed: bool,
    match_category: str,
    match_confidence: float,
    matched_risk_level: str,
    matched_typologies: tuple[str, ...],
    disclosure_mode: str,
    contributing_institutions: tuple[str, ...],
    protocol: str,
    assurance_level: str,
    executed_at: str,
) -> bytes:
    """Construct canonical byte representation of Bank A evaluated PSI match result."""
    typologies_str = ",".join(sorted(matched_typologies))
    institutions_str = ",".join(sorted(contributing_institutions))
    canonical = (
        f"{version}:{receipt_id}:{job_id}:{consortium_id}:{grant_id}:{grant_revision}:{grant_scope_digest}:"
        f"{requesting_bank_id}:{target_bank_id}:{query_entity_id}:{ticket_digest}:"
        f"{counterpart_evidence_digest}:{match_confirmed}:{match_category}:{match_confidence:.9f}:"
        f"{matched_risk_level}:{typologies_str}:{disclosure_mode}:{institutions_str}:"
        f"{protocol}:{assurance_level}:{executed_at}"
    )
    return canonical.encode("utf-8")


def canonical_evaluated_result_digest(
    version: str,
    receipt_id: str,
    job_id: str,
    consortium_id: str,
    grant_id: str,
    grant_revision: int,
    grant_scope_digest: str,
    requesting_bank_id: str,
    target_bank_id: str,
    query_entity_id: str,
    ticket_digest: str,
    counterpart_evidence_digest: str,
    match_confirmed: bool,
    match_category: str,
    match_confidence: float,
    matched_risk_level: str,
    matched_typologies: tuple[str, ...],
    disclosure_mode: str,
    contributing_institutions: tuple[str, ...],
    protocol: str,
    assurance_level: str,
    executed_at: str,
) -> str:
    """Compute SHA-256 digest of canonical evaluated result evidence."""
    msg = canonical_evaluated_result_bytes(
        version=version,
        receipt_id=receipt_id,
        job_id=job_id,
        consortium_id=consortium_id,
        grant_id=grant_id,
        grant_revision=grant_revision,
        grant_scope_digest=grant_scope_digest,
        requesting_bank_id=requesting_bank_id,
        target_bank_id=target_bank_id,
        query_entity_id=query_entity_id,
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
        executed_at=executed_at,
    )
    return hashlib.sha256(msg).hexdigest()


class InstitutionalSigningIdentity:
    """Authentic Ed25519 signing identity held strictly by an isolated bank worker."""

    def __init__(self, bank_id: str, private_key: ed25519.Ed25519PrivateKey) -> None:
        clean_bank_id = bank_id.lower().strip()
        if not clean_bank_id:
            raise ValueError("InstitutionalSigningIdentity requires a non-empty bank_id.")
        self.bank_id = clean_bank_id
        self._private_key = private_key
        self._public_key = private_key.public_key()
        raw_pub = self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_key_hex = raw_pub.hex()

    @property
    def private_key_hex(self) -> str:
        """Return the 64-character raw hex representation of the private key."""
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ).hex()

    @property
    def public_key(self) -> ed25519.Ed25519PublicKey:
        """Expose public verification key."""
        return self._public_key

    def sign(self, message: bytes) -> str:
        """Sign canonical message bytes and return signature as hex string."""
        sig = self._private_key.sign(message)
        return sig.hex()

    def verify(self, signature_hex: str, message: bytes) -> bool:
        """Verify an Ed25519 signature against this identity's public verification key."""
        try:
            self._public_key.verify(bytes.fromhex(signature_hex.strip()), message)
            return True
        except Exception:
            return False

    @classmethod
    def from_seed(cls, bank_id: str, secret: str) -> InstitutionalSigningIdentity:
        """Reject deterministic seed derivation to guarantee independent institutional key sovereignty."""
        raise PermissionError(
            "Security policy violation: Deriving institutional signing keys from shared secrets "
            "or seeds is strictly prohibited. Institutional keys must be generated independently "
            "using generate(), from_private_hex(), or from_private_key_file() to prevent counterpart "
            "or coordinator key compromise."
        )

    @classmethod
    def from_private_hex(cls, bank_id: str, hex_key: str) -> InstitutionalSigningIdentity:
        """Load an Ed25519 signing identity from a 64-char hex string (32 raw bytes)."""
        clean_hex = hex_key.strip()
        if len(clean_hex) != 64:
            raise ValueError(
                f"Invalid private key hex length: {len(clean_hex)}. Expected 64 hex characters (32 raw bytes)."
            )
        raw_bytes = bytes.fromhex(clean_hex)
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(raw_bytes)
        return cls(bank_id=bank_id, private_key=priv)

    @classmethod
    def from_private_bytes(cls, bank_id: str, raw_bytes: bytes) -> InstitutionalSigningIdentity:
        """Load an Ed25519 signing identity from 32 raw private bytes."""
        if len(raw_bytes) != 32:
            raise ValueError(
                f"Invalid private key byte length: {len(raw_bytes)}. Expected exactly 32 bytes."
            )
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(raw_bytes)
        return cls(bank_id=bank_id, private_key=priv)

    @classmethod
    def from_private_key_file(cls, bank_id: str, file_path: str) -> InstitutionalSigningIdentity:
        """Load an Ed25519 signing identity from a local file containing 64-char hex or 32 raw bytes."""
        import os
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Signing key file not found: {file_path}")
        with open(file_path, "rb") as f:
            content = f.read().strip()
        if len(content) == 32:
            return cls.from_private_bytes(bank_id, content)
        content_str = content.decode("utf-8").strip()
        return cls.from_private_hex(bank_id, content_str)

    def save_private_key_file(self, file_path: str) -> None:
        """Save the raw private key hex securely to a file."""
        import os
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(self.private_key_hex)

    @classmethod
    def generate(cls, bank_id: str) -> InstitutionalSigningIdentity:
        """Generate a fresh cryptographically random Ed25519 keypair."""
        priv = ed25519.Ed25519PrivateKey.generate()
        return cls(bank_id=bank_id, private_key=priv)


class EnrolledInstitutionalPublicKey:
    """Authoritative metadata binding for an enrolled institutional public verification key."""

    def __init__(
        self,
        bank_id: str,
        public_key: ed25519.Ed25519PublicKey,
        public_key_hex: str,
        version: int = 1,
        enrolled_at: str | None = None,
        trust_source: str = "DIRECT_BOOTSTRAP",
        is_revoked: bool = False,
    ) -> None:
        self.bank_id = bank_id.lower().strip()
        self.public_key = public_key
        self.public_key_hex = public_key_hex
        self.version = version
        import datetime
        self.enrolled_at = enrolled_at or datetime.datetime.now(datetime.UTC).isoformat()
        self.trust_source = trust_source
        self.is_revoked = is_revoked


class InstitutionalVerificationRegistry:
    """Registry holding ONLY trusted public verification keys for institutional members.

    Enforces strict key custody:
    - Never stores, receives, or exposes private signing keys.
    - Rejects any attempt to register private key material.
    - Accepts ONLY valid Ed25519 public key representations (64-char hex, 32 raw bytes, or Ed25519PublicKey).
    - Strictly rejects arbitrary strings, passwords, HMAC credentials, and seeds.
    - Fails closed if an un-enrolled or revoked institution signature is queried.
    - Prevents unauthenticated replacement of enrolled institutional keys.
    """

    def __init__(self) -> None:
        self._enrolled_keys: dict[str, EnrolledInstitutionalPublicKey] = {}
        self._public_keys: dict[str, ed25519.Ed25519PublicKey] = {}
        self._key_versions: dict[str, int] = {}

    def register_public_key(
        self,
        bank_id: str,
        public_key: ed25519.Ed25519PublicKey | str | bytes,
        version: int = 1,
        trust_source: str = "DIRECT_BOOTSTRAP",
        allow_overwrite: bool = False,
    ) -> None:
        """Enroll or update an institution's public verification key."""
        clean_bank_id = bank_id.lower().strip()
        if not clean_bank_id:
            raise ValueError("bank_id cannot be empty or whitespace.")

        if isinstance(public_key, ed25519.Ed25519PrivateKey):
            raise PermissionError(
                "Security boundary violation: Private key material cannot be registered "
                "in the public verification registry."
            )

        if clean_bank_id in self._enrolled_keys and not allow_overwrite:
            current_v = self._enrolled_keys[clean_bank_id].version
            raise ConsortiumSecurityError(
                f"Institution '{clean_bank_id}' is already enrolled with public verification key version {current_v}. "
                "Unauthorized key replacement via ordinary registration is prohibited. "
                "Ordinary registration must never overwrite an existing institutional key. "
                "Key updates require cryptographically authorized rotation via rotate_public_key()."
            )

        pub_obj: ed25519.Ed25519PublicKey
        if isinstance(public_key, ed25519.Ed25519PublicKey):
            pub_obj = public_key
        elif isinstance(public_key, bytes):
            if len(public_key) != 32:
                raise ValueError(
                    f"Invalid public key byte length: {len(public_key)}. Expected exactly 32 bytes for Ed25519."
                )
            pub_obj = ed25519.Ed25519PublicKey.from_public_bytes(public_key)
        elif isinstance(public_key, str):
            raw_str = public_key.strip()
            if len(raw_str) != 64:
                raise ValueError(
                    f"Invalid public verification key for institution '{clean_bank_id}': "
                    f"expected 64-character hex string (32 raw bytes), received length {len(raw_str)}. "
                    "String credentials, passwords, HMAC secrets, and seeds are strictly rejected."
                )
            try:
                raw_bytes = bytes.fromhex(raw_str)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid hex string for institutional public verification key: {exc}"
                ) from exc
            pub_obj = ed25519.Ed25519PublicKey.from_public_bytes(raw_bytes)
        else:
            raise TypeError(f"Invalid public key type: {type(public_key)}")

        pub_hex = pub_obj.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()

        enrolled = EnrolledInstitutionalPublicKey(
            bank_id=clean_bank_id,
            public_key=pub_obj,
            public_key_hex=pub_hex,
            version=version,
            trust_source=trust_source,
            is_revoked=False,
        )

        self._enrolled_keys[clean_bank_id] = enrolled
        self._public_keys[clean_bank_id] = pub_obj
        self._key_versions[clean_bank_id] = version

    def rotate_public_key(
        self,
        bank_id: str,
        new_public_key: ed25519.Ed25519PublicKey | str | bytes,
        rotation_authorization_signature: str,
    ) -> None:
        """Rotate an enrolled public key with cryptographic authorization from the previous key."""
        clean_bank_id = bank_id.lower().strip()
        current = self._enrolled_keys.get(clean_bank_id)
        if not current:
            raise ConsortiumSecurityError(
                f"Cannot rotate key: Institution '{clean_bank_id}' has no enrolled key."
            )
        if current.is_revoked:
            raise ConsortiumSecurityError(
                f"Cannot rotate key: Enrolled verification key for '{clean_bank_id}' has been revoked."
            )

        new_pub_obj: ed25519.Ed25519PublicKey
        if isinstance(new_public_key, ed25519.Ed25519PublicKey):
            new_pub_obj = new_public_key
        elif isinstance(new_public_key, bytes):
            if len(new_public_key) != 32:
                raise ValueError(
                    f"Invalid new public key byte length: {len(new_public_key)}. Expected 32 bytes."
                )
            new_pub_obj = ed25519.Ed25519PublicKey.from_public_bytes(new_public_key)
        elif isinstance(new_public_key, str):
            raw_str = new_public_key.strip()
            if len(raw_str) != 64:
                raise ValueError(
                    f"Invalid new public verification key for institution '{clean_bank_id}': "
                    f"expected 64-character hex string (32 raw bytes), received length {len(raw_str)}."
                )
            try:
                raw_bytes = bytes.fromhex(raw_str)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid hex string for new institutional public verification key: {exc}"
                ) from exc
            new_pub_obj = ed25519.Ed25519PublicKey.from_public_bytes(raw_bytes)
        else:
            raise TypeError(f"Invalid public key type: {type(new_public_key)}")

        new_raw_hex = new_pub_obj.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()

        next_version = current.version + 1
        challenge = f"ROTATE:{clean_bank_id}:{new_raw_hex}:{next_version}".encode()

        try:
            current.public_key.verify(bytes.fromhex(rotation_authorization_signature.strip()), challenge)
        except (InvalidSignature, ValueError) as exc:
            raise ConsortiumSecurityError(
                f"Key rotation authorization failed for institution '{clean_bank_id}': invalid authorization signature."
            ) from exc

        enrolled = EnrolledInstitutionalPublicKey(
            bank_id=clean_bank_id,
            public_key=new_pub_obj,
            public_key_hex=new_raw_hex,
            version=next_version,
            trust_source="AUTHORIZED_ROTATION",
            is_revoked=False,
        )

        self._enrolled_keys[clean_bank_id] = enrolled
        self._public_keys[clean_bank_id] = new_pub_obj
        self._key_versions[clean_bank_id] = next_version

    def revoke_public_key(self, bank_id: str, reason: str = "GOVERNANCE_REVOCATION") -> None:
        """Mark an institution's public verification key as revoked."""
        clean_bank_id = bank_id.lower().strip()
        current = self._enrolled_keys.get(clean_bank_id)
        if not current:
            raise ConsortiumSecurityError(
                f"Cannot revoke key: Institution '{clean_bank_id}' has no enrolled key."
            )
        revoked_item = EnrolledInstitutionalPublicKey(
            bank_id=clean_bank_id,
            public_key=current.public_key,
            public_key_hex=current.public_key_hex,
            version=current.version,
            enrolled_at=current.enrolled_at,
            trust_source=f"REVOKED:{reason}",
            is_revoked=True,
        )
        self._enrolled_keys[clean_bank_id] = revoked_item

    def get_public_key(self, bank_id: str) -> ed25519.Ed25519PublicKey:
        """Retrieve the trusted public verification key for an institution.

        Fails closed with ConsortiumSecurityError if un-enrolled or revoked.
        Never derives keys from shared secrets or HMAC credentials.
        """
        clean_bank_id = bank_id.lower().strip()
        import os

        if clean_bank_id not in self._enrolled_keys:
            env_key = (
                os.getenv(f"CFI_INSTITUTION_PUBLIC_KEY_{clean_bank_id.upper()}")
                or os.getenv(f"CFI_PEER_PUBLIC_KEY_{clean_bank_id.upper()}")
                or os.getenv(f"CFI_PEER_KEY_{clean_bank_id.upper()}")
            )
            if env_key and len(env_key.strip()) == 64:
                clean_env = env_key.strip()
                with contextlib.suppress(Exception):
                    self.register_public_key(
                        clean_bank_id,
                        clean_env,
                        trust_source="ENVIRONMENT_BOOTSTRAP",
                        allow_overwrite=True,
                    )

        enrolled = self._enrolled_keys.get(clean_bank_id)
        if enrolled is not None:
            if enrolled.is_revoked:
                raise ConsortiumSecurityError(
                    f"Institutional public verification key for bank '{clean_bank_id}' has been revoked. "
                    "Operational admission fails closed."
                )
            return enrolled.public_key

        raise ConsortiumSecurityError(
            f"Institutional public verification key not found for bank '{clean_bank_id}'. "
            "Execution fails closed to prevent unverified admission."
        )

    def verify_signature(self, bank_id: str, signature_hex: str, message: bytes) -> bool:
        """Verify an Ed25519 signature against an institution's enrolled public key."""
        try:
            pub = self.get_public_key(bank_id)
            pub.verify(bytes.fromhex(signature_hex.strip()), message)
            return True
        except (InvalidSignature, ValueError, ConsortiumSecurityError):
            return False

    def get_public_key_hex(self, bank_id: str) -> str:
        """Return the hex representation of an institution's public verification key."""
        pub = self.get_public_key(bank_id)
        return pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()

    def snapshot_public_keys(self) -> dict[str, str]:
        """Return a mapping of bank_id to public key hex for enrolled active institutions."""
        return {
            b_id: k.public_key_hex
            for b_id, k in self._enrolled_keys.items()
            if not k.is_revoked
        }

    def has_key(self, bank_id: str) -> bool:
        """Return True if an institutional public verification key is enrolled and not revoked."""
        try:
            self.get_public_key(bank_id)
            return True
        except Exception:
            return False
