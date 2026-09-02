"""Async database engine and session management with multi-tenant isolation.

Uses SQLAlchemy 2.0 async API with asyncpg for non-blocking PostgreSQL/CockroachDB access.
Sessions are scoped to FastAPI requests via dependency injection.

Multi-Tenancy (SOC2/PCI-DSS Compliance):
    Each bank institution operates against its own isolated database instance.
    The ``active_tenant`` context variable determines which database engine
    and session factory to use for the current request or task.  When no
    tenant is set, the system-level "central" database is used.

    SQLite mode:
        storage/cfi_central.db   — coordinator / system tables
        storage/cfi_bank_a.db    — Bank A's isolated tables
        storage/cfi_bank_b.db    — Bank B's isolated tables
        storage/cfi_bank_c.db    — Bank C's isolated tables

    PostgreSQL/CockroachDB mode:
        Separate database names suffixed per tenant (e.g. fraud_intelligence_bank_a).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Coroutine

    from starlette.requests import Request
    from starlette.responses import Response

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.infrastructure.storage.storage_utils import get_storage_dir

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""

    pass


# ── Tenant Context ────────────────────────────
# Set by middleware / simulation loops to route DB operations to the correct bank.
active_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_tenant", default=None
)

# Valid tenant identifiers — extend as new banks are onboarded.
VALID_TENANTS = {"bank_a", "bank_b", "bank_c"}

settings = get_settings()

# ── Storage root for SQLite databases ─────────
# Backward-compatible reference pointing to guaranteed writable storage
_STORAGE_ROOT = get_storage_dir()


def _resolve_database_url(tenant: str | None) -> str:
    """Return the async database URL for the given tenant.

    * ``None`` → central/system database
    * ``"bank_a"`` → bank_a's isolated database
    """
    if settings.database_type == "sqlite":
        storage_dir = get_storage_dir()
        with contextlib.suppress(OSError):
            os.makedirs(storage_dir, exist_ok=True)
            os.chmod(storage_dir, 0o777)  # nosec B103
        db_name = f"cfi_{tenant}.db" if tenant else "cfi_central.db"
        db_path = os.path.abspath(os.path.join(storage_dir, db_name))
        clean_path = db_path.replace("\\", "/")
        if not clean_path.startswith("/"):
            clean_path = f"/{clean_path}"
        return f"sqlite+aiosqlite://{clean_path}"

    # PostgreSQL / CockroachDB: append tenant suffix to database name
    base_db = settings.postgres_db
    db_name = f"{base_db}_{tenant}" if tenant else base_db
    return (
        f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{db_name}"
    )


def _make_engine_kwargs(tenant: str | None) -> dict[str, Any]:
    """Build engine keyword arguments with production connection pooling."""
    if settings.database_type == "sqlite":
        return {"echo": settings.app_debug}

    kwargs: dict[str, Any] = {
        "echo": settings.app_debug,
        "pool_size": 20,
        "max_overflow": 10,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
    }
    if settings.database_type == "cockroachdb":
        kwargs["connect_args"] = {"application_name": f"cfi_{tenant or 'central'}"}
    return kwargs


# ── Tenant Engine Registry ────────────────────
# Lazily created engines and session factories, one per tenant.
_tenant_engines: dict[str | None, AsyncEngine] = {}
_tenant_sessions: dict[str | None, async_sessionmaker[AsyncSession]] = {}
_tenant_initialized: set[str | None] = set()


def _get_or_create_engine(tenant: str | None) -> AsyncEngine:
    """Return (and cache) the async engine for a tenant, creating it on first access."""
    if tenant not in _tenant_engines:
        url = _resolve_database_url(tenant)
        eng = create_async_engine(url, **_make_engine_kwargs(tenant))
        _tenant_engines[tenant] = eng
        _tenant_sessions[tenant] = async_sessionmaker(
            eng, class_=AsyncSession, expire_on_commit=False
        )
        logger.info("Created database engine for tenant=%s url=%s", tenant or "central", url)
    return _tenant_engines[tenant]


_init_lock = asyncio.Lock()


async def init_tenant_tables(tenant: str | None) -> None:
    """Create all ORM tables in the tenant's database if not already initialized.

    Called lazily on first session access or eagerly at startup.
    """
    if tenant in _tenant_initialized:
        return
    async with _init_lock:
        if tenant in _tenant_initialized:
            return
        eng = _get_or_create_engine(tenant)
        try:
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                if tenant is None:
                    # Seed default demo banks if tenant_configs table is empty
                    from datetime import UTC, datetime

                    from sqlalchemy import insert, select

                    from app.infrastructure.models import TenantConfigModel

                    result = await conn.execute(select(TenantConfigModel.bank_id).limit(1))
                    if result.first() is None:
                        demo_banks = [
                            {
                                "bank_id": "bank_alpha",
                                "legal_name": "JPMorgan Chase & Co.",
                                "jurisdiction": "US",
                                "contact_email": "compliance@jpmorgan.com",
                                "data_residency_region": "us-east-1",
                                "status": "active",
                                "cert_fingerprint": "sha256_jpm_01",
                                "vault_key_path": "transit/keys/tenant_bank_alpha",
                                "schema_provisioned": 1,
                                "created_at": datetime.now(UTC),
                                "activated_at": datetime.now(UTC),
                            },
                            {
                                "bank_id": "bank_beta",
                                "legal_name": "HSBC Holdings plc",
                                "jurisdiction": "GB",
                                "contact_email": "compliance@hsbc.co.uk",
                                "data_residency_region": "eu-west-2",
                                "status": "active",
                                "cert_fingerprint": "sha256_hsbc_02",
                                "vault_key_path": "transit/keys/tenant_bank_beta",
                                "schema_provisioned": 1,
                                "created_at": datetime.now(UTC),
                                "activated_at": datetime.now(UTC),
                            },
                            {
                                "bank_id": "bank_gamma",
                                "legal_name": "Deutsche Bank AG",
                                "jurisdiction": "DE",
                                "contact_email": "compliance@db.com",
                                "data_residency_region": "eu-central-1",
                                "status": "active",
                                "cert_fingerprint": "sha256_db_03",
                                "vault_key_path": "transit/keys/tenant_bank_gamma",
                                "schema_provisioned": 1,
                                "created_at": datetime.now(UTC),
                                "activated_at": datetime.now(UTC),
                            },
                        ]
                        await conn.execute(insert(TenantConfigModel), demo_banks)
            _tenant_initialized.add(tenant)
            logger.info("Initialized tables for tenant=%s", tenant or "central")
        except Exception as exc:
            logger.warning("Could not initialize tenant tables for tenant=%s: %s", tenant, exc)


def get_session_factory(tenant: str | None = None) -> async_sessionmaker[AsyncSession]:
    """Return the session factory for the given tenant, creating engine if needed."""
    _get_or_create_engine(tenant)
    return _tenant_sessions[tenant]


# ── Backward-compatible default engine ────────
# The module-level ``engine`` and ``async_session_factory`` remain available for
# existing code that imports them directly.  They point to the *central* database.
engine = _get_or_create_engine(None)
async_session_factory = get_session_factory(None)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session scoped to the current request.

    Reads ``active_tenant`` to select the tenant-specific session factory.
    Falls back to the central database when no tenant context is set.
    """
    tenant = active_tenant.get()
    await init_tenant_tables(tenant)
    factory = get_session_factory(tenant)
    async with factory() as session:
        if tenant and settings.database_type != "sqlite":
            from app.infrastructure.database.tenant_provisioner import (
                _pg_quote_identifier,
                sanitize_bank_id,
            )

            try:
                clean_id = sanitize_bank_id(tenant)
                q_schema = _pg_quote_identifier(f"tenant_{clean_id}")
                await session.execute(text(f"SET search_path TO {q_schema}, public"))
            except Exception as exc:
                logger.warning("Could not set search_path for tenant_%s: %s", tenant, exc)
        try:
            yield session
        finally:
            await session.close()


async def get_tenant_session(bank_id: str) -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession scoped to the specified tenant's PostgreSQL schema (tenant_{bank_id})."""
    from app.infrastructure.database.tenant_provisioner import sanitize_bank_id

    clean_bank_id = sanitize_bank_id(bank_id)
    await init_tenant_tables(clean_bank_id)
    factory = get_session_factory(clean_bank_id)

    async with factory() as session:
        if settings.database_type != "sqlite":
            from app.infrastructure.database.tenant_provisioner import _pg_quote_identifier

            try:
                q_schema = _pg_quote_identifier(f"tenant_{clean_bank_id}")
                await session.execute(text(f"SET search_path TO {q_schema}, public"))
            except Exception as exc:
                logger.warning("Could not set search_path for tenant_%s: %s", clean_bank_id, exc)
        try:
            yield session
        finally:
            await session.close()


class TenantContextMiddleware(BaseHTTPMiddleware):
    """FastAPI/Starlette middleware reading X-Bank-ID header and setting active_tenant contextvar."""

    async def dispatch(  # type: ignore[override]
        self, request: Request, call_next: Callable[[Request], Coroutine[Any, Any, Response]]
    ) -> Response:
        bank_id_hdr = request.headers.get("X-Bank-ID") or request.headers.get("x-bank-id")
        token_reset = None
        if bank_id_hdr:
            from app.infrastructure.database.tenant_provisioner import sanitize_bank_id

            try:
                clean_id = sanitize_bank_id(bank_id_hdr)
                token_reset = active_tenant.set(clean_id)
            except ValueError as err:
                logger.warning("Invalid X-Bank-ID header '%s': %s", bank_id_hdr, err)

        try:
            return await call_next(request)
        finally:
            if token_reset:
                active_tenant.reset(token_reset)


async def run_cockroach_transaction(
    session_factory: async_sessionmaker[AsyncSession],
    callback: Callable[[AsyncSession], Coroutine[Any, Any, Any]],
    max_retries: int = 5,
) -> Any:
    """Execute a database transaction block with CockroachDB serializable isolation conflict retry loops.

    Saves state by handling SQLSTATE 40001 (serialization_failure) and transparently retrying.
    """
    for attempt in range(max_retries):
        async with session_factory() as session, session.begin():
            try:
                return await callback(session)
            except DBAPIError as err:
                # SQLSTATE 40001 represents a retryable transaction failure in CockroachDB/PostgreSQL
                if (
                    err.orig
                    and hasattr(err.orig, "pgcode")
                    and err.orig.pgcode == "40001"
                    and attempt < max_retries - 1
                ):
                    logger.warning(
                        "CockroachDB serializable transaction conflict (40001) detected. "
                        "Retrying transaction attempt %d/%d...",
                        attempt + 1,
                        max_retries,
                    )
                    continue
                raise


async def dispose_all_engines() -> None:
    """Dispose all tenant engines on application shutdown."""
    for tenant, eng in _tenant_engines.items():
        logger.info("Disposing engine for tenant=%s", tenant or "central")
        await eng.dispose()
    _tenant_engines.clear()
    _tenant_sessions.clear()
    _tenant_initialized.clear()
