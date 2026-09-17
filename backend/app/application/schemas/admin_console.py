"""Admin Web Console Application Schemas.

Strict validation models for commercial multi-role management dashboards,
system configurations, tenant partitions, and background maintenance health.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DashboardSummaryResponse(BaseModel):
    """Schema for platform summary performance metrics."""

    model_config = ConfigDict(extra="forbid")

    active_bank_nodes_count: int = Field(..., ge=0, description="Active participating bank nodes")
    federated_rounds_completed: int = Field(..., ge=0, description="Total completed FL training rounds")
    global_model_auc: float = Field(..., ge=0.0, le=1.0, description="Area under the ROC curve for current production model")
    total_cases_opened: int = Field(..., ge=0, description="Total fraud investigation cases registered")
    sla_compliance_pct: float = Field(..., ge=0.0, le=100.0, description="Consortium SLA adherence percentage")


class RoleConfigResponse(BaseModel):
    """Schema for role-based view configuration tailored per persona."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(..., description="Enterprise user role (EXECUTIVE, COMPLIANCE_OFFICER, ML_ENGINEER, FRAUD_INVESTIGATOR)")
    visible_widgets: list[str] = Field(..., description="Authorized dashboard widget identifiers")
    permissions: list[str] = Field(..., description="Active granular ABAC permissions for role")
    theme: str = Field(..., description="UI visual aesthetic profile")


class AdminConfigResponse(BaseModel):
    """Schema representing global platform configuration parameters."""

    model_config = ConfigDict(extra="forbid")

    platform_title: str = Field(..., description="Platform instance brand and title")
    environment: str = Field(..., description="Deployment environment (production, staging, sandbox)")
    enclave_mode: str = Field(..., description="Hardware isolation attestation (Intel SGX, AWS Nitro, Software Emulated)")
    default_dp_epsilon: float = Field(..., ge=0.1, le=10.0, description="Default consortium differential privacy epsilon")
    default_dp_delta: float = Field(..., ge=1e-9, le=1e-3, description="Default differential privacy delta")
    secagg_protocol: str = Field(..., description="Cryptographic secure aggregation mechanism")
    max_active_tenants: int = Field(..., ge=1, description="Licensed consortium participant quota")
    api_version: str = Field("0.2.0", description="FastAPI core platform version")


class TenantPartitionItem(BaseModel):
    """Details for a single multi-tenant bank partition."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(..., description="Unique bank node tenant identifier")
    legal_name: str = Field(..., description="Registered financial institution name")
    status: str = Field(..., description="Tenant partition status (ACTIVE, SUSPENDED, PENDING_VERIFICATION)")
    schema_name: str = Field(..., description="PostgreSQL isolated schema identifier")
    jurisdiction: str = Field(..., description="ISO 3166-1 alpha-2 regulatory jurisdiction")
    kms_vault_path: str = Field(..., description="HashiCorp Vault envelope key transit path")


class TenantPartitionResponse(BaseModel):
    """Response cataloging all provisioned multi-tenant partitions."""

    model_config = ConfigDict(extra="forbid")

    total_tenants: int = Field(..., ge=0, description="Total tenant partitions provisioned")
    active_tenants: int = Field(..., ge=0, description="Count of active operational tenant nodes")
    partitions: list[TenantPartitionItem] = Field(..., description="List of individual partition details")


class AdminMaintenanceResponse(BaseModel):
    """Response detailing platform maintenance subsystems and storage health."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field("OPERATIONAL", description="Consortium platform operating state")
    database_pool_status: str = Field("HEALTHY", description="Async SQLAlchemy connection pool status")
    redis_cache_status: str = Field("CONNECTED", description="Redis LSH and rate-limiting cache state")
    vault_transit_status: str = Field("SEALED_OK", description="KMS encryption engine status")
    background_worker_count: int = Field(..., ge=0, description="Active daemon workers")
    disk_free_mb: float = Field(..., ge=0.0, description="Host storage capacity available in MB")
    last_vacuum_iso: str | None = Field(default=None, description="ISO timestamp of last maintenance cleanup run")
