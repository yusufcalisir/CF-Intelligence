"""Dependency injection for FastAPI route handlers.

Provides database sessions, services, and repositories as injectable
dependencies. Keeps route handlers thin and testable.
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.data_generator import DataGenerator
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.kms_service import KMSService, get_kms_service
from app.application.services.metrics_service import MetricsService
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import PrivacyService
from app.application.services.simulation_service import SimulationService
from app.application.services.tenant_metering import (
    TenantMeteringService,
    get_tenant_metering_service,
)
from app.config import Settings, get_settings
from app.infrastructure.database import active_tenant, get_async_session
from app.infrastructure.repositories.bank_repository import BankRepository
from app.infrastructure.repositories.metrics_repository import MetricsRepository
from app.infrastructure.repositories.simulation_repository import SimulationRepository

# ── Settings ──────────────────────────────────
SettingsDep = Annotated[Settings, Depends(get_settings)]


# ── Tenant Resolution & BOLA / ABAC Access Control ───
async def resolve_tenant(request: Request) -> str | None:
    """Extract the bank tenant from the request and bind it to the active context.

    Resolution order:
        1. ``Authorization`` Bearer JWT Token (OIDC claims)
        2. ``X-Tenant-ID`` or ``X-Bank-ID`` headers (explicit identity)
        3. API key metadata embedded in the ``X-API-Key`` header
           (format: ``key_bank_a:bank_a:bank`` → tenant = ``bank_a``)
        4. ``bank_id`` query parameter

    Returns the resolved tenant identifier or None for system-level access.
    """
    tenant: str | None = None
    is_privileged = False

    # 1. Bearer JWT Token (OIDC)
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        try:
            from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator

            auth_helper = OIDCAuthenticator()
            valid, claims, _ = auth_helper.decode_and_validate_token(token)
            if valid and claims:
                if any(
                    r in claims.roles
                    for r in ("super_admin", "cross_bank_investigator", "compliance_auditor")
                ):
                    is_privileged = True
                else:
                    tenant = claims.bank_id
        except Exception:
            pass

    # If privileged, caller has cross-institution authority (unrestricted)
    if is_privileged:
        req_bank = request.query_params.get("bank_id")
        active_tenant.set(req_bank.strip() if req_bank else None)
        return None

    # 2. Explicit headers
    if not tenant:
        tenant = (
            request.headers.get("X-Tenant-ID")
            or request.headers.get("x-tenant-id")
            or request.headers.get("X-Bank-ID")
            or request.headers.get("x-bank-id")
        )

    # 3. API key metadata
    if not tenant:
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            parts = api_key.split(":")
            if len(parts) >= 2:
                tenant = parts[1]

    # 4. Query parameter fallback
    if not tenant:
        tenant = request.query_params.get("bank_id")

    # Set the context variable for downstream database routing
    if tenant:
        active_tenant.set(tenant.strip())
    else:
        active_tenant.set(None)

    return tenant.strip() if tenant else None



def enforce_tenant_isolation(
    caller_tenant: str | None,
    resource_bank_id: str | None,
    roles: list[str] | None = None,
) -> None:
    """Enforce ABAC / Broken Object-Level Authorization (BOLA) tenant isolation.

    If a caller has an identified tenant, they are strictly forbidden from accessing
    resources owned by another bank unless the resource is marked global or the caller
    has privileged cross-institution roles (super_admin, cross_bank_investigator).
    """
    if not caller_tenant or not resource_bank_id:
        return

    if roles and any(r in roles for r in ("super_admin", "cross_bank_investigator", "compliance_auditor")):
        return

    norm_caller = caller_tenant.lower().replace("-", "_").strip()
    norm_resource = resource_bank_id.lower().replace("-", "_").strip()

    if norm_resource in ("global", "all", "system", "coordinator"):
        return

    if norm_caller != norm_resource:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Broken Access Control Prevention: Tenant '{caller_tenant}' is not authorized "
                f"to access resources belonging to '{resource_bank_id}'."
            ),
        )


TenantDep = Annotated[str | None, Depends(resolve_tenant)]


# ── Database Session ──────────────────────────
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_async_session():
        yield session


async def get_optional_session() -> AsyncGenerator[AsyncSession | None, None]:
    try:
        async for session in get_async_session():
            yield session
    except Exception:
        yield None


SessionDep = Annotated[AsyncSession, Depends(get_session)]
OptionalSessionDep = Annotated[AsyncSession | None, Depends(get_optional_session)]


# ── Repositories ──────────────────────────────
def get_simulation_repository(session: SessionDep) -> SimulationRepository:
    return SimulationRepository(session)


def get_bank_repository(session: SessionDep) -> BankRepository:
    return BankRepository(session)


def get_metrics_repository(session: SessionDep) -> MetricsRepository:
    return MetricsRepository(session)


SimulationRepoDep = Annotated[SimulationRepository, Depends(get_simulation_repository)]
BankRepoDep = Annotated[BankRepository, Depends(get_bank_repository)]
MetricsRepoDep = Annotated[MetricsRepository, Depends(get_metrics_repository)]


# ── Services ──────────────────────────────────
def get_data_generator() -> DataGenerator:
    return DataGenerator()


def get_model_service(settings: SettingsDep) -> ModelService:
    return ModelService(settings)


def get_privacy_service() -> PrivacyService:
    return PrivacyService()


def get_metrics_service() -> MetricsService:
    return MetricsService()


def get_fl_engine(
    settings: SettingsDep,
    model_service: Annotated[ModelService, Depends(get_model_service)],
    privacy_service: Annotated[PrivacyService, Depends(get_privacy_service)],
) -> FederatedLearningEngine:
    return FederatedLearningEngine(settings, model_service, privacy_service)


def get_simulation_service(
    settings: SettingsDep,
    simulation_repo: SimulationRepoDep,
    bank_repo: BankRepoDep,
    metrics_repo: MetricsRepoDep,
    data_generator: Annotated[DataGenerator, Depends(get_data_generator)],
    fl_engine: Annotated[FederatedLearningEngine, Depends(get_fl_engine)],
    metrics_service: Annotated[MetricsService, Depends(get_metrics_service)],
    model_service: Annotated[ModelService, Depends(get_model_service)],
) -> SimulationService:
    return SimulationService(
        settings=settings,
        simulation_repo=simulation_repo,
        bank_repo=bank_repo,
        metrics_repo=metrics_repo,
        data_generator=data_generator,
        fl_engine=fl_engine,
        metrics_service=metrics_service,
        model_service=model_service,
    )


SimulationServiceDep = Annotated[SimulationService, Depends(get_simulation_service)]
DataGeneratorDep = Annotated[DataGenerator, Depends(get_data_generator)]
MetricsServiceDep = Annotated[MetricsService, Depends(get_metrics_service)]
FLEngineDep = Annotated[FederatedLearningEngine, Depends(get_fl_engine)]

# ── KMS ───────────────────────────────────────
KMSServiceDep = Annotated[KMSService, Depends(get_kms_service)]

# ── Tenant Metering & Quota Enforcement ───────
TenantMeteringDep = Annotated[TenantMeteringService, Depends(get_tenant_metering_service)]


def get_optional_tenant_metering_service() -> TenantMeteringService | None:
    """Return a TenantMeteringService instance, or None if unavailable."""
    return get_tenant_metering_service()


# Annotated must stay outermost so FastAPI extracts Depends() correctly.
# Using Optional[Annotated[...]] breaks FastAPI's dependency injection.
OptionalTenantMeteringDep = Annotated[
    TenantMeteringService | None, Depends(get_optional_tenant_metering_service)
]


async def enforce_tenant_quota(
    request: Request,
    tenant_id: TenantDep = None,
    metering: OptionalTenantMeteringDep = None,
) -> str:
    """FastAPI dependency enforcing tenant resource quotas before request execution.

    Extracts tenant identity and evaluates usage limits.
    Raises HTTP 429 Too Many Requests if quota is exceeded.
    """
    if metering is None:
        metering = get_tenant_metering_service()

    target_tenant = (
        tenant_id
        or request.headers.get("X-Tenant-ID")
        or request.headers.get("X-Bank-ID")
        or getattr(request.state, "tenant_id", None)
        or "bank_alpha"
    )

    allowed, reason = metering.check_quota(target_tenant, "INFERENCE")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=reason,
            headers={"Retry-After": "3600", "X-Quota-Exceeded": "true"},
        )
    return target_tenant

