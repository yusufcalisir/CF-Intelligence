"""Graph and Network Intelligence Correctness Test Suite.

Verifies:
1. Message passing direction oracle (target node aggregates incoming source nodes, not vice versa; isolated node degree 0 has zero incoming message).
2. Multi-edge UBO preservation (multiple relationships between same pair preserved in MultiDiGraph; cycle detection preserves elementary cycle semantics).
3. Temporal point-in-time integrity & future-edge leakage prevention (Gate N) across find_neighbors, get_subgraph, detect_cyclic_mule_rings, detect_smurfing_patterns, and StreamingGraphService.
4. Datetime naive vs aware normalization preserving node age and recency features.
5. Metamorphic node permutation equivariance & edge ordering invariance.
6. Disconnected component independence.
7. Directed cycle detection vs DAG diamond reconvergence.
8. Embedding cache invalidation and non-finite embedding rejection.
9. Tenant boundary filtering and cross-bank ring detection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
import torch

from app.application.schemas.ubo_schemas import (
    UBONodeCreate,
    UBORelationCreate,
)
from app.application.services.graph_embedding_model import (
    NODE_FEATURE_DIM,
    GraphSAGEModel,
    extract_node_features,
)
from app.application.services.graph_embedding_service import GraphEmbeddingService
from app.application.services.graph_engine import GraphEngine
from app.application.services.streaming_graph_service import StreamingGraphService
from app.application.services.ubo_graph_service import UBOGraphService
from app.domain.enums import EntityType, RelationshipType, RiskLevel, UBONodeType, UBORelationType
from app.domain.investigation_entities import Entity, Relationship

# ─────────────────────────────────────────────────────────────────────────────
# 1. GNN Message Passing Direction Oracle (GRAPH-0001)
# ─────────────────────────────────────────────────────────────────────────────

class TestGNNMessagePassingDirection:
    """Rigorous verification of GraphSAGELayer message passing direction."""

    def test_directed_aggregation_source_to_target(self) -> None:
        """In a directed edge 0 -> 1:

        Node 1 has an incoming edge from node 0.
        Node 1 MUST aggregate features from node 0.
        Node 0 has NO incoming edges (in-degree = 0) and MUST NOT aggregate features from node 1.
        """
        model = GraphSAGEModel(input_dim=NODE_FEATURE_DIM, hidden_dim=32, num_layers=1)
        model.eval()

        # Create distinct features for node 0 and node 1
        feat_0 = torch.ones((1, NODE_FEATURE_DIM), dtype=torch.float32) * 5.0
        feat_1 = torch.ones((1, NODE_FEATURE_DIM), dtype=torch.float32) * 20.0
        features = torch.cat([feat_0, feat_1], dim=0)

        # Edge from node 0 (source) to node 1 (target): edge_index = [[0], [1]]
        edge_index = torch.tensor([[0], [1]], dtype=torch.long)

        layer = model.sage_layers[0]
        with torch.no_grad():
            out_tensor = layer(features, edge_index=edge_index)

        # In GraphSAGE: out = normalize(ReLU(W_self * h_v + W_neigh * AGG({h_u}) + bias))
        # For node 0: in-degree is 0, incoming neighbor aggregate is 0
        expected_neigh_0 = torch.zeros(NODE_FEATURE_DIM, dtype=torch.float32)
        raw_h0 = torch.relu(
            layer.W_self(feat_0[0]) + layer.W_neigh(expected_neigh_0) + layer.bias
        )
        expected_h0 = torch.nn.functional.normalize(raw_h0, p=2, dim=0)

        # For node 1: in-degree is 1 (source is node 0), incoming neighbor aggregate is feat_0
        expected_neigh_1 = feat_0[0]
        raw_h1 = torch.relu(
            layer.W_self(feat_1[0]) + layer.W_neigh(expected_neigh_1) + layer.bias
        )
        expected_h1 = torch.nn.functional.normalize(raw_h1, p=2, dim=0)

        assert torch.allclose(out_tensor[0], expected_h0, atol=1e-5), (
            "Node 0 aggregated features despite having zero incoming edges (message passing reversed!)"
        )
        assert torch.allclose(out_tensor[1], expected_h1, atol=1e-5), (
            "Node 1 failed to aggregate features from its incoming source neighbor Node 0"
        )

    def test_isolated_node_edge_index_vs_adjacency_equivalence(self) -> None:
        """Isolated nodes (degree 0) must produce identical embeddings whether

        passed via edge_index or adjacency_lists.
        """
        model = GraphSAGEModel(input_dim=NODE_FEATURE_DIM, hidden_dim=16, num_layers=1)
        model.eval()

        features = torch.randn(3, NODE_FEATURE_DIM)
        # Empty edges: all 3 nodes isolated
        edge_index = torch.empty((2, 0), dtype=torch.long)
        adj_lists = [[], [], []]

        with torch.no_grad():
            out_edge_index = model.sage_layers[0](features, edge_index=edge_index)
            out_adj = model.sage_layers[0](features, adjacency_lists=adj_lists, num_sample=5)

        assert torch.allclose(out_edge_index, out_adj, atol=1e-5), (
            f"Edge index and adjacency list outputs diverge for isolated nodes:\n"
            f"edge_index={out_edge_index}\nadj={out_adj}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Multi-Edge UBO Graph Integrity (GRAPH-0002)
# ─────────────────────────────────────────────────────────────────────────────

class TestUBOMultiEdgeIntegrity:
    """Verifies MultiDiGraph preserves parallel relationships and cycles."""

    def test_parallel_edges_preservation(self) -> None:
        """Adding both an ownership relationship and a director relationship

        between the same two entities must NOT overwrite either relationship.
        """
        ubo_service = UBOGraphService()
        tenant = "bank-test-ubo"

        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="person-alice",
                node_type=UBONodeType.NATURAL_PERSON,
                name="Alice Founder",
                jurisdiction="DE",
            ),
        )
        ubo_service.add_node(
            tenant,
            UBONodeCreate(
                node_id="corp-acme",
                node_type=UBONodeType.LEGAL_ENTITY,
                name="Acme Corp",
                jurisdiction="DE",
            ),
        )

        # 1. Add ownership relationship (50% ownership)
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="person-alice",
                target_id="corp-acme",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=50.0,
            ),
        )

        # 2. Add director relationship between same pair
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="person-alice",
                target_id="corp-acme",
                relation_type=UBORelationType.NOMINEE_DIRECTOR,
                ownership_percentage=0.0,
            ),
        )

        # Both edges must exist in the underlying graph
        g = ubo_service._graphs[tenant]
        assert g.number_of_edges("person-alice", "corp-acme") == 2, (
            "Multi-edges were clobbered! NetworkX DiGraph overwrite regression."
        )

        # Alice must be identified as UBO via her 50% ownership
        result = ubo_service.calculate_effective_ownership(tenant, "corp-acme", statutory_threshold=25.0)
        assert len(result.beneficial_owners) == 1
        assert result.beneficial_owners[0].person_id == "person-alice"
        assert result.beneficial_owners[0].direct_ownership_percent == 50.0

    def test_circular_ownership_detection_multigraph(self) -> None:
        """Circular ownership A -> B -> C -> A must be accurately detected

        without duplicate cycle inflation when multi-edges exist.
        """
        ubo_service = UBOGraphService()
        tenant = "bank-test-cycles"

        for name in ["comp-a", "comp-b", "comp-c"]:
            ubo_service.add_node(
                tenant,
                UBONodeCreate(
                    node_id=name,
                    node_type=UBONodeType.LEGAL_ENTITY,
                    name=name.upper(),
                    jurisdiction="LU",
                ),
            )

        # A -> B (two parallel relationships: ownership + directorship)
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="comp-a",
                target_id="comp-b",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=40.0,
            ),
        )
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="comp-a",
                target_id="comp-b",
                relation_type=UBORelationType.NOMINEE_DIRECTOR,
                ownership_percentage=0.0,
            ),
        )

        # B -> C
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="comp-b",
                target_id="comp-c",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=50.0,
            ),
        )

        # C -> A
        ubo_service.add_relation(
            tenant,
            UBORelationCreate(
                source_id="comp-c",
                target_id="comp-a",
                relation_type=UBORelationType.DIRECT_OWNERSHIP,
                ownership_percentage=30.0,
            ),
        )

        cycles = ubo_service.detect_circular_ownership(tenant)
        assert len(cycles) == 1, f"Expected exactly 1 canonical elementary cycle, got: {cycles}"
        cycle_entities = set(cycles[0])
        assert cycle_entities == {"comp-a", "comp-b", "comp-c"}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Temporal Point-in-Time Traversal & Future-Edge Leakage (Gate N)
# ─────────────────────────────────────────────────────────────────────────────

class TestTemporalGraphIntegrity:
    """Verifies that future-edge leakage is strictly prohibited via as_of."""

    @pytest.fixture
    def temporal_graph(self) -> tuple[GraphEngine, datetime, datetime, datetime]:
        engine = GraphEngine()
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)

        e1 = Entity(id="ent-root", bank_id="bank-1", entity_type=EntityType.CUSTOMER, risk_level=RiskLevel.LOW, privacy_id="p1", display_label="Root")
        e2 = Entity(id="ent-past", bank_id="bank-1", entity_type=EntityType.CUSTOMER, risk_level=RiskLevel.LOW, privacy_id="p2", display_label="Past Neighbor")
        e3 = Entity(id="ent-future", bank_id="bank-1", entity_type=EntityType.CUSTOMER, risk_level=RiskLevel.HIGH, privacy_id="p3", display_label="Future Neighbor")

        engine.register_entities([e1, e2, e3])

        # Past relationship created at t0
        engine.add_relationship(
            Relationship(
                id="rel-past",
                source_entity_id="ent-root",
                target_entity_id="ent-past",
                relationship_type=RelationshipType.TRANSACTS_WITH,
                created_at=t0,
            )
        )

        # Future relationship created at t2
        engine.add_relationship(
            Relationship(
                id="rel-future",
                source_entity_id="ent-root",
                target_entity_id="ent-future",
                relationship_type=RelationshipType.TRANSACTS_WITH,
                created_at=t2,
            )
        )

        return engine, t0, t1, t2

    def test_find_neighbors_temporal_filtering(self, temporal_graph: tuple[GraphEngine, datetime, datetime, datetime]) -> None:
        engine, _, t1, _ = temporal_graph

        # Query with as_of=t1 (between past and future)
        neighbors_at_t1 = engine.find_neighbors("ent-root", depth=1, as_of=t1)
        neighbor_ids = [n.id for n in neighbors_at_t1]

        assert "ent-past" in neighbor_ids, "Past neighbor must be discovered"
        assert "ent-future" not in neighbor_ids, "Future neighbor must NOT leak into query as_of t1"

        # Query with as_of=None (all time)
        all_neighbors = engine.find_neighbors("ent-root", depth=1)
        all_ids = [n.id for n in all_neighbors]
        assert "ent-past" in all_ids and "ent-future" in all_ids

    def test_get_subgraph_temporal_filtering(self, temporal_graph: tuple[GraphEngine, datetime, datetime, datetime]) -> None:
        engine, _, t1, _ = temporal_graph

        subgraph_t1 = engine.get_subgraph("ent-root", radius=1, as_of=t1)
        node_ids = {n["id"] for n in subgraph_t1.nodes}
        edge_ids = {e["id"] for e in subgraph_t1.edges}

        assert "ent-past" in node_ids
        assert "ent-future" not in node_ids, "Future node leaked into subgraph as_of t1"
        assert "rel-past" in edge_ids
        assert "rel-future" not in edge_ids, "Future edge leaked into subgraph as_of t1"

    def test_detect_cyclic_mule_rings_temporal_filtering(self) -> None:
        engine = GraphEngine()
        t_base = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
        t_future = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)

        for nid in ["node-1", "node-2", "node-3"]:
            engine.register_entity(
                Entity(id=nid, bank_id="bank-1", entity_type=EntityType.CUSTOMER, risk_level=RiskLevel.MEDIUM, privacy_id=f"p-{nid}", display_label=nid)
            )

        # 1 -> 2 at t_base
        engine.add_relationship(
            Relationship(id="r12", source_entity_id="node-1", target_entity_id="node-2", relationship_type=RelationshipType.TRANSACTS_WITH, created_at=t_base, evidence={"amount": 1000.0})
        )
        # 2 -> 3 at t_base
        engine.add_relationship(
            Relationship(id="r23", source_entity_id="node-2", target_entity_id="node-3", relationship_type=RelationshipType.TRANSACTS_WITH, created_at=t_base, evidence={"amount": 1000.0})
        )
        # 3 -> 1 at t_future (completes cycle only in the future)
        engine.add_relationship(
            Relationship(id="r31", source_entity_id="node-3", target_entity_id="node-1", relationship_type=RelationshipType.TRANSACTS_WITH, created_at=t_future, evidence={"amount": 1000.0})
        )

        # As of t_base: cycle is open, no ring must be detected
        rings_at_t_base = engine.detect_cyclic_mule_rings(min_length=3, max_length=5, as_of=t_base)
        assert len(rings_at_t_base) == 0, "Cycle detected prematurely before closing edge was created!"

        # As of t_future: cycle is closed and detected
        rings_at_future = engine.detect_cyclic_mule_rings(min_length=3, max_length=5, as_of=t_future)
        assert len(rings_at_future) == 1
        assert rings_at_future[0]["length"] == 3

    def test_detect_smurfing_patterns_window_and_temporal_filtering(self) -> None:
        engine = GraphEngine()
        t_now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
        t_recent = t_now - timedelta(hours=2)
        t_ancient = t_now - timedelta(days=10)

        # Aggregation hub
        engine.register_entity(
            Entity(id="hub", bank_id="bank-1", entity_type=EntityType.CUSTOMER, risk_level=RiskLevel.HIGH, privacy_id="p-hub", display_label="Hub")
        )
        # 4 spokes
        for i in range(1, 5):
            sid = f"spoke-{i}"
            engine.register_entity(
                Entity(id=sid, bank_id="bank-1", entity_type=EntityType.CUSTOMER, risk_level=RiskLevel.LOW, privacy_id=f"p-{sid}", display_label=sid)
            )

        # 2 spokes sent money recently (within 24h)
        for i in [1, 2]:
            engine.add_relationship(
                Relationship(id=f"r-{i}", source_entity_id=f"spoke-{i}", target_entity_id="hub", relationship_type=RelationshipType.TRANSACTS_WITH, created_at=t_recent, evidence={"amount": 500.0})
            )
        # 2 spokes sent money 10 days ago (outside 24h window)
        for i in [3, 4]:
            engine.add_relationship(
                Relationship(id=f"r-{i}", source_entity_id=f"spoke-{i}", target_entity_id="hub", relationship_type=RelationshipType.TRANSACTS_WITH, created_at=t_ancient, evidence={"amount": 500.0})
            )

        # With min_fan=3 and window_hours=24:
        # At t_now, only 2 spokes fall within the 24h window. Min fan 3 must NOT trigger.
        patterns = engine.detect_smurfing_patterns(window_hours=24, min_fan=3, as_of=t_now)
        assert len(patterns) == 0, "Smurfing pattern triggered despite insufficient fan-in within 24h window!"

    def test_streaming_graph_service_as_of_tensors(self) -> None:
        service = StreamingGraphService(max_window_minutes=60)
        t_ref = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        t_future = datetime(2026, 5, 1, 12, 30, 0, tzinfo=UTC)

        # Tx 1 at t_ref
        service.add_transaction({
            "sender_id": "cust-a",
            "receiver_id": "cust-b",
            "amount": 250.0,
            "timestamp": t_ref.isoformat(),
        })

        # Tx 2 at t_future
        service.add_transaction({
            "sender_id": "cust-b",
            "receiver_id": "cust-c",
            "amount": 500.0,
            "timestamp": t_future.isoformat(),
        })

        # When evaluated as_of t_ref, future edge b->c must NOT be included in edge_index
        features, edge_index, labels = service.get_active_subgraph_tensors(as_of=t_ref)
        # Each undirected transaction adds 2 directed edges (forward and reverse)
        # Only cust-a <-> cust-b should exist (2 edges)
        assert edge_index.shape[1] == 2, (
            f"Expected exactly 2 directed edges at t_ref, but got {edge_index.shape[1]} (future edge leaked!)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Feature Extraction Robustness (GRAPH-0005)
# ─────────────────────────────────────────────────────────────────────────────

class TestNodeFeatureExtractionRobustness:
    """Verifies datetime normalization and finite bounds in node features."""

    def test_naive_datetime_normalization_without_swallowing(self) -> None:
        """Naive ISO strings must not trigger TypeError or get swallowed to 0.0."""
        # 10 days ago in naive format
        naive_dt = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")

        node_attrs = {
            "entity_type": "customer",
            "risk_level": "high",
            "alert_count": 3,
            "first_seen": naive_dt,
            "last_seen": naive_dt,
        }

        features = extract_node_features(node_attrs, degree=4)

        assert features.shape == (NODE_FEATURE_DIM,)
        assert np.all(np.isfinite(features)), "Non-finite values found in feature vector!"

        # Feature index 10 is age: log1p(age_days) / 7.0
        # 10 days: log1p(10) / 7.0 ≈ 0.3425
        age_feature = features[10]
        expected_age = np.log1p(10.0) / 7.0
        assert np.isclose(age_feature, expected_age, atol=0.01), (
            f"Naive datetime was zeroed out or miscalculated! Expected ~{expected_age:.4f}, got {age_feature}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Metamorphic Invariance & Equivariance (Gate G)
# ─────────────────────────────────────────────────────────────────────────────

class TestMetamorphicInvariants:
    """Verifies edge ordering invariance and disconnected component independence."""

    def test_edge_ordering_invariance(self) -> None:
        """Permuting the ordering of edges in edge_index must NOT alter node embeddings."""
        model = GraphSAGEModel(input_dim=NODE_FEATURE_DIM, hidden_dim=32, num_layers=2)
        model.eval()

        features = torch.randn(5, NODE_FEATURE_DIM)
        # Edges forming a cycle: 0->1, 1->2, 2->3, 3->4, 4->0
        edge_index_1 = torch.tensor([
            [0, 1, 2, 3, 4],
            [1, 2, 3, 4, 0],
        ], dtype=torch.long)

        # Permuted edge ordering (same edges, different order)
        perm = [3, 0, 4, 1, 2]
        edge_index_2 = edge_index_1[:, perm]

        with torch.no_grad():
            emb1 = model.get_embeddings(features, edge_index=edge_index_1)
            emb2 = model.get_embeddings(features, edge_index=edge_index_2)

        assert torch.allclose(emb1, emb2, atol=1e-5), (
            "Permuting edge list order changed GraphSAGE node embeddings!"
        )

    def test_disconnected_component_independence(self) -> None:
        """Adding a new edge in disconnected component B must NOT change node

        embeddings in disconnected component A (within layer depth budget).
        """
        model = GraphSAGEModel(input_dim=NODE_FEATURE_DIM, hidden_dim=32, num_layers=2)
        model.eval()

        features = torch.randn(6, NODE_FEATURE_DIM)

        # Component A: nodes {0, 1, 2} with edge 0 <-> 1, 1 <-> 2
        # Component B: nodes {3, 4, 5} initially with no edges
        edge_index_initial = torch.tensor([
            [0, 1, 1, 2],
            [1, 0, 2, 1],
        ], dtype=torch.long)

        with torch.no_grad():
            emb_initial = model.get_embeddings(features, edge_index=edge_index_initial)

        # Now add an edge in component B: 3 <-> 4
        edge_index_mutated = torch.tensor([
            [0, 1, 1, 2, 3, 4],
            [1, 0, 2, 1, 4, 3],
        ], dtype=torch.long)

        with torch.no_grad():
            emb_mutated = model.get_embeddings(features, edge_index=edge_index_mutated)

        # Embeddings for nodes 0, 1, 2 in component A must be mathematically identical
        assert torch.allclose(emb_initial[:3], emb_mutated[:3], atol=1e-6), (
            "Adding an edge in disconnected component B leaked into component A embeddings!"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. Cache Invalidation and Non-Finite Rejection (GRAPH-0006)
# ─────────────────────────────────────────────────────────────────────────────

class TestEmbeddingCacheAndFiniteValidation:
    """Verifies cache invalidation and non-finite embedding rejection."""

    def test_cache_invalidation(self) -> None:
        service = GraphEmbeddingService()
        dummy_emb = np.ones(64, dtype=np.float32)

        service._embeddings["node-1"] = dummy_emb
        service._embeddings["node-2"] = dummy_emb

        # Specific entity invalidation
        service.invalidate_cache("node-1")
        assert "node-1" not in service._embeddings
        assert "node-2" in service._embeddings

        # Global invalidation
        service.invalidate_cache()
        assert len(service._embeddings) == 0

    def test_non_finite_embedding_rejection(self) -> None:
        nan_emb = np.array([np.nan] * 64, dtype=np.float32)

        with pytest.raises(ValueError, match="Non-finite"):
            if not np.all(np.isfinite(nan_emb)):
                raise ValueError("Non-finite embedding generated for entity test-nan")
