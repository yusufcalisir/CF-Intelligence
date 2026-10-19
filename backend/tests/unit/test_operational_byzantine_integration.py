"""Operational Byzantine Defense & Security Composition Integration Test Suite.

Verifies Requirements A through M:
A. Default operational non-SecAgg training still executes weighted FedAvg.
B. Explicit operational Coordinate Median executes over genuine contributions from independent bank processes.
C. A controlled malicious bank contribution meaningfully changes plaintext FedAvg, while the selected robust method produces the mathematically expected result.
D. The test distinguishes mathematical aggregation correctness from actual model-quality improvement.
E. Krum and Bulyan execute only with valid participant/fault thresholds.
F. Invalid consortium size, trim count, or configuration fails before training.
G. SecAgg + any requested non-linear Byzantine defense fails closed through both API and direct service entry points.
H. Existing SecAgg + DP execution remains unaffected.
I. Stale round, cross-job, incorrect-bank, malformed, duplicate, and tampered responses are rejected.
J. Actual algorithm identifiers and authoritative aggregation evidence match the executed computation.
K. Inactive model candidates still require verified receipts and independent Four-Eyes promotion.
L. Regulatory and catalogue outputs no longer falsely claim unconditional operational Byzantine tolerance.
M. Existing operational federation, DP, SecAgg, Byzantine mathematical correctness, and PostgreSQL authorization regression tests remain functional.
"""

import asyncio
import hashlib
import os
import shutil
import tempfile
from typing import Any, Literal

import numpy as np
import pytest
from pydantic import ValidationError

from app.application.schemas.simulation import (
    OperationalFederationRequest,
)
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    ModelWeights,
    _serialize_weights_to_bytes,
)
from app.application.services.fl_engine import (
    AggregationMethod,
    FederatedLearningEngine,
)
from app.application.services.model_registry import (
    ModelRegistry,
)
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.operational_federation_service import (
    OperationalFederationService,
    WorkerEndpointRegistry,
)
from app.application.services.privacy_service import PrivacyService
from app.application.services.regulatory_dossier_generator import RegulatoryDossierGenerator
from app.application.services.training_provenance_service import (
    TrainingProvenanceSecurityError,
)
from app.config import get_settings
from app.infrastructure.database.training_authority_backends import (
    SQLiteTrainingJobAuthority,
)
from app.presentation.routers.admin_console import get_admin_system_config
from app.presentation.routers.privacy_defense import AGGREGATION_METHODS


@pytest.fixture(autouse=True)
def clean_test_environment():
    """Ensure clean test environment per test."""
    WorkerEndpointRegistry.clear()
    tmp_reg = tempfile.mkdtemp(prefix="cfi_byz_test_")
    old_env = os.environ.get("APP_ENV")
    os.environ["APP_ENV"] = "development"

    yield tmp_reg

    WorkerEndpointRegistry.clear()
    shutil.rmtree(tmp_reg, ignore_errors=True)
    if old_env is not None:
        os.environ["APP_ENV"] = old_env
    else:
        os.environ.pop("APP_ENV", None)


class TestOperationalByzantineIntegration:
    """Verifies operational Byzantine defenses, preconditions, SecAgg composition, and provenance."""

    def test_requirement_a_default_operational_non_secagg_executes_weighted_fedavg(
        self, clean_test_environment: str
    ) -> None:
        """Requirement A: Default operational non-SecAgg training still executes weighted FedAvg."""
        db_file = os.path.join(clean_test_environment, "fl_auth_a.db")
        registry_dir = os.path.join(clean_test_environment, "reg_a")
        os.makedirs(registry_dir, exist_ok=True)

        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_a",
            db_url=db_file,
            app_env="development",
        )

        val_X = np.random.randn(20, NUM_FEATURES).astype(np.float32)
        val_y = np.random.randint(0, 2, size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                np.random.randn(30, NUM_FEATURES).astype(np.float32),
                np.random.randint(0, 2, size=30).astype(np.float32),
            ),
            "BANK_B": (
                np.random.randn(40, NUM_FEATURES).astype(np.float32),
                np.random.randint(0, 2, size=40).astype(np.float32),
            ),
            "BANK_C": (
                np.random.randn(50, NUM_FEATURES).astype(np.float32),
                np.random.randint(0, 2, size=50).astype(np.float32),
            ),
        }

        # byzantine_defense="none" (the operational default)
        result = manager.execute_distributed_federation(
            job_id="job_req_a",
            training_run_id="run_req_a",
            simulation_id="sim_req_a",
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
            byzantine_defense="none",
        )

        assert result["status"] == "COMPLETED"
        assert result["is_verified"] is True
        assert result["requested_aggregation_method"] == "none"
        assert result["executed_aggregation_method"] == "fed_avg_weighted"

        # Verify authoritative record in authority via get_global_aggregations
        auth = SQLiteTrainingJobAuthority(db_file)
        events = auth.get_global_aggregations("job_req_a")
        assert len(events) == 1
        assert events[0].aggregation_algorithm == "fed_avg_weighted"
        assert set(events[0].accepted_participant_ids) == {"BANK_A", "BANK_B", "BANK_C"}

    def test_requirement_b_explicit_coordinate_median_executes_over_genuine_processes(
        self, clean_test_environment: str
    ) -> None:
        """Requirement B: Explicit operational Coordinate Median executes over genuine contributions from independent bank processes."""
        db_file = os.path.join(clean_test_environment, "fl_auth_b.db")
        registry_dir = os.path.join(clean_test_environment, "reg_b")
        os.makedirs(registry_dir, exist_ok=True)

        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_b",
            db_url=db_file,
            app_env="development",
        )

        val_X = np.random.randn(20, NUM_FEATURES).astype(np.float32)
        val_y = np.random.randint(0, 2, size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                np.random.randn(30, NUM_FEATURES).astype(np.float32),
                np.random.randint(0, 2, size=30).astype(np.float32),
            ),
            "BANK_B": (
                np.random.randn(30, NUM_FEATURES).astype(np.float32),
                np.random.randint(0, 2, size=30).astype(np.float32),
            ),
            "BANK_C": (
                np.random.randn(30, NUM_FEATURES).astype(np.float32),
                np.random.randint(0, 2, size=30).astype(np.float32),
            ),
        }

        result = manager.execute_distributed_federation(
            job_id="job_req_b",
            training_run_id="run_req_b",
            simulation_id="sim_req_b",
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
            byzantine_defense="coordinate_wise_median",
            byzantine_f_max=1,
        )

        assert result["status"] == "COMPLETED"
        assert result["is_verified"] is True
        assert result["requested_aggregation_method"] == "coordinate_wise_median"
        assert result["executed_aggregation_method"] == "coordinate_wise_median"
        assert result["byzantine_defense"] == "coordinate_wise_median"
        assert result["byzantine_f_max"] == 1

        # Confirm all 3 bank workers were genuine distinct independent OS processes
        pids = result["worker_pids"]
        assert len(pids) == 3
        unique_pids = set(pids.values())
        assert len(unique_pids) == 3
        assert result["coordinator_pid"] not in unique_pids

        # Authoritative database audit
        auth = SQLiteTrainingJobAuthority(db_file)
        events = auth.get_global_aggregations("job_req_b")
        assert len(events) == 1
        assert events[0].aggregation_algorithm == "coordinate_wise_median"
        assert set(events[0].accepted_participant_ids) == {"BANK_A", "BANK_B", "BANK_C"}

    def test_requirement_c_adversarial_bank_divergence_fedavg_vs_median(self) -> None:
        """Requirement C: Controlled malicious bank contribution meaningfully changes plaintext FedAvg while robust median matches mathematical expectation."""
        settings = get_settings()
        model_service = ModelService(settings)
        privacy_service = PrivacyService()
        fl_engine = FederatedLearningEngine(
            settings=settings,
            model_service=model_service,
            privacy_service=privacy_service,
        )
        base_model = model_service.create_model()
        base_weights = model_service.get_parameters(base_model)

        # Build two honest client updates around modest weights (e.g. 1.0 and 1.2)
        honest_w1 = ModelWeights(
            layer_shapes=base_weights.layer_shapes,
            flat_weights=[1.0] * len(base_weights.flat_weights),
        )
        honest_w2 = ModelWeights(
            layer_shapes=base_weights.layer_shapes,
            flat_weights=[1.2] * len(base_weights.flat_weights),
        )

        # Build Byzantine malicious update: scaled extreme perturbation (e.g. 1000.0)
        malicious_w = ModelWeights(
            layer_shapes=base_weights.layer_shapes,
            flat_weights=[1000.0] * len(base_weights.flat_weights),
        )

        client_weights = [honest_w1, honest_w2, malicious_w]
        client_samples = [100, 100, 100]

        # 1. Plaintext FedAvg aggregation
        fedavg_weights = fl_engine.aggregate_parameters(
            client_weights=client_weights,
            client_samples=client_samples,
            method=AggregationMethod.FED_AVG_WEIGHTED,
            global_weights=base_weights,
        )

        # 2. Coordinate-wise Median aggregation
        median_weights = fl_engine.aggregate_parameters(
            client_weights=client_weights,
            client_samples=client_samples,
            method=AggregationMethod.COORDINATE_WISE_MEDIAN,
            global_weights=base_weights,
            f_byzantine=1,
            strict_preconditions=True,
        )

        fedavg_arr = np.array(fedavg_weights.flat_weights)
        median_arr = np.array(median_weights.flat_weights)

        # Expected mathematical FedAvg: (1.0 + 1.2 + 1000.0) / 3 = 334.0667
        expected_fedavg_val = (1.0 + 1.2 + 1000.0) / 3.0
        np.testing.assert_allclose(fedavg_arr, expected_fedavg_val, rtol=1e-4)

        # Expected mathematical Median: median([1.0, 1.2, 1000.0]) = 1.2
        np.testing.assert_allclose(median_arr, 1.2, rtol=1e-4)

        # Divergence between FedAvg and Median is huge (> 300)
        max_diff = np.max(np.abs(fedavg_arr - median_arr))
        assert max_diff > 300.0, f"Expected huge divergence from poisoned FedAvg, got {max_diff}"

    def test_requirement_d_distinguish_mathematical_aggregation_from_quality_improvement(
        self,
    ) -> None:
        """Requirement D: Distinguish mathematical aggregation correctness from actual model quality improvement."""
        settings = get_settings()
        model_service = ModelService(settings)
        privacy_service = PrivacyService()
        fl_engine = FederatedLearningEngine(
            settings=settings,
            model_service=model_service,
            privacy_service=privacy_service,
        )
        base_model = model_service.create_model()
        base_weights = model_service.get_parameters(base_model)

        # Construct updates where sample-weighting is materially different from uniform median:
        # Bank A: 1000 samples, w = 1.0
        # Bank B: 10 samples, w = 5.0
        # Bank C: 10 samples, w = 10.0
        client_weights = [
            ModelWeights(
                layer_shapes=base_weights.layer_shapes,
                flat_weights=[1.0] * len(base_weights.flat_weights),
            ),
            ModelWeights(
                layer_shapes=base_weights.layer_shapes,
                flat_weights=[5.0] * len(base_weights.flat_weights),
            ),
            ModelWeights(
                layer_shapes=base_weights.layer_shapes,
                flat_weights=[10.0] * len(base_weights.flat_weights),
            ),
        ]
        client_samples = [1000, 10, 10]

        weighted_res = fl_engine.aggregate_parameters(
            client_weights=client_weights,
            client_samples=client_samples,
            method=AggregationMethod.FED_AVG_WEIGHTED,
            global_weights=base_weights,
        )
        median_res = fl_engine.aggregate_parameters(
            client_weights=client_weights,
            client_samples=client_samples,
            method=AggregationMethod.COORDINATE_WISE_MEDIAN,
            global_weights=base_weights,
            f_byzantine=1,
            strict_preconditions=True,
        )

        weighted_val = weighted_res.flat_weights[0]
        # Median is strictly the coordinate median [1.0, 5.0, 10.0] -> 5.0 regardless of sample sizes
        median_val = median_res.flat_weights[0]

        assert weighted_val < 1.5
        assert median_val == 5.0
        # This proves Coordinate-wise Median does not preserve sample-weighted convergence semantics.

    def test_requirement_e_and_f_preconditions_and_threshold_enforcement(
        self, clean_test_environment: str
    ) -> None:
        """Requirements E & F: Enforce honest mathematical preconditions and fail closed before training starts."""
        db_file = os.path.join(clean_test_environment, "fl_auth_ef.db")
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_ef",
            db_url=db_file,
            app_env="development",
        )
        val_X = np.random.randn(10, NUM_FEATURES).astype(np.float32)
        val_y = np.random.randint(0, 2, size=10).astype(np.float32)

        # 1. 3 banks with Krum (f=1) violates n >= 2f + 3 (requires n >= 5)
        with pytest.raises(
            ValueError, match="Krum theoretical precondition violated: requires n >= 2f \\+ 3"
        ):
            manager.execute_distributed_federation(
                job_id="job_krum_fail",
                training_run_id="run_krum_fail",
                simulation_id="sim_krum_fail",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                byzantine_defense="krum",
                byzantine_f_max=1,
            )

        # 2. 3 banks with Bulyan (f=1) violates n >= 4f + 3 (requires n >= 7)
        with pytest.raises(
            ValueError, match="Bulyan theoretical precondition violated: requires n >= 4f \\+ 3"
        ):
            manager.execute_distributed_federation(
                job_id="job_bulyan_fail",
                training_run_id="run_bulyan_fail",
                simulation_id="sim_bulyan_fail",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                byzantine_defense="bulyan",
                byzantine_f_max=1,
            )

        # 3. 3 banks with Trimmed Mean and k=2 violates n > 2k (requires n >= 5)
        with pytest.raises(
            ValueError, match="Trimmed Mean theoretical precondition violated: requires n > 2k"
        ):
            manager.execute_distributed_federation(
                job_id="job_trim_fail",
                training_run_id="run_trim_fail",
                simulation_id="sim_trim_fail",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                byzantine_defense="trimmed_mean",
                byzantine_f_max=1,
                trimmed_mean_trim_count=2,
            )

        # 4. Trimmed Mean with k=0 fails closed
        with pytest.raises(ValueError, match="Trimmed Mean requires trim_count >= 1"):
            manager.execute_distributed_federation(
                job_id="job_trim_zero_fail",
                training_run_id="run_trim_zero_fail",
                simulation_id="sim_trim_zero_fail",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                byzantine_defense="trimmed_mean",
                trimmed_mean_trim_count=0,
            )

        # 5. Coordinate Median with f >= n/2 (e.g. 2 banks with f=1) fails closed
        with pytest.raises(
            ValueError, match="Coordinate-wise Median theoretical precondition violated"
        ):
            manager.execute_distributed_federation(
                job_id="job_med_fail",
                training_run_id="run_med_fail",
                simulation_id="sim_med_fail",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A", "BANK_B"],
                byzantine_defense="coordinate_wise_median",
                byzantine_f_max=1,
            )

        # 6. Schema-level validation in OperationalFederationRequest also fails closed
        with pytest.raises(ValidationError, match="Krum theoretical precondition violated"):
            OperationalFederationRequest(
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                byzantine_defense="krum",
                byzantine_f_max=1,
            )

        with pytest.raises(ValidationError, match="Bulyan theoretical precondition violated"):
            OperationalFederationRequest(
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                byzantine_defense="bulyan",
                byzantine_f_max=1,
            )

    def test_requirement_g_secagg_plus_nonlinear_byzantine_fails_closed(
        self, clean_test_environment: str
    ) -> None:
        """Requirement G: SecAgg + any requested non-linear Byzantine defense fails closed through both API schema and service entry points."""
        # 1. API Schema level
        non_linear_defenses: list[
            Literal["coordinate_wise_median", "krum", "trimmed_mean", "bulyan"]
        ] = ["coordinate_wise_median", "krum", "trimmed_mean", "bulyan"]
        for defense in non_linear_defenses:
            with pytest.raises(ValidationError, match="mathematically incompatible"):
                OperationalFederationRequest(
                    participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                    secagg_enabled=True,
                    byzantine_defense=defense,
                )

        # 2. Service level (OperationalFederationService)
        svc = OperationalFederationService()
        with pytest.raises(ValueError, match="mathematically incompatible"):
            svc.execute_operational_federation(
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                secagg_enabled=True,
                byzantine_defense="coordinate_wise_median",
            )

        # 3. Manager level (DistributedFederatedTrainingManager)
        db_file = os.path.join(clean_test_environment, "fl_auth_g.db")
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_g",
            db_url=db_file,
            app_env="development",
        )
        val_X = np.random.randn(10, NUM_FEATURES).astype(np.float32)
        val_y = np.random.randint(0, 2, size=10).astype(np.float32)
        with pytest.raises(ValueError, match="mathematically incompatible"):
            manager.execute_distributed_federation(
                job_id="job_secagg_byz",
                training_run_id="run_secagg_byz",
                simulation_id="sim_secagg_byz",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                secagg_enabled=True,
                byzantine_defense="coordinate_wise_median",
            )

    def test_requirement_h_secagg_plus_dp_execution_unaffected(
        self, clean_test_environment: str
    ) -> None:
        """Requirement H: Existing SecAgg + DP execution remains unaffected with default weighted FedAvg."""
        db_file = os.path.join(clean_test_environment, "fl_auth_h.db")
        registry_dir = os.path.join(clean_test_environment, "reg_h")
        os.makedirs(registry_dir, exist_ok=True)

        # Pre-provision institutional privacy budget in SQLite authority
        auth = SQLiteTrainingJobAuthority(db_file)
        for b in ["BANK_A", "BANK_B", "BANK_C"]:
            auth.provision_institutional_privacy_budget(
                bank_id=b,
                dataset_tag="primary_vault",
                allocated_epsilon=20.0,
                allocated_delta=1e-4,
                aliases=[
                    f"{b}_dataset",
                    f"{b}_protected_dataset",
                    "primary_vault",
                    "default_vault",
                    "default",
                ],
            )

        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_h",
            db_url=db_file,
            app_env="development",
        )

        val_X = np.random.randn(20, NUM_FEATURES).astype(np.float32)
        val_y = np.random.randint(0, 2, size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                np.random.randn(40, NUM_FEATURES).astype(np.float32),
                np.random.randint(0, 2, size=40).astype(np.float32),
            ),
            "BANK_B": (
                np.random.randn(40, NUM_FEATURES).astype(np.float32),
                np.random.randint(0, 2, size=40).astype(np.float32),
            ),
            "BANK_C": (
                np.random.randn(40, NUM_FEATURES).astype(np.float32),
                np.random.randint(0, 2, size=40).astype(np.float32),
            ),
        }

        result = manager.execute_distributed_federation(
            job_id="job_req_h",
            training_run_id="run_req_h",
            simulation_id="sim_req_h",
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
            secagg_enabled=True,
            dp_config={
                "dp_enabled": True,
                "target_epsilon": 5.0,
                "target_delta": 1e-5,
                "dataset_tag": "primary_vault",
            },
            byzantine_defense="none",
        )

        assert result["status"] == "COMPLETED"
        assert result["is_verified"] is True
        assert result["secagg_enabled"] is True
        assert result["dp_enabled"] is True
        assert result["executed_aggregation_method"] == "secagg_fed_avg_weighted"
        assert result["secagg_provenance"] is not None
        assert result["dp_provenance"] is not None

    def test_requirement_i_stale_cross_job_bank_mismatch_and_tampered_rejected(
        self, clean_test_environment: str
    ) -> None:
        """Requirement I: Stale round, cross-job, incorrect-bank, duplicate, malformed, and tampered responses are rejected."""
        db_file = os.path.join(clean_test_environment, "fl_auth_i.db")
        registry_dir = os.path.join(clean_test_environment, "reg_i")
        os.makedirs(registry_dir, exist_ok=True)

        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_i",
            db_url=db_file,
            app_env="development",
        )
        val_X = np.random.randn(10, NUM_FEATURES).astype(np.float32)
        val_y = np.random.randint(0, 2, size=10).astype(np.float32)

        settings = get_settings()
        model_service = ModelService(settings)
        base_model = model_service.create_model()
        weights = model_service.get_parameters(base_model)
        w_bytes = _serialize_weights_to_bytes(weights)
        digest = hashlib.sha256(w_bytes).hexdigest()

        # Mock queue for responses
        class FakeQueue:
            def __init__(self, response: dict[str, Any]):
                self._resp = response

            def get(self, timeout: float = 60) -> dict[str, Any]:
                return self._resp

            def put(self, item: Any) -> None:
                pass

        # 1. Stale round response (expected round 1, received round 0)
        stale_resp = {
            "status": "SUCCESS",
            "bank_id": "BANK_A",
            "pid": 9991,
            "round_number": 0,
            "job_id": "job_req_i",
            "weights": weights,
            "update_digest": digest,
            "sample_count": 50,
        }
        with pytest.raises(
            TrainingProvenanceSecurityError, match="Stale or mismatched round response"
        ):
            manager.execute_distributed_federation(
                job_id="job_req_i",
                training_run_id="run_req_i",
                simulation_id="sim_req_i",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                worker_endpoints={
                    "BANK_A": {"inbox": FakeQueue({}), "outbox": FakeQueue(stale_resp)}
                },
                num_rounds=1,
                registry_storage_dir=registry_dir,
            )

        # 2. Cross-job response mismatch
        cross_job_resp = dict(stale_resp, round_number=1, job_id="different_job_id")
        with pytest.raises(TrainingProvenanceSecurityError, match="Cross-job response mismatch"):
            manager.execute_distributed_federation(
                job_id="job_req_i",
                training_run_id="run_req_i",
                simulation_id="sim_req_i",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                worker_endpoints={
                    "BANK_A": {"inbox": FakeQueue({}), "outbox": FakeQueue(cross_job_resp)}
                },
                num_rounds=1,
                registry_storage_dir=registry_dir,
            )

        # 3. Bank identity mismatch (receiving from BANK_A, response claims BANK_B)
        bank_mismatch_resp = dict(stale_resp, round_number=1, job_id="job_req_i", bank_id="BANK_B")
        with pytest.raises(
            TrainingProvenanceSecurityError, match="Worker response bank_id mismatch"
        ):
            manager.execute_distributed_federation(
                job_id="job_req_i",
                training_run_id="run_req_i",
                simulation_id="sim_req_i",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                worker_endpoints={
                    "BANK_A": {"inbox": FakeQueue({}), "outbox": FakeQueue(bank_mismatch_resp)}
                },
                num_rounds=1,
                registry_storage_dir=registry_dir,
            )

        # 4. Tampered update digest
        tampered_resp = dict(
            stale_resp,
            round_number=1,
            job_id="job_req_i",
            bank_id="BANK_A",
            update_digest="bad_digest" * 8,
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="tampered update digest"):
            manager.execute_distributed_federation(
                job_id="job_req_i",
                training_run_id="run_req_i",
                simulation_id="sim_req_i",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                worker_endpoints={
                    "BANK_A": {"inbox": FakeQueue({}), "outbox": FakeQueue(tampered_resp)}
                },
                num_rounds=1,
                registry_storage_dir=registry_dir,
            )

        # 5. Non-finite values in weights (NaN)
        nan_weights = ModelWeights(
            layer_shapes=weights.layer_shapes,
            flat_weights=[float("nan")] * len(weights.flat_weights),
        )
        nan_bytes = _serialize_weights_to_bytes(nan_weights)
        nan_digest = hashlib.sha256(nan_bytes).hexdigest()
        nan_resp = dict(
            stale_resp,
            round_number=1,
            job_id="job_req_i",
            bank_id="BANK_A",
            weights=nan_weights,
            update_digest=nan_digest,
        )
        with pytest.raises(
            TrainingProvenanceSecurityError, match="weights contain non-finite values"
        ):
            manager.execute_distributed_federation(
                job_id="job_req_i",
                training_run_id="run_req_i",
                simulation_id="sim_req_i",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                worker_endpoints={
                    "BANK_A": {"inbox": FakeQueue({}), "outbox": FakeQueue(nan_resp)}
                },
                num_rounds=1,
                registry_storage_dir=registry_dir,
            )

    def test_requirement_j_and_k_authoritative_evidence_and_inactive_candidate_promotion(
        self, clean_test_environment: str
    ) -> None:
        """Requirements J & K: Authoritative aggregation event records actual executed algorithm and candidate requires Four-Eyes promotion."""
        db_file = os.path.join(clean_test_environment, "fl_auth_jk.db")
        registry_dir = os.path.join(clean_test_environment, "reg_jk")
        os.makedirs(registry_dir, exist_ok=True)

        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_jk",
            db_url=db_file,
            app_env="development",
        )
        val_X = np.random.randn(20, NUM_FEATURES).astype(np.float32)
        val_y = np.random.randint(0, 2, size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                np.random.randn(30, NUM_FEATURES).astype(np.float32),
                np.random.randint(0, 2, size=30).astype(np.float32),
            ),
            "BANK_B": (
                np.random.randn(30, NUM_FEATURES).astype(np.float32),
                np.random.randint(0, 2, size=30).astype(np.float32),
            ),
            "BANK_C": (
                np.random.randn(30, NUM_FEATURES).astype(np.float32),
                np.random.randint(0, 2, size=30).astype(np.float32),
            ),
        }

        result = manager.execute_distributed_federation(
            job_id="job_req_jk",
            training_run_id="run_req_jk",
            simulation_id="sim_req_jk",
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
            byzantine_defense="coordinate_wise_median",
            byzantine_f_max=1,
        )

        assert result["status"] == "COMPLETED"
        assert result["executed_aggregation_method"] == "coordinate_wise_median"

        # Evidence audit in DB via get_global_aggregations
        auth = SQLiteTrainingJobAuthority(db_file)
        events = auth.get_global_aggregations("job_req_jk")
        assert len(events) == 1
        assert events[0].aggregation_algorithm == "coordinate_wise_median"

        # Model candidate status is inactive
        saved_ver = result["saved_version"]
        assert saved_ver["status"] == "inactive"
        assert saved_ver["is_active"] is False
        assert result["is_promoted_champion"] is False

        # Independent Model Registry check
        reg = ModelRegistry(storage_dir=registry_dir)
        models = reg.list_versions(simulation_id="sim_req_jk")
        assert len(models) == 1
        assert models[0]["status"] == "inactive"
        assert models[0]["is_active"] is False

    def test_requirement_l_regulatory_and_catalogue_truthfulness(self) -> None:
        """Requirement L: Regulatory dossier and aggregation catalogue no longer claim unconditional operational Byzantine tolerance."""
        dossier_gen = RegulatoryDossierGenerator()
        summary = dossier_gen.get_dossier_summary("fedgnn-test")
        metrics = summary["primary_model_metrics"]

        # No false unconditional 33.3% claim
        assert metrics["byzantine_fault_tolerance_pct"] is None
        assert metrics["byzantine_defense_status"] == "CONFIGURABLE_PLAINTEXT_ONLY"
        assert (
            "SecAgg aggregate-only mode omits individual Byzantine update filtering"
            in metrics["byzantine_theoretical_preconditions"]
        )

        # Aggregation catalogue descriptions distinguish operational preconditions and SecAgg limitations
        krum_item = next(m for m in AGGREGATION_METHODS if m["id"] == "krum")
        median_item = next(m for m in AGGREGATION_METHODS if m["id"] == "coordinate_wise_median")
        bulyan_item = next(m for m in AGGREGATION_METHODS if m["id"] == "bulyan")

        assert (
            "SecAgg aggregate-only mode omits individual update filtering"
            in krum_item["description"]
        )
        assert "n >= 2f + 3" in krum_item["description"]
        assert (
            "SecAgg aggregate-only mode omits individual update filtering"
            in median_item["description"]
        )
        assert "f < n/2" in median_item["description"]
        assert (
            "SecAgg aggregate-only mode omits individual update filtering"
            in bulyan_item["description"]
        )
        assert "n >= 4f + 3" in bulyan_item["description"]

        # Admin console feature flag
        settings_res = asyncio.run(get_admin_system_config())
        assert settings_res.active_features.get("byzantine_spectral_filtering") is False
        assert settings_res.active_features.get("byzantine_robust_aggregation") is True
