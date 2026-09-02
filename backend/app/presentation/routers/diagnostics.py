"""Enterprise Connector Diagnostics & Infrastructure Health API Router."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.application.services.connector_diagnostics_service import (
    ConnectorDiagnosticsService,
    ConnectorHealthSummary,
    ConnectorTestProbeResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])

_diagnostics_service = ConnectorDiagnosticsService()


class ConnectorProbeRequest(BaseModel):
    """Payload to trigger an active probe test on an enterprise connector."""

    connector_id: str = Field(..., description="Connector identifier (kafka, vault, kms, splunk, redis, database, iso20022)")


class DiagnosticsOverviewResponse(BaseModel):
    """Response schema containing health metrics for all enterprise connectors."""

    total_connectors: int
    healthy_connectors: int
    avg_latency_ms: float
    connectors: list[ConnectorHealthSummary]


@router.get("/connectors", response_model=DiagnosticsOverviewResponse, status_code=status.HTTP_200_OK)
def get_connectors_overview() -> dict[str, Any]:
    """Retrieve comprehensive connectivity status for all enterprise infrastructure connectors."""
    summaries = _diagnostics_service.get_all_connector_statuses()
    healthy_count = sum(1 for c in summaries if c.status == "HEALTHY")
    avg_lat = round(sum(c.latency_ms for c in summaries) / len(summaries), 2) if summaries else 0.0

    return {
        "total_connectors": len(summaries),
        "healthy_connectors": healthy_count,
        "avg_latency_ms": avg_lat,
        "connectors": [c.model_dump() for c in summaries],
    }


@router.post("/test-connector", response_model=ConnectorTestProbeResult, status_code=status.HTTP_200_OK)
def test_connector_connection(req: ConnectorProbeRequest) -> ConnectorTestProbeResult:
    """Execute an on-demand active connectivity test probe against the target enterprise adapter."""
    if not req.connector_id or not req.connector_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="connector_id cannot be empty",
        )

    try:
        return _diagnostics_service.test_connector(req.connector_id)
    except Exception as exc:
        logger.exception("Connector probe failed for %s", req.connector_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to probe connector {req.connector_id}: {exc}",
        ) from exc
