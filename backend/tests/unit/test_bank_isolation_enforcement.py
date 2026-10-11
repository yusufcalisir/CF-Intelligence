"""Bank Workload Isolation and Access Denial Suite (BI-01 to BI-10).

Verifies the physical and cryptographic boundaries separating financial institutions:
- BI-01: Production coordinator attempts to supply bank_partitions -> rejected.
- BI-02: Production coordinator attempts to supply bank_tokens -> rejected.
- BI-03: Production coordinator attempts to supply bank_data_paths -> rejected.
- BI-04: Coordinator attempts automatic bank credential generation -> rejected.
- BI-05: Coordinator attempts to impersonate Bank A -> rejected.
- BI-06: Bank A attempts to impersonate Bank B -> rejected.
- BI-07: Unauthorized worker attempts to participate in another bank's job -> rejected.
- BI-08: Coordinator attempts to read Bank A's private training data -> native OS PermissionError.
- BI-09: Coordinator attempts to obtain bank private credentials -> credentials unexposed.
- BI-10: Coordinator attempts to generate valid bank observation without authenticated execution -> rejected.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

import numpy as np
import pytest

from app.application.services.distributed_fl_service import (
    BankPrivateStorageVault,
    DistributedFederatedTrainingManager,
    setup_bank_private_storage,
)
from app.application.services.model_service import NUM_FEATURES
from app.application.services.training_provenance_service import (
    TrainingProvenanceSecurityError,
    TrustedTrainingJobRecordSource,
)

COORD_SECRET = "coord_secret_bi_attacks_production_991"
BANK_A_SECRET = "bank_a_secret_bi_attacks_alpha_112"
BANK_B_SECRET = "bank_b_secret_bi_attacks_beta_223"
BANK_C_SECRET = "bank_c_secret_bi_attacks_gamma_334"

BANK_TOKENS = {
    "BANK_A": BANK_A_SECRET,
    "BANK_B": BANK_B_SECRET,
    "BANK_C": BANK_C_SECRET,
}


@pytest.fixture(scope="module")
def real_pg_instance() -> Generator[str, None, None]:
    """Provide embedded real PostgreSQL 18.1 instance."""
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


class TestBankIsolationEnforcement:
    """Rigorous verification of institutional privacy and workload isolation."""

    def test_bi_01_coordinator_receiving_bank_partitions_rejected_in_production(
        self, real_pg_instance: str, tmp_path
    ) -> None:
        """BI-01: Production coordinator cannot receive raw bank training partitions."""
        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )
        fake_partitions: dict[str, Any] = {
            "BANK_A": (np.zeros((10, NUM_FEATURES), dtype=np.float32), np.zeros(10, dtype=np.float32))
        }

        with pytest.raises(TrainingProvenanceSecurityError, match="Production coordinator cannot receive bank private dataset partitions"):
            manager.execute_distributed_federation(
                job_id="job_bi01",
                training_run_id="run_bi01",
                simulation_id="sim_bi01",
                holdout_val_data=(np.zeros((5, NUM_FEATURES)), np.zeros(5)),
                bank_partitions=fake_partitions,
            )

    def test_bi_02_coordinator_receiving_bank_tokens_rejected_in_production(
        self, real_pg_instance: str
    ) -> None:
        """BI-02: Production coordinator cannot receive bank private credentials."""
        # 1. Constructor injection rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="Production coordinator cannot receive bank private credentials"):
            DistributedFederatedTrainingManager(
                coordinator_token=COORD_SECRET,
                db_url=real_pg_instance,
                app_env="production",
                bank_tokens=BANK_TOKENS,
            )

        # 2. Execution-time injection rejected
        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Production coordinator cannot receive bank private credentials"):
            manager.execute_distributed_federation(
                job_id="job_bi02",
                training_run_id="run_bi02",
                simulation_id="sim_bi02",
                holdout_val_data=(np.zeros((5, NUM_FEATURES)), np.zeros(5)),
                bank_tokens=BANK_TOKENS,
            )

    def test_bi_03_coordinator_receiving_bank_data_paths_rejected_in_production(
        self, real_pg_instance: str
    ) -> None:
        """BI-03: Production coordinator cannot receive bank private data paths."""
        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Production coordinator cannot receive bank private data paths"):
            manager.execute_distributed_federation(
                job_id="job_bi03",
                training_run_id="run_bi03",
                simulation_id="sim_bi03",
                holdout_val_data=(np.zeros((5, NUM_FEATURES)), np.zeros(5)),
                bank_data_paths={"BANK_A": "/vaults/BANK_A/private_dataset.npz"},
            )

    def test_bi_04_coordinator_auto_token_generation_rejected_in_production(
        self, real_pg_instance: str
    ) -> None:
        """BI-04: Coordinator cannot automatically generate bank credentials or spawn workers with generated secrets."""
        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Production coordinator cannot generate bank credentials"):
            manager.execute_distributed_federation(
                job_id="job_bi04",
                training_run_id="run_bi04",
                simulation_id="sim_bi04",
                holdout_val_data=(np.zeros((5, NUM_FEATURES)), np.zeros(5)),
                worker_endpoints=None,  # Triggering auto-fallback
            )

    def test_bi_05_coordinator_attempts_bank_impersonation_rejected(
        self, real_pg_instance: str
    ) -> None:
        """BI-05: Coordinator token cannot submit bank round observations."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator credential cannot authorize worker execution events"):
            source.record_round_observation(
                job_id="job_bi05",
                round_number=1,
                sample_count=100,
                worker_token=COORD_SECRET,
                bank_id="BANK_A",
                model_update_digest="digest_coord_fake",
            )

    def test_bi_06_bank_a_attempts_bank_b_impersonation_rejected(
        self, real_pg_instance: str
    ) -> None:
        """BI-06: Bank A worker credential cannot submit observations claiming Bank B identity."""
        source = TrustedTrainingJobRecordSource(
            worker_token=BANK_A_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Identity spoofing rejected"):
            source.record_round_observation(
                job_id="job_bi06",
                round_number=1,
                sample_count=100,
                worker_token=BANK_A_SECRET,
                bank_id="BANK_B",
                model_update_digest="digest_spoof_a_to_b",
            )

    def test_bi_07_unauthorized_worker_cannot_participate_in_another_job(
        self, real_pg_instance: str
    ) -> None:
        """BI-07: Bank worker not listed in permitted_workers is rejected from starting or observing job."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_bi07_{uuid.uuid4().hex[:8]}"
        run_id = f"run_bi07_{uuid.uuid4().hex[:8]}"

        # Schedule job permitting ONLY BANK_A and BANK_B
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_bi07",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A", "BANK_B"],
        )

        # Bank C attempts to record observation
        source_c = TrustedTrainingJobRecordSource(
            worker_token=BANK_C_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="is not authorized to record observations|not in permitted workers"):
            source_c.record_round_observation(
                job_id=job_id,
                round_number=1,
                sample_count=50,
                worker_token=BANK_C_SECRET,
                bank_id="BANK_C",
                model_update_digest="update_c_unauth",
            )

    def test_bi_08_coordinator_attempt_to_read_bank_data_denied_by_os(
        self, tmp_path
    ) -> None:
        """BI-08: Actual access attempt to Bank A's private vault from coordinator context raises PermissionError."""
        vault_dir = str(tmp_path / "bank_vaults")
        X_mock = np.random.randn(20, NUM_FEATURES).astype(np.float32)
        y_mock = np.random.randint(0, 2, 20).astype(np.float32)

        data_path_a = setup_bank_private_storage(
            bank_storage_dir=vault_dir,
            bank_id="BANK_A",
            X_partition=X_mock,
            y_partition=y_mock,
            bank_token=BANK_A_SECRET,
            lock_boundary=True,
        )

        # Coordinator or external process attempts to read Bank A's raw dataset directly
        # Native OS kernel lock denies access with PermissionError
        with pytest.raises(PermissionError) as exc_info:
            with open(data_path_a, "rb") as f:
                f.read()

        assert "Permission denied" in str(exc_info.value) or isinstance(exc_info.value, PermissionError)

        # Attempting logical read via vault with coordinator identity is also denied
        vault = BankPrivateStorageVault("BANK_A", vault_dir, bank_token=BANK_A_SECRET)
        with pytest.raises(PermissionError, match="Access denied: Caller 'COORDINATOR' is forbidden"):
            vault.read_dataset_as_bank(caller_identity="COORDINATOR", caller_token=COORD_SECRET)

        # Bank B worker attempting to access Bank A's vault is also denied
        with pytest.raises(PermissionError, match="Access denied: Caller 'BANK_B' is forbidden"):
            vault.read_dataset_as_bank(caller_identity="BANK_B", caller_token=BANK_B_SECRET)

        # Clean up lock
        vault.release_exclusive_os_boundary()

    def test_bi_09_coordinator_does_not_possess_bank_credentials(
        self, real_pg_instance: str
    ) -> None:
        """BI-09: Coordinator domain holds only coordinator_token and has zero bank private credentials."""
        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )
        assert manager.coordinator_token == COORD_SECRET
        assert not hasattr(manager, "bank_tokens")
        assert not hasattr(manager.authority_source, "bank_tokens") or manager.authority_source.bank_tokens is None

    def test_bi_10_coordinator_cannot_forge_bank_round_observation(
        self, real_pg_instance: str
    ) -> None:
        """BI-10: Submitting bank round observation requires valid bank HMAC token; coordinator token is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator credential cannot authorize worker execution events"):
            source.record_round_observation(
                job_id="job_bi10",
                round_number=1,
                sample_count=100,
                worker_token=COORD_SECRET,
                bank_id="BANK_C",
                model_update_digest="digest_forged_c",
            )
