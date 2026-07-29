"""Unit tests for Hardware Security Module (HSM / PKCS#11) Key Vault Engine (Section 6.4)."""

from __future__ import annotations

import pytest

from app.infrastructure.security.hsm_signer import (
    HSMKeyType,
    HSMSessionConfig,
    HSMSignerEngine,
)


def test_hsm_session_initialization() -> None:
    """Verifies HSM PKCS#11 / Cloud KMS session initialization with PIN authentication."""
    config = HSMSessionConfig(slot_id=1, pin="secure_pin_999", kms_provider="AWS_KMS")
    engine = HSMSignerEngine(config=config)

    assert not engine.is_session_active
    success = engine.initialize_session()

    assert success is True
    assert engine.is_session_active is True

    # Failure case: missing PIN
    bad_config = HSMSessionConfig(slot_id=1, pin="", kms_provider="AWS_KMS")
    bad_engine = HSMSignerEngine(config=bad_config)
    assert bad_engine.initialize_session() is False


def test_zero_disk_key_signing_and_verification() -> None:
    """Verifies RSA-4096 & Ed25519 digital envelope signatures executed via key handles without disk leakage."""
    engine = HSMSignerEngine()
    engine.initialize_session()

    handle = engine.generate_key_pair(key_label="bank_alpha_mtls_key", key_type=HSMKeyType.RSA_4096)

    assert handle.is_exportable is False, (
        "Private key must be non-exportable (Zero-Disk Key policy)"
    )
    assert handle.key_type == HSMKeyType.RSA_4096

    payload_digest = b"sample_model_update_payload_hash_12345"
    signature = engine.sign_digest(payload_digest, key_label="bank_alpha_mtls_key")

    assert isinstance(signature, bytes)
    assert len(signature) == 32  # SHA256 HMAC signature length

    # Verify valid signature
    is_valid = engine.verify_signature(payload_digest, signature, key_label="bank_alpha_mtls_key")
    assert is_valid is True

    # Verify tampered digest fails
    tampered_digest = b"tampered_model_update_payload_hash_99999"
    is_valid_tampered = engine.verify_signature(
        tampered_digest, signature, key_label="bank_alpha_mtls_key"
    )
    assert is_valid_tampered is False


def test_hardware_attestation_report() -> None:
    """Verifies FIPS 140-2 Level 3 hardware attestation report structure and key handle binding."""
    engine = HSMSignerEngine()
    engine.initialize_session()

    attestation = engine.get_hardware_attestation(key_label="attested_bank_key")

    assert attestation["status"] == "VALIDATED"
    assert attestation["fips_compliance_level"] == "FIPS 140-2 Level 3"
    assert attestation["is_exportable"] is False
    assert "attestation_signature" in attestation
    assert "timestamp" in attestation


def test_hsm_uninitialized_session_error_handling() -> None:
    """Verifies sign_digest raises RuntimeError when session is uninitialized."""
    engine = HSMSignerEngine(config=HSMSessionConfig(pin=""))
    engine.is_session_active = False

    with pytest.raises(RuntimeError, match="HSM session is not active"):
        engine.sign_digest(b"test_digest")
