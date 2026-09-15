# ruff: noqa: UP042
"""Domain models and registry for SaaS Multi-Tenancy Lifecycle Management."""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

_TENANT_ID_REGEX = re.compile(r"^[a-z0-9_]+$")


class TenantStatus(str, Enum):
    """Lifecycle state enum for a tenant bank node."""

    PROVISIONING = "PROVISIONING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


@dataclass
class TenantRecord:
    """Represents an onboarded financial institution in the SaaS platform."""

    tenant_id: str
    name: str
    status: TenantStatus = TenantStatus.PROVISIONING
    db_schema: str = ""
    kms_key_path: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tenant_id or not isinstance(self.tenant_id, str):
            raise ValueError("Tenant ID must be a non-empty string.")

        clean_id = self.tenant_id.lower().strip()
        if len(clean_id) > 48:
            raise ValueError(
                f"Tenant ID '{self.tenant_id}' exceeds 48-character maximum (PostgreSQL identifier limit)."
            )
        if not _TENANT_ID_REGEX.match(clean_id):
            raise ValueError(
                f"Invalid tenant_id format '{self.tenant_id}'. "
                "Must contain only lowercase alphanumeric characters and underscores."
            )
        if clean_id[0].isdigit():
            raise ValueError(
                f"Tenant ID '{self.tenant_id}' must not start with a digit (PostgreSQL identifier rule)."
            )

        self.tenant_id = clean_id
        if not self.db_schema:
            self.db_schema = f"tenant_{self.tenant_id}"
        if not self.kms_key_path:
            self.kms_key_path = f"storage/{self.tenant_id}/kms/"


class TenantRegistry:
    """Central thread-safe registry tracking active and onboarded tenant institutions."""

    _MAX_TENANTS: int = 10000

    def __init__(self) -> None:
        self._tenants: dict[str, TenantRecord] = {}
        self._lock = threading.RLock()

    def register_tenant(
        self, tenant_id: str, name: str, metadata: dict[str, Any] | None = None
    ) -> TenantRecord:
        """Registers a new tenant record in PROVISIONING state or reactivates a deleted one."""
        with self._lock:
            clean_id = tenant_id.lower().strip()
            if clean_id in self._tenants:
                record = self._tenants[clean_id]
                if record.status != TenantStatus.DELETED:
                    return record
                record.status = TenantStatus.PROVISIONING
                record.name = name
                record.updated_at = datetime.now(UTC)
                if metadata:
                    record.metadata.update(metadata)
                logger.info("Re-registered deleted tenant '%s' (%s) in PROVISIONING state", name, clean_id)
                return record

            if len(self._tenants) >= self._MAX_TENANTS:
                raise RuntimeError(f"Tenant registry capacity limit reached ({self._MAX_TENANTS})")

            record = TenantRecord(tenant_id=tenant_id, name=name, metadata=metadata or {})
            self._tenants[record.tenant_id] = record
            logger.info("Registered tenant '%s' (%s) in PROVISIONING state", name, record.tenant_id)
            return record

    def set_status(self, tenant_id: str, status: TenantStatus) -> TenantRecord:
        """Updates the status of an existing tenant."""
        clean_id = tenant_id.lower().strip()
        with self._lock:
            if clean_id not in self._tenants:
                raise KeyError(f"Tenant '{clean_id}' is not registered.")

            record = self._tenants[clean_id]
            record.status = status
            record.updated_at = datetime.now(UTC)
            logger.info("Tenant '%s' status updated to %s", clean_id, status.value)
            return record

    def get_tenant(self, tenant_id: str, include_deleted: bool = True) -> TenantRecord | None:
        """Retrieves tenant record by ID."""
        clean_id = tenant_id.lower().strip()
        with self._lock:
            record = self._tenants.get(clean_id)
            if record is None:
                return None
            if not include_deleted and record.status == TenantStatus.DELETED:
                return None
            return record

    def list_active_tenants(self) -> list[TenantRecord]:
        """Returns list of all ACTIVE tenants."""
        with self._lock:
            return [t for t in self._tenants.values() if t.status == TenantStatus.ACTIVE]

    def list_all_tenants(self, include_deleted: bool = False) -> list[TenantRecord]:
        """Returns list of all registered tenants, optionally filtering out DELETED ones."""
        with self._lock:
            if include_deleted:
                return list(self._tenants.values())
            return [t for t in self._tenants.values() if t.status != TenantStatus.DELETED]

    def is_tenant_active(self, tenant_id: str) -> bool:
        """Returns True if tenant exists and is ACTIVE."""
        clean_id = tenant_id.lower().strip()
        with self._lock:
            tenant = self._tenants.get(clean_id)
            return tenant is not None and tenant.status == TenantStatus.ACTIVE

    def delete_tenant(self, tenant_id: str) -> bool:
        """Marks tenant as DELETED."""
        clean_id = tenant_id.lower().strip()
        with self._lock:
            if clean_id not in self._tenants:
                return False
            self.set_status(clean_id, TenantStatus.DELETED)
            return True

    def purge_tenant(self, tenant_id: str) -> bool:
        """Purges tenant record completely from registry memory."""
        clean_id = tenant_id.lower().strip()
        with self._lock:
            return self._tenants.pop(clean_id, None) is not None
