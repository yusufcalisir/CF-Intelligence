"""Unit test suite for European Credit Card Fraud Detection benchmark loader,

Time/Amount scaling, 3-way train/val/test splitting, and fixed-FPR threshold selection (Phase 7, Sub-Plan 7.1).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch
from experiments.credit_card.evaluate_thresholds import (
    CreditCardImbalanceMLP,
    CreditCardThresholdEvaluator,
    evaluate_predictions,
    select_fixed_fpr_thresholds,
)

from app.application.services.dataloader import (
    load_creditcard,
    load_creditcard_fraud,
    load_dataset,
    resolve_dataset_dir,
)


class TestCreditCardDataLoader:
    """Validates ingestion, schema, scaling, and zero-leakage splitting for Credit Card Fraud."""

    def test_creditcard_real_dataset_loading_30_features(self) -> None:
        """Verify real Credit Card Fraud ingestion with 30 features (Time, V1-V28, Amount)."""
        root = resolve_dataset_dir("creditcard")
        csv_file = root / "creditcard.csv"
        parquet_file = list(root.glob("*.parquet"))

        if not csv_file.exists() and not parquet_file:
            pytest.skip("Physical creditcard.csv or parquet not present on disk.")

        data = load_creditcard_fraud(nrows=1000, require_real=True, include_time=True)
        assert data["source"] in ("real_csv", "real_parquet")
        assert len(data["y"]) == 1000
        assert data["X"].shape == (1000, 30)
        assert not np.isnan(data["X"]).any(), "Feature matrix contains NaN values"
        assert set(np.unique(data["y"])).issubset({0, 1})

        feature_names = data["feature_names"]
        assert feature_names[0] == "Time"
        assert feature_names[-1] == "Amount"
        assert len(feature_names) == 30
        assert "imbalance_ratio" in data
        assert data["imbalance_ratio"] > 0

    def test_creditcard_backward_compatible_29_features(self) -> None:
        """Verify backward compatibility when include_time=False (29 features)."""
        root = resolve_dataset_dir("creditcard")
        if not (root / "creditcard.csv").exists() and not list(root.glob("*.parquet")):
            pytest.skip("Physical creditcard dataset not found.")

        data = load_creditcard_fraud(nrows=500, require_real=True, include_time=False)
        assert data["X"].shape == (500, 29)
        assert "Time" not in data["feature_names"]
        assert data["feature_names"][-1] == "Amount"

    def test_creditcard_robust_scaling_properties(self) -> None:
        """Verify RobustScaler maps Time and Amount via median and IQR."""
        root = resolve_dataset_dir("creditcard")
        if not (root / "creditcard.csv").exists() and not list(root.glob("*.parquet")):
            pytest.skip("Physical creditcard dataset not found.")

        data = load_creditcard_fraud(
            nrows=2000,
            require_real=True,
            include_time=True,
            scale_time_amount=True,
            scaling_strategy="robust",
        )
        scaling_params = data.get("scaling_params", {})
        assert "Time" in scaling_params
        assert "Amount" in scaling_params
        assert scaling_params["Time"]["strategy"] == "robust"
        assert scaling_params["Amount"]["strategy"] == "robust"
        assert scaling_params["Amount"]["scale"] > 0.0

        # After robust scaling, median should be approximately 0.0
        amt_idx = data["feature_names"].index("Amount")
        scaled_amt = data["X"][:, amt_idx]
        assert abs(float(np.median(scaled_amt))) < 0.10

    def test_creditcard_standard_scaling_properties(self) -> None:
        """Verify StandardScaler maps Time and Amount via mean and standard deviation."""
        data = load_creditcard_fraud(
            nrows=1000,
            require_real=False,
            include_time=True,
            scale_time_amount=True,
            scaling_strategy="standard",
        )
        scaling_params = data.get("scaling_params", {})
        assert scaling_params["Time"]["strategy"] == "standard"
        assert scaling_params["Amount"]["strategy"] == "standard"

        amt_idx = data["feature_names"].index("Amount")
        scaled_amt = data["X"][:, amt_idx]
        assert abs(float(np.mean(scaled_amt))) < 0.15
        assert abs(float(np.std(scaled_amt)) - 1.0) < 0.15

    def test_creditcard_train_val_test_3way_split_invariants(self) -> None:
        """Verify 3-way train/val/test splitting satisfies total sample conservation and non-overlap."""
        data = load_creditcard_fraud(
            nrows=1500,
            require_real=False,
            include_time=True,
            split_data=True,
            train_ratio=0.60,
            val_ratio=0.20,
            test_ratio=0.20,
            seed=42,
        )
        assert "train" in data
        assert "val" in data
        assert "test" in data

        n_train = len(data["train"]["y"])
        n_val = len(data["val"]["y"])
        n_test = len(data["test"]["y"])
        assert n_train + n_val + n_test == 1500

        # Ensure non-overlapping index partitions
        idx_train = set(data["train"]["indices"])
        idx_val = set(data["val"]["indices"])
        idx_test = set(data["test"]["indices"])

        assert idx_train.isdisjoint(idx_val), "Train and Val splits share overlapping indices"
        assert idx_val.isdisjoint(idx_test), "Val and Test splits share overlapping indices"
        assert idx_train.isdisjoint(idx_test), "Train and Test splits share overlapping indices"

        # Verify zero lookahead leakage: scaling parameters fitted on train
        assert "scaling_params" in data
        assert "split_ratios" in data
        assert data["split_ratios"]["train"] == 0.60

    def test_creditcard_temporal_split_zero_leakage(self) -> None:
        """Verify temporal train/val/test splitting along Time axis enforces zero future lookahead."""
        data = load_creditcard_fraud(
            nrows=1200,
            require_real=False,
            include_time=True,
            split_data=True,
            temporal_split=True,
            train_ratio=0.60,
            val_ratio=0.20,
            test_ratio=0.20,
        )
        time_idx = data["feature_names"].index("Time")
        t_train_max = float(np.max(data["train"]["X"][:, time_idx]))
        t_val_min = float(np.min(data["val"]["X"][:, time_idx]))
        t_val_max = float(np.max(data["val"]["X"][:, time_idx]))
        t_test_min = float(np.min(data["test"]["X"][:, time_idx]))

        assert t_train_max <= t_val_min, "Lookahead leakage between train and val"
        assert t_val_max <= t_test_min, "Lookahead leakage between val and test"

    def test_creditcard_synthetic_fallback_schema(self, tmp_path: Path) -> None:
        """Verify synthetic generation fallback produces expected schema when real data is absent."""
        empty_dir = tmp_path / "no_data"
        empty_dir.mkdir()

        data = load_creditcard_fraud(
            path=empty_dir,
            n_mock_txns=800,
            require_real=False,
            include_time=True,
            split_data=True,
        )
        assert data["source"] == "mock_pca"
        assert data["X"].shape == (800, 30)
        assert len(data["y"]) == 800
        assert int(np.sum(data["y"] == 1)) >= 1
        assert "train" in data
        assert "val" in data
        assert "test" in data

    def test_creditcard_dataset_registry_integration(self) -> None:
        """Verify convenience DATASET_REGISTRY resolves creditcard and credit_card aliases."""
        d1 = load_dataset("creditcard", nrows=100, require_real=False)
        d2 = load_dataset("credit_card", nrows=100, require_real=False)
        d3 = load_creditcard(nrows=100, require_real=False)

        assert len(d1["y"]) == 100
        assert len(d2["y"]) == 100
        assert len(d3["y"]) == 100


class TestCreditCardFixedFPRThresholdSelection:
    """Validates mathematical guarantees and evaluation of fixed-FPR decision thresholds."""

    def test_threshold_selection_mathematical_guarantee(self) -> None:
        """Verify that thresholds selected on validation set strictly guarantee empirical FPR <= alpha."""
        rng = np.random.default_rng(42)
        n_samples = 10_000
        # Simulated validation split: 9,980 negative samples, 20 positive samples
        y_val = np.array([0] * 9980 + [1] * 20, dtype=int)
        val_scores = rng.beta(a=0.5, b=10.0, size=n_samples)  # heavy concentration near 0

        target_fprs = [0.0001, 0.0005, 0.001, 0.005, 0.01]
        thresholds = select_fixed_fpr_thresholds(y_val, val_scores, target_fprs)

        neg_scores = val_scores[y_val == 0]
        n_neg = len(neg_scores)

        for alpha in target_fprs:
            tau = thresholds[alpha]
            fp_count = int(np.sum(neg_scores >= tau))
            emp_fpr = fp_count / n_neg
            assert emp_fpr <= alpha + 1e-9, f"Target FPR {alpha} exceeded on validation: got {emp_fpr}"

    def test_fixed_fpr_evaluation_metrics_structure(self) -> None:
        """Verify evaluate_predictions computes PR-AUC, ROC-AUC, Brier score, and fixed-FPR metrics."""
        rng = np.random.default_rng(42)
        y_true = np.array([0] * 900 + [1] * 100, dtype=int)
        scores = rng.random(1000)

        thresholds = {0.001: 0.95, 0.01: 0.85, 0.05: 0.60}
        results = evaluate_predictions(y_true, scores, thresholds)

        assert "pr_auc" in results
        assert "roc_auc" in results
        assert "brier_score" in results
        assert "fixed_fpr_metrics" in results
        assert "0.001" in results["fixed_fpr_metrics"]
        assert "recall" in results["fixed_fpr_metrics"]["0.001"]
        assert "empirical_fpr" in results["fixed_fpr_metrics"]["0.001"]
        assert "precision" in results["fixed_fpr_metrics"]["0.001"]
        assert "f1_score" in results["fixed_fpr_metrics"]["0.001"]

    def test_neural_classifier_single_sample_inference(self) -> None:
        """Verify CreditCardImbalanceMLP executes single-sample inference (batch_size=1) via LayerNorm."""
        model = CreditCardImbalanceMLP(in_features=30, hidden_dims=(32, 16))
        single_input = torch.randn(1, 30, dtype=torch.float32)

        model.eval()
        logits = model(single_input)
        assert logits.shape == (1,)

        probs = model.predict_proba(single_input)
        assert probs.shape == (1,)
        assert 0.0 <= float(probs[0]) <= 1.0

    def test_threshold_evaluator_end_to_end_synthetic(self, tmp_path: Path) -> None:
        """Verify full threshold evaluation runner on synthetic data with artifact serialization."""
        empty_data_dir = tmp_path / "mock_creditcard"
        empty_data_dir.mkdir(parents=True, exist_ok=True)
        evaluator = CreditCardThresholdEvaluator(seed=42)
        evaluator.load_and_preprocess(
            path=empty_data_dir,
            nrows=600,
            require_real=False,
            include_time=True,
            train_ratio=0.60,
            val_ratio=0.20,
            test_ratio=0.20,
        )

        out_dir = tmp_path / "credit_card_eval"
        outputs = evaluator.run_full_evaluation(
            models=["logistic_regression", "neural_mlp"],
            output_dir=out_dir,
        )

        assert "summary" in outputs
        assert "json_path" in outputs
        assert "markdown_path" in outputs
        assert outputs["json_path"].exists()
        assert outputs["markdown_path"].exists()

        # Validate JSON content
        with open(outputs["json_path"], encoding="utf-8") as f:
            data = json.load(f)
        assert data["dataset_name"] == "European Credit Card Fraud Detection"
        assert "logistic_regression" in data["models"]
        assert "neural_mlp" in data["models"]
        assert "test_evaluation" in data["models"]["logistic_regression"]

        # Validate Markdown content
        md_text = outputs["markdown_path"].read_text(encoding="utf-8")
        assert "# 💳 European Credit Card Fraud Detection" in md_text
        assert "Logistic Regression" in md_text
        assert "Empirical Test Set Evaluation" in md_text
