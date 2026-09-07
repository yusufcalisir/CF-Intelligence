"""Alembic environment configuration proxy.

Synchronized with the canonical Alembic migration environment in
`app.infrastructure.database.migrations.env` to prevent dead-code divergence.
"""

from __future__ import annotations

# Re-export and execute the canonical migration environment
from app.infrastructure.database.migrations.env import (  # noqa: F401
    Base,
    _configure_tenant_schema,
    _get_active_tenants,
    _resolve_target_database_url,
    config,
    run_migrations_for_all_tenants,
    run_migrations_offline,
    run_migrations_online,
    target_metadata,
)
