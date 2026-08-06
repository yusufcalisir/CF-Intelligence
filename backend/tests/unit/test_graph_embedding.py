"""Unit tests for GraphSAGE model and graph embedding service.

Tests the core FedGNN components:
1. GraphSAGE model forward pass, embedding dimensions, and gradient flow
2. Node feature extraction from entity dictionaries
3. Model weight serialization/deserialization for federation
4. Graph embedding service: local graph construction, training, similarity search
5. Integration with FL engine: GNN weight aggregation validation
"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from app.application.services.graph_embedding_model import (
    NODE_FEATURE_DIM,
    GraphSAGELayer,
    GraphSAGEModel,
    extract_node_features,
)
from app.application.services.graph_embedding_service import GraphEmbeddingService
from app.application.services.graph_engine import GraphEngine
from app.domain.enums import AggregationMethod

# ── Node Feature Extraction ──────────────────────


class TestNodeFeatureExtraction:
    """Tests for extract_node_features()."""

    def test_feature_dimension(self) -> None:
        """Feature vector must be exactly NODE_FEATURE_DIM (12) dimensions."""
        entity = {
            "entity_type": "customer",
            "risk_level": "high",
            "alert_count": 5,
        }
        features = extract_node_features(entity, degree=3)
        assert features.shape == (NODE_FEATURE_DIM,)
        assert features.dtype == np.float32

    def test_entity_type_one_hot(self) -> None:
        """Entity type should be one-hot encoded in positions 0-6."""
        for etype, expected_idx in [
            ("customer", 0),
            ("merchant", 1),
            ("device", 2),
            ("card", 3),
        ]:
            features = extract_node_features({"entity_type": etype})
            # The expected index should be 1.0, all others 0.0
            assert features[expected_idx] == 1.0
            for i in range(7):
                if i != expected_idx:
                    assert features[i] == 0.0

    def test_risk_level_ordinal(self) -> None:
        """Risk level should map to ordinal value in position 7."""
        features_min = extract_node_features({"risk_level": "minimal"})
        features_crit = extract_node_features({"risk_level": "critical"})
        assert features_min[7] == 0.0
        assert features_crit[7] == 1.0

    def test_alert_count_normalized(self) -> None:
        """Alert count should be log-normalized and bounded."""
        features_zero = extract_node_features({"alert_count": 0})
        features_high = extract_node_features({"alert_count": 100})
        assert features_zero[8] == 0.0
        assert 0.0 < features_high[8] <= 1.5  # log1p(100)/5.0 ≈ 0.92

    def test_degree_centrality(self) -> None:
        """Degree should be log-normalized in position 9."""
        features_iso = extract_node_features({}, degree=0)
        features_hub = extract_node_features({}, degree=50)
        assert features_iso[9] == 0.0
        assert features_hub[9] > features_iso[9]

    def test_empty_entity_dict(self) -> None:
        """Should handle empty dict without crashing."""
        features = extract_node_features({})
        assert features.shape == (NODE_FEATURE_DIM,)
        # Default entity_type is customer (index 0)
        assert features[0] == 1.0


# ── GraphSAGE Layer ──────────────────────


class TestGraphSAGELayer:
    """Tests for individual GraphSAGE message-passing layers."""

    def test_output_shape(self) -> None:
        """Layer output shape should be (N, out_dim)."""
        layer = GraphSAGELayer(in_dim=12, out_dim=64)
        features = torch.randn(5, 12)
        adj = [[1, 2], [0, 2], [0, 1, 3], [2, 4], [3]]

        output = layer(features, adj)
        assert output.shape == (5, 64)

    def test_isolated_node(self) -> None:
        """Isolated nodes (no neighbors) should still produce embeddings."""
        layer = GraphSAGELayer(in_dim=12, out_dim=32)
        features = torch.randn(3, 12)
        adj = [[], [], []]  # All nodes isolated

        output = layer(features, adj)
        assert output.shape == (3, 32)
        # Output should not be all zeros (bias + self-loop)
        assert not torch.all(output == 0)

    def test_l2_normalization(self) -> None:
        """Output embeddings should be L2-normalized to unit sphere."""
        layer = GraphSAGELayer(in_dim=12, out_dim=32)
        features = torch.randn(4, 12)
        adj = [[1], [0, 2], [1, 3], [2]]

        output = layer(features, adj)
        norms = torch.norm(output, dim=1)
        # Should be approximately 1.0 for non-zero rows
        for i in range(4):
            if torch.any(output[i] != 0):
                assert abs(norms[i].item() - 1.0) < 1e-5

    def test_gradient_flow(self) -> None:
        """Gradients should flow through the layer."""
        layer = GraphSAGELayer(in_dim=12, out_dim=32)
        features = torch.randn(3, 12, requires_grad=True)
        adj = [[1, 2], [0], [0]]

        output = layer(features, adj)
        loss = output.sum()
        loss.backward()

        assert features.grad is not None
        assert not torch.all(features.grad == 0)


# ── GraphSAGE Model ──────────────────────


class TestGraphSAGEModel:
    """Tests for the full GraphSAGE model."""

    def test_embedding_dimension(self) -> None:
        """Model should produce embeddings of the configured dimension."""
        model = GraphSAGEModel(input_dim=12, hidden_dim=64, embedding_dim=32, num_layers=2)
        features = torch.randn(5, 12)
        adj = [[1, 2], [0, 3], [0, 4], [1], [2]]

        embeddings = model.get_embeddings(features, adj)
        assert embeddings.shape == (5, 32)

    def test_forward_returns_embeddings_and_predictions(self) -> None:
        """Forward pass should return both embeddings and fraud predictions."""
        model = GraphSAGEModel(input_dim=12, hidden_dim=64, embedding_dim=32, num_layers=2)
        features = torch.randn(4, 12)
        adj = [[1], [0, 2], [1, 3], [2]]

        embeddings, predictions = model(features, adj)
        assert embeddings.shape == (4, 32)
        assert predictions.shape == (4,)
        # Predictions should be in [0, 1] (sigmoid output)
        assert torch.all(predictions >= 0)
        assert torch.all(predictions <= 1)

    def test_model_weights_serialization_roundtrip(self) -> None:
        """Weights should survive serialize → deserialize without loss."""
        model = GraphSAGEModel(input_dim=12, hidden_dim=64, embedding_dim=32, num_layers=2)

        # Set known weights
        for param in model.parameters():
            param.data = torch.randn_like(param)

        # Serialize
        weights = model.to_model_weights(include_classifier=True)
        assert len(weights.flat_weights) > 0
        assert len(weights.layer_shapes) > 0

        # Create new model and load weights
        model2 = GraphSAGEModel(input_dim=12, hidden_dim=64, embedding_dim=32, num_layers=2)
        model2.load_model_weights(weights, include_classifier=True)

        # Compare parameters
        for p1, p2 in zip(model.parameters(), model2.parameters()):
            assert torch.allclose(p1.data, p2.data, atol=1e-6)

    def test_model_weights_parameter_count(self) -> None:
        """Serialized weight count should match model parameter count."""
        model = GraphSAGEModel(input_dim=12, hidden_dim=128, embedding_dim=64, num_layers=2)
        weights = model.to_model_weights(include_classifier=True)

        expected_params = sum(p.numel() for p in model.parameters())
        assert weights.num_parameters == expected_params


# ── Model Weight Aggregation ──────────────────────


class TestGNNAggregation:
    """Tests for GNN weight aggregation via fl_engine."""

    def test_fedavg_gnn_weights(self) -> None:
        """FedAvg should correctly average GNN model weights."""
        from app.application.services.fl_engine import FederatedLearningEngine

        # Create two models with different weights
        model_a = GraphSAGEModel(input_dim=12, hidden_dim=32, embedding_dim=16, num_layers=1)
        model_b = GraphSAGEModel(input_dim=12, hidden_dim=32, embedding_dim=16, num_layers=1)

        for p in model_a.parameters():
            p.data = torch.ones_like(p) * 2.0
        for p in model_b.parameters():
            p.data = torch.ones_like(p) * 4.0

        weights_a = model_a.to_model_weights()
        weights_b = model_b.to_model_weights()

        # Create a mock engine (doesn't need real services for aggregation)
        engine = FederatedLearningEngine.__new__(FederatedLearningEngine)

        aggregated = engine.aggregate_parameters(
            [weights_a, weights_b],
            [100, 100],
            method=AggregationMethod.FED_AVG,
        )

        # Average of 2.0 and 4.0 = 3.0
        for w in aggregated.flat_weights:
            assert abs(w - 3.0) < 1e-6

    def test_gnn_layer_shape_validation(self) -> None:
        """aggregate_graph_parameters should reject mismatched layer shapes."""
        from app.application.services.fl_engine import FederatedLearningEngine

        # With num_layers=2, hidden_dim affects intermediate layer shapes
        model_a = GraphSAGEModel(input_dim=12, hidden_dim=32, embedding_dim=16, num_layers=2)
        model_b = GraphSAGEModel(input_dim=12, hidden_dim=64, embedding_dim=16, num_layers=2)

        weights_a = model_a.to_model_weights()
        weights_b = model_b.to_model_weights()

        engine = FederatedLearningEngine.__new__(FederatedLearningEngine)

        with pytest.raises(ValueError, match="GNN layer shape mismatch"):
            engine.aggregate_graph_parameters(
                [weights_a, weights_b],
                [100, 100],
            )


# ── Graph Embedding Service ──────────────────────


class TestGraphEmbeddingService:
    """Tests for the graph embedding orchestration service."""

    def test_empty_graph_training(self) -> None:
        """Training on an empty graph should return zero loss without crashing."""
        service = GraphEmbeddingService(graph_engine=GraphEngine())
        weights, metrics = service.train_local_gnn(bank_id="bank_nonexistent")
        assert metrics["loss"] == 0.0
        assert metrics["num_nodes"] == 0

    def test_embedding_stats_empty(self) -> None:
        """Stats should return zeros when no embeddings exist."""
        service = GraphEmbeddingService()
        stats = service.get_embedding_stats()
        assert stats["num_embedded_nodes"] == 0

    def test_find_similar_no_embeddings(self) -> None:
        """Similarity search should return empty when no embeddings cached."""
        service = GraphEmbeddingService()
        results = service.find_similar_entities("nonexistent")
        assert results == []

    def test_get_all_embeddings_serializable(self) -> None:
        """get_all_embeddings should return JSON-serializable dict."""
        service = GraphEmbeddingService()
        # Manually inject a cached embedding
        service._embeddings["test_node"] = np.array([0.1, 0.2, 0.3], dtype=np.float32)

        result = service.get_all_embeddings()
        assert "test_node" in result
        assert isinstance(result["test_node"], list)
        assert len(result["test_node"]) == 3

    def test_cosine_similarity_search(self) -> None:
        """Cosine similarity search should find similar embeddings."""
        service = GraphEmbeddingService()

        # Create embeddings: A and B are similar, C is different
        service._embeddings["node_a"] = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        service._embeddings["node_b"] = np.array([0.9, 0.1, 0.0, 0.0], dtype=np.float32)
        service._embeddings["node_c"] = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)

        results = service.find_similar_entities("node_a", top_k=5, threshold=0.5)

        # node_b should be found (high similarity to node_a)
        entity_ids = [r["entity_id"] for r in results]
        assert "node_b" in entity_ids

        # node_c should NOT be found (orthogonal to node_a)
        assert "node_c" not in entity_ids
