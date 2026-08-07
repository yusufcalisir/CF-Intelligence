"""Dependency injection for FastAPI route handlers.

Provides database sessions, services, and repositories as injectable
dependencies. Keeps route handlers thin and testable.
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.data_generator import DataGenerator
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.kms_service import KMSService, get_kms_service
from app.application.services.metrics_service import MetricsService
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import PrivacyService
from app.application.services.simulation_service import SimulationService
from app.config import Settings, get_settings
from app.infrastructure.database import active_tenant, get_async_session
from app.infrastructure.repositories.bank_repository import BankRepository
from app.infrastructure.repositories.metrics_repository import MetricsRepository
from app.infrastructure.repositories.simulation_repository import SimulationRepository

# ── Settings ──────────────────────────────────
SettingsDep = Annotated[Settings, Depends(get_settings)]


# ── Tenant Resolution ────────────────────────
async def resolve_tenant(request: Request) -> str | None:
    """Extract the bank tenant from the request and bind it to the active context.

    Resolution order:
        1. ``X-Tenant-ID`` header (explicit override for internal services)
        2. ``bank_id`` query parameter
        3. API key metadata embedded in the ``X-API-Key`` header
           (format: ``key_bank_a:bank_a:bank`` → tenant = ``bank_a``)

    Returns the resolved tenant identifier or None for system-level access.
    """
    # 1. Explicit header
    tenant = request.headers.get("X-Tenant-ID")

    # 2. Query parameter fallback
    if not tenant:
        tenant = request.query_params.get("bank_id")

    # 3. API key metadata
    if not tenant:
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            parts = api_key.split(":")
            if len(parts) >= 2:
                tenant = parts[1]

    # Set the context variable for downstream database routing
    if tenant:
        active_tenant.set(tenant)
    else:
        active_tenant.set(None)

    return tenant


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
