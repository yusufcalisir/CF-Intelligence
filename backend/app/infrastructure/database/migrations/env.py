"""Alembic migration environment script for production database schema management."""

from __future__ import annotations

import asyncio
import contextlib
from logging.config import fileConfig
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context  # type: ignore[attr-defined]
from app.config import get_settings
from app.infrastructure.database import Base

try:
    config = context.config
except AttributeError:
    config = None  # type: ignore[assignment]

if config and config.config_file_name:
    with contextlib.suppress(Exception):
        fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_target_database_url() -> str:
    """Resolve database URL dynamically from x-arguments, environment, or settings."""
    import os

    # 1. Custom URL passed via CLI '-x url=...'
    if context and hasattr(context, "get_x_argument"):
        with contextlib.suppress(Exception):
            x_args = context.get_x_argument(as_dictionary=True)
            if x_args and "url" in x_args:
                return x_args["url"]

    # 2. Environment variable overrides
    env_url = os.getenv("ALEMBIC_DATABASE_URL") or os.getenv("DATABASE_URL")
    if env_url:
        return env_url

    # 3. Config option if explicitly configured (e.g. from alembic.ini or cfg.set_main_option)
    if config:
        cfg_url = config.get_main_option("sqlalchemy.url")
        if cfg_url and cfg_url.strip() and "change_me_in_production" not in cfg_url:
            return cfg_url.strip()

    # 4. Project settings fallback
    settings = get_settings()
    if settings.database_type == "sqlite":
        from app.infrastructure.database import _STORAGE_ROOT

        db_path = os.path.abspath(os.path.join(_STORAGE_ROOT, "cfi_central.db")).replace("\\", "/")
        return f"sqlite+aiosqlite:///{db_path}"

    # 5. Default PostgreSQL credentials from settings
    return (
        f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = _resolve_target_database_url()
    if "+aiosqlite" in url:
        url = url.replace("+aiosqlite", "")
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode with AsyncEngine."""
    db_url = _resolve_target_database_url()
    if db_url.startswith("sqlite:///") and "+aiosqlite" not in db_url:
        db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///")

    is_sqlite = db_url.startswith("sqlite")
    connectable = create_async_engine(db_url)

    async with connectable.connect() as connection:
        await connection.run_sync(
            lambda conn: context.configure(
                connection=conn,
                target_metadata=target_metadata,
                render_as_batch=is_sqlite,
            )
        )

        async with connection.begin():
            await connection.run_sync(lambda conn: context.run_migrations())

    await connectable.dispose()


def _configure_tenant_schema(sync_conn: Any, target_schema: str) -> None:
    context.configure(
        connection=sync_conn,
        target_metadata=target_metadata,
        version_table_schema=target_schema,
    )


async def _get_active_tenants(connection_url: str) -> list[str]:
    """Dynamically query active tenants from tenant_configs, falling back to VALID_TENANTS."""
    from sqlalchemy import select

    from app.infrastructure.database import VALID_TENANTS
    from app.infrastructure.models import TenantConfigModel

    engine = create_async_engine(connection_url)
    try:
        async with engine.connect() as conn:
            stmt = select(TenantConfigModel.bank_id).where(TenantConfigModel.status != "suspended")
            result = await conn.execute(stmt)
            tenants = [row[0] for row in result.fetchall() if row[0]]
            if tenants:
                return sorted(tenants)
    except Exception:
        # Fallback to configured VALID_TENANTS if tenant_configs table does not exist or fails
        pass
    finally:
        await engine.dispose()

    return sorted(list(VALID_TENANTS))


def _is_context_bound() -> bool:
    """Return True if alembic.context is actively bound inside a migration run."""
    try:
        context.is_offline_mode()
        return True
    except (NameError, AttributeError):
        return False


async def run_migrations_for_all_tenants() -> None:
    """Query active bank tenants dynamically and execute Alembic migrations per tenant space."""
    from sqlalchemy import text

    from app.infrastructure.database import _resolve_database_url
    from app.infrastructure.database.tenant_provisioner import sanitize_bank_id

    settings = get_settings()
    central_url = _resolve_target_database_url()
    if central_url.startswith("sqlite:///") and "+aiosqlite" not in central_url:
        central_url = central_url.replace("sqlite:///", "sqlite+aiosqlite:///")

    tenants = await _get_active_tenants(central_url)

    if not _is_context_bound():
        # Standalone programmatic invocation without an active Alembic runner
        from app.infrastructure.database.migration_manager import upgrade_head

        for tenant in tenants:
            clean_bank_id = sanitize_bank_id(tenant)
            tenant_url = _resolve_database_url(clean_bank_id)
            if "+aiosqlite" in tenant_url:
                tenant_sync_url = tenant_url.replace("+aiosqlite", "")
            else:
                tenant_sync_url = tenant_url
            await asyncio.to_thread(upgrade_head, tenant_sync_url)
        return

    if settings.database_type == "sqlite":
        # SQLite: isolated database file per tenant
        for tenant in tenants:
            clean_bank_id = sanitize_bank_id(tenant)
            tenant_url = _resolve_database_url(clean_bank_id)
            tenant_engine = create_async_engine(tenant_url)
            async with tenant_engine.connect() as conn:
                await conn.run_sync(
                    lambda sync_conn: context.configure(
                        connection=sync_conn,
                        target_metadata=target_metadata,
                        render_as_batch=True,
                    )
                )
                async with conn.begin():
                    await conn.run_sync(lambda sync_conn: context.run_migrations())
            await tenant_engine.dispose()
    else:
        # PostgreSQL / CockroachDB: schema-isolated tenancy via search_path
        connectable = create_async_engine(central_url)
        async with connectable.connect() as connection:
            for tenant in tenants:
                clean_bank_id = sanitize_bank_id(tenant)
                s_name = f"tenant_{clean_bank_id}"
                await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {s_name}"))
                await connection.execute(text(f"SET search_path TO {s_name}, public"))
                await connection.run_sync(_configure_tenant_schema, s_name)
                async with connection.begin():
                    await connection.run_sync(lambda conn: context.run_migrations())

        await connectable.dispose()


if config is not None:
    try:
        if context.is_offline_mode():
            run_migrations_offline()
        else:
            asyncio.run(run_migrations_online())
    except NameError:
        pass
