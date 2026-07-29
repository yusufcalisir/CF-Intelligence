# ruff: noqa: E402
from __future__ import annotations

import logging
import os

# Configure CPU threading limits to 2 cores for maximum performance
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["VECLIB_MAXIMUM_THREADS"] = "2"
os.environ["NUMEXPR_NUM_THREADS"] = "2"

print(">>> Python main.py loaded successfully! <<<", flush=True)

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.presentation.routers import (
    alerts,
    bank_client,
    banks,
    cases,
    compliance,
    coordinator,
    dashboard,
    entities,
    graph,
    health,
    maintenance_cron,
    model_registry,
    monitoring,
    predict,
    privacy_defense,
    psd2,
    realtime_inference,
    rules,
    scenarios,
    security,
    settlement,
    simulation,
    training,
)
from app.presentation.websockets import streaming_ws, training_ws

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# ── Logging ───────────────────────────────────
settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.app_log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Tenant-Isolated Logging ──────────────────
def _setup_tenant_logging() -> None:
    """Add per-tenant file handlers that route logs to isolated files.

    Each bank's logs are written to ``storage/logs/{bank_id}.log``.
    System/coordinator logs go to ``storage/logs/system.log``.
    """
    import os

    from app.infrastructure.database import active_tenant

    logs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "storage", "logs"))
    os.makedirs(logs_dir, exist_ok=True)

    class TenantLogFilter(logging.Filter):
        """Filter that only passes records matching the target tenant."""

        def __init__(self, target_tenant: str | None) -> None:
            super().__init__()
            self.target_tenant = target_tenant

        def filter(self, record: logging.LogRecord) -> bool:
            current = active_tenant.get()
            return current == self.target_tenant

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # System log handler
    sys_handler = logging.FileHandler(os.path.join(logs_dir, "system.log"), encoding="utf-8")
    sys_handler.setFormatter(fmt)
    sys_handler.addFilter(TenantLogFilter(None))
    logging.getLogger().addHandler(sys_handler)

    # Per-bank log handlers
    for tenant in ("bank_a", "bank_b", "bank_c"):
        handler = logging.FileHandler(os.path.join(logs_dir, f"{tenant}.log"), encoding="utf-8")
        handler.setFormatter(fmt)
        handler.addFilter(TenantLogFilter(tenant))
        logging.getLogger().addHandler(handler)

    logger.info("Tenant-isolated logging configured → %s", logs_dir)


try:
    _setup_tenant_logging()
except Exception as exc:
    logger.warning("Failed to set up tenant-isolated logging: %s", exc)


# ── Lifecycle ─────────────────────────────────
# ── Lifecycle ─────────────────────────────────
def seed_mock_data() -> None:
    """Seed initial mock data for Phase 2 AML platform."""
    from app.application.services.alert_service import _alert_to_dict, _intel_to_dict
    from app.application.services.case_service import _case_to_dict
    from app.domain.entities_phase2 import Alert, SharedIntelligence
    from app.domain.enums import (
        AlertSeverity,
        AlertStatus,
        CasePriority,
        CaseStatus,
        EntityType,
        IntelligenceType,
        RelationshipType,
        RiskLevel,
    )
    from app.presentation.routers.alerts import get_alert_service
    from app.presentation.routers.cases import get_case_service
    from app.presentation.routers.entities import get_entity_service
    from app.presentation.routers.graph import get_graph_engine

    alert_svc = get_alert_service()
    case_svc = get_case_service()
    entity_svc = get_entity_service()
    graph_engine = get_graph_engine()

    # Clear existing to be idempotent
    alert_svc._alert_store.clear()
    alert_svc._intelligence_store.clear()
    case_svc._cases.clear()
    entity_svc._entities.clear()
    entity_svc._relationships.clear()
    entity_svc._hash_index.clear()
    graph_engine._entities.clear()
    graph_engine._relationships.clear()
    graph_engine._adjacency.clear()

    # 1. Create seed entities
    c1 = entity_svc.create_entity(
        EntityType.CUSTOMER,
        "user_john_doe",
        "bank_a",
        {"risk_score": 0.12, "bank_name": "Meridian National"},
    )
    c2 = entity_svc.create_entity(
        EntityType.CUSTOMER,
        "user_jane_smith",
        "bank_b",
        {"risk_score": 0.85, "bank_name": "Nexus Digital"},
    )
    c3 = entity_svc.create_entity(
        EntityType.CUSTOMER,
        "user_bob_jones",
        "bank_c",
        {"risk_score": 0.45, "bank_name": "Heritage Regional"},
    )

    dev1 = entity_svc.create_entity(
        EntityType.DEVICE, "device_secure_token_99", "bank_a", {"device_type": "mobile_app"}
    )
    dev2 = entity_svc.create_entity(
        EntityType.DEVICE, "device_secure_token_99", "bank_b", {"device_type": "mobile_app"}
    )

    m1 = entity_svc.create_entity(
        EntityType.MERCHANT, "merchant_crypto_exchange", "bank_b", {"category": "crypto"}
    )
    m2 = entity_svc.create_entity(
        EntityType.MERCHANT, "merchant_luxury_store", "bank_c", {"category": "luxury"}
    )

    for e in [c1, c2, c3, dev1, dev2, m1, m2]:
        graph_engine.register_entity(e)

    # 2. Create relationships
    r1 = entity_svc.add_relationship(c1.id, dev1.id, RelationshipType.USES, confidence=1.0)
    r2 = entity_svc.add_relationship(c2.id, dev2.id, RelationshipType.USES, confidence=1.0)
    r3 = entity_svc.add_relationship(c2.id, m1.id, RelationshipType.TRANSACTS_WITH, confidence=0.95)
    r4 = entity_svc.add_relationship(c3.id, m2.id, RelationshipType.TRANSACTS_WITH, confidence=0.80)
    r5 = entity_svc.add_relationship(
        dev1.id, dev2.id, RelationshipType.SHARES_DEVICE, confidence=1.0
    )

    for r in [r1, r2, r3, r4, r5]:
        graph_engine.add_relationship(r)

    # 3. Create mock alerts
    a1 = Alert(
        bank_id="bank_b",
        transaction_id="tx_98234",
        risk_score=850.0,
        severity=AlertSeverity.HIGH,
        status=AlertStatus.NEW,
        reason_codes=["VEL-001", "DEV-ANOM"],
        confidence=0.85,
        involved_entity_ids=[c2.id],
        model_confidence=0.85,
        top_features=[
            {"feature": "velocity", "value": 0.92},
            {"feature": "new_device", "value": 1.0},
        ],
        risk_factors=[
            "Rapid transfer immediately after device change",
            "Unusual high-risk merchant destination",
        ],
    )
    alert_svc._alert_store.set(a1.id, _alert_to_dict(a1))
    entity_svc.increment_alert_count(c2.id)
    entity_svc.update_risk_level(c2.id, RiskLevel.HIGH)

    a2 = Alert(
        bank_id="bank_c",
        transaction_id="tx_12049",
        risk_score=450.0,
        severity=AlertSeverity.MEDIUM,
        status=AlertStatus.NEW,
        reason_codes=["AMT-ANOM"],
        confidence=0.45,
        involved_entity_ids=[c3.id],
        model_confidence=0.45,
        top_features=[{"feature": "amount", "value": 0.78}],
        risk_factors=["Transaction amount significantly exceeds customer historical average"],
    )
    alert_svc._alert_store.set(a2.id, _alert_to_dict(a2))
    entity_svc.increment_alert_count(c3.id)
    entity_svc.update_risk_level(c3.id, RiskLevel.MEDIUM)

    # 4. Create mock case
    case = case_svc.create_case(
        title="High-Risk Activity: Device Sharing & Crypto Outflow",
        priority=CasePriority.P2_HIGH,
        alert_ids=[a1.id],
    )
    case.assigned_to = "senior_analyst_1"
    case.status = CaseStatus.INVESTIGATING
    case_svc._cases.set(case.id, _case_to_dict(case))

    # 5. Create shared intelligence
    intel = SharedIntelligence(
        source_bank_id="bank_b",
        intelligence_type=IntelligenceType.FRAUD_ALERT,
        privacy_hash=dev1.privacy_id,
        risk_indicator=0.85,
        description="High-risk device hash associated with rapid account takeovers",
        entity_type=EntityType.DEVICE,
        related_alert_count=1,
    )
    alert_svc._intelligence_store.push_list("intelligence_list", _intel_to_dict(intel))
    logger.info("Successfully seeded mock data for Phase 2")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown hooks."""
    logger.info("Environment: %s", settings.app_env)

    # Configure PyTorch runtime threads for 2 cores
    try:
        import torch

        torch.set_num_threads(2)
        torch.set_num_interop_threads(2)
    except Exception as e:
        logger.warning("Could not set PyTorch threading limits: %s", e)

    # Seed mock data
    try:
        seed_mock_data()
    except Exception as exc:
        logger.error("Failed to seed mock data: %s", exc, exc_info=True)

    # Start Redis Bank Client Listeners
    redis_listeners = []
    if service_name.startswith("bank-"):
        try:
            from app.presentation.messaging.redis_listener import RedisBankClientListener

            redis_url = settings.redis_url
            if redis_url:
                listener = RedisBankClientListener(redis_url=redis_url, bank_id=service_name)
                await listener.start()
                redis_listeners.append(listener)
        except Exception as exc:
            logger.error("Failed to start Redis Bank Client Listener: %s", exc)
    elif not service_name:
        try:
            from app.presentation.messaging.redis_listener import RedisBankClientListener

            redis_url = settings.redis_url
            if redis_url:
                for b_id in ["bank-a", "bank-b", "bank-c"]:
                    listener = RedisBankClientListener(redis_url=redis_url, bank_id=b_id)
                    await listener.start()
                    redis_listeners.append(listener)
        except Exception as exc:
            logger.error("Failed to start monolith Redis Bank Client Listeners: %s", exc)

    yield

    # Shutdown Redis Bank Client Listeners
    for listener in redis_listeners:
        try:
            await listener.stop()
        except Exception as exc:
            logger.error("Failed to stop Redis Bank Client Listener cleanly: %s", exc)

    logger.info("Shutting down")


# ── Application ───────────────────────────────
mode_env = os.getenv("MODE", "").lower()
service_name = (mode_env or os.getenv("SERVICE_NAME", "")).lower()

app_title = "Collaborative Fraud Intelligence Simulator"
app_description = (
    "Privacy-preserving cross-institution fraud detection using Federated Learning. "
    "Simulates collaborative model training between three independent banks without "
    "sharing raw transaction data. Phase 2 adds collaborative alert intelligence, "
    "risk scoring, case management, entity resolution, and relationship graphs."
)

if service_name == "gateway":
    app_title = "Collaborative Fraud Intelligence Gateway"
    app_description = "API Gateway for proxying requests to downstream microservices and aggregating API documentation."
elif service_name in ("fl-coordinator", "coordinator"):
    app_title = "Federated Learning Coordinator Service"
    app_description = "Handles Federated Learning training simulations, participant bank configurations, and metrics."

elif service_name == "identity-graph":
    app_title = "Identity & Graph Service"
    app_description = "Provides privacy-preserving cross-bank entity resolution and relationship network graph visualization."
elif service_name == "fraud-alert":
    app_title = "Fraud Engine & Alert Service"
    app_description = "Provides risk scoring, fraud alert generation, case management, and real-time streaming scenarios."

app = FastAPI(
    title=app_title,
    description=app_description,
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ──────────────────────────────────────
# Allow all origins and disable credentials to avoid any CORS issues on Vercel preview/production links.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Observability ─────────────────────────────
from app.infrastructure.telemetry import setup_telemetry

setup_telemetry(app)

# ── Global Core Routers ────────────────────────
from app.presentation.routers import onboarding

app.include_router(onboarding.router)


# ── Service Mode Specific Routers ──────────────
if service_name == "gateway":
    from app.presentation.routers import gateway

    app.include_router(health.router)
    app.include_router(gateway.router)

elif service_name in ("fl-coordinator", "coordinator"):
    app.include_router(health.router)
    app.include_router(simulation.router)
    app.include_router(banks.router)
    app.include_router(training.router)
    app.include_router(model_registry.router)
    app.include_router(training_ws.router)
    app.include_router(coordinator.router)
    app.include_router(privacy_defense.router)
    app.include_router(settlement.router)

elif service_name == "identity-graph":
    app.include_router(health.router)
    app.include_router(entities.router)
    app.include_router(graph.router)

elif service_name == "fraud-alert":
    app.include_router(health.router)
    app.include_router(alerts.router)
    app.include_router(cases.router)
    app.include_router(predict.router)
    app.include_router(rules.router)
    app.include_router(
        entities.router
    )  # Mounted for read access of entities within streaming engine if queried directly
    app.include_router(
        graph.router
    )  # Mounted for read access of graph within streaming engine if queried directly
    app.include_router(scenarios.router)
    app.include_router(dashboard.router)
    app.include_router(streaming_ws.router)

elif service_name.startswith("bank-") or service_name == "bank_client":
    app.include_router(health.router)
    app.include_router(bank_client.router)
else:
    from app.presentation.routers import onboarding

    app.include_router(health.router)
    app.include_router(maintenance_cron.router)
    app.include_router(onboarding.router)
    app.include_router(simulation.router)

    app.include_router(banks.router)
    app.include_router(training.router)
    app.include_router(model_registry.router)
    app.include_router(training_ws.router)
    app.include_router(alerts.router)
    app.include_router(cases.router)
    app.include_router(predict.router)
    app.include_router(rules.router)
    app.include_router(bank_client.router)
    app.include_router(entities.router)
    app.include_router(graph.router)
    app.include_router(scenarios.router)
    app.include_router(dashboard.router)
    app.include_router(streaming_ws.router)
    app.include_router(psd2.router)
    app.include_router(security.router)
    app.include_router(monitoring.router)
    app.include_router(coordinator.router)
    app.include_router(privacy_defense.router)
    app.include_router(settlement.router)
    app.include_router(realtime_inference.router)
    app.include_router(compliance.router)


@app.get("/", tags=["root"])
async def root() -> dict:
    """API root — returns basic service info."""
    return {
        "service": app_title,
        "version": "0.2.0",
        "docs": "/docs",
        "health": "/health",
        "service_name": service_name or "monolith",
    }
