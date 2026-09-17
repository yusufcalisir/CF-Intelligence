"""Graph API endpoints.

Exposes REST APIs for entity-relationship property graph traversal, cyclic mule ring detection,
multi-hop smurfing detection, local and inductive GraphSAGE embeddings, parameterized Cypher
execution, real-time Apache Flink edge processing, and Elliptic Bitcoin benchmarks.
Supports dual routing: /api/v1/graph and /v1/graph.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.application.schemas.graph import (
    CommunityAnalyticsResponse,
    CypherQueryRequest,
    CypherQueryResponse,
    EllipticBenchmarkRequest,
    EllipticBenchmarkResponse,
    EntityEmbeddingResponse,
    FlinkStreamStatusResponse,
    GNNEmbeddingClusterRequest,
    GNNEmbeddingClusterResponse,
    GNNEmbeddingStatsResponse,
    GNNInferEmbeddingRequest,
    GNNInferEmbeddingResponse,
    GNNSimilarityRequest,
    GNNSimilarityResponse,
    GNNTrainRequest,
    GNNTrainResponse,
    GraphClusterItem,
    GraphEdgeItem,
    GraphEdgesResponse,
    GraphResponse,
    GraphSearchNodeItem,
    GraphStatsResponse,
    MuleRingDetectionResponse,
    MuleRingItem,
    RiskPropagationRequest,
    RiskPropagationResponse,
    SmurfingDetectionResponse,
    SmurfingPatternItem,
    StreamEdgeEventRequest,
    StreamEdgeEventResponse,
    StreamingGNNTrainStepResponse,
    TemporalAnomalyResponse,
)
from app.application.services.elliptic_benchmark_service import EllipticBenchmarkService
from app.application.services.flink_graph_streaming import StreamingEdgeEvent
from app.application.services.graph_analytics_service import GraphAnalyticsService
from app.application.services.graph_embedding_service import GraphEmbeddingService
from app.application.services.graph_engine import (
    _EDGE_STYLES,
    GraphEngine,
    _dict_to_relationship,
)
from app.application.services.streaming_gnn_model import StreamingGATModel
from app.application.services.streaming_graph_service import StreamingGraphService
from app.domain.enums import EntityType, RelationshipType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/graph", tags=["graph"])
api_router = APIRouter(prefix="/v1/graph", tags=["graph"])

_graph_engine = GraphEngine()
_graph_analytics = GraphAnalyticsService(graph_engine=_graph_engine)
_graph_embedding_service = GraphEmbeddingService(graph_engine=_graph_engine)
_streaming_graph_service = StreamingGraphService()
_streaming_gnn_model = StreamingGATModel(in_dim=12)
_elliptic_benchmark_service = EllipticBenchmarkService()


def get_graph_engine() -> GraphEngine:
    """Dependency provider returning singleton GraphEngine instance."""
    return _graph_engine


# ── Topology & Search Endpoints (Static Paths) ──────────────────────────────

def _get_clusters_handler(min_size: int) -> list[GraphClusterItem]:
    clusters = _graph_engine.detect_clusters(min_size=min_size)
    return [
        GraphClusterItem(
            cluster_id=i,
            entity_ids=cluster,
            size=len(cluster),
        )
        for i, cluster in enumerate(clusters)
    ]


@router.get("/clusters/list", response_model=list[GraphClusterItem], status_code=status.HTTP_200_OK)
@api_router.get("/clusters/list", response_model=list[GraphClusterItem], status_code=status.HTTP_200_OK)
async def get_clusters(min_size: int = Query(3, ge=2, le=20)) -> list[GraphClusterItem]:
    """Get suspicious entity clusters."""
    return _get_clusters_handler(min_size)


def _search_nodes_handler(
    q: str,
    entity_type: str | None = None,
    limit: int = 20,
) -> list[GraphSearchNodeItem]:
    et = EntityType(entity_type) if entity_type else None
    entities = _graph_engine.search_nodes(q, entity_type=et, limit=limit)
    return [
        GraphSearchNodeItem(
            id=e.id,
            display_label=e.display_label,
            entity_type=e.entity_type.value,
            bank_id=e.bank_id,
            risk_level=e.risk_level.value,
            alert_count=e.alert_count,
        )
        for e in entities
    ]


@router.get("/search/nodes", response_model=list[GraphSearchNodeItem], status_code=status.HTTP_200_OK)
@api_router.get("/search/nodes", response_model=list[GraphSearchNodeItem], status_code=status.HTTP_200_OK)
async def search_nodes(
    q: str = Query(..., min_length=1, description="Entity search query"),
    entity_type: str | None = Query(None, description="Optional entity type filter"),
    limit: int = Query(20, ge=1, le=100, description="Maximum nodes to return"),
) -> list[GraphSearchNodeItem]:
    """Search entities in the graph."""
    return _search_nodes_handler(q=q, entity_type=entity_type, limit=limit)


@router.get("/nodes", response_model=list[GraphSearchNodeItem], status_code=status.HTTP_200_OK)
@api_router.get("/nodes", response_model=list[GraphSearchNodeItem], status_code=status.HTTP_200_OK)
async def get_nodes(
    q: str = Query(default="", description="Search query filter (empty returns sampled nodes)"),
    entity_type: str | None = Query(None, description="Optional entity type filter"),
    limit: int = Query(20, ge=1, le=100, description="Maximum nodes to return"),
) -> list[GraphSearchNodeItem]:
    """List or filter graph entities (API_REGISTRY.md endpoint)."""
    return _search_nodes_handler(q=q, entity_type=entity_type, limit=limit)


def _get_edges_handler(
    source_id: str | None = None,
    target_id: str | None = None,
    limit: int = 50,
) -> GraphEdgesResponse:
    raw_relationships = [_dict_to_relationship(v) for v in _graph_engine._relationships.list_values()]
    filtered = []
    for rel in raw_relationships:
        if source_id and rel.source_entity_id != source_id:
            continue
        if target_id and rel.target_entity_id != target_id:
            continue
        style = _EDGE_STYLES.get(rel.relationship_type, {})
        filtered.append(
            GraphEdgeItem(
                id=rel.id,
                source=rel.source_entity_id,
                target=rel.target_entity_id,
                label=rel.relationship_type.value.replace("_", " "),
                type="smoothstep",
                animated=rel.relationship_type == RelationshipType.LINKED_ALERT,
                confidence=rel.confidence,
                relationship_type=rel.relationship_type.value,
                style=style,
                data={"confidence": rel.confidence, "relationshipType": rel.relationship_type.value},
            )
        )
        if len(filtered) >= limit:
            break
    return GraphEdgesResponse(
        total_edges=len(raw_relationships),
        count=len(filtered),
        edges=filtered,
    )


@router.get("/edges", response_model=GraphEdgesResponse, status_code=status.HTTP_200_OK)
@api_router.get("/edges", response_model=GraphEdgesResponse, status_code=status.HTTP_200_OK)
async def get_edges(
    source_id: str | None = Query(None, description="Filter by source entity ID"),
    target_id: str | None = Query(None, description="Filter by target entity ID"),
    limit: int = Query(50, ge=1, le=500, description="Maximum edges to sample"),
) -> GraphEdgesResponse:
    """Retrieve sampled or filtered graph edges (API_REGISTRY.md endpoint)."""
    return _get_edges_handler(source_id=source_id, target_id=target_id, limit=limit)


def _graph_stats_handler() -> GraphStatsResponse:
    stats = _graph_engine.get_stats()
    return GraphStatsResponse(**stats)


@router.get("/stats/summary", response_model=GraphStatsResponse, status_code=status.HTTP_200_OK)
@api_router.get("/stats/summary", response_model=GraphStatsResponse, status_code=status.HTTP_200_OK)
@router.get("/stats", response_model=GraphStatsResponse, status_code=status.HTTP_200_OK)
@api_router.get("/stats", response_model=GraphStatsResponse, status_code=status.HTTP_200_OK)
async def graph_stats() -> GraphStatsResponse:
    """Get graph statistics."""
    return _graph_stats_handler()


@router.post("/propagate-risk", response_model=RiskPropagationResponse, status_code=status.HTTP_200_OK)
@api_router.post("/propagate-risk", response_model=RiskPropagationResponse, status_code=status.HTTP_200_OK)
async def propagate_risk(req: RiskPropagationRequest) -> RiskPropagationResponse:
    """Propagate risk scores along relationships using decay."""
    res = _graph_analytics.propagate_risk(decay_factor=req.decay_factor)
    return RiskPropagationResponse(**res)


@router.get("/communities/analytics", response_model=list[CommunityAnalyticsResponse], status_code=status.HTTP_200_OK)
@api_router.get("/communities/analytics", response_model=list[CommunityAnalyticsResponse], status_code=status.HTTP_200_OK)
async def get_communities_analytics(
    min_size: int = Query(3, ge=2, le=20),
) -> list[CommunityAnalyticsResponse]:
    """Get detailed community metrics sorted by fraud/risk density."""
    res = _graph_analytics.get_community_analytics(min_size=min_size)
    return [CommunityAnalyticsResponse(**c) for c in res]


@router.get("/temporal-anomalies", response_model=list[TemporalAnomalyResponse], status_code=status.HTTP_200_OK)
@api_router.get("/temporal-anomalies", response_model=list[TemporalAnomalyResponse], status_code=status.HTTP_200_OK)
async def get_temporal_anomalies(
    window_minutes: int = Query(5, ge=1, le=60),
    min_edges: int = Query(3, ge=2, le=50),
) -> list[TemporalAnomalyResponse]:
    """Get temporal edge velocity anomaly subgraphs."""
    res = _graph_analytics.get_temporal_anomalies(
        window_minutes=window_minutes, min_edges=min_edges
    )
    return [TemporalAnomalyResponse(**a) for a in res]


# ── Ring & Smurfing Detection Endpoints ─────────────────────────────────────

def _detect_mule_rings_handler(
    min_length: int,
    max_length: int,
    bank_id: str | None,
) -> MuleRingDetectionResponse:
    if min_length > max_length:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"min_length ({min_length}) cannot exceed max_length ({max_length}).",
        )
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


@router.get("/rings/detect", response_model=MuleRingDetectionResponse, status_code=status.HTTP_200_OK)
@api_router.get("/rings/detect", response_model=MuleRingDetectionResponse, status_code=status.HTTP_200_OK)
@router.get("/rings", response_model=MuleRingDetectionResponse, status_code=status.HTTP_200_OK)
@api_router.get("/rings", response_model=MuleRingDetectionResponse, status_code=status.HTTP_200_OK)
async def detect_mule_rings(
    min_length: int = Query(3, ge=3, le=10, description="Minimum cycle length"),
    max_length: int = Query(7, ge=3, le=10, description="Maximum cycle length"),
    bank_id: str | None = Query(None, description="Filter rings by participating bank ID"),
) -> MuleRingDetectionResponse:
    """Detect cyclic mule rings in the transaction graph (API_REGISTRY.md endpoint)."""
    return _detect_mule_rings_handler(min_length=min_length, max_length=max_length, bank_id=bank_id)


@router.get("/smurfing/detect", response_model=SmurfingDetectionResponse, status_code=status.HTTP_200_OK)
@api_router.get("/smurfing/detect", response_model=SmurfingDetectionResponse, status_code=status.HTTP_200_OK)
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
    patterns = [SmurfingPatternItem.from_raw(p) for p in patterns_data]
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


# ── Cypher Execution & Streaming ────────────────────────────────────────────

@router.post("/cypher/execute", response_model=CypherQueryResponse, status_code=status.HTTP_200_OK)
@api_router.post("/cypher/execute", response_model=CypherQueryResponse, status_code=status.HTTP_200_OK)
async def execute_cypher_query(req: CypherQueryRequest) -> CypherQueryResponse:
    """Safely execute a parameterized Cypher query with read_only mutation rejection."""
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        logger.error("Error executing Cypher query: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cypher execution failed: {e}",
        )


@router.post("/stream/edge", response_model=StreamEdgeEventResponse, status_code=status.HTTP_200_OK)
@api_router.post("/stream/edge", response_model=StreamEdgeEventResponse, status_code=status.HTTP_200_OK)
async def process_streaming_edge(req: StreamEdgeEventRequest) -> StreamEdgeEventResponse:
    """Stream a real-time graph edge transaction through Apache Flink processor and active GNN window."""
    try:
        event = StreamingEdgeEvent(
            edge_id=req.edge_id,
            source_id=req.source_id,
            target_id=req.target_id,
            rel_type=req.rel_type,
            amount=req.amount,
            bank_id=req.bank_id,
        )
        receipt = _graph_analytics.flink_processor.process_streaming_edge(event)

        _streaming_graph_service.add_transaction(
            {
                "sender_id": req.source_id,
                "receiver_id": req.target_id,
                "amount": req.amount,
                "bank_id": req.bank_id,
                "is_fraud": len(receipt.velocity_anomalies) > 0,
            }
        )

        return StreamEdgeEventResponse(
            processed_count=receipt.processed_count,
            latency_ms=receipt.latency_ms,
            window_size_ms=receipt.window_size_ms,
            velocity_anomalies=receipt.velocity_anomalies,
            high_risk_entities=receipt.high_risk_entities,
            processed_at=receipt.processed_at,
        )
    except Exception as e:
        logger.error("Error processing streaming edge: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Streaming edge processing failed: {e}",
        )


@router.get("/stream/status", response_model=FlinkStreamStatusResponse, status_code=status.HTTP_200_OK)
@api_router.get("/stream/status", response_model=FlinkStreamStatusResponse, status_code=status.HTTP_200_OK)
async def get_stream_status() -> FlinkStreamStatusResponse:
    """Retrieve Apache Flink streaming engine and SLA status telemetry."""
    stream_status = _graph_analytics.get_flink_streaming_status()
    return FlinkStreamStatusResponse(**stream_status)


@router.post("/stream/gnn/train", response_model=StreamingGNNTrainStepResponse, status_code=status.HTTP_200_OK)
@api_router.post("/stream/gnn/train", response_model=StreamingGNNTrainStepResponse, status_code=status.HTTP_200_OK)
async def trigger_streaming_gnn_train() -> StreamingGNNTrainStepResponse:
    """Trigger an online backpropagation step on the active streaming graph with time-decayed edge weights."""
    import torch

    h, edge_index, labels, edge_weights = _streaming_graph_service.get_active_subgraph_tensors(return_weights=True)
    summary = _streaming_graph_service.get_status_summary()

    if h.size(0) == 0 or edge_index.size(1) == 0 or len(labels) == 0:
        return StreamingGNNTrainStepResponse(
            loss=0.0,
            node_count=summary["node_count"],
            edge_count=summary["edge_count"],
            window_size_minutes=summary["window_size_minutes"],
            training_applied=False,
        )

    labels_tensor = torch.tensor(labels, dtype=torch.float32)
    loss = _streaming_gnn_model.online_train_step(
        h=h,
        edge_index=edge_index,
        labels=labels_tensor,
        edge_weights=edge_weights,
    )

    return StreamingGNNTrainStepResponse(
        loss=round(loss, 4),
        node_count=summary["node_count"],
        edge_count=summary["edge_count"],
        window_size_minutes=summary["window_size_minutes"],
        training_applied=True,
    )


# ── Elliptic Bitcoin Benchmark Endpoints ────────────────────────────────────

@router.post("/benchmark/elliptic", response_model=EllipticBenchmarkResponse, status_code=status.HTTP_200_OK)
@api_router.post("/benchmark/elliptic", response_model=EllipticBenchmarkResponse, status_code=status.HTTP_200_OK)
async def run_elliptic_benchmark(req: EllipticBenchmarkRequest | None = None) -> EllipticBenchmarkResponse:
    """Execute the Elliptic Bitcoin dataset graph benchmark using genuine PyTorch models."""
    params = req or EllipticBenchmarkRequest()
    try:
        results = _elliptic_benchmark_service.run_benchmark(
            n_samples=params.n_samples,
            random_seed=params.random_seed,
            epochs=params.epochs,
            learning_rate=params.learning_rate,
        )
        report_path = None
        if params.save_report:
            report_p = _elliptic_benchmark_service.save_report(results)
            report_path = str(report_p)

        return EllipticBenchmarkResponse(
            **results,
            report_saved=params.save_report,
            report_path=report_path,
        )
    except Exception as exc:
        logger.error("Elliptic benchmark failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Elliptic benchmark execution failed: {exc}",
        ) from exc


@router.get("/benchmark/elliptic/latest", response_model=EllipticBenchmarkResponse, status_code=status.HTTP_200_OK)
@api_router.get("/benchmark/elliptic/latest", response_model=EllipticBenchmarkResponse, status_code=status.HTTP_200_OK)
async def get_latest_elliptic_benchmark() -> EllipticBenchmarkResponse:
    """Retrieve the latest cached Elliptic graph benchmark results."""
    latest = _elliptic_benchmark_service.get_latest_benchmark_results()
    if latest is None:
        latest = _elliptic_benchmark_service.run_benchmark(n_samples=200, random_seed=42, epochs=3)
    return EllipticBenchmarkResponse(
        **latest,
        report_saved=True,
        report_path="verification/real_data_benchmark/README.md",
    )


# ── Federated Graph Embedding (FedGNN) Endpoints ────────────────────────────

@router.post("/embeddings/train", response_model=GNNTrainResponse, status_code=status.HTTP_200_OK)
@api_router.post("/embeddings/train", response_model=GNNTrainResponse, status_code=status.HTTP_200_OK)
async def train_graph_embeddings(req: GNNTrainRequest) -> GNNTrainResponse:
    """Train GraphSAGE model locally on a bank's entity graph."""
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


@router.get("/embeddings/stats", response_model=GNNEmbeddingStatsResponse, status_code=status.HTTP_200_OK)
@api_router.get("/embeddings/stats", response_model=GNNEmbeddingStatsResponse, status_code=status.HTTP_200_OK)
async def get_embedding_stats() -> GNNEmbeddingStatsResponse:
    """Get summary statistics about the GNN embedding space."""
    stats = _graph_embedding_service.get_embedding_stats()
    return GNNEmbeddingStatsResponse(
        total_embeddings=stats.get("num_embedded_nodes", 0),
        embedding_dim=stats.get("embedding_dim", 64),
        model_parameters=stats.get("model_parameters", 0),
        coverage_percentage=stats.get("coverage_percentage", 100.0),
    )


@router.post("/embeddings/infer", response_model=GNNInferEmbeddingResponse, status_code=status.HTTP_200_OK)
@api_router.post("/embeddings/infer", response_model=GNNInferEmbeddingResponse, status_code=status.HTTP_200_OK)
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity '{req.entity_id}' not found in graph engine for inductive embedding.",
        )
    return GNNInferEmbeddingResponse(
        entity_id=req.entity_id,
        embedding=embedding.tolist(),
        dimension=len(embedding),
        is_inductive=is_inductive,
    )


@router.post("/embeddings/similar", response_model=GNNSimilarityResponse, status_code=status.HTTP_200_OK)
@api_router.post("/embeddings/similar", response_model=GNNSimilarityResponse, status_code=status.HTTP_200_OK)
async def find_similar_entities(req: GNNSimilarityRequest) -> GNNSimilarityResponse:
    """Find structurally similar entities via embedding cosine similarity."""
    results = _graph_embedding_service.find_similar_entities(
        query_entity_id=req.entity_id,
        top_k=req.top_k,
        threshold=req.threshold,
    )
    # Budget exhaustion check
    if not results and _graph_embedding_service.is_budget_exhausted(req.entity_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Query budget exhausted for entity '{req.entity_id}'. "
                f"Maximum {_graph_embedding_service.max_query_budget} similarity "
                "queries per entity are permitted to prevent membership inference attacks."
            ),
        )
    return GNNSimilarityResponse(
        query_entity_id=req.entity_id,
        similar_entities=results,
        count=len(results),
    )


@router.post("/embeddings/propagate-risk", status_code=status.HTTP_200_OK)
@api_router.post("/embeddings/propagate-risk", status_code=status.HTTP_200_OK)
async def embedding_risk_propagation(
    decay_factor: float = Query(0.85, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """Propagate risk using learned embeddings instead of heuristic multipliers."""
    embeddings = _graph_embedding_service.get_all_embeddings(dp_noise=True)
    if not embeddings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No embeddings available. Train the GNN first.",
        )
    result = _graph_analytics.embedding_enhanced_risk_propagation(
        embeddings=embeddings,
        decay_factor=decay_factor,
    )
    return result


@router.post("/embeddings/clusters", response_model=GNNEmbeddingClusterResponse, status_code=status.HTTP_200_OK)
@api_router.post("/embeddings/clusters", response_model=GNNEmbeddingClusterResponse, status_code=status.HTTP_200_OK)
async def embedding_fraud_clusters(req: GNNEmbeddingClusterRequest) -> GNNEmbeddingClusterResponse:
    """Cluster entities by embedding similarity for fraud ring detection."""
    embeddings = _graph_embedding_service.get_all_embeddings(dp_noise=True)
    if not embeddings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No embeddings available. Train the GNN first.",
        )
    clusters = _graph_analytics.find_fraud_clusters_by_embedding(
        embeddings=embeddings,
        similarity_threshold=req.similarity_threshold,
        min_cluster_size=req.min_cluster_size,
    )
    cluster_nodes = [c["node_ids"] for c in clusters]
    return GNNEmbeddingClusterResponse(
        clusters=cluster_nodes,
        total_clusters=len(cluster_nodes),
    )


@router.get("/embeddings/{entity_id}", response_model=EntityEmbeddingResponse, status_code=status.HTTP_200_OK)
@api_router.get("/embeddings/{entity_id}", response_model=EntityEmbeddingResponse, status_code=status.HTTP_200_OK)
async def get_entity_embedding(entity_id: str) -> EntityEmbeddingResponse:
    """Get the embedding vector for a specific entity."""
    clean_id = entity_id.strip()
    if not clean_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="entity_id must not be empty.",
        )
    embedding = _graph_embedding_service.get_embedding(clean_id)
    if embedding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No embedding found for entity '{clean_id}'. Train the GNN first.",
        )
    return EntityEmbeddingResponse(
        entity_id=clean_id,
        embedding=embedding.tolist(),
        dimension=len(embedding),
    )


# ── Subgraph Traversal (Parameterized Routes Placed at the Bottom) ──────────

def _get_subgraph_handler(entity_id: str, depth: int, max_nodes: int) -> GraphResponse:
    clean_id = entity_id.strip()
    if not clean_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="entity_id must not be empty.",
        )
    subgraph = _graph_engine.get_subgraph(clean_id, radius=depth, max_nodes=max_nodes)
    if not subgraph.nodes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity '{clean_id}' not found in graph",
        )

    return GraphResponse(
        nodes=subgraph.nodes,
        edges=subgraph.edges,
        clusters=subgraph.clusters,
        center_entity_id=subgraph.center_entity_id,
        depth=subgraph.depth,
    )


@router.get("/subgraph/{entity_id}", response_model=GraphResponse, status_code=status.HTTP_200_OK)
@api_router.get("/subgraph/{entity_id}", response_model=GraphResponse, status_code=status.HTTP_200_OK)
async def get_subgraph_by_path(
    entity_id: str,
    depth: int = Query(2, ge=1, le=4, description="Ego-network radius"),
    max_nodes: int = Query(100, ge=5, le=500, description="Maximum nodes budget"),
) -> GraphResponse:
    """Traverse entity-relationship subgraph with depth parameters (API_REGISTRY.md endpoint)."""
    return _get_subgraph_handler(entity_id, depth, max_nodes)


@router.get("/{entity_id}", response_model=GraphResponse, status_code=status.HTTP_200_OK)
@api_router.get("/{entity_id}", response_model=GraphResponse, status_code=status.HTTP_200_OK)
async def get_subgraph(
    entity_id: str,
    depth: int = Query(2, ge=1, le=4, description="Ego-network radius"),
    max_nodes: int = Query(100, ge=5, le=500, description="Maximum nodes budget"),
) -> GraphResponse:
    """Get subgraph centered on an entity with bounded k-hop ego-network."""
    return _get_subgraph_handler(entity_id, depth, max_nodes)
