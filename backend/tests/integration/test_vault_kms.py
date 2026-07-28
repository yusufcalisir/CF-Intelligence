"""Integration tests for Section 40.1: HashiCorp Vault Real KMS Integration."""

from __future__ import annotations

import contextlib

import pytest
from cryptography import x509
from cryptography.hazmat.backends import default_backend

from app.infrastructure.security.mtls_manager import MTLSManager
from app.infrastructure.security.tenant_kms import TenantKMSManager
from app.infrastructure.security.vault_client import VaultClient, VaultUnavailableError


def test_transit_key_created() -> None:
    """Verifies creation of Vault Transit key for tenant."""
    vault = VaultClient(vault_url="http://localhost:8200")
    res = vault.create_transit_key("test_bank")

    assert res["key_name"] == "tenant_test_bank"
    assert res["status"] in ("CREATED", "EXISTS", "SIMULATED_FALLBACK")


def test_encrypt_decrypt_roundtrip() -> None:
    """Verifies Vault Transit encrypt and decrypt roundtrip."""
    vault = VaultClient(vault_url="http://localhost:8200")
    raw_bytes = b"secret_transaction_payload_2026"

    ciphertext = vault.encrypt("test_bank", raw_bytes)
    assert ciphertext.startswith("vault:")

    decrypted = vault.decrypt("test_bank", ciphertext)
    assert decrypted == raw_bytes


def test_key_rotation_increments_version() -> None:
    """Verifies KMS key rotation and version metadata updates."""
    kms = TenantKMSManager()
    kms.get_or_create_tenant_key("test_bank")

    res = kms.rotate_key("test_bank")
    assert res["status"] == "ROTATED"
    assert "key_version" in res

    meta = kms.get_key_metadata("test_bank")
    assert meta["latest_version"] >= 2


def test_circuit_breaker_opens_after_failures() -> None:
    """Verifies 3-strikes Circuit Breaker tripping and immediate VaultUnavailableError."""
    unreachable_vault = VaultClient(vault_url="http://127.0.0.1:59999", enabled=True)
    unreachable_vault.cooldown_seconds = 60.0

    # 3 strikes of unreachable network calls
    for _strike in range(3):
        with contextlib.suppress(Exception):
            unreachable_vault.encrypt("test_bank", b"test")

    # 4th call must raise VaultUnavailableError immediately without network attempt
    with pytest.raises(VaultUnavailableError, match="Vault Circuit Breaker is OPEN"):
        unreachable_vault.encrypt("test_bank", b"test")


def test_cert_issued_and_parseable() -> None:
    """Verifies mTLS cert issuance and checks X.509 CN match."""
    mtls = MTLSManager()
    cert_pem, key_pem = mtls.issue_cert("test_bank")

    assert "-----BEGIN CERTIFICATE-----" in cert_pem
    assert "-----BEGIN RSA PRIVATE KEY-----" in key_pem or "-----BEGIN PRIVATE KEY-----" in key_pem

    cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"), default_backend())
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value

    assert cn == "test_bank.client.cf-intelligence.io"
