"""Focused adversarial tests for Model Registry directory containment and artifact path security.

Verifies:
- Rejection of path traversal (../, absolute, Windows-style, null bytes).
- Canonical directory containment under registry_root.
- Unsafe symlink rejection.
- Read-only lookup functions (manifest, ownership, version listing) do NOT create directories.
- Storage permissions: 0o700 on POSIX, no 0o777, and production fail-closed on unwritable storage.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.application.services.model_registry import (
    ModelRegistry,
    validate_simulation_id,
)
from app.domain.model_serving_errors import ModelIntegrityError
from app.infrastructure.storage.storage_utils import get_storage_dir


class TestSimulationIdentifierValidation:
    """Verifies that simulation identifiers are strictly validated at the service boundary."""

    @pytest.mark.parametrize(
        "invalid_id",
        [
            "",
            "   ",
            "../escape",
            "../../etc/passwd",
            "..\\windows\\system32",
            "/absolute/unix/path",
            "C:\\absolute\\windows\\path",
            "sim/subpath",
            "sim\\subpath",
            "sim\0nullbyte",
            "sim;drop_table",
            "sim id with spaces",
            "sim$dollar",
            "sim#hash",
            "sim?query",
            ".",
            "..",
            "a" * 129,  # exceeds maximum length of 128
        ],
    )
    def test_invalid_simulation_identifiers_rejected(self, invalid_id: str) -> None:
        with pytest.raises(ValueError, match="Invalid simulation identifier|Unsafe simulation identifier"):
            validate_simulation_id(invalid_id)

    @pytest.mark.parametrize(
        "valid_id",
        [
            "sim_001",
            "federated-training-run-42",
            "fedgnn_champion_v4",
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "BANK_A_MODEL_2026",
            "x",
            "a" * 128,
        ],
    )
    def test_valid_simulation_identifiers_accepted(self, valid_id: str) -> None:
        assert validate_simulation_id(valid_id) == valid_id


class TestRegistryPathContainment:
    """Verifies filesystem path containment under authoritative registry root."""

    def test_get_sim_dir_containment_under_registry_root(self, tmp_path: Path) -> None:
        storage_dir = str(tmp_path / "storage")
        reg = ModelRegistry(storage_dir=storage_dir)

        sim_id = "test_sim_alpha"
        sim_dir = reg._get_sim_dir(sim_id, create=True)

        assert os.path.isdir(sim_dir)
        canonical_sim_dir = os.path.realpath(sim_dir)
        canonical_root = os.path.realpath(reg.registry_root)
        assert os.path.commonpath([canonical_sim_dir, canonical_root]) == canonical_root

    def test_traversal_attempt_in_get_sim_dir_raises_value_error(self, tmp_path: Path) -> None:
        storage_dir = str(tmp_path / "storage")
        reg = ModelRegistry(storage_dir=storage_dir)

        with pytest.raises(ValueError):
            reg._get_sim_dir("../escaped_dir")

    def test_symlink_pointing_outside_registry_root_rejected(self, tmp_path: Path) -> None:
        storage_dir = str(tmp_path / "storage")
        external_dir = str(tmp_path / "external_target")
        os.makedirs(external_dir, exist_ok=True)

        reg = ModelRegistry(storage_dir=storage_dir)
        sim_id = "symlink_attack_sim"
        sim_link_path = os.path.join(reg.registry_root, sim_id)

        try:
            os.symlink(external_dir, sim_link_path)
        except (OSError, NotImplementedError):
            pytest.skip("Symlink creation not supported in current environment.")

        with pytest.raises(ModelIntegrityError, match="targets external path"):
            reg._get_sim_dir(sim_id, create=False)

    def test_read_only_lookups_do_not_create_directories_as_side_effect(
        self, tmp_path: Path
    ) -> None:
        storage_dir = str(tmp_path / "storage")
        reg = ModelRegistry(storage_dir=storage_dir)

        non_existent_sim = "non_existent_sim_query_999"
        expected_sim_path = os.path.join(reg.registry_root, non_existent_sim)

        assert not os.path.exists(expected_sim_path)

        # 1. Manifest lookup should return empty list without creating directory
        manifest = reg._load_manifest(non_existent_sim)
        assert manifest == []
        assert not os.path.exists(expected_sim_path), "Directory was created as side effect of _load_manifest"

        # 2. Ownership lookup should return unbound record without creating directory
        ownership = reg.get_simulation_ownership(non_existent_sim)
        assert ownership.get("is_unbound") is True
        assert not os.path.exists(expected_sim_path), "Directory was created as side effect of get_simulation_ownership"

        # 3. List versions should return empty list without creating directory
        versions = reg.list_versions(non_existent_sim)
        assert versions == []
        assert not os.path.exists(expected_sim_path), "Directory was created as side effect of list_versions"

        # 4. Active version should return None without creating directory
        active = reg.get_active_version(non_existent_sim)
        assert active is None
        assert not os.path.exists(expected_sim_path), "Directory was created as side effect of get_active_version"


class TestSecureStoragePermissions:
    """Verifies storage permissions and production fail-closed behavior."""

    def test_registry_directory_permissions_least_privilege(self, tmp_path: Path) -> None:
        storage_dir = str(tmp_path / "storage")
        reg = ModelRegistry(storage_dir=storage_dir)
        sim_dir = reg._get_sim_dir("sim_perms_test", create=True)

        if os.name != "nt":
            mode = stat.S_IMODE(os.stat(sim_dir).st_mode)
            # Must NOT be world-writable (0o777)
            assert (mode & 0o002) == 0, f"Directory is world-writable: {oct(mode)}"
            assert mode == 0o700, f"Expected 0o700 least-privilege mode, got {oct(mode)}"

    def test_production_fails_closed_when_storage_unwritable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        unwritable_path = str(tmp_path / "read_only_storage" / "does_not_exist" / "deep")

        # Simulate unwritable configured storage
        monkeypatch.setenv("CFI_STORAGE_DIR", unwritable_path)

        # In production, get_storage_dir must raise PermissionError, NOT silently switch to /tmp
        from app.infrastructure.storage import storage_utils

        # Invalidate cache probe
        storage_utils._cached_storage_dir = None

        def fail_makedirs(*args: object, **kwargs: object) -> None:
            raise PermissionError("Simulated unwritable disk")

        monkeypatch.setattr(os, "makedirs", fail_makedirs)

        with pytest.raises(PermissionError, match="prohibited in production|unavailable or not writable"):
            get_storage_dir()
