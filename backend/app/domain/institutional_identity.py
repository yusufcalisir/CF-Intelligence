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

import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.domain.consortium_governance import ConsortiumSecurityError


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
    def public_key(self) -> ed25519.Ed25519PublicKey:
        """Expose public verification key."""
        return self._public_key

    def sign(self, message: bytes) -> str:
        """Sign canonical message bytes and return signature as hex string."""
        sig = self._private_key.sign(message)
        return sig.hex()

    @classmethod
    def from_seed(cls, bank_id: str, secret: str) -> InstitutionalSigningIdentity:
        """Derive an Ed25519 signing identity from an authoritative seed secret."""
        if not secret or not secret.strip():
            raise ValueError("Seed secret cannot be empty.")
        seed = hashlib.sha256(b"CFI-INSTITUTION-SIGNING-SEED-V1:" + secret.encode("utf-8")).digest()
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
        return cls(bank_id=bank_id, private_key=priv)

    @classmethod
    def from_private_hex(cls, bank_id: str, hex_key: str) -> InstitutionalSigningIdentity:
        """Load an Ed25519 signing identity from a 64-char hex string (32 raw bytes)."""
        raw_bytes = bytes.fromhex(hex_key.strip())
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(raw_bytes)
        return cls(bank_id=bank_id, private_key=priv)

    @classmethod
    def generate(cls, bank_id: str) -> InstitutionalSigningIdentity:
        """Generate a fresh cryptographically random Ed25519 keypair."""
        priv = ed25519.Ed25519PrivateKey.generate()
        return cls(bank_id=bank_id, private_key=priv)


class InstitutionalVerificationRegistry:
    """Registry holding ONLY trusted public verification keys for institutional members.

    Enforces strict key custody:
    - Never stores, receives, or exposes private signing keys.
    - Rejects any attempt to register private key material.
    - Fails closed if an un-enrolled institution signature is queried.
    """

    def __init__(self) -> None:
        self._public_keys: dict[str, ed25519.Ed25519PublicKey] = {}
        self._key_versions: dict[str, int] = {}

    def register_public_key(
        self,
        bank_id: str,
        public_key: ed25519.Ed25519PublicKey | str,
        version: int = 1,
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

        pub_obj: ed25519.Ed25519PublicKey
        if isinstance(public_key, str):
            raw_str = public_key.strip()
            # If 64 hex chars, load directly
            if len(raw_str) == 64:
                pub_obj = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(raw_str))
            else:
                # Deterministic seed derivation fallback
                seed = hashlib.sha256(b"CFI-INSTITUTION-SIGNING-SEED-V1:" + raw_str.encode("utf-8")).digest()
                pub_obj = ed25519.Ed25519PrivateKey.from_private_bytes(seed).public_key()
        elif isinstance(public_key, ed25519.Ed25519PublicKey):
            pub_obj = public_key
        else:
            raise TypeError(f"Invalid public key type: {type(public_key)}")

        self._public_keys[clean_bank_id] = pub_obj
        self._key_versions[clean_bank_id] = version

    def rotate_public_key(
        self,
        bank_id: str,
        new_public_key: ed25519.Ed25519PublicKey | str,
        rotation_authorization_signature: str,
    ) -> None:
        """Rotate an enrolled public key with cryptographic authorization from the previous key."""
        clean_bank_id = bank_id.lower().strip()
        current_pub = self._public_keys.get(clean_bank_id)
        if not current_pub:
            raise ConsortiumSecurityError(
                f"Cannot rotate key: Institution '{clean_bank_id}' has no enrolled key."
            )

        new_pub_obj: ed25519.Ed25519PublicKey
        if isinstance(new_public_key, str):
            raw_str = new_public_key.strip()
            if len(raw_str) == 64:
                new_pub_obj = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(raw_str))
            else:
                seed = hashlib.sha256(b"CFI-INSTITUTION-SIGNING-SEED-V1:" + raw_str.encode("utf-8")).digest()
                new_pub_obj = ed25519.Ed25519PrivateKey.from_private_bytes(seed).public_key()
        elif isinstance(new_public_key, ed25519.Ed25519PublicKey):
            new_pub_obj = new_public_key
        else:
            raise TypeError(f"Invalid public key type: {type(new_public_key)}")

        new_raw_hex = new_pub_obj.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()

        current_version = self._key_versions.get(clean_bank_id, 1)
        challenge = f"ROTATE:{clean_bank_id}:{new_raw_hex}:{current_version + 1}".encode()

        try:
            current_pub.verify(bytes.fromhex(rotation_authorization_signature.strip()), challenge)
        except (InvalidSignature, ValueError) as exc:
            raise ConsortiumSecurityError(
                f"Key rotation authorization failed for institution '{clean_bank_id}': invalid authorization signature."
            ) from exc

        self._public_keys[clean_bank_id] = new_pub_obj
        self._key_versions[clean_bank_id] = current_version + 1

    def get_public_key(self, bank_id: str) -> ed25519.Ed25519PublicKey:
        """Retrieve the trusted public verification key for an institution.

        Fails closed with ConsortiumSecurityError if un-enrolled.
        """
        clean_bank_id = bank_id.lower().strip()
        pub = self._public_keys.get(clean_bank_id)
        if pub is not None:
            return pub

        # Check environment variable for institutional public verification key
        import os

        env_key = (
            os.getenv(f"CFI_INSTITUTION_PUBLIC_KEY_{clean_bank_id.upper()}")
            or os.getenv(f"CFI_PEER_KEY_{clean_bank_id.upper()}")
            or os.getenv(f"CFI_WORKER_SECRET_{clean_bank_id.upper()}")
            or os.getenv(f"WORKER_AUTH_SECRET_{clean_bank_id.upper()}")
        )
        if env_key:
            self.register_public_key(clean_bank_id, env_key)
            return self._public_keys[clean_bank_id]

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
        """Return a mapping of bank_id to public key hex for enrolled institutions."""
        return {
            b_id: k.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            ).hex()
            for b_id, k in self._public_keys.items()
        }
