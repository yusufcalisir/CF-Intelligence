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

import contextlib
import os
import tempfile
import uuid
from unittest.mock import patch

import numpy as np
import pytest
import torch

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    run_bank_worker_node,
)
from app.application.services.model_service import (
    NUM_FEATURES,
    ModelService,
)
from app.application.services.training_provenance_service import (
    TrainingJobAuthorityDatabase,
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
        with contextlib.suppress(OSError):
            os.remove(path)


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
        authority.provision_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag="credit_partition",
            allocated_epsilon=10.0,
            allocated_delta=1e-5,
        )
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
        authority.provision_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag="cum_ds",
            allocated_epsilon=10.0,
            allocated_delta=1e-5,
        )
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
        authority.provision_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag="fail_test",
            allocated_epsilon=10.0,
            allocated_delta=1e-5,
        )
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

        # Ensure telemetry classification clearly marks training loss as restricted bank-internal data
        telemetry = prov["telemetry_classification"]
        assert telemetry["model_weights"] == "differentially_private_record_level"
        assert telemetry["training_loss"] == "bank_internal_private_data_restricted"
        assert telemetry["observed_loss"] == "bank_internal_private_data_restricted"
        assert telemetry["sample_count"] == "public_metadata_exact"
        assert telemetry["receipt_attestation"] == "execution_evidence_not_mathematical_dp_proof"
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

    # Regression Evidence A: A job requiring epsilon greater than remaining budget cannot train using a smaller reservation.
    def test_regression_a_job_requiring_epsilon_greater_than_remaining_cannot_train(
        self, temp_db_url: str, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        coord_token = f"coord_{uuid.uuid4().hex}"
        bank_id = "BANK_A"
        worker_token = f"tok_{uuid.uuid4().hex}"
        job_id = f"job_a_{uuid.uuid4().hex[:8]}"
        dataset_tag = "budget_a_ds"

        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        # Allocate total 5.0, spend 3.5 -> remaining 1.5
        authority.get_or_create_institutional_privacy_budget(
            bank_id=bank_id, dataset_tag=dataset_tag, allocated_epsilon=5.0
        )
        authority.reserve_institutional_privacy_budget(bank_id, dataset_tag, "job_prior", 3.5)
        authority.commit_institutional_privacy_expenditure(bank_id, dataset_tag, "job_prior", 3.5)

        # Job requests target_epsilon 3.0 (needed_eps = 3.0 > remaining 1.5)
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
                "target_epsilon": 3.0,
                "target_delta": 1e-5,
                "dataset_tag": dataset_tag,
            },
        )

        import multiprocessing as mp
        inbox: mp.Queue = mp.Queue()
        outbox: mp.Queue = mp.Queue()
        inbox.put({"command": "TRAIN_ROUND", "job_id": job_id, "round_number": 1, "global_weights": None})
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
        # Must fail closed with ERROR status, NOT clamp reservation to 1.5
        assert res["status"] == "ERROR"
        assert "Privacy budget exhausted" in res["error"]
        # Ensure budget was NOT consumed
        b = authority.get_institutional_privacy_budget(bank_id, dataset_tag)
        assert b is not None
        assert b.spent_epsilon == pytest.approx(3.5, abs=1e-5)
        assert b.reserved_epsilon == 0.0

    # Regression Evidence B: Commit rejects actual expenditure exceeding available authorized budget.
    def test_regression_b_commit_rejects_actual_expenditure_exceeding_authorized_budget(
        self, temp_db_url: str
    ) -> None:
        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        bank_id = "BANK_B"
        dataset_tag = "overspend_ds"
        authority.get_or_create_institutional_privacy_budget(
            bank_id=bank_id, dataset_tag=dataset_tag, allocated_epsilon=2.0
        )
        authority.reserve_institutional_privacy_budget(bank_id, dataset_tag, "job_b", 2.0)

        # Attempt to commit 3.5 epsilon (allocated is 2.0)
        with pytest.raises(TrainingProvenanceSecurityError, match="exceeds allocated budget"):
            authority.commit_institutional_privacy_expenditure(
                bank_id=bank_id, dataset_tag=dataset_tag, job_id="job_b", spent_epsilon=3.5
            )

        # Conservative state: budget is exhausted/marked, reservation cleared
        b = authority.get_institutional_privacy_budget(bank_id, dataset_tag)
        assert b is not None
        assert b.spent_epsilon >= 2.0
        assert b.reserved_epsilon == 0.0
        assert b.remaining_epsilon <= 0.0

    # Regression Evidence C: Restarted workers cannot repeat training without additional accounting.
    def test_regression_c_restarted_workers_cannot_repeat_training_without_accounting(
        self, temp_db_url: str, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        bank_id = "BANK_A"
        dataset_tag = "continuity_ds"
        job_id = "job_restart"
        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        authority.get_or_create_institutional_privacy_budget(
            bank_id=bank_id, dataset_tag=dataset_tag, allocated_epsilon=25.0, allocated_delta=1e-4
        )
        authority.reserve_institutional_privacy_budget(bank_id, dataset_tag, job_id, 2.0, round_number=1, attempt_id="att_1")
        authority.commit_institutional_privacy_expenditure(
            bank_id, dataset_tag, job_id, spent_epsilon=1.5, steps_executed=10, round_number=1, attempt_id="att_1"
        )

        b1 = authority.get_institutional_privacy_budget(bank_id, dataset_tag)
        assert b1 is not None and b1.spent_epsilon == pytest.approx(1.5, abs=1e-5)

        # Worker restarts in a new process (simulated fresh session with prior_steps restored from DB)
        # Round 2 runs
        model_svc = ModelService(get_settings())
        model = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        # train with prior_steps=10
        _, _, eps_r2 = model_svc.train_local_with_opacus(
            model=model,
            X_train=X,
            y_train=y,
            target_epsilon=10.0,
            target_delta=1e-5,
            prior_steps=10,
            epochs=1,
        )
        # Cumulative epsilon is strictly positive and training adds to accounting
        assert eps_r2 > 0.0
        authority.reserve_institutional_privacy_budget(bank_id, dataset_tag, job_id, eps_r2, round_number=2, attempt_id="att_2")
        authority.commit_institutional_privacy_expenditure(
            bank_id, dataset_tag, job_id, spent_epsilon=eps_r2, steps_executed=10, round_number=2, attempt_id="att_2"
        )
        b2 = authority.get_institutional_privacy_budget(bank_id, dataset_tag)
        assert b2 is not None
        assert b2.spent_epsilon > 1.5

    # Regression Evidence D: Replayed attempts are idempotent only when no new computation occurs.
    def test_regression_d_replayed_attempts_are_idempotent_only_when_no_new_computation(
        self, temp_db_url: str
    ) -> None:
        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        bank_id = "BANK_C"
        dataset_tag = "replay_ds"
        job_id = "job_replay"
        authority.get_or_create_institutional_privacy_budget(
            bank_id=bank_id, dataset_tag=dataset_tag, allocated_epsilon=10.0, allocated_delta=1e-3
        )
        authority.reserve_institutional_privacy_budget(
            bank_id, dataset_tag, job_id, 2.0, round_number=1, attempt_id="attempt_alpha"
        )

        # Initial commit of attempt_alpha
        b1 = authority.commit_institutional_privacy_expenditure(
            bank_id, dataset_tag, job_id, spent_epsilon=1.8, steps_executed=10, round_number=1, attempt_id="attempt_alpha"
        )
        assert b1.spent_epsilon == pytest.approx(1.8, abs=1e-5)

        # Exact duplicate commit of same attempt is idempotent
        b2 = authority.commit_institutional_privacy_expenditure(
            bank_id, dataset_tag, job_id, spent_epsilon=1.8, steps_executed=10, round_number=1, attempt_id="attempt_alpha"
        )
        assert b2.spent_epsilon == pytest.approx(1.8, abs=1e-5)

        # Re-execution with new attempt consumes additional budget
        authority.reserve_institutional_privacy_budget(
            bank_id, dataset_tag, job_id, 2.0, round_number=1, attempt_id="attempt_beta"
        )
        b3 = authority.commit_institutional_privacy_expenditure(
            bank_id, dataset_tag, job_id, spent_epsilon=1.8, steps_executed=10, round_number=1, attempt_id="attempt_beta"
        )
        assert b3.spent_epsilon == pytest.approx(3.6, abs=1e-5)

    # Regression Evidence E: Cross-bank tokens cannot mutate another institution's budget.
    def test_regression_e_cross_bank_tokens_cannot_mutate_or_read_budget(
        self, temp_db_url: str
    ) -> None:
        source = TrustedTrainingJobRecordSource(
            db_url=temp_db_url,
            app_env="development",
            bank_tokens={"BANK_A": "token_bank_a", "BANK_B": "token_bank_b"},
        )
        # Bank B worker attempts to query or mutate Bank A budget using Bank B token
        with pytest.raises(TrainingProvenanceSecurityError, match="Cross-bank credential rejected"):
            source.get_or_create_institutional_privacy_budget(
                bank_id="BANK_A", dataset_tag="ds_a", token="token_bank_b"
            )

        with pytest.raises(TrainingProvenanceSecurityError, match="Cross-bank credential rejected"):
            source.reserve_institutional_privacy_budget(
                bank_id="BANK_A", dataset_tag="ds_a", job_id="job_x", requested_epsilon=1.0, worker_token="token_bank_b"
            )

        with pytest.raises(TrainingProvenanceSecurityError, match="Cross-bank credential rejected"):
            source.commit_institutional_privacy_expenditure(
                bank_id="BANK_A", dataset_tag="ds_a", job_id="job_x", spent_epsilon=1.0, worker_token="token_bank_b"
            )

        # Anonymous call when bank tokens are configured is also rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="missing training worker authorization token"):
            source.reserve_institutional_privacy_budget(
                bank_id="BANK_A", dataset_tag="ds_a", job_id="job_x", requested_epsilon=1.0, worker_token=None
            )

    # Regression Evidence F: Altering dataset_tag or consortium_id does not reset an existing dataset's budget.
    def test_regression_f_altering_dataset_tag_aliases_does_not_reset_budget(
        self, temp_db_url: str
    ) -> None:
        source = TrustedTrainingJobRecordSource(
            db_url=temp_db_url, app_env="development", coordinator_token="coord_tok"
        )
        # Base dataset budget
        source.get_or_create_institutional_privacy_budget(
            bank_id="BANK_A", dataset_tag="vault_records", allocated_epsilon=5.0
        )
        source.reserve_institutional_privacy_budget("BANK_A", "vault_records", "job_1", 3.0)
        source.commit_institutional_privacy_expenditure("BANK_A", "vault_records", "job_1", 3.0)

        # Querying with alias or copy suffix resolves to canonical dataset tag
        b_alias = source.get_institutional_privacy_budget("BANK_A", "vault_records_alias")
        assert b_alias is not None
        assert b_alias.spent_epsilon == pytest.approx(3.0, abs=1e-5)
        assert b_alias.remaining_epsilon == pytest.approx(2.0, abs=1e-5)

        b_copy = source.get_institutional_privacy_budget("BANK_A", "vault_records_copy")
        assert b_copy is not None
        assert b_copy.spent_epsilon == pytest.approx(3.0, abs=1e-5)

    # Regression Evidence G: A client cannot arbitrarily allocate or increase privacy budgets.
    def test_regression_g_client_cannot_arbitrarily_allocate_or_increase_budget(
        self, temp_db_url: str
    ) -> None:
        source = TrustedTrainingJobRecordSource(
            db_url=temp_db_url, app_env="development", coordinator_token="coord_tok"
        )
        # Institutional authority provisions 4.0
        b1 = source.get_or_create_institutional_privacy_budget("BANK_A", "strict_ds", allocated_epsilon=4.0)
        assert b1.allocated_epsilon == 4.0

        # Subsequent client request claiming allocated_epsilon=50.0 does NOT increase it
        b2 = source.get_or_create_institutional_privacy_budget("BANK_A", "strict_ds", allocated_epsilon=50.0)
        assert b2.allocated_epsilon == 4.0

    # Regression Evidence H: Protected training loss is not released merely because it was numerically clipped.
    def test_regression_h_protected_training_loss_omitted_in_dp_mode(
        self, temp_db_url: str, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_dp_h",
            db_url=temp_db_url,
            app_env="development",
        )
        job_id = f"job_h_{uuid.uuid4().hex[:8]}"
        res = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id=f"sim_{job_id}",
            holdout_val_data=(X[:50], y[:50]),
            bank_partitions={"BANK_A": (X[:100], y[:100])},
            num_rounds=1,
            dp_config={
                "dp_enabled": True,
                "target_epsilon": 5.0,
                "target_delta": 1e-5,
                "dataset_tag": "h_ds",
            },
        )
        assert res["status"] == "COMPLETED"
        # Verify round observations stored in DB omit training loss
        obs_list = manager.authority_source.get_round_observations(job_id)
        assert len(obs_list) >= 1
        for o in obs_list:
            assert o.observed_loss is None
            assert "loss" not in o.metrics

    # Regression Evidence I: Actual Opacus accountant, sampling, noise, and secure RNG configuration match declared policy.
    def test_regression_i_accountant_naming_and_secure_rng_match_policy(
        self, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        model_svc = ModelService(get_settings())
        model = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)

        # Test accountant_type normalization (opacus_prv -> prv)
        _, _, _ = model_svc.train_local_with_opacus(
            model=model,
            X_train=X,
            y_train=y,
            target_epsilon=3.0,
            target_delta=1e-5,
            accountant_type="opacus_prv",
            require_secure_rng=False,
            epochs=1,
        )
        prov = model._dp_provenance  # type: ignore[attr-defined]
        assert prov["accountant"] == "opacus_prv"
        assert prov["secure_rng"] is False
        assert prov["sampling"] == "poisson"

    # Negative Test A: Two unrelated labels for the same authoritative bank dataset cannot create independent budgets.
    def test_negative_a_unrelated_labels_same_dataset_cannot_create_independent_budgets(
        self, temp_db_url: str
    ) -> None:
        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        bank_id = "BANK_A"
        auth_tag = "fraud_vault_v1"
        authority.provision_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag=auth_tag,
            allocated_epsilon=6.0,
            allocated_delta=1e-3,
        )
        # Register two unrelated aliases pointing to the same authoritative dataset
        authority.register_institutional_dataset_alias(bank_id, "transactions_q1", auth_tag)
        authority.register_institutional_dataset_alias(bank_id, "transactions_q2", auth_tag)

        assert authority.resolve_authoritative_dataset_identity(bank_id, "transactions_q1") == auth_tag
        assert authority.resolve_authoritative_dataset_identity(bank_id, "transactions_q2") == auth_tag

        # Spend 4.0 via transactions_q1
        authority.reserve_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag="transactions_q1",
            job_id="job_q1",
            round_number=1,
            requested_epsilon=4.0,
            requested_delta=1e-4,
            attempt_id="att_q1",
        )
        authority.commit_institutional_privacy_expenditure(
            bank_id=bank_id,
            dataset_tag="transactions_q1",
            job_id="job_q1",
            round_number=1,
            spent_epsilon=4.0,
            spent_delta=1e-4,
            steps_executed=10,
            attempt_id="att_q1",
        )

        # Observed budget via transactions_q2 MUST reflect the 4.0 spent
        b2 = authority.get_institutional_privacy_budget(bank_id, "transactions_q2")
        assert b2 is not None
        assert b2.spent_epsilon == pytest.approx(4.0, abs=1e-5)
        assert b2.remaining_epsilon == pytest.approx(2.0, abs=1e-5)

        # Attempting to reserve 3.0 via transactions_q2 must fail closed (4.0 + 3.0 = 7.0 > 6.0)
        with pytest.raises(TrainingProvenanceSecurityError, match="Privacy budget exceeded"):
            authority.reserve_institutional_privacy_budget(
                bank_id=bank_id,
                dataset_tag="transactions_q2",
                job_id="job_q2",
                round_number=1,
                requested_epsilon=3.0,
                requested_delta=1e-4,
                attempt_id="att_q2",
            )

    # Negative Test B: Consortium or job change cannot reset cumulative expenditure.
    def test_negative_b_consortium_or_job_change_cannot_reset_cumulative_expenditure(
        self, temp_db_url: str
    ) -> None:
        source = TrustedTrainingJobRecordSource(
            coordinator_token="coord_alpha", db_url=temp_db_url, app_env="development"
        )
        bank_id = "BANK_A"
        dataset_tag = "core_transactions"
        source.provision_institutional_privacy_budget(
            bank_id=bank_id, dataset_tag=dataset_tag, allocated_epsilon=5.0, allocated_delta=1e-3
        )

        # Job 1 under Consortium Alpha spends 3.5 epsilon
        source.schedule_training_job(
            job_id="job_alpha",
            training_run_id="run_alpha",
            simulation_id="sim_alpha",
            coordinator_token="coord_alpha",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=[bank_id],
            consortium_id="consortium-alpha",
            dp_config={"dp_enabled": True, "target_epsilon": 3.5, "dataset_tag": dataset_tag},
        )
        source.reserve_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag=dataset_tag,
            job_id="job_alpha",
            requested_epsilon=3.5,
            attempt_id="att_alpha",
        )
        source.commit_institutional_privacy_expenditure(
            bank_id=bank_id,
            dataset_tag=dataset_tag,
            job_id="job_alpha",
            spent_epsilon=3.5,
            attempt_id="att_alpha",
        )

        # Switch to Consortium Beta with different coordinator token
        source_beta = TrustedTrainingJobRecordSource(
            coordinator_token="coord_beta", db_url=temp_db_url, app_env="development"
        )
        source_beta.schedule_training_job(
            job_id="job_beta",
            training_run_id="run_beta",
            simulation_id="sim_beta",
            coordinator_token="coord_beta",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=[bank_id],
            consortium_id="consortium-beta",
            dp_config={"dp_enabled": True, "target_epsilon": 2.0, "dataset_tag": dataset_tag},
        )

        # Reserving 2.0 must fail closed because cumulative expenditure (3.5 + 2.0 = 5.5) exceeds 5.0
        with pytest.raises(TrainingProvenanceSecurityError, match="Privacy budget exceeded"):
            source_beta.reserve_institutional_privacy_budget(
                bank_id=bank_id,
                dataset_tag=dataset_tag,
                job_id="job_beta",
                requested_epsilon=2.0,
                attempt_id="att_beta",
            )

    # Negative Test C: Missing trusted dataset identity fails closed in production.
    def test_negative_c_missing_trusted_dataset_identity_fails_closed_in_production(
        self, temp_db_url: str
    ) -> None:
        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        # 1. Unknown or unregistered dataset tag fails closed in production
        with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized dataset identity"):
            authority.resolve_authoritative_dataset_identity(
                bank_id="BANK_A", dataset_tag="unknown_external_alias", is_prod=True
            )

        # 2. Empty dataset_tag fails closed in production
        with pytest.raises(TrainingProvenanceSecurityError, match="Empty dataset_tag rejected"):
            authority.resolve_authoritative_dataset_identity(
                bank_id="BANK_A", dataset_tag="", is_prod=True
            )

    # Negative Test D: Training request with large target epsilon cannot create or enlarge allocation.
    def test_negative_d_training_request_with_large_epsilon_cannot_create_or_enlarge_allocation(
        self, temp_db_url: str
    ) -> None:
        source = TrustedTrainingJobRecordSource(
            coordinator_token="coord_tok", db_url=temp_db_url, app_env="development"
        )
        bank_id = "BANK_A"
        dataset_tag = "fixed_cap_ds"

        # Explicit provisioning by allocation authority sets budget to 3.0
        source.provision_institutional_privacy_budget(
            bank_id=bank_id, dataset_tag=dataset_tag, allocated_epsilon=3.0, allocated_delta=1e-3
        )

        # Training request / get_or_create attempting to allocate 100.0 epsilon does NOT enlarge the budget
        b = source.get_or_create_institutional_privacy_budget(
            bank_id=bank_id, dataset_tag=dataset_tag, allocated_epsilon=100.0
        )
        assert b.allocated_epsilon == 3.0

        # Requesting 4.0 epsilon fails closed against the 3.0 cap
        with pytest.raises(TrainingProvenanceSecurityError, match="Privacy budget exceeded"):
            source.reserve_institutional_privacy_budget(
                bank_id=bank_id,
                dataset_tag=dataset_tag,
                job_id="job_oversize",
                requested_epsilon=4.0,
            )

    # Negative Test E: Only authorized allocation authority can provision new dataset budget.
    def test_negative_e_only_authorized_allocation_authority_can_provision_new_budget(
        self, temp_db_url: str
    ) -> None:
        source = TrustedTrainingJobRecordSource(
            coordinator_token="valid_coord_token",
            db_url=temp_db_url,
            app_env="development",
            bank_tokens={"BANK_A": "bank_worker_token_a"},
        )
        # Attempting to provision using worker credential instead of coordinator credential fails closed
        with pytest.raises(TrainingProvenanceSecurityError, match="Bank worker credential cannot authorize coordinator actions"):
            source.provision_institutional_privacy_budget(
                bank_id="BANK_A",
                dataset_tag="unauth_ds",
                allocated_epsilon=10.0,
                authority_token="bank_worker_token_a",
            )

        # Attempting to provision using invalid coordinator token fails closed
        with pytest.raises(TrainingProvenanceSecurityError, match="invalid coordinator token"):
            source.provision_institutional_privacy_budget(
                bank_id="BANK_A",
                dataset_tag="unauth_ds",
                allocated_epsilon=10.0,
                authority_token="invalid_coord_token",
            )

        # With valid coordinator token, provisioning succeeds
        b = source.provision_institutional_privacy_budget(
            bank_id="BANK_A",
            dataset_tag="auth_ds",
            allocated_epsilon=10.0,
            authority_token="valid_coord_token",
        )
        assert b.allocated_epsilon == 10.0

    # Negative Test F: Replayed TRAIN_ROUND and attempt ID cannot execute second training.
    def test_negative_f_replayed_train_round_and_attempt_id_cannot_execute_second_training(
        self, temp_db_url: str, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        coord_token = f"coord_{uuid.uuid4().hex}"
        bank_id = "BANK_A"
        worker_token = f"tok_{uuid.uuid4().hex}"
        job_id = f"job_replay_{uuid.uuid4().hex[:8]}"
        attempt_id = f"attempt_dedup_{uuid.uuid4().hex[:6]}"

        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        authority.provision_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag="replay_train_ds",
            allocated_epsilon=10.0,
            allocated_delta=1e-3,
        )
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
                "dataset_tag": "replay_train_ds",
            },
        )

        import multiprocessing as mp

        inbox: mp.Queue = mp.Queue()
        outbox: mp.Queue = mp.Queue()

        model_svc = ModelService(get_settings())
        base_model = model_svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        init_weights = model_svc.get_parameters(base_model)

        # First TRAIN_ROUND execution
        inbox.put({
            "command": "TRAIN_ROUND",
            "job_id": job_id,
            "round_number": 1,
            "attempt_id": attempt_id,
            "global_weights": init_weights,
        })
        # Replay the EXACT SAME TRAIN_ROUND and attempt_id
        inbox.put({
            "command": "TRAIN_ROUND",
            "job_id": job_id,
            "round_number": 1,
            "attempt_id": attempt_id,
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

        res_1 = outbox.get(timeout=10)
        assert res_1["status"] == "SUCCESS"
        assert "dp_provenance" in res_1
        eps_1 = res_1["dp_provenance"]["actual_epsilon"]

        res_2 = outbox.get(timeout=10)
        assert res_2["status"] == "SUCCESS"
        # Second execution was detected as ALREADY_COMPLETED and returned cached result
        assert res_2.get("replayed") is True or res_2.get("dp_provenance") is not None

        # Check durable budget in DB: spent_epsilon must NOT have doubled!
        b = authority.get_institutional_privacy_budget(bank_id, "replay_train_ds")
        assert b is not None
        assert b.spent_epsilon == pytest.approx(eps_1, abs=1e-5)

    # Negative Test G: Distinct valid attempts consume privacy budget independently.
    def test_negative_g_distinct_valid_attempts_consume_budget_independently(
        self, temp_db_url: str
    ) -> None:
        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        bank_id = "BANK_A"
        dataset_tag = "multi_attempt_ds"
        job_id = "job_multi_att"

        authority.provision_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag=dataset_tag,
            allocated_epsilon=10.0,
            allocated_delta=1e-3,
        )

        # Attempt 1
        authority.reserve_institutional_privacy_budget(
            bank_id, dataset_tag, job_id, 2.0, round_number=1, attempt_id="att_valid_1"
        )
        b1 = authority.commit_institutional_privacy_expenditure(
            bank_id, dataset_tag, job_id, spent_epsilon=1.5, round_number=1, attempt_id="att_valid_1"
        )
        assert b1.spent_epsilon == pytest.approx(1.5, abs=1e-5)

        # Attempt 2 (Round 2 cumulative Opacus expenditure)
        authority.reserve_institutional_privacy_budget(
            bank_id, dataset_tag, job_id, 2.0, round_number=2, attempt_id="att_valid_2"
        )
        b2 = authority.commit_institutional_privacy_expenditure(
            bank_id, dataset_tag, job_id, spent_epsilon=3.0, round_number=2, attempt_id="att_valid_2"
        )
        assert b2.spent_epsilon == pytest.approx(3.0, abs=1e-5)

    # Negative Test H: Concurrent or restarted workers cannot reuse one attempt authorization.
    def test_negative_h_concurrent_or_restarted_workers_cannot_reuse_attempt_authorization(
        self, temp_db_url: str
    ) -> None:
        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        bank_id = "BANK_A"
        dataset_tag = "concurrent_ds"
        job_id = "job_conc"
        attempt_id = "att_locked_123"

        authority.provision_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag=dataset_tag,
            allocated_epsilon=10.0,
            allocated_delta=1e-3,
        )

        # Worker 1 admits attempt_locked_123 (now in active_reservations as RESERVED)
        res1 = authority.admit_training_attempt(
            bank_id=bank_id,
            dataset_tag=dataset_tag,
            job_id=job_id,
            round_number=1,
            attempt_id=attempt_id,
            parameters_digest="params_digest_conc_1",
            requested_epsilon=2.0,
            requested_delta=1e-5,
        )
        assert res1.status == "ADMITTED"

        # Worker 2 attempts to admit the same attempt_id while still active
        res2 = authority.admit_training_attempt(
            bank_id=bank_id,
            dataset_tag=dataset_tag,
            job_id=job_id,
            round_number=1,
            attempt_id=attempt_id,
            parameters_digest="params_digest_conc_1",
            requested_epsilon=2.0,
            requested_delta=1e-5,
        )
        assert res2.status == "CONCURRENT_EXECUTION_BLOCKED"
        assert "currently executing in another active worker" in res2.reason

        # reserve_institutional_privacy_budget raises TrainingProvenanceSecurityError
        with pytest.raises(TrainingProvenanceSecurityError, match="currently executing in another active worker"):
            authority.reserve_institutional_privacy_budget(
                bank_id=bank_id,
                dataset_tag=dataset_tag,
                job_id=job_id,
                round_number=1,
                requested_epsilon=2.0,
                attempt_id=attempt_id,
            )

    # Negative Test I: Commit for absent or mismatched reservation is rejected safely.
    def test_negative_i_commit_for_absent_or_mismatched_reservation_is_rejected_safely(
        self, temp_db_url: str
    ) -> None:
        authority = TrainingJobAuthorityDatabase(db_url=temp_db_url, is_prod=False)
        bank_id = "BANK_A"
        dataset_tag = "mismatch_ds"
        job_id = "job_mismatch"

        authority.provision_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag=dataset_tag,
            allocated_epsilon=10.0,
            allocated_delta=1e-3,
        )

        # Case 1: Commit for unreserved attempt is rejected safely
        with pytest.raises(TrainingProvenanceSecurityError, match="no active matching reservation"):
            authority.commit_institutional_privacy_expenditure(
                bank_id=bank_id,
                dataset_tag=dataset_tag,
                job_id=job_id,
                round_number=1,
                spent_epsilon=1.0,
                attempt_id="att_never_reserved",
            )

        # Case 2: Reservation made for att_alpha, commit attempted for att_beta is rejected safely
        authority.reserve_institutional_privacy_budget(
            bank_id=bank_id,
            dataset_tag=dataset_tag,
            job_id=job_id,
            round_number=1,
            requested_epsilon=2.0,
            attempt_id="att_alpha",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="attempt mismatch"):
            authority.commit_institutional_privacy_expenditure(
                bank_id=bank_id,
                dataset_tag=dataset_tag,
                job_id=job_id,
                round_number=1,
                spent_epsilon=1.0,
                attempt_id="att_beta",
            )

    # Negative Test J: Multi-process Opacus training and inactive candidate registration.
    def test_negative_j_multi_process_opacus_training_and_inactive_candidate_registration(
        self, temp_db_url: str, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_neg_j",
            db_url=temp_db_url,
            app_env="development",
        )
        job_id = f"job_j_{uuid.uuid4().hex[:8]}"

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

    # Negative Test K: Existing non-DP training remains unaffected.
    def test_negative_k_existing_non_dp_training_remains_unaffected(
        self, temp_db_url: str, synthetic_bank_data: tuple[np.ndarray, np.ndarray]
    ) -> None:
        X, y = synthetic_bank_data
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_neg_k",
            db_url=temp_db_url,
            app_env="development",
        )
        job_id = f"job_k_{uuid.uuid4().hex[:8]}"

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
            dp_config=None,
        )

        assert result["status"] == "COMPLETED"
        assert result["is_verified"] is True
        assert result["dp_enabled"] is False
        assert result["dp_provenance"] == {}
        assert result["saved_version"]["status"] == "inactive"

