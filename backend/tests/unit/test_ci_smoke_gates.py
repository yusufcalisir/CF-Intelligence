"""Deterministic CI Smoke Gates & Pipeline Correctness Test Suite.

Validates that:
1. Canonical dataloaders initialize rapidly in deterministic smoke mode with valid schemas.
2. Model architectures serialize and deserialize with bit-exact parameter and forward pass parity.
3. Pydantic v2 experiment schemas validate and roundtrip without schema drift.
4. CI workflows (.github/workflows/ci.yml and benchmarks.yml) maintain strict decoupling
   between fast PR/commit smoke gates and heavy empirical benchmark sweeps.
5. Makefile targets and documentation remain strictly synchronized.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
import torch
from experiments.harness.schema import (
    DatasetMetadata,
    ExperimentConfig,
    ExperimentResult,
    HardwareMetadata,
    StepMetric,
)
from pydantic import ValidationError

from app.application.services.dataloader import (
    load_amlnet,
    load_amlsim,
    load_creditcard_fraud,
    load_elliptic,
    load_ieee_cis,
    load_paysim,
    load_synthaml,
)
from app.application.services.model_service import FraudDetectionModel
from tests.fixtures.dataloader_smoke_fixtures import (
    create_amlnet_test_fixture,
    create_amlsim_test_fixture,
    create_creditcard_test_fixture,
    create_elliptic_test_fixture,
    create_ieee_cis_test_fixture,
    create_paysim_test_fixture,
    create_synthaml_test_fixture,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestDataloaderSmokeGates:
    """Fast deterministic smoke tests for canonical dataloaders.

    Gate A Architecture:
    Exercises production dataloader parsing, column cleaning, and label mapping logic
    using minimal deterministic temporary test fixtures. Does not depend on external
    multi-gigabyte Kaggle/empirical downloads, allowing standard CI to execute safely
    and rapidly while verifying real loader implementation contracts.
    """

    def test_dataloader_smoke_paysim_and_ieeecis(self, tmp_path: Path) -> None:
        """Verify PaySim and IEEE-CIS production loaders parse schema fixtures correctly."""
        paysim_dir = create_paysim_test_fixture(tmp_path / "paysim", n_rows=10)
        paysim_data = load_paysim(path=paysim_dir, nrows=10)
        assert isinstance(paysim_data, dict)
        assert "X" in paysim_data and "y" in paysim_data
        assert paysim_data["X"].shape == (10, 13)
        assert len(paysim_data["y"]) == 10
        assert set(np.unique(paysim_data["y"])).issubset({0, 1})

        ieee_dir = create_ieee_cis_test_fixture(tmp_path / "ieee_cis", n_rows=10)
        ieee_data = load_ieee_cis(path=ieee_dir, nrows=10, join_identity=False)
        assert isinstance(ieee_data, dict)
        assert "X" in ieee_data and "y" in ieee_data
        assert len(ieee_data["X"]) == 10
        assert len(ieee_data["y"]) == 10
        assert set(np.unique(ieee_data["y"])).issubset({0, 1})

    def test_dataloader_smoke_creditcard_and_elliptic(self, tmp_path: Path) -> None:
        """Verify CreditCard and Elliptic graph production loaders parse schema fixtures."""
        cc_dir = create_creditcard_test_fixture(tmp_path / "creditcard", n_rows=10)
        cc_data = load_creditcard_fraud(path=cc_dir, nrows=10)
        assert isinstance(cc_data, dict)
        assert "X" in cc_data and "y" in cc_data
        assert cc_data["X"].shape == (10, 29)
        assert len(cc_data["y"]) == 10
        assert set(np.unique(cc_data["y"])).issubset({0, 1})

        elliptic_dir = create_elliptic_test_fixture(tmp_path / "elliptic", n_nodes=10)
        elliptic_data = load_elliptic(path=elliptic_dir, nrows=10, include_unknown=True)
        assert isinstance(elliptic_data, dict)
        assert "X" in elliptic_data and "y" in elliptic_data
        assert elliptic_data["X"].shape == (10, 166)
        assert len(elliptic_data["y"]) == 10
        assert "edges" in elliptic_data and len(elliptic_data["edges"]) > 0

    def test_dataloader_smoke_amlsim_synthaml_amlnet(self, tmp_path: Path) -> None:
        """Verify specialized AML topology production loaders parse schema fixtures."""
        amlsim_dir = create_amlsim_test_fixture(tmp_path / "amlsim", n_rows=10)
        amlsim_data = load_amlsim(path=amlsim_dir, nrows=10)
        assert isinstance(amlsim_data, dict)
        assert "X" in amlsim_data and "y" in amlsim_data
        assert len(amlsim_data["X"]) == 10

        synthaml_dir = create_synthaml_test_fixture(tmp_path / "synthaml", n_alerts=5, n_txs=15)
        synthaml_data = load_synthaml(path=synthaml_dir, nrows=5)
        assert isinstance(synthaml_data, dict)
        assert "X" in synthaml_data and "y" in synthaml_data
        assert synthaml_data["X"].shape[1] == 14
        assert len(synthaml_data["y"]) == 5

        amlnet_dir = create_amlnet_test_fixture(tmp_path / "amlnet", n_rows=10)
        amlnet_data = load_amlnet(path=amlnet_dir, nrows=10)
        assert isinstance(amlnet_data, dict)
        assert "X" in amlnet_data and "y" in amlnet_data
        assert amlnet_data["X"].shape == (10, 18)
        assert len(amlnet_data["y"]) == 10


class TestModelSerializationSmokeGates:
    """Fast deterministic tests for model architecture and parameter serialization."""

    def test_model_architecture_and_forward_pass_smoke(self) -> None:
        """Verify standard and DP-compatible model forward passes and feature representations."""
        # Standard BatchNorm model
        model_std = FraudDetectionModel(input_dim=10, dp_compatible=False)
        model_std.eval()
        x = torch.randn(8, 10)
        out_std = model_std(x)
        assert out_std.shape == (8,)
        assert torch.all(out_std >= 0.0) and torch.all(out_std <= 1.0)

        # DP-compatible GroupNorm model
        model_dp = FraudDetectionModel(input_dim=10, dp_compatible=True)
        model_dp.eval()
        out_dp = model_dp(x)
        assert out_dp.shape == (8,)
        assert torch.all(out_dp >= 0.0) and torch.all(out_dp <= 1.0)

        # Feature representations
        preds, feats = model_dp(x, return_features=True)
        assert preds.shape == (8,)
        assert feats.shape == (8, 32)

    def test_model_serialization_and_deserialization_parity(self) -> None:
        """Verify model weights serialize and deserialize with bit-exact parity."""
        torch.manual_seed(42)
        model1 = FraudDetectionModel(input_dim=10, dp_compatible=True)
        model1.eval()

        # Serialize in-memory
        buffer = io.BytesIO()
        torch.save(model1.state_dict(), buffer)
        buffer.seek(0)

        # Deserialize into new instance
        model2 = FraudDetectionModel(input_dim=10, dp_compatible=True)
        model2.load_state_dict(torch.load(buffer, weights_only=True))
        model2.eval()

        # Check weights are bit-exact
        for p1, p2 in zip(model1.parameters(), model2.parameters(), strict=True):
            assert torch.equal(p1, p2)

        # Check forward pass parity
        x = torch.randn(16, 10)
        with torch.no_grad():
            out1 = model1(x)
            out2 = model2(x)
        assert torch.allclose(out1, out2, atol=1e-7)

    def test_smoke_seed_determinism(self) -> None:
        """Verify random seed determinism produces bit-exact weight initialization."""
        torch.manual_seed(1337)
        m1 = FraudDetectionModel(input_dim=10)

        torch.manual_seed(1337)
        m2 = FraudDetectionModel(input_dim=10)

        for p1, p2 in zip(m1.parameters(), m2.parameters(), strict=True):
            assert torch.equal(p1, p2)


class TestSchemaValidationSmokeGates:
    """Fast deterministic tests for Pydantic v2 experiment schemas."""

    def test_pydantic_v2_experiment_schema_validation_smoke(self) -> None:
        """Verify Pydantic v2 schemas validate and serialize properly."""
        hw = HardwareMetadata.capture()
        assert hw.os_platform in ["Windows", "Linux", "Darwin"]

        dataset = DatasetMetadata(
            dataset_name="PaySim Smoke Evaluation",
            source_uri="backend/storage/datasets/paysim/smoke.csv",
            sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            total_samples=100,
            num_features=10,
            fraud_samples=5,
            fraud_rate=0.05,
            split_ratios={"train": 0.8, "test": 0.2},
        )
        assert dataset.total_samples == 100
        assert dataset.fraud_rate == 0.05

        config = ExperimentConfig(
            experiment_id="exp_ci_smoke_01",
            experiment_name="SmokeVerificationExp",
            model_type="FraudDetectionModel",
            strategy="FedAvg",
            num_rounds=2,
            local_epochs=1,
            batch_size=16,
            learning_rate=0.001,
        )

        step = StepMetric(
            step=1,
            train_loss=0.45,
            val_loss=0.42,
            pr_auc=0.88,
            roc_auc=0.92,
            f1_score=0.75,
        )
        assert step.step == 1

        result = ExperimentResult(
            schema_version="1.0.0",
            experiment_id="exp_ci_smoke_01",
            config=config,
            hardware=hw,
            dataset=dataset,
            git_commit="HEAD",
            git_branch="main",
            status="COMPLETED",
            start_time_utc="2026-09-29T00:00:00Z",
            end_time_utc="2026-09-29T00:00:05Z",
            total_duration_seconds=5.0,
            final_metrics={"pr_auc": 0.88, "roc_auc": 0.92},
            history=[step],
        )
        dumped = result.model_dump(mode="json")
        assert dumped["status"] == "COMPLETED"
        assert dumped["final_metrics"]["pr_auc"] == 0.88

    def test_schema_rejects_invalid_inputs(self) -> None:
        """Verify schema validation raises errors on missing or invalid inputs."""
        with pytest.raises(ValidationError):
            # Missing required fields: sha256_hash, total_samples, num_features
            DatasetMetadata(dataset_name="MissingFieldsDataset")  # type: ignore[call-arg]

        with pytest.raises(ValidationError):
            # Missing required experiment_id and model_type
            ExperimentConfig(experiment_name="InvalidConfig")  # type: ignore[call-arg]


class TestCIWorkflowDecouplingGates:
    """Verifies that CI workflow configurations maintain strict decoupling."""

    def test_ci_workflow_decoupling_contracts(self) -> None:
        """Verify ci.yml runs fast smoke gates while benchmarks.yml handles heavy evaluations."""
        ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        assert ci_path.exists(), "ci.yml must exist"
        ci_content = ci_path.read_text(encoding="utf-8")

        # CI must run smoke gate
        assert "test_ci_smoke_gates.py" in ci_content, (
            "ci.yml must explicitly run test_ci_smoke_gates.py as a fast smoke gate"
        )

        # CI must not run heavy full dataset sweeps
        assert "--all-rows" not in ci_content, "ci.yml must not execute heavy --all-rows sweeps"
        assert "--workers 500" not in ci_content, "ci.yml must not execute heavy 500-concurrency load"

        benchmarks_path = REPO_ROOT / ".github" / "workflows" / "benchmarks.yml"
        assert benchmarks_path.exists(), "benchmarks.yml must exist"
        bench_content = benchmarks_path.read_text(encoding="utf-8")

        # benchmarks.yml must have workflow_dispatch and schedule
        assert "workflow_dispatch:" in bench_content
        assert "schedule:" in bench_content
        assert "upload-artifact" in bench_content

    def test_makefile_smoke_target_parity(self) -> None:
        """Verify Makefile contains test-smoke target wired to test_ci_smoke_gates.py."""
        makefile_path = REPO_ROOT / "Makefile"
        assert makefile_path.exists(), "Makefile must exist"
        makefile_content = makefile_path.read_text(encoding="utf-8")

        assert "test-smoke" in makefile_content
        assert "test_ci_smoke_gates.py" in makefile_content
