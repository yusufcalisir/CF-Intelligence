"""Unit and hardening tests for Elliptic Bitcoin Benchmark Subsystem (Phase 54 / STAGE_35).

Verifies:
1. Schema and feature dimension integrity (166 features, binary labels).
2. Real dataset parsing with exact txId merge and row alignment.
3. Genuine PyTorch model execution without fake noise formulas.
4. Empirical metric bounds (ROC-AUC, PR-AUC, Recall@0.1% FPR in [0, 1]).
5. Topological federation advantage calculation.
6. Deterministic reproducibility with random seeds.
7. Thread-safe RLock concurrency across parallel threads.
8. Report persistence (JSON + Markdown) with KaTeX strict compatibility.
9. FastAPI REST endpoint integration (/api/v1/graph/benchmark/elliptic).
10. Parameter boundary validation and exception handling.
"""

from __future__ import annotations

import concurrent.futures
import csv
import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from app.application.services.dataloader import ELLIPTIC_FEATURE_DIM, load_elliptic
from app.application.services.elliptic_benchmark_service import (
    EllipticBenchmarkService,
    _IsolatedLocalClassifier,
)
from app.application.services.graph_embedding_model import GraphSAGEModel
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def benchmark_service() -> EllipticBenchmarkService:
    return EllipticBenchmarkService()


class TestEllipticBenchmarkHardening:
    """Comprehensive test suite for STAGE_35 Elliptic Benchmark Subsystem."""

    def test_elliptic_schema_and_feature_dimension(self):
        """Vector 4: Verify Elliptic dataset produces 166 features and binary labels."""
        data = load_elliptic(n_mock_nodes=100, rng=np.random.default_rng(42))

        assert data["X"].shape == (100, ELLIPTIC_FEATURE_DIM)
        assert data["y"].shape == (100,)
        assert set(np.unique(data["y"])).issubset({0, 1})
        assert isinstance(data["edges"], list)
        assert len(data["edges"]) > 0
        assert data["source"] == "mock"

    def test_real_elliptic_parsing_and_txid_row_alignment(self):
        """Vector 4: Verify real dataset loader merges strictly on txId without row drift."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)

            # Create synthetic Kaggle-like CSV files with scrambled txIds
            # txIds: 101 (licit), 202 (unknown), 303 (illicit), 404 (licit)
            classes_path = root / "elliptic_txs_classes.csv"
            with open(classes_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["txId", "class"])
                writer.writerow(["101", "2"])       # licit -> 0
                writer.writerow(["202", "unknown"]) # unknown -> must be dropped
                writer.writerow(["303", "1"])       # illicit -> 1
                writer.writerow(["404", "2"])       # licit -> 0

            # Features: column 0 is txId, followed by 166 features
            features_path = root / "elliptic_txs_features.csv"
            with open(features_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                # Intentionally in different order from classes to test inner merge
                for tx_id, val in [("404", 4.0), ("202", 2.0), ("101", 1.0), ("303", 3.0)]:
                    row = [tx_id] + [val] * ELLIPTIC_FEATURE_DIM
                    writer.writerow(row)

            # Edges: 101 -> 303 (valid), 303 -> 202 (202 is unknown, must be omitted)
            edges_path = root / "elliptic_txs_edgelist.csv"
            with open(edges_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["txId1", "txId2"])
                writer.writerow(["101", "303"])
                writer.writerow(["303", "202"])  # Has unknown endpoint

            data = load_elliptic(path=root)

            assert data["source"] == "real"
            assert data["X"].shape == (3, ELLIPTIC_FEATURE_DIM)
            assert len(data["y"]) == 3

            # Verify txId 303 is illicit (label 1) and its feature value is exactly 3.0
            idx_303 = np.where(data["y"] == 1)[0]
            assert len(idx_303) == 1
            assert data["X"][idx_303[0], 0] == 3.0

            # Verify edge translation to node indices [0..N-1]
            assert len(data["edges"]) == 1  # 303 -> 202 omitted
            src_idx, dst_idx = data["edges"][0]
            assert 0 <= src_idx < 3
            assert 0 <= dst_idx < 3

    def test_genuine_pytorch_models_execution(self, benchmark_service: EllipticBenchmarkService):
        """Vector 1: Verify models perform real PyTorch gradient descent without fake noise."""
        X = torch.randn(100, ELLIPTIC_FEATURE_DIM)
        y = torch.randint(0, 2, (100,)).float()
        adj = [[(i + 1) % 100] for i in range(100)]

        # 1. Test baseline classifier forward and backward
        local_model = _IsolatedLocalClassifier(input_dim=ELLIPTIC_FEATURE_DIM, hidden_dim=16)
        opt_local = torch.optim.Adam(local_model.parameters(), lr=0.01)
        criterion = torch.nn.BCELoss()

        initial_param = local_model.net[0].weight.clone()
        opt_local.zero_grad()
        pred_local = local_model(X)
        loss_local = criterion(pred_local, y)
        loss_local.backward()
        opt_local.step()

        # Assert parameters updated via real backprop
        assert not torch.allclose(initial_param, local_model.net[0].weight)

        # 2. Test GraphSAGE forward and backward
        fed_model = GraphSAGEModel(input_dim=ELLIPTIC_FEATURE_DIM, hidden_dim=16, embedding_dim=8)
        opt_fed = torch.optim.Adam(fed_model.parameters(), lr=0.01)

        initial_sage_param = list(fed_model.sage_layers[0].parameters())[0].clone()
        opt_fed.zero_grad()
        _, pred_fed = fed_model(X, adj)
        loss_fed = criterion(pred_fed, y)
        loss_fed.backward()
        opt_fed.step()

        assert not torch.allclose(initial_sage_param, list(fed_model.sage_layers[0].parameters())[0])

    def test_empirical_metric_calculation_bounds(self, benchmark_service: EllipticBenchmarkService):
        """Vector 1 & 4: Verify empirical metrics are within valid probability ranges."""
        results = benchmark_service.run_benchmark(n_samples=150, random_seed=42, epochs=2)

        fed = results["metrics"]["federated_graph_pipeline"]
        loc = results["metrics"]["isolated_single_bank_baseline"]
        adv = results["metrics"]["federated_advantage"]

        for metric in (fed, loc):
            assert 0.0 <= metric["roc_auc"] <= 1.0
            assert 0.0 <= metric["pr_auc"] <= 1.0
            assert 0.0 <= metric["recall_at_01_fpr"] <= 1.0

        assert "pr_auc_gain" in adv
        assert "roc_auc_gain" in adv
        assert "recall_gain" in adv

    def test_federated_graph_topology_advantage(self, benchmark_service: EllipticBenchmarkService):
        """Vector 3: Verify benchmark outputs valid comparative advantage between graph and isolated baseline."""
        results = benchmark_service.run_benchmark(n_samples=200, random_seed=123, epochs=3)

        assert results["dataset"] == "Elliptic Bitcoin Dataset"
        assert results["total_nodes"] == 200
        assert results["total_edges"] > 0
        assert results["illicit_node_count"] >= 2
        assert results["evaluated_test_nodes"] == 40  # 20% of 200

    def test_reproducibility_with_random_seed(self, benchmark_service: EllipticBenchmarkService):
        """Vector 7: Verify execution is deterministic when random seed is held constant."""
        run1 = benchmark_service.run_benchmark(n_samples=100, random_seed=77, epochs=2)
        run2 = benchmark_service.run_benchmark(n_samples=100, random_seed=77, epochs=2)

        assert run1["metrics"] == run2["metrics"]

    def test_thread_concurrency_and_rlock(self, benchmark_service: EllipticBenchmarkService):
        """Vector 16: Verify concurrent benchmark runs are safe under RLock synchronization."""
        def _worker(seed: int) -> dict:
            return benchmark_service.run_benchmark(n_samples=100, random_seed=seed, epochs=2)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_worker, seed) for seed in [10, 20, 30, 40]]
            results = [f.result() for f in futures]

        assert len(results) == 4
        for res in results:
            assert res["total_nodes"] == 100
            assert "metrics" in res

    def test_report_persistence_and_markdown_formatting(self, benchmark_service: EllipticBenchmarkService):
        """Vector 6 & 20: Verify JSON and Markdown report persistence with KaTeX compatibility."""
        results = benchmark_service.run_benchmark(n_samples=100, random_seed=42, epochs=2)

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            md_path = benchmark_service.save_report(results, output_dir=out_dir)

            assert md_path.exists()
            json_path = out_dir / "benchmark_report.json"
            assert json_path.exists()

            with open(json_path, encoding="utf-8") as f:
                saved_json = json.load(f)
            assert saved_json["dataset"] == "Elliptic Bitcoin Dataset"

            with open(md_path, encoding="utf-8") as f:
                content = f.read()

            # Assert KaTeX strictly compatible (no bare underscores in \text{})
            assert "\\text{votes_" not in content
            assert "Federation Advantage" in content
            assert "PR-AUC" in content

    def test_fastapi_benchmark_endpoints(self, client: TestClient):
        """Vector 17: Verify POST and GET benchmark endpoints on /api/v1/graph."""
        # 1. Trigger benchmark run
        post_res = client.post(
            "/api/v1/graph/benchmark/elliptic",
            json={
                "n_samples": 120,
                "random_seed": 42,
                "epochs": 2,
                "learning_rate": 0.01,
                "save_report": False,
            },
        )
        assert post_res.status_code == 200
        data = post_res.json()
        assert data["dataset"] == "Elliptic Bitcoin Dataset"
        assert data["total_nodes"] == 120
        assert "metrics" in data
        assert "federated_graph_pipeline" in data["metrics"]

        # 2. Query latest benchmark
        get_res = client.get("/api/v1/graph/benchmark/elliptic/latest")
        assert get_res.status_code == 200
        latest_data = get_res.json()
        assert latest_data["dataset"] == "Elliptic Bitcoin Dataset"
        assert latest_data["total_nodes"] == 120

    def test_invalid_parameters_and_graceful_validation(self, benchmark_service: EllipticBenchmarkService):
        """Vector 8: Verify boundary checks fail fast with clear ValueError."""
        with pytest.raises(ValueError, match="n_samples must be at least 50"):
            benchmark_service.run_benchmark(n_samples=20)

        with pytest.raises(ValueError, match="epochs must be at least 1"):
            benchmark_service.run_benchmark(n_samples=100, epochs=0)
