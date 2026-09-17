"""Pydantic v2 schemas for Admin Console and Multi-Tenant Provisioning."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.web_console import ConsoleUserRole


class DashboardSummaryResponse(BaseModel):
    """Unified high-level platform performance metrics for web console."""

    active_bank_nodes_count: int
    federated_rounds_completed: int
    global_model_auc: float
    total_cases_opened: int
    sla_compliance_pct: float


class RoleConfigResponse(BaseModel):
    """Schema for role-based view configuration."""

    role: ConsoleUserRole
    visible_widgets: list[str]
    permissions: list[str]
    theme: str


class AdminConfigResponse(BaseModel):
    """System configuration and operational feature flags."""

    environment: str
    app_version: str
    multi_tenant_enabled: bool
    hsm_vault_status: str
    rate_limit_rpm: int
    max_federated_clients: int
    cors_allowed_origins: list[str]
    active_features: dict[str, bool]


class TenantItem(BaseModel):
    """Registered bank institution tenant detail."""

    bank_id: str
    legal_name: str
    jurisdiction: str
    status: str
    schema_provisioned: bool
    created_at: str


class TenantListResponse(BaseModel):
    """List of all multi-tenant bank institutions."""

    total_tenants: int
    tenants: list[TenantItem]


class MaintenanceWindowResponse(BaseModel):
    """System maintenance windows and health telemetry."""

    status: str
    active_window: bool
    next_scheduled_maintenance: str
    last_vacuum_at: str
    last_key_rotation_at: str
    storage_healthy: bool
    details: dict[str, Any] = Field(default_factory=dict)
