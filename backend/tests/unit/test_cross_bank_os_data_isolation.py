"""Adversarial Verification Suite for Real Cross-Bank Private Data Isolation (BANK-OS-01 to BANK-OS-10).

Verifies the genuine Operating System and Container Security Boundaries separating
participating financial institutions (BANK_A, BANK_B, BANK_C) and the Federation Coordinator:

- BANK-OS-01: Inspect actual effective security identities of all three bank processes and coordinator.
- BANK-OS-02: Coordinator cannot read any bank's private dataset (denied by OS kernel via NTFS DACL).
- BANK-OS-03: Coordinator cannot access any bank's encryption key (denied by OS kernel via NTFS DACL).
- BANK-OS-04: Bank A cannot read Bank B or C datasets (denied by OS kernel via NTFS DACL).
- BANK-OS-05: Bank B cannot read Bank A or C keys (denied by OS kernel via NTFS DACL).
- BANK-OS-06: Unauthorized replacement or deletion of bank data/keys fails (denied by OS kernel).
- BANK-OS-07: Access denial persists after bank worker termination and file-lock release.
- BANK-OS-08: Failure to enforce required OS permissions prevents production startup (fails closed).
- BANK-OS-09: Every authorized bank successfully loads and trains on its own encrypted dataset.
- BANK-OS-10: Genuine three-bank, two-round federation completes with enforced isolation, verified receipt,
              and inactive Model Registry candidate.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
import uuid
from collections.abc import Generator
from unittest import mock

import numpy as np
import psycopg
import pytest
from cryptography.fernet import Fernet

from app.application.services.distributed_fl_service import (
    BANK_SECURITY_SIDS,
    COORDINATOR_RESTRICTED_SID,
    BankPrivateStorageVault,
    BankWorkerOSSecurityContext,
    CoordinatorOSSecurityContext,
    IsolatedReceiptSigningAuthority,
    get_bank_security_sid,
    get_bank_worker_os_identity,
    get_coordinator_os_identity,
    provision_isolated_completion_authority_vault,
    provision_isolated_issuer_vault,
    setup_bank_private_storage,
    spawn_bank_workers,
    spawn_isolated_completion_authority_service,
    spawn_isolated_coordinator_process,
    validate_restricted_bank_worker_policy,
)
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.config import get_settings
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
)
from app.infrastructure.database.training_authority_backends import provision_runtime_service_roles

COORD_SECRET = "coord_secret_bank_os_suite_prod_991"
DEV_SEED_HEX = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest().hex()

BANK_TOKENS = {
    "BANK_A": "token_bank_a_os_iso_test_101",
    "BANK_B": "token_bank_b_os_iso_test_102",
    "BANK_C": "token_bank_c_os_iso_test_103",
}


@pytest.fixture(scope="module")
def pg_roles() -> Generator[dict[str, str], None, None]:
    """Provision real PostgreSQL 18.1 embedded instance with partitioned runtime service roles."""
    try:
        import pg0
    except ImportError:
        pytest.skip("pg0 not installed")

    pg = pg0.Pg0()
    pg.start()
    try:
        admin_url = pg.uri
        assert admin_url is not None
        roles = provision_runtime_service_roles(admin_url)
        roles["admin"] = admin_url
        yield roles
    finally:
        pg.stop()


class TestCrossBankOSDataIsolation:
    """Rigorous verification of operating system and container data ownership for all banks."""

    def test_bank_os_01_inspect_actual_effective_security_identities(self) -> None:
        """BANK-OS-01: Inspect actual effective security identities of all three bank processes and coordinator."""
        if os.name != "nt":
            pytest.skip("Windows NT required for Windows restricted security tokens")

        # 1. Bank A OS Principal
        with BankWorkerOSSecurityContext("BANK_A"):
            ident_a = get_bank_worker_os_identity("BANK_A")
            assert ident_a.details["is_restricted"] is True
            assert BANK_SECURITY_SIDS["BANK_A"] in ident_a.details["restricted_sids"]
            assert BANK_SECURITY_SIDS["BANK_B"] not in ident_a.details["restricted_sids"]
            assert BANK_SECURITY_SIDS["BANK_C"] not in ident_a.details["restricted_sids"]
            assert COORDINATOR_RESTRICTED_SID not in ident_a.details["restricted_sids"]
            assert ident_a.details["is_admin_disabled"] is True
            assert ident_a.principal_type == "WindowsBankWorkerPrincipal"
            validate_restricted_bank_worker_policy(ident_a.details, "BANK_A")

        # 2. Bank B OS Principal
        with BankWorkerOSSecurityContext("BANK_B"):
            ident_b = get_bank_worker_os_identity("BANK_B")
            assert ident_b.details["is_restricted"] is True
            assert BANK_SECURITY_SIDS["BANK_B"] in ident_b.details["restricted_sids"]
            assert BANK_SECURITY_SIDS["BANK_A"] not in ident_b.details["restricted_sids"]
            assert BANK_SECURITY_SIDS["BANK_C"] not in ident_b.details["restricted_sids"]
            assert COORDINATOR_RESTRICTED_SID not in ident_b.details["restricted_sids"]
            assert ident_b.details["is_admin_disabled"] is True
            assert ident_b.principal_type == "WindowsBankWorkerPrincipal"
            validate_restricted_bank_worker_policy(ident_b.details, "BANK_B")

        # 3. Bank C OS Principal
        with BankWorkerOSSecurityContext("BANK_C"):
            ident_c = get_bank_worker_os_identity("BANK_C")
            assert ident_c.details["is_restricted"] is True
            assert BANK_SECURITY_SIDS["BANK_C"] in ident_c.details["restricted_sids"]
            assert BANK_SECURITY_SIDS["BANK_A"] not in ident_c.details["restricted_sids"]
            assert BANK_SECURITY_SIDS["BANK_B"] not in ident_c.details["restricted_sids"]
            assert COORDINATOR_RESTRICTED_SID not in ident_c.details["restricted_sids"]
            assert ident_c.details["is_admin_disabled"] is True
            assert ident_c.principal_type == "WindowsBankWorkerPrincipal"
            validate_restricted_bank_worker_policy(ident_c.details, "BANK_C")

        # 4. Coordinator OS Principal
        with CoordinatorOSSecurityContext():
            ident_coord = get_coordinator_os_identity()
            assert ident_coord.details["is_restricted"] is True
            assert COORDINATOR_RESTRICTED_SID in ident_coord.details["restricted_sids"]
            assert BANK_SECURITY_SIDS["BANK_A"] not in ident_coord.details["restricted_sids"]
            assert BANK_SECURITY_SIDS["BANK_B"] not in ident_coord.details["restricted_sids"]
            assert BANK_SECURITY_SIDS["BANK_C"] not in ident_coord.details["restricted_sids"]
            assert ident_coord.details["is_admin_disabled"] is True

        # All four security identities are strictly disjoint
        all_restricted = {
            "BANK_A": ident_a.details["restricted_sids"],
            "BANK_B": ident_b.details["restricted_sids"],
            "BANK_C": ident_c.details["restricted_sids"],
            "COORDINATOR": ident_coord.details["restricted_sids"],
        }
        assert BANK_SECURITY_SIDS["BANK_A"] in all_restricted["BANK_A"]
        assert BANK_SECURITY_SIDS["BANK_A"] not in all_restricted["BANK_B"]
        assert BANK_SECURITY_SIDS["BANK_A"] not in all_restricted["BANK_C"]
        assert BANK_SECURITY_SIDS["BANK_A"] not in all_restricted["COORDINATOR"]

    def test_bank_os_02_coordinator_cannot_read_any_bank_private_dataset(self, tmp_path) -> None:
        """BANK-OS-02: Coordinator cannot read any bank's private dataset."""
        if os.name != "nt":
            pytest.skip("Windows NT required for NTFS DACL access verification")

        vault_root = str(tmp_path / "bank_vaults_os02")
        bank_paths = {}
        for b_id in ("BANK_A", "BANK_B", "BANK_C"):
            X = np.random.randn(50, NUM_FEATURES).astype(np.float32)
            y = np.random.randint(0, 2, 50).astype(np.float32)
            enc_key = Fernet.generate_key()
            p = setup_bank_private_storage(
                bank_storage_dir=vault_root,
                bank_id=b_id,
                X_partition=X,
                y_partition=y,
                bank_token=BANK_TOKENS[b_id],
                bank_encryption_key=enc_key,
                lock_boundary=False,
            )
            bank_paths[b_id] = p

        # Coordinator attempts to read each bank's private dataset directly from disk
        with CoordinatorOSSecurityContext():
            for b_id, p in bank_paths.items():
                with pytest.raises(PermissionError) as exc_info:
                    with open(p, "rb") as f:
                        f.read()
                assert "Permission denied" in str(exc_info.value) or isinstance(exc_info.value, PermissionError)

    def test_bank_os_03_coordinator_cannot_access_any_bank_encryption_key(self, tmp_path) -> None:
        """BANK-OS-03: Coordinator cannot access any bank's encryption key."""
        if os.name != "nt":
            pytest.skip("Windows NT required for NTFS DACL access verification")

        vault_root = str(tmp_path / "bank_vaults_os03")
        key_paths = {}
        for b_id in ("BANK_A", "BANK_B", "BANK_C"):
            X = np.random.randn(30, NUM_FEATURES).astype(np.float32)
            y = np.random.randint(0, 2, 30).astype(np.float32)
            enc_key = Fernet.generate_key()
            setup_bank_private_storage(
                bank_storage_dir=vault_root,
                bank_id=b_id,
                X_partition=X,
                y_partition=y,
                bank_token=BANK_TOKENS[b_id],
                bank_encryption_key=enc_key,
            )
            key_paths[b_id] = os.path.join(vault_root, b_id, "encryption.key")

        # Verify keys exist on disk under test provisioning identity
        for b_id, kp in key_paths.items():
            assert os.path.exists(kp)

        # Coordinator attempts to read each bank's encryption key directly from disk
        with CoordinatorOSSecurityContext():
            for b_id, kp in key_paths.items():
                with pytest.raises(PermissionError) as exc_info:
                    with open(kp, "rb") as f:
                        f.read()
                assert "Permission denied" in str(exc_info.value) or isinstance(exc_info.value, PermissionError)

    def test_bank_os_04_bank_a_cannot_read_bank_b_or_c_datasets(self, tmp_path) -> None:
        """BANK-OS-04: Bank A cannot read Bank B or C datasets."""
        if os.name != "nt":
            pytest.skip("Windows NT required for NTFS DACL access verification")

        vault_root = str(tmp_path / "bank_vaults_os04")
        data_paths = {}
        for b_id in ("BANK_A", "BANK_B", "BANK_C"):
            X = np.random.randn(40, NUM_FEATURES).astype(np.float32)
            y = np.random.randint(0, 2, 40).astype(np.float32)
            p = setup_bank_private_storage(
                bank_storage_dir=vault_root,
                bank_id=b_id,
                X_partition=X,
                y_partition=y,
                bank_token=BANK_TOKENS[b_id],
                bank_encryption_key=Fernet.generate_key(),
                lock_boundary=False,
            )
            data_paths[b_id] = p

        with BankWorkerOSSecurityContext("BANK_A"):
            # Bank A can legitimately open its own dataset
            with open(data_paths["BANK_A"], "rb") as f:
                data_a = f.read()
            assert len(data_a) > 0

            # Bank A attempting to open Bank B dataset -> denied by OS
            with pytest.raises(PermissionError) as exc_b:
                with open(data_paths["BANK_B"], "rb") as f:
                    f.read()
            assert "Permission denied" in str(exc_b.value) or isinstance(exc_b.value, PermissionError)

            # Bank A attempting to open Bank C dataset -> denied by OS
            with pytest.raises(PermissionError) as exc_c:
                with open(data_paths["BANK_C"], "rb") as f:
                    f.read()
            assert "Permission denied" in str(exc_c.value) or isinstance(exc_c.value, PermissionError)

    def test_bank_os_05_bank_b_cannot_read_bank_a_or_c_keys(self, tmp_path) -> None:
        """BANK-OS-05: Bank B cannot read Bank A or C keys."""
        if os.name != "nt":
            pytest.skip("Windows NT required for NTFS DACL access verification")

        vault_root = str(tmp_path / "bank_vaults_os05")
        key_paths = {}
        for b_id in ("BANK_A", "BANK_B", "BANK_C"):
            X = np.random.randn(40, NUM_FEATURES).astype(np.float32)
            y = np.random.randint(0, 2, 40).astype(np.float32)
            setup_bank_private_storage(
                bank_storage_dir=vault_root,
                bank_id=b_id,
                X_partition=X,
                y_partition=y,
                bank_token=BANK_TOKENS[b_id],
                bank_encryption_key=Fernet.generate_key(),
            )
            key_paths[b_id] = os.path.join(vault_root, b_id, "encryption.key")

        with BankWorkerOSSecurityContext("BANK_B"):
            # Bank B can legitimately open its own key
            with open(key_paths["BANK_B"], "rb") as f:
                key_b = f.read()
            assert len(key_b) > 0

            # Bank B attempting to open Bank A key -> denied by OS
            with pytest.raises(PermissionError) as exc_a:
                with open(key_paths["BANK_A"], "rb") as f:
                    f.read()
            assert "Permission denied" in str(exc_a.value) or isinstance(exc_a.value, PermissionError)

            # Bank B attempting to open Bank C key -> denied by OS
            with pytest.raises(PermissionError) as exc_c:
                with open(key_paths["BANK_C"], "rb") as f:
                    f.read()
            assert "Permission denied" in str(exc_c.value) or isinstance(exc_c.value, PermissionError)

    def test_bank_os_06_unauthorized_replacement_or_deletion_fails(self, tmp_path) -> None:
        """BANK-OS-06: Unauthorized replacement/deletion of bank data fails."""
        if os.name != "nt":
            pytest.skip("Windows NT required for NTFS DACL access verification")

        vault_root = str(tmp_path / "bank_vaults_os06")
        for b_id in ("BANK_A", "BANK_B"):
            X = np.random.randn(30, NUM_FEATURES).astype(np.float32)
            y = np.random.randint(0, 2, 30).astype(np.float32)
            setup_bank_private_storage(
                bank_storage_dir=vault_root,
                bank_id=b_id,
                X_partition=X,
                y_partition=y,
                bank_token=BANK_TOKENS[b_id],
                bank_encryption_key=Fernet.generate_key(),
            )

        data_a = os.path.join(vault_root, "BANK_A", "private_dataset.npz")
        key_a = os.path.join(vault_root, "BANK_A", "encryption.key")
        data_b = os.path.join(vault_root, "BANK_B", "private_dataset.npz")

        # 1. Bank A attempting to overwrite or delete Bank B dataset
        with BankWorkerOSSecurityContext("BANK_A"):
            with pytest.raises(PermissionError):
                with open(data_b, "wb") as f:
                    f.write(b"malicious_data_replacement")

            with pytest.raises(PermissionError):
                os.remove(data_b)

        # 2. Coordinator attempting to overwrite or delete Bank A dataset & key
        with CoordinatorOSSecurityContext():
            with pytest.raises(PermissionError):
                with open(data_a, "wb") as f:
                    f.write(b"coordinator_data_overwrite")

            with pytest.raises(PermissionError):
                os.remove(data_a)

            with pytest.raises(PermissionError):
                with open(key_a, "wb") as f:
                    f.write(b"coordinator_key_overwrite")

            with pytest.raises(PermissionError):
                os.remove(key_a)

    def test_bank_os_07_access_denial_persists_after_worker_termination(self, pg_roles: dict[str, str], tmp_path) -> None:
        """BANK-OS-07: Access denial persists after bank worker termination and file-lock release."""
        if os.name != "nt":
            pytest.skip("Windows NT required for NTFS DACL access verification")

        vault_root = str(tmp_path / "bank_vaults_os07")
        X_a = np.random.randn(50, NUM_FEATURES).astype(np.float32)
        y_a = np.random.randint(0, 2, 50).astype(np.float32)
        enc_key_a = Fernet.generate_key()
        data_path_a = setup_bank_private_storage(
            bank_storage_dir=vault_root,
            bank_id="BANK_A",
            X_partition=X_a,
            y_partition=y_a,
            bank_token=BANK_TOKENS["BANK_A"],
            bank_encryption_key=enc_key_a,
            lock_boundary=False,
        )

        # Spawn a genuine worker process for Bank A
        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A"],
            bank_tokens={"BANK_A": BANK_TOKENS["BANK_A"]},
            db_url=pg_roles["admin"],
            bank_data_paths={"BANK_A": data_path_a},
            bank_db_urls={"BANK_A": pg_roles["worker_bank_a"]},
            bank_encryption_keys={"BANK_A": enc_key_a},
            app_env="production",
        )
        proc_a = procs["BANK_A"]
        assert proc_a.is_alive()

        # Stop worker and wait for clean termination and complete lock release
        endpoints["BANK_A"]["inbox"].put({"command": "STOP"})
        proc_a.join(timeout=3)
        if proc_a.is_alive():
            proc_a.terminate()
            proc_a.join(timeout=3)
        assert proc_a.is_alive() is False

        # After worker has completely exited and any ephemeral OS handle closed,
        # persistent NTFS DACL must still deny unauthorized access!
        key_path_a = os.path.join(vault_root, "BANK_A", "encryption.key")

        # Coordinator denied:
        with CoordinatorOSSecurityContext():
            with pytest.raises(PermissionError):
                with open(data_path_a, "rb") as f:
                    f.read()
            with pytest.raises(PermissionError):
                with open(key_path_a, "rb") as f:
                    f.read()

        # Other bank worker denied:
        with BankWorkerOSSecurityContext("BANK_B"):
            with pytest.raises(PermissionError):
                with open(data_path_a, "rb") as f:
                    f.read()
            with pytest.raises(PermissionError):
                with open(key_path_a, "rb") as f:
                    f.read()

    def test_bank_os_08_failure_to_enforce_required_permissions_prevents_startup(self, tmp_path) -> None:
        """BANK-OS-08: Failure to enforce required permissions prevents production startup."""
        vault_root = str(tmp_path / "bank_vaults_os08")
        X = np.random.randn(20, NUM_FEATURES).astype(np.float32)
        y = np.random.randint(0, 2, 20).astype(np.float32)

        # 1. DACL enforcement failure in setup_bank_private_storage raises fail-closed exception
        if os.name == "nt":
            import win32security

            def failing_set_security(*args: Any, **kwargs: Any) -> None:
                raise OSError("Simulated Windows Security API failure")

            with mock.patch("win32security.SetNamedSecurityInfo", side_effect=failing_set_security):
                with pytest.raises(TrainingProvenanceConfigurationError, match="Failed to configure mandatory operating system NTFS DACL"):
                    setup_bank_private_storage(
                        bank_storage_dir=vault_root,
                        bank_id="BANK_A",
                        X_partition=X,
                        y_partition=y,
                        bank_token=BANK_TOKENS["BANK_A"],
                        bank_encryption_key=Fernet.generate_key(),
                        app_env="production",
                    )

        # 2. Bank worker executing without restricted token rejected by policy validator
        unrestricted_identity_details = {
            "is_restricted": False,
            "restricted_sids": [],
            "is_admin_disabled": True,
        }
        with pytest.raises(TrainingProvenanceSecurityError, match="must execute under an enforced restricted operating system security token"):
            validate_restricted_bank_worker_policy(unrestricted_identity_details, "BANK_A")

        # 3. Bank worker possessing other bank's SID rejected by policy validator
        cross_sid_details = {
            "is_restricted": True,
            "restricted_sids": [BANK_SECURITY_SIDS["BANK_A"], BANK_SECURITY_SIDS["BANK_B"]],
            "is_admin_disabled": True,
        }
        with pytest.raises(TrainingProvenanceSecurityError, match="illegally possesses another bank's SID"):
            validate_restricted_bank_worker_policy(cross_sid_details, "BANK_A")

        # 4. Bank worker possessing coordinator code SID rejected by policy validator
        coord_sid_details = {
            "is_restricted": True,
            "restricted_sids": [BANK_SECURITY_SIDS["BANK_A"], COORDINATOR_RESTRICTED_SID],
            "is_admin_disabled": True,
        }
        with pytest.raises(TrainingProvenanceSecurityError, match="cannot possess coordinator restricted code SID"):
            validate_restricted_bank_worker_policy(coord_sid_details, "BANK_A")

        # 5. Bank worker without admin disabled rejected
        active_admin_details = {
            "is_restricted": True,
            "restricted_sids": [BANK_SECURITY_SIDS["BANK_A"]],
            "is_admin_disabled": False,
        }
        with pytest.raises(TrainingProvenanceSecurityError, match="has active administrative authority"):
            validate_restricted_bank_worker_policy(active_admin_details, "BANK_A")

    def test_bank_os_09_every_authorized_bank_loads_and_trains_on_encrypted_dataset(self, tmp_path) -> None:
        """BANK-OS-09: Every authorized bank successfully loads and trains on its own encrypted dataset."""
        vault_root = str(tmp_path / "bank_vaults_os09")
        settings = get_settings()
        model_service = ModelService(settings)

        for b_id in ("BANK_A", "BANK_B", "BANK_C"):
            X = np.random.randn(60, NUM_FEATURES).astype(np.float32)
            y = np.random.randint(0, 2, 60).astype(np.float32)
            enc_key = Fernet.generate_key()

            data_path = setup_bank_private_storage(
                bank_storage_dir=vault_root,
                bank_id=b_id,
                X_partition=X,
                y_partition=y,
                bank_token=BANK_TOKENS[b_id],
                bank_encryption_key=enc_key,
                app_env="production",
            )

            # Authorized bank worker executes under its dedicated OS security context
            with BankWorkerOSSecurityContext(b_id):
                vault = BankPrivateStorageVault(
                    bank_id=b_id,
                    vault_dir=vault_root,
                    bank_token=BANK_TOKENS[b_id],
                    bank_encryption_key=enc_key,
                    app_env="production",
                )
                X_loaded, y_loaded = vault.read_dataset_as_bank(b_id, caller_token=BANK_TOKENS[b_id])
                assert len(X_loaded) == 60
                np.testing.assert_allclose(X_loaded, X, rtol=1e-5)

                model = model_service.create_model(input_dim=NUM_FEATURES)
                model, loss_history, _ = model_service.train_local(
                    model=model,
                    X_train=X_loaded,
                    y_train=y_loaded,
                    epochs=2,
                    learning_rate=0.01,
                )
                assert len(loss_history) == 2
                assert all(np.isfinite(l) for l in loss_history)

    def test_bank_os_10_genuine_three_bank_two_round_federation_with_enforced_isolation(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """BANK-OS-10: Genuine three-bank, two-round federation completes with enforced isolation, verified receipt, and inactive Registry candidate."""
        coord_url = pg_roles["coordinator"]
        comp_url = pg_roles["completion"]
        issuer_url = pg_roles["issuer"]

        comp_vault_dir = str(tmp_path / "comp_vault_os10")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )

        vault_root = str(tmp_path / "bank_vaults_os10")
        bank_data_paths = {}
        bank_enc_keys = {b: Fernet.generate_key() for b in ("BANK_A", "BANK_B", "BANK_C")}
        np.random.seed(42)
        for b_id, n_samples in (("BANK_A", 100), ("BANK_B", 150), ("BANK_C", 200)):
            X_part = np.random.randn(n_samples, NUM_FEATURES).astype(np.float32)
            y_part = np.random.randint(0, 2, size=n_samples).astype(np.float32)
            p = setup_bank_private_storage(
                bank_storage_dir=vault_root,
                bank_id=b_id,
                X_partition=X_part,
                y_partition=y_part,
                bank_token=BANK_TOKENS[b_id],
                bank_encryption_key=bank_enc_keys[b_id],
                app_env="production",
            )
            bank_data_paths[b_id] = p

        bank_db_urls = {
            "BANK_A": pg_roles["worker_bank_a"],
            "BANK_B": pg_roles["worker_bank_b"],
            "BANK_C": pg_roles["worker_bank_c"],
        }
        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_tokens=BANK_TOKENS,
            db_url=pg_roles["worker"],
            bank_data_paths=bank_data_paths,
            bank_db_urls=bank_db_urls,
            bank_encryption_keys=bank_enc_keys,
            app_env="production",
        )

        # Provision issuer vault with independent key material
        issuer_vault_dir = str(tmp_path / "issuer_vault_os10")
        dev_seed = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest().hex()
        issuer_key_file, pub_hex = provision_isolated_issuer_vault(issuer_vault_dir, signing_key_hex=dev_seed, app_env="development")
        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=issuer_key_file,
            coordinator_token=COORD_SECRET,
            db_url=issuer_url,
            key_id=DEFAULT_TRAINING_KEY_ID,
            app_env="production",
        )

        coord_proc, coord_client = spawn_isolated_coordinator_process(app_env="production")

        try:
            uid = uuid.uuid4().hex[:8]
            job_id = f"job_os10_{uid}"
            run_id = f"run_os10_{uid}"
            sim_id = f"sim_os10_{uid}"
            registry_dir = str(tmp_path / "model_reg_os10")

            X_test = np.random.randn(100, NUM_FEATURES).astype(np.float32)
            y_test = np.random.randint(0, 2, size=100).astype(np.float32)

            worker_pids: dict[str, int] = {b: (procs[b].pid or 0) for b in procs}

            result = coord_client.execute_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(X_test, y_test),
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                worker_endpoints=endpoints,
                num_rounds=2,
                expected_unique_samples=450,
                registry_storage_dir=registry_dir,
                coordinator_token=COORD_SECRET,
                db_url=coord_url,
                comp_service_endpoint=comp_endpoint,
                signing_authority=signing_auth,
                worker_pids=worker_pids,
            )

            assert result["completed_record"].job_id == job_id
            assert result["completed_record"].status == "COMPLETED"
            assert result["completed_record"].executed_rounds == 2
            assert result["receipt"].job_id == job_id
            assert result["is_verified"] is True

            saved_ver = result["saved_version"]
            assert saved_ver["is_production_eligible"] is True
            assert saved_ver["status"] == "inactive"
            assert saved_ver["provenance_type"] == "TRAINING_RECORDED"
            assert result["is_promoted_champion"] is False

            # Verify PostgreSQL state
            with psycopg.connect(pg_roles["admin"]) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                comp_row = cur.fetchone()
                assert comp_row is not None and comp_row[0] == 1

                cur.execute("SELECT COUNT(*) FROM cfi_round_observations WHERE job_id = %s", (job_id,))
                obs_row = cur.fetchone()
                assert obs_row is not None and obs_row[0] == 6  # 2 rounds * 3 banks
        finally:
            coord_client.terminate()
            comp_proc.terminate()
            comp_proc.join(timeout=3)
            signing_auth.terminate()
            for p in procs.values():
                p.terminate()
                p.join(timeout=3)
