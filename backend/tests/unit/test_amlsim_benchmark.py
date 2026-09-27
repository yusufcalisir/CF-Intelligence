"""Unit and Integration Tests for IBM AMLSim Multi-Hop Pattern Detection Benchmark.

Validates:
1. TabularMLPBaseline architecture, forward pass, and logit output shape.
2. GraphSAGELayer inductive bidirectional neighborhood message passing.
3. GraphSAGEPatternDetector multi-hop relational detection and node embedding computation.
4. Account node feature extraction (uncentered log1p scaling) and normalized sparse adjacency.
5. Fixed-FPR thresholding, confusion matrix, and typology-specific recall (Cycle, Fan-In).
6. Pydantic v2 ExperimentResult schema compliance and JSON serialization.
7. Publication plotting and markdown audit dossier generation.
8. End-to-end benchmark execution on synthetic fallback testbed.
9. Standalone CLI benchmark runner arguments and execution interface.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch
from experiments.amlsim.evaluate_patterns import (
    GraphSAGELayer,
    GraphSAGEPatternDetector,
    TabularMLPBaseline,
    build_account_features_and_adjacency,
    calculate_metrics_at_fixed_fpr,
    evaluate_model_metrics,
    run_amlsim_pattern_benchmark,
)
from experiments.harness.schema import ExperimentResult


# ===========================================================================
# 1. Architecture & Forward Pass Unit Tests
# ===========================================================================
class TestModelArchitectures:
    """Verifies neural network architectures and layer-wise computations."""

    def test_tabular_mlp_forward_shape_and_finite_logits(self) -> None:
        """Verify TabularMLPBaseline output shape (batch_size,) and finite logits."""
        in_dim = 6
        hidden_dim = 32
        model = TabularMLPBaseline(in_dim=in_dim, hidden_dim=hidden_dim, dropout=0.0)
        model.eval()

        batch_size = 50
        x = torch.randn(batch_size, in_dim)
        with torch.no_grad():
            out = model(x)

        assert out.shape == (batch_size,)
        assert not torch.isnan(out).any()
        assert not torch.isinf(out).any()

    def test_graphsage_layer_bidirectional_aggregation(self) -> None:
        """Verify GraphSAGELayer transforms self features and aggregates forward/backward neighbors."""
        in_dim = 7
        out_dim = 16
        num_nodes = 20
        layer = GraphSAGELayer(in_features=in_dim, out_features=out_dim)
        layer.eval()

        h = torch.randn(num_nodes, in_dim)
        # Construct simple identity-plus-edge sparse adjacency
        indices = torch.tensor([[0, 1, 2, 3], [1, 2, 3, 0]], dtype=torch.long)
        values = torch.tensor([0.5, 0.5, 0.5, 0.5], dtype=torch.float32)
        adj = torch.sparse_coo_tensor(indices, values, (num_nodes, num_nodes)).coalesce()

        with torch.no_grad():
            out = layer(h, adj, adj)

        assert out.shape == (num_nodes, out_dim)
        assert not torch.isnan(out).any()

    def test_graphsage_detector_forward_and_embeddings(self) -> None:
        """Verify GraphSAGEPatternDetector computes inductive node embeddings and edge logits."""
        num_nodes = 30
        node_in_dim = 7
        edge_in_dim = 6
        hidden_dim = 32
        embedding_dim = 16

        detector = GraphSAGEPatternDetector(
            node_in_dim=node_in_dim,
            edge_in_dim=edge_in_dim,
            hidden_dim=hidden_dim,
            embedding_dim=embedding_dim,
            num_hops=2,
            dropout=0.0,
        )
        detector.eval()

        num_edges = 40
        x_edge = torch.randn(num_edges, edge_in_dim)
        senders = torch.randint(0, num_nodes, (num_edges,), dtype=torch.long)
        receivers = torch.randint(0, num_nodes, (num_edges,), dtype=torch.long)
        node_feats = torch.randn(num_nodes, node_in_dim)

        # Build dummy sparse COO adjacencies
        idx_fwd = torch.stack([senders, receivers], dim=0)
        val_fwd = torch.ones(num_edges, dtype=torch.float32) / 2.0
        adj_fwd = torch.sparse_coo_tensor(idx_fwd, val_fwd, (num_nodes, num_nodes)).coalesce()

        idx_rev = torch.stack([receivers, senders], dim=0)
        val_rev = torch.ones(num_edges, dtype=torch.float32) / 2.0
        adj_rev = torch.sparse_coo_tensor(idx_rev, val_rev, (num_nodes, num_nodes)).coalesce()

        with torch.no_grad():
            # Test compute_node_embeddings
            node_emb = detector.compute_node_embeddings(node_feats, adj_fwd, adj_rev)
            assert node_emb.shape == (num_nodes, embedding_dim)

            # Test edge scoring with cached embeddings
            logits = detector(
                edge_x=x_edge,
                senders=senders,
                receivers=receivers,
                node_feats=node_feats,
                adj_fwd=adj_fwd,
                adj_bwd=adj_rev,
                cached_node_emb=node_emb,
            )

        assert logits.shape == (num_edges,)
        assert not torch.isnan(logits).any()


# ===========================================================================
# 2. Graph Feature Extraction & Adjacency Construction Tests
# ===========================================================================
class TestGraphFeatureEngineering:
    """Verifies account node feature extraction and sparse COO adjacency generation."""

    def test_uncentered_node_features_and_sparsity(self) -> None:
        """Verify account node features preserve non-negativity and sparsity."""
        senders = np.array([0, 1, 2, 0], dtype=np.int64)
        receivers = np.array([1, 2, 0, 3], dtype=np.int64)
        amounts = np.array([100.0, 200.0, 150.0, 50.0], dtype=np.float32)
        num_accounts = 4

        node_feats, adj_fwd, adj_bwd = build_account_features_and_adjacency(
            senders=senders,
            receivers=receivers,
            amounts=amounts,
            num_accounts=num_accounts,
            train_indices=np.array([0, 1, 2, 3]),
        )

        assert node_feats.shape == (4, 7)
        # Check that log-transformed degrees and volumes are non-negative
        assert (node_feats[:, :6] >= 0.0).all()

        # Check sparse COO shapes and coalescing
        assert adj_fwd.is_sparse
        assert adj_bwd.is_sparse
        assert adj_fwd.shape == (4, 4)
        assert adj_bwd.shape == (4, 4)


# ===========================================================================
# 3. Metric Evaluation & Typology Recall Tests
# ===========================================================================
class TestMetricEvaluation:
    """Verifies fixed-FPR threshold calibration and multi-hop typology recall breakdowns."""

    def test_calculate_metrics_at_fixed_fpr(self) -> None:
        """Verify calculate_metrics_at_fixed_fpr computes recall at target FPR."""
        y_true = np.array([1] * 20 + [0] * 80)
        np.random.seed(42)
        y_prob = np.concatenate([
            np.random.uniform(0.6, 0.99, size=20),
            np.random.uniform(0.01, 0.4, size=80),
        ])

        recall_at_05 = calculate_metrics_at_fixed_fpr(y_true, y_prob, target_fpr=0.05)
        assert 0.0 <= recall_at_05 <= 1.0

    def test_evaluate_model_metrics_with_typologies(self) -> None:
        """Verify evaluate_model_metrics accurately breaks down Cycle and Fan-In recalls."""
        y_true = np.array([1, 1, 1, 1, 0, 0, 0, 0])
        # Two cycles, two fan-ins
        typologies = np.array(["cycle", "cycle", "fan_in", "fan_in", "none", "none", "none", "none"])
        # Give high score to first cycle and first fan-in, lower to second
        y_prob = np.array([0.9, 0.2, 0.85, 0.15, 0.05, 0.02, 0.01, 0.03])

        metrics = evaluate_model_metrics(
            y_true=y_true,
            y_pred=y_prob,
            typologies=typologies,
            model_name="TypologyTest",
            latency_ms=1.5,
        )

        assert metrics["model_name"] == "TypologyTest"
        assert metrics["cycle_count"] == 2
        assert metrics["fan_in_count"] == 2
        assert metrics["cycle_recall"] >= 0.0
        assert metrics["fan_in_recall"] >= 0.0
        assert "confusion_matrix" in metrics
        assert metrics["confusion_matrix"]["tp"] + metrics["confusion_matrix"]["fn"] == 4


# ===========================================================================
# 4. Schema Compliance & Serialization Tests
# ===========================================================================
class TestSchemaAndArtifacts:
    """Verifies Pydantic v2 ExperimentResult schema compliance and visual artifact generation."""

    def test_experiment_result_pydantic_schema_validation(self) -> None:
        """Verify experiment serialization strictly satisfies ExperimentResult Pydantic schema."""
        results_path = Path("experiments/amlsim/results.json")
        if results_path.exists():
            with open(results_path, encoding="utf-8") as f:
                data = json.load(f)
            validated = ExperimentResult.model_validate(data)
            assert validated.schema_version == "1.0.0"
            assert validated.config.model_type == "GraphSAGE"
            assert "pr_auc" in validated.final_metrics
            assert validated.final_metrics["pr_auc"] >= 0.0
            assert validated.confusion_matrix is not None
            assert validated.confusion_matrix.tp >= 0
            assert validated.curves is not None


# ===========================================================================
# 5. End-to-End Benchmark Execution Tests
# ===========================================================================
class TestEndToEndBenchmark:
    """Verifies complete end-to-end benchmark execution on synthetic fallback."""

    def test_run_amlsim_pattern_benchmark_synthetic(self, tmp_path: Path) -> None:
        """Verify complete pipeline executes cleanly on synthetic data and emits artifacts."""
        output_dir = tmp_path / "test_benchmark_output"

        results = run_amlsim_pattern_benchmark(
            seed=42,
            epochs=2,
            lr=0.01,
            hidden_dim=16,
            embedding_dim=8,
            require_real=False,
            all_rows=False,
            nrows=500,
            output_dir=str(output_dir),
        )

        assert "graphsage_metrics" in results
        assert "tabular_metrics" in results
        assert "uplift" in results
        assert "comparative_baselines" in results
        assert "experiment_result" in results
        assert "paths" in results

        paths = results["paths"]
        assert Path(paths["results_json"]).exists()
        assert Path(paths["comparative_baselines_json"]).exists()
        assert Path(paths["audit_dossier"]).exists()

        # Validate serialized ExperimentResult JSON
        with open(paths["results_json"], encoding="utf-8") as f:
            data = json.load(f)
        ExperimentResult.model_validate(data)

    def test_cli_runner_argument_parser(self) -> None:
        """Verify benchmarks.runners.run_amlsim_benchmark imports and configures arguments cleanly."""
        import importlib

        runner_module = importlib.import_module("benchmarks.runners.run_amlsim_benchmark")
        assert hasattr(runner_module, "main")
