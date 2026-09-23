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
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.infrastructure.security.error_handler import format_safe_error_response
from app.infrastructure.security.security_headers import SecurityHeadersMiddleware
from app.presentation.routers import (
    admin_console,
    alerts,
    asset_recovery,
    auth,
    bank_client,
    banks,
    bridge_messaging,
    cases,
    compliance,
    coordinator,
    core_banking_gateway,
    dashboard,
    design_partner,
    diagnostics,
    entities,
    european_scenarios,
    financial_messages,
    gateway,
    graph,
    health,
    maintenance_cron,
    model_registry,
    monitoring,
    onboarding,
    open_aml_adapter,
    optimization,
    payment_recall,
    predict,
    privacy_defense,
    psd2,
    realtime_inference,
    regulatory,
    regulatory_dossier,
    rules,
    scenarios,
    screening,
    security,
    settlement,
    simulation,
    training,
    ubo_graph,
    webhook_gateway,
)
from app.presentation.websockets import streaming_ws, training_ws

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# ── Logging ───────────────────────────────────

settings = get_settings()

# ── Structured JSON Logging ────────────────────────────────────────────────────
# Uses python-json-logger for machine-parseable log output compatible with
# ELK / Datadog / Cloud Logging ingestion pipelines without regex parsing.
try:
    import importlib

    try:
        _json_module = importlib.import_module("pythonjsonlogger.json")
    except ImportError:
        _json_module = importlib.import_module("pythonjsonlogger.jsonlogger")

    _JsonFormatter = _json_module.JsonFormatter

    _json_handler = logging.StreamHandler()
    _json_formatter = _JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    _json_handler.setFormatter(_json_formatter)
    logging.root.setLevel(getattr(logging, settings.app_log_level.upper(), logging.INFO))
    logging.root.handlers = [_json_handler]
except (ImportError, AttributeError):
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
    from app.infrastructure.storage.storage_utils import get_storage_dir

    env_dir = os.environ.get("CFI_STORAGE_DIR")
    if env_dir:
        logs_dir = os.path.abspath(os.path.join(env_dir, "logs"))
    else:
        logs_dir = os.path.abspath(os.path.join(get_storage_dir(), "logs"))

    try:
        os.makedirs(logs_dir, exist_ok=True)
        test_file = os.path.join(logs_dir, ".write_test")
        with open(test_file, "w") as _f:
            pass
        os.remove(test_file)
    except OSError:
        logs_dir = os.path.join(tempfile.gettempdir(), "cfi_storage", "logs")
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

    # 4. Create initial demonstration cases
    case = case_svc.create_case(
        title="High-Risk Activity: Device Sharing & Crypto Outflow",
        priority=CasePriority.P2_HIGH,
        alert_ids=[a1.id],
    )
    case.assigned_to = "senior_analyst_1"
    case.status = CaseStatus.INVESTIGATING
    case_svc._cases.set(case.id, _case_to_dict(case))

    # Seed canonical demo case CASE-98492 for FinCEN BSA SAR XML export
    case_98492 = case_svc.create_case(
        title="Structuring Pattern Detected: Smurfing Indicators",
        priority=CasePriority.P1_CRITICAL,
        alert_ids=[a1.id, a3.id],
        total_risk_score=940.0,
    )
    case_98492.id = "CASE-98492"
    case_98492.assigned_to = "senior_analyst_1"
    case_98492.status = CaseStatus.INVESTIGATING
    case_98492.supervisor_signatures = ["SIG_SUPERVISOR_ALICE_9941", "SIG_SUPERVISOR_BOB_8820"]
    case_svc._cases.set("CASE-98492", _case_to_dict(case_98492))

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
    if service_name.startswith("bank-") or service_name == "bank_client":
        try:
            from app.presentation.messaging.redis_listener import RedisBankClientListener

            redis_url = settings.redis_url
            if redis_url:
                target_id = service_name if service_name.startswith("bank-") else "bank_a"
                listener = RedisBankClientListener(redis_url=redis_url, bank_id=target_id)
                await listener.start()
                redis_listeners.append(listener)
        except Exception as exc:
            logger.error("Failed to start Redis Bank Client Listener: %s", exc)
    elif not service_name or service_name == "monolith":
        try:
            from app.presentation.messaging.redis_listener import RedisBankClientListener

            redis_url = settings.redis_url
            if redis_url:
                for b_id in ["bank_a", "bank_b", "bank_c"]:
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
    docs_url=None,
    redoc_url=None,
)

# ── Endpoint-Specific Rate Limiting (slowapi) ─────────────────────────────────
from slowapi.errors import RateLimitExceeded

from app.infrastructure.security.rate_limiter import limiter

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Handle slowapi rate limit exceeded exceptions with RFC 7807 problem details."""
    return JSONResponse(
        status_code=429,
        content={
            "type": "https://cfi-platform.org/errors/RateLimitExceeded",
            "title": "Endpoint Rate Limit Exceeded",
            "status": 429,
            "detail": f"Rate limit exceeded: {exc.detail}",
            "instance": request.url.path,
        },
        headers={"Retry-After": "60", "X-RateLimit-Exceeded": "true"},
        media_type="application/problem+json",
    )


# ── CORS (Strict Whitelist — No Wildcards) ────────────────────────────────────
# Wildcard origins are strictly prohibited to prevent cross-origin browser abuse.
# Only authenticated platform frontend domains and verified preview regexes are allowed.
_cors_origins = [
    origin.strip()
    for origin in settings.cors_allowed_origins.split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "X-RateLimit-Exceeded"],
)

# ── HTTP Security Headers ─────────────────────────────────────────────────────
# Injected on every response: CSP, HSTS, X-Frame-Options, nosniff, Referrer-Policy.
# NOTE: Starlette executes middleware in LIFO order (last added = outermost).
# SecurityHeadersMiddleware is added AFTER CORSMiddleware so CORS headers are
# set first and security headers wrap the final outbound response.
app.add_middleware(SecurityHeadersMiddleware)


# ── Global Exception Handler (RFC 7807 Compliant & Production Sanitized) ──────
# Ensures ALL unhandled runtime exceptions return structured JSON (HTTP 500).
# In production: Strips stack traces, internal file paths, and database details.
# Returns generic user message + correlation incident ID while logging full traceback server-side.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return format_safe_error_response(request, exc, status_code=500)


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
    """Enforce sliding-window L7 volumetric flood protection per client IP with bounded memory pruning and hard ceiling eviction."""

    _WINDOW_SECONDS = 10.0
    _MAX_REQUESTS_PER_WINDOW = 100
    _MAX_TRACKED_IPS = 1000  # Threshold to trigger expired IP pruning
    _HARD_CEILING_TRACKED_IPS = 5000  # Hard ceiling: oldest active IPs evicted if active count exceeds ceiling
    _requests: dict[str, list[float]] = {}
    _lock = Lock()

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        # Extract real client IP considering trusted reverse proxy headers (Vercel, Cloudflare, AWS ALB)
        forwarded = request.headers.get("x-forwarded-for")
        client_ip = (
            request.headers.get("cf-connecting-ip")
            or request.headers.get("x-real-ip")
            or (forwarded.split(",")[0].strip() if forwarded else None)
            or (request.client.host if request.client else "unknown")
        )

        # Bypass throttling in test environments only for standard testclient/loopback
        # to prevent cross-test 429 accumulation while allowing real burst testing on explicit IPs.
        import os
        if os.environ.get("TESTING") == "1" and client_ip in ("testclient", "127.0.0.1", "unknown", "localhost"):
            return await call_next(request)
        now = time.time()
        cutoff = now - self._WINDOW_SECONDS

        with self._lock:
            # Periodic pruning of expired entries when dictionary size exceeds threshold
            if len(self._requests) > self._MAX_TRACKED_IPS:
                cleaned: dict[str, list[float]] = {}
                for ip, timestamps in self._requests.items():
                    valid_ts = [t for t in timestamps if t > cutoff]
                    if valid_ts:
                        cleaned[ip] = valid_ts

                # If active entries still exceed hard ceiling, evict oldest-active IPs first (LRU bound)
                if len(cleaned) > self._HARD_CEILING_TRACKED_IPS:
                    sorted_ips = sorted(
                        cleaned.items(),
                        key=lambda item: max(item[1]) if item[1] else 0.0,
                        reverse=True,
                    )
                    cleaned = dict(sorted_ips[: self._HARD_CEILING_TRACKED_IPS])

                self._requests = cleaned

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
                    headers={
                        "Retry-After": str(int(self._WINDOW_SECONDS)),
                        "X-DDoS-Throttled": "true",
                        "X-RateLimit-Limit": str(self._MAX_REQUESTS_PER_WINDOW),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(now + self._WINDOW_SECONDS)),
                    },
                    media_type="application/problem+json",
                )
            history.append(now)
            self._requests[client_ip] = history

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self._MAX_REQUESTS_PER_WINDOW)
        response.headers["X-RateLimit-Remaining"] = str(max(0, self._MAX_REQUESTS_PER_WINDOW - len(history)))
        return response


app.add_middleware(DDoSProtectionMiddleware)


# ── Multi-Tenant Broken Access Control (BOLA/IDOR) Middleware ────────────────
# Enforces tenant boundary isolation at the HTTP middleware layer:
# Prevents a bank user from accessing another bank's data simply by tampering
# with bank_id parameters in the URL, query string, or request context.
class TenantAccessControlMiddleware(BaseHTTPMiddleware):
    """Enforces multi-tenant broken access control (BOLA/IDOR) prevention across all routes."""

    _EXEMPT_PREFIXES = (
        "/docs",
        "/redoc",
        "/openapi.json",
        "/health",
        "/api/v1/health",
        "/v1/health",
        "/metrics",
        "/ws/",
        "/api/v1/onboarding",
    )

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if any(request.url.path.startswith(p) for p in self._EXEMPT_PREFIXES):
            return await call_next(request)

        # 1. Extract caller tenant identity and roles from OIDC JWT, X-Tenant-ID, X-Bank-ID, or X-API-Key
        caller_tenant: str | None = None
        caller_roles: list[str] = []

        auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
            try:
                from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator

                auth_helper = OIDCAuthenticator()
                valid, claims, _ = auth_helper.decode_and_validate_token(token)
                if valid and claims:
                    caller_tenant = claims.bank_id
                    caller_roles = claims.roles
            except Exception:
                pass

        if not caller_tenant:
            caller_tenant = (
                request.headers.get("X-Tenant-ID")
                or request.headers.get("x-tenant-id")
                or request.headers.get("X-Bank-ID")
                or request.headers.get("x-bank-id")
            )

        if not caller_tenant:
            api_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key") or ""
            if api_key and ":" in api_key:
                parts = api_key.split(":")
                if len(parts) >= 2:
                    caller_tenant = parts[1]

        # 2. If caller tenant is bound, verify against target bank_id query param
        if caller_tenant and not any(
            r in caller_roles for r in ("super_admin", "cross_bank_investigator", "compliance_auditor")
        ):
            norm_caller = caller_tenant.lower().replace("-", "_").strip()

            target_bank = request.query_params.get("bank_id")
            if target_bank:
                norm_target = target_bank.lower().replace("-", "_").strip()
                if (
                    norm_target not in ("global", "all", "system", "coordinator")
                    and norm_target != norm_caller
                ):
                    logger.warning(
                        "BOLA Access Denied: Tenant '%s' attempted cross-tenant access to bank '%s' on %s",
                        caller_tenant,
                        target_bank,
                        request.url.path,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "type": "https://cfi-platform.org/errors/TenantAccessDenied",
                            "title": "Cross-Tenant Broken Access Control Forbidden",
                            "status": 403,
                            "detail": f"Tenant '{caller_tenant}' is not authorized to access resources belonging to bank '{target_bank}'.",
                            "instance": request.url.path,
                        },
                        media_type="application/problem+json",
                    )

        return await call_next(request)


app.add_middleware(TenantAccessControlMiddleware)



# ── Observability ─────────────────────────────
from app.infrastructure.telemetry import setup_telemetry

setup_telemetry(app)

# ── Global Core Routers ────────────────────────
from app.presentation.routers import copilot, datasets, feedback

app.include_router(auth.router)
app.include_router(onboarding.router)
app.include_router(onboarding.api_router)
app.include_router(design_partner.router)
app.include_router(design_partner.api_router)
app.include_router(diagnostics.router)
app.include_router(diagnostics.api_router)
app.include_router(datasets.router)
app.include_router(datasets.api_router)
app.include_router(copilot.router)
app.include_router(copilot.api_router)
app.include_router(feedback.router)
app.include_router(feedback.api_router)



# ── Service Mode Specific Routers ──────────────
if service_name == "gateway":
    from app.presentation.routers import gateway

    app.include_router(health.router)
    app.include_router(health.api_router)
    app.include_router(health.v1_router)
    app.include_router(gateway.router)

elif service_name in ("fl-coordinator", "coordinator"):
    app.include_router(health.router)
    app.include_router(health.api_router)
    app.include_router(health.v1_router)
    app.include_router(simulation.router)
    app.include_router(simulation.api_router)
    app.include_router(simulation.singular_router)
    app.include_router(simulation.singular_api_router)
    app.include_router(banks.router)
    app.include_router(banks.api_router)
    app.include_router(training.router)
    app.include_router(training.api_router)
    app.include_router(model_registry.router)
    app.include_router(model_registry.api_router)
    app.include_router(model_registry.models_router)
    app.include_router(model_registry.models_api_router)
    app.include_router(training_ws.router)
    app.include_router(coordinator.router)
    app.include_router(coordinator.api_router)
    app.include_router(privacy_defense.router)
    app.include_router(settlement.router)
    app.include_router(settlement.api_router)
    app.include_router(optimization.router)
    app.include_router(optimization.api_router)
    app.include_router(optimization.admin_router)
    app.include_router(optimization.admin_api_router)
    app.include_router(admin_console.router)
    app.include_router(admin_console.api_router)
    app.include_router(admin_console.admin_router)
    app.include_router(admin_console.admin_v1_router)
    app.include_router(maintenance_cron.router)
    app.include_router(maintenance_cron.api_router)

elif service_name == "identity-graph":
    app.include_router(health.router)
    app.include_router(health.api_router)
    app.include_router(health.v1_router)
    app.include_router(entities.router)
    app.include_router(entities.api_router)
    app.include_router(entities.psi_router)
    app.include_router(entities.psi_api_router)
    app.include_router(graph.router)
    app.include_router(graph.api_router)

elif service_name == "fraud-alert":
    app.include_router(health.router)
    app.include_router(health.api_router)
    app.include_router(health.v1_router)
    app.include_router(alerts.router)
    app.include_router(alerts.api_router)
    app.include_router(cases.router)
    app.include_router(cases.api_router)
    app.include_router(predict.router)
    app.include_router(rules.router)
    app.include_router(rules.api_router)
    app.include_router(
        entities.router
    )  # Mounted for read access of entities within streaming engine if queried directly
    app.include_router(
        entities.api_router
    )
    app.include_router(
        graph.router
    )  # Mounted for read access of graph within streaming engine if queried directly
    app.include_router(
        graph.api_router
    )
    app.include_router(scenarios.router)
    app.include_router(dashboard.router)
    app.include_router(streaming_ws.router)

elif service_name.startswith("bank-") or service_name == "bank_client":
    app.include_router(health.router)
    app.include_router(health.api_router)
    app.include_router(health.v1_router)
    app.include_router(bank_client.router)
    app.include_router(bank_client.api_router)
else:
    app.include_router(health.router)
    app.include_router(health.api_router)
    app.include_router(health.v1_router)
    app.include_router(maintenance_cron.router)
    app.include_router(maintenance_cron.api_router)
    app.include_router(admin_console.router)
    app.include_router(admin_console.api_router)
    app.include_router(admin_console.admin_router)
    app.include_router(admin_console.admin_v1_router)
    app.include_router(simulation.router)
    app.include_router(simulation.api_router)
    app.include_router(simulation.singular_router)
    app.include_router(simulation.singular_api_router)

    app.include_router(banks.router)
    app.include_router(banks.api_router)
    app.include_router(training.router)
    app.include_router(training.api_router)
    app.include_router(model_registry.router)
    app.include_router(model_registry.api_router)
    app.include_router(model_registry.models_router)
    app.include_router(model_registry.models_api_router)
    app.include_router(training_ws.router)
    app.include_router(streaming_ws.router)
    app.include_router(alerts.router)
    app.include_router(alerts.api_router)
    app.include_router(cases.router)
    app.include_router(cases.api_router)
    app.include_router(predict.router)
    app.include_router(rules.router)
    app.include_router(rules.api_router)
    app.include_router(bank_client.router)
    app.include_router(bank_client.api_router)
    app.include_router(entities.router)
    app.include_router(entities.api_router)
    app.include_router(entities.psi_router)
    app.include_router(entities.psi_api_router)
    app.include_router(graph.router)
    app.include_router(graph.api_router)
    app.include_router(scenarios.router)
    app.include_router(dashboard.router)
    app.include_router(psd2.router)
    app.include_router(psd2.api_router)
    app.include_router(security.router)
    app.include_router(security.api_router)
    app.include_router(monitoring.router)
    app.include_router(monitoring.api_router)
    app.include_router(coordinator.router)
    app.include_router(coordinator.api_router)
    app.include_router(privacy_defense.router)
    app.include_router(privacy_defense.api_router)
    app.include_router(settlement.router)
    app.include_router(settlement.api_router)
    app.include_router(realtime_inference.router)
    app.include_router(realtime_inference.api_router)
    app.include_router(compliance.router)
    app.include_router(compliance.api_router)
    app.include_router(optimization.router)
    app.include_router(optimization.api_router)
    app.include_router(optimization.admin_router)
    app.include_router(optimization.admin_api_router)
    app.include_router(webhook_gateway.router)
    app.include_router(webhook_gateway.api_router)
    app.include_router(gateway.api_router)
    app.include_router(bridge_messaging.router)
    app.include_router(bridge_messaging.api_router)
    app.include_router(payment_recall.router)
    app.include_router(payment_recall.api_router)
    app.include_router(screening.router)
    app.include_router(screening.api_router)
    app.include_router(regulatory.router)
    app.include_router(regulatory.api_router)
    app.include_router(open_aml_adapter.router)
    app.include_router(open_aml_adapter.api_router)
    app.include_router(ubo_graph.router)
    app.include_router(ubo_graph.api_router)
    app.include_router(european_scenarios.router)
    app.include_router(european_scenarios.api_router)
    app.include_router(asset_recovery.router)
    app.include_router(asset_recovery.api_router)
    app.include_router(core_banking_gateway.router)
    app.include_router(core_banking_gateway.api_router)
    app.include_router(financial_messages.router)
    app.include_router(financial_messages.api_router)
    app.include_router(regulatory_dossier.router)
    app.include_router(regulatory_dossier.api_router)


@app.get("/", tags=["root"])
async def root() -> dict:
    """API root — returns basic service info."""
    return {
        "service": app_title,
        "version": "0.2.0",
        "docs": "/docs",
        "redoc": "/redoc",
        "scalar": "/scalar",
        "health": "/health",
        "service_name": service_name or "monolith",
    }


_CFI_DOCS_TOPBAR_HTML = """
  <div class="cfi-topbar">
    <a href="/" class="cfi-brand">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#818cf8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
      </svg>
      <span style="font-weight: 700; font-size: 14px; letter-spacing: -0.01em;">CF-Intelligence</span>
      <span class="cfi-badge">OpenAPI 3.1</span>
    </a>
    <div class="cfi-nav-links">
      <a href="/docs" class="cfi-nav-link {docs_active}">Swagger UI</a>
      <a href="/redoc" class="cfi-nav-link {redoc_active}">ReDoc</a>
      <a href="/scalar" class="cfi-nav-link {scalar_active}">Scalar</a>
      <a href="/openapi.json" target="_blank" class="cfi-nav-link">openapi.json ↗</a>
    </div>
  </div>
"""

_CFI_DOCS_NAV_CSS = """
    /* Branded Top Navigation Bar */
    .cfi-topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 24px;
      background: rgba(11, 15, 25, 0.95);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      position: sticky;
      top: 0;
      z-index: 1000;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    .cfi-brand {
      display: flex;
      align-items: center;
      gap: 10px;
      text-decoration: none;
      color: #fff;
    }
    .cfi-badge {
      font-size: 10px;
      font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
      padding: 2px 8px;
      border-radius: 9999px;
      background: rgba(99, 102, 241, 0.15);
      color: #a5b4fc;
      border: 1px solid rgba(99, 102, 241, 0.3);
    }
    .cfi-nav-links {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .cfi-nav-link {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 600;
      text-decoration: none;
      transition: all 0.15s ease-in-out;
    }
    .cfi-nav-link.active {
      background: rgba(99, 102, 241, 0.2);
      color: #e0e7ff;
      border: 1px solid rgba(99, 102, 241, 0.4);
    }
    .cfi-nav-link:not(.active) {
      background: rgba(255, 255, 255, 0.05);
      color: #94a3b8;
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .cfi-nav-link:not(.active):hover {
      background: rgba(255, 255, 255, 0.1);
      color: #f8fafc;
    }
"""


@app.get("/docs", include_in_schema=False, response_class=HTMLResponse)
async def swagger_ui_html() -> HTMLResponse:
    """Serve customized dark-mode Swagger UI documentation."""
    from app.infrastructure.security.security_headers import _DOCS_CSP_DIRECTIVES

    topbar = _CFI_DOCS_TOPBAR_HTML.replace("{docs_active}", "active").replace("{redoc_active}", "").replace("{scalar_active}", "")
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>CF-Intelligence | Swagger UI</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" type="text/css" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  <style>
    :root {{
      --bg-primary: #030712;
      --bg-surface: #0b0f19;
      --bg-card: #0f172a;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --border-color: rgba(255, 255, 255, 0.08);
      --accent-indigo: #6366f1;
      --accent-emerald: #10b981;
    }}
    body {{
      margin: 0;
      padding: 0;
      background-color: var(--bg-primary);
      color: var(--text-main);
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }}
    ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
    ::-webkit-scrollbar-track {{ background: var(--bg-primary); }}
    ::-webkit-scrollbar-thumb {{ background: #1e293b; border-radius: 4px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: #334155; }}

    {_CFI_DOCS_NAV_CSS}

    /* Swagger UI Dark Mode Overrides */
    .swagger-ui {{
      color: var(--text-main);
      font-family: 'Inter', sans-serif;
    }}
    .swagger-ui .topbar {{ display: none !important; }}
    .swagger-ui .wrapper {{ max-width: 1300px; padding: 24px; }}
    .swagger-ui .info {{ margin: 20px 0; }}
    .swagger-ui .info .title {{
      color: #f8fafc !important;
      font-family: 'Inter', sans-serif;
      font-weight: 800;
      font-size: 28px;
      letter-spacing: -0.02em;
    }}
    .swagger-ui .info p, .swagger-ui .info li {{
      color: var(--text-muted) !important;
      font-size: 14px;
      line-height: 1.6;
    }}
    .swagger-ui .scheme-container {{
      background: var(--bg-surface) !important;
      box-shadow: none !important;
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 16px 20px !important;
      margin-bottom: 24px;
    }}
    .swagger-ui .opblock-tag {{
      color: #f8fafc !important;
      font-family: 'Inter', sans-serif;
      border-bottom: 1px solid var(--border-color) !important;
      font-weight: 700;
      font-size: 18px;
      padding: 16px 0 8px 0;
    }}
    .swagger-ui .opblock-tag small {{ color: var(--text-muted) !important; font-size: 13px; }}
    .swagger-ui .opblock {{
      background: var(--bg-card) !important;
      border-radius: 12px !important;
      border: 1px solid var(--border-color) !important;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3) !important;
      margin: 0 0 12px 0 !important;
      transition: border-color 0.15s ease;
    }}
    .swagger-ui .opblock:hover {{
      border-color: rgba(99, 102, 241, 0.35) !important;
    }}
    .swagger-ui .opblock .opblock-summary {{
      border-bottom: 1px solid transparent;
      padding: 10px 16px;
    }}
    .swagger-ui .opblock.is-open .opblock-summary {{
      border-bottom: 1px solid var(--border-color);
    }}
    .swagger-ui .opblock .opblock-summary-path,
    .swagger-ui .opblock .opblock-summary-path__deprecated {{
      color: #f1f5f9 !important;
      font-family: 'JetBrains Mono', monospace !important;
      font-size: 13px !important;
      font-weight: 600 !important;
    }}
    .swagger-ui .opblock .opblock-summary-description {{
      color: var(--text-muted) !important;
      font-size: 12px !important;
    }}
    .swagger-ui .opblock .opblock-summary-method {{
      border-radius: 8px !important;
      font-family: 'JetBrains Mono', monospace !important;
      font-weight: 700 !important;
      font-size: 12px !important;
      padding: 4px 12px !important;
    }}
    .swagger-ui .opblock.opblock-get {{ border-color: rgba(56, 189, 248, 0.3) !important; background: rgba(56, 189, 248, 0.04) !important; }}
    .swagger-ui .opblock.opblock-get .opblock-summary-method {{ background: #0284c7 !important; }}
    .swagger-ui .opblock.opblock-post {{ border-color: rgba(52, 211, 153, 0.3) !important; background: rgba(52, 211, 153, 0.04) !important; }}
    .swagger-ui .opblock.opblock-post .opblock-summary-method {{ background: #059669 !important; }}
    .swagger-ui .opblock.opblock-put {{ border-color: rgba(251, 191, 36, 0.3) !important; background: rgba(251, 191, 36, 0.04) !important; }}
    .swagger-ui .opblock.opblock-put .opblock-summary-method {{ background: #d97706 !important; }}
    .swagger-ui .opblock.opblock-delete {{ border-color: rgba(248, 113, 113, 0.3) !important; background: rgba(248, 113, 113, 0.04) !important; }}
    .swagger-ui .opblock.opblock-delete .opblock-summary-method {{ background: #dc2626 !important; }}

    .swagger-ui .opblock-body {{ background: var(--bg-surface) !important; padding: 16px !important; }}
    .swagger-ui .opblock-description-wrapper p, .swagger-ui .opblock-external-docs-wrapper p, .swagger-ui .opblock-title_normal p {{
      color: var(--text-muted) !important;
    }}
    .swagger-ui table thead tr th, .swagger-ui table thead tr td {{
      color: #f1f5f9 !important;
      border-bottom: 1px solid var(--border-color) !important;
      font-size: 12px !important;
      font-weight: 600 !important;
    }}
    .swagger-ui .parameter__name {{ color: #e2e8f0 !important; font-family: 'JetBrains Mono', monospace; }}
    .swagger-ui .parameter__type {{ color: #818cf8 !important; font-family: 'JetBrains Mono', monospace; }}
    .swagger-ui input[type=text], .swagger-ui textarea {{
      background: #020617 !important;
      color: #f8fafc !important;
      border: 1px solid rgba(255, 255, 255, 0.15) !important;
      border-radius: 8px !important;
      font-family: 'JetBrains Mono', monospace !important;
    }}
    .swagger-ui select {{
      background: #0f172a !important;
      color: #f8fafc !important;
      border: 1px solid rgba(255, 255, 255, 0.15) !important;
      border-radius: 8px !important;
    }}
    .swagger-ui .btn {{
      border-radius: 8px !important;
      font-weight: 600 !important;
      font-size: 12px !important;
      transition: all 0.15s ease;
    }}
    .swagger-ui .btn.execute {{
      background: #10b981 !important;
      border-color: #10b981 !important;
      color: #fff !important;
      font-weight: 700 !important;
    }}
    .swagger-ui .btn.authorize {{
      border-color: #6366f1 !important;
      color: #818cf8 !important;
    }}
    .swagger-ui .btn.authorize svg {{ fill: #818cf8 !important; }}
    .swagger-ui .responses-inner h4, .swagger-ui .responses-inner h5 {{ color: #f8fafc !important; }}
    .swagger-ui .response-col_status {{ color: #34d399 !important; font-family: 'JetBrains Mono', monospace; }}
    .swagger-ui .response-col_description {{ color: var(--text-muted) !important; }}
    .swagger-ui pre.microlight, .swagger-ui .highlight-code {{
      background: #020617 !important;
      border: 1px solid var(--border-color) !important;
      border-radius: 8px !important;
      color: #f1f5f9 !important;
      font-family: 'JetBrains Mono', monospace !important;
    }}
    .swagger-ui section.models {{
      border: 1px solid var(--border-color) !important;
      background: var(--bg-surface) !important;
      border-radius: 12px !important;
    }}
    .swagger-ui section.models h4 {{ color: #f8fafc !important; }}
    .swagger-ui .model-box {{ background: var(--bg-card) !important; }}
    .swagger-ui .model {{ color: #cbd5e1 !important; font-family: 'JetBrains Mono', monospace; }}
    .swagger-ui .model-title {{ color: #f1f5f9 !important; }}
  </style>
</head>
<body>
  {topbar}
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.onload = () => {{
      window.ui = SwaggerUIBundle({{
        url: '/openapi.json',
        dom_id: '#swagger-ui',
        deepLinking: true,
        presets: [
          SwaggerUIBundle.presets.apis,
          SwaggerUIBundle.SwaggerUIStandalonePreset
        ],
        layout: "BaseLayout",
        persistAuthorization: true,
        displayRequestDuration: true,
        filter: true,
        tryItOutEnabled: true,
        syntaxHighlight: {{
          theme: "monokai"
        }}
      }});
    }};
  </script>
</body>
</html>"""
    return HTMLResponse(
        content=html_content,
        headers={"Content-Security-Policy": _DOCS_CSP_DIRECTIVES},
    )


@app.get("/redoc", include_in_schema=False, response_class=HTMLResponse)
async def redoc_html() -> HTMLResponse:
    """Serve customized dark-mode ReDoc technical documentation."""
    from app.infrastructure.security.security_headers import _DOCS_CSP_DIRECTIVES

    topbar = _CFI_DOCS_TOPBAR_HTML.replace("{docs_active}", "").replace("{redoc_active}", "active").replace("{scalar_active}", "")
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>CF-Intelligence | ReDoc Technical Reference</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    body {{
      margin: 0;
      padding: 0;
      background-color: #030712;
      color: #f8fafc;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }}
    ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
    ::-webkit-scrollbar-track {{ background: #030712; }}
    ::-webkit-scrollbar-thumb {{ background: #1e293b; border-radius: 4px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: #334155; }}

    {_CFI_DOCS_NAV_CSS}
  </style>
</head>
<body>
  {topbar}
  <div id="redoc-container"></div>
  <script src="https://cdn.jsdelivr.net/npm/redoc@latest/bundles/redoc.standalone.js"></script>
  <script>
    Redoc.init('/openapi.json', {{
      theme: {{
        colors: {{
          primary: {{ main: '#6366f1' }},
          success: {{ main: '#10b981' }},
          warning: {{ main: '#f59e0b' }},
          error: {{ main: '#ef4444' }},
          text: {{ primary: '#f8fafc', secondary: '#94a3b8' }},
          http: {{
            get: '#38bdf8',
            post: '#34d399',
            put: '#fbbf24',
            delete: '#f87171'
          }}
        }},
        sidebar: {{
          backgroundColor: '#0b0f19',
          textColor: '#e2e8f0',
          activeTextColor: '#818cf8'
        }},
        rightPanel: {{
          backgroundColor: '#030712',
          textColor: '#f8fafc'
        }},
        typography: {{
          fontSize: '14px',
          fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, sans-serif',
          headings: {{
            fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, sans-serif',
            fontWeight: '700'
          }},
          code: {{
            fontFamily: 'JetBrains Mono, monospace',
            backgroundColor: '#0f172a'
          }}
        }}
      }}
    }}, document.getElementById('redoc-container'));
  </script>

</body>
</html>"""
    return HTMLResponse(
        content=html_content,
        headers={"Content-Security-Policy": _DOCS_CSP_DIRECTIVES},
    )


@app.get("/scalar", include_in_schema=False, response_class=HTMLResponse)
async def scalar_api_reference() -> HTMLResponse:
    """Serve modern dark-themed Scalar API Reference documentation in 3-column layout."""
    from app.infrastructure.security.security_headers import _DOCS_CSP_DIRECTIVES

    topbar = _CFI_DOCS_TOPBAR_HTML.replace("{docs_active}", "").replace("{redoc_active}", "").replace("{scalar_active}", "active")
    html_content = f"""<!doctype html>
<html lang="en">
  <head>
    <title>CF-Intelligence | Enterprise API Reference</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
      :root {{
        --scalar-font: 'Inter', system-ui, -apple-system, sans-serif;
        --scalar-font-code: 'JetBrains Mono', monospace;
      }}
      body {{
        margin: 0;
        background-color: #0b0f19;
        font-family: var(--scalar-font);
      }}
      ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
      ::-webkit-scrollbar-track {{ background: #0b0f19; }}
      ::-webkit-scrollbar-thumb {{ background: #1e293b; border-radius: 4px; }}
      ::-webkit-scrollbar-thumb:hover {{ background: #334155; }}

      {_CFI_DOCS_NAV_CSS}
    </style>
  </head>
  <body>
    {topbar}
    <script
      id="api-reference"
      data-url="/openapi.json"
      data-configuration='{{"theme":"deepSpace","layout":"classic","darkMode":true,"showSidebar":true,"hideModels":false,"searchHotKey":"k","defaultHttpClient":{{"targetKey":"python","clientKey":"httpx"}}}}'
      src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@latest">
    </script>
  </body>
</html>"""
    return HTMLResponse(
        content=html_content,
        headers={"Content-Security-Policy": _DOCS_CSP_DIRECTIVES},
    )



@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Return empty 204 No Content for browser favicon requests."""
    return Response(status_code=204)



