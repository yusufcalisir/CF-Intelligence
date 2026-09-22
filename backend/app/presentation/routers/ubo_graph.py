"""Cross-Border Corporate UBO & Heterogeneous Graph Modeling Router.

Provides REST endpoints for:
1. Registering corporate nodes (entities, holdings, offshore shells, natural persons).
2. Creating directed ownership and control relations.
3. Batch ingesting corporate network hierarchies.
4. Computing multi-tier effective Ultimate Beneficial Owners (UBOs).
5. Detecting circular ownership loops, nominee director syndicates, and offshore shell clusters.
6. Exporting ego-subgraphs for interactive React Flow visualization.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.application.schemas.ubo_schemas import (
    UBOAnomalyDetectionResponse,
    UBOCalculationResponse,
    UBOGraphIngestRequest,
    UBOGraphIngestResponse,
    UBOMetricsResponse,
    UBONodeCreate,
    UBONodeResponse,
    UBORelationCreate,
    UBORelationResponse,
    UBOSubgraphResponse,
)
from app.application.services.ubo_graph_service import (
    UBOGraphService,
    get_ubo_graph_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ubo", tags=["Corporate UBO & Knowledge Graph"])
api_router = APIRouter(prefix="/api/v1/ubo", tags=["Corporate UBO & Knowledge Graph"])


def _resolve_tenant(
    x_tenant_id: str | None = None,
    x_bank_id: str | None = None,
) -> str:
    """Extract tenant from headers, defaulting to bank_alpha."""
    tenant = x_tenant_id or x_bank_id or "bank_alpha"
    return tenant.strip().lower()


def _register_ubo_endpoints(target_router: APIRouter) -> None:
    """Bind all corporate UBO endpoints to target router."""

    @target_router.post(
        "/nodes",
        response_model=UBONodeResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Register corporate node",
        description="Creates a natural person, legal entity, holding, or offshore shell in the graph.",
    )
    async def create_node(
        node: UBONodeCreate,
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> UBONodeResponse:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: UBOGraphService = get_ubo_graph_service()
        try:
            return service.add_node(tenant, node)
        except Exception as e:
            logger.error("Failed to add corporate node: %s", e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    @target_router.get(
        "/nodes/{node_id}",
        response_model=UBONodeResponse,
        status_code=status.HTTP_200_OK,
        summary="Get corporate node details",
    )
    async def get_node(
        node_id: str,
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> UBONodeResponse:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: UBOGraphService = get_ubo_graph_service()
        res = service.get_node(tenant, node_id)
        if not res:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Node '{node_id}' not found in corporate graph.",
            )
        return res

    @target_router.post(
        "/relations",
        response_model=UBORelationResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Create ownership or control relation",
        description="Creates directed edge connecting owner to controlled entity with ownership percentage.",
    )
    async def create_relation(
        relation: UBORelationCreate,
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> UBORelationResponse:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: UBOGraphService = get_ubo_graph_service()
        try:
            return service.add_relation(tenant, relation)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    @target_router.post(
        "/batch",
        response_model=UBOGraphIngestResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Batch ingest corporate network",
        description="Bulk registration of corporate entities, directors, and ownership hierarchies.",
    )
    async def batch_ingest(
        batch: UBOGraphIngestRequest,
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> UBOGraphIngestResponse:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: UBOGraphService = get_ubo_graph_service()

        nodes_created = 0
        relations_created = 0

        for n in batch.nodes:
            service.add_node(tenant, n)
            nodes_created += 1

        for r in batch.relations:
            try:
                service.add_relation(tenant, r)
                relations_created += 1
            except ValueError as e:
                logger.warning("Skipping invalid relation during batch: %s", e)

        return UBOGraphIngestResponse(
            nodes_created=nodes_created,
            relations_created=relations_created,
            tenant_id=tenant,
            message="Batch corporate network ingested successfully.",
        )

    @target_router.get(
        "/entities/{entity_id}/beneficial-owners",
        response_model=UBOCalculationResponse,
        status_code=status.HTTP_200_OK,
        summary="Calculate effective Ultimate Beneficial Owners (UBO)",
        description="Traverses multi-tiered ownership paths to compute compounded direct and indirect ownership.",
    )
    async def calculate_ubos(
        entity_id: str,
        threshold: float = Query(
            default=25.0,
            ge=0.0,
            le=100.0,
            description="Statutory ownership threshold percentage (EU AMLD default: 25.0%).",
        ),
        max_depth: int = Query(
            default=8,
            ge=1,
            le=20,
            description="Maximum corporate ownership depth hops.",
        ),
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> UBOCalculationResponse:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: UBOGraphService = get_ubo_graph_service()
        try:
            return service.calculate_effective_ownership(
                tenant, entity_id, statutory_threshold=threshold, max_depth=max_depth
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

    @target_router.get(
        "/entities/{entity_id}/anomalies",
        response_model=UBOAnomalyDetectionResponse,
        status_code=status.HTTP_200_OK,
        summary="Audit entity for corporate structural anomalies",
        description="Detects circular ownership loops, nominee directors, and offshore shell clusters.",
    )
    async def get_entity_anomalies(
        entity_id: str,
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> UBOAnomalyDetectionResponse:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: UBOGraphService = get_ubo_graph_service()
        node = service.get_node(tenant, entity_id)
        if not node:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity '{entity_id}' not found in corporate graph.",
            )
        return service.analyze_structural_anomalies(tenant, target_entity_id=entity_id)

    @target_router.get(
        "/entities/{entity_id}/subgraph",
        response_model=UBOSubgraphResponse,
        status_code=status.HTTP_200_OK,
        summary="Get corporate ego-subgraph for visualization",
        description="Extracts directed network ego-subgraph up to max_hops for interactive visualizers.",
    )
    async def get_entity_subgraph(
        entity_id: str,
        max_hops: int = Query(default=3, ge=1, le=10),
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> UBOSubgraphResponse:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: UBOGraphService = get_ubo_graph_service()
        try:
            return service.get_visualization_subgraph(
                tenant, root_id=entity_id, max_hops=max_hops
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

    @target_router.get(
        "/anomalies/circular-ownership",
        response_model=list[list[str]],
        status_code=status.HTTP_200_OK,
        summary="Consortium-wide circular ownership scan",
        description="Scans full corporate graph for directed circular ownership cycles.",
    )
    async def get_all_circular_cycles(
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> list[list[str]]:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: UBOGraphService = get_ubo_graph_service()
        return service.detect_circular_ownership(tenant)

    @target_router.get(
        "/anomalies/nominee-directors",
        response_model=list[dict[str, Any]],
        status_code=status.HTTP_200_OK,
        summary="Consortium-wide nominee director scan",
        description="Identifies individuals serving as directors across an excessive portfolio of entities.",
    )
    async def get_all_nominee_directors(
        min_entities: int = Query(default=5, ge=2, le=50),
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> list[dict[str, Any]]:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: UBOGraphService = get_ubo_graph_service()
        return service.identify_nominee_directors(tenant, min_entities=min_entities)

    @target_router.get(
        "/anomalies/shell-clusters",
        response_model=list[dict[str, Any]],
        status_code=status.HTTP_200_OK,
        summary="Consortium-wide shell company cluster scan",
        description="Identifies offshore entities in non-cooperative tax jurisdictions.",
    )
    async def get_all_shell_clusters(
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> list[dict[str, Any]]:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: UBOGraphService = get_ubo_graph_service()
        return service.detect_shell_clusters(tenant)

    @target_router.get(
        "/metrics",
        response_model=UBOMetricsResponse,
        status_code=status.HTTP_200_OK,
        summary="Consortium UBO registry telemetry metrics",
    )
    async def get_metrics(
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> UBOMetricsResponse:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: UBOGraphService = get_ubo_graph_service()
        return service.get_metrics(tenant)


# Bind endpoints to both root and canonical routers
_register_ubo_endpoints(router)
_register_ubo_endpoints(api_router)
