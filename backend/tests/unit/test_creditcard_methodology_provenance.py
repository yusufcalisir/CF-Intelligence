"""Targeted unit tests for Credit Card FL methodology, budget equalization, and provenance.

Verifies:
1. Centralized epochs parameter is strictly honored by ComparativeCreditCardEvaluator and trainer.
2. Dataset SHA-256 provenance is computed from actual bytes, avoiding empty-string placeholders.
3. Generated experiment artifacts record unambiguous training budget metadata distinguishing
   centralized epochs from federated communication rounds and local epochs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from experiments.credit_card.run_creditcard_benchmark import (
    ComparativeCreditCardEvaluator,
    FederatedCreditCardTrainer,
    run_creditcard_benchmark,
)

from app.application.services.dataloader import compute_file_sha256, load_creditcard_fraud

EMPTY_STR_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestCreditCardMethodologyAndProvenance:
    """Verifies methodology integrity, budget control, and provenance traceability."""

    def test_centralized_epochs_parameter_is_honored(self) -> None:
        """Test A: Verify evaluate_centralized_pooled explicitly passes requested epochs to trainer."""
        trainer = FederatedCreditCardTrainer(in_features=30, local_epochs=2, batch_size=32, device="cpu")
        evaluator = ComparativeCreditCardEvaluator(trainer=trainer)

        rng = np.random.default_rng(42)
        X_mock = rng.standard_normal((64, 30)).astype(np.float32)
        y_mock = np.array([0] * 60 + [1] * 4, dtype=int)
        partitions = {"bank_a": (X_mock, y_mock)}
        X_test = rng.standard_normal((32, 30)).astype(np.float32)
        y_test = np.array([0] * 30 + [1] * 2, dtype=int)

        # Spy on trainer._train_client_local to check passed local_epochs
        with patch.object(trainer, "_train_client_local", wraps=trainer._train_client_local) as spy_train:
            res_5 = evaluator.evaluate_centralized_pooled(partitions, X_test, y_test, epochs=5)
            assert res_5["epochs"] == 5
            _, kwargs_5 = spy_train.call_args
            assert kwargs_5.get("local_epochs") == 5

        with patch.object(trainer, "_train_client_local", wraps=trainer._train_client_local) as spy_train:
            res_1 = evaluator.evaluate_centralized_pooled(partitions, X_test, y_test, epochs=1)
            assert res_1["epochs"] == 1
            _, kwargs_1 = spy_train.call_args
            assert kwargs_1.get("local_epochs") == 1

    def test_dataset_sha256_computation_and_provenance(self, tmp_path: Path) -> None:
        """Test B: Verify SHA-256 is computed from actual bytes and rejects empty-string placeholders."""
        # 1. Verify compute_file_sha256 against known payload
        sample_bytes = b"CF-Intelligence-CreditCard-Benchmark-Payload-2026"
        expected_hash = hashlib.sha256(sample_bytes).hexdigest()
        sample_file = tmp_path / "test_data.csv"
        sample_file.write_bytes(sample_bytes)

        computed_hash = compute_file_sha256(sample_file)
        assert computed_hash == expected_hash
        assert computed_hash != EMPTY_STR_SHA256

        # 2. Verify compute_file_sha256 fails clearly on missing file
        with pytest.raises(FileNotFoundError):
            compute_file_sha256(tmp_path / "non_existent_file.csv")

        # 3. Verify load_creditcard_fraud on real dataset returns valid hash if real file exists
        real_csv = Path("backend/storage/datasets/creditcard/creditcard.csv")
        if real_csv.is_file():
            loaded = load_creditcard_fraud(nrows=10, require_real=True)
            assert loaded["sha256_hash"] is not None
            assert len(loaded["sha256_hash"]) == 64
            assert loaded["sha256_hash"] != EMPTY_STR_SHA256
            assert loaded["file_path"] is not None
            assert Path(loaded["file_path"]).is_file()

    def test_metric_provenance_and_budget_distinction(self, tmp_path: Path) -> None:
        """Test C: Verify result artifacts distinguish centralized epochs from FL rounds x local epochs."""
        out_dir = tmp_path / "bench_provenance"
        res = run_creditcard_benchmark(
            nrows=400,
            rounds=3,
            local_epochs=2,
            centralized_epochs=6,
            evaluate_legacy_centralized=True,
            batch_size=32,
            output_dir=out_dir,
            seed=42,
        )
        assert res["experiment_result"].status == "COMPLETED"

        comp_path = out_dir / "comparative_baselines.json"
        assert comp_path.exists()
        comp_data = json.loads(comp_path.read_text(encoding="utf-8"))

        # Verify training budget metadata is present and distinct
        assert "training_budget" in comp_data
        budget = comp_data["training_budget"]
        assert budget["centralized_equalized"]["epochs"] == 6
        assert budget["centralized_equalized"]["dataset_passes"] == 6
        assert budget["centralized_legacy_2ep"]["epochs"] == 2
        assert budget["federated"]["rounds"] == 3
        assert budget["federated"]["local_epochs"] == 2
        assert budget["federated"]["effective_passes"] == 6
        assert budget["budget_equalized"] is True

        # Verify provenance section does not contain the empty-string placeholder
        assert "provenance" in comp_data
        assert comp_data["provenance"]["sha256_hash"] != EMPTY_STR_SHA256

        # Verify results.json hyperparameters record the exact configuration
        results_path = out_dir / "results.json"
        assert results_path.exists()
        results_data = json.loads(results_path.read_text(encoding="utf-8"))
        hyper = results_data["config"]["hyperparameters"]
        assert hyper["centralized_epochs"] == 6
        assert hyper["centralized_legacy_epochs"] == 2
        assert hyper["fl_rounds"] == 3
        assert hyper["fl_local_epochs"] == 2
        assert hyper["effective_dataset_passes"] == 6
        assert hyper["budget_equalized"] is True
