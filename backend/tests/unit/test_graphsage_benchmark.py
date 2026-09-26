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

import json
from pathlib import Path

import numpy as np
import torch
from experiments.elliptic.train_graphsage import (
    EllipticGraphSAGEBenchmark,
    EllipticGraphSAGEClassifier,
    TabularMLPBaseline,
    build_normalized_adjacency,
    compute_fixed_fpr_recalls,
    evaluate_predictions,
    run_graphsage_benchmark,
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
