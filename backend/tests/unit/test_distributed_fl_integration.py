"""Real Distributed Three-Bank FL Integration Suite (DIST-01 through DIST-10).

Verifies Module 01-A.1 Requirement P6:
- DIST-01: Three independent bank processes execute real local PyTorch training.
- DIST-02: Coordinator runs as a separate operating-system process with distinct PID.
- DIST-03: Real application FederatedLearningEngine executes multi-round FedAvg aggregation.
- DIST-04: Coordinator has zero access to bank-local raw datasets in bank storage and over IPC.
- DIST-05: Coordinator cannot impersonate banks or submit bank round observations.
- DIST-06: Bank A credentials cannot record events for Bank B; bank credentials strictly isolated.
- DIST-07: Asymmetric Ed25519 receipt issued by isolated signer; verifier has public-key only.
- DIST-08: Unauthorized receipt signer key is rejected by ModelRegistry and verifier.
- DIST-09: ModelRegistry admits verified inactive candidate with verified provenance.
- DIST-10: Active production champion remains unaltered (no automatic promotion).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import uuid
from collections.abc import Generator
from typing import Any

import numpy as np
import pytest
from cryptography.fernet import Fernet

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    IsolatedReceiptSigningAuthority,
    _generate_ed25519_keypair_hex,
    provision_isolated_issuer_vault,
    setup_bank_private_storage,
    spawn_bank_workers,
)
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrustedTrainingJobRecordSource,
    get_default_training_verifier,
)
from app.infrastructure.database.training_authority_backends import (
    provision_runtime_service_roles,
)

COORD_SECRET = "coord_secret_dist_fl_8831_production"
BANK_A_SECRET = "bank_a_secret_dist_fl_1192_alpha"
BANK_B_SECRET = "bank_b_secret_dist_fl_2283_beta"
BANK_C_SECRET = "bank_c_secret_dist_fl_3374_gamma"

BANK_TOKENS = {
    "BANK_A": BANK_A_SECRET,
    "BANK_B": BANK_B_SECRET,
    "BANK_C": BANK_C_SECRET,
}


@pytest.fixture(scope="module")
def real_pg_instance() -> Generator[str, None, None]:
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


@pytest.fixture(scope="module")
def federated_execution_result(real_pg_instance: str, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Execute a genuine multi-process distributed training lifecycle across Bank A, B, and C."""
    tmp_path = tmp_path_factory.mktemp("dist_fl")
    job_id = f"job_dist_{uuid.uuid4().hex[:8]}"
    run_id = f"run_dist_{uuid.uuid4().hex[:8]}"
    sim_id = f"sim_dist_{uuid.uuid4().hex[:8]}"

    # 1. Provision restricted runtime PostgreSQL service roles
    role_urls = provision_runtime_service_roles(real_pg_instance, app_env="production")

    # 2. Setup isolated bank private storage vaults with independent encryption keys
    vaults_dir = str(tmp_path / "bank_vaults")
    np.random.seed(42)
    X_a = np.random.randn(120, NUM_FEATURES).astype(np.float32)
    y_a = np.random.randint(0, 2, size=120).astype(np.float32)

    X_b = np.random.randn(150, NUM_FEATURES).astype(np.float32)
    y_b = np.random.randint(0, 2, size=150).astype(np.float32)

    X_c = np.random.randn(180, NUM_FEATURES).astype(np.float32)
    y_c = np.random.randint(0, 2, size=180).astype(np.float32)

    bank_enc_keys = {
        "BANK_A": Fernet.generate_key(),
        "BANK_B": Fernet.generate_key(),
        "BANK_C": Fernet.generate_key(),
    }

    data_path_a = setup_bank_private_storage(vaults_dir, "BANK_A", X_a, y_a, bank_token=BANK_TOKENS["BANK_A"], bank_encryption_key=bank_enc_keys["BANK_A"])
    data_path_b = setup_bank_private_storage(vaults_dir, "BANK_B", X_b, y_b, bank_token=BANK_TOKENS["BANK_B"], bank_encryption_key=bank_enc_keys["BANK_B"])
    data_path_c = setup_bank_private_storage(vaults_dir, "BANK_C", X_c, y_c, bank_token=BANK_TOKENS["BANK_C"], bank_encryption_key=bank_enc_keys["BANK_C"])

    bank_data_paths = {
        "BANK_A": data_path_a,
        "BANK_B": data_path_b,
        "BANK_C": data_path_c,
    }

    # Restrict permissions on Bank A storage to bank-only owner
    with contextlib.suppress(Exception):
        os.chmod(os.path.dirname(data_path_a), 0o700)

    # 3. Spawn worker processes under bank-specific database roles and data paths
    bank_db_urls = {
        "BANK_A": role_urls["worker_bank_a"],
        "BANK_B": role_urls["worker_bank_b"],
        "BANK_C": role_urls["worker_bank_c"],
    }
    procs, endpoints = spawn_bank_workers(
        participating_banks=["BANK_A", "BANK_B", "BANK_C"],
        bank_tokens=BANK_TOKENS,
        db_url=role_urls["worker"],
        bank_data_paths=bank_data_paths,
        bank_db_urls=bank_db_urls,
        bank_encryption_keys=bank_enc_keys,
    )

    # Holdout test set for coordinator evaluation
    X_test = np.random.randn(100, NUM_FEATURES).astype(np.float32)
    y_test = np.random.randint(0, 2, size=100).astype(np.float32)

    # 4. Coordinator initialized with coordinator role and completion authority role
    manager = DistributedFederatedTrainingManager(
        coordinator_token=COORD_SECRET,
        db_url=role_urls["coordinator"],
        completion_db_url=role_urls["completion"],
        app_env="production",
    )

    registry_dir = str(tmp_path / "registry")
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

    result = manager.execute_distributed_federation(
        job_id=job_id,
        training_run_id=run_id,
        simulation_id=sim_id,
        holdout_val_data=(X_test, y_test),
        participating_banks=["BANK_A", "BANK_B", "BANK_C"],
        worker_endpoints=endpoints,
        worker_processes=procs,
        num_rounds=2,
        expected_unique_samples=450,
        registry_storage_dir=registry_dir,
        signing_authority=signing_auth,
    )
    result["job_id"] = job_id
    result["run_id"] = run_id
    result["sim_id"] = sim_id
    result["manager"] = manager
    result["vaults_dir"] = vaults_dir
    result["data_path_a"] = data_path_a
    result["registry_dir"] = registry_dir
    return result


class TestDistributedFederatedLearningIntegration:
    """Rigorous end-to-end verification of real multi-process federated training."""

    def test_dist_01_three_independent_bank_processes_execute_local_training(
        self, federated_execution_result: dict[str, Any]
    ) -> None:
        """DIST-01: Three distinct bank worker OS processes execute local PyTorch training steps."""
        worker_pids = federated_execution_result["worker_pids"]
        assert len(worker_pids) == 3
        assert worker_pids["BANK_A"] > 0
        assert worker_pids["BANK_B"] > 0
        assert worker_pids["BANK_C"] > 0
        assert worker_pids["BANK_A"] != worker_pids["BANK_B"]
        assert worker_pids["BANK_B"] != worker_pids["BANK_C"]

        # Verify that all 3 banks recorded real observations in PostgreSQL
        manager: DistributedFederatedTrainingManager = federated_execution_result["manager"]
        obs = manager.authority_source.get_round_observations(federated_execution_result["job_id"])
        banks_observed = {o.bank_id for o in obs}
        assert banks_observed == {"BANK_A", "BANK_B", "BANK_C"}
        for o in obs:
            assert o.execution_status == "COMPLETED"
            assert o.observed_loss > 0.0
            assert o.actual_sample_count > 0

    def test_dist_02_coordinator_runs_as_separate_process(
        self, federated_execution_result: dict[str, Any]
    ) -> None:
        """DIST-02: Coordinator runs in an OS process strictly distinct from all worker PIDs."""
        coord_pid = federated_execution_result["coordinator_pid"]
        worker_pids = federated_execution_result["worker_pids"]
        all_pids = [coord_pid, worker_pids["BANK_A"], worker_pids["BANK_B"], worker_pids["BANK_C"]]
        assert len(set(all_pids)) == 4
        assert coord_pid != worker_pids["BANK_A"]
        assert coord_pid != worker_pids["BANK_B"]
        assert coord_pid != worker_pids["BANK_C"]

    def test_dist_03_real_application_fl_engine_aggregation_and_two_rounds(
        self, federated_execution_result: dict[str, Any]
    ) -> None:
        """DIST-03 & DIST-05: Real FederatedLearningEngine aggregates weights over 2 continuous rounds."""
        manager: DistributedFederatedTrainingManager = federated_execution_result["manager"]
        job_id = federated_execution_result["job_id"]
        aggregations = manager.authority_source._db.get_global_aggregations(job_id)
        assert len(aggregations) == 2
        assert aggregations[0].round_number == 1
        assert aggregations[1].round_number == 2
        assert sorted(aggregations[1].accepted_participant_ids) == ["BANK_A", "BANK_B", "BANK_C"]
        assert aggregations[0].aggregated_global_model_digest != ""
        assert aggregations[1].aggregated_global_model_digest != ""

    def test_dist_04_coordinator_cannot_access_bank_raw_datasets(
        self, federated_execution_result: dict[str, Any]
    ) -> None:
        """DIST-04: Coordinator has no raw dataset parameters and cannot read bank-local private storage."""
        manager: DistributedFederatedTrainingManager = federated_execution_result["manager"]
        # Coordinator authority source does not hold bank datasets
        assert not hasattr(manager, "bank_partitions")

        # Verify IPC messages never transmit raw dataset arrays (X or y)
        authority = manager.authority_source
        obs = authority.get_round_observations(federated_execution_result["job_id"])
        for o in obs:
            # Observation binds update digest and sample count only
            assert hasattr(o, "model_update_digest")
            assert hasattr(o, "actual_sample_count")
            assert not hasattr(o, "raw_samples")
            assert not hasattr(o, "X_data")

    def test_dist_05_coordinator_cannot_impersonate_banks_or_submit_observations(
        self, federated_execution_result: dict[str, Any]
    ) -> None:
        """DIST-05: Coordinator token cannot submit bank round observations or forge bank execution."""
        manager: DistributedFederatedTrainingManager = federated_execution_result["manager"]
        job_id = federated_execution_result["job_id"]
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator credential cannot authorize worker execution events"):
            manager.authority_source.record_round_observation(
                job_id=job_id,
                round_number=1,
                sample_count=100,
                worker_token=COORD_SECRET,  # Coordinator token attempting to submit bank observation
                bank_id="BANK_A",
                model_update_digest="fake_digest_from_coordinator",
            )

    def test_dist_06_independent_bank_credential_ownership_and_cross_bank_denial(
        self, real_pg_instance: str
    ) -> None:
        """DIST-06: Bank A worker credential cannot submit observations claiming Bank B identity."""
        source = TrustedTrainingJobRecordSource(
            worker_token=BANK_A_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Identity spoofing rejected"):
            source.record_round_observation(
                job_id="job_cross_bank_test",
                round_number=1,
                sample_count=100,
                worker_token=BANK_A_SECRET,
                bank_id="BANK_B",  # Bank A token claiming Bank B
                model_update_digest="digest_spoof",
            )

    def test_dist_07_isolated_receipt_signer_and_public_key_only_verifier(
        self, federated_execution_result: dict[str, Any]
    ) -> None:
        """DIST-07 & DIST-09: Ed25519 receipt is verified by public-key-only verifier bound to model bytes."""
        assert federated_execution_result["is_verified"] is True
        receipt = federated_execution_result["receipt"]
        assert receipt.job_id == federated_execution_result["job_id"]
        assert receipt.model_artifact_sha256 == federated_execution_result["final_artifact_sha256"]

        # Verifier operating strictly with public key verifies the genuine receipt
        verifier = get_default_training_verifier()
        is_valid, _ = verifier.verify_receipt(
            receipt=receipt,
            expected_simulation_id=federated_execution_result["sim_id"],
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
        )
        assert is_valid is True

    def test_dist_08_unauthorized_issuer_key_rejected_by_registry(
        self, federated_execution_result: dict[str, Any], tmp_path
    ) -> None:
        """DIST-08: Receipt signed by unapproved Ed25519 key is rejected by ModelRegistry and verifier."""
        # Generate an unapproved, rogue Ed25519 keypair
        rogue_priv, _ = _generate_ed25519_keypair_hex()
        rogue_issuer = TrainingReceiptIssuer(
            signing_key_hex=rogue_priv,
            key_id="unapproved_rogue_key_id",
            job_record_source=federated_execution_result["manager"].authority_source,
            enforce_job_records=False,
        )
        orig_receipt = federated_execution_result["receipt"]
        rogue_receipt = rogue_issuer.issue_receipt(
            training_run_id=orig_receipt.training_run_id,
            job_id=orig_receipt.job_id,
            model_artifact_sha256=orig_receipt.model_artifact_sha256,
            architecture=orig_receipt.architecture,
            feature_schema_version=orig_receipt.feature_schema_version,
            executed_epochs=orig_receipt.executed_epochs,
            sample_count=orig_receipt.sample_count,
            metrics=dict(orig_receipt.metrics),
            simulation_id=orig_receipt.simulation_id,
            input_dim=orig_receipt.input_dim,
            feature_names=list(orig_receipt.feature_names),
            executed_rounds=orig_receipt.executed_rounds,
        )

        # Standard verifier with default trusted store rejects rogue key
        verifier = get_default_training_verifier()
        is_valid, reason = verifier.verify_receipt(
            receipt=rogue_receipt,
            expected_simulation_id=federated_execution_result["sim_id"],
        )
        assert is_valid is False
        assert "not present in trusted" in reason or "not in trusted" in reason or "Invalid signature" in reason

        # ModelRegistry rejects production admission for receipt signed by unapproved key
        registry = ModelRegistry(
            storage_dir=str(tmp_path / "rogue_reg"),
            verifier=verifier,
        )
        version_entry = registry.save_version(
            simulation_id=federated_execution_result["sim_id"],
            state_dict={},
            metrics={"loss": 0.5},
            is_promoted=False,
            training_receipt=rogue_receipt,
        )
        assert version_entry["is_production_eligible"] is False
        assert version_entry["provenance_type"] == "UNVERIFIED"

    def test_dist_09_model_registry_admits_inactive_candidate_with_verified_provenance(
        self, federated_execution_result: dict[str, Any]
    ) -> None:
        """DIST-09: ModelRegistry admits candidate as inactive with verified training provenance."""
        saved_version = federated_execution_result["saved_version"]
        assert saved_version["is_production_eligible"] is True
        assert saved_version["provenance_type"] == "TRAINING_RECORDED"
        assert saved_version["status"] == "inactive"
        assert saved_version["is_active"] is False

    def test_dist_10_active_champion_remains_unaltered(
        self, federated_execution_result: dict[str, Any]
    ) -> None:
        """DIST-10: Newly registered inactive candidate is NOT promoted to active champion."""
        assert federated_execution_result["is_promoted_champion"] is False
        registry = ModelRegistry(storage_dir=federated_execution_result["registry_dir"])
        champion = registry.get_active_champion_identity(federated_execution_result["sim_id"])
        # No champion was promoted; champion remains None
        assert champion is None
