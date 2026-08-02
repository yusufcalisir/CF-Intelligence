"""Health check endpoints.

Used by Docker health checks, load balancers, and Kubernetes readiness probes.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.infrastructure.cache import check_redis_health
from app.infrastructure.database import engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


async def check_db_health() -> bool:
    """Check database connectivity by executing a lightweight query."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error("Database health check failed: %s", e)
        return False


@router.get("/health", status_code=status.HTTP_200_OK)
async def health() -> dict:
    """Basic liveness probe. Returns 200 if the process is running."""
    return {"status": "healthy", "service": "fraud-intelligence-api"}


@router.get("/health/ready")
async def readiness() -> JSONResponse:
    """Readiness probe. Checks downstream dependencies.

    Returns HTTP 200 when all dependencies are healthy.
    Returns HTTP 503 (Service Unavailable) when any dependency is degraded,
    as required by the Kubernetes readiness probe contract.
    """
    checks: dict[str, bool] = {}

    # Redis health check
    checks["redis"] = await check_redis_health()

    # PostgreSQL health check
    checks["database"] = await check_db_health()

    # Determine overall status
    all_healthy = all(checks.values())

    if not all_healthy:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "checks": checks},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ready", "checks": checks},
    )
