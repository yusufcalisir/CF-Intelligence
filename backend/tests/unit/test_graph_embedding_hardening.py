"""Hardening tests for GraphSAGE Inductive Embedding Model & Service.

Verifies:
1. Inductive embedding inference on unseen/novel nodes without retraining.
2. Inductive inference on isolated unseen nodes.
3. 128-dimensional topological node embedding configuration.
4. Calibrated differential privacy noise injection and unit L2 re-normalization.
5. Production guardrail blocking un-noised raw embedding export.
6. Strict validation of federated model weight layer shapes and element counts.
7. Privacy policy exclusion of classifier head weights in federated aggregation.
8. Query budget enforcement and reset mechanism.
9. Concurrency thread safety under multi-threaded operations.
10. End-to-end FastAPI endpoint integration for inductive embedding inference.
"""

from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.application.services.graph_embedding_model import (
    GraphSAGEModel,
)
from app.application.services.graph_embedding_service import (
    GraphEmbeddingService,
)
from app.application.services.graph_engine import GraphEngine
from app.domain.entities_phase2 import Entity, Relationship
from app.domain.enums import EntityType, RelationshipType, RiskLevel
from app.domain.value_objects import ModelWeights
from app.main import app


@pytest.fixture
def clean_graph_engine() -> GraphEngine:
    engine = GraphEngine()
    engine._entities.clear()
    engine._relationships.clear()
    return engine


@pytest.fixture
def embedding_service(clean_graph_engine: GraphEngine) -> GraphEmbeddingService:
    return GraphEmbeddingService(graph_engine=clean_graph_engine, embedding_dim=64)


def _make_entity(
    entity_id: str,
    bank_id: str = "bank_alpha",
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    entity_type: EntityType = EntityType.CUSTOMER,
) -> Entity:
    now = datetime.now(UTC)
    return Entity(
        id=entity_id,
        entity_type=entity_type,
        privacy_id=f"priv_{entity_id}",
        bank_id=bank_id,
        display_label=f"Entity {entity_id}",
        attributes={"region": "EU"},
        risk_level=risk_level,
        alert_count=2,
        first_seen=now,
        last_seen=now,
    )


def _make_rel(rel_id: str, src: str, tgt: str) -> Relationship:
    return Relationship(
        id=rel_id,
        source_entity_id=src,
        target_entity_id=tgt,
        relationship_type=RelationshipType.TRANSACTS_WITH,
        confidence=1.0,
        evidence={"amount": 1000.0},
        created_at=datetime.now(UTC),
    )


class TestGraphEmbeddingHardening:
    def test_inductive_node_embedding_inference_unseen_node(
        self, clean_graph_engine: GraphEngine, embedding_service: GraphEmbeddingService
    ) -> None:
        """Inductive inference must compute valid unit-norm embeddings for novel unseen nodes."""
        engine = clean_graph_engine
        # Train on initial 3 nodes
        for i in range(1, 4):
            engine.register_entity(_make_entity(f"train_node_{i}"))
        engine.add_relationship(_make_rel("r12", "train_node_1", "train_node_2"))
        engine.add_relationship(_make_rel("r23", "train_node_2", "train_node_3"))

        embedding_service.train_local_gnn(bank_id="bank_alpha", epochs=2)

        # Register a brand new unseen entity with connection to train_node_1
        unseen = _make_entity("novel_node_99", risk_level=RiskLevel.HIGH)
        engine.register_entity(unseen)
        engine.add_relationship(_make_rel("r_unseen", "novel_node_99", "train_node_1"))

        # Cached lookup should return None initially
        assert embedding_service.get_embedding("novel_node_99", allow_inductive=False) is None

        # Inductive inference must compute the embedding on-the-fly
        emb = embedding_service.infer_node_embedding("novel_node_99", depth=2)
        assert emb is not None
        assert emb.shape == (64,)
        # Must be unit L2 normalized
        assert abs(float(np.linalg.norm(emb)) - 1.0) < 1e-4

        # Now cached lookup should succeed
        assert embedding_service.get_embedding("novel_node_99", allow_inductive=False) is not None

    def test_inductive_node_embedding_isolated_node(
        self, clean_graph_engine: GraphEngine, embedding_service: GraphEmbeddingService
    ) -> None:
        """Inductive inference on an isolated node (no edges) must succeed via self-loop."""
        engine = clean_graph_engine
        isolated = _make_entity("isolated_node_x")
        engine.register_entity(isolated)

        emb = embedding_service.infer_node_embedding("isolated_node_x", depth=2)
        assert emb is not None
        assert emb.shape == (64,)
        assert abs(float(np.linalg.norm(emb)) - 1.0) < 1e-4

    def test_128_dimensional_embedding_configuration(
        self, clean_graph_engine: GraphEngine
    ) -> None:
        """GraphSAGE must support 128-dimensional inductive node representation."""
        engine = clean_graph_engine
        service_128 = GraphEmbeddingService(graph_engine=engine, embedding_dim=128)

        for i in range(1, 4):
            engine.register_entity(_make_entity(f"node_128_{i}"))
        engine.add_relationship(_make_rel("r1", "node_128_1", "node_128_2"))

        weights, metrics = service_128.train_local_gnn(bank_id="bank_alpha", epochs=2)
        assert metrics["embedding_dim"] == 128

        emb = service_128.get_embedding("node_128_1")
        assert emb is not None
        assert emb.shape == (128,)
        assert abs(float(np.linalg.norm(emb)) - 1.0) < 1e-4

    def test_dp_noise_injection_on_single_and_bulk_embeddings(
        self, clean_graph_engine: GraphEngine, embedding_service: GraphEmbeddingService
    ) -> None:
        """DP noise injection must perturb embeddings while strictly preserving unit L2 norm."""
        engine = clean_graph_engine
        e1 = _make_entity("e_dp_1")
        e2 = _make_entity("e_dp_2")
        engine.register_entity(e1)
        engine.register_entity(e2)
        engine.add_relationship(_make_rel("r_dp", e1.id, e2.id))

        embedding_service.train_local_gnn(bank_id="bank_alpha", epochs=2)

        raw_emb = embedding_service.get_embedding("e_dp_1", dp_noise=False)
        noised_emb = embedding_service.get_embedding("e_dp_1", dp_noise=True, noise_scale=0.10)

        assert raw_emb is not None
        assert noised_emb is not None
        # Must differ due to calibrated noise
        assert not np.allclose(raw_emb, noised_emb, atol=1e-3)
        # But both must be unit L2 norm
        assert abs(float(np.linalg.norm(raw_emb)) - 1.0) < 1e-4
        assert abs(float(np.linalg.norm(noised_emb)) - 1.0) < 1e-4

    def test_production_raw_embedding_export_blocked(
        self,
        clean_graph_engine: GraphEngine,
        embedding_service: GraphEmbeddingService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """In production environment, exporting raw embeddings without DP noise must raise RuntimeError."""
        engine = clean_graph_engine
        engine.register_entity(_make_entity("node_prod"))
        embedding_service.train_local_gnn(bank_id="bank_alpha", epochs=1)

        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(RuntimeError, match="DP noise cannot be disabled in production"):
            embedding_service.get_all_embeddings(dp_noise=False)

    def test_model_weights_layer_shape_validation(self) -> None:
        """load_model_weights must reject mismatched layer shapes or truncated weights."""
        model = GraphSAGEModel(input_dim=12, hidden_dim=32, embedding_dim=16, num_layers=2)

        # Corrupted layer count
        bad_weights_count = ModelWeights(
            layer_shapes=[(32, 12)],
            flat_weights=[0.1] * 384,
        )
        with pytest.raises(ValueError, match="layer count mismatch"):
            model.load_model_weights(bad_weights_count)

        # Corrupted layer shape
        bad_weights_shape = ModelWeights(
            layer_shapes=[(99, 99), (32, 12), (32,), (16, 32), (16, 32), (16,)],
            flat_weights=[0.1] * 20000,
        )
        with pytest.raises(ValueError, match="layer shape mismatch"):
            model.load_model_weights(bad_weights_shape)

        # Truncated flat weights
        valid_shapes = [tuple(p.shape) for p in model.sage_layers.parameters()]
        truncated_weights = ModelWeights(
            layer_shapes=valid_shapes,
            flat_weights=[0.1] * 5,  # Far too few elements
        )
        with pytest.raises(ValueError, match="truncated"):
            model.load_model_weights(truncated_weights)

    def test_privacy_policy_classifier_head_exclusion(self) -> None:
        """Federated model weights export must strictly exclude the classifier head."""
        model = GraphSAGEModel(input_dim=12, hidden_dim=64, embedding_dim=32, num_layers=2)

        # Export for federation
        fed_weights = model.to_model_weights(include_classifier=False)
        # Export with classifier
        local_weights = model.to_model_weights(include_classifier=True)

        assert fed_weights.num_parameters < local_weights.num_parameters
        # Classifier layers (32->16, 16->1) have shapes (16, 32), (16,), (1, 16), (1,)
        assert (16, 32) not in fed_weights.layer_shapes
        assert (1, 16) not in fed_weights.layer_shapes

    def test_similarity_query_budget_enforcement_and_reset(
        self, clean_graph_engine: GraphEngine, embedding_service: GraphEmbeddingService
    ) -> None:
        """Query budget must cap membership inference attacks and allow controlled resets."""
        engine = clean_graph_engine
        target = _make_entity("target_victim")
        engine.register_entity(target)
        for i in range(1, 4):
            engine.register_entity(_make_entity(f"sim_{i}"))

        embedding_service.train_local_gnn(bank_id="bank_alpha", epochs=1)
        embedding_service.max_query_budget = 3

        for _ in range(3):
            embedding_service.find_similar_entities("target_victim")

        assert embedding_service.is_budget_exhausted("target_victim") is True
        assert embedding_service.get_query_count("target_victim") == 3

        # Fourth call should be rejected with empty list
        rejected = embedding_service.find_similar_entities("target_victim")
        assert rejected == []

        # Reset budget
        embedding_service.reset_query_budgets()
        assert embedding_service.is_budget_exhausted("target_victim") is False
        assert embedding_service.get_query_count("target_victim") == 0

    def test_thread_concurrency_safety(
        self, clean_graph_engine: GraphEngine, embedding_service: GraphEmbeddingService
    ) -> None:
        """Concurrent multi-threaded inductive inference and similarity search must be race-free."""
        engine = clean_graph_engine
        for i in range(1, 10):
            engine.register_entity(_make_entity(f"thread_node_{i}"))
        for i in range(1, 8):
            engine.add_relationship(_make_rel(f"tr_{i}", f"thread_node_{i}", f"thread_node_{i+1}"))

        embedding_service.train_local_gnn(bank_id="bank_alpha", epochs=2)

        def worker(idx: int) -> int:
            node_id = f"thread_node_{idx}"
            emb = embedding_service.get_embedding(node_id, allow_inductive=True)
            sims = embedding_service.find_similar_entities(node_id, top_k=2, threshold=0.1)
            stats = embedding_service.get_embedding_stats()
            return len(sims) + stats["num_embedded_nodes"] + (1 if emb is not None else 0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(worker, i) for i in range(1, 7)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 6
        assert all(r >= 1 for r in results)

    def test_gnn_inductive_endpoint_integration(
        self, clean_graph_engine: GraphEngine
    ) -> None:
        """POST /api/v1/graph/embeddings/infer must return inductive embedding for an entity."""
        client = TestClient(app)
        # Register an entity directly in graph engine
        clean_graph_engine.register_entity(_make_entity("endpoint_entity_1"))

        resp = client.post(
            "/api/v1/graph/embeddings/infer",
            json={"entity_id": "endpoint_entity_1", "allow_unseen": True, "dp_noise": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["entity_id"] == "endpoint_entity_1"
        assert len(data["embedding"]) == 64
        assert data["dimension"] == 64
        assert "is_inductive" in data
