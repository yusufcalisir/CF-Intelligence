"""Comprehensive Hypothesis Property-Based Tests for Federated GraphSAGE (FedGNN).

Verifies mathematical and graph-theoretic invariants across hundreds of randomized scenarios:
- Property 1: Neighborhood Permutation Invariance (AGG(pi(N(v))) == AGG(N(v)))
- Property 2: L2 Hypersphere Unit Norm Invariant (||h_v||_2 == 1.0)
- Property 3: Cosine Similarity Domain Boundedness & Symmetry (-1.0 <= Sim <= 1.0, Sim(u, v) == Sim(v, u))
- Property 4: Model Weight Serialization Round-Trip Bijection
- Property 5: Federated GNN Aggregation Convexity Invariant
- Property 6: Isolated Node Isolation Safety & Self-Loop Invariant
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import torch
from hypothesis import given, settings, strategies as st

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
from app.config import get_settings  # noqa: E402

mock_settings = get_settings()
fl_engine = FederatedLearningEngine(
    settings=mock_settings,
    model_service=MagicMock(),
    privacy_service=MagicMock(),
)


# =====================================================================
# HYPOTHESIS STRATEGIES FOR GRAPHS
# =====================================================================


@st.composite
def random_graph_topology(draw):
    num_nodes = draw(st.integers(min_value=2, max_value=40))
    in_dim = draw(st.integers(min_value=4, max_value=32))

    # Random node feature matrix
    X_np = draw(
        st.lists(
            st.lists(
                st.floats(
                    min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False
                ),
                min_size=in_dim,
                max_size=in_dim,
            ),
            min_size=num_nodes,
            max_size=num_nodes,
        )
    )
    X_pt = torch.tensor(X_np, dtype=torch.float32)

    # Random adjacency lists
    adj: list[list[int]] = []
    for i in range(num_nodes):
        degree = draw(st.integers(min_value=0, max_value=min(num_nodes, 10)))
        if degree == 0:
            adj.append([])
        else:
            neighbors = draw(
                st.sets(
                    st.integers(min_value=0, max_value=num_nodes - 1),
                    min_size=degree,
                    max_size=degree,
                )
            )
            adj.append(list(neighbors))

    return num_nodes, in_dim, X_pt, adj


# =====================================================================
# PROPERTY-BASED TEST SUITE
# =====================================================================


@settings(max_examples=50, deadline=None)
@given(
    graph_data=random_graph_topology(), out_dim=st.integers(min_value=8, max_value=64)
)
def test_property_1_neighborhood_permutation_invariance(graph_data, out_dim):
    r"""PROPERTY 1: Neighborhood Permutation Invariance.

    Math Invariant:
        \forall \text{permutation } \pi, \quad \text{AGG}(\pi(\mathcal{N}(v))) \equiv \text{AGG}(\mathcal{N}(v))
    """
    num_nodes, in_dim, X_pt, adj = graph_data
    layer = GraphSAGELayer(in_dim, out_dim)
    layer.eval()

    with torch.no_grad():
        out_orig = layer(X_pt, adj, num_sample=1000)

        # Shuffle neighbor ordering for every node
        shuffled_adj = []
        rng = np.random.default_rng(42)
        for neighbors in adj:
            if neighbors:
                shuffled = rng.permutation(neighbors).tolist()
                shuffled_adj.append(shuffled)
            else:
                shuffled_adj.append([])

        out_shuffled = layer(X_pt, shuffled_adj, num_sample=1000)

    max_diff = torch.max(torch.abs(out_orig - out_shuffled)).item()
    assert max_diff < 1e-5, f"Permutation invariance violated: max_diff={max_diff}"


@settings(max_examples=50, deadline=None)
@given(graph_data=random_graph_topology(), emb_dim=st.sampled_from([16, 32, 64]))
def test_property_2_l2_hypersphere_unit_norm(graph_data, emb_dim):
    r"""PROPERTY 2: L2 Hypersphere Unit Norm Invariant.

    Math Invariant:
        \forall v \in V, \quad \|\mathbf{h}_v\|_2 = 1.0 \pm 10^{-6}
    """
    num_nodes, in_dim, X_pt, adj = graph_data
    model = GraphSAGEModel(
        input_dim=in_dim, hidden_dim=32, embedding_dim=emb_dim, num_layers=2
    )
    model.eval()

    with torch.no_grad():
        embs = model.get_embeddings(X_pt, adj, num_sample=1000)

    norms = torch.norm(embs, p=2, dim=1)
    for i in range(num_nodes):
        n_val = norms[i].item()
        # Non-zero activation check
        if torch.sum(torch.abs(embs[i])).item() > 1e-10:  # type: ignore
            assert abs(n_val - 1.0) < 1e-5, (
                f"L2 unit norm violated at node {i}: norm={n_val}"
            )


@settings(max_examples=100, deadline=None)
@given(
    dim=st.sampled_from([16, 32, 64]), seed=st.integers(min_value=0, max_value=10000)
)
def test_property_3_cosine_similarity_bounds_and_symmetry(dim, seed):
    r"""PROPERTY 3: Cosine Similarity Domain Boundedness & Symmetry.

    Math Invariant:
        -1.0 \le \text{Sim}(\mathbf{u}, \mathbf{v}) \le 1.0 \quad \text{and} \quad \text{Sim}(\mathbf{u}, \mathbf{v}) \equiv \text{Sim}(\mathbf{v}, \mathbf{u})
    """
    rng = np.random.default_rng(seed)
    u = rng.normal(0, 1, dim).astype(np.float32)
    v = rng.normal(0, 1, dim).astype(np.float32)

    u_t = torch.tensor(u).unsqueeze(0)
    v_t = torch.tensor(v).unsqueeze(0)

    sim_uv = torch.nn.functional.cosine_similarity(u_t, v_t).item()
    sim_vu = torch.nn.functional.cosine_similarity(v_t, u_t).item()

    assert -1.0 - 1e-6 <= sim_uv <= 1.0 + 1e-6, f"Cosine out of bounds: {sim_uv}"
    assert abs(sim_uv - sim_vu) < 1e-6, f"Symmetry violated: {sim_uv} vs {sim_vu}"


@settings(max_examples=50, deadline=None)
@given(
    in_dim=st.integers(min_value=4, max_value=32),
    hidden_dim=st.sampled_from([16, 32, 64]),
    emb_dim=st.sampled_from([8, 16, 32]),
    num_layers=st.integers(min_value=1, max_value=3),
)
def test_property_4_serialization_bijection(in_dim, hidden_dim, emb_dim, num_layers):
    r"""PROPERTY 4: Model Weight Serialization Round-Trip Bijection.

    Math Invariant:
        \text{load\_model\_weights}(\text{to\_model\_weights}(\mathbf{M})) \equiv \mathbf{M}
    """
    m1 = GraphSAGEModel(
        input_dim=in_dim,
        hidden_dim=hidden_dim,
        embedding_dim=emb_dim,
        num_layers=num_layers,
    )
    weights_rep = m1.to_model_weights(include_classifier=False)

    m2 = GraphSAGEModel(
        input_dim=in_dim,
        hidden_dim=hidden_dim,
        embedding_dim=emb_dim,
        num_layers=num_layers,
    )
    m2.load_model_weights(weights_rep, include_classifier=False)

    p1 = list(m1.sage_layers.parameters())
    p2 = list(m2.sage_layers.parameters())

    assert len(p1) == len(p2)
    for param1, param2 in zip(p1, p2, strict=False):
        diff = torch.max(torch.abs(param1 - param2)).item()  # type: ignore
        assert diff == 0.0, f"Serialization bijection error: diff={diff}"

    # Also test full model weights bijection
    weights_full = m1.to_model_weights(include_classifier=True)
    m3 = GraphSAGEModel(
        input_dim=in_dim,
        hidden_dim=hidden_dim,
        embedding_dim=emb_dim,
        num_layers=num_layers,
    )
    m3.load_model_weights(weights_full, include_classifier=True)

    p1_full = list(m1.parameters())
    p3_full = list(m3.parameters())

    assert len(p1_full) == len(p3_full)
    for param1, param3 in zip(p1_full, p3_full, strict=False):
        diff = torch.max(torch.abs(param1 - param3)).item()  # type: ignore
        assert diff == 0.0, f"Full model serialization bijection error: diff={diff}"


@settings(max_examples=50, deadline=None)
@given(
    num_clients=st.integers(min_value=2, max_value=10),
    sample_partitions=st.lists(
        st.integers(min_value=1, max_value=1000), min_size=2, max_size=10
    ),
)
def test_property_5_fedavg_aggregation_convexity(num_clients, sample_partitions):
    r"""PROPERTY 5: Federated GNN Aggregation Scale Invariance & Convexity.

    Math Invariant:
        \forall \boldsymbol{\theta}_1 = \dots = \boldsymbol{\theta}_K, \quad \text{FedAvg}(\{\boldsymbol{\theta}_k\}, \{n_k\}) \equiv \boldsymbol{\theta}_1
    """
    K = len(sample_partitions)
    m_base = GraphSAGEModel(input_dim=12, hidden_dim=32, embedding_dim=16, num_layers=2)
    base_w = m_base.to_model_weights()

    client_weights = [base_w for _ in range(K)]
    agg_w = fl_engine.aggregate_graph_parameters(
        client_weights, sample_partitions, method=AggregationMethod.FED_AVG_WEIGHTED
    )

    diff = np.max(np.abs(np.array(agg_w.flat_weights) - np.array(base_w.flat_weights)))
    assert diff < 1e-6, f"Convexity violated: diff={diff}"


@settings(max_examples=50, deadline=None)
@given(in_dim=st.integers(min_value=4, max_value=32))
def test_property_6_isolated_node_safety(in_dim):
    r"""PROPERTY 6: Isolated Node Safety & Self-Loop Fallback Invariant.

    Math Invariant:
        \forall v \text{ with } \mathcal{N}(v) = \emptyset, \quad \text{Model}(\mathbf{x}_v, \emptyset) \text{ succeeds without NaN/Inf}.
    """
    N = 10
    X_pt = torch.randn(N, in_dim)
    empty_adj = [[] for _ in range(N)]

    model = GraphSAGEModel(
        input_dim=in_dim, hidden_dim=32, embedding_dim=16, num_layers=2
    )
    model.eval()

    with torch.no_grad():
        embs, preds = model(X_pt, empty_adj, num_sample=10)

    assert not torch.isnan(embs).any(), "NaN found in isolated node embeddings"
    assert not torch.isinf(embs).any(), "Inf found in isolated node embeddings"
    assert embs.shape == (N, 16)
    assert preds.shape == (N,)


# =====================================================================
# HP11: Classifier Exclusion Monotonicity
# =====================================================================


@given(
    in_dim=st.integers(min_value=4, max_value=16),
    hidden_dim=st.integers(min_value=8, max_value=32),
    emb_dim=st.integers(min_value=8, max_value=32),
    num_layers=st.integers(min_value=1, max_value=3),
)
@settings(max_examples=30, deadline=5000)
def test_property_7_classifier_exclusion_monotonicity(in_dim, hidden_dim, emb_dim, num_layers):
    """HP11: to_model_weights(include_classifier=False) always produces strictly fewer
    parameters than to_model_weights(include_classifier=True) for any valid architecture.

    Ensures the privacy policy of classifier head exclusion is invariant across
    all model configurations — not just the default 12→128→64 architecture.
    """
    model = GraphSAGEModel(
        input_dim=in_dim,
        hidden_dim=hidden_dim,
        embedding_dim=emb_dim,
        num_layers=num_layers,
    )

    weights_gnn_only = model.to_model_weights(include_classifier=False)
    weights_full = model.to_model_weights(include_classifier=True)

    assert weights_gnn_only.num_parameters < weights_full.num_parameters, (
        "GNN-only weights must have fewer parameters than full model weights. "
        "Classifier head must contribute at least 1 additional parameter."
    )
    assert len(weights_gnn_only.layer_shapes) < len(weights_full.layer_shapes), (
        "GNN-only weights must have fewer layers than full model weights."
    )


# =====================================================================
# HP12: DP Noise Monotone Cosine Distance
# =====================================================================


@given(
    noise_scale=st.floats(min_value=0.01, max_value=0.5),
    dim=st.integers(min_value=8, max_value=64),
)
@settings(max_examples=30, deadline=5000)
def test_property_8_dp_noise_unit_sphere_invariant(noise_scale, dim):
    """HP12: DP-noised embeddings from get_all_embeddings(dp_noise=True, noise_scale=s)
    are re-normalized to the unit sphere — ||noised_emb||_2 ≈ 1.0 within 1e-5 tolerance.

    This validates that the L2 re-normalization step in get_all_embeddings() correctly
    preserves the unit-sphere invariant established by GraphSAGELayer.forward().
    """
    from app.application.services.graph_embedding_service import GraphEmbeddingService

    service = GraphEmbeddingService(embedding_dim=dim)

    # Create a unit-sphere embedding
    raw_emb = np.random.default_rng(42).standard_normal(dim).astype(np.float32)
    raw_emb = raw_emb / np.linalg.norm(raw_emb)
    service._embeddings["test_node"] = raw_emb  # type: ignore

    result = service.get_all_embeddings(dp_noise=True, noise_scale=noise_scale)

    assert "test_node" in result
    noised = np.array(result["test_node"])
    norm = np.linalg.norm(noised)

    # Must be on unit sphere after DP noise + re-normalization
    assert abs(norm - 1.0) < 1e-5, (
        f"Noised embedding norm={norm:.8f} deviates from unit sphere "
        f"(noise_scale={noise_scale}, dim={dim}). Re-normalization failed."
    )

