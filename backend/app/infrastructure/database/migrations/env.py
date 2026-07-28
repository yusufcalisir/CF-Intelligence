"""Alembic migration environment script for production database schema management."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.config import get_settings
from app.infrastructure.database import Base

config = context.config

if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode with AsyncEngine."""
    settings = get_settings()
    db_url = (
        f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )

    connectable = create_async_engine(db_url)

    async with connectable.connect() as connection:
        await connection.run_sync(
            lambda conn: context.configure(connection=conn, target_metadata=target_metadata)
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


async def run_migrations_for_all_tenants() -> None:
    """Query active bank tenants and execute Alembic migrations per tenant schema space."""
    from sqlalchemy import text

    from app.infrastructure.database import VALID_TENANTS
    from app.infrastructure.database.tenant_provisioner import sanitize_bank_id

    settings = get_settings()
    db_url = (
        f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )

    connectable = create_async_engine(db_url)

    async with connectable.connect() as connection:
        for tenant in VALID_TENANTS:
            clean_bank_id = sanitize_bank_id(tenant)
            s_name = f"tenant_{clean_bank_id}"
            await connection.execute(text(f"SET search_path TO {s_name}, public"))
            await connection.run_sync(_configure_tenant_schema, s_name)
            async with connection.begin():
                await connection.run_sync(lambda conn: context.run_migrations())

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
