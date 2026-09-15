"""Automated Tenant Provisioner & Schema Migration Worker.

Canonical implementation lives in app.infrastructure.database.tenant_provisioner.
This module re-exports the canonical TenantProvisioner, sanitize_bank_id,
and global_tenant_registry for backwards compatibility and zero-drift maintenance.
"""

from __future__ import annotations

from app.infrastructure.database.tenant_provisioner import (
    TenantProvisioner,
    global_tenant_registry,
    sanitize_bank_id,
)

__all__ = ["TenantProvisioner", "global_tenant_registry", "sanitize_bank_id"]
