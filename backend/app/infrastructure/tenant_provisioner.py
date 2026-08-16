"""Automated Tenant Provisioner & Schema Migration Worker."""

from __future__ import annotations

import logging
import os

from app.domain.tenant_management import TenantRecord, TenantRegistry, TenantStatus
from app.infrastructure.database import (
    VALID_TENANTS,
    Base,
    _get_or_create_engine,
    _tenant_initialized,
)
from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)

# Global singleton registry
global_tenant_registry = TenantRegistry()


class TenantProvisioner:
    """Automates schema creation, database table migration, and tenant lifecycle transitions."""

    def __init__(self, registry: TenantRegistry | None = None) -> None:
        self.registry = registry or global_tenant_registry

    async def provision_tenant(self, tenant_id: str, name: str) -> TenantRecord:
        """Provisions a new tenant database schema/file and initializes all tables."""
        clean_tenant_id = tenant_id.lower().strip()
        record = self.registry.register_tenant(clean_tenant_id, name)

        logger.info("Starting automated provisioning for tenant '%s'...", clean_tenant_id)

        try:
            # 1. Register tenant in VALID_TENANTS
            VALID_TENANTS.add(clean_tenant_id)

            # 2. Get/create tenant database engine
            engine = _get_or_create_engine(clean_tenant_id)

            # 3. Create all DDL tables for the tenant database
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            _tenant_initialized.add(clean_tenant_id)

            # 4. Update status to ACTIVE
            record = self.registry.set_status(clean_tenant_id, TenantStatus.ACTIVE)
            logger.info("Successfully provisioned tenant '%s' (%s)", name, clean_tenant_id)
            return record

        except Exception as exc:
            logger.error("Failed to provision tenant '%s': %s", clean_tenant_id, exc)
            self.registry.set_status(clean_tenant_id, TenantStatus.SUSPENDED)
            raise RuntimeError(
                f"Tenant provisioning failed for '{clean_tenant_id}': {exc}"
            ) from exc

    async def suspend_tenant(self, tenant_id: str) -> TenantRecord:
        """Suspends an active tenant node."""
        clean_tenant_id = tenant_id.lower().strip()
        record = self.registry.set_status(clean_tenant_id, TenantStatus.SUSPENDED)
        logger.warning("Tenant '%s' has been SUSPENDED", clean_tenant_id)
        return record

    async def delete_tenant(self, tenant_id: str, purge_database: bool = False) -> bool:
        """Deletes tenant record and optionally removes tenant database file/schema."""
        clean_tenant_id = tenant_id.lower().strip()
        record = self.registry.get_tenant(clean_tenant_id)
        if not record:
            return False

        self.registry.set_status(clean_tenant_id, TenantStatus.DELETED)
        VALID_TENANTS.discard(clean_tenant_id)

        if purge_database:
            storage_dir = get_storage_dir()
            db_path = os.path.join(storage_dir, f"cfi_{clean_tenant_id}.db")
            if os.path.exists(db_path):
                try:
                    os.remove(db_path)
                    logger.info("Purged tenant database file: %s", db_path)
                except OSError as exc:
                    logger.warning("Failed to remove tenant DB file %s: %s", db_path, exc)

        logger.info("Tenant '%s' DELETED cleanly", clean_tenant_id)
        return True
