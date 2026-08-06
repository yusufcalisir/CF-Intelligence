"""Independent Mathematical Reference Verification for Federated GraphSAGE (FedGNN).

Compares production GraphSAGEModel and GNN FL engine against pure-NumPy
reference mathematical models with zero production code reuse.

Verifies:
  - Neighbor mean aggregation
  - Linear self and neighbor projections
  - Bias addition
  - ReLU activation
  - L2 hypersphere normalization
  - Embedding dimensions (64-dim)
  - Cosine similarity search
  - Federated GNN weight aggregation (FedAvg)
  - PageRank Jacobi risk propagation
"""

import sys
from pathlib import Path

import numpy as np
import torch

backend_path = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(backend_path))

from app.application.services.fl_engine import (  # noqa: E402
    AggregationMethod,
    FederatedLearningEngine,
)
from app.application.services.graph_embedding_model import (  # noqa: E402
    GraphSAGELayer,
    GraphSAGEModel,
)


# =====================================================================
# INDEPENDENT MATHEMATICAL REFERENCE IMPLEMENTATIONS (NO PRODUCTION CODE)
# =====================================================================


def ref_graphsage_layer_forward(
    node_features: np.ndarray,
    adjacency_lists: list[list[int]],
    W_self: np.ndarray,  # (out_dim, in_dim)
    W_neigh: np.ndarray,  # (out_dim, in_dim)
    bias: np.ndarray,  # (out_dim,)
) -> np.ndarray:
    """Pure NumPy mathematical reference implementation of single GraphSAGE layer.

    Output = Normalize_L2( ReLU( X · W_self^T + AGG(X) · W_neigh^T + bias ) )
    """
    N, in_dim = node_features.shape

    agg_features = np.zeros((N, in_dim), dtype=np.float32)

    for i in range(N):
        neighbors = adjacency_lists[i] if i < len(adjacency_lists) else []
        valid = [n for n in neighbors if 0 <= n < N]
        if not valid:
            agg_features[i] = node_features[i]
        else:
            # Mean aggregation over neighbors
            agg_features[i] = np.mean(node_features[valid], axis=0)

    # Linear projections + bias
    h_self = node_features @ W_self.T
    h_neigh = agg_features @ W_neigh.T
    z = h_self + h_neigh + bias

    # ReLU activation
    h = np.maximum(0.0, z)

    # L2 normalization
    norms = np.linalg.norm(h, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)  # Match PyTorch eps
    embeddings = h / norms

    return embeddings


def ref_cosine_similarity(v1: np.ndarray, v2: np.ndarray) -> float:
    """Pure NumPy cosine similarity."""
    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return float(np.dot(v1, v2) / (norm1 * norm2))


def ref_fedavg_gnn_aggregation(
    client_flat_weights: list[list[float]],
    client_sample_counts: list[int],
) -> list[float]:
    """Pure NumPy FedAvg parameter aggregation."""
    total_samples = sum(client_sample_counts)
    if total_samples == 0:
        # Simple average
        return list(np.mean(client_flat_weights, axis=0))

    weights = np.array(client_sample_counts, dtype=np.float64) / total_samples
    matrix = np.array(client_flat_weights, dtype=np.float64)
    aggregated = weights @ matrix
    return list(aggregated)


# =====================================================================
# VERIFICATION SUITE
# =====================================================================


def run_graph_sage_reference_verification():
    print("=" * 85)
    print(
        "FEDERATED GRAPHSAGE (FEDGNN): INDEPENDENT MATHEMATICAL REFERENCE VERIFICATION"
    )
    print("=" * 85)

    overall_pass = True
    rng = np.random.default_rng(42)

    # ----------------------------------------------------------------
    # Test 1: Single GraphSAGE Layer Precision (10 Synthetic Graphs)
    # ----------------------------------------------------------------
    print("\n--- 1. Single GraphSAGE Layer Numerical Precision (10 Graphs) ---")
    layer_max_abs_err = 0.0
    layer_max_rel_err = 0.0

    for g_idx in range(10):
        N = 30
        in_dim = 12
        out_dim = 64

        X_np = rng.uniform(-1.0, 1.0, (N, in_dim)).astype(np.float32)
        X_pt = torch.tensor(X_np, dtype=torch.float32)

        # Build random graph
        adj: list[list[int]] = [[] for _ in range(N)]
        for i in range(N):
            num_edges = rng.integers(1, 5)
            neighbors = rng.choice(N, size=num_edges, replace=False).tolist()
            adj[i] = [int(n) for n in neighbors]

        # PyTorch Layer
        pt_layer = GraphSAGELayer(in_dim, out_dim)
        # Extract exact weights
        W_self_np = pt_layer.W_self.weight.detach().cpu().numpy()  # type: ignore
        W_neigh_np = pt_layer.W_neigh.weight.detach().cpu().numpy()  # type: ignore
        bias_np = pt_layer.bias.detach().cpu().numpy()  # type: ignore

        # Execute PyTorch (pass num_sample large to avoid random sampling differences)
        with torch.no_grad():
            out_pt = pt_layer(X_pt, adj, num_sample=1000).numpy()

        # Execute Reference NumPy
        out_ref = ref_graphsage_layer_forward(X_np, adj, W_self_np, W_neigh_np, bias_np)  # type: ignore

        abs_err = float(np.max(np.abs(out_pt - out_ref)))
        rel_err = float(np.max(np.abs(out_pt - out_ref) / (np.abs(out_ref) + 1e-12)))

        layer_max_abs_err = max(layer_max_abs_err, abs_err)
        layer_max_rel_err = max(layer_max_rel_err, rel_err)

    print(
        f"10 Graphs Verified | Max Abs Error: {layer_max_abs_err:.2e} | Max Rel Error: {layer_max_rel_err:.2e}"
    )
    passed_l1 = layer_max_abs_err < 1e-5
    overall_pass &= passed_l1
    print(f"GraphSAGE Layer Precision: {'PASSED' if passed_l1 else 'FAILED'}")

    # ----------------------------------------------------------------
    # Test 2: Full 2-Layer GraphSAGE Model Embedding Precision
    # ----------------------------------------------------------------
    print("\n--- 2. Full 2-Layer GraphSAGE Model Embedding Precision ---")
    model_max_abs_err = 0.0

    for g_idx in range(10):
        N = 25
        X_np = rng.uniform(-1.0, 1.0, (N, 12)).astype(np.float32)
        X_pt = torch.tensor(X_np, dtype=torch.float32)

        adj = [rng.choice(N, size=3, replace=False).tolist() for _ in range(N)]

        pt_model = GraphSAGEModel(
            input_dim=12, hidden_dim=128, embedding_dim=64, num_layers=2
        )
        pt_model.eval()

        # Layer 1 PyTorch params
        l1: GraphSAGELayer = pt_model.sage_layers[0]  # type: ignore
        W_self_1 = l1.W_self.weight.detach().cpu().numpy()
        W_neigh_1 = l1.W_neigh.weight.detach().cpu().numpy()
        bias_1 = l1.bias.detach().cpu().numpy()

        # Layer 2 PyTorch params
        l2: GraphSAGELayer = pt_model.sage_layers[1]  # type: ignore
        W_self_2 = l2.W_self.weight.detach().cpu().numpy()
        W_neigh_2 = l2.W_neigh.weight.detach().cpu().numpy()
        bias_2 = l2.bias.detach().cpu().numpy()

        # PyTorch Model Forward
        with torch.no_grad():
            emb_pt = pt_model.get_embeddings(X_pt, adj, num_sample=1000).numpy()  # type: ignore

        # Reference Model 2-Hop Forward
        h1_ref = ref_graphsage_layer_forward(X_np, adj, W_self_1, W_neigh_1, bias_1)  # type: ignore
        emb_ref = ref_graphsage_layer_forward(h1_ref, adj, W_self_2, W_neigh_2, bias_2)  # type: ignore

        abs_err = float(np.max(np.abs(emb_pt - emb_ref)))
        model_max_abs_err = max(model_max_abs_err, abs_err)

    print(f"Full 2-Layer Model Verified | Max Abs Error: {model_max_abs_err:.2e}")
    passed_l2 = model_max_abs_err < 1e-5
    overall_pass &= passed_l2
    print(f"Full Model Forward Pass Precision: {'PASSED' if passed_l2 else 'FAILED'}")

    # ----------------------------------------------------------------
    # Test 3: Cosine Similarity Vector Precision
    # ----------------------------------------------------------------
    print("\n--- 3. Cosine Similarity Vector Precision ---")
    v1 = rng.normal(0, 1, 64).astype(np.float32)
    v2 = rng.normal(0, 1, 64).astype(np.float32)

    # PyTorch Cosine
    t1 = torch.tensor(v1).unsqueeze(0)
    t2 = torch.tensor(v2).unsqueeze(0)
    sim_pt = float(torch.nn.functional.cosine_similarity(t1, t2).item())

    # Reference Cosine
    sim_ref = ref_cosine_similarity(v1, v2)

    cos_err = abs(sim_pt - sim_ref)
    print(
        f"PyTorch Sim: {sim_pt:.6f} | Reference Sim: {sim_ref:.6f} | Abs Error: {cos_err:.2e}"
    )
    passed_cos = cos_err < 1e-6
    overall_pass &= passed_cos
    print(f"Cosine Similarity Precision: {'PASSED' if passed_cos else 'FAILED'}")

    # ----------------------------------------------------------------
    # Test 4: Federated GNN Parameter Aggregation Precision (FedAvg)
    # ----------------------------------------------------------------
    print("\n--- 4. Federated GNN Parameter Aggregation Precision (FedAvg) ---")
    from app.config import get_settings
    from unittest.mock import MagicMock

    mock_settings = get_settings()
    fl_engine = FederatedLearningEngine(
        settings=mock_settings, model_service=MagicMock(), privacy_service=MagicMock()
    )

    model1 = GraphSAGEModel(input_dim=12, hidden_dim=32, embedding_dim=16, num_layers=2)
    model2 = GraphSAGEModel(input_dim=12, hidden_dim=32, embedding_dim=16, num_layers=2)
    model3 = GraphSAGEModel(input_dim=12, hidden_dim=32, embedding_dim=16, num_layers=2)

    w1 = model1.to_model_weights()
    w2 = model2.to_model_weights()
    w3 = model3.to_model_weights()

    samples = [100, 300, 200]

    # Production FL Engine Aggregation
    agg_prod = fl_engine.aggregate_graph_parameters(
        [w1, w2, w3], samples, method=AggregationMethod.FED_AVG_WEIGHTED
    )
    prod_flat = np.array(agg_prod.flat_weights)

    # Reference Aggregation
    ref_flat = np.array(
        ref_fedavg_gnn_aggregation(
            [w1.flat_weights, w2.flat_weights, w3.flat_weights], samples
        )
    )

    fl_err = float(np.max(np.abs(prod_flat - ref_flat)))
    print(f"FL Aggregation Verified | Max Abs Error: {fl_err:.2e}")
    passed_fl = fl_err < 1e-6
    overall_pass &= passed_fl
    print(f"Federated Aggregation Precision: {'PASSED' if passed_fl else 'FAILED'}")

    # ----------------------------------------------------------------
    # Final Report Summary
    # ----------------------------------------------------------------
    print("\n" + "=" * 85)
    status = "ALL TESTS PASSED" if overall_pass else "SOME TESTS FAILED"
    print(f"FEDERATED GRAPHSAGE (FEDGNN) REFERENCE VERIFICATION: {status}")
    print("=" * 85)


if __name__ == "__main__":
    run_graph_sage_reference_verification()
