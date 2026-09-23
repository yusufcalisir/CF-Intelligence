"""Inter-Bank Encrypted FININT Case Messaging Service.

Implements the European Collaborative FININT information-sharing protocol for
cross-institution compliance intelligence exchange. Provides:

- Ephemeral Curve25519 ECDH key agreement → AES-256-GCM symmetric payload encryption.
- SHA-256 evidence attachment hash verification (zero raw file transmission).
- Structured ticket types: URGENT_FREEZE_REQUEST, MULE_ACCOUNT_ALERT,
  INFORMATION_REQUEST, TRANSACTION_DISPUTE_TRACE.
- Immutable SHA-256 hash-chained audit trail for every lifecycle transition.
- Strict ticket state machine enforcing valid FININT compliance workflows.

Privacy invariants:
- Zero raw PII across all inter-bank channels: institution identifiers are
  HMAC-SHA256 hashes, not cleartext names.
- Encrypted payloads are decryptable only by the intended recipient institution.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any

from app.domain.entities_phase2 import FinintBridgeTicket, FinintTicketAuditEntry
from app.domain.enums import FinintTicketStatus, FinintTicketType

logger = logging.getLogger(__name__)

# ── Cryptographic helpers ──────────────────────────────────────────────────────

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    _CRYPTO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CRYPTO_AVAILABLE = False
    logger.warning(
        "cryptography package not available — FININT bridge will use "
        "deterministic software-mode key agreement (non-production fallback)."
    )


def _hkdf_derive(shared_secret: bytes, salt: bytes, info: bytes = b"finint-aes256-gcm") -> bytes:
    """Derive a 256-bit AES key from ECDH shared secret via HKDF-SHA256."""
    if _CRYPTO_AVAILABLE:
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=info,
        )
        return hkdf.derive(shared_secret)
    # Software-mode fallback (test environments without cryptography package)
    return hashlib.sha256(shared_secret + salt + info).digest()


def encrypt_payload(plaintext: bytes, recipient_public_key_b64: str) -> tuple[str, str, str]:
    """Encrypt plaintext using ephemeral Curve25519 ECDH + AES-256-GCM.

    Args:
        plaintext: Raw payload bytes to protect.
        recipient_public_key_b64: Base64-encoded Curve25519 public key of recipient.

    Returns:
        Tuple of (ciphertext_b64, nonce_b64, ephemeral_pubkey_b64) — all base64url-safe.
    """
    recipient_pub_bytes = base64.urlsafe_b64decode(recipient_public_key_b64 + "==")

    if _CRYPTO_AVAILABLE:
        ephemeral_priv = X25519PrivateKey.generate()
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

        recipient_pub = X25519PublicKey.from_public_bytes(recipient_pub_bytes)
        shared_secret = ephemeral_priv.exchange(recipient_pub)
        ephem_pub_bytes = ephemeral_priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    else:
        # Software-mode: generate deterministic ephemeral key pair
        ephem_priv_bytes = hashlib.sha256(recipient_pub_bytes + b"ephemeral").digest()
        ephem_pub_bytes = hashlib.sha256(ephem_priv_bytes).digest()[:32]
        shared_secret = hashlib.sha256(ephem_priv_bytes + recipient_pub_bytes).digest()

    salt = ephem_pub_bytes  # use ephemeral public key bytes as HKDF salt
    aes_key = _hkdf_derive(shared_secret, salt)
    nonce = os.urandom(12)  # 96-bit GCM nonce

    if _CRYPTO_AVAILABLE:
        aesgcm = AESGCM(aes_key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    else:
        # XOR-based software fallback for test environments
        import itertools

        key_stream = bytes(
            a ^ b for a, b in zip(plaintext, itertools.cycle(aes_key + nonce))
        )
        ciphertext = key_stream + hashlib.sha256(key_stream).digest()[:16]

    return (
        base64.urlsafe_b64encode(ciphertext).decode(),
        base64.urlsafe_b64encode(nonce).decode(),
        base64.urlsafe_b64encode(ephem_pub_bytes).decode(),
    )


def decrypt_payload(
    ciphertext_b64: str,
    nonce_b64: str,
    ephemeral_pubkey_b64: str,
    recipient_private_key_b64: str,
) -> bytes:
    """Decrypt AES-GCM ciphertext using recipient's Curve25519 private key.

    Args:
        ciphertext_b64: Base64url-encoded ciphertext + GCM tag.
        nonce_b64: Base64url-encoded 96-bit nonce.
        ephemeral_pubkey_b64: Base64url-encoded sender ephemeral public key.
        recipient_private_key_b64: Base64url-encoded recipient Curve25519 private key.

    Returns:
        Decrypted plaintext bytes.
    """
    ciphertext = base64.urlsafe_b64decode(ciphertext_b64 + "==")
    nonce = base64.urlsafe_b64decode(nonce_b64 + "==")
    ephem_pub_bytes = base64.urlsafe_b64decode(ephemeral_pubkey_b64 + "==")
    priv_bytes = base64.urlsafe_b64decode(recipient_private_key_b64 + "==")

    if _CRYPTO_AVAILABLE:
        from cryptography.hazmat.primitives.asymmetric.x25519 import (
            X25519PrivateKey,
            X25519PublicKey,
        )

        priv = X25519PrivateKey.from_private_bytes(priv_bytes)
        ephem_pub = X25519PublicKey.from_public_bytes(ephem_pub_bytes)
        shared_secret = priv.exchange(ephem_pub)
        aes_key = _hkdf_derive(shared_secret, salt=ephem_pub_bytes)
        aesgcm = AESGCM(aes_key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    else:
        shared_secret = hashlib.sha256(priv_bytes + ephem_pub_bytes).digest()
        aes_key = _hkdf_derive(shared_secret, salt=ephem_pub_bytes)
        import itertools

        raw = ciphertext[:-16]  # strip software-mode tag
        return bytes(
            a ^ b for a, b in zip(raw, itertools.cycle(aes_key + nonce))
        )


def decrypt_payload_with_hsm(
    ciphertext_b64: str,
    nonce_b64: str,
    ephemeral_pubkey_b64: str,
    hsm_key_service: Any,
    key_label: str = "cfi_node_identity_key",
) -> bytes:
    """Decrypt AES-GCM ciphertext using Curve25519 shared secret derived directly inside HSM.

    Zero-Process-Memory Private Key Exposure: The recipient private scalar never enters Python
    process memory; the shared secret is derived within the HSM / Vault Transit enclave boundary.
    """
    ciphertext = base64.urlsafe_b64decode(ciphertext_b64 + "==")
    nonce = base64.urlsafe_b64decode(nonce_b64 + "==")
    ephem_pub_bytes = base64.urlsafe_b64decode(ephemeral_pubkey_b64 + "==")

    shared_secret = hsm_key_service.derive_shared_secret(ephem_pub_bytes, key_label=key_label)
    aes_key = _hkdf_derive(shared_secret, salt=ephem_pub_bytes)

    if _CRYPTO_AVAILABLE:
        aesgcm = AESGCM(aes_key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    else:
        import itertools

        raw = ciphertext[:-16]
        return bytes(a ^ b for a, b in zip(raw, itertools.cycle(aes_key + nonce)))


def generate_bank_keypair() -> tuple[str, str]:
    """Generate a Curve25519 keypair for a bank node (private_b64, public_b64).

    Returns:
        (private_key_b64, public_key_b64) both base64url-safe encoded.
    """
    if _CRYPTO_AVAILABLE:
        priv = X25519PrivateKey.generate()
        priv_bytes = priv.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        )
        pub_bytes = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    else:
        priv_bytes = os.urandom(32)
        pub_bytes = hashlib.sha256(priv_bytes).digest()

    return (
        base64.urlsafe_b64encode(priv_bytes).decode().rstrip("="),
        base64.urlsafe_b64encode(pub_bytes).decode().rstrip("="),
    )


# ── Audit chain helpers ────────────────────────────────────────────────────────


def _compute_event_hash(
    previous_hash: str,
    seq: int,
    actor: str,
    action: str,
    timestamp: datetime,
) -> str:
    """Compute SHA-256 hash for an audit chain entry."""
    raw = f"{previous_hash}|{seq}|{actor}|{action}|{timestamp.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _append_audit_entry(
    ticket: FinintBridgeTicket,
    actor: str,
    action: str,
    new_status: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a new hash-chained audit entry to the ticket's audit trail."""
    seq = len(ticket.audit_trail)
    previous_hash = ticket.head_hash
    ts = datetime.now(UTC)
    event_hash = _compute_event_hash(previous_hash, seq, actor, action, ts)
    entry = FinintTicketAuditEntry(
        seq=seq,
        actor=actor,
        action=action,
        previous_status=ticket.status,
        new_status=new_status,
        event_hash=event_hash,
        previous_hash=previous_hash,
        timestamp=ts,
        metadata=metadata or {},
    )
    ticket.audit_trail.append(entry)
    ticket.head_hash = event_hash


# ── Ticket state machine ───────────────────────────────────────────────────────

# Valid state transitions for the FININT ticket lifecycle
_VALID_TRANSITIONS: dict[str, set[str]] = {
    FinintTicketStatus.OPEN: {
        FinintTicketStatus.ACKNOWLEDGED,
        FinintTicketStatus.DECLINED,
    },
    FinintTicketStatus.ACKNOWLEDGED: {
        FinintTicketStatus.FUNDS_FROZEN,
        FinintTicketStatus.INFORMATION_ATTACHED,
        FinintTicketStatus.DECLINED,
    },
    FinintTicketStatus.FUNDS_FROZEN: {
        FinintTicketStatus.INFORMATION_ATTACHED,
        FinintTicketStatus.CLOSED,
    },
    FinintTicketStatus.INFORMATION_ATTACHED: {
        FinintTicketStatus.CLOSED,
    },
    FinintTicketStatus.DECLINED: {
        FinintTicketStatus.CLOSED,
    },
    FinintTicketStatus.CLOSED: set(),
}


class InvalidTicketTransitionError(ValueError):
    """Raised when an invalid FININT ticket state transition is attempted."""


class TicketNotFoundError(KeyError):
    """Raised when a FININT bridge ticket is not found."""


# ── SLA definitions (hours) per ticket type ───────────────────────────────────
_SLA_HOURS: dict[str, int] = {
    FinintTicketType.URGENT_FREEZE_REQUEST: 4,
    FinintTicketType.MULE_ACCOUNT_ALERT: 24,
    FinintTicketType.INFORMATION_REQUEST: 72,
    FinintTicketType.TRANSACTION_DISPUTE_TRACE: 48,
}


# ── Bridge Case Service ────────────────────────────────────────────────────────


class BridgeCaseService:
    """Encrypted inter-bank FININT case messaging service.

    Manages the full lifecycle of cross-institution FININT tickets:
    - Creation with E2EE payload (Curve25519 ECDH + AES-256-GCM).
    - SHA-256 evidence attachment registration and verification.
    - State machine transitions with immutable hash-chained audit logging.
    - Listing and retrieval filtered by originating or recipient bank.

    Thread-safe: all mutable state is protected by a reentrant lock.
    """

    def __init__(self) -> None:
        self._tickets: dict[str, FinintBridgeTicket] = {}
        self._lock = threading.RLock()

    # ── Key registry (in-memory for consortium banks) ──────────────────────────

    def _get_tickets(self) -> dict[str, FinintBridgeTicket]:
        return self._tickets

    # ── Ticket creation ────────────────────────────────────────────────────────

    def create_ticket(
        self,
        ticket_type: str,
        originating_bank_id: str,
        recipient_bank_id: str,
        plaintext_payload: bytes,
        recipient_public_key_b64: str,
        evidence_bytes_list: list[bytes] | None = None,
        actor: str = "system",
    ) -> FinintBridgeTicket:
        """Create an encrypted FININT bridge ticket.

        Args:
            ticket_type: One of the FinintTicketType values.
            originating_bank_id: HMAC-SHA256 identifier of originating institution.
            recipient_bank_id: HMAC-SHA256 identifier of target institution.
            plaintext_payload: Structured request payload (JSON bytes).
            recipient_public_key_b64: Base64url-encoded Curve25519 public key.
            evidence_bytes_list: Optional list of raw evidence bytes to hash.
            actor: Anonymised officer identifier.

        Returns:
            FinintBridgeTicket with encrypted payload and initialised audit trail.
        """
        if ticket_type not in {t.value for t in FinintTicketType}:
            raise ValueError(f"Unknown ticket type: {ticket_type!r}")

        ciphertext_b64, nonce_b64, ephem_pub_b64 = encrypt_payload(
            plaintext_payload, recipient_public_key_b64
        )

        evidence_hashes = [
            hashlib.sha256(ev).hexdigest() for ev in (evidence_bytes_list or [])
        ]

        sla_hours = _SLA_HOURS.get(ticket_type, 48)

        ticket = FinintBridgeTicket(
            ticket_type=ticket_type,
            status=FinintTicketStatus.OPEN,
            originating_bank_id=originating_bank_id,
            recipient_bank_id=recipient_bank_id,
            encrypted_payload=ciphertext_b64,
            payload_nonce=nonce_b64,
            ephemeral_public_key=ephem_pub_b64,
            evidence_hashes=evidence_hashes,
            sla_hours=sla_hours,
        )

        _append_audit_entry(
            ticket,
            actor=actor,
            action="TICKET_CREATED",
            new_status=FinintTicketStatus.OPEN,
            metadata={"ticket_type": ticket_type, "sla_hours": sla_hours},
        )

        with self._lock:
            self._tickets[ticket.id] = ticket

        logger.info(
            "FININT ticket created: id=%s type=%s originator=%s recipient=%s",
            ticket.id,
            ticket_type,
            originating_bank_id,
            recipient_bank_id,
        )
        return ticket

    # ── State machine ──────────────────────────────────────────────────────────

    def transition_status(
        self,
        ticket_id: str,
        new_status: str,
        actor: str,
        action: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> FinintBridgeTicket:
        """Transition a ticket to a new lifecycle state.

        Args:
            ticket_id: UUID of the ticket to update.
            new_status: Target FinintTicketStatus value.
            actor: Anonymised officer identifier.
            action: Human-readable action description for the audit trail.
            metadata: Optional additional context for the audit entry.

        Raises:
            TicketNotFoundError: If ticket_id does not exist.
            InvalidTicketTransitionError: If the transition violates the state machine.
        """
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise TicketNotFoundError(ticket_id)

            allowed = _VALID_TRANSITIONS.get(ticket.status, set())
            if new_status not in allowed:
                raise InvalidTicketTransitionError(
                    f"Transition {ticket.status!r} → {new_status!r} is not permitted. "
                    f"Valid targets: {sorted(allowed)}"
                )

            audit_action = action or f"STATUS_CHANGED_TO_{new_status}"
            _append_audit_entry(ticket, actor=actor, action=audit_action, new_status=new_status, metadata=metadata)

            ticket.status = new_status
            ticket.updated_at = datetime.now(UTC)
            if new_status == FinintTicketStatus.CLOSED:
                ticket.closed_at = datetime.now(UTC)

        logger.info(
            "FININT ticket %s transitioned to %s by %s", ticket_id, new_status, actor
        )
        return ticket

    # ── Evidence attachment ────────────────────────────────────────────────────

    def attach_evidence(
        self,
        ticket_id: str,
        evidence_bytes: bytes,
        actor: str,
    ) -> str:
        """Register an evidence attachment by its SHA-256 hash.

        Args:
            ticket_id: UUID of the ticket to update.
            evidence_bytes: Raw evidence bytes (never stored; only the hash).
            actor: Anonymised officer identifier.

        Returns:
            SHA-256 hex digest of the attached evidence.
        """
        evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise TicketNotFoundError(ticket_id)
            if evidence_hash not in ticket.evidence_hashes:
                ticket.evidence_hashes.append(evidence_hash)
            ticket.updated_at = datetime.now(UTC)
            _append_audit_entry(
                ticket,
                actor=actor,
                action="EVIDENCE_ATTACHED",
                new_status=ticket.status,
                metadata={"evidence_hash": evidence_hash},
            )
        return evidence_hash

    def verify_evidence(self, ticket_id: str, evidence_bytes: bytes) -> bool:
        """Verify that evidence bytes match a registered hash on the ticket.

        Args:
            ticket_id: UUID of the ticket to check.
            evidence_bytes: Raw bytes to verify.

        Returns:
            True if SHA-256 of evidence matches any registered hash.
        """
        evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise TicketNotFoundError(ticket_id)
            return evidence_hash in ticket.evidence_hashes

    # ── Retrieval ──────────────────────────────────────────────────────────────

    def get_ticket(self, ticket_id: str) -> FinintBridgeTicket:
        """Retrieve a ticket by its UUID.

        Raises:
            TicketNotFoundError: If the ticket does not exist.
        """
        with self._lock:
            ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise TicketNotFoundError(ticket_id)
        return ticket

    def list_tickets(
        self,
        bank_id: str | None = None,
        status_filter: str | None = None,
        ticket_type_filter: str | None = None,
        limit: int = 100,
    ) -> list[FinintBridgeTicket]:
        """Return tickets optionally filtered by bank, status, or type.

        Args:
            bank_id: Filter to tickets where bank is originator OR recipient.
            status_filter: Exact FinintTicketStatus value match.
            ticket_type_filter: Exact FinintTicketType value match.
            limit: Maximum number of results returned (default 100).
        """
        with self._lock:
            tickets = list(self._tickets.values())

        if bank_id:
            tickets = [
                t for t in tickets
                if t.originating_bank_id == bank_id or t.recipient_bank_id == bank_id
            ]
        if status_filter:
            tickets = [t for t in tickets if t.status == status_filter]
        if ticket_type_filter:
            tickets = [t for t in tickets if t.ticket_type == ticket_type_filter]

        tickets.sort(key=lambda t: t.created_at, reverse=True)
        return tickets[:limit]

    # ── Audit chain verification ───────────────────────────────────────────────

    def verify_audit_chain(self, ticket_id: str) -> bool:
        """Verify the SHA-256 hash chain integrity of a ticket's audit trail.

        Recomputes each event hash from scratch and confirms sequential linkage.

        Returns:
            True if the entire chain is intact; False if any entry is tampered.
        """
        with self._lock:
            ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise TicketNotFoundError(ticket_id)

        running_hash = ""
        for entry in ticket.audit_trail:
            expected = _compute_event_hash(
                running_hash, entry.seq, entry.actor, entry.action, entry.timestamp
            )
            if not hmac.compare_digest(expected, entry.event_hash):
                logger.error(
                    "FININT audit chain breach detected at seq=%d for ticket=%s",
                    entry.seq,
                    ticket_id,
                )
                return False
            running_hash = entry.event_hash

        return True

    # ── Hardware-Anchored HSM Ticket Attestation ──────────────────────────────

    def sign_ticket_with_hsm(
        self,
        ticket_id: str,
        hsm_key_service: Any,
        actor: str = "HSM_OPERATOR",
        key_label: str = "cfi_node_identity_key",
    ) -> str:
        """Sign the ticket's immutable audit chain head with an HSM hardware key.

        Enforces non-repudiation across institutional boundaries without exposing
        the bank node's private signing key in process memory.

        Returns:
            URL-safe Base64-encoded hardware digital signature.
        """
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise TicketNotFoundError(ticket_id)

            head_str = ticket.head_hash
            sig_bytes = hsm_key_service.sign_payload(head_str.encode("utf-8"), key_label=key_label)
            sig_b64 = base64.urlsafe_b64encode(sig_bytes).decode().rstrip("=")

            _append_audit_entry(
                ticket,
                actor=actor,
                action="TICKET_SIGNED_HSM",
                new_status=ticket.status,
                metadata={
                    "hsm_key_label": key_label,
                    "signature": sig_b64,
                    "signed_head_hash": head_str,
                    "provider": getattr(hsm_key_service, "provider", "PKCS11"),
                },
            )
            return sig_b64

    def verify_ticket_hsm_signature(
        self,
        ticket_id: str,
        signature_b64: str,
        hsm_key_service: Any,
        key_label: str = "cfi_node_identity_key",
    ) -> bool:
        """Verify that a ticket's audit chain was attested by the specified HSM key."""
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise TicketNotFoundError(ticket_id)

            # Find the audit entry containing this HSM signature
            target_hash = ticket.head_hash
            for entry in reversed(ticket.audit_trail):
                if entry.action == "TICKET_SIGNED_HSM" and entry.metadata.get("signature") == signature_b64:
                    target_hash = entry.metadata.get("signed_head_hash", entry.previous_hash)
                    break

        sig_bytes = base64.urlsafe_b64decode(signature_b64 + "==")
        return hsm_key_service.verify_signature(
            hashlib.sha256(target_hash.encode("utf-8")).digest(),
            sig_bytes,
            key_label=key_label,
        )

    # ── Metrics ───────────────────────────────────────────────────────────────

    def get_metrics(self) -> dict[str, Any]:
        """Return aggregate FININT bridge metrics."""
        with self._lock:
            tickets = list(self._tickets.values())

        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for t in tickets:
            by_status[t.status] = by_status.get(t.status, 0) + 1
            by_type[t.ticket_type] = by_type.get(t.ticket_type, 0) + 1

        return {
            "total_tickets": len(tickets),
            "by_status": by_status,
            "by_type": by_type,
            "crypto_backend": "cryptography-x25519-aesgcm" if _CRYPTO_AVAILABLE else "software-fallback",
        }


# ── Module-level singleton ─────────────────────────────────────────────────────

_bridge_service: BridgeCaseService | None = None
_bridge_lock = threading.Lock()


def get_bridge_service() -> BridgeCaseService:
    """Return the module-level BridgeCaseService singleton."""
    global _bridge_service
    if _bridge_service is None:
        with _bridge_lock:
            if _bridge_service is None:
                _bridge_service = BridgeCaseService()
    return _bridge_service
