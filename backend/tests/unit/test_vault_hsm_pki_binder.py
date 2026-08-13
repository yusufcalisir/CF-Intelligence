"""Targeted unit tests for VaultHSMPKIBinder.

Covers:
  - Root CA generation and HSM slot binding
  - Zero-Disk non-exportable key handle guarantee (is_exportable == False)
  - CSR signing roundtrip with HSM hardware handle
  - FIPS 140-2 Level 3 attestation report verification
  - Auto-binding on demand
"""

from __future__ import annotations

import pytest

from app.infrastructure.security.hsm_signer import HSMKeyType, HSMSignerEngine
from app.infrastructure.security.vault_hsm_pki_binder import (
    VaultHSMPKIBinder,
)


@pytest.fixture()
def hsm_engine() -> HSMSignerEngine:
    engine = HSMSignerEngine()
    engine.initialize_session()
    return engine


@pytest.fixture()
def binder(hsm_engine: HSMSignerEngine) -> VaultHSMPKIBinder:
    return VaultHSMPKIBinder(hsm_signer=hsm_engine)


def test_bind_root_ca_success(binder: VaultHSMPKIBinder) -> None:
    """bind_root_ca must generate non-exportable key handle and produce valid Root CA PEM."""
    res = binder.bind_root_ca(key_label="test_root_ca", key_type=HSMKeyType.RSA_4096)

    assert res.key_handle.is_exportable is False
    assert res.key_handle.key_label == "test_root_ca"
    assert res.fips_compliance == "FIPS 140-2 Level 3"
    assert "BEGIN CERTIFICATE" in res.cert_pem
    assert "X-HSM-FIPS-Level: FIPS 140-2 Level 3" in res.cert_pem


def test_sign_certificate_csr_with_hsm(binder: VaultHSMPKIBinder) -> None:
    """sign_certificate_csr must sign CSR using HSM handle and include signature metadata."""
    mock_csr = "-----BEGIN CERTIFICATE REQUEST-----\nMIIB...MOCK_CSR_PAYLOAD\n-----END CERTIFICATE REQUEST-----"

    cert_pem = binder.sign_certificate_csr(
        csr_pem=mock_csr,
        key_label="test_root_ca",
        validity_days=365,
    )

    assert "BEGIN CERTIFICATE" in cert_pem
    assert "X-HSM-Signature:" in cert_pem
    assert "X-HSM-FIPS-Level: FIPS 140-2 Level 3" in cert_pem
    assert "Validity: 365 days" in cert_pem


def test_verify_hsm_attestation_valid(binder: VaultHSMPKIBinder) -> None:
    """verify_hsm_attestation must confirm FIPS 140-2 Level 3 validity for HSM signed certs."""
    res = binder.bind_root_ca()
    report = binder.verify_hsm_attestation(res.cert_pem)

    assert report.is_valid is True
    assert report.fips_level == "FIPS 140-2 Level 3"
    assert report.signature_algorithm == "SHA256withRSA"


def test_verify_hsm_attestation_invalid(binder: VaultHSMPKIBinder) -> None:
    """verify_hsm_attestation must reject invalid or non-HSM cert strings."""
    report = binder.verify_hsm_attestation("INVALID_CERT_STRING")
    assert report.is_valid is False
    assert report.fips_level == "UNKNOWN"


def test_sign_csr_empty_raises(binder: VaultHSMPKIBinder) -> None:
    """Empty CSR string must raise ValueError."""
    with pytest.raises(ValueError, match="csr_pem must be non-empty"):
        binder.sign_certificate_csr("")
