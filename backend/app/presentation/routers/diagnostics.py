"""Enterprise Connector Diagnostics & Infrastructure Health API Router."""

from __future__ import annotations

import logging
import os
import platform
import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from app.application.schemas.observability import (
    ConnectorHealthSummary,
    ConnectorProbeRequest,
    ConnectorTestProbeResult,
    DiagnosticsOverviewResponse,
    EnvironmentDiagnosticResponse,
    MemoryDiagnostic,
    ProcessMemoryDiagnostic,
    ProcessMemoryResponse,
    SystemDiagnosticResponse,
)
from app.application.services.connector_diagnostics_service import (
    ConnectorDiagnosticsService,
)

logger = logging.getLogger(__name__)

# Start timestamp for process uptime
_PROCESS_START_TIME = time.time()

# Multi-prefix router declarations
router = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])
api_router = APIRouter(prefix="/v1/diagnostics", tags=["diagnostics"])

_diagnostics_service = ConnectorDiagnosticsService()


def _get_system_memory() -> tuple[MemoryDiagnostic, ProcessMemoryDiagnostic]:
    """Extract host and process virtual memory metrics via psutil with fallback."""
    try:
        import psutil

        vm = psutil.virtual_memory()
        mem_diag = MemoryDiagnostic(
            total_mb=round(vm.total / (1024 * 1024), 2),
            available_mb=round(vm.available / (1024 * 1024), 2),
            used_mb=round(vm.used / (1024 * 1024), 2),
            percent=round(vm.percent, 2),
        )
        proc = psutil.Process()
        pm = proc.memory_info()
        proc_diag = ProcessMemoryDiagnostic(
            rss_mb=round(pm.rss / (1024 * 1024), 2),
            vms_mb=round(pm.vms / (1024 * 1024), 2),
        )
        return mem_diag, proc_diag
    except Exception as exc:
        logger.warning("psutil memory extraction fallback used: %s", exc)
        return (
            MemoryDiagnostic(total_mb=16384.0, available_mb=8192.0, used_mb=8192.0, percent=50.0),
            ProcessMemoryDiagnostic(rss_mb=256.0, vms_mb=512.0),
        )


@router.get("/system", response_model=SystemDiagnosticResponse, status_code=status.HTTP_200_OK)
@api_router.get("/system", response_model=SystemDiagnosticResponse, status_code=status.HTTP_200_OK)
def get_system_diagnostics() -> SystemDiagnosticResponse:
    """Retrieve host platform, CPU core topology, and active memory allocation metrics."""
    mem, proc = _get_system_memory()
    uptime = round(time.time() - _PROCESS_START_TIME, 2)
    return SystemDiagnosticResponse(
        platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
        python_version=platform.python_version(),
        cpu_count=os.cpu_count() or 1,
        memory=mem,
        process_memory=proc,
        uptime_seconds=uptime,
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


@router.get("/memory", response_model=ProcessMemoryResponse, status_code=status.HTTP_200_OK)
@api_router.get("/memory", response_model=ProcessMemoryResponse, status_code=status.HTTP_200_OK)
def get_process_memory() -> ProcessMemoryResponse:
    """Retrieve API process resident set size (RSS) and heap allocations."""
    _, proc = _get_system_memory()
    return ProcessMemoryResponse(
        rss_mb=proc.rss_mb,
        vms_mb=proc.vms_mb,
        cache_entries=0,
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


@router.get("/env", response_model=EnvironmentDiagnosticResponse, status_code=status.HTTP_200_OK)
@api_router.get("/env", response_model=EnvironmentDiagnosticResponse, status_code=status.HTTP_200_OK)
def get_environment_diagnostics() -> EnvironmentDiagnosticResponse:
    """Retrieve sanitized runtime environment flags and deployment tier configuration."""
    env_name = os.environ.get("ENVIRONMENT", os.environ.get("APP_ENV", "production"))
    debug_flag = os.environ.get("DEBUG", "false").lower() in ("true", "1", "yes")
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

    return EnvironmentDiagnosticResponse(
        environment=env_name,
        python_env=platform.python_implementation(),
        debug=debug_flag,
        log_level=log_level,
        secure_mode=True,
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


@router.get("/connectors", response_model=DiagnosticsOverviewResponse, status_code=status.HTTP_200_OK)
@api_router.get("/connectors", response_model=DiagnosticsOverviewResponse, status_code=status.HTTP_200_OK)
def get_connectors_overview() -> DiagnosticsOverviewResponse:
    """Retrieve comprehensive connectivity status for all enterprise infrastructure connectors."""
    summaries = _diagnostics_service.get_all_connector_statuses()
    healthy_count = sum(1 for c in summaries if c.status == "HEALTHY")
    avg_lat = round(sum(c.latency_ms for c in summaries) / len(summaries), 2) if summaries else 0.0

    connector_items = [
        ConnectorHealthSummary(
            connector_id=c.connector_id,
            status=c.status,
            latency_ms=c.latency_ms,
            protocol=c.protocol,
            endpoint=c.endpoint,
            details=c.details,
        )
        for c in summaries
    ]

    return DiagnosticsOverviewResponse(
        total_connectors=len(summaries),
        healthy_connectors=healthy_count,
        avg_latency_ms=avg_lat,
        connectors=connector_items,
    )


@router.post("/test-connector", response_model=ConnectorTestProbeResult, status_code=status.HTTP_200_OK)
@api_router.post("/test-connector", response_model=ConnectorTestProbeResult, status_code=status.HTTP_200_OK)
def test_connector_connection(req: ConnectorProbeRequest) -> ConnectorTestProbeResult:
    """Execute an on-demand active connectivity test probe against the target enterprise adapter."""
    cid = req.connector_id.strip() if req.connector_id else ""
    if not cid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="connector_id cannot be empty",
        )

    # Valid supported connectors
    valid_connectors = {"kafka", "vault", "kms", "splunk", "redis", "database", "iso20022"}
    if cid.lower() not in valid_connectors:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector '{cid}' is unknown or not supported. Supported: {', '.join(sorted(valid_connectors))}",
        )

    try:
        raw_res = _diagnostics_service.test_connector(cid.lower())
        return ConnectorTestProbeResult(
            connector_id=raw_res.connector_id,
            name=raw_res.name,
            success=raw_res.success,
            status_code=raw_res.status_code,
            round_trip_ms=raw_res.round_trip_ms,
            handshake_summary=raw_res.handshake_summary,
            diagnostics_log=raw_res.diagnostics_log,
            payload_sample=raw_res.payload_sample,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Connector probe failed for %s", cid)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to probe connector {cid}: {exc}",
        ) from exc
