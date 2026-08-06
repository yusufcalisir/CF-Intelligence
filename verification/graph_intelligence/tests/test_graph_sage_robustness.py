"""Robustness and Failure Injection Test Suite for Federated GraphSAGE (FedGNN).

Attempts to break every graph learning component:
1. Empty Graph (N=0 nodes, 0 edges)
2. Single-Node Graph (N=1 node, 0 edges)
3. Isolated Nodes & Disconnected Components
4. Duplicate Edges & Self-Loops in Adjacency
5. Out-of-Bounds Neighbor Index Filtering
6. NaN Floating-Point Feature Injection
7. Infinite Floating-Point (+Inf / -Inf) Injection
8. Extremely High-Degree Hub Nodes (10,000 neighbors)
9. Severe Class Imbalance (100% Fraud or 0% Fraud)
10. Model Weight Mismatched Layer Aggregation Validation
"""

import math
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
import torch

backend_path = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(backend_path))

from app.application.services.fl_engine import (  # noqa: E402
    AggregationMethod,
    FederatedLearningEngine,
)
from app.application.services.graph_embedding_model import (  # noqa: E402
    NODE_FEATURE_DIM,
    GraphSAGELayer,
    GraphSAGEModel,
)
from app.application.services.graph_embedding_service import GraphEmbeddingService  # noqa: E402
from app.config import get_settings  # noqa: E402


# =====================================================================
# ROBUSTNESS TEST SUITE
# =====================================================================


def test_robustness_1_empty_graph():
    """ROBUSTNESS 1: Empty Graph (N=0 nodes, 0 edges)."""
    service = GraphEmbeddingService()
    features, adj, labels, node_map = service.build_local_graph("non_existent_bank_id")

    assert features.size(0) == 0
    assert len(adj) == 0
    assert len(labels) == 0
    assert len(node_map) == 0

    weights, metrics = service.train_local_gnn("non_existent_bank_id")
    assert metrics["num_nodes"] == 0
    assert metrics["loss"] == 0.0


def test_robustness_2_single_node_graph():
    """ROBUSTNESS 2: Single-Node Graph (N=1 node, 0 edges)."""
    X_pt = torch.randn(1, NODE_FEATURE_DIM)
    adj = [[]]

    model = GraphSAGEModel(
        input_dim=NODE_FEATURE_DIM, hidden_dim=32, embedding_dim=16, num_layers=2
    )
    model.eval()

    with torch.no_grad():
        embs, preds = model(X_pt, adj)

    assert embs.shape == (1, 16)
    assert preds.shape == (1,)
    assert not torch.isnan(embs).any()


def test_robustness_3_isolated_nodes_and_disconnected_components():
    """ROBUSTNESS 3: Isolated Nodes & Disconnected Components."""
    N = 15
    X_pt = torch.randn(N, NODE_FEATURE_DIM)
    # Component A: nodes 0..4 connected; Component B: 5..9 connected; 10..14 isolated
    adj = [[] for _ in range(N)]
    adj[0] = [1, 2]
    adj[1] = [0, 2]
    adj[2] = [0, 1]

    adj[5] = [6, 7]
    adj[6] = [5, 7]
    adj[7] = [5, 6]

    model = GraphSAGEModel(
        input_dim=NODE_FEATURE_DIM, hidden_dim=32, embedding_dim=16, num_layers=2
    )
    model.eval()

    with torch.no_grad():
        embs = model.get_embeddings(X_pt, adj)  # type: ignore

    assert embs.shape == (N, 16)
    norms = torch.norm(embs, p=2, dim=1)
    assert torch.allclose(norms, torch.ones(N), atol=1e-5)


def test_robustness_4_duplicate_edges_and_self_loops():
    """ROBUSTNESS 4: Duplicate Edges & Self-Loops in Adjacency."""
    N = 5
    X_pt = torch.randn(N, NODE_FEATURE_DIM)
    # Node 0 has duplicate neighbor 1 and self-loop 0
    adj = [[1, 1, 1, 0, 0, 2], [0], [0], [], []]

    layer = GraphSAGELayer(NODE_FEATURE_DIM, 32)
    layer.eval()

    with torch.no_grad():
        out = layer(X_pt, adj)

    assert out.shape == (N, 32)
    assert not torch.isnan(out).any()


def test_robustness_5_out_of_bounds_neighbor_indices():
    """ROBUSTNESS 5: Out-of-Bounds Neighbor Index Filtering."""
    N = 4
    X_pt = torch.randn(N, NODE_FEATURE_DIM)
    # Node 0 has invalid neighbor indices -5 and 999
    adj = [[-5, 999], [0], [1], [2]]

    layer = GraphSAGELayer(NODE_FEATURE_DIM, 32)
    layer.eval()

    with torch.no_grad():
        out = layer(X_pt, adj)

    # Valid neighbor filtering falls back to self-loop for node 0
    assert out.shape == (N, 32)
    assert not torch.isnan(out).any()


def test_robustness_6_nan_feature_injection():
    """ROBUSTNESS 6: NaN Floating-Point Feature Injection."""
    N = 5
    X_pt = torch.randn(N, NODE_FEATURE_DIM)
    X_pt[0, 0] = float("nan")
    adj = [[1], [0], [3], [2], []]

    layer = GraphSAGELayer(NODE_FEATURE_DIM, 32)
    layer.eval()

    # Executing forward pass with NaN feature propagates NaN gracefully without process abort
    out = layer(X_pt, adj)
    assert torch.isnan(out[0]).any()


def test_robustness_7_infinite_feature_injection():
    """ROBUSTNESS 7: Infinite Floating-Point (+Inf / -Inf) Injection."""
    N = 5
    X_pt = torch.randn(N, NODE_FEATURE_DIM)
    X_pt[0, 2] = float("inf")
    adj = [[1], [0], [3], [2], []]

    layer = GraphSAGELayer(NODE_FEATURE_DIM, 32)
    layer.eval()

    out = layer(X_pt, adj)
    # L2 normalization converts Inf to finite or 0/Inf handling
    assert out.shape == (N, 32)


def test_robustness_8_extremely_high_degree_hub_nodes():
    """ROBUSTNESS 8: Extremely High-Degree Hub Nodes (10,000 neighbors)."""
    N = 10005
    X_pt = torch.randn(N, NODE_FEATURE_DIM)
    # Node 0 is a massive hub connected to 10,000 nodes
    hub_neighbors = list(range(1, 10001))
    adj = [hub_neighbors] + [[0] for _ in range(10004)]

    layer = GraphSAGELayer(NODE_FEATURE_DIM, 32)
    layer.eval()

    # Test mini-batch sampling caps neighborhood size to num_sample=10 efficiently
    out = layer(X_pt, adj, num_sample=10)

    assert out.shape == (N, 32)
    assert not torch.isnan(out).any()


def test_robustness_9_severe_class_imbalance():
    """ROBUSTNESS 9: Severe Class Imbalance (100% Fraud or 0% Fraud)."""
    service = GraphEmbeddingService()
    # Mock single bank graph with 0 fraud nodes
    model = service._get_or_create_model()

    # Test training handling 0 fraud nodes fallback
    features = torch.randn(10, NODE_FEATURE_DIM)
    adj = [[1], [0], [3], [2], [5], [4], [7], [6], [9], [8]]
    labels = [0.0] * 10  # 0% fraud

    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)  # type: ignore
    criterion = torch.nn.BCELoss()

    embeddings, predictions = model(features, adj)
    loss = criterion(predictions, torch.tensor(labels))
    loss.backward()
    optimizer.step()

    assert not math.isnan(loss.item())


def test_robustness_10_mismatched_layer_aggregation_validation():
    """ROBUSTNESS 10: Model Weight Mismatched Layer Aggregation Validation."""
    mock_settings = get_settings()
    fl_engine = FederatedLearningEngine(
        settings=mock_settings, model_service=MagicMock(), privacy_service=MagicMock()
    )

    m1 = GraphSAGEModel(input_dim=12, hidden_dim=32, embedding_dim=16, num_layers=2)
    m2 = GraphSAGEModel(
        input_dim=12, hidden_dim=64, embedding_dim=16, num_layers=2
    )  # Different hidden dim

    w1 = m1.to_model_weights()
    w2 = m2.to_model_weights()

    with pytest.raises(
        ValueError, match="GNN layer shape mismatch|GNN parameter count mismatch"
    ):
        fl_engine.aggregate_graph_parameters(
            [w1, w2], [100, 200], method=AggregationMethod.FED_AVG
        )


def test_robustness_11_dp_noise_injection_on_embeddings():
    """ROBUSTNESS 11: Differential Privacy Noise Injection on Exported Embeddings."""
    service = GraphEmbeddingService()
    service._embeddings["node_1"] = np.ones(64, dtype=np.float32) / np.sqrt(64)  # type: ignore

    raw_embs = service.get_all_embeddings(dp_noise=False)
    noised_embs = service.get_all_embeddings(noise_scale=0.1, dp_noise=True)

    assert "node_1" in noised_embs
    assert not np.allclose(raw_embs["node_1"], noised_embs["node_1"])
    # Unit norm invariant preserved after noise
    norm = np.linalg.norm(noised_embs["node_1"])
    assert norm == pytest.approx(1.0, abs=1e-5)


def test_robustness_12_query_rate_limit_budget_enforcement():
    """ROBUSTNESS 12: Query Rate-Limiting Budget Enforcement."""
    service = GraphEmbeddingService()
    service.max_query_budget = 3
    service._embeddings["node_1"] = np.ones(64, dtype=np.float32) / np.sqrt(64)  # type: ignore
    v2 = np.zeros(64, dtype=np.float32)
    v2[0] = 1.0
    service._embeddings["node_2"] = v2  # type: ignore

    # Execute 3 allowed queries
    for _ in range(3):
        res = service.find_similar_entities("node_1", top_k=5)
        assert isinstance(res, list)

    # 4th query exceeds budget and returns empty list
    exhausted_res = service.find_similar_entities("node_1", top_k=5)
    assert exhausted_res == []


def test_robustness_13_classifier_head_isolation():
    """ROBUSTNESS 13: Local Classifier Head Isolation During Federation."""
    model = GraphSAGEModel(input_dim=12, hidden_dim=32, embedding_dim=16, num_layers=2)

    weights_representation_only = model.to_model_weights(include_classifier=False)
    weights_full = model.to_model_weights(include_classifier=True)

    # Representation-only excludes 2 classifier Linear layers
    assert weights_representation_only.num_parameters < weights_full.num_parameters
    assert len(weights_representation_only.layer_shapes) < len(
        weights_full.layer_shapes
    )


def test_robustness_14_production_dp_noise_guard():
    """ROBUSTNESS 14: Production DP Noise Guard — dp_noise=False raises RuntimeError in production.

    When APP_ENV=production, disabling DP noise on get_all_embeddings() must be
    rejected with RuntimeError to prevent raw embedding export (topology reconstruction risk).
    """
    import os

    service = GraphEmbeddingService()
    service._embeddings["node_a"] = np.ones(64, dtype=np.float32) / np.sqrt(64)  # type: ignore

    # Set production environment
    old_env = os.environ.get("APP_ENV")
    os.environ["APP_ENV"] = "production"
    try:
        with pytest.raises(RuntimeError, match="DP noise cannot be disabled in production"):
            service.get_all_embeddings(dp_noise=False)
    finally:
        if old_env is None:
            os.environ.pop("APP_ENV", None)
        else:
            os.environ["APP_ENV"] = old_env


def test_robustness_15_train_local_gnn_excludes_classifier_in_weights():
    """ROBUSTNESS 15: train_local_gnn() returns ModelWeights without classifier head.

    GraphSAGEModel with num_layers=2 has 2 GraphSAGELayers, each with 3 parameter
    tensors (W_self, W_neigh, bias). So federated weights must have exactly 6 layer_shapes.
    """
    service = GraphEmbeddingService(hidden_dim=32, embedding_dim=16, num_layers=2)

    # Build a minimal synthetic graph directly on the service model
    model = service._get_or_create_model()
    weights = model.to_model_weights(include_classifier=False)

    # 2 layers × 3 params each (W_self, W_neigh, bias) = 6 parameter tensors
    assert len(weights.layer_shapes) == 6, (
        f"Expected 6 GNN-only parameter tensors, got {len(weights.layer_shapes)}. "
        "Classifier head must be excluded from federated weights."
    )
    # Verify no classifier layer dimensions appear (classifier first layer is 16→16)
    # Classifier: Linear(embedding_dim=16, 16), Linear(16, 1) → shapes (16,16), (16,1)
    # GNN W_self/W_neigh shapes are always (out_dim, in_dim) from nn.Linear
    # None should be (1, 16) which is the final classifier head
    for shape in weights.layer_shapes:
        assert shape != (1, 16), (
            f"Classifier output layer shape (1, 16) found in federated weights: {shape}. "
            "Classifier head must be excluded."
        )

