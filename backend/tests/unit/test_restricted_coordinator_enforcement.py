"""Adversarial Verification Suite: Mandatory Restricted Coordinator Execution in Production (EXEC-01 through EXEC-10).

Verifies the single mandatory invariant:
Every production-authoritative distributed federated training execution must run
through the required, verified restricted coordinator security boundary.
No ordinary production caller may bypass the restriction by:
- Instantiating DistributedFederatedTrainingManager directly.
- Calling execute_distributed_federation() directly.
- Using an alternate factory or wrapper.
- Supplying app_env="production" inside an unrestricted process.
- Using a legacy coordinator entry point.
- Falling back to unrestricted execution after launch failure.
- Invoking a lower-level production-authoritative method that lacks equivalent enforcement.
"""

from __future__ import annotations

import hashlib
import os
import unittest.mock as mock
import uuid

import numpy as np
import psycopg
import pytest
from cryptography.fernet import Fernet

from app.application.services.distributed_fl_service import (
    COORDINATOR_ALLOWED_WINDOWS_PRIVILEGES,
    DistributedFederatedTrainingManager,
    IsolatedReceiptSigningAuthority,
    SecurityIdentityInfo,
    get_os_process_identity,
    provision_isolated_completion_authority_vault,
    provision_isolated_issuer_vault,
    run_production_federation,
    setup_bank_private_storage,
    spawn_bank_workers,
    spawn_isolated_completion_authority_service,
    spawn_isolated_coordinator_process,
    validate_restricted_coordinator_policy,
    verify_restricted_coordinator_os_boundary,
)
from app.application.services.model_service import NUM_FEATURES
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
)
from app.infrastructure.database.training_authority_backends import (
    provision_runtime_service_roles,
)

COORD_SECRET = "coord_secret_exec_enforce_99182_prod"
BANK_A_SECRET = "bank_a_secret_exec_enforce_1101_alpha"
BANK_B_SECRET = "bank_b_secret_exec_enforce_2202_beta"
BANK_C_SECRET = "bank_c_secret_exec_enforce_3303_gamma"

BANK_TOKENS = {
    "BANK_A": BANK_A_SECRET,
    "BANK_B": BANK_B_SECRET,
    "BANK_C": BANK_C_SECRET,
}


@pytest.fixture(scope="module")
def real_pg_instance():
    """Start embedded real PostgreSQL 18.1 instance and yield connection URI."""
    try:
        import pg0
    except ImportError:
        pytest.skip("pg0 is not installed (PostgreSQL 18.1 embedded authority unavailable)")
    try:
        import psycopg  # noqa: F401
    except ImportError:
        pytest.skip("psycopg is not installed (PostgreSQL driver unavailable)")

    pg = pg0.Pg0()
    pg.start()
    try:
        assert pg.uri is not None
        yield pg.uri
    finally:
        pg.stop()


class TestRestrictedCoordinatorEnforcement:
    """Rigorous adversarial test suite for mandatory restricted coordinator OS execution."""

    def test_exec_01_unrestricted_direct_construction(self, real_pg_instance: str) -> None:
        """EXEC-01: Direct construction of DistributedFederatedTrainingManager outside the approved boundary is rejected.

        From an unrestricted production process, attempting to construct the manager
        must fail closed with TrainingProvenanceSecurityError before creating any execution evidence.
        """
        # The pytest process is an ordinary user process without restricting SIDs.
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="Production federation coordinator must execute under an enforced restricted operating system security token",
        ):
            DistributedFederatedTrainingManager(
                coordinator_token=COORD_SECRET,
                db_url=real_pg_instance,
                app_env="production",
            )

    def test_exec_02_alternate_direct_invocation(self, real_pg_instance: str) -> None:
        """EXEC-02: Bypassing __init__ or mutating app_env to production before execute_distributed_federation is rejected.

        An adversary instantiates the manager with app_env='testing' and subsequently sets
        manager.app_env = 'production', then invokes execute_distributed_federation.
        The boundary check inside execute_distributed_federation must reject execution.
        """
        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="testing",
        )
        # Attempt to subvert the environment to claim production-authoritative evidence
        manager.app_env = "production"

        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="Production federation coordinator must execute under an enforced restricted operating system security token",
        ):
            manager.execute_distributed_federation(
                job_id="job_exec02_subvert",
                training_run_id="run_exec02_subvert",
                simulation_id="sim_exec02_subvert",
                holdout_val_data=(np.zeros((5, NUM_FEATURES)), np.zeros(5)),
                participating_banks=["BANK_A"],
                worker_endpoints={},
                num_rounds=1,
            )

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_exec_03_secure_launch_succeeds(self) -> None:
        """EXEC-03: Real coordinator launched through authorized restricted-process launcher satisfies mandatory OS security policy.

        Verifies the actual kernel access token of the spawned process:
        - IsTokenRestricted == True
        - Restricted SID S-1-5-12 present
        - Administrative group S-1-5-32-544 disabled (SE_GROUP_USE_FOR_DENY_ONLY)
        - Administrative privileges stripped (<= 1 enabled privilege).
        """
        proc, client = spawn_isolated_coordinator_process(app_env="production")
        try:
            ident = client.get_process_identity()
            details = ident.details

            assert details.get("is_restricted") is True, "Spawned process token must be restricted"
            assert details.get("has_restricted_code_sid") is True, "Token must have S-1-5-12 restricting SID"
            assert details.get("is_admin_disabled") is True, "Admin group must be SE_GROUP_USE_FOR_DENY_ONLY"
            assert "privileges" in details, "Mandatory privileges evidence must be present"
            assert set(details["privileges"]) <= COORDINATOR_ALLOWED_WINDOWS_PRIVILEGES, (
                f"Unauthorized privileges present: {details['privileges']}"
            )
            assert details["privileges"] == ["SeChangeNotifyPrivilege"], (
                f"Spawned restricted token must contain only SeChangeNotifyPrivilege, got: {details['privileges']}"
            )
        finally:
            client.terminate()

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_exec_04_invalid_security_token_fails_closed(self) -> None:
        """EXEC-04: Restricted-token creation failure causes production startup to fail closed.

        Simulates an environment failure during create_restricted_coordinator_token().
        Production startup must fail with TrainingProvenanceConfigurationError and leave no dangling resources.
        """
        with mock.patch(
            "app.application.services.distributed_fl_service.create_restricted_coordinator_token",
            side_effect=RuntimeError("Simulated kernel restricted token creation failure"),
        ):
            with pytest.raises(TrainingProvenanceConfigurationError, match="Failed to create operating system restricted coordinator security token"):
                spawn_isolated_coordinator_process(app_env="production")

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_exec_05_misleading_ready_handshake_rejected(self) -> None:
        """EXEC-05: Child process reporting READY while violating mandatory OS security policy is rejected and terminated.

        Simulates a compromised or misleading child reporting READY but having an unrestricted kernel token.
        Parent independent token audit must catch the violation, terminate the child, and raise TrainingProvenanceSecurityError.
        """
        # Mock get_os_process_identity to return an unrestricted identity for the spawned child
        unrestricted_identity = get_os_process_identity()  # Current process is unrestricted
        with mock.patch(
            "app.application.services.distributed_fl_service.get_os_process_identity",
            return_value=unrestricted_identity,
        ):
            with pytest.raises(
                TrainingProvenanceSecurityError,
                match="Spawned coordinator process violates mandatory production security policy.*EXEC-05",
            ):
                spawn_isolated_coordinator_process(app_env="production")

    def test_exec_06_unsupported_platform_fails_closed(self) -> None:
        """EXEC-06: Production startup on unsupported operating system fails closed.

        Verifies that on non-Windows platforms (where restricted tokens are not implemented),
        both verify_restricted_coordinator_os_boundary and spawn_isolated_coordinator_process
        fail closed with TrainingProvenanceSecurityError without silent fallback.
        """
        with mock.patch("os.name", "posix"), mock.patch("sys.platform", "linux"):
            with pytest.raises(
                TrainingProvenanceSecurityError,
                match="Production coordinator isolation is not supported on platform 'linux'.*EXEC-06",
            ):
                verify_restricted_coordinator_os_boundary(app_env="production")

            with pytest.raises(
                TrainingProvenanceSecurityError,
                match="Production coordinator isolation is not supported on platform 'linux'.*EXEC-06",
            ):
                spawn_isolated_coordinator_process(app_env="production")

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_exec_07_no_privileged_credential_recovery(self, real_pg_instance: str, tmp_path) -> None:
        """EXEC-07: Restricted coordinator process cannot read completion credentials or invoke privileged completion SQL.

        Verifies that:
        1. NTFS DACL denies reading the completion authority vault file (PermissionError).
        2. Attempting direct completion SQL under coordinator DB role is rejected.
        3. Attempting privilege escalation via RevertToSelf does not escape the restricted process token.
        """
        role_urls = provision_runtime_service_roles(real_pg_instance, app_env="production")
        vault_dir = str(tmp_path / "comp_vault_exec07")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, role_urls["completion"])

        proc, client = spawn_isolated_coordinator_process(app_env="production")
        try:
            # 1. Attempt to read completion vault
            read_res = client.attempt_read_vault(vault_file)
            assert read_res.get("status") == "DENIED"
            assert "PermissionError" in read_res.get("error_type", "") or "Access is denied" in read_res.get("error", "")

            # 2. Attempt privilege escalation
            esc_res = client.attempt_privilege_escalation(vault_file)
            assert esc_res.get("is_restricted") is True
            assert esc_res.get("read_err") is not None

            # 3. Establish valid prerequisite record in cfi_scheduled_jobs
            job_id = f"job_exec07_{uuid.uuid4().hex[:8]}"
            run_id = f"run_exec07_{uuid.uuid4().hex[:8]}"
            sim_id = f"sim_exec07_{uuid.uuid4().hex[:8]}"
            with psycopg.connect(role_urls["coordinator"]) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO cfi_scheduled_jobs (
                            job_id, training_run_id, simulation_id, coordinator_id,
                            architecture, feature_schema_version, expected_rounds,
                            expected_epochs, input_dim, feature_names_json,
                            permitted_workers_json, config_fingerprint, scheduled_at, status
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), 'SCHEDULED')
                        """,
                        (
                            job_id, run_id, sim_id, "COORDINATOR",
                            "FraudDetectionNN", "v1.0", 2, 1, NUM_FEATURES,
                            '["f1", "f2"]', '["BANK_A", "BANK_B", "BANK_C"]', "fp_123",
                        ),
                    )
                conn.commit()

            # 4. Attempt direct completion SQL via coordinator connection targeting cfi_completed_jobs
            valid_completion_sql = f"""
                INSERT INTO cfi_completed_jobs (
                    job_id, training_run_id, simulation_id, completed_at,
                    executed_rounds, executed_epochs, sample_count,
                    observed_metrics_json, artifact_sha256, architecture,
                    feature_schema_version, input_dim, feature_names_json,
                    worker_id, status, git_commit_hash, dataset_manifest_id,
                    evidence_fingerprint
                ) VALUES (
                    '{job_id}', '{run_id}', '{sim_id}', '2026-10-12T00:00:00',
                    2, 1, 100, '{{}}', 'sha256_fake_artifact', 'FraudDetectionNN',
                    'v1.0', {NUM_FEATURES}, '["f1", "f2"]', 'COORDINATOR', 'COMPLETED',
                    NULL, NULL, 'fp_fake_exec07'
                )
            """
            sql_res = client.attempt_direct_sql(role_urls["coordinator"], valid_completion_sql)
            assert sql_res.get("status") == "DENIED"
            assert sql_res.get("sqlstate") == "42501", (
                f"Expected SQLSTATE 42501 (insufficient_privilege) on cfi_completed_jobs, got {sql_res.get('sqlstate')}"
            )
            assert "InsufficientPrivilege" in sql_res.get("error_type", "") or "permission denied" in sql_res.get("error", "").lower()

            # 5. Verify zero forged completed rows persisted
            with psycopg.connect(role_urls["coordinator"]) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                    row = cur.fetchone()
                    assert row is not None
                    count = row[0]
                    assert count == 0, f"No forged completed rows must be persisted, found {count}"
        finally:
            client.terminate()

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_exec_08_real_federation_through_enforced_path(self, real_pg_instance: str, tmp_path) -> None:
        """EXEC-08: Genuine two-round, three-bank PyTorch federated training succeeds through canonical restricted coordinator path.

        Verifies:
        - Real bank workers execute training in independent processes.
        - Restricted coordinator aggregates model updates over 2 rounds.
        - PostgreSQL observations verified.
        - Independent completion authority reconciles and finalizes job.
        - Ed25519 receipt is signed and verified by public key.
        - ModelRegistry admits candidate as inactive with verified provenance.
        """
        role_urls = provision_runtime_service_roles(real_pg_instance, app_env="production")
        job_id = f"job_exec08_{uuid.uuid4().hex[:8]}"
        run_id = f"run_exec08_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_exec08_{uuid.uuid4().hex[:8]}"

        # Setup bank datasets
        vaults_dir = str(tmp_path / "bank_vaults")
        np.random.seed(42)
        X_a = np.random.randn(120, NUM_FEATURES).astype(np.float32)
        y_a = np.random.randint(0, 2, size=120).astype(np.float32)
        X_b = np.random.randn(150, NUM_FEATURES).astype(np.float32)
        y_b = np.random.randint(0, 2, size=150).astype(np.float32)
        X_c = np.random.randn(180, NUM_FEATURES).astype(np.float32)
        y_c = np.random.randint(0, 2, size=180).astype(np.float32)

        bank_enc_keys: dict[str, str | bytes] = {
            "BANK_A": Fernet.generate_key(),
            "BANK_B": Fernet.generate_key(),
            "BANK_C": Fernet.generate_key(),
        }
        data_path_a = setup_bank_private_storage(vaults_dir, "BANK_A", X_a, y_a, bank_token=BANK_TOKENS["BANK_A"], bank_encryption_key=bank_enc_keys["BANK_A"])
        data_path_b = setup_bank_private_storage(vaults_dir, "BANK_B", X_b, y_b, bank_token=BANK_TOKENS["BANK_B"], bank_encryption_key=bank_enc_keys["BANK_B"])
        data_path_c = setup_bank_private_storage(vaults_dir, "BANK_C", X_c, y_c, bank_token=BANK_TOKENS["BANK_C"], bank_encryption_key=bank_enc_keys["BANK_C"])

        data_paths = {
            "BANK_A": data_path_a,
            "BANK_B": data_path_b,
            "BANK_C": data_path_c,
        }
        bank_db_urls = {
            "BANK_A": role_urls["worker_bank_a"],
            "BANK_B": role_urls["worker_bank_b"],
            "BANK_C": role_urls["worker_bank_c"],
        }
        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_tokens=BANK_TOKENS,
            db_url=role_urls["worker"],
            bank_data_paths=data_paths,
            bank_db_urls=bank_db_urls,
            bank_encryption_keys=bank_enc_keys,
        )

        # Setup completion authority service
        comp_vault_dir = str(tmp_path / "comp_vault")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, role_urls["completion"])
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )

        # Setup receipt signing authority
        dev_seed = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest().hex()
        issuer_vault_dir = str(tmp_path / "issuer_vault")
        issuer_key_file, _ = provision_isolated_issuer_vault(issuer_vault_dir, signing_key_hex=dev_seed)
        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=issuer_key_file,
            coordinator_token=COORD_SECRET,
            db_url=role_urls["issuer"],
            key_id=DEFAULT_TRAINING_KEY_ID,
            app_env="production",
        )

        registry_dir = str(tmp_path / "registry")
        worker_pids = {b_id: p.pid for b_id, p in procs.items()}

        try:
            result = run_production_federation(
                coordinator_token=COORD_SECRET,
                db_url=role_urls["coordinator"],
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(np.random.randn(30, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, size=30).astype(np.float32)),
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                worker_endpoints=endpoints,
                comp_service_endpoint=comp_endpoint,
                signing_authority=signing_auth,
                num_rounds=2,
                expected_unique_samples=450,
                registry_storage_dir=registry_dir,
                worker_pids=worker_pids,
                app_env="production",
            )

            assert result["status"] == "COMPLETED"
            assert result["is_verified"] is True
            assert result["coordinator_pid"] not in worker_pids.values()

            # Verify ModelRegistry admission
            saved_ver = result["saved_version"]
            assert saved_ver["is_production_eligible"] is True
            assert saved_ver["status"] == "inactive"
            assert saved_ver["provenance_type"] == "TRAINING_RECORDED"
            assert result["is_promoted_champion"] is False
        finally:
            comp_proc.terminate()
            for p in procs.values():
                p.terminate()

    def test_exec_09_canonical_production_entry_points_enforce_boundary(self) -> None:
        """EXEC-09: Canonical production entry points enforce boundary; direct construction fails closed.

        Demonstrates that:
        - run_production_federation invokes spawn_isolated_coordinator_process under the hood.
        - Direct construction of DistributedFederatedTrainingManager in production raises TrainingProvenanceSecurityError.
        """
        # 1. Direct construction fails closed
        with pytest.raises(TrainingProvenanceSecurityError):
            DistributedFederatedTrainingManager(
                coordinator_token="tok",
                db_url="postgresql://localhost/db",
                app_env="production",
            )

        # 2. Canonical runner spawns restricted coordinator
        with mock.patch("app.application.services.distributed_fl_service.spawn_isolated_coordinator_process") as mock_spawn:
            mock_client = mock.MagicMock()
            mock_client.execute_federation.return_value = {"status": "COMPLETED"}
            mock_spawn.return_value = (mock.MagicMock(), mock_client)

            res = run_production_federation(
                coordinator_token="tok",
                db_url="postgresql://localhost/db",
                job_id="job_9",
                training_run_id="run_9",
                simulation_id="sim_9",
                holdout_val_data=(np.zeros((2, NUM_FEATURES)), np.zeros(2)),
                participating_banks=["BANK_A"],
                worker_endpoints={},
                comp_service_endpoint=("127.0.0.1", 1234),
                signing_authority=mock.MagicMock(),
                app_env="production",
            )
            assert res["status"] == "COMPLETED"
            mock_spawn.assert_called_once_with(app_env="production")

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_exec_10_no_fallback_after_coordinator_crash(self) -> None:
        """EXEC-10: Terminating restricted coordinator raises TrainingProvenanceSecurityError without silent fallback.

        When the restricted coordinator process is terminated during execution,
        the client must raise TrainingProvenanceSecurityError and never fall back to unrestricted local execution.
        """
        proc, client = spawn_isolated_coordinator_process(app_env="production")
        # Terminate child process immediately
        client.terminate()

        # Attempt execution on terminated coordinator
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="Restricted coordinator process disconnected or terminated during execution.*EXEC-10",
        ):
            client.execute_federation(
                job_id="job_crashed",
                training_run_id="run_crashed",
                simulation_id="sim_crashed",
                holdout_val_data=(np.zeros((2, NUM_FEATURES)), np.zeros(2)),
                participating_banks=["BANK_A"],
                worker_endpoints={},
                num_rounds=1,
                expected_unique_samples=None,
                registry_storage_dir=None,
                coordinator_token="tok",
                db_url="postgresql://localhost/db",
                comp_service_endpoint=("127.0.0.1", 1234),
                signing_authority=mock.MagicMock(),
            )


class TestCoordinatorSecurityPolicy:
    """Mandatory adversarial test suite verifying coordinator operating-system security policy (POLICY-01 to POLICY-08)."""

    def test_policy_01_correctly_restricted_token_passes(self) -> None:
        """POLICY-01: Correctly restricted token passes authoritative validation."""
        details = {
            "pid": 5432,
            "user_sid": "S-1-5-21-99999-88888-77777-1001",
            "is_restricted": True,
            "has_restricted_code_sid": True,
            "restricted_sids": ["S-1-5-12", "S-1-5-21-99999-88888-77777-1001"],
            "is_admin_disabled": True,
            "group_sids": ["S-1-5-32-545", "S-1-5-32-544"],
            "disabled_groups": ["S-1-5-32-544"],
            "privileges": ["SeChangeNotifyPrivilege"],
            "integrity_sid": "S-1-16-8192",
        }
        validate_restricted_coordinator_policy(details)

    def test_policy_02_missing_privilege_evidence_is_rejected(self) -> None:
        """POLICY-02: Missing privilege evidence is rejected rather than assumed safe."""
        details = {
            "is_restricted": True,
            "has_restricted_code_sid": True,
            "restricted_sids": ["S-1-5-12"],
            "is_admin_disabled": True,
        }
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="Mandatory coordinator security evidence field 'privileges' is missing",
        ):
            validate_restricted_coordinator_policy(details)

    def test_policy_03_incorrectly_named_privilege_field_is_rejected(self) -> None:
        """POLICY-03: Incorrectly named privilege field ('enabled_privileges') is rejected rather than silently accepted."""
        details = {
            "is_restricted": True,
            "has_restricted_code_sid": True,
            "restricted_sids": ["S-1-5-12"],
            "is_admin_disabled": True,
            "enabled_privileges": ["SeChangeNotifyPrivilege"],
        }
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="Mandatory coordinator security evidence field 'privileges' is missing",
        ):
            validate_restricted_coordinator_policy(details)

    def test_policy_04_token_containing_unauthorized_enabled_privilege_is_rejected(self) -> None:
        """POLICY-04: Token containing unauthorized enabled privileges is rejected."""
        unauthorized_privs = [
            "SeDebugPrivilege",
            "SeShutdownPrivilege",
            "SeTakeOwnershipPrivilege",
            "SeImpersonatePrivilege",
            "SeTcbPrivilege",
        ]
        for bad_priv in unauthorized_privs:
            details = {
                "is_restricted": True,
                "has_restricted_code_sid": True,
                "restricted_sids": ["S-1-5-12"],
                "is_admin_disabled": True,
                "privileges": ["SeChangeNotifyPrivilege", bad_priv],
            }
            with pytest.raises(
                TrainingProvenanceSecurityError,
                match=f"unauthorized enabled privileges: \\['{bad_priv}'\\]",
            ):
                validate_restricted_coordinator_policy(details)

    def test_policy_05_missing_restricting_sid_is_rejected(self) -> None:
        """POLICY-05: Missing restricting SID (S-1-5-12) is rejected."""
        # Case A: has_restricted_code_sid is False
        details_a = {
            "is_restricted": True,
            "has_restricted_code_sid": False,
            "restricted_sids": ["S-1-5-21-99999"],
            "is_admin_disabled": True,
            "privileges": ["SeChangeNotifyPrivilege"],
        }
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="lacks mandatory WinRestrictedCodeSid \\(S-1-5-12\\) restriction",
        ):
            validate_restricted_coordinator_policy(details_a)

        # Case B: S-1-5-12 absent from restricted_sids list
        details_b = {
            "is_restricted": True,
            "has_restricted_code_sid": True,
            "restricted_sids": ["S-1-5-21-99999"],
            "is_admin_disabled": True,
            "privileges": ["SeChangeNotifyPrivilege"],
        }
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="lacks mandatory WinRestrictedCodeSid \\(S-1-5-12\\) restriction",
        ):
            validate_restricted_coordinator_policy(details_b)

    def test_policy_06_active_administrator_authority_is_rejected(self) -> None:
        """POLICY-06: Active administrator authority or contradictory evidence is rejected."""
        # Case A: is_admin_disabled is False
        details_a = {
            "is_restricted": True,
            "has_restricted_code_sid": True,
            "restricted_sids": ["S-1-5-12"],
            "is_admin_disabled": False,
            "privileges": ["SeChangeNotifyPrivilege"],
        }
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="active administrative authority \\(S-1-5-32-544 not disabled\\)",
        ):
            validate_restricted_coordinator_policy(details_a)

        # Case B: Contradictory evidence (admin SID in group_sids but missing from disabled_groups)
        details_b = {
            "is_restricted": True,
            "has_restricted_code_sid": True,
            "restricted_sids": ["S-1-5-12"],
            "is_admin_disabled": True,
            "group_sids": ["S-1-5-32-544"],
            "disabled_groups": [],
            "privileges": ["SeChangeNotifyPrivilege"],
        }
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="Contradictory security evidence: S-1-5-32-544 is present in group_sids but not in disabled_groups",
        ):
            validate_restricted_coordinator_policy(details_b)

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_policy_07_child_violating_policy_terminated(self) -> None:
        """POLICY-07: A child that reports READY but violates kernel token policy is terminated and rejected."""
        violating_identity = SecurityIdentityInfo(
            principal_id="mock_violating:RESTRICTED",
            principal_type="WindowsRestrictedProcessPrincipal",
            details={
                "pid": 9999,
                "user_sid": "S-1-5-21-mock",
                "is_restricted": True,
                "has_restricted_code_sid": True,
                "restricted_sids": ["S-1-5-12"],
                "is_admin_disabled": True,
                "privileges": ["SeChangeNotifyPrivilege", "SeDebugPrivilege"],
            },
        )
        with mock.patch(
            "app.application.services.distributed_fl_service.get_os_process_identity",
            return_value=violating_identity,
        ):
            with pytest.raises(
                TrainingProvenanceSecurityError,
                match="Spawned coordinator process violates mandatory production security policy.*EXEC-05",
            ):
                spawn_isolated_coordinator_process(app_env="production")

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_policy_08_actual_windows_token_inspection_agrees_with_launcher(self) -> None:
        """POLICY-08: Actual Windows process-token inspection agrees with the independent launcher decision."""
        proc, client = spawn_isolated_coordinator_process(app_env="production")
        try:
            # Child's internal token check
            child_ident = client.get_process_identity()
            validate_restricted_coordinator_policy(child_ident.details)

            # Parent launcher's independent token check of the child process
            parent_inspected_ident = get_os_process_identity(proc)
            validate_restricted_coordinator_policy(parent_inspected_ident.details)

            # Exact privilege set agreement
            assert child_ident.details["privileges"] == ["SeChangeNotifyPrivilege"]
            assert parent_inspected_ident.details["privileges"] == ["SeChangeNotifyPrivilege"]
        finally:
            client.terminate()


class TestCoordinatorSqlAuthorization:
    """Mandatory adversarial test suite verifying genuine PostgreSQL permission boundary (SQL-01 to SQL-06)."""

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_sql_01_coordinator_actual_postgresql_role_is_verified(self, real_pg_instance: str) -> None:
        """SQL-01: Coordinator's actual PostgreSQL role is verified as cfi_coord_role."""
        role_urls = provision_runtime_service_roles(real_pg_instance, app_env="production")
        proc, client = spawn_isolated_coordinator_process(app_env="production")
        try:
            sql_res = client.attempt_direct_sql(
                role_urls["coordinator"],
                "SELECT current_user, session_user;",
            )
            assert sql_res.get("status") == "SUCCESS", f"Query failed: {sql_res}"
            assert sql_res.get("rows") == [("cfi_coord_role", "cfi_coord_role")], (
                f"Expected cfi_coord_role, got {sql_res.get('rows')}"
            )
        finally:
            client.terminate()

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_sql_02_direct_completed_job_insert_denied_with_sqlstate_42501(self, real_pg_instance: str) -> None:
        """SQL-02: Direct completed-job INSERT into cfi_completed_jobs is denied with SQLSTATE 42501."""
        role_urls = provision_runtime_service_roles(real_pg_instance, app_env="production")
        job_id = f"job_sql02_{uuid.uuid4().hex[:8]}"
        run_id = f"run_sql02_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_sql02_{uuid.uuid4().hex[:8]}"

        # Establish prerequisite scheduled job record
        with psycopg.connect(role_urls["coordinator"]) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cfi_scheduled_jobs (
                        job_id, training_run_id, simulation_id, coordinator_id,
                        architecture, feature_schema_version, expected_rounds,
                        expected_epochs, input_dim, feature_names_json,
                        permitted_workers_json, config_fingerprint, scheduled_at, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), 'SCHEDULED')
                    """,
                    (
                        job_id, run_id, sim_id, "COORDINATOR",
                        "FraudDetectionNN", "v1.0", 2, 1, NUM_FEATURES,
                        '["f1", "f2"]', '["BANK_A", "BANK_B", "BANK_C"]', "fp_123",
                    ),
                )
            conn.commit()

        # Structurally and syntactically valid completion INSERT targeting authoritative cfi_completed_jobs
        valid_completion_sql = f"""
            INSERT INTO cfi_completed_jobs (
                job_id, training_run_id, simulation_id, completed_at,
                executed_rounds, executed_epochs, sample_count,
                observed_metrics_json, artifact_sha256, architecture,
                feature_schema_version, input_dim, feature_names_json,
                worker_id, status, git_commit_hash, dataset_manifest_id,
                evidence_fingerprint
            ) VALUES (
                '{job_id}', '{run_id}', '{sim_id}', '2026-10-12T00:00:00',
                2, 1, 100, '{{}}', 'sha256_fake_artifact', 'FraudDetectionNN',
                'v1.0', {NUM_FEATURES}, '["f1", "f2"]', 'COORDINATOR', 'COMPLETED',
                NULL, NULL, 'fp_fake_sql02'
            )
        """
        proc, client = spawn_isolated_coordinator_process(app_env="production")
        try:
            sql_res = client.attempt_direct_sql(role_urls["coordinator"], valid_completion_sql)
            assert sql_res.get("status") == "DENIED"
            assert sql_res.get("sqlstate") == "42501", (
                f"Expected SQLSTATE 42501 (insufficient_privilege) on cfi_completed_jobs, got {sql_res.get('sqlstate')}"
            )
            assert "InsufficientPrivilege" in sql_res.get("error_type", "") or "permission denied" in sql_res.get("error", "").lower()
        finally:
            client.terminate()

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_sql_03_test_fails_if_target_table_does_not_exist(self, real_pg_instance: str) -> None:
        """SQL-03: The authorization test fails if rejection occurs because the target table does not exist (42P01 vs 42501)."""
        role_urls = provision_runtime_service_roles(real_pg_instance, app_env="production")
        proc, client = spawn_isolated_coordinator_process(app_env="production")
        try:
            # Query against nonexistent table (the old false-positive test target)
            sql_res = client.attempt_direct_sql(
                role_urls["coordinator"],
                "INSERT INTO completed_job_execution_records (job_id, status) VALUES ('f', 'f')",
            )
            assert sql_res.get("status") == "DENIED"
            # Nonexistent table yields 42P01 (UndefinedTable), NOT 42501 (InsufficientPrivilege)
            assert sql_res.get("sqlstate") == "42P01", (
                f"Expected SQLSTATE 42P01 for nonexistent table, got {sql_res.get('sqlstate')}"
            )
            assert sql_res.get("sqlstate") != "42501", (
                "False-positive detection: 42P01 must NOT be accepted as 42501"
            )
            with pytest.raises(AssertionError):
                assert sql_res.get("sqlstate") == "42501"
        finally:
            client.terminate()

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_sql_04_test_fails_if_sql_is_malformed(self, real_pg_instance: str) -> None:
        """SQL-04: The authorization test fails if rejection occurs because the SQL is malformed (42601 vs 42501)."""
        role_urls = provision_runtime_service_roles(real_pg_instance, app_env="production")
        proc, client = spawn_isolated_coordinator_process(app_env="production")
        try:
            # Query with syntax error
            sql_res = client.attempt_direct_sql(
                role_urls["coordinator"],
                "INSRT INTO cfi_completed_jobs (job_id) VALUES ('f')",
            )
            assert sql_res.get("status") == "DENIED"
            assert sql_res.get("sqlstate") == "42601", (
                f"Expected SQLSTATE 42601 for syntax error, got {sql_res.get('sqlstate')}"
            )
            assert sql_res.get("sqlstate") != "42501", (
                "False-positive detection: 42601 syntax error must NOT be accepted as 42501"
            )
            with pytest.raises(AssertionError):
                assert sql_res.get("sqlstate") == "42501"
        finally:
            client.terminate()

    def test_sql_05_valid_authorized_finalization_succeeds_through_completion_authority(
        self, real_pg_instance: str
    ) -> None:
        """SQL-05: A valid authorized finalization succeeds through the actual completion authority (cfi_completion_role)."""
        role_urls = provision_runtime_service_roles(real_pg_instance, app_env="production")
        job_id = f"job_sql05_{uuid.uuid4().hex[:8]}"
        run_id = f"run_sql05_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_sql05_{uuid.uuid4().hex[:8]}"

        # 1. Schedule job via coordinator
        with psycopg.connect(role_urls["coordinator"]) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cfi_scheduled_jobs (
                        job_id, training_run_id, simulation_id, coordinator_id,
                        architecture, feature_schema_version, expected_rounds,
                        expected_epochs, input_dim, feature_names_json,
                        permitted_workers_json, config_fingerprint, scheduled_at, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), 'SCHEDULED')
                    """,
                    (
                        job_id, run_id, sim_id, "COORDINATOR",
                        "FraudDetectionNN", "v1.0", 2, 1, NUM_FEATURES,
                        '["f1", "f2"]', '["BANK_A", "BANK_B", "BANK_C"]', "fp_123",
                    ),
                )
            conn.commit()

        valid_completion_sql = """
            INSERT INTO cfi_completed_jobs (
                job_id, training_run_id, simulation_id, completed_at,
                executed_rounds, executed_epochs, sample_count,
                observed_metrics_json, artifact_sha256, architecture,
                feature_schema_version, input_dim, feature_names_json,
                worker_id, status, git_commit_hash, dataset_manifest_id,
                evidence_fingerprint
            ) VALUES (
                %s, %s, %s, '2026-10-12T00:00:00',
                2, 1, 100, '{}', 'sha256_legit_artifact', 'FraudDetectionNN',
                'v1.0', %s, '["f1", "f2"]', 'COMPLETION_AUTHORITY', 'COMPLETED',
                NULL, NULL, 'fp_legit_sql05'
            )
        """
        with psycopg.connect(role_urls["completion"]) as conn:
            with conn.cursor() as cur:
                cur.execute(valid_completion_sql, (job_id, run_id, sim_id, NUM_FEATURES))
            conn.commit()

        # 3. Verify record was persisted and is readable
        with psycopg.connect(role_urls["coordinator"]) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, worker_id FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                assert row is not None
                assert row[0] == "COMPLETED"
                assert row[1] == "COMPLETION_AUTHORITY"

    @pytest.mark.skipif(os.name != "nt", reason="Production coordinator isolation requires Windows Restricted Token (EXEC-06)")
    def test_sql_06_rejected_coordinator_attempt_leaves_zero_forged_rows(
        self, real_pg_instance: str
    ) -> None:
        """SQL-06: Rejected coordinator attempt leaves zero forged completed rows and cannot produce trusted downstream provenance."""
        role_urls = provision_runtime_service_roles(real_pg_instance, app_env="production")
        job_id = f"job_sql06_{uuid.uuid4().hex[:8]}"
        run_id = f"run_sql06_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_sql06_{uuid.uuid4().hex[:8]}"

        with psycopg.connect(role_urls["coordinator"]) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cfi_scheduled_jobs (
                        job_id, training_run_id, simulation_id, coordinator_id,
                        architecture, feature_schema_version, expected_rounds,
                        expected_epochs, input_dim, feature_names_json,
                        permitted_workers_json, config_fingerprint, scheduled_at, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), 'SCHEDULED')
                    """,
                    (
                        job_id, run_id, sim_id, "COORDINATOR",
                        "FraudDetectionNN", "v1.0", 2, 1, NUM_FEATURES,
                        '["f1", "f2"]', '["BANK_A", "BANK_B", "BANK_C"]', "fp_123",
                    ),
                )
            conn.commit()

        valid_completion_sql = f"""
            INSERT INTO cfi_completed_jobs (
                job_id, training_run_id, simulation_id, completed_at,
                executed_rounds, executed_epochs, sample_count,
                observed_metrics_json, artifact_sha256, architecture,
                feature_schema_version, input_dim, feature_names_json,
                worker_id, status, git_commit_hash, dataset_manifest_id,
                evidence_fingerprint
            ) VALUES (
                '{job_id}', '{run_id}', '{sim_id}', '2026-10-12T00:00:00',
                2, 1, 100, '{{}}', 'sha256_forged_artifact', 'FraudDetectionNN',
                'v1.0', {NUM_FEATURES}, '["f1", "f2"]', 'COORDINATOR', 'COMPLETED',
                NULL, NULL, 'fp_forged_sql06'
            )
        """
        proc, client = spawn_isolated_coordinator_process(app_env="production")
        try:
            sql_res = client.attempt_direct_sql(role_urls["coordinator"], valid_completion_sql)
            assert sql_res.get("status") == "DENIED"
            assert sql_res.get("sqlstate") == "42501"

            # Verify PostgreSQL state: exactly 0 rows persisted
            with psycopg.connect(role_urls["coordinator"]) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                    row = cur.fetchone()
                    assert row is not None
                    count = row[0]
                    assert count == 0, f"Expected 0 completed rows, found {count}"

            # Verify downstream provenance query finds zero evidence
            with psycopg.connect(role_urls["coordinator"]) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                    record = cur.fetchone()
                    assert record is None, "Downstream provenance query must return None for unauthorized coordinator write"
        finally:
            client.terminate()
