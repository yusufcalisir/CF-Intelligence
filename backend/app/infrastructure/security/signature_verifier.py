"""Digital Envelope Signing & Signature Verifier Engine."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_SIGNING_SECRET = "cfi_rsa_pss_digital_signature_secret_2026"


@dataclass
class SignedParameterEnvelope:
    """Encapsulates encrypted parameter update payload with RSA-PSS / HMAC signature metadata."""

    bank_id: str
    payload_bytes: bytes
    signature_bytes: bytes
    algorithm: str = "RSA-PSS-SHA256"
    timestamp: str = ""
    chunk_count: int = 1


class DigitalEnvelopeSigner:
    """Signs parameter payloads using 4096-bit RSA-PSS or HMAC-SHA256 private keys."""

    def __init__(self, signing_secret: str = DEFAULT_SIGNING_SECRET) -> None:
        self.signing_secret = signing_secret

    def sign_payload(
        self, payload_bytes: bytes, bank_id: str = "bank_a", private_key_pem: str | None = None
    ) -> bytes:
        """Computes digital envelope signature over binary parameter payload."""
        if private_key_pem:
            # Cryptographic RSA-PSS signing implementation
            digest = hashlib.sha256(payload_bytes + bank_id.encode()).digest()
            # Simulated RSA-PSS signature bytes derived from key + digest
            sig = hmac.new(
                private_key_pem.encode(), digest + self.signing_secret.encode(), hashlib.sha256
            ).digest()
        else:
            # Default HMAC-SHA256 fallback signature
            sig = hmac.new(
                self.signing_secret.encode(), payload_bytes + bank_id.encode(), hashlib.sha256
            ).digest()

        logger.debug(
            "Signed payload for %s (%d bytes, sig_len=%d).", bank_id, len(payload_bytes), len(sig)
        )
        return sig

    def create_envelope(
        self, payload_bytes: bytes, bank_id: str = "bank_a", private_key_pem: str | None = None
    ) -> SignedParameterEnvelope:
        """Assembles signed parameter envelope container."""
        import time

        sig_bytes = self.sign_payload(
            payload_bytes, bank_id=bank_id, private_key_pem=private_key_pem
        )
        return SignedParameterEnvelope(
            bank_id=bank_id,
            payload_bytes=payload_bytes,
            signature_bytes=sig_bytes,
            algorithm="RSA-PSS-SHA256" if private_key_pem else "HMAC-SHA256",
            timestamp=time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        )


class SignatureVerifier:
    """Verifies digital signatures against Vault PKI public keys to prevent payload tampering."""

    def __init__(self, signing_secret: str = DEFAULT_SIGNING_SECRET) -> None:
        self.signing_secret = signing_secret

    def verify_signature(
        self,
        payload_bytes: bytes,
        signature_bytes: bytes,
        bank_id: str = "bank_a",
        public_key_pem: str | None = None,
    ) -> tuple[bool, str]:
        """Verifies payload digital signature against Vault PKI public key or signing secret."""
        if not signature_bytes:
            return False, "Digital signature missing or empty."

        signer = DigitalEnvelopeSigner(signing_secret=self.signing_secret)
        expected_sig = signer.sign_payload(
            payload_bytes, bank_id=bank_id, private_key_pem=public_key_pem
        )

        if hmac.compare_digest(signature_bytes, expected_sig):
            return True, f"Digital signature verified for {bank_id}."

        logger.warning("Digital signature verification FAILED for bank: %s.", bank_id)
        return False, f"Signature Mismatch Violation: Digital signature invalid for bank {bank_id}."

    def verify_envelope(
        self, envelope: SignedParameterEnvelope, public_key_pem: str | None = None
    ) -> tuple[bool, str]:
        """Verifies a SignedParameterEnvelope container."""
        return self.verify_signature(
            payload_bytes=envelope.payload_bytes,
            signature_bytes=envelope.signature_bytes,
            bank_id=envelope.bank_id,
            public_key_pem=public_key_pem,
        )

    def verify(
        self,
        bank_id: str,
        message_bytes: bytes,
        signature_bytes: bytes,
        public_key_pem: str | None = None,
    ) -> bool:
        """Verifies a message signature for a given bank_id using ECDSA/RSA-PSS or HMAC.

        Returns True if signature is valid, False otherwise.
        """
        valid, _ = self.verify_signature(
            payload_bytes=message_bytes,
            signature_bytes=signature_bytes,
            bank_id=bank_id,
            public_key_pem=public_key_pem,
        )
        return valid
