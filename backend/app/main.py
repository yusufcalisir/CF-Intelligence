# ruff: noqa: E402
from __future__ import annotations

import logging
import os
import pathlib
import tempfile
import time
import uuid
from threading import Lock

# Configure CPU threading limits to 2 cores for maximum performance
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["VECLIB_MAXIMUM_THREADS"] = "2"
os.environ["NUMEXPR_NUM_THREADS"] = "2"
os.environ["GIT_PYTHON_REFRESH"] = "quiet"
os.environ["DISABLE_PANDERA_IMPORT_WARNING"] = "True"
os.environ["TQDM_DISABLE"] = "1"

print(">>> Python main.py loaded successfully! <<<", flush=True)

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

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
    optimization,
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

    from starlette.responses import Response

# ── Logging ───────────────────────────────────
settings = get_settings()

# ── Structured JSON Logging ────────────────────────────────────────────────────
# Uses python-json-logger for machine-parseable log output compatible with
# ELK / Datadog / Cloud Logging ingestion pipelines without regex parsing.
try:
    from pythonjsonlogger import jsonlogger  # type: ignore[import-untyped]

    _json_handler = logging.StreamHandler()
    _json_formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    _json_handler.setFormatter(_json_formatter)
    logging.root.setLevel(getattr(logging, settings.app_log_level.upper(), logging.INFO))
    logging.root.handlers = [_json_handler]
except ImportError:
    # Graceful fallback if python-json-logger is not yet installed
    logging.basicConfig(
        level=getattr(logging, settings.app_log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
logger = logging.getLogger(__name__)

# ── Silence noisy third-party loggers ─────────────────────────────────────────
# great_expectations, alembic, and ray internal logging
for _noisy_logger_name in (
    "great_expectations",
    "great_expectations._docs_decorators",
    "great_expectations.expectations.registry",
    "great_expectations.data_context.types.base",
    "alembic.runtime.plugins",
):
    logging.getLogger(_noisy_logger_name).setLevel(logging.WARNING)


# ── Tenant-Isolated Logging ──────────────────
def _setup_tenant_logging() -> None:
    """Add per-tenant file handlers that route logs to isolated files.

    Each bank's logs are written to ``storage/logs/{bank_id}.log``.
    System/coordinator logs go to ``storage/logs/system.log``.
    """
    import os
    import tempfile

    from app.infrastructure.database import active_tenant

    env_dir = os.environ.get("CFI_STORAGE_DIR")
    if env_dir:
        logs_dir = os.path.abspath(os.path.join(env_dir, "logs"))
    else:
        logs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "storage", "logs"))

    try:
        os.makedirs(logs_dir, exist_ok=True)
        test_file = os.path.join(logs_dir, ".write_test")
        with open(test_file, "w") as _f:
            pass
        os.remove(test_file)
    except OSError:
        logs_dir = os.path.join(tempfile.gettempdir(), "cfi_storage", "logs")  # nosec B108
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


def _seed_sentinel_path() -> pathlib.Path:
    """Return the path of the one-time seed sentinel file."""
    storage = os.environ.get("CFI_STORAGE_DIR", tempfile.gettempdir())
    return pathlib.Path(storage) / "cfi_seeded.sentinel"


def _acquire_seed_right() -> bool:
    """Return True if this process should run seed_mock_data().

    Uses an atomic file creation (exclusive, fails if exists) as a
    cross-worker lock inside the same container.  Works reliably on
    POSIX filesystems (Linux / HF Spaces /tmp).
    """
    sentinel = _seed_sentinel_path()
    try:
        sentinel.touch(exist_ok=False)  # atomic O_CREAT | O_EXCL
        return True
    except FileExistsError:
        return False


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
            {"feature": "velocity", "contribution": 0.92},
            {"feature": "new_device", "contribution": 1.0},
            {"feature": "high_risk_merchant", "contribution": 0.74},
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
        top_features=[
            {"feature": "amount", "contribution": 0.78},
            {"feature": "country_mismatch", "contribution": 0.45},
        ],
        risk_factors=["Transaction amount significantly exceeds customer historical average"],
    )
    alert_svc._alert_store.set(a2.id, _alert_to_dict(a2))
    entity_svc.increment_alert_count(c3.id)
    entity_svc.update_risk_level(c3.id, RiskLevel.MEDIUM)

    a3 = Alert(
        bank_id="bank_a",
        transaction_id="tx_77821",
        risk_score=930.0,
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.NEW,
        reason_codes=["ML-HIGH", "GEO-RISK", "CB-HIST"],
        confidence=0.93,
        involved_entity_ids=[c1.id],
        model_confidence=0.93,
        top_features=[
            {"feature": "ml_fraud_score", "contribution": 0.93},
            {"feature": "geo_anomaly", "contribution": 0.81},
            {"feature": "chargeback_history", "contribution": 0.67},
        ],
        risk_factors=[
            "ML model confidence exceeds critical threshold",
            "Transaction originates from high-risk jurisdiction",
            "Customer has prior chargeback history",
        ],
    )
    alert_svc._alert_store.set(a3.id, _alert_to_dict(a3))
    entity_svc.increment_alert_count(c1.id)
    entity_svc.update_risk_level(c1.id, RiskLevel.CRITICAL)

    # 4. Create initial demonstration case
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
    logger.info("Successfully seeded initial demonstration data for local environment")


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

    # Probe Redis availability once at startup (avoids per-connection WARNING spam)
    _redis_available = False
    try:
        import redis.asyncio as _aioredis
        _redis_url = settings.redis_url or "redis://localhost:6379"
        _r = _aioredis.from_url(_redis_url, socket_connect_timeout=1.0)
        await _r.ping()
        await _r.aclose()
        _redis_available = True
        logger.info("Redis: available at %s", _redis_url)
    except Exception as _re:
        logger.info(
            "Redis: not available (%s) — WebSocket will use in-process event bus "
            "(expected degraded mode in HF Spaces / no-Redis deployments)",
            type(_re).__name__,
        )

    # Seed mock data — only the first worker/process to acquire the sentinel runs this
    if _acquire_seed_right():
        try:
            seed_mock_data()
        except Exception as exc:
            logger.error("Failed to seed mock data: %s", exc, exc_info=True)
    else:
        logger.info("Seed skipped — sentinel exists, another worker already seeded")

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


# ── Global Exception Handler (RFC 7807 Compliant) ─────────────────────────────
# Ensures ALL unhandled runtime exceptions return structured JSON (HTTP 500).
# Supports RFC 7807 application/problem+json when requested via Accept header.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    accept = request.headers.get("accept", "")
    media_type = (
        "application/problem+json" if "application/problem+json" in accept else "application/json"
    )

    problem_details = {
        "type": f"https://cfi-platform.org/errors/{type(exc).__name__}",
        "title": "Internal Server Error",
        "status": 500,
        "detail": str(exc) or "An unhandled internal server error occurred",
        "instance": request.url.path,
    }
    return JSONResponse(
        status_code=500,
        content=problem_details,
        media_type=media_type,
    )


# ── Content-Type Enforcement Middleware ───────────────────────────────────────
# POST / PUT / PATCH requests that do not send Content-Type: application/json
# receive HTTP 415 Unsupported Media Type instead of a cryptic HTTP 500.
class ContentTypeMiddleware(BaseHTTPMiddleware):
    """Reject non-JSON bodies on mutating endpoints with HTTP 415."""

    _MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH"})
    # Paths exempt from the check (form-data uploads, WebSocket upgrades, etc.)
    _EXEMPT_PREFIXES = ("/ws/", "/docs", "/redoc", "/openapi.json", "/api/v1/banks/upload")

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if request.method in self._MUTATING_METHODS and not any(
            request.url.path.startswith(p) for p in self._EXEMPT_PREFIXES
        ):
            ct = request.headers.get("content-type", "")
            if ct and not ct.startswith("application/json"):
                return JSONResponse(
                    status_code=415,
                    content={
                        "type": "https://cfi-platform.org/errors/UnsupportedMediaType",
                        "title": "Unsupported Media Type",
                        "status": 415,
                        "detail": "Only 'application/json' bodies are supported for mutating operations.",
                        "received": ct.split(";")[0].strip(),
                        "instance": request.url.path,
                    },
                )
        return await call_next(request)


app.add_middleware(ContentTypeMiddleware)


# ── W3C Distributed Trace Context Middleware ─────────────────────────────────
# Injects W3C compliant traceparent header (00-{trace_id}-{span_id}-01) into all responses
# for cross-service distributed trace propagation per OpenTelemetry standards.
class W3CTraceContextMiddleware(BaseHTTPMiddleware):
    """Extract or generate W3C traceparent header and propagate to HTTP response headers."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        incoming_tp = request.headers.get("traceparent")
        if incoming_tp and incoming_tp.startswith("00-") and len(incoming_tp.split("-")) == 4:
            traceparent = incoming_tp
        else:
            trace_id = uuid.uuid4().hex
            span_id = uuid.uuid4().hex[:16]
            traceparent = f"00-{trace_id}-{span_id}-01"

        response = await call_next(request)
        response.headers["traceparent"] = traceparent
        return response


app.add_middleware(W3CTraceContextMiddleware)


# ── API Version Lifecycle Headers Middleware ──────────────────────────────────
# Adds RFC 8594 Deprecation and Sunset headers to all responses so that clients
# and gateways can handle version lifecycle transitions programmatically.
class APIVersionLifecycleMiddleware(BaseHTTPMiddleware):
    """Attach RFC 8594 Deprecation / Sunset headers to every API response."""

    # Update these dates when planning a version deprecation cycle.
    _DEPRECATION_DATE: str | None = None  # e.g. "Sat, 01 Jan 2026 00:00:00 GMT"
    _SUNSET_DATE: str | None = None  # e.g. "Sat, 01 Jul 2026 00:00:00 GMT"
    _API_VERSION = "v1"

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        response = await call_next(request)
        response.headers["X-API-Version"] = self._API_VERSION
        if self._DEPRECATION_DATE:
            response.headers["Deprecation"] = self._DEPRECATION_DATE
        if self._SUNSET_DATE:
            response.headers["Sunset"] = self._SUNSET_DATE
        return response


app.add_middleware(APIVersionLifecycleMiddleware)


# ── In-App mTLS Peer Verification Middleware ─────────────────────────────────
# Enforces mutual TLS peer certificate verification at the application layer,
# checking client certificate SHA-256 fingerprints and CRL revocation status.
class MTLSVerificationMiddleware(BaseHTTPMiddleware):
    """Enforce in-app mTLS peer certificate validation on sensitive routes."""

    _ENFORCED_PREFIXES = ("/api/v1/predict", "/api/v1/training", "/api/v1/banks")

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if settings.mtls_enabled and any(
            request.url.path.startswith(p) for p in self._ENFORCED_PREFIXES
        ):
            cert_verify = request.headers.get("x-ssl-client-verify", "").upper()
            cert_hash = request.headers.get("x-client-cert-sha256", "")

            if cert_verify and cert_verify != "SUCCESS":
                return JSONResponse(
                    status_code=403,
                    content={
                        "type": "https://cfi-platform.org/errors/mTLSVerificationFailed",
                        "title": "mTLS Handshake Verification Failed",
                        "status": 403,
                        "detail": f"Client certificate verification status: '{cert_verify}'",
                        "instance": request.url.path,
                    },
                    media_type="application/problem+json",
                )

            from app.infrastructure.security.mtls_manager import MTLSManager

            mtls_mgr = MTLSManager()
            if cert_hash and cert_hash in mtls_mgr.crl_revoked_serials:
                return JSONResponse(
                    status_code=403,
                    content={
                        "type": "https://cfi-platform.org/errors/mTLSCertificateRevoked",
                        "title": "mTLS Certificate Revoked",
                        "status": 403,
                        "detail": f"Client certificate SHA-256 fingerprint '{cert_hash}' is revoked in CRL.",
                        "instance": request.url.path,
                    },
                    media_type="application/problem+json",
                )

        return await call_next(request)


app.add_middleware(MTLSVerificationMiddleware)


# ── Application-Layer DDoS & Volumetric Flood Protection Middleware ──────────
# Implements sliding-window token bucket rate limiting for burst attack detection
# and volumetric request flood prevention at the L7 application layer.
class DDoSProtectionMiddleware(BaseHTTPMiddleware):
    """Enforce sliding-window L7 volumetric flood protection per client IP."""

    _WINDOW_SECONDS = 10.0
    _MAX_REQUESTS_PER_WINDOW = 100
    _requests: dict[str, list[float]] = {}
    _lock = Lock()

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        cutoff = now - self._WINDOW_SECONDS

        with self._lock:
            history = [t for t in self._requests.get(client_ip, []) if t > cutoff]
            if len(history) >= self._MAX_REQUESTS_PER_WINDOW:
                self._requests[client_ip] = history
                logger.warning(
                    "DDoS Volumetric Throttling triggered for IP %s (%d reqs in %.1fs)",
                    client_ip,
                    len(history),
                    self._WINDOW_SECONDS,
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "type": "https://cfi-platform.org/errors/DDoSThrottled",
                        "title": "Volumetric Flood Throttling Triggered",
                        "status": 429,
                        "detail": f"Request burst limit exceeded ({self._MAX_REQUESTS_PER_WINDOW} reqs/{int(self._WINDOW_SECONDS)}s). Temporarily throttled.",
                        "instance": request.url.path,
                    },
                    headers={"Retry-After": "10", "X-DDoS-Throttled": "true"},
                    media_type="application/problem+json",
                )
            history.append(now)
            self._requests[client_ip] = history

        return await call_next(request)


app.add_middleware(DDoSProtectionMiddleware)


# ── Observability ─────────────────────────────
from app.infrastructure.telemetry import setup_telemetry

setup_telemetry(app)

# ── Global Core Routers ────────────────────────
from app.presentation.routers import design_partner, onboarding

app.include_router(onboarding.router)
app.include_router(design_partner.router)


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
    app.include_router(design_partner.router)

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
    app.include_router(optimization.router)
    app.include_router(design_partner.router)


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
