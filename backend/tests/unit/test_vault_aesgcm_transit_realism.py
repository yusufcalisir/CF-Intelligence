"""Unit tests verifying real cryptographic AES-256-GCM encryption and zero-mock enforcement in VaultClient."""

from __future__ import annotations

import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from app.infrastructure.security.vault_client import VaultClient, VaultUnavailableError


def test_vault_local_aesgcm_genuine_cryptography() -> None:
    """Verifies that offline fallback produces genuine AES-256-GCM ciphertext, not masqueraded plaintext."""
    client = VaultClient(vault_url="http://127.0.0.1:59999", enabled=True)
    secret_payload = b"TOP_SECRET_CREDIT_CARD_PAN_4111_2222_3333_4444"

    ciphertext = client.encrypt("bank_alpha", secret_payload)

    # 1. Honest protocol header
    assert ciphertext.startswith("vault:local_aes256_gcm:v1:")

    # 2. Extract payload and check it is NOT plaintext
    payload_str = ciphertext.removeprefix("vault:local_aes256_gcm:v1:")
    raw_blob = base64.b64decode(payload_str)

    # Plaintext must never appear in raw bytes
    assert secret_payload not in raw_blob
    assert b"TOP_SECRET" not in raw_blob

    # Payload must have at least 12 bytes nonce + 16 bytes tag + plaintext length
    expected_len = 12 + len(secret_payload) + 16
    assert len(raw_blob) == expected_len

    # 3. Decrypt reproduces original bytes
    decrypted = client.decrypt("bank_alpha", ciphertext)
    assert decrypted == secret_payload


def test_vault_local_aesgcm_tamper_detection_fails_closed() -> None:
    """Verifies that tampering with even a single bit in the ciphertext raises InvalidTag."""
    client = VaultClient(vault_url="http://127.0.0.1:59999", enabled=True)
    secret_payload = b"transfer_amount_1000000_usd"

    ciphertext = client.encrypt("bank_beta", secret_payload)
    payload_str = ciphertext.removeprefix("vault:local_aes256_gcm:v1:")
    raw_bytes = bytearray(base64.b64decode(payload_str))

    # Tamper with the authentication tag at the end
    raw_bytes[-1] ^= 0x01
    tampered_payload_str = base64.b64encode(raw_bytes).decode("utf-8")
    tampered_ciphertext = f"vault:local_aes256_gcm:v1:{tampered_payload_str}"

    with pytest.raises(InvalidTag):
        client.decrypt("bank_beta", tampered_ciphertext)


def test_vault_local_aesgcm_strict_tenant_isolation() -> None:
    """Verifies that Tenant A's ciphertext cannot be decrypted by Tenant B."""
    client = VaultClient(vault_url="http://127.0.0.1:59999", enabled=True)
    alpha_payload = b"bank_alpha_confidential_aml_dossier"

    ciphertext_alpha = client.encrypt("bank_alpha", alpha_payload)

    # Attempting to decrypt under bank_beta must fail cryptographic verification
    with pytest.raises(InvalidTag):
        client.decrypt("bank_beta", ciphertext_alpha)


def test_vault_decrypt_remote_vault_fails_closed_without_fabrication() -> None:
    """Verifies that decrypting remote vault:v1 format when Vault is down raises VaultUnavailableError."""
    client = VaultClient(vault_url="http://127.0.0.1:59999", enabled=True)
    remote_ciphertext = "vault:v1:some_remote_vault_ciphertext_data"

    with pytest.raises(VaultUnavailableError, match="Vault decrypt failed"):
        client.decrypt("bank_alpha", remote_ciphertext)


def test_vault_local_aesgcm_large_payload_roundtrip() -> None:
    """Verifies cryptographic roundtrip with arbitrary binary and large payloads."""
    client = VaultClient(vault_url="http://127.0.0.1:59999", enabled=True)
    random_binary = os.urandom(64 * 1024)  # 64 KB binary payload

    ciphertext = client.encrypt("bank_gamma", random_binary)
    assert ciphertext.startswith("vault:local_aes256_gcm:v1:")

    decrypted = client.decrypt("bank_gamma", ciphertext)
    assert decrypted == random_binary
