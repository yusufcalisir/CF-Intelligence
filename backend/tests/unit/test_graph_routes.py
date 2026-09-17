"""Unit Tests for Property Graph Traversal & GraphSAGE Ring Analytics API.

Validates dual-prefix routing (/api/v1/graph and /v1/graph), alias endpoints,
Pydantic v2 schema compliance, subgraph traversal, mule ring detection,
GraphSAGE embeddings, Cypher read-only safety, and streaming telemetry.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.domain.entities_phase2 import Entity, Relationship
from app.domain.enums import EntityType, RelationshipType, RiskLevel
from app.main import app
from app.presentation.routers.graph import _graph_engine

client = TestClient(app)


@pytest.fixture(autouse=True)
def seed_test_graph_data() -> None:
    """Seed deterministically known entities and relationships for route testing."""
    e1 = Entity(
        id="cust_graph_test_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="priv_hash_cust_1",
        bank_id="bank_a",
        display_label="Test Alpha Corp",
        risk_level=RiskLevel.HIGH,
        alert_count=3,
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
    )
    e2 = Entity(
        id="cust_graph_test_2",
        entity_type=EntityType.CUSTOMER,
        privacy_id="priv_hash_cust_2",
        bank_id="bank_b",
        display_label="Test Beta LLC",
        risk_level=RiskLevel.CRITICAL,
        alert_count=5,
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
    )
    e3 = Entity(
        id="dev_graph_test_1",
        entity_type=EntityType.DEVICE,
        privacy_id="priv_hash_dev_1",
        bank_id="bank_a",
        display_label="Device iPhone 15",
        risk_level=RiskLevel.MEDIUM,
        alert_count=1,
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
    )

    r1 = Relationship(
        id="rel_graph_test_1",
        source_entity_id=e1.id,
        target_entity_id=e2.id,
        relationship_type=RelationshipType.TRANSACTS_WITH,
        confidence=0.95,
        evidence={"amount": 75000.0, "volume": 75000.0},
        created_at=datetime.now(UTC),
    )
    r2 = Relationship(
        id="rel_graph_test_2",
        source_entity_id=e1.id,
        target_entity_id=e3.id,
        relationship_type=RelationshipType.USES,
        confidence=0.88,
        evidence={"ip": "192.168.1.1"},
        created_at=datetime.now(UTC),
    )

    from app.application.services.graph_engine import _entity_to_dict, _relationship_to_dict

    _graph_engine._entities.set(e1.id, _entity_to_dict(e1))
    _graph_engine._entities.set(e2.id, _entity_to_dict(e2))
    _graph_engine._entities.set(e3.id, _entity_to_dict(e3))
    _graph_engine._relationships.set(r1.id, _relationship_to_dict(r1))
    _graph_engine._relationships.set(r2.id, _relationship_to_dict(r2))
    _graph_engine._adjacency = None  # Force rebuild
    yield


def test_graph_dual_prefix_and_stats() -> None:
    """Verifies that stats and summary endpoints work identically under both prefixes."""
    resp1 = client.get("/api/v1/graph/stats/summary")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["total_nodes"] >= 3
    assert data1["total_edges"] >= 2
    assert "Redis" in data1["database_backend"] or "In-memory" in data1["database_backend"]

    # Test /v1/graph/stats/summary
    resp2 = client.get("/v1/graph/stats/summary")
    assert resp2.status_code == 200
    assert resp2.json()["total_nodes"] == data1["total_nodes"]

    # Test /stats alias
    resp3 = client.get("/api/v1/graph/stats")
    assert resp3.status_code == 200
    assert resp3.json()["total_nodes"] == data1["total_nodes"]

    resp4 = client.get("/v1/graph/stats")
    assert resp4.status_code == 200
    assert resp4.json()["total_nodes"] == data1["total_nodes"]


def test_subgraph_traversal_and_aliases() -> None:
    """Verifies subgraph ego-network extraction under both prefixes and alias endpoints."""
    # 1. Traversal under /api/v1/graph/subgraph/{entity_id}
    resp1 = client.get("/api/v1/graph/subgraph/cust_graph_test_1", params={"depth": 2, "max_nodes": 50})
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["center_entity_id"] == "cust_graph_test_1"
    assert len(data1["nodes"]) >= 2
    assert any(n["id"] == "cust_graph_test_1" for n in data1["nodes"])

    # 2. Traversal under /v1/graph/subgraph/{entity_id}
    resp2 = client.get("/v1/graph/subgraph/cust_graph_test_1", params={"depth": 2})
    assert resp2.status_code == 200
    assert resp2.json()["center_entity_id"] == "cust_graph_test_1"

    # 3. Direct path traversal /{entity_id}
    resp3 = client.get("/api/v1/graph/cust_graph_test_1")
    assert resp3.status_code == 200
    assert resp3.json()["center_entity_id"] == "cust_graph_test_1"

    resp4 = client.get("/v1/graph/cust_graph_test_1")
    assert resp4.status_code == 200
    assert resp4.json()["center_entity_id"] == "cust_graph_test_1"

    # 4. Non-existent entity -> 404 Not Found
    resp_missing = client.get("/api/v1/graph/subgraph/non_existent_entity_xyz")
    assert resp_missing.status_code == 404
    assert "not found in graph" in resp_missing.json()["detail"]

    # 5. Empty/blank entity -> 400 Bad Request
    resp_blank = client.get("/api/v1/graph/subgraph/%20")
    assert resp_blank.status_code == 400


def test_nodes_and_search_endpoints() -> None:
    """Verifies node search and listing endpoints."""
    # Search with query string
    resp_search1 = client.get("/api/v1/graph/search/nodes", params={"q": "Alpha"})
    assert resp_search1.status_code == 200
    nodes1 = resp_search1.json()
    assert len(nodes1) >= 1
    assert any("Alpha" in n["display_label"] for n in nodes1)

    # Search under /v1/graph
    resp_search2 = client.get("/v1/graph/search/nodes", params={"q": "Beta"})
    assert resp_search2.status_code == 200
    assert any("Beta" in n["display_label"] for n in resp_search2.json())

    # Listing under /nodes
    resp_nodes1 = client.get("/api/v1/graph/nodes", params={"limit": 10})
    assert resp_nodes1.status_code == 200
    assert len(resp_nodes1.json()) >= 3

    resp_nodes2 = client.get("/v1/graph/nodes", params={"limit": 10})
    assert resp_nodes2.status_code == 200
    assert len(resp_nodes2.json()) >= 3


def test_edges_endpoint() -> None:
    """Verifies graph edges retrieval and filtering."""
    resp1 = client.get("/api/v1/graph/edges", params={"limit": 10})
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["total_edges"] >= 2
    assert data1["count"] >= 2
    assert len(data1["edges"]) >= 2
    assert any(e["source"] == "cust_graph_test_1" for e in data1["edges"])

    # Test under /v1/graph/edges
    resp2 = client.get("/v1/graph/edges", params={"limit": 1})
    assert resp2.status_code == 200
    assert resp2.json()["count"] == 1


def test_mule_ring_detection_and_validation() -> None:
    """Verifies cyclic mule ring detection and query bounds validation."""
    # Under /api/v1/graph/rings
    resp1 = client.get("/api/v1/graph/rings", params={"min_length": 3, "max_length": 7})
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert "total_rings" in data1
    assert "cross_bank_rings" in data1

    # Under /v1/graph/rings/detect
    resp2 = client.get("/v1/graph/rings/detect")
    assert resp2.status_code == 200
    assert "total_rings" in resp2.json()

    # Invalid range: min_length > max_length -> 400 Bad Request
    resp_range_err = client.get("/api/v1/graph/rings", params={"min_length": 6, "max_length": 4})
    assert resp_range_err.status_code == 400
    assert "cannot exceed max_length" in resp_range_err.json()["detail"]

    # Boundary violation: min_length < 3 -> 422 Unprocessable Entity
    resp_bound_err = client.get("/api/v1/graph/rings", params={"min_length": 1})
    assert resp_bound_err.status_code == 422


def test_smurfing_and_community_analytics() -> None:
    """Verifies smurfing detection and community risk metrics."""
    # Smurfing detection
    resp_smurf1 = client.get("/api/v1/graph/smurfing/detect", params={"window_hours": 24, "min_fan": 2})
    assert resp_smurf1.status_code == 200
    smurf_data = resp_smurf1.json()
    assert "total_patterns" in smurf_data

    resp_smurf2 = client.get("/v1/graph/smurfing/detect")
    assert resp_smurf2.status_code == 200

    # Communities analytics
    resp_comm = client.get("/api/v1/graph/communities/analytics", params={"min_size": 2})
    assert resp_comm.status_code == 200
    assert isinstance(resp_comm.json(), list)

    # Clusters list
    resp_clusters = client.get("/api/v1/graph/clusters/list", params={"min_size": 2})
    assert resp_clusters.status_code == 200
    assert isinstance(resp_clusters.json(), list)


def test_cypher_execution_safety() -> None:
    """Verifies Cypher execution with read-only mutation guardrail."""
    # Valid read-only query
    resp_read = client.post(
        "/api/v1/graph/cypher/execute",
        json={"query": "MATCH (n:Entity) RETURN n.id as id", "read_only": True},
    )
    assert resp_read.status_code == 200
    data = resp_read.json()
    assert data["success"] is True

    # Mutating query rejected with 400 Bad Request
    resp_mutate = client.post(
        "/api/v1/graph/cypher/execute",
        json={"query": "CREATE (n:Entity {id: 'malicious'})", "read_only": True},
    )
    assert resp_mutate.status_code == 400
    detail_lower = resp_mutate.json()["detail"].lower()
    assert "mutating" in detail_lower or "create" in detail_lower


def test_embeddings_and_similarity_endpoints() -> None:
    """Verifies GraphSAGE embedding training, inductive inference, and statistics."""
    # GNN train endpoint
    resp_train = client.post(
        "/api/v1/graph/embeddings/train",
        json={"bank_id": "bank_a", "epochs": 1, "learning_rate": 0.01},
    )
    assert resp_train.status_code == 200
    train_data = resp_train.json()
    assert train_data["bank_id"] == "bank_a"
    assert train_data["embedding_dim"] == 64

    # GNN embedding stats
    resp_stats = client.get("/api/v1/graph/embeddings/stats")
    assert resp_stats.status_code == 200
    assert resp_stats.json()["embedding_dim"] == 64

    # Inductive inference for existing entity
    resp_infer = client.post(
        "/api/v1/graph/embeddings/infer",
        json={"entity_id": "cust_graph_test_1", "dp_noise": True},
    )
    assert resp_infer.status_code == 200
    infer_data = resp_infer.json()
    assert infer_data["entity_id"] == "cust_graph_test_1"
    assert len(infer_data["embedding"]) == 64

    # Similarity search
    resp_sim = client.post(
        "/api/v1/graph/embeddings/similar",
        json={"entity_id": "cust_graph_test_1", "top_k": 5, "threshold": 0.1},
    )
    assert resp_sim.status_code == 200
    sim_data = resp_sim.json()
    assert sim_data["query_entity_id"] == "cust_graph_test_1"
    assert "similar_entities" in sim_data

    # Embedding lookup for non-existent entity -> 404 Not Found
    resp_missing = client.get("/api/v1/graph/embeddings/unseen_entity_9999")
    assert resp_missing.status_code == 404


def test_streaming_and_benchmark_telemetry() -> None:
    """Verifies Flink streaming status, edge processing, and Elliptic benchmark endpoints."""
    # Flink stream status
    resp_status = client.get("/api/v1/graph/stream/status")
    assert resp_status.status_code == 200
    assert "status" in resp_status.json()

    # Stream edge transaction
    resp_edge = client.post(
        "/api/v1/graph/stream/edge",
        json={
            "edge_id": "stream_edge_001",
            "source_id": "cust_graph_test_1",
            "target_id": "cust_graph_test_2",
            "amount": 2500.0,
            "bank_id": "bank_a",
        },
    )
    assert resp_edge.status_code == 200
    assert resp_edge.json()["processed_count"] >= 1

    # Elliptic latest cached benchmark
    resp_benchmark = client.get("/api/v1/graph/benchmark/elliptic/latest")
    assert resp_benchmark.status_code == 200
    bench_data = resp_benchmark.json()
    assert "samples_evaluated" in bench_data
    assert "auc_roc" in bench_data
