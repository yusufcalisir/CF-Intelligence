"""Pydantic v2 schemas for Maintenance CronJob execution and scheduling."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CronCleanupResponse(BaseModel):
    """Structured response output for session & artifact maintenance cleanup."""

    status: str = "SUCCESS"
    expired_sessions_purged: int = Field(
        default=0, description="Count of expired user/investigator sessions purged"
    )
    temporary_artifacts_removed: int = Field(
        default=0, description="Count of stale temporary files removed"
    )
    gdpr_ttl_erasure_records: int = Field(
        default=0, description="Count of GDPR TTL erasure records processed"
    )
    timestamp_iso: str = Field(description="ISO timestamp of cron execution completion")


class CronHealthStatusResponse(BaseModel):
    """Structured system health status summary for scheduled monitoring."""

    status: str = "HEALTHY"
    database_pool_active: bool = True
    active_bank_nodes_ping: int = 3
    disk_storage_available_mb: float = 10240.0
    sla_compliance_pct: float = 99.95
    timestamp_iso: str = Field(description="ISO timestamp of health check")


class CronKeyRotationRequest(BaseModel):
    """Configuration options for scheduled cryptographic key rotation."""

    model_config = ConfigDict(extra="forbid")

    tenant_ids: list[str] | None = Field(
        default=None, description="List of tenant IDs to rotate (defaults to all active tenants)"
    )
    auto_reencrypt: bool = Field(
        default=True, description="Whether to re-encrypt data under newly active key versions"
    )
    revoke_retired: bool = Field(
        default=True, description="Whether to invalidate retired key versions past grace period"
    )


class CronKeyRotationResponse(BaseModel):
    """Structured response output for scheduled cryptographic key rotation."""

    status: str = "SUCCESS"
    tenants_rotated: list[str] = Field(
        default_factory=list, description="List of tenant IDs whose keys were rotated"
    )
    re_encrypted_records: int = Field(
        default=0, description="Count of records re-wrapped to new key version"
    )
    revoked_old_keys: int = Field(
        default=0, description="Count of obsolete retired key versions revoked"
    )
    timestamp_iso: str = Field(description="ISO timestamp of key rotation execution")


class CronScheduleItem(BaseModel):
    """Cataloged cron schedule task definition."""

    job_id: str
    schedule_cron: str
    description: str
    last_run: str | None
    next_run: str


class CronScheduleResponse(BaseModel):
    """Schedule definition table for cron maintenance workers."""

    scheduled_jobs: list[CronScheduleItem]
