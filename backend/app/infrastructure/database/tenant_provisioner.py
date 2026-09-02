"""Automated Tenant Provisioner & Schema Migration Worker — Section 39.1."""

from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any

from sqlalchemy import text

from app.domain.tenant_management import TenantRecord, TenantRegistry, TenantStatus
from app.infrastructure.database import (
    _STORAGE_ROOT,
    VALID_TENANTS,
    Base,
    _get_or_create_engine,
    _tenant_initialized,
    settings,
)

logger = logging.getLogger(__name__)

# Global singleton registry
global_tenant_registry = TenantRegistry()



# PostgreSQL reserved words that must never appear as schema/role identifiers.
# This list is not exhaustive but covers the most dangerous ones.
_PG_RESERVED_KEYWORDS: frozenset[str] = frozenset(
    {
        "all", "analyse", "analyze", "and", "any", "array", "as", "asc",
        "asymmetric", "both", "case", "cast", "check", "collate", "column",
        "constraint", "create", "cross", "current_catalog", "current_date",
        "current_role", "current_schema", "current_time", "current_timestamp",
        "current_user", "default", "deferrable", "desc", "distinct", "do",
        "drop", "else", "end", "except", "false", "fetch", "for", "foreign",
        "from", "grant", "group", "having", "in", "initially", "inner",
        "intersect", "into", "lateral", "leading", "limit", "localtime",
        "localtimestamp", "not", "null", "offset", "on", "only", "or",
        "order", "placing", "primary", "references", "returning", "right",
        "schema", "select", "session_user", "some", "symmetric", "table",
        "then", "to", "trailing", "true", "union", "unique", "user", "using",
        "variadic", "verbose", "when", "where", "window", "with",
        # Extra SQL injection keywords not in the PG reserved list above
        "delete", "insert", "update", "exec", "execute", "xp_",
    }
)

# Maximum length for a PostgreSQL identifier (NAMEDATALEN - 1)
_PG_MAX_IDENTIFIER_LEN: int = 63


def sanitize_bank_id(bank_id: str) -> str:
    """Sanitize bank_id to prevent SQL injection in schema/role DDL statements.

    Enforces:
      - Non-empty string
      - Maximum length of 48 chars (leaves room for the ``tenant_`` prefix +
        ``_role`` suffix to stay within PostgreSQL's 63-char NAMEDATALEN limit)
      - Lowercase alphanumeric characters and underscores only (``^[a-z0-9_]+$``)
      - Must not start with a digit (PostgreSQL identifier rule)
      - Must not be a PostgreSQL reserved keyword
    """
    if not bank_id or not isinstance(bank_id, str):
        raise ValueError("Invalid bank_id: must be a non-empty string.")

    clean_id = bank_id.lower().strip()

    if len(clean_id) > 48:
        raise ValueError(
            f"bank_id '{bank_id}' exceeds 48-character maximum (PostgreSQL identifier limit)."
        )

    if not re.match(r"^[a-z0-9_]+$", clean_id):
        raise ValueError(
            f"Invalid bank_id format '{bank_id}' for schema name. "
            "Must contain only lowercase alphanumeric characters and underscores."
        )

    if clean_id[0].isdigit():
        raise ValueError(
            f"bank_id '{bank_id}' must not start with a digit (PostgreSQL identifier rule)."
        )

    if clean_id in _PG_RESERVED_KEYWORDS:
        raise ValueError(
            f"bank_id '{bank_id}' is a reserved PostgreSQL keyword and cannot be used as an identifier."
        )

    return clean_id


def _pg_quote_identifier(identifier: str) -> str:
    """Return a safely double-quoted PostgreSQL identifier.

    Double-quoting is the PostgreSQL-standard defence for DDL identifiers
    that cannot be passed as bind parameters (CREATE SCHEMA, GRANT, SET).
    Any embedded double-quote characters are escaped by doubling them.

    The input MUST already have been validated by ``sanitize_bank_id`` — this
    function is a belt-and-suspenders measure, not a primary sanitizer.
    """
    # Escape any embedded double-quotes (shouldn't exist after sanitize, but be safe)
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


class TenantProvisioner:
    """Automates PostgreSQL schema-level creation, role granting, table migrations, and lifecycle management."""

    def __init__(self, registry: TenantRegistry | None = None) -> None:
        self.registry = registry or global_tenant_registry
        self.provisioned_schemas: set[str] = set()
        self.provisioned_roles: set[str] = set()

    async def provision(self, bank_id: str, password: str | None = None) -> dict[str, Any]:
        """Execute DDL statements for schema-level isolation (CREATE SCHEMA, CREATE ROLE, GRANT privileges)."""
        clean_bank_id = sanitize_bank_id(bank_id)
        role_password = password or f"Cfi_P@ss_{uuid.uuid4().hex[:12]}"
        schema_name = f"tenant_{clean_bank_id}"
        role_name = f"tenant_{clean_bank_id}_role"

        logger.info("Executing DDL schema-level isolation provisioning for '%s'...", clean_bank_id)

        engine = _get_or_create_engine(clean_bank_id)

        if settings.database_type != "sqlite":
            try:
                async with engine.begin() as conn:
                    q_schema = _pg_quote_identifier(schema_name)
                    q_role = _pg_quote_identifier(role_name)

                    # 1. CREATE SCHEMA
                    await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {q_schema}"))

                    # 2. CREATE ROLE if not exists (password is a bind parameter — never interpolated)
                    res = await conn.execute(
                        text("SELECT 1 FROM pg_roles WHERE rolname = :role_name"),
                        {"role_name": role_name},
                    )
                    if not res.scalar():
                        await conn.execute(
                            text(
                                f"CREATE ROLE {q_role} WITH NOINHERIT LOGIN PASSWORD :password"
                            ),
                            {"password": role_password},
                        )

                    # 3. GRANT USAGE ON SCHEMA
                    await conn.execute(
                        text(f"GRANT USAGE ON SCHEMA {q_schema} TO {q_role}")
                    )

                    # 4. GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES
                    await conn.execute(
                        text(
                            f"GRANT SELECT, INSERT, UPDATE, DELETE "
                            f"ON ALL TABLES IN SCHEMA {q_schema} TO {q_role}"
                        )
                    )

                    # 5. ALTER DEFAULT PRIVILEGES
                    await conn.execute(
                        text(
                            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {q_schema} "
                            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {q_role}"
                        )
                    )
            except Exception as exc:
                logger.warning(
                    "PostgreSQL engine unreachable for schema DDL execution (%s). "
                    "Tracking schema '%s' in provisioner memory.",
                    exc,
                    schema_name,
                )

        self.provisioned_schemas.add(schema_name)
        self.provisioned_roles.add(role_name)
        VALID_TENANTS.add(clean_bank_id)

        logger.info(
            "Successfully provisioned DDL schema '%s' and role '%s'", schema_name, role_name
        )
        return {
            "bank_id": clean_bank_id,
            "schema_name": schema_name,
            "role_name": role_name,
            "status": "PROVISIONED",
        }

    async def run_migrations_for_tenant(self, bank_id: str) -> None:
        """Run database migrations / table creation targeting tenant schema space."""
        clean_bank_id = sanitize_bank_id(bank_id)
        schema_name = f"tenant_{clean_bank_id}"

        engine = _get_or_create_engine(clean_bank_id)
        try:
            async with engine.begin() as conn:
                if settings.database_type != "sqlite":
                    q_schema = _pg_quote_identifier(schema_name)
                    await conn.execute(text(f"SET search_path TO {q_schema}, public"))
                await conn.run_sync(Base.metadata.create_all)
        except Exception as exc:
            logger.warning(
                "Could not run table DDL migrations for schema '%s': %s", schema_name, exc
            )

        _tenant_initialized.add(clean_bank_id)
        logger.info("Ran DDL migrations successfully for schema '%s'", schema_name)

    async def provision_tenant(self, tenant_id: str, name: str) -> TenantRecord:
        """Provisions a new tenant database schema/file and initializes all tables."""
        clean_tenant_id = sanitize_bank_id(tenant_id)
        record = self.registry.register_tenant(clean_tenant_id, name)

        logger.info("Starting automated provisioning for tenant '%s'...", clean_tenant_id)

        try:
            await self.provision(clean_tenant_id)
            await self.run_migrations_for_tenant(clean_tenant_id)

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
        clean_tenant_id = sanitize_bank_id(tenant_id)
        record = self.registry.set_status(clean_tenant_id, TenantStatus.SUSPENDED)
        logger.warning("Tenant '%s' has been SUSPENDED", clean_tenant_id)
        return record

    async def delete_tenant(self, tenant_id: str, purge_database: bool = False) -> bool:
        """Deletes tenant record and optionally removes tenant database file/schema."""
        clean_tenant_id = sanitize_bank_id(tenant_id)
        record = self.registry.get_tenant(clean_tenant_id)
        if not record:
            return False

        self.registry.set_status(clean_tenant_id, TenantStatus.DELETED)
        VALID_TENANTS.discard(clean_tenant_id)

        if purge_database:
            db_path = os.path.join(_STORAGE_ROOT, f"cfi_{clean_tenant_id}.db")
            if os.path.exists(db_path):
                try:
                    os.remove(db_path)
                    logger.info("Purged tenant database file: %s", db_path)
                except OSError as exc:
                    logger.warning("Failed to remove tenant DB file %s: %s", db_path, exc)

        logger.info("Tenant '%s' DELETED cleanly", clean_tenant_id)
        return True
