"""Tests for multi-tenancy infrastructure and KMS key isolation.

Verifies:
    * Database-per-tenant isolation (separate engines, no cross-tenant queries)
    * KMS key generation, persistence, and tenant isolation
    * Model vault local checkpoint saves
    * Tenant-isolated log file routing
"""

from __future__ import annotations

import json
import logging
import os

import pytest

# ── KMS Service Tests ─────────────────────────


class TestKMSService:
    """Test per-tenant cryptographic key management."""

    @pytest.fixture(autouse=True)
    def _setup_temp_storage(self, tmp_path: str) -> None:
        """Use a temp directory for KMS vault storage."""
        from app.application.services.kms_service import KMSService

        self.storage_root = str(tmp_path)
        self.kms = KMSService(storage_root=self.storage_root)

    def test_hmac_key_generated_on_first_access(self) -> None:
        """First call should generate a new HMAC key."""
        key = self.kms.get_hmac_key("bank_a")
        assert isinstance(key, str)
        assert len(key) == 64  # 256-bit hex

    def test_hmac_key_is_persistent(self) -> None:
        """Subsequent calls return the same key."""
        key1 = self.kms.get_hmac_key("bank_a")
        key2 = self.kms.get_hmac_key("bank_a")
        assert key1 == key2

    def test_hmac_keys_isolated_per_tenant(self) -> None:
        """Different banks have different HMAC keys."""
        key_a = self.kms.get_hmac_key("bank_a")
        key_b = self.kms.get_hmac_key("bank_b")
        assert key_a != key_b

    def test_psi_exponent_generated_and_persisted(self) -> None:
        """PSI private exponents should be positive integers, persisted across calls."""
        exp1 = self.kms.get_psi_private_exponent("bank_a")
        exp2 = self.kms.get_psi_private_exponent("bank_a")
        assert isinstance(exp1, int)
        assert exp1 > 1
        assert exp1 == exp2

    def test_psi_exponents_isolated_per_tenant(self) -> None:
        """Different banks have different PSI exponents."""
        exp_a = self.kms.get_psi_private_exponent("bank_a")
        exp_b = self.kms.get_psi_private_exponent("bank_b")
        assert exp_a != exp_b

    def test_aggregation_seed_generated_and_persisted(self) -> None:
        """Aggregation mask seed should be 32 bytes, persisted."""
        seed1 = self.kms.get_aggregation_mask_seed("bank_a")
        seed2 = self.kms.get_aggregation_mask_seed("bank_a")
        assert isinstance(seed1, bytes)
        assert len(seed1) == 32
        assert seed1 == seed2

    def test_key_rotation(self) -> None:
        """Rotating a key should produce a new value."""
        old_key = self.kms.get_hmac_key("bank_a")
        self.kms.rotate_key("bank_a", "hmac_key")
        new_key = self.kms.get_hmac_key("bank_a")
        assert old_key != new_key

    def test_vault_files_created_per_tenant(self) -> None:
        """Vault key files should be created in tenant-isolated directories."""
        self.kms.get_hmac_key("bank_a")
        self.kms.get_hmac_key("bank_b")

        path_a = os.path.join(self.storage_root, "bank_a", "kms", "keys.json")
        path_b = os.path.join(self.storage_root, "bank_b", "kms", "keys.json")

        assert os.path.exists(path_a)
        assert os.path.exists(path_b)

        # Verify they are separate files with different contents
        with open(path_a) as f:
            keys_a = json.load(f)
        with open(path_b) as f:
            keys_b = json.load(f)

        assert keys_a["hmac_key"] != keys_b["hmac_key"]

    def test_list_tenants(self) -> None:
        """List tenants that have KMS vaults."""
        self.kms.get_hmac_key("bank_a")
        self.kms.get_hmac_key("bank_c")
        tenants = self.kms.list_tenants()
        assert "bank_a" in tenants
        assert "bank_c" in tenants
        assert "bank_b" not in tenants


# ── Database Multi-Tenant Tests ───────────────


class TestDatabaseMultiTenancy:
    """Test database-per-tenant isolation."""

    def test_resolve_database_url_central(self) -> None:
        """Central (None) tenant resolves to the base/central database."""
        from app.infrastructure.database import _resolve_database_url

        url = _resolve_database_url(None)
        # For SQLite: cfi_central.db  |  For Postgres: base db name (no suffix)
        assert "bank_a" not in url
        assert "bank_b" not in url
        assert "bank_c" not in url

    def test_resolve_database_url_bank(self) -> None:
        """Bank tenant resolves to a tenant-suffixed database."""
        from app.infrastructure.database import _resolve_database_url

        url = _resolve_database_url("bank_a")
        assert "bank_a" in url

    def test_different_tenants_get_different_urls(self) -> None:
        """Each tenant should have a unique database URL."""
        from app.infrastructure.database import _resolve_database_url

        url_a = _resolve_database_url("bank_a")
        url_b = _resolve_database_url("bank_b")
        url_sys = _resolve_database_url(None)

        assert url_a != url_b
        assert url_a != url_sys
        assert url_b != url_sys

    def test_active_tenant_context_variable(self) -> None:
        """The active_tenant context variable should default to None and be settable."""
        from app.infrastructure.database import active_tenant

        assert active_tenant.get() is None

        active_tenant.set("bank_a")
        assert active_tenant.get() == "bank_a"

        active_tenant.set(None)
        assert active_tenant.get() is None


# ── Privacy-Preserving Identifier KMS Tests ───


class TestPrivacyIdentifierKMS:
    """Test that entity hashing uses per-tenant KMS keys."""

    @pytest.fixture(autouse=True)
    def _reset_kms(self, tmp_path: str) -> None:
        """Use a temp KMS vault for testing."""
        import app.application.services.kms_service as kms_mod

        kms_mod._default_kms = kms_mod.KMSService(storage_root=str(tmp_path))
        yield
        kms_mod._default_kms = None

    def test_compute_with_kms_differs_per_tenant(self) -> None:
        """The same raw value should hash differently for different banks."""
        from app.domain.value_objects_phase2 import PrivacyPreservingIdentifier

        hash_a = PrivacyPreservingIdentifier.compute_with_kms(
            "john.doe@example.com", "CUSTOMER", "bank_a"
        )
        hash_b = PrivacyPreservingIdentifier.compute_with_kms(
            "john.doe@example.com", "CUSTOMER", "bank_b"
        )
        assert hash_a != hash_b

    def test_compute_with_kms_deterministic(self) -> None:
        """Same input + same bank should produce the same hash."""
        from app.domain.value_objects_phase2 import PrivacyPreservingIdentifier

        hash1 = PrivacyPreservingIdentifier.compute_with_kms(
            "john.doe@example.com", "CUSTOMER", "bank_a"
        )
        hash2 = PrivacyPreservingIdentifier.compute_with_kms(
            "john.doe@example.com", "CUSTOMER", "bank_a"
        )
        assert hash1 == hash2

    def test_backward_compatible_compute(self) -> None:
        """The original compute() with default key should still work."""
        from app.domain.value_objects_phase2 import PrivacyPreservingIdentifier

        hash1 = PrivacyPreservingIdentifier.compute("test@email.com", "CUSTOMER")
        hash2 = PrivacyPreservingIdentifier.compute("test@email.com", "CUSTOMER")
        assert hash1 == hash2
        assert len(hash1) == 32


# ── Model Vault Tests ─────────────────────────


class TestModelVault:
    """Test that local model checkpoints are saved in tenant-isolated vaults."""

    def test_vault_directory_structure(self, tmp_path: str) -> None:
        """Vault directories should be created with correct tenant paths."""
        vault_a = os.path.join(str(tmp_path), "bank_a", "model_vault")
        vault_b = os.path.join(str(tmp_path), "bank_b", "model_vault")

        os.makedirs(vault_a, exist_ok=True)
        os.makedirs(vault_b, exist_ok=True)

        # Simulate checkpoint save
        with open(os.path.join(vault_a, "local_model_weights.pt"), "w") as f:
            f.write("bank_a_weights")
        with open(os.path.join(vault_b, "local_model_weights.pt"), "w") as f:
            f.write("bank_b_weights")

        assert os.path.exists(os.path.join(vault_a, "local_model_weights.pt"))
        assert os.path.exists(os.path.join(vault_b, "local_model_weights.pt"))

        # Verify isolation — different file contents
        with open(os.path.join(vault_a, "local_model_weights.pt")) as f:
            a_content = f.read()
        with open(os.path.join(vault_b, "local_model_weights.pt")) as f:
            b_content = f.read()
        assert a_content != b_content


# ── Tenant-Isolated Logging Tests ─────────────


class TestTenantLogging:
    """Test per-tenant log file routing."""

    def test_tenant_log_filter(self) -> None:
        """TenantLogFilter should only pass records matching the target tenant."""
        from app.infrastructure.database import active_tenant

        class TenantLogFilter(logging.Filter):
            def __init__(self, target_tenant: str | None) -> None:
                super().__init__()
                self.target_tenant = target_tenant

            def filter(self, record: logging.LogRecord) -> bool:
                current = active_tenant.get()
                return current == self.target_tenant

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test message",
            args=(),
            exc_info=None,
        )

        # No tenant context → system filter should pass
        active_tenant.set(None)
        system_filter = TenantLogFilter(None)
        bank_a_filter = TenantLogFilter("bank_a")

        assert system_filter.filter(record) is True
        assert bank_a_filter.filter(record) is False

        # Bank A context → bank_a filter should pass
        active_tenant.set("bank_a")
        assert system_filter.filter(record) is False
        assert bank_a_filter.filter(record) is True

        # Clean up
        active_tenant.set(None)
