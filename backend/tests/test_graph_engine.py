"""Tests for the graph engine."""

import pytest

from app.application.services.graph_engine import GraphEngine
from app.domain.enums import EntityType, RelationshipType
from app.domain.investigation_entities import Entity, Relationship


def _make_entity(eid: str, etype: EntityType = EntityType.CUSTOMER, bank: str = "bank_a") -> Entity:
    return Entity(
        id=eid,
        entity_type=etype,
        privacy_id=f"hash_{eid}",
        bank_id=bank,
        display_label=f"ENT-{eid[:4]}",
    )


def _make_relationship(
    rid: str,
    src: str,
    tgt: str,
    rtype: RelationshipType = RelationshipType.TRANSACTS_WITH,
) -> Relationship:
    return Relationship(
        id=rid,
        source_entity_id=src,
        target_entity_id=tgt,
        relationship_type=rtype,
    )


@pytest.fixture
def graph() -> GraphEngine:
    g = GraphEngine()
    # Build a small graph: A -- B -- C -- D, plus A -- E
    entities = [
        _make_entity("A"),
        _make_entity("B", EntityType.MERCHANT),
        _make_entity("C", EntityType.DEVICE),
        _make_entity("D"),
        _make_entity("E", EntityType.CARD),
    ]
    g.register_entities(entities)
    g.add_relationship(_make_relationship("r1", "A", "B"))
    g.add_relationship(_make_relationship("r2", "B", "C"))
    g.add_relationship(_make_relationship("r3", "C", "D"))
    g.add_relationship(_make_relationship("r4", "A", "E"))
    return g


class TestNeighborTraversal:
    def test_direct_neighbors(self, graph: GraphEngine) -> None:
        neighbors = graph.find_neighbors("A", depth=1)
        neighbor_ids = {n.id for n in neighbors}
        assert neighbor_ids == {"B", "E"}

    def test_depth_2(self, graph: GraphEngine) -> None:
        neighbors = graph.find_neighbors("A", depth=2)
        neighbor_ids = {n.id for n in neighbors}
        assert neighbor_ids == {"B", "E", "C"}

    def test_depth_3(self, graph: GraphEngine) -> None:
        neighbors = graph.find_neighbors("A", depth=3)
        neighbor_ids = {n.id for n in neighbors}
        assert neighbor_ids == {"B", "E", "C", "D"}

    def test_type_filter(self, graph: GraphEngine) -> None:
        neighbors = graph.find_neighbors(
            "B",
            depth=2,
            relationship_types={RelationshipType.TRANSACTS_WITH},
        )
        # All relationships in fixture are TRANSACTS_WITH, so should find all
        assert len(neighbors) > 0


class TestClusterDetection:
    def test_single_cluster(self, graph: GraphEngine) -> None:
        # All nodes are connected — one cluster of size 5
        clusters = graph.detect_clusters(min_size=3)
        assert len(clusters) == 1
        assert len(clusters[0]) == 5

    def test_min_size_filter(self, graph: GraphEngine) -> None:
        clusters = graph.detect_clusters(min_size=6)
        assert len(clusters) == 0

    def test_disconnected_clusters(self) -> None:
        g = GraphEngine()
        # Two disconnected components: {A, B, C} and {D, E, F}
        for eid in ["A", "B", "C", "D", "E", "F"]:
            g.register_entity(_make_entity(eid))
        g.add_relationship(_make_relationship("r1", "A", "B"))
        g.add_relationship(_make_relationship("r2", "B", "C"))
        g.add_relationship(_make_relationship("r3", "D", "E"))
        g.add_relationship(_make_relationship("r4", "E", "F"))

        clusters = g.detect_clusters(min_size=3)
        assert len(clusters) == 2


class TestSubgraph:
    def test_subgraph_generation(self, graph: GraphEngine) -> None:
        subgraph = graph.get_subgraph("A", radius=2)
        assert len(subgraph.nodes) > 0
        assert len(subgraph.edges) > 0
        assert subgraph.center_entity_id == "A"

    def test_subgraph_react_flow_format(self, graph: GraphEngine) -> None:
        subgraph = graph.get_subgraph("A", radius=1)
        # Check React Flow node format
        for node in subgraph.nodes:
            assert "id" in node
            assert "position" in node
            assert "data" in node
            assert "x" in node["position"]
            assert "y" in node["position"]

    def test_nonexistent_entity(self, graph: GraphEngine) -> None:
        subgraph = graph.get_subgraph("NONEXISTENT", radius=2)
        assert len(subgraph.nodes) == 0


class TestSearchAndStats:
    def test_search_by_label(self, graph: GraphEngine) -> None:
        results = graph.search_nodes("ENT-A")
        assert len(results) >= 1

    def test_stats(self, graph: GraphEngine) -> None:
        stats = graph.get_stats()
        assert stats["total_nodes"] == 5
        assert stats["total_edges"] == 4
        assert "customer" in stats["nodes_by_type"]
