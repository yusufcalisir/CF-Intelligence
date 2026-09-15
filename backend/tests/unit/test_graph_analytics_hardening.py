"""Hardening tests for Graph Engine & Topological Ring Analytics.

Verifies:
1. Cyclic transaction loop detection (L in [3, 7]).
2. Canonical rotation deduplication.
3. Cycle length bounds enforcement.
4. Cross-bank ring indicators and risk scoring.
5. Bank ID tenant scoping for mule rings.
6. Fan-in smurfing detection (aggregation mules).
7. Fan-out smurfing detection (dispersion mules).
8. Multi-hop layering smurfing detection (transit mules).
9. Safe parameterized Cypher query execution and mutation rejection.
10. Thread concurrency safety under multi-threaded operations.
"""

from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime

import pytest

from app.application.services.graph_engine import GraphEngine
from app.domain.entities_phase2 import Entity, Relationship
from app.domain.enums import EntityType, RelationshipType, RiskLevel


@pytest.fixture
def clean_graph_engine() -> GraphEngine:
    engine = GraphEngine()
    engine._entities.clear()
    engine._relationships.clear()
    return engine


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
        attributes={"country": "US"},
        risk_level=risk_level,
        alert_count=1,
        first_seen=now,
        last_seen=now,
    )


def _make_rel(
    rel_id: str,
    src: str,
    tgt: str,
    rel_type: RelationshipType = RelationshipType.TRANSACTS_WITH,
    amount: float = 1500.0,
) -> Relationship:
    return Relationship(
        id=rel_id,
        source_entity_id=src,
        target_entity_id=tgt,
        relationship_type=rel_type,
        confidence=1.0,
        evidence={"amount": amount, "currency": "USD"},
        created_at=datetime.now(UTC),
    )


class TestGraphAnalyticsHardening:
    def test_cyclic_mule_ring_detection_length_3(self, clean_graph_engine: GraphEngine) -> None:
        """3-hop cyclic money flow (A -> B -> C -> A) must be detected as a mule ring."""
        engine = clean_graph_engine
        e_a = _make_entity("node_a", bank_id="bank_1", risk_level=RiskLevel.HIGH)
        e_b = _make_entity("node_b", bank_id="bank_2", risk_level=RiskLevel.MEDIUM)
        e_c = _make_entity("node_c", bank_id="bank_3", risk_level=RiskLevel.HIGH)

        for e in (e_a, e_b, e_c):
            engine.register_entity(e)

        engine.add_relationship(_make_rel("r1", "node_a", "node_b", amount=5000.0))
        engine.add_relationship(_make_rel("r2", "node_b", "node_c", amount=4900.0))
        engine.add_relationship(_make_rel("r3", "node_c", "node_a", amount=4800.0))

        rings = engine.detect_cyclic_mule_rings(min_length=3, max_length=7)
        assert len(rings) == 1
        ring = rings[0]
        assert ring["length"] == 3
        assert set(ring["entity_ids"]) == {"node_a", "node_b", "node_c"}
        assert ring["is_cross_bank"] is True
        assert set(ring["banks_involved"]) == {"bank_1", "bank_2", "bank_3"}
        assert ring["total_volume"] == 14700.0
        assert ring["risk_score"] > 0.7

    def test_canonical_rotation_deduplication(self, clean_graph_engine: GraphEngine) -> None:
        """Regardless of discovery order, identical cycles must map to the canonical min-index rotation."""
        engine = clean_graph_engine
        # Cycle: z_node -> m_node -> a_node -> z_node
        for nid in ("z_node", "m_node", "a_node"):
            engine.register_entity(_make_entity(nid))

        engine.add_relationship(_make_rel("r1", "z_node", "m_node"))
        engine.add_relationship(_make_rel("r2", "m_node", "a_node"))
        engine.add_relationship(_make_rel("r3", "a_node", "z_node"))

        rings = engine.detect_cyclic_mule_rings(min_length=3, max_length=5)
        assert len(rings) == 1
        # Canonical rotation must start with lexicographical minimum 'a_node'
        assert rings[0]["entity_ids"] == ["a_node", "z_node", "m_node"]

    def test_ring_length_filtering_bounds(self, clean_graph_engine: GraphEngine) -> None:
        """Cycles shorter than min_length or longer than max_length must be excluded."""
        engine = clean_graph_engine
        # 2-hop loop: x <-> y
        engine.register_entity(_make_entity("x"))
        engine.register_entity(_make_entity("y"))
        engine.add_relationship(_make_rel("r_xy", "x", "y"))
        engine.add_relationship(_make_rel("r_yx", "y", "x"))

        # 4-hop loop: 1 -> 2 -> 3 -> 4 -> 1
        for i in range(1, 5):
            engine.register_entity(_make_entity(f"hop_{i}"))
        for i in range(1, 4):
            engine.add_relationship(_make_rel(f"r_hop_{i}", f"hop_{i}", f"hop_{i+1}"))
        engine.add_relationship(_make_rel("r_hop_4", "hop_4", "hop_1"))

        # min_length=3, max_length=3 (4-hop and 2-hop should both be excluded)
        rings_l3 = engine.detect_cyclic_mule_rings(min_length=3, max_length=3)
        assert len(rings_l3) == 0

        # min_length=3, max_length=4 should capture only the 4-hop ring
        rings_l4 = engine.detect_cyclic_mule_rings(min_length=3, max_length=4)
        assert len(rings_l4) == 1
        assert rings_l4[0]["length"] == 4

    def test_cross_bank_ring_indicators(self, clean_graph_engine: GraphEngine) -> None:
        """Cross-bank rings must have is_cross_bank=True and higher risk score than single-bank rings."""
        engine = clean_graph_engine
        # Ring 1: All in bank_single
        for nid in ("s1", "s2", "s3"):
            engine.register_entity(_make_entity(nid, bank_id="bank_single", risk_level=RiskLevel.LOW))
        engine.add_relationship(_make_rel("rs1", "s1", "s2"))
        engine.add_relationship(_make_rel("rs2", "s2", "s3"))
        engine.add_relationship(_make_rel("rs3", "s3", "s1"))

        # Ring 2: Cross bank (b1, b2, b3)
        for nid, b in [("c1", "b1"), ("c2", "b2"), ("c3", "b3")]:
            engine.register_entity(_make_entity(nid, bank_id=b, risk_level=RiskLevel.LOW))
        engine.add_relationship(_make_rel("rc1", "c1", "c2"))
        engine.add_relationship(_make_rel("rc2", "c2", "c3"))
        engine.add_relationship(_make_rel("rc3", "c3", "c1"))

        rings = engine.detect_cyclic_mule_rings(min_length=3, max_length=3)
        assert len(rings) == 2

        single_ring = next(r for r in rings if "s1" in r["entity_ids"])
        cross_ring = next(r for r in rings if "c1" in r["entity_ids"])

        assert single_ring["is_cross_bank"] is False
        assert cross_ring["is_cross_bank"] is True
        assert cross_ring["risk_score"] > single_ring["risk_score"]

    def test_ring_filtering_by_bank_id(self, clean_graph_engine: GraphEngine) -> None:
        """Filtering by bank_id should only return rings containing that institution."""
        engine = clean_graph_engine
        for nid in ("u1", "u2", "u3"):
            engine.register_entity(_make_entity(nid, bank_id="chase"))
        engine.add_relationship(_make_rel("r1", "u1", "u2"))
        engine.add_relationship(_make_rel("r2", "u2", "u3"))
        engine.add_relationship(_make_rel("r3", "u3", "u1"))

        # Should find with bank_id="chase"
        chase_rings = engine.detect_cyclic_mule_rings(bank_id="chase")
        assert len(chase_rings) == 1

        # Should find zero with bank_id="citi"
        citi_rings = engine.detect_cyclic_mule_rings(bank_id="citi")
        assert len(citi_rings) == 0

    def test_smurfing_fan_in_detection(self, clean_graph_engine: GraphEngine) -> None:
        """Multiple small deposits fanning in to a central aggregator hub."""
        engine = clean_graph_engine
        hub = _make_entity("hub_aggregator", bank_id="bank_alpha", risk_level=RiskLevel.HIGH)
        engine.register_entity(hub)

        for i in range(1, 5):
            spoke = _make_entity(f"smurf_in_{i}", bank_id=f"bank_{i}")
            engine.register_entity(spoke)
            engine.add_relationship(_make_rel(f"rin_{i}", spoke.id, hub.id, amount=950.0))

        patterns = engine.detect_smurfing_patterns(min_fan=3)
        fan_in_patterns = [p for p in patterns if p["pattern_type"] == "fan_in"]
        assert len(fan_in_patterns) == 1
        p = fan_in_patterns[0]
        assert p["hub_entity_id"] == "hub_aggregator"
        assert p["fan_degree"] == 4
        assert p["is_cross_bank"] is True
        assert p["total_volume"] == 3800.0

    def test_smurfing_fan_out_detection(self, clean_graph_engine: GraphEngine) -> None:
        """Single disperser hub distributing funds to multiple destination mules."""
        engine = clean_graph_engine
        hub = _make_entity("hub_disperser", bank_id="bank_beta", risk_level=RiskLevel.HIGH)
        engine.register_entity(hub)

        for i in range(1, 5):
            spoke = _make_entity(f"smurf_out_{i}", bank_id="bank_beta")
            engine.register_entity(spoke)
            engine.add_relationship(_make_rel(f"rout_{i}", hub.id, spoke.id, amount=990.0))

        patterns = engine.detect_smurfing_patterns(min_fan=3)
        fan_out_patterns = [p for p in patterns if p["pattern_type"] == "fan_out"]
        assert len(fan_out_patterns) == 1
        p = fan_out_patterns[0]
        assert p["hub_entity_id"] == "hub_disperser"
        assert p["fan_degree"] == 4
        assert p["total_volume"] == 3960.0

    def test_smurfing_multi_hop_layering(self, clean_graph_engine: GraphEngine) -> None:
        """Transit mule hub experiencing both fan-in and fan-out (layering)."""
        engine = clean_graph_engine
        transit = _make_entity("transit_hub", bank_id="bank_gamma", risk_level=RiskLevel.HIGH)
        engine.register_entity(transit)

        # 2 in-sources
        for i in range(1, 3):
            src = _make_entity(f"layer_in_{i}")
            engine.register_entity(src)
            engine.add_relationship(_make_rel(f"r_lin_{i}", src.id, transit.id, amount=2000.0))

        # 2 out-targets
        for i in range(1, 3):
            tgt = _make_entity(f"layer_out_{i}")
            engine.register_entity(tgt)
            engine.add_relationship(_make_rel(f"r_lout_{i}", transit.id, tgt.id, amount=1900.0))

        patterns = engine.detect_smurfing_patterns(min_fan=3)
        layering = [p for p in patterns if p["pattern_type"] == "multi_hop_layering"]
        assert len(layering) == 1
        l_pat = layering[0]
        assert l_pat["hub_entity_id"] == "transit_hub"
        assert l_pat["depth"] == 2
        assert l_pat["fan_degree"] == 4
        assert l_pat["total_volume"] == 7800.0

    def test_safe_cypher_execution_read_only_and_mutation_rejection(
        self, clean_graph_engine: GraphEngine
    ) -> None:
        """Cypher runner must execute read-only queries and reject mutating statements."""
        engine = clean_graph_engine
        engine.register_entity(_make_entity("c1", entity_type=EntityType.CUSTOMER))
        engine.register_entity(_make_entity("m1", entity_type=EntityType.MERCHANT))

        # Valid read query in in-memory mode
        res = engine.execute_cypher("MATCH (n:Entity) RETURN n.entity_type as type, count(n) as count")
        assert isinstance(res, list)
        assert len(res) > 0

        # Mutation query with read_only=True must raise ValueError
        mutating_queries = [
            "CREATE (n:Entity {id: 'hack'})",
            "MATCH (n:Entity) DELETE n",
            "MERGE (n:Entity {id: 'hack'})",
            "MATCH (n:Entity) SET n.risk = 'CRITICAL'",
            "DROP INDEX entity_idx",
            "MATCH (n) DETACH DELETE n",
        ]
        for bad_q in mutating_queries:
            with pytest.raises(ValueError, match="mutating keyword"):
                engine.execute_cypher(bad_q, read_only=True)

    def test_graph_engine_concurrency_thread_safety(
        self, clean_graph_engine: GraphEngine
    ) -> None:
        """Concurrent multi-threaded entity registration, edge insertion, and cycle detection."""
        engine = clean_graph_engine

        def worker(thread_idx: int) -> int:
            base_id = f"t{thread_idx}"
            e1 = _make_entity(f"{base_id}_1")
            e2 = _make_entity(f"{base_id}_2")
            e3 = _make_entity(f"{base_id}_3")
            engine.register_entity(e1)
            engine.register_entity(e2)
            engine.register_entity(e3)

            engine.add_relationship(_make_rel(f"r_{base_id}_1", e1.id, e2.id))
            engine.add_relationship(_make_rel(f"r_{base_id}_2", e2.id, e3.id))
            engine.add_relationship(_make_rel(f"r_{base_id}_3", e3.id, e1.id))

            rings = engine.detect_cyclic_mule_rings(min_length=3, max_length=3)
            smurfs = engine.detect_smurfing_patterns(min_fan=2)
            return len(rings) + len(smurfs)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(worker, i) for i in range(6)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 6
        assert all(r >= 1 for r in results)
