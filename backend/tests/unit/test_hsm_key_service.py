"""Unit tests for Hardware Security Module (HSM) PKCS#11 & Vault Transit Key Wrapper.

Validates:
- Hardware-backed key lifecycle and non-exportable private key invariant (Zero-Disk Key).
- Curve25519 / X25519 ECDH shared secret derivation inside enclave boundary.
- Hardware digital signatures and verification (RSA-PSS, ECDSA, Ed25519, Vault Transit).
- AES-256-GCM envelope encryption and decryption within hardware boundary.
- Automated consortium mTLS 1.3 certificate rotation check and expiry alerts.
- X.509 consortium peer thumbprint attestation against approved whitelists.
- FIPS 140-2 Level 3 hardware attestation report generation.
- Cross-module integration with BridgeCaseService and KMSService.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.x509.oid import NameOID

from app.application.services.bridge_case_service import (
    BridgeCaseService,
    decrypt_payload_with_hsm,
    encrypt_payload,
)
from app.application.services.kms_service import KMSService
from app.domain.enums import FinintTicketType
from app.infrastructure.security.cert_generator import generate_self_signed_pem
from app.infrastructure.security.hsm_key_service import (
    CertRotationStatus,
    HSMKeyService,
    HSMProvider,
    KeyAlgorithm,
)


def _generate_test_cert_with_validity(days_from_now: int, common_name: str = "node.bank-a.eu") -> str:
    """Helper generating a certificate that expires exactly `days_from_now` days from today."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=10))
        .not_valid_after(now + timedelta(days=days_from_now))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")


class TestHSMKeyLifecycle:
    """Tests for HSM key generation, non-exportability, and provider resolution."""

    def test_pkcs11_initialization_and_key_generation(self) -> None:
        service = HSMKeyService(provider=HSMProvider.PKCS11)
        meta = service.generate_key(key_label="test_sig_key", algorithm=KeyAlgorithm.ED25519)

        assert meta.key_id.startswith("hsm_")
        assert meta.key_label == "test_sig_key"
        assert meta.algorithm == KeyAlgorithm.ED25519
        assert meta.is_exportable is False  # Zero-Disk private key guarantee
        assert "FIPS 140-2 Level 3" in meta.fips_compliance_level
        assert meta.public_key_b64 is not None

    def test_zero_disk_key_enforcement_ignores_exportable_flag(self) -> None:
        service = HSMKeyService(provider=HSMProvider.PKCS11)
        # Even if an attacker requests exportable=True, policy forces is_exportable=False
        meta = service.generate_key(key_label="node_sec_key", exportable=True)
        assert meta.is_exportable is False

    def test_key_generation_across_all_algorithms(self) -> None:
        service = HSMKeyService(provider=HSMProvider.PKCS11)
        for algo in (
            KeyAlgorithm.ED25519,
            KeyAlgorithm.CURVE25519,
            KeyAlgorithm.ECDSA_P256,
            KeyAlgorithm.RSA_4096,
            KeyAlgorithm.AES_256_GCM,
        ):
            meta = service.generate_key(key_label=f"key_{algo.value}", algorithm=algo)
            assert meta.algorithm == algo
            assert meta.is_exportable is False

    def test_provider_fallback_on_unknown(self) -> None:
        service = HSMKeyService(provider="UNKNOWN_HARDWARE_SLOT")
        assert service.provider == HSMProvider.LOCAL_EMULATED

    def test_get_key_metadata_idempotent(self) -> None:
        service = HSMKeyService(provider=HSMProvider.PKCS11)
        meta1 = service.generate_key("idempotent_key")
        meta2 = service.get_key_metadata("idempotent_key")
        assert meta1.key_id == meta2.key_id
        assert meta1.key_label == meta2.key_label


class TestHSMSharedSecretDerivation:
    """Tests for Curve25519 / X25519 ECDH key agreement within hardware boundary."""

    def test_curve25519_shared_secret_bilateral_exchange(self) -> None:
        # Bank A uses HSM
        hsm_bank_a = HSMKeyService(provider=HSMProvider.PKCS11)
        meta_a = hsm_bank_a.generate_key("bank_a_ecdh", algorithm=KeyAlgorithm.CURVE25519)
        pub_a_bytes = base64.urlsafe_b64decode(meta_a.public_key_b64 + "==")

        # Bank B generates peer keypair
        priv_b = X25519PrivateKey.generate()
        pub_b_bytes = priv_b.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

        # HSM Bank A derives shared secret with Bank B's public key
        shared_secret_a = hsm_bank_a.derive_shared_secret(pub_b_bytes, key_label="bank_a_ecdh")

        # Bank B derives shared secret with Bank A's public key
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

        shared_secret_b = priv_b.exchange(X25519PublicKey.from_public_bytes(pub_a_bytes))

        # Both derived identical 32-byte shared secrets
        assert len(shared_secret_a) == 32
        assert shared_secret_a == shared_secret_b

    def test_distinct_peers_produce_distinct_shared_secrets(self) -> None:
        hsm = HSMKeyService(provider=HSMProvider.PKCS11)
        hsm.generate_key("multi_peer_key", algorithm=KeyAlgorithm.CURVE25519)

        peer1 = X25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        peer2 = X25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

        sec1 = hsm.derive_shared_secret(peer1, key_label="multi_peer_key")
        sec2 = hsm.derive_shared_secret(peer2, key_label="multi_peer_key")

        assert sec1 != sec2


class TestHSMDigitalSignatures:
    """Tests for hardware digital signatures and tamper detection."""

    def test_sign_and_verify_digest_success(self) -> None:
        service = HSMKeyService(provider=HSMProvider.PKCS11)
        digest = hashlib.sha256(b"compliance_audit_record_12345").digest()

        sig = service.sign_digest(digest, key_label="audit_signer")
        assert len(sig) > 0
        assert service.verify_signature(digest, sig, key_label="audit_signer") is True

    def test_tampered_digest_fails_verification(self) -> None:
        service = HSMKeyService(provider=HSMProvider.PKCS11)
        digest1 = hashlib.sha256(b"original_payload").digest()
        digest2 = hashlib.sha256(b"tampered_payload").digest()

        sig = service.sign_digest(digest1, key_label="audit_signer")
        assert service.verify_signature(digest2, sig, key_label="audit_signer") is False

    def test_tampered_signature_fails_verification(self) -> None:
        service = HSMKeyService(provider=HSMProvider.PKCS11)
        digest = hashlib.sha256(b"original_payload").digest()

        sig = bytearray(service.sign_digest(digest, key_label="audit_signer"))
        sig[0] ^= 0xFF  # Corrupt first byte
        assert service.verify_signature(digest, bytes(sig), key_label="audit_signer") is False

    def test_sign_payload_helper(self) -> None:
        service = HSMKeyService(provider=HSMProvider.PKCS11)
        payload = b'{"action": "FREEZE_FUNDS", "account": "DE89370400440532013000"}'
        sig = service.sign_payload(payload, key_label="action_signer")
        digest = hashlib.sha256(payload).digest()
        assert service.verify_signature(digest, sig, key_label="action_signer") is True


class TestHSMEnvelopeEncryption:
    """Tests for hardware-anchored AES-256-GCM envelope encryption."""

    def test_encrypt_decrypt_roundtrip(self) -> None:
        service = HSMKeyService(provider=HSMProvider.PKCS11)
        secret_payload = b"CRITICAL_FININT_SUSPECT_SAR_ATTACHMENT_2026"

        envelope = service.encrypt_envelope(secret_payload, key_label="env_key")
        assert "ciphertext" in envelope
        assert "nonce" in envelope

        decrypted = service.decrypt_envelope(
            envelope["ciphertext"],
            key_label="env_key",
            nonce_b64=envelope.get("nonce"),
        )
        assert decrypted == secret_payload

    def test_different_payloads_produce_different_ciphertexts(self) -> None:
        service = HSMKeyService(provider=HSMProvider.PKCS11)
        env1 = service.encrypt_envelope(b"payload_alpha", key_label="env_key")
        env2 = service.encrypt_envelope(b"payload_beta", key_label="env_key")
        assert env1["ciphertext"] != env2["ciphertext"]


class TestVaultTransitProvider:
    """Tests for HashiCorp Vault Transit engine wrapper mode."""

    def test_vault_transit_provider_initialization(self) -> None:
        service = HSMKeyService(
            provider=HSMProvider.VAULT_TRANSIT,
            vault_url="http://vault.internal:8200",
            vault_token="dev-test-token",
            transit_mount="transit",
        )
        meta = service.generate_key("consortium_master_transit", algorithm=KeyAlgorithm.RSA_4096)
        assert meta.provider == HSMProvider.VAULT_TRANSIT
        assert meta.key_id.startswith("vault_transit_")
        assert meta.is_exportable is False

    def test_vault_transit_envelope_offline_resilience(self) -> None:
        service = HSMKeyService(
            provider=HSMProvider.VAULT_TRANSIT,
            vault_url="http://nonexistent-vault.internal:8200",
            transit_mount="transit",
        )
        # Resilient fallback handles offline Vault safely without crashing
        data = b"offline_vault_resilience_test"
        envelope = service.encrypt_envelope(data, key_label="resilience_key")
        assert "ciphertext" in envelope


class TestConsortiumCertificateRotation:
    """Tests for automated mTLS 1.3 certificate rotation monitoring."""

    def test_valid_certificate_no_rotation_required(self) -> None:
        service = HSMKeyService()
        cert_pem = _generate_test_cert_with_validity(days_from_now=90)
        report = service.check_certificate_rotation(cert_pem, days_threshold=30)

        assert report.status == CertRotationStatus.VALID
        assert report.rotation_required is False
        assert report.days_remaining >= 80
        assert report.threshold_days == 30
        assert "node.bank-a.eu" in report.subject

    def test_certificate_near_expiry_requires_rotation(self) -> None:
        service = HSMKeyService()
        cert_pem = _generate_test_cert_with_validity(days_from_now=15)
        report = service.check_certificate_rotation(cert_pem, days_threshold=30)

        assert report.status == CertRotationStatus.ROTATION_REQUIRED
        assert report.rotation_required is True
        assert report.days_remaining <= 15

    def test_expired_certificate_triggers_expired_status(self) -> None:
        service = HSMKeyService()
        cert_pem = _generate_test_cert_with_validity(days_from_now=-5)
        report = service.check_certificate_rotation(cert_pem, days_threshold=30)

        assert report.status == CertRotationStatus.EXPIRED
        assert report.rotation_required is True
        assert report.days_remaining < 0


class TestPeerThumbprintAttestation:
    """Tests for consortium X.509 peer thumbprint verification."""

    def test_trusted_peer_attestation_success(self) -> None:
        service = HSMKeyService()
        cert_pem, _ = generate_self_signed_pem("trusted.bank-node.consortium.eu")

        # Compute certificate's DER thumbprint
        cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
        der_bytes = cert.public_bytes(serialization.Encoding.DER)
        expected_thumbprint = hashlib.sha256(der_bytes).hexdigest()

        whitelist = [expected_thumbprint, "deadbeef1234567890abcdef"]
        report = service.attest_peer_thumbprint(cert_pem, whitelist)

        assert report.is_attested is True
        assert report.attestation_status == "TRUSTED"
        assert report.thumbprint_sha256 == expected_thumbprint.lower()
        assert report.common_name == "trusted.bank-node.consortium.eu"

    def test_untrusted_peer_attestation_rejected(self) -> None:
        service = HSMKeyService()
        cert_pem, _ = generate_self_signed_pem("rogue.adversary.attacker.eu")

        whitelist = ["aa:bb:cc:dd:ee:ff:00:11:22:33:44:55", "1234567890abcdef"]
        report = service.attest_peer_thumbprint(cert_pem, whitelist)

        assert report.is_attested is False
        assert report.attestation_status == "UNTRUSTED"


class TestFIPSAttestationReport:
    """Tests for FIPS 140-2 Level 3 hardware attestation reports."""

    def test_fips_attestation_report_contents(self) -> None:
        service = HSMKeyService(provider=HSMProvider.PKCS11)
        service.generate_key("fips_attested_key")
        report = service.get_hardware_attestation("fips_attested_key")

        assert report["status"] == "ATTESTED"
        assert report["key_label"] == "fips_attested_key"
        assert report["is_exportable"] is False
        assert "FIPS 140-2 Level 3" in report["fips_compliance_level"]
        assert report["attestation_signature"] is not None
        assert "timestamp" in report


class TestBridgeCaseServiceHSMIntegration:
    """Tests for BridgeCaseService hardware ticket attestation and payload decryption."""

    def test_decrypt_payload_with_hsm(self) -> None:
        hsm_service = HSMKeyService(provider=HSMProvider.PKCS11)
        meta = hsm_service.generate_key("recipient_bank_hsm", algorithm=KeyAlgorithm.CURVE25519)

        # Sender encrypts for recipient
        plaintext = b"URGENT_AML_ALERT: Laundering ring detected at account NL91ABNA0417164300"
        ct_b64, nonce_b64, ephem_pub_b64 = encrypt_payload(plaintext, meta.public_key_b64)

        # Recipient decrypts using HSM without accessing private scalar
        decrypted = decrypt_payload_with_hsm(
            ct_b64,
            nonce_b64,
            ephem_pub_b64,
            hsm_service,
            key_label="recipient_bank_hsm",
        )
        assert decrypted == plaintext

    def test_ticket_signing_and_verification_with_hsm(self) -> None:
        bridge = BridgeCaseService()
        hsm = HSMKeyService(provider=HSMProvider.PKCS11)
        meta = hsm.generate_key("recipient_pub_key", algorithm=KeyAlgorithm.CURVE25519)

        ticket = bridge.create_ticket(
            ticket_type=FinintTicketType.URGENT_FREEZE_REQUEST.value,
            originating_bank_id="bank_alpha",
            recipient_bank_id="bank_beta",
            plaintext_payload=b'{"freeze_reason": "APP_FRAUD"}',
            recipient_public_key_b64=meta.public_key_b64,
            actor="compliance_officer_1",
        )

        # Sign ticket audit head with HSM
        sig_b64 = bridge.sign_ticket_with_hsm(ticket.id, hsm, key_label="bank_alpha_hsm_key")
        assert len(sig_b64) > 0

        # Verify signature
        assert bridge.verify_ticket_hsm_signature(
            ticket.id, sig_b64, hsm, key_label="bank_alpha_hsm_key"
        ) is True

        # Confirm audit trail contains TICKET_SIGNED_HSM
        assert any(entry.action == "TICKET_SIGNED_HSM" for entry in ticket.audit_trail)


class TestKMSServiceHSMIntegration:
    """Tests for KMSService integration with HSM key delegation."""

    def test_kms_hsm_delegation(self, tmp_path: pytest.TempPathFactory) -> None:
        kms = KMSService(storage_root=str(tmp_path))
        bank_id = "bank_nordic_01"

        hsm = kms.get_hsm_key_service(bank_id)
        assert isinstance(hsm, HSMKeyService)

        # Sign digest using tenant HSM
        digest = hashlib.sha256(b"tenant_transaction_batch_001").digest()
        sig = kms.sign_tenant_digest_with_hsm(bank_id, digest)
        assert len(sig) > 0

        # Derive shared secret using tenant HSM
        peer_key = X25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        secret = kms.derive_tenant_shared_secret(bank_id, peer_key)
        assert len(secret) == 32
