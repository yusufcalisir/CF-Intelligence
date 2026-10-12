"""Adversarial Verification Suite for Enforced Process-Boundary Bank Isolation (BANK-FIX-01 to BANK-FIX-10).

Verifies the genuine Operating System Primary Process Token boundaries separating
participating financial institutions (BANK_A, BANK_B, BANK_C) and the Federation Coordinator:

- BANK-FIX-01: Inspect the actual primary process tokens of all three running bank workers.
               Verify their effective restrictions and bank-specific authorization boundaries.
- BANK-FIX-02: From Bank A's real worker process, attempt to read Bank B/C datasets and encryption keys.
               Verify actual OS access denial.
- BANK-FIX-03: Repeat cross-bank attacks from Bank B and Bank C.
- BANK-FIX-04: Inside a compromised bank worker process, attempt RevertToSelf() and other available
               privilege-recovery paths. Verify that the process cannot regain access to another bank's vault.
- BANK-FIX-05: Verify the actual restricted coordinator cannot access any bank's private dataset or encryption key.
- BANK-FIX-06: Verify access denial persists after the target bank worker terminates and file locks are released.
- BANK-FIX-07: Deliberately fail restricted-token creation, process launching, or required DACL configuration.
               Production execution must fail closed.
- BANK-FIX-08: Verify that no unrestricted multiprocessing.Process or development fallback can produce
               production-trusted bank observations.
- BANK-FIX-09: Confirm that every authorized bank worker can still decrypt its own dataset and perform
               genuine PyTorch training.
- BANK-FIX-10: Execute genuine two-round, three-bank federated training with the corrected bank processes
               and restricted coordinator, including PostgreSQL evidence, completed-job reconciliation,
               verified Ed25519 receipt, and inactive Registry candidate registration.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import uuid
from collections.abc import Generator
from typing import Any
from unittest import mock

import numpy as np
import pytest
from cryptography.fernet import Fernet

from app.application.services.distributed_fl_service import (
    COORDINATOR_RESTRICTED_SID,
    CoordinatorOSSecurityContext,
    IsolatedReceiptSigningAuthority,
    get_bank_security_sid,
    get_os_process_identity,
    provision_isolated_completion_authority_vault,
    provision_isolated_issuer_vault,
    setup_bank_private_storage,
    spawn_bank_workers,
    spawn_isolated_completion_authority_service,
    spawn_isolated_coordinator_process,
    verify_restricted_bank_worker_os_boundary,
)
from app.application.services.model_service import NUM_FEATURES
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    PostgresTrainingJobAuthority,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
)
from app.infrastructure.database.training_authority_backends import provision_runtime_service_roles

COORD_SECRET = "coord_secret_bank_fix_suite_prod_771"
DEV_SEED_HEX = hashlib.sha256(b"cfi_bank_fix_ed25519_issuer_seed_v1").digest().hex()

BANK_TOKENS = {
    "BANK_A": "token_bank_a_fix_test_101",
    "BANK_B": "token_bank_b_fix_test_102",
    "BANK_C": "token_bank_c_fix_test_103",
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


def _provision_test_vaults(
    tmp_path: Any,
    banks: list[str] | None = None,
) -> tuple[str, dict[str, str], dict[str, bytes]]:
    """Helper to provision isolated NTFS vaults with genuine datasets and Fernet keys."""
    target_banks = banks if banks is not None else ["BANK_A", "BANK_B", "BANK_C"]
    vault_root = str(tmp_path / "bank_vaults")
    data_paths = {}
    encryption_keys = {}
    for b in target_banks:
        rng = np.random.RandomState(hash(b) % (2**31))
        X = rng.randn(64, NUM_FEATURES).astype(np.float32)
        y = rng.randint(0, 2, size=64).astype(np.int64)
        enc_key = Fernet.generate_key()
        encryption_keys[b] = enc_key
        dpath = setup_bank_private_storage(
            bank_storage_dir=vault_root,
            bank_id=b,
            X_partition=X,
            y_partition=y,
            bank_token=BANK_TOKENS[b],
            bank_encryption_key=enc_key,
            lock_boundary=False,
            app_env="production",
        )
        data_paths[b] = dpath
    return vault_root, data_paths, encryption_keys


class TestCrossBankProcessIsolation:
    """Adversarial verification of OS Primary Process Token isolation across Bank A, Bank B, and Bank C."""

    def test_bank_fix_01_inspect_actual_primary_process_tokens(self, pg_roles: dict[str, str], tmp_path) -> None:
        """BANK-FIX-01: Inspect the actual primary process tokens of all three running bank workers.

        Verify their effective restrictions and bank-specific authorization boundaries.
        A test claiming process isolation must fail if the real worker primary token is unrestricted.
        """
        if os.name != "nt":
            pytest.skip("Windows NT required for Windows restricted primary process tokens")

        vault_root, data_paths, encryption_keys = _provision_test_vaults(tmp_path)
        bank_db_urls = {
            "BANK_A": pg_roles["worker_bank_a"],
            "BANK_B": pg_roles["worker_bank_b"],
            "BANK_C": pg_roles["worker_bank_c"],
        }

        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_tokens=BANK_TOKENS,
            db_url=pg_roles["admin"],
            bank_data_paths=data_paths,
            bank_db_urls=bank_db_urls,
            bank_encryption_keys=encryption_keys,
            app_env="production",
        )

        try:
            for b_id in ("BANK_A", "BANK_B", "BANK_C"):
                proc = procs[b_id]
                assert proc.is_alive() is True, f"Worker [{b_id}] must be active"
                assert isinstance(proc.pid, int) and proc.pid > 0

                # 1. Inspect actual OS primary process token via kernel query on PID
                primary_ident = get_os_process_identity(proc.pid, check_thread=False)
                details = primary_ident.details

                assert details["is_restricted"] is True, f"Primary token of [{b_id}] must be RESTRICTED"
                expected_bank_sid = get_bank_security_sid(b_id)
                assert expected_bank_sid in details["restricted_sids"], (
                    f"Primary token of [{b_id}] must contain bank SID '{expected_bank_sid}'"
                )

                # Must not contain coordinator restricted code SID
                assert COORDINATOR_RESTRICTED_SID not in details["restricted_sids"]

                # Must not contain other banks' SIDs
                for other_bank in ("BANK_A", "BANK_B", "BANK_C"):
                    if other_bank != b_id:
                        other_sid = get_bank_security_sid(other_bank)
                        assert other_sid not in details["restricted_sids"], (
                            f"Worker [{b_id}] illegally possesses other bank SID '{other_sid}'"
                        )

                # Administrator authority must be disabled
                assert details["is_admin_disabled"] is True

                # 2. Inspect worker's self-reported token via IPC
                endpoints[b_id]["inbox"].put({"command": "GET_SECURITY_IDENTITY"})
                resp = endpoints[b_id]["outbox"].get(timeout=10)
                assert resp["status"] == "SUCCESS"
                assert resp["identity"]["is_restricted"] is True
                assert expected_bank_sid in resp["identity"]["restricted_sids"]
                assert resp["effective_identity"]["is_restricted"] is True
        finally:
            for b_id in ("BANK_A", "BANK_B", "BANK_C"):
                with contextlib.suppress(Exception):
                    endpoints[b_id]["inbox"].put({"command": "STOP"})
                    procs[b_id].join(timeout=3)
                    if procs[b_id].is_alive():
                        procs[b_id].terminate()

    def test_bank_fix_02_bank_a_real_process_cross_bank_access_denial(self, pg_roles: dict[str, str], tmp_path) -> None:
        """BANK-FIX-02: From Bank A's real worker process, attempt to read Bank B/C datasets and encryption keys.

        Verify actual OS access denial.
        """
        if os.name != "nt":
            pytest.skip("Windows NT required for NTFS DACL process verification")

        vault_root, data_paths, encryption_keys = _provision_test_vaults(tmp_path)
        bank_db_urls = {
            "BANK_A": pg_roles["worker_bank_a"],
            "BANK_B": pg_roles["worker_bank_b"],
            "BANK_C": pg_roles["worker_bank_c"],
        }

        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_tokens=BANK_TOKENS,
            db_url=pg_roles["admin"],
            bank_data_paths=data_paths,
            bank_db_urls=bank_db_urls,
            bank_encryption_keys=encryption_keys,
            app_env="production",
        )

        try:
            key_path_b = os.path.join(vault_root, "BANK_B", "encryption.key")
            key_path_c = os.path.join(vault_root, "BANK_C", "encryption.key")

            # Attack 1: Bank A attempts to read Bank B dataset
            endpoints["BANK_A"]["inbox"].put({"command": "ATTEMPT_READ_FILE", "path": data_paths["BANK_B"]})
            resp_data_b = endpoints["BANK_A"]["outbox"].get(timeout=10)
            assert resp_data_b["status"] == "DENIED", f"Bank A reading Bank B data must be DENIED: {resp_data_b}"
            assert "Permission denied" in resp_data_b.get("error", "")

            # Attack 2: Bank A attempts to read Bank B encryption key
            endpoints["BANK_A"]["inbox"].put({"command": "ATTEMPT_READ_FILE", "path": key_path_b})
            resp_key_b = endpoints["BANK_A"]["outbox"].get(timeout=10)
            assert resp_key_b["status"] == "DENIED", f"Bank A reading Bank B key must be DENIED: {resp_key_b}"
            assert "Permission denied" in resp_key_b.get("error", "")

            # Attack 3: Bank A attempts to read Bank C dataset
            endpoints["BANK_A"]["inbox"].put({"command": "ATTEMPT_READ_FILE", "path": data_paths["BANK_C"]})
            resp_data_c = endpoints["BANK_A"]["outbox"].get(timeout=10)
            assert resp_data_c["status"] == "DENIED", f"Bank A reading Bank C data must be DENIED: {resp_data_c}"
            assert "Permission denied" in resp_data_c.get("error", "")

            # Attack 4: Bank A attempts to read Bank C encryption key
            endpoints["BANK_A"]["inbox"].put({"command": "ATTEMPT_READ_FILE", "path": key_path_c})
            resp_key_c = endpoints["BANK_A"]["outbox"].get(timeout=10)
            assert resp_key_c["status"] == "DENIED", f"Bank A reading Bank C key must be DENIED: {resp_key_c}"
            assert "Permission denied" in resp_key_c.get("error", "")

            # Positive control: Bank A can legitimately read its own encryption key
            key_path_a = os.path.join(vault_root, "BANK_A", "encryption.key")
            endpoints["BANK_A"]["inbox"].put({"command": "ATTEMPT_READ_FILE", "path": key_path_a})
            resp_self = endpoints["BANK_A"]["outbox"].get(timeout=10)
            assert resp_self["status"] == "SUCCESS"
            assert resp_self["bytes_read"] > 0
        finally:
            for b in ("BANK_A", "BANK_B", "BANK_C"):
                with contextlib.suppress(Exception):
                    endpoints[b]["inbox"].put({"command": "STOP"})
                    procs[b].join(timeout=3)
                    if procs[b].is_alive():
                        procs[b].terminate()

    def test_bank_fix_03_bank_b_and_c_real_processes_cross_bank_denial(self, pg_roles: dict[str, str], tmp_path) -> None:
        """BANK-FIX-03: Repeat cross-bank attacks from Bank B and Bank C."""
        if os.name != "nt":
            pytest.skip("Windows NT required for NTFS DACL process verification")

        vault_root, data_paths, encryption_keys = _provision_test_vaults(tmp_path)
        bank_db_urls = {
            "BANK_A": pg_roles["worker_bank_a"],
            "BANK_B": pg_roles["worker_bank_b"],
            "BANK_C": pg_roles["worker_bank_c"],
        }

        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_tokens=BANK_TOKENS,
            db_url=pg_roles["admin"],
            bank_data_paths=data_paths,
            bank_db_urls=bank_db_urls,
            bank_encryption_keys=encryption_keys,
            app_env="production",
        )

        try:
            key_path_a = os.path.join(vault_root, "BANK_A", "encryption.key")
            key_path_b = os.path.join(vault_root, "BANK_B", "encryption.key")
            key_path_c = os.path.join(vault_root, "BANK_C", "encryption.key")

            # --- Bank B attacks ---
            # Bank B -> Bank A data
            endpoints["BANK_B"]["inbox"].put({"command": "ATTEMPT_READ_FILE", "path": data_paths["BANK_A"]})
            r_ba_data = endpoints["BANK_B"]["outbox"].get(timeout=10)
            assert r_ba_data["status"] == "DENIED"

            # Bank B -> Bank A key
            endpoints["BANK_B"]["inbox"].put({"command": "ATTEMPT_READ_FILE", "path": key_path_a})
            r_ba_key = endpoints["BANK_B"]["outbox"].get(timeout=10)
            assert r_ba_key["status"] == "DENIED"

            # Bank B -> Bank C data
            endpoints["BANK_B"]["inbox"].put({"command": "ATTEMPT_READ_FILE", "path": data_paths["BANK_C"]})
            r_bc_data = endpoints["BANK_B"]["outbox"].get(timeout=10)
            assert r_bc_data["status"] == "DENIED"

            # Bank B -> Bank B own key
            endpoints["BANK_B"]["inbox"].put({"command": "ATTEMPT_READ_FILE", "path": key_path_b})
            r_bb_self = endpoints["BANK_B"]["outbox"].get(timeout=10)
            assert r_bb_self["status"] == "SUCCESS"

            # --- Bank C attacks ---
            # Bank C -> Bank A data
            endpoints["BANK_C"]["inbox"].put({"command": "ATTEMPT_READ_FILE", "path": data_paths["BANK_A"]})
            r_ca_data = endpoints["BANK_C"]["outbox"].get(timeout=10)
            assert r_ca_data["status"] == "DENIED"

            # Bank C -> Bank B data
            endpoints["BANK_C"]["inbox"].put({"command": "ATTEMPT_READ_FILE", "path": data_paths["BANK_B"]})
            r_cb_data = endpoints["BANK_C"]["outbox"].get(timeout=10)
            assert r_cb_data["status"] == "DENIED"

            # Bank C -> Bank C own key
            endpoints["BANK_C"]["inbox"].put({"command": "ATTEMPT_READ_FILE", "path": key_path_c})
            r_cc_self = endpoints["BANK_C"]["outbox"].get(timeout=10)
            assert r_cc_self["status"] == "SUCCESS"
        finally:
            for b in ("BANK_A", "BANK_B", "BANK_C"):
                with contextlib.suppress(Exception):
                    endpoints[b]["inbox"].put({"command": "STOP"})
                    procs[b].join(timeout=3)
                    if procs[b].is_alive():
                        procs[b].terminate()

    def test_bank_fix_04_compromised_worker_revert_to_self_cannot_escalate(self, pg_roles: dict[str, str], tmp_path) -> None:
        """BANK-FIX-04: Inside a compromised bank worker process, attempt RevertToSelf() and other available

        privilege-recovery paths. Verify that the process cannot regain access to another bank's vault.
        """
        if os.name != "nt":
            pytest.skip("Windows NT required for RevertToSelf process test")

        vault_root, data_paths, encryption_keys = _provision_test_vaults(tmp_path)
        bank_db_urls = {
            "BANK_A": pg_roles["worker_bank_a"],
            "BANK_B": pg_roles["worker_bank_b"],
            "BANK_C": pg_roles["worker_bank_c"],
        }

        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A", "BANK_B"],
            bank_tokens=BANK_TOKENS,
            db_url=pg_roles["admin"],
            bank_data_paths=data_paths,
            bank_db_urls=bank_db_urls,
            bank_encryption_keys=encryption_keys,
            app_env="production",
        )

        try:
            key_path_b = os.path.join(vault_root, "BANK_B", "encryption.key")

            # 1. Compromised Bank A worker calls RevertToSelf() and attempts to read Bank B dataset
            endpoints["BANK_A"]["inbox"].put({
                "command": "ATTEMPT_REVERT_AND_READ",
                "path": data_paths["BANK_B"],
            })
            resp_revert = endpoints["BANK_A"]["outbox"].get(timeout=10)

            # Even after RevertToSelf(), primary token is still restricted, and read is strictly DENIED
            assert resp_revert["status"] == "DENIED", f"Expected DENIED after RevertToSelf, got: {resp_revert}"
            assert resp_revert["is_restricted"] is True, "Process must remain restricted after RevertToSelf"
            assert get_bank_security_sid("BANK_A") in resp_revert["restricted_sids"]

            # 1b. Compromised Bank A worker also attempts to read Bank B key after RevertToSelf()
            endpoints["BANK_A"]["inbox"].put({
                "command": "ATTEMPT_REVERT_AND_READ",
                "path": key_path_b,
            })
            resp_revert_key = endpoints["BANK_A"]["outbox"].get(timeout=10)
            assert resp_revert_key["status"] == "DENIED"

            # 2. Compromised Bank A worker attempts privilege recovery
            endpoints["BANK_A"]["inbox"].put({"command": "ATTEMPT_PRIVILEGE_RECOVERY"})
            resp_priv = endpoints["BANK_A"]["outbox"].get(timeout=10)
            assert resp_priv["status"] == "SUCCESS"
            assert resp_priv["is_restricted"] is True
            assert resp_priv["is_admin_disabled"] is True
        finally:
            for b in ("BANK_A", "BANK_B"):
                with contextlib.suppress(Exception):
                    endpoints[b]["inbox"].put({"command": "STOP"})
                    procs[b].join(timeout=3)
                    if procs[b].is_alive():
                        procs[b].terminate()

    def test_bank_fix_05_restricted_coordinator_denied_all_bank_vaults(self, tmp_path) -> None:
        """BANK-FIX-05: Verify the actual restricted coordinator cannot access any bank's private dataset or encryption key."""
        if os.name != "nt":
            pytest.skip("Windows NT required for coordinator process test")

        vault_root, data_paths, encryption_keys = _provision_test_vaults(tmp_path)

        # Spawn genuine isolated coordinator process
        coord_proc, coord_client = spawn_isolated_coordinator_process(app_env="production")
        assert coord_client.pid > 0

        try:
            # Audit coordinator's primary token
            coord_id = get_os_process_identity(coord_client.pid, check_thread=False)
            assert coord_id.details["is_restricted"] is True
            assert COORDINATOR_RESTRICTED_SID in coord_id.details["restricted_sids"]

            # Verify that coordinator execution context is strictly denied access to all bank datasets and keys
            for b in ("BANK_A", "BANK_B", "BANK_C"):
                key_path = os.path.join(vault_root, b, "encryption.key")
                data_path = data_paths[b]

                with CoordinatorOSSecurityContext():
                    with pytest.raises(PermissionError):
                        with open(data_path, "rb") as f:
                            f.read()
                    with pytest.raises(PermissionError):
                        with open(key_path, "rb") as f:
                            f.read()
        finally:
            coord_client.terminate()

    def test_bank_fix_06_access_denial_persists_after_worker_termination(self, pg_roles: dict[str, str], tmp_path) -> None:
        """BANK-FIX-06: Verify access denial persists after the target bank worker terminates and file locks are released."""
        if os.name != "nt":
            pytest.skip("Windows NT required for persistent DACL test")

        vault_root, data_paths, encryption_keys = _provision_test_vaults(tmp_path)
        bank_db_urls = {
            "BANK_A": pg_roles["worker_bank_a"],
            "BANK_B": pg_roles["worker_bank_b"],
        }

        # Spawn Bank A and Bank B
        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A", "BANK_B"],
            bank_tokens=BANK_TOKENS,
            db_url=pg_roles["admin"],
            bank_data_paths=data_paths,
            bank_db_urls=bank_db_urls,
            bank_encryption_keys=encryption_keys,
            app_env="production",
        )

        try:
            # 1. Terminate Bank A worker cleanly and wait for OS file lock release
            endpoints["BANK_A"]["inbox"].put({"command": "STOP"})
            procs["BANK_A"].join(timeout=3)
            if procs["BANK_A"].is_alive():
                procs["BANK_A"].terminate()
                procs["BANK_A"].join(timeout=3)
            assert procs["BANK_A"].is_alive() is False

            # 2. From running Bank B worker, attempt to read terminated Bank A's dataset
            endpoints["BANK_B"]["inbox"].put({
                "command": "ATTEMPT_READ_FILE",
                "path": data_paths["BANK_A"],
            })
            resp = endpoints["BANK_B"]["outbox"].get(timeout=10)
            assert resp["status"] == "DENIED", f"Bank B reading terminated Bank A data must be DENIED: {resp}"
            assert "Permission denied" in resp.get("error", "")

            # 3. From running Bank B worker, attempt to read terminated Bank A's key
            key_path_a = os.path.join(vault_root, "BANK_A", "encryption.key")
            endpoints["BANK_B"]["inbox"].put({
                "command": "ATTEMPT_READ_FILE",
                "path": key_path_a,
            })
            resp_key = endpoints["BANK_B"]["outbox"].get(timeout=10)
            assert resp_key["status"] == "DENIED", f"Bank B reading terminated Bank A key must be DENIED: {resp_key}"
            assert "Permission denied" in resp_key.get("error", "")
        finally:
            with contextlib.suppress(Exception):
                endpoints["BANK_B"]["inbox"].put({"command": "STOP"})
                procs["BANK_B"].join(timeout=3)
                if procs["BANK_B"].is_alive():
                    procs["BANK_B"].terminate()

    def test_bank_fix_07_deliberate_token_or_dacl_failure_fails_closed(self, pg_roles: dict[str, str], tmp_path) -> None:
        """BANK-FIX-07: Deliberately fail restricted-token creation, process launching, or required DACL configuration.

        Production execution must fail closed.
        """
        vault_root, data_paths, encryption_keys = _provision_test_vaults(tmp_path, banks=["BANK_A"])
        bank_db_urls = {"BANK_A": pg_roles["worker_bank_a"]}

        # 1. Token creation failure fails closed
        with mock.patch(
            "app.application.services.distributed_fl_service.create_bank_worker_security_token",
            side_effect=RuntimeError("Simulated Token Creation OS Failure"),
        ):
            with pytest.raises(TrainingProvenanceConfigurationError, match="Failed to create operating system restricted security token"):
                spawn_bank_workers(
                    participating_banks=["BANK_A"],
                    bank_tokens=BANK_TOKENS,
                    db_url=pg_roles["admin"],
                    bank_data_paths=data_paths,
                    bank_db_urls=bank_db_urls,
                    bank_encryption_keys=encryption_keys,
                    app_env="production",
                )

        # 2. Process launch failure fails closed
        if os.name == "nt":
            with mock.patch("win32process.CreateProcessAsUser", side_effect=OSError("Simulated CreateProcessAsUser Failure")):
                with pytest.raises(TrainingProvenanceConfigurationError, match="Failed to spawn isolated bank worker process"):
                    spawn_bank_workers(
                        participating_banks=["BANK_A"],
                        bank_tokens=BANK_TOKENS,
                        db_url=pg_roles["admin"],
                        bank_data_paths=data_paths,
                        bank_db_urls=bank_db_urls,
                        bank_encryption_keys=encryption_keys,
                        app_env="production",
                    )

        # 3. DACL configuration failure fails closed
        if os.name == "nt":
            with mock.patch("win32security.SetNamedSecurityInfo", side_effect=OSError("Simulated DACL Failure")):
                with pytest.raises(TrainingProvenanceConfigurationError, match="Failed to configure mandatory operating system NTFS DACL"):
                    setup_bank_private_storage(
                        bank_storage_dir=str(tmp_path / "failed_dacl"),
                        bank_id="BANK_A",
                        X_partition=np.zeros((10, NUM_FEATURES)),
                        y_partition=np.zeros(10),
                        bank_token=BANK_TOKENS["BANK_A"],
                        bank_encryption_key=Fernet.generate_key(),
                        app_env="production",
                    )

    def test_bank_fix_08_unrestricted_worker_cannot_produce_trusted_observations(self, pg_roles: dict[str, str], tmp_path) -> None:
        """BANK-FIX-08: Verify that no unrestricted multiprocessing.Process or development fallback can

        produce production-trusted bank observations.
        """
        # When an unrestricted process calls verify_restricted_bank_worker_os_boundary in production:
        if os.name == "nt":
            # Direct call from unrestricted pytest runner process
            with pytest.raises(TrainingProvenanceSecurityError, match="must execute under an enforced restricted"):
                verify_restricted_bank_worker_os_boundary("BANK_A", app_env="production")

        # In production on non-Windows:
        if os.name != "nt":
            with pytest.raises(TrainingProvenanceSecurityError, match="Production bank worker isolation is not supported"):
                verify_restricted_bank_worker_os_boundary("BANK_A", app_env="production")

    def test_bank_fix_09_authorized_bank_workers_decrypt_and_train(self, pg_roles: dict[str, str], tmp_path) -> None:
        """BANK-FIX-09: Confirm that every authorized bank worker can still decrypt its own dataset and perform genuine PyTorch training."""
        if os.name != "nt":
            pytest.skip("Windows NT required for worker training test")

        vault_root, data_paths, encryption_keys = _provision_test_vaults(tmp_path)
        bank_db_urls = {
            "BANK_A": pg_roles["worker_bank_a"],
            "BANK_B": pg_roles["worker_bank_b"],
            "BANK_C": pg_roles["worker_bank_c"],
        }

        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_tokens=BANK_TOKENS,
            db_url=pg_roles["admin"],
            bank_data_paths=data_paths,
            bank_db_urls=bank_db_urls,
            bank_encryption_keys=encryption_keys,
            app_env="production",
        )

        try:
            job_id = f"test_job_fix09_{uuid.uuid4().hex[:8]}"
            authority = PostgresTrainingJobAuthority(db_url=pg_roles["admin"])
            authority.schedule_job(
                job_id=job_id,
                training_run_id=f"run_{job_id}",
                simulation_id=f"sim_{job_id}",
                architecture="LogisticRegression",
                feature_schema_version="v1.0",
                expected_rounds=1,
                expected_epochs=1,
                input_dim=NUM_FEATURES,
                feature_names=[f"feat_{i}" for i in range(NUM_FEATURES)],
                coordinator_id="COORDINATOR",
                permitted_workers=["BANK_A", "BANK_B", "BANK_C", "TRAINING_EXECUTOR"],
            )
            authority.start_job(job_id=job_id, worker_id="TRAINING_EXECUTOR")

            # Send TRAIN_ROUND to all 3 banks
            for b in ("BANK_A", "BANK_B", "BANK_C"):
                endpoints[b]["inbox"].put({
                    "command": "TRAIN_ROUND",
                    "job_id": job_id,
                    "round_number": 1,
                    "global_weights": None,
                })
                resp = endpoints[b]["outbox"].get(timeout=25)
                assert resp["status"] == "SUCCESS", f"Bank [{b}] training failed: {resp}"
                assert resp["round_number"] == 1
                assert "weights" in resp
                assert "update_digest" in resp
                assert len(resp["update_digest"]) == 64
                assert resp["loss"] > 0
                assert resp["sample_count"] == 64

            # Verify observations in PostgreSQL authority
            obs_list = authority.get_observations(job_id=job_id)
            assert len(obs_list) == 3
            participating = {o.bank_id for o in obs_list}
            assert participating == {"BANK_A", "BANK_B", "BANK_C"}
        finally:
            for b in ("BANK_A", "BANK_B", "BANK_C"):
                with contextlib.suppress(Exception):
                    endpoints[b]["inbox"].put({"command": "STOP"})
                    procs[b].join(timeout=3)
                    if procs[b].is_alive():
                        procs[b].terminate()

    def test_bank_fix_10_genuine_two_round_three_bank_federation(self, pg_roles: dict[str, str], tmp_path) -> None:
        """BANK-FIX-10: Execute genuine two-round, three-bank federated training with the corrected bank

        processes and restricted coordinator, including PostgreSQL evidence, completed-job reconciliation,
        verified Ed25519 receipt, and inactive Registry candidate registration.
        """
        if os.name != "nt":
            pytest.skip("Windows NT required for full federation test")

        coord_url = pg_roles["coordinator"]
        comp_url = pg_roles["completion"]
        issuer_url = pg_roles["issuer"]

        comp_vault_dir = str(tmp_path / "comp_vault_fix10")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )

        vault_root = str(tmp_path / "bank_vaults_fix10")
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
        issuer_vault_dir = str(tmp_path / "issuer_vault_fix10")
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
            job_id = f"job_fix10_{uid}"
            run_id = f"run_fix10_{uid}"
            sim_id = f"sim_fix10_{uid}"
            registry_dir = str(tmp_path / "model_reg_fix10")

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
            import psycopg
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
