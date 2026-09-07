"""Unit tests for Phase 12: Cryptographic Key Lifecycle, Multi-Version Re-Encryption, and Vault Integration."""

import os
import pytest
from cryptography.fernet import InvalidToken
from fastapi.testclient import TestClient

from app.infrastructure.security.tenant_kms import TenantKMSManager
from app.application.services.kms_service import KMSService
from app.infrastructure.security.vault_client import VaultClient
from app.main import app


def test_tenant_kms_multi_version_envelope_and_reencryption():
    """Verify envelope encryption, key rotation, live re-encryption, and retired key invalidation."""
    kms = TenantKMSManager(master_secret="test_phase12_master_secret")
    tenant = "bank_gamma"
    plaintext = "super_confidential_account_balance_12345"

    # 1. Initial encryption (version 1)
    ciphertext_v1 = kms.encrypt_tenant_data(tenant, plaintext)
    assert ciphertext_v1.startswith("v1:")
    assert kms.decrypt_tenant_data(tenant, ciphertext_v1) == plaintext

    # 2. Key rotation: version 1 becomes RETIRED, version 2 becomes ACTIVE
    rot_meta = kms.rotate_key(tenant)
    assert rot_meta["status"] == "ROTATED"
    assert rot_meta["active_version"] == 2
    meta = kms.get_key_metadata(tenant)
    assert meta["latest_version"] == 2
    assert meta["retired_versions"] == 1
    assert meta["revoked_versions"] == 0

    # 3. New encryption uses version 2
    ciphertext_v2 = kms.encrypt_tenant_data(tenant, "new_transaction_payload")
    assert ciphertext_v2.startswith("v2:")

    # 4. Old ciphertext_v1 still decrypts while v1 is in RETIRED status
    assert kms.decrypt_tenant_data(tenant, ciphertext_v1) == plaintext

    # 5. Live Re-encryption (rewrapping): upgrades ciphertext_v1 to active version 2
    reencrypted_v2 = kms.re_encrypt_tenant_data(tenant, ciphertext_v1)
    assert reencrypted_v2.startswith("v2:")
    assert kms.decrypt_tenant_data(tenant, reencrypted_v2) == plaintext

    # 6. Invalidate retired keys (v1 is now REVOKED)
    revoked = kms.invalidate_retired_keys(tenant)
    assert 1 in revoked

    meta_after = kms.get_key_metadata(tenant)
    assert meta_after["revoked_versions"] == 1

    # 7. Decryption of un-reencrypted v1 ciphertext is REJECTED
    with pytest.raises(InvalidToken, match="REVOKED/INVALIDATED"):
        kms.decrypt_tenant_data(tenant, ciphertext_v1)

    # 8. Re-encrypted v2 ciphertext decrypts successfully
    assert kms.decrypt_tenant_data(tenant, reencrypted_v2) == plaintext


def test_tenant_kms_cross_tenant_isolation_across_versions():
    """Verify Bank B cannot decrypt Bank A's data across any key version."""
    kms = TenantKMSManager(master_secret="test_isolation_master_secret")
    c_a = kms.encrypt_tenant_data("bank_a", "bank_a_secret_payload")
    kms.rotate_key("bank_a")
    c_a_v2 = kms.encrypt_tenant_data("bank_a", "bank_a_v2_payload")

    with pytest.raises(InvalidToken):
        kms.decrypt_tenant_data("bank_b", c_a)

    with pytest.raises(InvalidToken):
        kms.decrypt_tenant_data("bank_b", c_a_v2)


def test_kms_service_multi_version_envelope_and_invalidation(tmp_path):
    """Verify KMSService filesystem vault multi-version envelope encryption, re-encryption and invalidation."""
    kms_svc = KMSService(storage_root=str(tmp_path))
    bank_id = "test_bank_fs"
    payload = "stored_pii_token_account_98765"

    # 1. Encrypt under initial envelope key v1
    c1 = kms_svc.encrypt_data(bank_id, payload)
    assert c1.startswith("v1:")
    assert kms_svc.decrypt_data(bank_id, c1) == payload

    # 2. Rotate envelope key to v2
    new_key = kms_svc.rotate_key(bank_id, "envelope_key")
    assert new_key is not None

    # 3. New writes use v2
    c2 = kms_svc.encrypt_data(bank_id, "second_payload")
    assert c2.startswith("v2:")

    # 4. Old ciphertext still decrypts before invalidation
    assert kms_svc.decrypt_data(bank_id, c1) == payload

    # 5. Re-encrypt data from v1 to v2
    re_c = kms_svc.re_encrypt_data(bank_id, c1)
    assert re_c.startswith("v2:")
    assert kms_svc.decrypt_data(bank_id, re_c) == payload

    # 6. Invalidate retired v1 key
    revoked = kms_svc.invalidate_old_keys(bank_id)
    assert 1 in revoked

    # 7. Old un-reencrypted ciphertext fails
    with pytest.raises(InvalidToken):
        kms_svc.decrypt_data(bank_id, c1)

    # 8. Re-encrypted ciphertext succeeds
    assert kms_svc.decrypt_data(bank_id, re_c) == payload


def test_vault_client_honest_fallback_disclosure():
    """Verify VaultClient attempts live KV-v2 GET and honestly discloses fallback state when offline."""
    client = VaultClient(vault_url="http://127.0.0.1:59998", enabled=True)
    secret = client.get_secret("database/credentials")
    assert secret["username"] == "fraud_user"

    meta = client.get_secret_metadata("database/credentials")
    assert "secret/data/database/credentials" in meta.path
    assert "Local Development Fallback" in meta.source


def test_scheduled_key_rotation_cron_endpoint():
    """Verify /v1/cron/rotate-keys endpoint executes key rotation and invalidation under valid authorization."""
    client = TestClient(app)
    headers = {"X-Cron-Secret": "cfi_cron_secret_secure_token_2026"}

    # Unauthorized attempt rejected
    unauth_resp = client.post("/v1/cron/rotate-keys", headers={"X-Cron-Secret": "wrong_secret"})
    assert unauth_resp.status_code == 401

    # Authorized execution
    resp = client.post(
        "/v1/cron/rotate-keys",
        headers=headers,
        json={"tenant_ids": ["bank_a", "bank_b"], "auto_reencrypt": True, "revoke_retired": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert "bank_a" in data["tenants_rotated"]
    assert "bank_b" in data["tenants_rotated"]
    assert "timestamp_iso" in data
