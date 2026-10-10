"""Low-Latency Real-Time Inference Gateway Router — Section 42.1."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import threading
import time
from typing import Any

import torch
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.application.schemas.transaction import (
    InferenceQuotaResponse,
    RealtimeInferenceRequest,
    RealtimeInferenceResponse,
)
from app.config import get_settings
from app.dependencies import TenantDep
from app.domain.inference_fallback import (
    InferenceDecision,
    InferenceFallbackEngine,
)
from app.domain.model_serving_errors import (
    ModelCompatibilityError,
    ModelExecutionError,
    ModelIntegrityError,
    ModelNotAvailableError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/inference", tags=["Real-Time Inference"])
api_router = APIRouter(prefix="/api/v1/inference", tags=["Real-Time Inference"])

fallback_engine = InferenceFallbackEngine()

# Circuit Breaker & Redis Cache State
_cb_lock = threading.Lock()
_consecutive_failures: int = 0
_circuit_open: bool = False
_circuit_opened_at: float = 0.0
_cached_scripted_model: Any | None = None
_cached_from_redis: bool = False


def reset_circuit_breaker() -> None:
    """Reset circuit breaker state for testing or recovery."""
    global _consecutive_failures, _circuit_open, _circuit_opened_at
    with _cb_lock:
        _consecutive_failures = 0
        _circuit_open = False
        _circuit_opened_at = 0.0


def reset_model_cache() -> None:
    """Clear local and Redis cached scripted model."""
    global _cached_scripted_model, _cached_from_redis
    with _cb_lock:
        _cached_scripted_model = None
        _cached_from_redis = False


def _is_demo_mode_active() -> bool:
    """Check if demonstration/simulation fallback is explicitly permitted."""
    settings = get_settings()
    env = settings.app_env.lower()
    if env == "production":
        return False
    return env in ("demo", "simulation") or settings.enable_demo_fallback


def get_scripted_model() -> tuple[Any, bool]:
    """Retrieve or compile PyTorch TorchScript JIT champion model with secure Redis caching.

    Enforces cryptographic provenance, trusted local champion loading, and strict parameter verification.
    Never falls back to unsafe deserialization (pickle) or random weights.
    """
    global _cached_scripted_model, _cached_from_redis

    # 1. Thread-safe check of process-local cache
    with _cb_lock:
        if _cached_scripted_model is not None:
            return _cached_scripted_model, _cached_from_redis

    settings = get_settings()
    from app.application.services.model_service import NUM_FEATURES, ModelService

    # 2. Try Redis cache with strict HMAC authentication and SHA-256 integrity verification
    try:
        from app.infrastructure.cache import get_redis_client

        redis_client = get_redis_client()
        if redis_client:
            cached_bytes = redis_client.get("cfi:champion_model")
            cached_auth_bytes = redis_client.get("cfi:champion_model:auth")

            if cached_bytes:
                if not cached_auth_bytes:
                    logger.warning(
                        "Redis model cache entry missing HMAC authentication envelope (cfi:champion_model:auth); "
                        "rejecting unauthenticated payload."
                    )
                else:
                    try:
                        auth_data = json.loads(
                            cached_auth_bytes.decode("utf-8")
                            if isinstance(cached_auth_bytes, bytes)
                            else cached_auth_bytes
                        )
                        expected_hmac = hmac.new(
                            settings.payload_signing_secret.encode("utf-8"),
                            cached_bytes,
                            hashlib.sha256,
                        ).hexdigest()
                        claimed_hmac = auth_data.get("hmac", "")

                        if not hmac.compare_digest(expected_hmac, claimed_hmac):
                            logger.warning(
                                "Redis model cache failed HMAC authenticity verification; "
                                "rejecting untrusted/poisoned payload."
                            )
                        else:
                            expected_sha256 = hashlib.sha256(cached_bytes).hexdigest()
                            claimed_sha256 = auth_data.get("sha256", "")
                            if not hmac.compare_digest(expected_sha256, claimed_sha256):
                                logger.warning(
                                    "Redis model cache SHA-256 digest mismatch; corrupt cached bytes."
                                )
                            else:
                                # Authenticated! Load via TorchScript JIT only. Never use pickle.
                                buffer = io.BytesIO(cached_bytes)
                                loaded_jit = torch.jit.load(buffer, map_location="cpu")
                                loaded_jit.eval()

                                # Forward sanity check
                                dummy = torch.zeros(1, NUM_FEATURES)
                                with torch.no_grad():
                                    test_val = loaded_jit(dummy)
                                    if torch.isfinite(test_val).all():
                                        with _cb_lock:
                                            _cached_scripted_model = loaded_jit
                                            _cached_from_redis = True
                                        logger.info(
                                            "Loaded authenticated champion TorchScript model from Redis cache."
                                        )
                                        return loaded_jit, True
                                    logger.warning(
                                        "Cached TorchScript model sanity check produced non-finite values."
                                    )
                    except Exception as parse_err:
                        logger.warning(
                            "Failed to load authenticated model from Redis cache: %s; falling back to local registry.",
                            parse_err,
                        )
    except Exception as exc:
        logger.debug("Redis cache read error or unavailable: %s", exc)

    # 3. Load genuine champion model from ModelRegistry via ModelService
    svc = ModelService(settings)
    from app.application.services.model_registry import ModelRegistry

    registry = ModelRegistry()

    # Loads actual trained weights; strictly rejects missing, incomplete, or incompatible checkpoints
    champion_model = svc.get_champion(dp_compatible=True, registry=registry)
    champion_model.eval()

    # 4. Compile TorchScript JIT model
    dummy_input = torch.zeros(1, NUM_FEATURES)
    try:
        scripted = torch.jit.trace(champion_model, dummy_input, check_trace=False)
        if isinstance(scripted, torch.nn.Module):
            scripted.eval()
        logger.info("TorchScript JIT model compiled successfully from verified champion weights.")
    except Exception as exc:
        logger.warning("TorchScript tracing failed (%s); using verified PyTorch model directly", exc)
        scripted = champion_model

    # Atomic publication to local cache under thread lock
    with _cb_lock:
        _cached_scripted_model = scripted
        _cached_from_redis = False

    # 5. Securely populate Redis cache with authenticated envelope (never pickle)
    try:
        from app.infrastructure.cache import get_redis_client

        redis_client = get_redis_client()
        if redis_client:
            buffer = io.BytesIO()
            torch.jit.save(scripted, buffer)
            model_bytes = buffer.getvalue()
            model_hmac = hmac.new(
                settings.payload_signing_secret.encode("utf-8"),
                model_bytes,
                hashlib.sha256,
            ).hexdigest()
            model_sha256 = hashlib.sha256(model_bytes).hexdigest()
            auth_envelope = json.dumps(
                {
                    "hmac": model_hmac,
                    "sha256": model_sha256,
                    "cached_at": time.time(),
                }
            )
            redis_client.set("cfi:champion_model", model_bytes, ex=3600)
            redis_client.set("cfi:champion_model:auth", auth_envelope, ex=3600)
            logger.info("Cached champion model in Redis with HMAC authentication envelope.")
    except Exception as exc:
        logger.debug("Failed to store champion model in Redis: %s", exc)

    return scripted, False


@router.get("/quota", response_model=InferenceQuotaResponse)
@api_router.get("/quota", response_model=InferenceQuotaResponse)
def get_inference_quota(
    request: Request,
    x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
    caller_tenant: TenantDep = None,
) -> InferenceQuotaResponse:
    """Returns real-time tenant inference quotas, limits, and consumption."""
    from app.application.services.tenant_metering import get_tenant_metering_service

    tenant = x_tenant_id or x_bank_id or caller_tenant or "bank_alpha"
    metering = get_tenant_metering_service()
    limits = metering.get_quota_limits(tenant)
    usage = metering.get_usage(tenant)

    rem_daily = max(0, limits.max_daily_inferences - usage.daily_inferences)
    rem_monthly = max(0, limits.max_monthly_fl_rounds - usage.monthly_fl_rounds)

    return InferenceQuotaResponse(
        tenant_id=tenant,
        tier="ENTERPRISE",
        daily_inferences_limit=limits.max_daily_inferences,
        daily_inferences_used=usage.daily_inferences,
        daily_inferences_remaining=rem_daily,
        monthly_fl_rounds_limit=limits.max_monthly_fl_rounds,
        monthly_fl_rounds_used=usage.monthly_fl_rounds,
        monthly_fl_rounds_remaining=rem_monthly,
        storage_used_mb=round(usage.storage_used_mb, 2),
        max_storage_mb=limits.max_storage_mb,
        reset_date=usage.last_reset_date,
    )


@router.post("/score", response_model=RealtimeInferenceResponse)
@api_router.post("/score", response_model=RealtimeInferenceResponse)
def score_transaction_realtime(
    payload: RealtimeInferenceRequest,
) -> RealtimeInferenceResponse:
    """Scores an incoming transaction in real time with verified champion model, Redis caching, and circuit breaker."""
    global _consecutive_failures, _circuit_open, _circuit_opened_at

    start_time = time.perf_counter()
    now = time.time()
    settings = get_settings()
    is_production = settings.app_env.lower() == "production"
    demo_active = _is_demo_mode_active()

    # 1. Check Circuit Breaker State (60s cooldown) under thread lock
    with _cb_lock:
        is_open = _circuit_open
        opened_at = _circuit_opened_at

    if is_open:
        if now - opened_at > 60.0:
            logger.info("Circuit Breaker cooldown elapsed. Attempting model recovery...")
            reset_circuit_breaker()
        else:
            logger.warning("Circuit Breaker is OPEN (3 consecutive failures).")
            if demo_active:
                decision, risk_score, explanation = fallback_engine.evaluate_heuristic_fallback(
                    amount=payload.amount,
                    velocity_1h=payload.velocity_1h,
                    merchant_category=payload.merchant_category,
                )
                latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
                return RealtimeInferenceResponse(
                    transaction_id=payload.transaction_id,
                    risk_score=risk_score,
                    decision=decision,
                    latency_ms=latency_ms,
                    evaluated_by="HEURISTIC_FALLBACK",
                    explanation=f"[Circuit Breaker Open] {explanation}",
                )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "CIRCUIT_BREAKER_OPEN",
                    "message": "Inference circuit breaker is open due to consecutive failures; scoring unavailable.",
                },
            )

    # 2. Check Simulation Fallback request
    if payload.force_fallback:
        if is_production:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "SIMULATION_NOT_ALLOWED",
                    "message": "Simulation fallback is disabled in production environment.",
                },
            )
        if not demo_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "DEMO_MODE_DISABLED",
                    "message": "Simulation fallback is not enabled.",
                },
            )
        # Record failure strike in demo mode
        with _cb_lock:
            _consecutive_failures += 1
            cur_failures = _consecutive_failures
            if cur_failures >= 3:
                _circuit_open = True
                _circuit_opened_at = time.time()
                logger.error("Inference Circuit Breaker TRIPPED OPEN after 3 simulated failures!")

        decision, risk_score, explanation = fallback_engine.evaluate_heuristic_fallback(
            amount=payload.amount,
            velocity_1h=payload.velocity_1h,
            merchant_category=payload.merchant_category,
        )
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return RealtimeInferenceResponse(
            transaction_id=payload.transaction_id,
            risk_score=risk_score,
            decision=decision,
            latency_ms=latency_ms,
            evaluated_by="HEURISTIC_FALLBACK",
            explanation=f"[Simulation Fallback] {explanation}",
        )

    # 3. Retrieve verified champion model (fail-closed if unavailable or corrupt)
    try:
        scripted_model, from_redis = get_scripted_model()
    except ModelNotAvailableError as exc:
        logger.warning("Champion model unavailable for transaction %s: %s", payload.transaction_id, exc)
        if demo_active:
            decision, risk_score, explanation = fallback_engine.evaluate_heuristic_fallback(
                amount=payload.amount,
                velocity_1h=payload.velocity_1h,
                merchant_category=payload.merchant_category,
            )
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return RealtimeInferenceResponse(
                transaction_id=payload.transaction_id,
                risk_score=risk_score,
                decision=decision,
                latency_ms=latency_ms,
                evaluated_by="HEURISTIC_FALLBACK",
                explanation=f"[Model Not Ready - Demo Fallback] {explanation}",
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "MODEL_NOT_READY",
                "message": "A verified champion model is not currently available.",
            },
        ) from exc
    except (ModelIntegrityError, ModelCompatibilityError) as exc:
        logger.error("Champion model verification failed for transaction %s: %s", payload.transaction_id, exc)
        if demo_active:
            decision, risk_score, explanation = fallback_engine.evaluate_heuristic_fallback(
                amount=payload.amount,
                velocity_1h=payload.velocity_1h,
                merchant_category=payload.merchant_category,
            )
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return RealtimeInferenceResponse(
                transaction_id=payload.transaction_id,
                risk_score=risk_score,
                decision=decision,
                latency_ms=latency_ms,
                evaluated_by="HEURISTIC_FALLBACK",
                explanation=f"[Model Verification Failed - Demo Fallback] {explanation}",
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "MODEL_NOT_READY",
                "message": "A verified champion model is not currently available.",
            },
        ) from exc

    # 4. Construct feature tensor (10 features)
    try:
        from app.application.services.data_generator import MERCHANT_CATEGORIES

        cat_str = payload.merchant_category.lower()
        merchant_risk = 0.50 if cat_str in ("crypto_exchange", "crypto", "gambling") else 0.10
        cat_idx = float(
            MERCHANT_CATEGORIES.index(cat_str) if cat_str in MERCHANT_CATEGORIES else 0
        )
        hour_now = float(time.gmtime().tm_hour)

        features = [
            min(1.0, max(0.0, payload.amount / 5000.0)),
            min(1.0, max(0.0, cat_idx / 19.0)),
            0.0,  # country_code index (US default)
            0.25,  # device_type (mobile_app / web_browser default)
            min(1.0, max(0.0, float(payload.velocity_1h) / 30.0)),
            min(1.0, max(0.0, hour_now / 23.0)),
            merchant_risk,
            0.90,  # customer_history_score
            0.0,  # chargeback_count
            0.365,  # account_age_days (365/1000)
        ]
        input_tensor = torch.tensor([features], dtype=torch.float32)

        # 5. Execute inference and validate model output contract
        with torch.no_grad():
            output = scripted_model(input_tensor)

            # Contract validation
            if not isinstance(output, torch.Tensor):
                raise ModelExecutionError(f"Model returned non-tensor output of type {type(output)}")
            if not torch.isfinite(output).all():
                raise ModelExecutionError("Model inference produced non-finite output (NaN or Inf).")

            model_score = float(output.item()) if hasattr(output, "item") else float(output[0])
            if not (0.0 <= model_score <= 1.0):
                raise ModelExecutionError(f"Model output score {model_score} outside valid range [0.0, 1.0].")

        # Success: reset consecutive failures under lock
        with _cb_lock:
            _consecutive_failures = 0

        # Business rule overlay
        reasons: list[str] = []
        final_score = model_score
        if payload.amount > 20000.0:
            final_score += 0.30
            reasons.append("High amount")
        if cat_str in ("crypto_exchange", "crypto", "gambling", "p2p_cash"):
            final_score += 0.25
            reasons.append("High-risk merchant")

        risk_score = min(round(final_score, 4), 1.0)

        if risk_score >= 0.70:
            decision = InferenceDecision.BLOCK
        elif risk_score >= 0.35:
            decision = InferenceDecision.REVIEW
        else:
            decision = InferenceDecision.ALLOW

        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        source_label = "Redis Cache JIT" if from_redis else "PyTorch JIT"
        explanation = f"ML Model ({source_label}): " + (
            "; ".join(reasons) if reasons else "Normal risk profile"
        )

        from app.infrastructure import telemetry

        telemetry.cfi_inference_latency_ms.observe(latency_ms)

        return RealtimeInferenceResponse(
            transaction_id=payload.transaction_id,
            risk_score=risk_score,
            decision=decision,
            latency_ms=latency_ms,
            evaluated_by="ML_MODEL",
            explanation=explanation,
        )

    except HTTPException:
        raise
    except Exception as exc:
        # Runtime inference execution failure trips circuit breaker
        with _cb_lock:
            _consecutive_failures += 1
            cur_failures = _consecutive_failures
            if cur_failures >= 3:
                _circuit_open = True
                _circuit_opened_at = time.time()
                logger.error("Inference Circuit Breaker TRIPPED OPEN after 3 failures!")

        logger.warning(
            "Primary ML inference execution failed for tx %s (strike %d/3: %s).",
            payload.transaction_id,
            cur_failures,
            exc,
        )

        if demo_active:
            decision, risk_score, explanation = fallback_engine.evaluate_heuristic_fallback(
                amount=payload.amount,
                velocity_1h=payload.velocity_1h,
                merchant_category=payload.merchant_category,
            )
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return RealtimeInferenceResponse(
                transaction_id=payload.transaction_id,
                risk_score=risk_score,
                decision=decision,
                latency_ms=latency_ms,
                evaluated_by="HEURISTIC_FALLBACK",
                explanation=f"[Inference Error - Demo Fallback] {explanation}",
            )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "INFERENCE_FAILED",
                "message": "Inference execution failed.",
            },
        ) from exc
