"""Maintenance & Health CronJob Application Schemas.

Strict validation models for scheduled session purging, temporary artifact cleanup,
storage health checks, cryptographic key rotation, and maintenance task schedules.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CronCleanupResponse(BaseModel):
    """Structured response output for session & artifact maintenance cleanup."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field("SUCCESS", description="Execution result status")
    expired_sessions_purged: int = Field(
        default=0, ge=0, description="Count of expired user/investigator sessions purged"
    )
    temporary_artifacts_removed: int = Field(
        default=0, ge=0, description="Count of stale temporary files removed"
    )
    gdpr_ttl_erasure_records: int = Field(
        default=0, ge=0, description="Count of GDPR TTL erasure records processed"
    )
    timestamp_iso: str = Field(..., description="ISO timestamp of cron execution completion")


class CronHealthStatusResponse(BaseModel):
    """Structured system health status summary for scheduled monitoring."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field("HEALTHY", description="Overall health state")
    database_pool_active: bool = Field(True, description="PostgreSQL async engine connectivity")
    active_bank_nodes_ping: int = Field(3, ge=0, description="Count of live responding bank edge daemons")
    disk_storage_available_mb: float = Field(..., ge=0.0, description="Free disk storage in megabytes")
    sla_compliance_pct: float = Field(..., ge=0.0, le=100.0, description="Current consortium SLA uptime percentage")
    timestamp_iso: str = Field(..., description="ISO timestamp of health check")


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

    model_config = ConfigDict(extra="forbid")

    status: str = Field("SUCCESS", description="Execution result status")
    tenants_rotated: list[str] = Field(
        default_factory=list, description="List of tenant IDs whose keys were rotated"
    )
    re_encrypted_records: int = Field(
        default=0, ge=0, description="Count of records re-wrapped to new key version"
    )
    revoked_old_keys: int = Field(
        default=0, ge=0, description="Count of obsolete retired key versions revoked"
    )
    timestamp_iso: str = Field(..., description="ISO timestamp of key rotation execution")


class CronTaskScheduleItem(BaseModel):
    """Individual recurring scheduled maintenance job configuration."""

    model_config = ConfigDict(extra="forbid")

    job_name: str = Field(..., description="Unique cron job identifier")
    cron_expression: str = Field(..., description="Standard 5-field cron schedule expression")
    description: str = Field(..., description="Human-readable purpose of scheduled job")
    estimated_duration_sec: int = Field(..., ge=1, description="Estimated execution time in seconds")
    is_active: bool = Field(True, description="Whether the job is currently enabled")


class CronScheduleResponse(BaseModel):
    """Response detailing registered scheduled maintenance jobs."""

    model_config = ConfigDict(extra="forbid")

    schedule_version: str = Field("2026.1", description="Maintenance schedule definition version")
    timezone: str = Field("UTC", description="Operating cron timezone")
    tasks: list[CronTaskScheduleItem] = Field(..., description="List of registered cron tasks")
