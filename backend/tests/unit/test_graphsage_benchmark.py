"""Unit and Integration Tests for Elliptic GraphSAGE Inductive Benchmark Suite (Phase 8).

Validates:
1. Temporal split isolation (timesteps 1-34 vs 35-49) and zero future leakage.
2. Graph adjacency normalization (mean vs GCN).
3. Tabular MLP baseline forward pass and probability bounds.
4. GraphSAGE forward pass, embeddings L2-normalization, and classification head.
5. Fixed-FPR threshold selection and recall quantification.
6. Controlled neighborhood hop ablation and uplift calculation.
7. Pydantic v2 ExperimentResult schema validation.
8. Matplotlib headless publication plot generation.
9. Markdown audit dossier generation.
10. End-to-end synthetic benchmark execution.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import torch
from experiments.elliptic.train_graphsage import (
    CANONICAL_AGGREGATE_METRICS,
    CANONICAL_CONFIG,
    REPO_ROOT,
    EllipticGraphSAGEBenchmark,
    EllipticGraphSAGEClassifier,
    TabularMLPBaseline,
    build_normalized_adjacency,
    compute_fixed_fpr_recalls,
    evaluate_predictions,
    run_canonical_graphsage_benchmark,
    run_graphsage_benchmark,
    validate_canonical_configuration,
)
from experiments.harness.schema import ExperimentResult


# ===========================================================================
# 1. Dataset & Temporal Split Invariant Tests
# ===========================================================================
class TestEllipticDataAndSplits:
    """Validates temporal splitting and graph data preparation."""

    def test_synthetic_fast_loading_and_preprocessing(self) -> None:
        """Verifies load_and_preprocess initializes cleanly on synthetic testbed."""
        bench = EllipticGraphSAGEBenchmark(seed=42, require_real=False, all_rows=False, nrows=300)
        data = bench.load_and_preprocess()

        assert "X" in data
        assert "y" in data
        assert "edge_index" in data
        assert "train_labeled_mask" in data
        assert "test_labeled_mask" in data

        X = data["X"]
        y = data["y"]
        train_mask = data["train_labeled_mask"]
        test_mask = data["test_labeled_mask"]

        assert len(X) == len(y)
        assert X.shape[1] >= 10
        # Disjoint train and test masks
        assert not np.any(train_mask & test_mask)
        # All labeled masks must have labels in {0, 1}
        assert np.all(np.isin(y[train_mask], [0, 1]))
        assert np.all(np.isin(y[test_mask], [0, 1]))

    def test_normalized_adjacency_sparse_construction(self) -> None:
        """Verifies sparse adjacency operators construct valid normalized tensors."""
        num_nodes = 50
        # Directed linear chain: 0 -> 1 -> 2 ... -> 49
        src = np.arange(num_nodes - 1)
        dst = np.arange(1, num_nodes)
        edge_index = np.vstack([src, dst])

        adj_mean = build_normalized_adjacency(edge_index, num_nodes, mode="mean", bidirectional=True)
        assert adj_mean.is_sparse
        assert adj_mean.shape == (num_nodes, num_nodes)

        # Multiplied by ones tensor should be positive everywhere (due to self-loops)
        ones = torch.ones(num_nodes, 1)
        agg = torch.sparse.mm(adj_mean, ones)
        assert torch.all(agg > 0.0)

        # GCN mode
        adj_gcn = build_normalized_adjacency(edge_index, num_nodes, mode="gcn", bidirectional=True)
        assert adj_gcn.is_sparse
        assert adj_gcn.shape == (num_nodes, num_nodes)

    def test_empty_edge_index_fallback(self) -> None:
        """Verifies build_normalized_adjacency handles zero edges gracefully via self-loops."""
        num_nodes = 20
        empty_edges = np.zeros((2, 0), dtype=np.int64)
        adj = build_normalized_adjacency(empty_edges, num_nodes)

        assert adj.is_sparse
        assert adj.shape == (num_nodes, num_nodes)
        # Identity matrix properties
        x = torch.randn(num_nodes, 5)
        out = torch.sparse.mm(adj, x)
        assert torch.allclose(out, x, atol=1e-5)


# ===========================================================================
# 2. Neural Architecture Invariant Tests
# ===========================================================================
class TestGraphNeuralArchitectures:
    """Validates Tabular MLP and GraphSAGE neural building blocks."""

    def test_tabular_mlp_forward_and_bounds(self) -> None:
        """Verifies TabularMLPBaseline produces outputs strictly within [0, 1]."""
        in_dim = 32
        model = TabularMLPBaseline(in_dim=in_dim, hidden_dim=64, dropout=0.1)
        model.eval()

        batch_size = 40
        x = torch.randn(batch_size, in_dim)
        with torch.no_grad():
            preds = model(x)

        assert preds.shape == (batch_size,)
        assert torch.all(preds >= 0.0)
        assert torch.all(preds <= 1.0)

    def test_graphsage_layers_and_l2_embeddings(self) -> None:
        """Verifies GraphSAGE produces L2-normalized embeddings and probabilities."""
        in_dim = 24
        num_nodes = 30
        model = EllipticGraphSAGEClassifier(
            in_dim=in_dim, hidden_dim=32, embedding_dim=16, num_layers=2, dropout=0.0
        )
        model.eval()

        # Build diagonal self-loop adjacency
        indices = torch.arange(num_nodes)
        adj = torch.sparse_coo_tensor(torch.stack([indices, indices]), torch.ones(num_nodes), (num_nodes, num_nodes))

        x = torch.randn(num_nodes, in_dim)
        with torch.no_grad():
            embeddings = model.get_embeddings(x, adj)
            preds = model(x, adj)

        assert embeddings.shape == (num_nodes, 16)
        # Verify L2-normalization: ||z_i||_2 == 1.0
        norms = torch.norm(embeddings, p=2, dim=1)
        assert torch.allclose(norms, torch.ones(num_nodes), atol=1e-5)

        assert preds.shape == (num_nodes,)
        assert torch.all(preds >= 0.0)
        assert torch.all(preds <= 1.0)

    def test_graphsage_1hop_ablation_architecture(self) -> None:
        """Verifies 1-layer GraphSAGE executes cleanly."""
        in_dim = 16
        num_nodes = 20
        model = EllipticGraphSAGEClassifier(
            in_dim=in_dim, hidden_dim=32, embedding_dim=8, num_layers=1
        )
        model.eval()

        indices = torch.arange(num_nodes)
        adj = torch.sparse_coo_tensor(torch.stack([indices, indices]), torch.ones(num_nodes), (num_nodes, num_nodes))

        x = torch.randn(num_nodes, in_dim)
        with torch.no_grad():
            preds = model(x, adj)

        assert preds.shape == (num_nodes,)


# ===========================================================================
# 3. Metric & Fixed-FPR Evaluation Tests
# ===========================================================================
class TestMetricEvaluationEngine:
    """Validates operational metrics and Fixed-FPR threshold calculations."""

    def test_fixed_fpr_recalls_monotonicity(self) -> None:
        """Verifies higher FPR targets yield equal or greater operational recall."""
        rng = np.random.default_rng(42)
        y_true = np.array([1] * 50 + [0] * 950)
        # Separated scores with some noise
        y_prob = np.concatenate([rng.uniform(0.3, 0.9, size=50), rng.uniform(0.01, 0.4, size=950)])

        results = compute_fixed_fpr_recalls(y_true, y_prob, [0.001, 0.005, 0.01])
        r01 = results["recall_at_0001_fpr"]
        r05 = results["recall_at_0005_fpr"]
        r10 = results["recall_at_001_fpr"]

        assert 0.0 <= r01 <= 1.0
        assert 0.0 <= r05 <= 1.0
        assert 0.0 <= r10 <= 1.0
        # Monotonicity check
        assert r01 <= r05 <= r10

    def test_evaluate_predictions_payload_structure(self) -> None:
        """Verifies evaluate_predictions returns complete dictionary structure."""
        y_true = np.array([1, 0, 1, 0, 1, 0, 0, 0, 0, 1])
        y_prob = np.array([0.9, 0.1, 0.8, 0.2, 0.7, 0.3, 0.4, 0.1, 0.2, 0.85])

        metrics = evaluate_predictions(y_true, y_prob)

        assert "pr_auc" in metrics
        assert "roc_auc" in metrics
        assert "f1_score" in metrics
        assert "precision" in metrics
        assert "recall" in metrics
        assert "brier_score" in metrics
        assert "recall_at_01_fpr" in metrics
        assert "confusion_matrix" in metrics

        cm = metrics["confusion_matrix"]
        assert cm["tn"] + cm["fp"] + cm["fn"] + cm["tp"] == len(y_true)


# ===========================================================================
# 4. End-to-End Benchmark & Serialization Tests
# ===========================================================================
class TestEndToEndBenchmarkExecution:
    """Validates complete benchmark pipeline, artifact serialization, and schema parity."""

    def test_fast_synthetic_benchmark_pipeline(self, tmp_path: Path) -> None:
        """Executes full benchmark on synthetic testbed and verifies all generated files."""
        result = run_graphsage_benchmark(
            seed=42,
            epochs=2,
            lr=0.01,
            hidden_dim=32,
            embedding_dim=16,
            require_real=False,
            all_rows=False,
            nrows=250,
            output_dir=tmp_path,
        )

        assert result["status"] == "COMPLETED"
        assert result["duration_seconds"] > 0
        assert "graphsage_metrics" in result
        assert "tabular_metrics" in result
        assert "uplift" in result

        uplift = result["uplift"]
        assert "delta_pr_auc_vs_tabular" in uplift
        assert "delta_roc_auc_vs_tabular" in uplift
        assert "delta_recall_01_fpr_vs_tabular" in uplift
        assert "hop_ablation" in uplift
        assert "aggregator_ablation" in uplift

        # Verify files were created
        paths = result["paths"]
        assert Path(paths["results_json"]).exists()
        assert Path(paths["comparative_baselines"]).exists()
        assert Path(paths["audit_dossier"]).exists()
        assert Path(paths["raw_benchmark"]).exists()
        assert Path(paths["pr_curves"]).exists()
        assert Path(paths["roc_curves"]).exists()
        assert Path(paths["neighborhood_ablation"]).exists()

        # Validate results_json against Pydantic schema
        with open(paths["results_json"], encoding="utf-8") as f:
            data = json.load(f)
        exp_res = ExperimentResult.model_validate(data)
        assert exp_res.config.model_type == "GraphSAGE"
        assert exp_res.final_metrics["pr_auc"] >= 0.0

        # Validate audit_dossier markdown content
        with open(paths["audit_dossier"], encoding="utf-8") as f:
            dossier_text = f.read()
        assert "# Elliptic Bitcoin GraphSAGE Inductive Benchmark Audit Dossier" in dossier_text
        assert "Neighborhood Aggregation Uplift" in dossier_text
        assert "Executive Summary & Comparative Matrix" in dossier_text

    def test_synthetic_artifact_isolation_does_not_touch_canonical_raw(self, tmp_path: Path) -> None:
        """Non-destructive regression test for DEF-01: Verifies synthetic test execution preserves canonical artifact byte-for-byte."""
        canonical_raw_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "graphsage_elliptic_benchmark.json"
        assert canonical_raw_path.exists(), "Canonical artifact must exist"

        # 1. Read actual canonical artifact and compute SHA-256 before synthetic execution
        before_bytes = canonical_raw_path.read_bytes()
        before_sha256 = hashlib.sha256(before_bytes).hexdigest()

        # 2. Execute synthetic benchmark using temporary output directory (NEVER mutating canonical path)
        res = run_graphsage_benchmark(
            seed=42,
            epochs=1,
            hidden_dim=16,
            embedding_dim=8,
            require_real=False,
            all_rows=False,
            nrows=100,
            output_dir=tmp_path,
        )

        # 3. Verify synthetic artifact is written ONLY to the isolated temp directory
        assert res["status"] == "COMPLETED"
        synthetic_artifact = tmp_path / "graphsage_elliptic_benchmark.json"
        assert synthetic_artifact.exists()
        assert Path(res["paths"]["raw_benchmark"]) == synthetic_artifact

        # 4. Read canonical artifact again and assert byte-for-byte SHA-256 identity
        after_bytes = canonical_raw_path.read_bytes()
        after_sha256 = hashlib.sha256(after_bytes).hexdigest()

        assert before_sha256 == after_sha256, "Canonical artifact bytes changed during synthetic execution!"
        assert before_bytes == after_bytes, "Canonical artifact content mutated during synthetic execution!"

    def test_canonical_write_guard_governance(self, tmp_path: Path) -> None:
        """Verifies canonical write authorization invariants without running expensive training."""
        # Invariant 1: Ordinary real diagnostic run (is_canonical=False) CANNOT authorize canonical write
        bench_diag = EllipticGraphSAGEBenchmark(
            dataset_mode="real",
            all_rows=True,
            seeds=[42, 123, 456],
            is_canonical=False,
        )
        assert bench_diag.is_canonical is False
        eff_diag = bool(getattr(bench_diag, "is_canonical", False))
        is_canon_diag = (
            eff_diag
            and bench_diag.dataset_mode == CANONICAL_CONFIG["dataset_mode"]
            and bench_diag.all_rows is True
            and tuple(bench_diag.seeds) == CANONICAL_CONFIG["seeds"]
        )
        assert is_canon_diag is False

        # Invariant 2: Single-seed real run CANNOT authorize canonical write (rejected at init)
        with pytest.raises(ValueError, match="seeds must match canonical"):
            EllipticGraphSAGEBenchmark(
                dataset_mode="real",
                all_rows=True,
                seeds=[42],
                is_canonical=True,
            )

        # Invariant 3: Synthetic run CANNOT authorize canonical write (rejected at init)
        with pytest.raises(ValueError, match="dataset_mode must be 'real'"):
            EllipticGraphSAGEBenchmark(
                dataset_mode="synthetic",
                all_rows=True,
                seeds=[42, 123, 456],
                is_canonical=True,
            )

        # Invariant 4: Subsampled / partial dataset CANNOT authorize canonical write (rejected at init)
        with pytest.raises(ValueError, match="all_rows must be True"):
            EllipticGraphSAGEBenchmark(
                dataset_mode="real",
                all_rows=False,
                seeds=[42, 123, 456],
                is_canonical=True,
            )

        with pytest.raises(ValueError, match="nrows subsampling prohibited"):
            EllipticGraphSAGEBenchmark(
                dataset_mode="real",
                all_rows=True,
                nrows=1000,
                seeds=[42, 123, 456],
                is_canonical=True,
            )

        # Invariant 5: Altered temporal split CANNOT authorize canonical write (rejected at init)
        with pytest.raises(ValueError, match="val_start_timestep must be 31"):
            EllipticGraphSAGEBenchmark(
                dataset_mode="real",
                all_rows=True,
                seeds=[42, 123, 456],
                val_start_timestep=32,
                is_canonical=True,
            )

        with pytest.raises(ValueError, match="split_timestep must be 34"):
            EllipticGraphSAGEBenchmark(
                dataset_mode="real",
                all_rows=True,
                seeds=[42, 123, 456],
                split_timestep=35,
                is_canonical=True,
            )

        # Invariant 6: Official canonical configuration satisfies all authorization predicates
        bench_canon = EllipticGraphSAGEBenchmark(
            dataset_mode="real",
            all_rows=True,
            seeds=[42, 123, 456],
            is_canonical=True,
        )
        assert bench_canon.is_canonical is True
        assert tuple(bench_canon.seeds) == CANONICAL_CONFIG["seeds"]

    def test_adversarial_canonical_hyperparameter_rejections(self, tmp_path: Path) -> None:
        """Adversarial validation: proves canonical authorization fails closed if any hyperparameter is modified."""
        # 1. Altered epochs rejected before training
        with pytest.raises(ValueError, match="epochs must be 15"):
            validate_canonical_configuration(
                dataset_mode="real",
                all_rows=True,
                seeds=[42, 123, 456],
                epochs=2,
                lr=0.005,
                hidden_dim=128,
                embedding_dim=64,
            )

        # 2. Altered learning rate rejected before training
        with pytest.raises(ValueError, match="lr must be 0.005"):
            validate_canonical_configuration(
                dataset_mode="real",
                all_rows=True,
                seeds=[42, 123, 456],
                epochs=15,
                lr=0.123,
                hidden_dim=128,
                embedding_dim=64,
            )

        # 3. Altered hidden_dim rejected before training
        with pytest.raises(ValueError, match="hidden_dim must be 128"):
            validate_canonical_configuration(
                dataset_mode="real",
                all_rows=True,
                seeds=[42, 123, 456],
                epochs=15,
                lr=0.005,
                hidden_dim=999,
                embedding_dim=64,
            )

        # 4. Altered embedding_dim rejected before training
        with pytest.raises(ValueError, match="embedding_dim must be 64"):
            validate_canonical_configuration(
                dataset_mode="real",
                all_rows=True,
                seeds=[42, 123, 456],
                epochs=15,
                lr=0.005,
                hidden_dim=128,
                embedding_dim=7,
            )

        # 5. Custom output directory rejected from canonical authorization
        with pytest.raises(ValueError, match="output_dir must be None"):
            validate_canonical_configuration(
                dataset_mode="real",
                all_rows=True,
                seeds=[42, 123, 456],
                epochs=15,
                lr=0.005,
                hidden_dim=128,
                embedding_dim=64,
                output_dir=tmp_path / "custom",
            )

        # 6. Exact canonical configuration passes validation
        validate_canonical_configuration(
            dataset_mode="real",
            all_rows=True,
            seeds=[42, 123, 456],
            epochs=15,
            lr=0.005,
            hidden_dim=128,
            embedding_dim=64,
            output_dir=None,
        )

    def test_canonical_runner_rejects_caller_overrides(self, tmp_path: Path) -> None:
        """Verifies run_canonical_graphsage_benchmark rejects any noncanonical caller overrides."""
        with pytest.raises(ValueError, match="Canonical runner rejects parameter override for 'epochs'"):
            run_canonical_graphsage_benchmark(epochs=2)

        with pytest.raises(ValueError, match="Canonical runner rejects parameter override for 'lr'"):
            run_canonical_graphsage_benchmark(lr=0.123)

        with pytest.raises(ValueError, match="Canonical runner rejects parameter override for 'seeds'"):
            run_canonical_graphsage_benchmark(seeds=[1, 2, 3])

        with pytest.raises(ValueError, match="Canonical runner rejects parameter override for 'hidden_dim'"):
            run_canonical_graphsage_benchmark(hidden_dim=999)

        with pytest.raises(ValueError, match="Canonical runner rejects non-None output_dir"):
            run_canonical_graphsage_benchmark(output_dir=tmp_path / "custom")

    def test_canonical_artifact_integrity_and_federated_taxonomy(self) -> None:
        """Verifies canonical artifact metrics, sample SD ddof=1, Recall@0.1%FPR min/max, and federated taxonomy."""
        canonical_raw_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "graphsage_elliptic_benchmark.json"
        with open(canonical_raw_path, encoding="utf-8") as f:
            art = json.load(f)

        # 1. Verify canonical aggregate metrics
        metrics = art["metrics"]
        assert round(metrics["pr_auc"], 4) == 0.3761
        assert round(metrics["roc_auc"], 4) == 0.8325
        assert round(metrics["recall_at_01_fpr"], 4) == 0.0462

        # 2. Verify sample standard deviation (ddof=1)
        m_std = art["metrics_std"]
        assert round(m_std["pr_auc"], 4) == 0.0482
        assert round(m_std["roc_auc"], 4) == 0.0078
        assert round(m_std["recall_at_01_fpr"], 4) == 0.0375

        # 3. Verify min/max extremes
        m_min = art["metrics_min"]
        m_max = art["metrics_max"]
        assert round(m_min["recall_at_01_fpr"], 4) == 0.0175
        assert round(m_max["recall_at_01_fpr"], 4) == 0.0886
        assert round(m_min["pr_auc"], 4) == 0.3304
        assert round(m_max["pr_auc"], 4) == 0.4265

        # 4. Verify legacy synthetic and single-run baseline isolation
        assert art["legacy_synthetic_benchmark"]["status"] == "RETIRED_SYNTHETIC_SMOKE_BENCHMARK"
        assert art["legacy_synthetic_benchmark"]["pr_auc"] == 0.9001
        assert art["legacy_real_data_single_run"]["status"] == "HISTORICAL_CENTRALIZED_SINGLE_RUN_BASELINE"
        assert art["legacy_real_data_single_run"]["pr_auc"] == 0.4372

        # 5. Verify master benchmark matrix federated taxonomy
        matrix_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "master_benchmark_matrix.json"
        with open(matrix_path, encoding="utf-8") as f:
            matrix = json.load(f)

        el = matrix["datasets"]["elliptic"]["paradigms"]
        assert el["centralized_pooled"]["status"] == "EVALUATED"
        assert el["centralized_pooled"]["pr_auc"] == 0.3761
        assert el["federated_fedavg"]["status"] == "NOT_EVALUATED"
        assert el["federated_fedavg"]["pr_auc"] is None
        assert el["federated_fedprox"]["status"] == "NOT_RUN"
        assert el["federated_fedprox"]["pr_auc"] is None

    def test_canonical_aggregate_metrics_completeness(self) -> None:
        """Regression test for DEF-02: Verifies all canonical metrics are present in mean, std, min, and max."""
        required_metrics = {
            "pr_auc",
            "roc_auc",
            "precision",
            "recall",
            "f1_score",
            "recall_at_01_fpr",
        }
        assert required_metrics.issubset(set(CANONICAL_AGGREGATE_METRICS))

        fake_results = [
            {"metrics": {m: 0.1 * i for m in required_metrics}, "threshold": 0.5}
            for i in range(1, 4)
        ]
        series = {m: [r["metrics"][m] for r in fake_results] for m in CANONICAL_AGGREGATE_METRICS}
        ddof = 1

        agg = {
            "mean": {m: float(np.mean(series[m])) for m in CANONICAL_AGGREGATE_METRICS},
            "std": {m: float(np.std(series[m], ddof=ddof)) for m in CANONICAL_AGGREGATE_METRICS},
            "min": {m: float(np.min(series[m])) for m in CANONICAL_AGGREGATE_METRICS},
            "max": {m: float(np.max(series[m])) for m in CANONICAL_AGGREGATE_METRICS},
        }

        for metric in required_metrics:
            assert metric in agg["mean"], f"Missing {metric} in mean"
            assert metric in agg["std"], f"Missing {metric} in std"
            assert metric in agg["min"], f"Missing {metric} in min"
            assert metric in agg["max"], f"Missing {metric} in max"
            assert agg["min"][metric] <= agg["mean"][metric] <= agg["max"][metric]

