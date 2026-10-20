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
    """Extract and authoritatively bind the tenant identity for the active context.

    Enforces runtime truth:
    - Never infers tenant identity from unauthenticated headers or query params.
    - Fails closed on invalid/expired/revoked credentials (HTTP 401).
    - Fails closed on conflicting headers (HTTP 400).
    - Rejects unauthorized cross-tenant requests (HTTP 403).
    - Binds verified tenant to active_tenant ContextVar.
    """
    from app.infrastructure.security.principal_auth import (
        resolve_authenticated_principal,
        resolve_authorized_tenant,
    )

    principal = getattr(request.state, "principal", None)
    if principal is None:
        principal, auth_err, auth_status = resolve_authenticated_principal(request)
        if auth_err:
            raise HTTPException(
                status_code=auth_status,
                detail=auth_err,
                headers={"WWW-Authenticate": "Bearer"} if auth_status == 401 else None,
            )
        request.state.principal = principal

    target_tenant, target_err, target_status = resolve_authorized_tenant(principal, request)
    if target_err:
        raise HTTPException(status_code=target_status, detail=target_err)

    if principal.is_privileged:
        active_tenant.set(target_tenant)
        return target_tenant

    if principal.is_authenticated:
        active_tenant.set(target_tenant)
        return target_tenant

    # Unauthenticated caller
    if request.headers.get("X-Tenant-ID") or request.headers.get("X-Bank-ID") or request.headers.get("X-API-Key"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Unauthenticated headers cannot confer tenant authority.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    active_tenant.set(None)
    return None



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
    session_yielded = False
    try:
        async for session in get_async_session():
            session_yielded = True
            yield session
            return
    except Exception:
        if session_yielded:
            raise
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

    Charges inference usage strictly to the authenticated, authorized tenant.
    Prevents double metering across nested dependencies or batch handlers.
    Raises HTTP 429 Too Many Requests if quota is exceeded.
    """
    if getattr(request.state, "quota_metered", False):
        return str(getattr(request.state, "metered_tenant", ""))

    if metering is None:
        metering = get_tenant_metering_service()

    from app.infrastructure.security.principal_auth import resolve_authenticated_principal

    principal = getattr(request.state, "principal", None)
    if principal is None:
        principal, auth_err, auth_status = resolve_authenticated_principal(request)
        if auth_err:
            raise HTTPException(
                status_code=auth_status,
                detail=auth_err,
                headers={"WWW-Authenticate": "Bearer"} if auth_status == 401 else None,
            )
        request.state.principal = principal

    if principal.is_authenticated:
        if principal.is_privileged:
            target_tenant = (
                tenant_id
                or request.headers.get("X-Tenant-ID")
                or request.headers.get("X-Bank-ID")
                or getattr(request.state, "tenant_id", None)
                or "system"
            )
        else:
            if not principal.tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Principal is not bound to an authorized tenant institution.",
                )

            selector = (request.headers.get("X-Tenant-ID") or request.headers.get("X-Bank-ID") or "").strip()
            if selector:
                norm_sel = selector.lower().replace("-", "_")
                norm_ten = principal.tenant_id.lower().replace("-", "_")
                if norm_sel != norm_ten:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Tenant '{principal.tenant_id}' cannot execute inference or consume quota on behalf of bank '{selector}'.",
                    )
            target_tenant = principal.tenant_id
    else:
        # Unauthenticated principal
        selector = (
            tenant_id
            or request.headers.get("X-Tenant-ID")
            or request.headers.get("X-Bank-ID")
            or ""
        ).strip()
        if selector:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Authentication required. Unauthenticated caller cannot consume quota for tenant '{selector}'.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        target_tenant = "synthetic_dev_quota"

    allowed, reason = metering.acquire_quota(target_tenant, "INFERENCE", count=1)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=reason,
            headers={"Retry-After": "3600", "X-Quota-Exceeded": "true"},
        )

    request.state.quota_metered = True
    request.state.metered_tenant = target_tenant
    return target_tenant

