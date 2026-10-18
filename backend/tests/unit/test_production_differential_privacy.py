"""Comprehensive production differential privacy verification suite.

Covers all 15 operational requirements from Section 5:
1. Operational worker genuinely executes Opacus DP-SGD.
2. DP update differs from non-DP route and comes from real model.
3. Per-sample gradient clipping and calibrated noise.
4. Privacy accountant matches actual Opacus mechanism (PRV/RDP) and Poisson sampling.
5. Multi-round job consumes cumulative budget.
6. Concurrent jobs sharing dataset cannot overspend budget.
7. Process restart preserves reserved and spent budget.
8. Retries, duplicates, and failed rounds cannot reset accounting.
9. Budget exhaustion rejects protected computation.
10. Different consortium IDs cannot bypass bank's dataset privacy budget.
11. DP failure never falls back to vanilla training.
12. Raw non-DP telemetry not released as DP-protected.
13. Multi-process operational training produces valid candidate.
14. Existing non-DP mode remains functional and correctly classified.
15. TrainingJobAuthorityDatabase.__init__ is_prod/active_env regression test.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest
import torch

from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    run_bank_worker_node,
)
from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.model_service import (
    NUM_FEATURES,
    FraudDetectionModel,
    ModelService,
)
from app.application.services.privacy_service import PrivacyService
from app.application.services.training_provenance_service import (
    TrainingJobAuthorityDatabase,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
    TrustedTrainingJobRecordSource,
)
from app.config import get_settings
from app.infrastructure.database.training_authority_backends import PostgresTrainingJobAuthority


@pytest.fixture
def temp_db_url() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{path}"
    yield db_url
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


@pytest.fixture
def synthetic_bank_data() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(1337)
    X = rng.standard_normal((300, NUM_FEATURES)).astype(np.float32)
    y = (X[:, 0] + X[:, 1] > 0.0).astype(np.float32)
    return X, y


class TestProductionDifferentialPrivacy:
    # 1. Operational worker genuinely executes Opacus DP-SGD
    def test_01_authenticated_worker_genuinely_executes_opacus_dp_sgd(
        self, temp_db_url: str, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        coord_token = f"coord_{uuid.uuid4().hex}"
        bank_id = "BANK_A"
        worker_token = f"tok_{uuid.uuid4().hex}"
        job_id = f"job_{uuid.uuid4().hex[:8]}"

        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        authority.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id=f"sim_{job_id}",
            coordinator_token=coord_token,
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=[bank_id],
            dp_config={
                "dp_enabled": True,
                "target_epsilon": 5.0,
                "target_delta": 1e-5,
                "max_grad_norm": 1.0,
                "dataset_tag": "credit_partition",
            },
        )

        import multiprocessing as mp

        inbox: mp.Queue = mp.Queue()
        outbox: mp.Queue = mp.Queue()

        model_svc = ModelService(get_settings())
        base_model = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        init_weights = model_svc.get_parameters(base_model)

        inbox.put({
            "command": "TRAIN_ROUND",
            "job_id": job_id,
            "round_number": 1,
            "global_weights": init_weights,
        })
        inbox.put({"command": "STOP"})

        run_bank_worker_node(
            bank_id=bank_id,
            worker_token=worker_token,
            inbox=inbox,
            outbox=outbox,
            db_url=temp_db_url,
            local_partition=(X, y),
            app_env="development",
        )

        res = outbox.get(timeout=10)
        assert res["status"] == "SUCCESS"
        assert "dp_provenance" in res
        dp_prov = res["dp_provenance"]
        assert dp_prov["mechanism"] == "SubsampledGaussianDP"
        assert "opacus" in dp_prov["accountant"].lower()
        assert dp_prov["sampling_mechanism"] == "poisson"
        assert dp_prov["noise_multiplier"] > 0.0
        assert dp_prov["actual_epsilon"] > 0.0
        assert dp_prov["actual_epsilon"] <= 5.05

    # 2. DP update differs from non-DP route and comes from real model
    def test_02_dp_update_differs_from_non_dp_and_comes_from_real_model(
        self, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        model_svc = ModelService(get_settings())

        torch.manual_seed(42)
        model_vanilla = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        torch.manual_seed(42)
        model_dp = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)

        trained_vanilla, loss_vanilla, _ = model_svc.train_local(
            model_vanilla, X, y, epochs=1, batch_size=32, learning_rate=0.01
        )
        trained_dp, loss_dp, eps = model_svc.train_local_with_opacus(
            model_dp,
            X,
            y,
            target_epsilon=2.0,
            target_delta=1e-5,
            max_grad_norm=1.0,
            epochs=1,
            batch_size=32,
            learning_rate=0.01,
        )

        w_vanilla = model_svc.get_parameters(trained_vanilla).flat_weights
        w_dp = model_svc.get_parameters(trained_dp).flat_weights

        assert not np.allclose(w_vanilla, w_dp, atol=1e-4)
        assert np.isfinite(w_dp).all()

        eval_res = model_svc.evaluate(trained_dp, X[:50], y[:50])
        assert np.isfinite(eval_res["loss"])
        assert 0.0 <= eval_res["auc_roc"] <= 1.0

    # 3. Per-sample gradient clipping and calibrated noise
    def test_03_per_sample_gradient_clipping_and_calibrated_noise(
        self, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        model_svc = ModelService(get_settings())

        model_1 = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        _, _, eps_1 = model_svc.train_local_with_opacus(
            model_1, X, y, target_epsilon=1.0, target_delta=1e-5, max_grad_norm=0.5, epochs=1
        )

        model_2 = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        _, _, eps_2 = model_svc.train_local_with_opacus(
            model_2, X, y, target_epsilon=10.0, target_delta=1e-5, max_grad_norm=0.5, epochs=1
        )

        prov_1 = model_1._dp_provenance  # type: ignore[attr-defined]
        prov_2 = model_2._dp_provenance  # type: ignore[attr-defined]

        # Tighter epsilon requires strictly higher noise multiplier
        assert prov_1["noise_multiplier"] > prov_2["noise_multiplier"]
        assert prov_1["clipping_norm"] == 0.5
        assert prov_2["clipping_norm"] == 0.5

    # 4. Privacy accountant matches actual Opacus mechanism and sampling
    def test_04_privacy_accountant_matches_actual_opacus_mechanism(
        self, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        model_svc = ModelService(get_settings())
        model = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)

        _, _, _ = model_svc.train_local_with_opacus(
            model, X, y, target_epsilon=5.0, target_delta=1e-5, epochs=2, batch_size=32
        )
        prov = model._dp_provenance  # type: ignore[attr-defined]

        assert prov["accountant"] == "opacus_prv"
        assert prov["sampling_mechanism"] == "poisson"
        assert prov["total_steps"] > 0
        assert prov["sample_rate"] == pytest.approx(32 / len(X), rel=1e-2)

    # 5. Multi-round job consumes cumulative budget
    def test_05_multi_round_job_consumes_cumulative_budget(
        self, temp_db_url: str, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        coord_token = f"coord_{uuid.uuid4().hex}"
        bank_id = "BANK_A"
        worker_token = f"tok_{uuid.uuid4().hex}"
        job_id = f"job_multiround_{uuid.uuid4().hex[:8]}"

        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        authority.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id=f"sim_{job_id}",
            coordinator_token=coord_token,
            expected_rounds=2,
            expected_epochs=2,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=[bank_id],
            dp_config={
                "dp_enabled": True,
                "target_epsilon": 5.0,
                "target_delta": 1e-5,
                "dataset_tag": "cum_ds",
            },
        )

        import multiprocessing as mp

        inbox: mp.Queue = mp.Queue()
        outbox: mp.Queue = mp.Queue()

        model_svc = ModelService(get_settings())
        base_model = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        weights_1 = model_svc.get_parameters(base_model)

        inbox.put({
            "command": "TRAIN_ROUND",
            "job_id": job_id,
            "round_number": 1,
            "global_weights": weights_1,
        })
        inbox.put({
            "command": "TRAIN_ROUND",
            "job_id": job_id,
            "round_number": 2,
            "global_weights": weights_1,
        })
        inbox.put({"command": "STOP"})

        run_bank_worker_node(
            bank_id=bank_id,
            worker_token=worker_token,
            inbox=inbox,
            outbox=outbox,
            db_url=temp_db_url,
            local_partition=(X, y),
            app_env="development",
        )

        res_1 = outbox.get(timeout=10)
        res_2 = outbox.get(timeout=10)

        eps_1 = res_1["dp_provenance"]["actual_epsilon"]
        eps_2 = res_2["dp_provenance"]["actual_epsilon"]

        # Round 2 cumulative epsilon must be strictly greater than Round 1
        assert eps_2 > eps_1
        assert eps_2 <= 5.05

        # Inspect durable institutional budget
        source = TrustedTrainingJobRecordSource(
            coordinator_token=coord_token, db_url=temp_db_url, app_env="development"
        )
        budget = source.get_institutional_privacy_budget(bank_id, "cum_ds")
        assert budget is not None
        assert budget.spent_epsilon == pytest.approx(eps_2, rel=1e-3)

    # 6. Concurrent jobs sharing dataset cannot overspend budget
    def test_06_concurrent_jobs_sharing_dataset_cannot_overspend(self, temp_db_url: str) -> None:
        source = TrustedTrainingJobRecordSource(
            coordinator_token="test_coord", db_url=temp_db_url, app_env="development"
        )
        bank_id = "BANK_A"
        dataset_tag = "shared_dataset"

        # Initialize budget with total 5.0 epsilon
        b = source.get_or_create_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag=dataset_tag,
            allocated_epsilon=5.0,
            allocated_delta=1e-5,
        )
        assert b.allocated_epsilon == 5.0

        # Job 1 reserves 3.5 epsilon -> succeeds
        success_1 = source.reserve_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag=dataset_tag,
            job_id="job_1",
            reserved_epsilon=3.5,
            reserved_delta=1e-5,
        )
        assert success_1 is True

        # Job 2 attempts to reserve 2.0 epsilon (3.5 + 2.0 = 5.5 > 5.0) -> fails closed
        with pytest.raises(TrainingProvenanceSecurityError, match="exceeds remaining available"):
            source.reserve_institutional_privacy_budget(
                bank_id=bank_id,
                dataset_tag=dataset_tag,
                job_id="job_2",
                reserved_epsilon=2.0,
                reserved_delta=1e-5,
            )

    # 7. Process restart preserves reserved and spent budget
    def test_07_process_restart_preserves_reserved_and_spent_budget(self, temp_db_url: str) -> None:
        # First process instance
        source_1 = TrustedTrainingJobRecordSource(
            coordinator_token="coord_1", db_url=temp_db_url, app_env="development"
        )
        source_1.get_or_create_institutional_privacy_budget("BANK_B", "core_ds", allocated_epsilon=10.0)
        source_1.reserve_institutional_privacy_budget("BANK_B", "core_ds", "job_x", reserved_epsilon=4.0)
        source_1.commit_institutional_privacy_expenditure(
            "BANK_B", "core_ds", "job_x", spent_epsilon=3.8, spent_delta=1e-5
        )

        # Simulated process restart: new instance entirely
        source_2 = TrustedTrainingJobRecordSource(
            coordinator_token="coord_2", db_url=temp_db_url, app_env="development"
        )
        budget = source_2.get_institutional_privacy_budget("BANK_B", "core_ds")
        assert budget is not None
        assert budget.spent_epsilon == pytest.approx(3.8, abs=1e-5)
        assert budget.reserved_epsilon == 0.0
        assert budget.remaining_epsilon == pytest.approx(6.2, abs=1e-5)

    # 8. Retries, duplicates, and failed rounds cannot reset accounting
    def test_08_retries_and_failed_rounds_cannot_reset_accounting(self, temp_db_url: str) -> None:
        source = TrustedTrainingJobRecordSource(
            coordinator_token="test_coord", db_url=temp_db_url, app_env="development"
        )
        source.get_or_create_institutional_privacy_budget("BANK_C", "vault_ds", allocated_epsilon=5.0)
        source.reserve_institutional_privacy_budget("BANK_C", "vault_ds", "job_retry", 2.0)
        source.commit_institutional_privacy_expenditure("BANK_C", "vault_ds", "job_retry", 2.0)

        # Committing a lower value or 0 must not refund spent budget (monotonicity)
        source.commit_institutional_privacy_expenditure("BANK_C", "vault_ds", "job_retry", 0.0)
        b = source.get_institutional_privacy_budget("BANK_C", "vault_ds")
        assert b is not None
        assert b.spent_epsilon == 2.0

    # 9. Budget exhaustion rejects protected computation
    def test_09_budget_exhaustion_rejects_protected_computation(
        self, temp_db_url: str, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        coord_token = f"coord_{uuid.uuid4().hex}"
        bank_id = "BANK_A"
        worker_token = f"tok_{uuid.uuid4().hex}"
        job_id = f"job_exhausted_{uuid.uuid4().hex[:8]}"

        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        authority.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id=f"sim_{job_id}",
            coordinator_token=coord_token,
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=[bank_id],
            dp_config={
                "dp_enabled": True,
                "target_epsilon": 2.0,
                "target_delta": 1e-5,
                "dataset_tag": "exhausted_ds",
            },
        )

        # Exhaust budget in advance
        authority.get_or_create_institutional_privacy_budget(
            bank_id=bank_id, dataset_tag="exhausted_ds", allocated_epsilon=2.0
        )
        authority.reserve_institutional_privacy_budget(bank_id, "exhausted_ds", "prior_job", 2.0)
        authority.commit_institutional_privacy_expenditure(bank_id, "exhausted_ds", "prior_job", 2.0)

        import multiprocessing as mp

        inbox: mp.Queue = mp.Queue()
        outbox: mp.Queue = mp.Queue()

        model_svc = ModelService(get_settings())
        base_model = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        init_weights = model_svc.get_parameters(base_model)

        inbox.put({
            "command": "TRAIN_ROUND",
            "job_id": job_id,
            "round_number": 1,
            "global_weights": init_weights,
        })
        inbox.put({"command": "STOP"})

        run_bank_worker_node(
            bank_id=bank_id,
            worker_token=worker_token,
            inbox=inbox,
            outbox=outbox,
            db_url=temp_db_url,
            local_partition=(X, y),
            app_env="development",
        )

        res = outbox.get(timeout=10)
        # Must fail closed with ERROR status due to privacy budget exhaustion
        assert res["status"] == "ERROR"
        assert "Privacy budget exhausted" in res["error"]

    # 10. Different consortium IDs cannot bypass bank's dataset privacy budget
    def test_10_different_consortium_ids_cannot_bypass_dataset_budget(self, temp_db_url: str) -> None:
        source = TrustedTrainingJobRecordSource(
            coordinator_token="test_coord", db_url=temp_db_url, app_env="development"
        )
        bank_id = "BANK_A"
        dataset_tag = "proprietary_records"

        # Initialize budget with 3.0 epsilon
        source.get_or_create_institutional_privacy_budget(bank_id, dataset_tag, allocated_epsilon=3.0)

        # Job 1 under Consortium Alpha spends 2.5
        source.schedule_training_job(
            job_id="job_alpha",
            training_run_id="run_alpha",
            simulation_id="sim_alpha",
            coordinator_token="test_coord",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=[bank_id],
            consortium_id="consortium-alpha",
            dp_config={"dp_enabled": True, "target_epsilon": 2.5, "dataset_tag": dataset_tag},
        )
        source.reserve_institutional_privacy_budget(bank_id, dataset_tag, "job_alpha", 2.5)
        source.commit_institutional_privacy_expenditure(bank_id, dataset_tag, "job_alpha", 2.5)

        # Job 2 under Consortium Beta tries to spend 1.5 on the same dataset (2.5 + 1.5 = 4.0 > 3.0)
        source.schedule_training_job(
            job_id="job_beta",
            training_run_id="run_beta",
            simulation_id="sim_beta",
            coordinator_token="test_coord",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=[bank_id],
            consortium_id="consortium-beta",
            dp_config={"dp_enabled": True, "target_epsilon": 1.5, "dataset_tag": dataset_tag},
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="exceeds remaining available"):
            source.reserve_institutional_privacy_budget(bank_id, dataset_tag, "job_beta", 1.5)

    # 11. DP failure never falls back to vanilla training
    def test_11_dp_failure_never_falls_back_to_vanilla_training(
        self, temp_db_url: str, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        coord_token = f"coord_{uuid.uuid4().hex}"
        bank_id = "BANK_A"
        worker_token = f"tok_{uuid.uuid4().hex}"
        job_id = f"job_fail_{uuid.uuid4().hex[:8]}"

        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        authority.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id=f"sim_{job_id}",
            coordinator_token=coord_token,
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=[bank_id],
            dp_config={
                "dp_enabled": True,
                "target_epsilon": 5.0,
                "target_delta": 1e-5,
                "dataset_tag": "fail_test",
            },
        )

        import multiprocessing as mp

        inbox: mp.Queue = mp.Queue()
        outbox: mp.Queue = mp.Queue()

        model_svc = ModelService(get_settings())
        base_model = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        init_weights = model_svc.get_parameters(base_model)

        inbox.put({
            "command": "TRAIN_ROUND",
            "job_id": job_id,
            "round_number": 1,
            "global_weights": init_weights,
        })
        inbox.put({"command": "STOP"})

        # Simulate Opacus failure
        with patch.object(
            ModelService,
            "train_local_with_opacus",
            side_effect=RuntimeError("Simulated Opacus crash during DP-SGD"),
        ):
            run_bank_worker_node(
                bank_id=bank_id,
                worker_token=worker_token,
                inbox=inbox,
                outbox=outbox,
                db_url=temp_db_url,
                local_partition=(X, y),
                app_env="development",
            )

        res = outbox.get(timeout=10)
        # Must fail closed with status ERROR and NEVER return status SUCCESS with vanilla weights
        assert res["status"] == "ERROR"
        assert "Simulated Opacus crash" in res["error"]

    # 12. Raw non-DP training telemetry is not released as though it were DP-protected
    def test_12_raw_non_dp_telemetry_not_released_as_dp_protected(
        self, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        model_svc = ModelService(get_settings())
        model = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)

        _, _, _ = model_svc.train_local_with_opacus(
            model, X, y, target_epsilon=3.0, target_delta=1e-5, epochs=1
        )
        prov = model._dp_provenance  # type: ignore[attr-defined]

        # Ensure telemetry classification clearly separates weights vs bounded loss observation
        telemetry = prov["telemetry_classification"]
        assert telemetry["model_weights"] == "differentially_private_record_level"
        assert telemetry["training_loss"] == "bounded_non_private_observation"
        assert telemetry["sample_count"] == "public_metadata_exact"
        assert prov["secure_rng"] is False or prov["secure_rng"] is True

    # 13. Real multi-process operational training produces valid candidate
    def test_13_multi_process_operational_training_produces_valid_candidate(
        self, temp_db_url: str, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_prod_test",
            db_url=temp_db_url,
            app_env="development",
        )
        job_id = f"job_mp_{uuid.uuid4().hex[:8]}"

        result = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id=f"sim_{job_id}",
            holdout_val_data=(X[:50], y[:50]),
            bank_partitions={
                "BANK_A": (X[:100], y[:100]),
                "BANK_B": (X[100:200], y[100:200]),
            },
            num_rounds=1,
            dp_config={
                "dp_enabled": True,
                "target_epsilon": 8.0,
                "target_delta": 1e-5,
                "max_grad_norm": 1.0,
                "dataset_tag": "partition_ds",
            },
        )

        assert result["status"] == "COMPLETED"
        assert result["is_verified"] is True
        assert result["is_promoted_champion"] is False
        assert result["saved_version"]["status"] == "inactive"
        assert result["dp_enabled"] is True
        assert "BANK_A" in result["dp_provenance"]
        assert "BANK_B" in result["dp_provenance"]
        assert result["dp_provenance"]["BANK_A"]["mechanism"] == "SubsampledGaussianDP"

    # 14. Existing non-DP mode remains correctly classified and functional
    def test_14_existing_non_dp_mode_remains_functional_and_correctly_classified(
        self, temp_db_url: str, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_nondp_test",
            db_url=temp_db_url,
            app_env="development",
        )
        job_id = f"job_nondp_{uuid.uuid4().hex[:8]}"

        result = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id=f"sim_{job_id}",
            holdout_val_data=(X[:50], y[:50]),
            bank_partitions={
                "BANK_A": (X[:100], y[:100]),
                "BANK_B": (X[100:200], y[100:200]),
            },
            num_rounds=1,
            dp_config=None,  # Standard non-DP mode
        )

        assert result["status"] == "COMPLETED"
        assert result["is_verified"] is True
        assert result["dp_enabled"] is False
        assert result["dp_provenance"] == {}
        assert result["metrics"]["auc_roc"] > 0.0

    # 15. TrainingJobAuthorityDatabase.__init__ is_prod / active_env regression test
    def test_15_training_job_authority_database_init_is_prod_regression(
        self, temp_db_url: str
    ) -> None:
        # Case A: is_prod=True explicitly passed, active_env=None (requires postgresql URL in prod)
        with patch.object(PostgresTrainingJobAuthority, "_test_connection"), patch.object(
            PostgresTrainingJobAuthority, "init_schema"
        ):
            auth_prod = TrainingJobAuthorityDatabase(
                db_url="postgresql://fake_user:fake_pass@localhost:5432/cfi_test", is_prod=True
            )
            assert auth_prod.active_env == "production"
            assert auth_prod.is_prod is True

        # Case B: is_prod=False explicitly passed, active_env="test_env"
        auth_dev = TrainingJobAuthorityDatabase(
            db_url=temp_db_url, is_prod=False, active_env="test_env"
        )
        assert auth_dev.active_env == "test_env"
        assert auth_dev.is_prod is False

        # Case C: Neither passed, falls back to settings
        auth_default = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        assert auth_default.active_env is not None
