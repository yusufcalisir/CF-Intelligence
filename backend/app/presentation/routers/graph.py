"""Graph API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.application.schemas.phase2 import (
    CommunityAnalyticsResponse,
    CypherQueryRequest,
    CypherQueryResponse,
    GNNInferEmbeddingRequest,
    GNNInferEmbeddingResponse,
    GraphResponse,
    GraphStatsResponse,
    MuleRingDetectionResponse,
    MuleRingItem,
    RiskPropagationRequest,
    RiskPropagationResponse,
    SmurfingDetectionResponse,
    SmurfingPatternItem,
    TemporalAnomalyResponse,
)
from app.application.services.graph_analytics_service import GraphAnalyticsService
from app.application.services.graph_embedding_service import GraphEmbeddingService
from app.application.services.graph_engine import GraphEngine
from app.domain.enums import EntityType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/graph", tags=["graph"])


class StreamGraphEdgeRequest(BaseModel):
    edge_id: str = Field(..., description="Unique graph edge transaction ID")
    source_id: str = Field(..., description="Source entity ID")
    target_id: str = Field(..., description="Target entity ID")
    rel_type: str = Field("TRANSACTS_WITH", description="Relationship type")
    amount: float = Field(100.0, description="Transaction amount")


_graph_engine = GraphEngine()


def get_graph_engine() -> GraphEngine:
    return _graph_engine


@router.get("/clusters/list")
async def get_clusters(min_size: int = Query(3, ge=2, le=20)) -> list[dict]:
    """Get suspicious entity clusters."""
    clusters = _graph_engine.detect_clusters(min_size=min_size)
    return [
        {
            "cluster_id": i,
            "entity_ids": cluster,
            "size": len(cluster),
        }
        for i, cluster in enumerate(clusters)
    ]


@router.get("/search/nodes")
async def search_nodes(
    q: str = Query(..., min_length=1),
    entity_type: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> list[dict]:
    """Search entities in the graph."""
    et = EntityType(entity_type) if entity_type else None
    entities = _graph_engine.search_nodes(q, entity_type=et, limit=limit)
    return [
        {
            "id": e.id,
            "display_label": e.display_label,
            "entity_type": e.entity_type.value,
            "bank_id": e.bank_id,
            "risk_level": e.risk_level.value,
            "alert_count": e.alert_count,
        }
        for e in entities
    ]


@router.get("/stats/summary", response_model=GraphStatsResponse)
async def graph_stats() -> GraphStatsResponse:
    """Get graph statistics."""
    stats = _graph_engine.get_stats()
    return GraphStatsResponse(**stats)


_graph_analytics = GraphAnalyticsService(_graph_engine)


@router.post("/propagate-risk", response_model=RiskPropagationResponse)
async def propagate_risk(req: RiskPropagationRequest) -> RiskPropagationResponse:
    """Propagate risk scores along relationships using decay."""
    res = _graph_analytics.propagate_risk(decay_factor=req.decay_factor)
    return RiskPropagationResponse(**res)


@router.get("/communities/analytics", response_model=list[CommunityAnalyticsResponse])
async def get_communities_analytics(
    min_size: int = Query(3, ge=2, le=20),
) -> list[CommunityAnalyticsResponse]:
    """Get detailed community metrics sorted by fraud/risk density."""
    res = _graph_analytics.get_community_analytics(min_size=min_size)
    return [CommunityAnalyticsResponse(**c) for c in res]


@router.get("/temporal-anomalies", response_model=list[TemporalAnomalyResponse])
async def get_temporal_anomalies(
    window_minutes: int = Query(5, ge=1, le=60),
    min_edges: int = Query(3, ge=2, le=50),
) -> list[TemporalAnomalyResponse]:
    """Get temporal edge velocity anomaly subgraphs."""
    res = _graph_analytics.get_temporal_anomalies(
        window_minutes=window_minutes, min_edges=min_edges
    )
    return [TemporalAnomalyResponse(**a) for a in res]


@router.get("/{entity_id}", response_model=GraphResponse)
async def get_subgraph(
    entity_id: str,
    depth: int = Query(2, ge=1, le=4),
    max_nodes: int = Query(100, ge=5, le=200),
) -> GraphResponse:
    """Get subgraph centered on an entity with bounded k-hop ego-network."""
    subgraph = _graph_engine.get_subgraph(entity_id, radius=depth, max_nodes=max_nodes)
    if not subgraph.nodes:
        raise HTTPException(status_code=404, detail="Entity not found in graph")

    return GraphResponse(
        nodes=subgraph.nodes,
        edges=subgraph.edges,
        clusters=subgraph.clusters,
        center_entity_id=subgraph.center_entity_id,
        depth=subgraph.depth,
    )


# ── Federated Graph Embedding (FedGNN) Endpoints ────────

_graph_embedding_service = GraphEmbeddingService(graph_engine=_graph_engine)


class GNNTrainRequest(BaseModel):
    """Request body for GNN training."""

    bank_id: str = Field(default="bank_a", description="Bank to train on")
    epochs: int = Field(default=5, ge=1, le=50)
    learning_rate: float = Field(default=0.01, gt=0.0, le=1.0)


class GNNTrainResponse(BaseModel):
    """Response from GNN training."""

    bank_id: str
    loss: float
    num_nodes: int
    num_edges: int
    fraud_nodes: int
    embedding_dim: int
    model_parameters: int


class GNNSimilarityRequest(BaseModel):
    """Request body for embedding similarity search."""

    entity_id: str
    top_k: int = Field(default=10, ge=1, le=100)
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class GNNEmbeddingClusterRequest(BaseModel):
    """Request body for embedding-based fraud clustering."""

    similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    min_cluster_size: int = Field(default=3, ge=2, le=50)


@router.post("/embeddings/train")
async def train_graph_embeddings(req: GNNTrainRequest) -> GNNTrainResponse:
    """Train GraphSAGE model locally on a bank's entity graph.

    Trains a 2-layer GraphSAGE model on the entity-relationship graph,
    producing 64-dimensional node embeddings that capture structural
    fraud patterns. Only model weights are federatable — raw graph
    structure never leaves the bank.
    """
    weights, metrics = _graph_embedding_service.train_local_gnn(
        bank_id=req.bank_id,
        epochs=req.epochs,
        learning_rate=req.learning_rate,
    )
    return GNNTrainResponse(
        bank_id=req.bank_id,
        loss=metrics["loss"],
        num_nodes=metrics["num_nodes"],
        num_edges=metrics["num_edges"],
        fraud_nodes=metrics["fraud_nodes"],
        embedding_dim=metrics["embedding_dim"],
        model_parameters=weights.num_parameters,
    )


@router.get("/embeddings/{entity_id}")
async def get_entity_embedding(entity_id: str) -> dict:
    """Get the embedding vector for a specific entity.

    Returns the 64-dimensional embedding vector learned by GraphSAGE.
    The embedding encodes the entity's structural neighborhood patterns.
    """
    embedding = _graph_embedding_service.get_embedding(entity_id)
    if embedding is None:
        raise HTTPException(
            status_code=404,
            detail=f"No embedding found for entity {entity_id}. Train the GNN first.",
        )
    return {
        "entity_id": entity_id,
        "embedding": embedding.tolist(),
        "dimension": len(embedding),
    }


@router.post("/embeddings/infer", response_model=GNNInferEmbeddingResponse)
async def infer_entity_embedding(req: GNNInferEmbeddingRequest) -> GNNInferEmbeddingResponse:
    """Inductively infer node embedding for seen or unseen entities."""
    cached = _graph_embedding_service.get_embedding(req.entity_id, allow_inductive=False)
    is_inductive = cached is None

    embedding = _graph_embedding_service.infer_node_embedding(
        entity_id=req.entity_id,
        allow_cache=not is_inductive,
        dp_noise=req.dp_noise,
    )
    if embedding is None:
        raise HTTPException(
            status_code=404,
            detail=f"Entity '{req.entity_id}' not found in graph engine for inductive embedding.",
        )
    return GNNInferEmbeddingResponse(
        entity_id=req.entity_id,
        embedding=embedding.tolist(),
        dimension=len(embedding),
        is_inductive=is_inductive,
    )


@router.post("/embeddings/similar")
async def find_similar_entities(req: GNNSimilarityRequest) -> dict:
    """Find structurally similar entities via embedding cosine similarity.

    Identifies entities with similar neighborhood patterns in the fraud
    graph, even if they are not directly connected. Useful for finding
    fraud rings using the same modus operandi across banks.
    """
    results = _graph_embedding_service.find_similar_entities(
        query_entity_id=req.entity_id,
        top_k=req.top_k,
        threshold=req.threshold,
    )
    # Budget exhaustion: service returns [] when per-entity query limit is reached
    if not results and _graph_embedding_service.is_budget_exhausted(req.entity_id):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Query budget exhausted for entity '{req.entity_id}'. "
                f"Maximum {_graph_embedding_service.max_query_budget} similarity "
                "queries per entity are permitted to prevent membership inference attacks."
            ),
        )
    return {
        "query_entity_id": req.entity_id,
        "similar_entities": results,
        "count": len(results),
    }


@router.post("/embeddings/propagate-risk")
async def embedding_risk_propagation(
    decay_factor: float = Query(0.85, ge=0.0, le=1.0),
) -> dict:
    """Propagate risk using learned embeddings instead of heuristic multipliers.

    Uses cosine similarity between GNN embeddings to weight risk transfer
    between connected entities. More accurate than hardcoded relationship
    multipliers because the weights are learned from labeled fraud data.
    """
    embeddings = _graph_embedding_service.get_all_embeddings(dp_noise=True)
    if not embeddings:
        raise HTTPException(
            status_code=400,
            detail="No embeddings available. Train the GNN first.",
        )
    result = _graph_analytics.embedding_enhanced_risk_propagation(
        embeddings=embeddings,
        decay_factor=decay_factor,
    )
    return result


@router.post("/embeddings/clusters")
async def embedding_fraud_clusters(req: GNNEmbeddingClusterRequest) -> dict:
    """Cluster entities by embedding similarity for fraud ring detection.

    Finds groups of entities with similar structural patterns, even if
    they are in different connected components of the graph. Detects
    fraud rings using the same techniques across different banks.
    """
    embeddings = _graph_embedding_service.get_all_embeddings(dp_noise=True)
    if not embeddings:
        raise HTTPException(
            status_code=400,
            detail="No embeddings available. Train the GNN first.",
        )
    clusters = _graph_analytics.find_fraud_clusters_by_embedding(
        embeddings=embeddings,
        similarity_threshold=req.similarity_threshold,
        min_cluster_size=req.min_cluster_size,
    )
    return {
        "clusters": clusters,
        "total_clusters": len(clusters),
    }


@router.post("/stream/edge")
async def stream_graph_edge(req: StreamGraphEdgeRequest) -> dict:
    """Stream a real-time graph edge event into Apache Flink processor."""
    return _graph_analytics.stream_realtime_edge_event(
        edge_id=req.edge_id,
        source_id=req.source_id,
        target_id=req.target_id,
        rel_type=req.rel_type,
        amount=req.amount,
    )


@router.get("/stream/status")
async def flink_stream_status() -> dict:
    """Get Apache Flink sub-second real-time graph streaming engine status."""
    return _graph_analytics.get_flink_streaming_status()


@router.get("/embeddings/stats")
async def get_embedding_stats() -> dict:
    """Get summary statistics about the GNN embedding space.

    Returns the number of embedded nodes, embedding dimension, model
    parameter count, and pairwise similarity distribution statistics.
    """
    return _graph_embedding_service.get_embedding_stats()


@router.get("/rings/detect", response_model=MuleRingDetectionResponse)
async def detect_mule_rings(
    min_length: int = Query(3, ge=3, le=10, description="Minimum cycle length"),
    max_length: int = Query(7, ge=3, le=10, description="Maximum cycle length"),
    bank_id: str | None = Query(None, description="Filter rings by participating bank ID"),
) -> MuleRingDetectionResponse:
    """Detect cyclic mule rings in the transaction graph."""
    rings_data = _graph_engine.detect_cyclic_mule_rings(
        min_length=min_length, max_length=max_length, bank_id=bank_id
    )
    rings = [MuleRingItem(**r) for r in rings_data]
    cross_bank_count = sum(1 for r in rings if r.is_cross_bank)
    max_score = max((r.risk_score for r in rings), default=0.0)
    return MuleRingDetectionResponse(
        total_rings=len(rings),
        cross_bank_rings=cross_bank_count,
        max_risk_score=max_score,
        rings=rings,
    )


@router.get("/smurfing/detect", response_model=SmurfingDetectionResponse)
async def detect_smurfing(
    window_hours: int = Query(24, ge=1, le=168, description="Analysis time window in hours"),
    min_fan: int = Query(3, ge=2, le=50, description="Minimum fan-in or fan-out degree"),
    max_depth: int = Query(3, ge=1, le=5, description="Maximum layering depth"),
    bank_id: str | None = Query(None, description="Filter patterns by bank ID"),
) -> SmurfingDetectionResponse:
    """Detect multi-hop financial smurfing patterns (fan-in, fan-out, layering)."""
    patterns_data = _graph_engine.detect_smurfing_patterns(
        window_hours=window_hours, min_fan=min_fan, max_depth=max_depth, bank_id=bank_id
    )
    patterns = [SmurfingPatternItem(**p) for p in patterns_data]
    fan_in_count = sum(1 for p in patterns if p.pattern_type == "fan_in")
    fan_out_count = sum(1 for p in patterns if p.pattern_type == "fan_out")
    layering_count = sum(1 for p in patterns if p.pattern_type == "multi_hop_layering")
    return SmurfingDetectionResponse(
        total_patterns=len(patterns),
        fan_in_count=fan_in_count,
        fan_out_count=fan_out_count,
        layering_count=layering_count,
        patterns=patterns,
    )


@router.post("/cypher/execute", response_model=CypherQueryResponse)
async def execute_cypher_query(req: CypherQueryRequest) -> CypherQueryResponse:
    """Safely execute a parameterized Cypher query with read_only mutation rejection."""
    import time

    start_t = time.perf_counter()
    try:
        results = _graph_engine.execute_cypher(
            query=req.query,
            params=req.query_parameters,
            read_only=req.read_only,
        )
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        return CypherQueryResponse(
            success=True,
            database_backend=_graph_engine.db_type.capitalize(),
            row_count=len(results),
            count=len(results),
            execution_time_ms=round(elapsed_ms, 2),
            results=results,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error("Error executing Cypher query: %s", e)
        raise HTTPException(status_code=500, detail=f"Cypher execution failed: {e}")

