"""Enterprise API Gateway & Ingress Routing Schemas.

Pydantic v2 schemas for gateway status diagnostics, health checks,
real-time metrics, downstream microservice routing, and rate-limiting telemetry.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GatewayServiceRoute(BaseModel):
    """Configuration and routing metadata for a downstream microservice."""

    model_config = ConfigDict(extra="ignore")

    service_name: str = Field(..., description="Target microservice identifier")
    http_url: str = Field(..., description="Target HTTP service endpoint URL")
    ws_url: str = Field(..., description="Target WebSocket service endpoint URL")
    healthy: bool = Field(True, description="Service health probe status")
    latency_ms: float = Field(0.0, ge=0.0, description="Observed round-trip ping latency in milliseconds")


class GatewayRateLimitConfig(BaseModel):
    """Perimeter rate-limiting policy and active threshold configuration."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(True, description="Whether rate limiting is actively enforced")
    limit_per_minute: int = Field(..., gt=0, description="Maximum permitted requests per client bucket per minute")
    tracked_clients: int = Field(0, ge=0, description="Number of currently tracked client buckets")
    storage_backend: str = Field("hybrid", description="Storage provider (redis, in_memory, or hybrid)")


class GatewayStatusResponse(BaseModel):
    """Detailed diagnostics and status report for the perimeter API Gateway."""

    model_config = ConfigDict(extra="ignore")

    status: str = Field("ok", description="Overall gateway status ('ok', 'degraded')")
    service: str = Field("gateway", description="Service identifier")
    version: str = Field("0.2.0", description="Gateway API engine version")
    environment: str = Field("production", description="Active runtime environment")
    uptime_seconds: float = Field(..., ge=0.0, description="Uptime duration in seconds")
    mode: str = Field("monolith", description="Operating deployment topology ('gateway', 'monolith')")
    downstream_services: dict[str, GatewayServiceRoute] = Field(
        default_factory=dict,
        description="Catalog of mapped downstream services",
    )
    path_mappings_count: int = Field(..., ge=0, description="Total active URL path route mappings")
    rate_limit: GatewayRateLimitConfig = Field(..., description="Rate limiting configuration and state")
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp of status evaluation")


class GatewayHealthResponse(BaseModel):
    """Liveness and readiness probe response for the gateway and upstream dependencies."""

    model_config = ConfigDict(extra="ignore")

    status: str = Field("ok", description="Gateway health status")
    healthy: bool = Field(True, description="Boolean flag for container orchestration probes")
    service: str = Field("gateway", description="Service component name")
    services_ready: dict[str, bool] = Field(
        default_factory=dict,
        description="Downstream microservice connectivity readiness",
    )
    timestamp: str = Field(..., description="ISO 8601 UTC probe evaluation timestamp")


class GatewayMetricsResponse(BaseModel):
    """Real-time ingress metrics, request distribution, and security events."""

    model_config = ConfigDict(extra="ignore")

    service: str = Field("gateway", description="Service identifier")
    requests_total: int = Field(0, ge=0, description="Cumulative HTTP requests processed")
    requests_by_method: dict[str, int] = Field(
        default_factory=dict,
        description="HTTP request distribution by method (GET, POST, etc.)",
    )
    rate_limited_total: int = Field(0, ge=0, description="Total requests throttled with HTTP 429")
    auth_failures_total: int = Field(0, ge=0, description="Total requests rejected with HTTP 401")
    abac_denials_total: int = Field(0, ge=0, description="Total requests denied by ABAC with HTTP 403")
    downstream_errors_total: int = Field(0, ge=0, description="Total upstream 502/504 connection errors")
    avg_latency_ms: float = Field(0.0, ge=0.0, description="Rolling average proxy processing latency in ms")
    uptime_seconds: float = Field(0.0, ge=0.0, description="Gateway service uptime in seconds")


class GatewayProxyErrorResponse(BaseModel):
    """RFC 7807 compliant problem details payload for gateway error responses."""

    model_config = ConfigDict(extra="ignore")

    type: str = Field(..., description="URI reference identifying the problem type")
    title: str = Field(..., description="Short, human-readable summary of the problem")
    status: int = Field(..., ge=400, le=599, description="HTTP status code")
    detail: str = Field(..., description="Detailed explanation of the specific error occurrence")
    instance: str | None = Field(None, description="URI reference of the affected path")
    client_ip: str | None = Field(None, description="Sanitized client IP associated with the event")
    timestamp: str | None = Field(None, description="ISO 8601 UTC incident timestamp")
