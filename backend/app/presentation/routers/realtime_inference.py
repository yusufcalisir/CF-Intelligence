"""Low-Latency Real-Time Inference Gateway Router — Section 42.1."""

from __future__ import annotations

import logging
import pickle
import time
from typing import Any

import torch
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import get_settings
from app.domain.inference_fallback import (
    InferenceDecision,
    InferenceFallbackEngine,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/inference", tags=["Real-Time Inference"])


class RealtimeInferenceRequest(BaseModel):
    """Schema for online transaction authorization requests."""

    transaction_id: str = Field(..., json_schema_extra={"example": "tx_88992211"})
    amount: float = Field(..., ge=0.0, json_schema_extra={"example": 1250.50})
    currency: str = Field("USD", json_schema_extra={"example": "USD"})
    source_account: str = Field(..., json_schema_extra={"example": "acc_src_991"})
    target_account: str = Field(..., json_schema_extra={"example": "acc_dst_002"})
    merchant_category: str = Field(
        "general_retail", json_schema_extra={"example": "crypto_exchange"}
    )
    velocity_1h: int = Field(1, ge=0, json_schema_extra={"example": 3})
    force_fallback: bool = Field(
        False, description="Simulate model timeout/failure to test fallback engine."
    )


class RealtimeInferenceResponse(BaseModel):
    """Schema for online transaction authorization decision responses."""

    transaction_id: str
    risk_score: float
    decision: InferenceDecision
    latency_ms: float
    evaluated_by: str  # "ML_MODEL" or "HEURISTIC_FALLBACK"
    explanation: str


fallback_engine = InferenceFallbackEngine()

# Circuit Breaker & Redis Cache State
_consecutive_failures: int = 0
_circuit_open: bool = False
_circuit_opened_at: float = 0.0
_cached_scripted_model: Any | None = None
_cached_from_redis: bool = False


def reset_circuit_breaker() -> None:
    """Reset circuit breaker state for testing or recovery."""
    global _consecutive_failures, _circuit_open, _circuit_opened_at
    _consecutive_failures = 0
    _circuit_open = False
    _circuit_opened_at = 0.0


def reset_model_cache() -> None:
    """Clear local and Redis cached scripted model."""
    global _cached_scripted_model, _cached_from_redis
    _cached_scripted_model = None
    _cached_from_redis = False


def get_scripted_model() -> tuple[Any, bool]:
    """Retrieve or compile PyTorch TorchScript JIT champion model with Redis caching."""
    global _cached_scripted_model, _cached_from_redis

    if _cached_scripted_model is not None:
        return _cached_scripted_model, _cached_from_redis

    # Try Redis cache first
    try:
        from app.infrastructure.cache import get_redis_client

        redis_client = get_redis_client()
        if redis_client:
            cached_bytes = redis_client.get("cfi:champion_model")
            if cached_bytes:
                _cached_scripted_model = pickle.loads(cached_bytes)
                _cached_from_redis = True
                logger.info(
                    "Loaded champion TorchScript model from Redis cache (cfi:champion_model)."
                )
                return _cached_scripted_model, True
    except Exception as exc:
        logger.debug("Redis cache miss or read error: %s", exc)

    # Compile fresh TorchScript model via ModelService
    from app.application.services.model_service import ModelService

    settings = get_settings()
    svc = ModelService(settings)
    raw_model = svc.get_champion()
    raw_model.eval()

    dummy_input = torch.randn(2, 10)
    try:
        scripted = torch.jit.trace(raw_model, dummy_input)
        scripted.eval()
    except Exception as exc:
        logger.warning("TorchScript tracing failed (%s); using PyTorch raw model", exc)
        scripted = raw_model

    _cached_scripted_model = scripted
    _cached_from_redis = False

    # Store in Redis
    try:
        from app.infrastructure.cache import get_redis_client

        redis_client = get_redis_client()
        if redis_client:
            redis_client.set("cfi:champion_model", pickle.dumps(scripted), ex=3600)
    except Exception:
        pass

    return _cached_scripted_model, False


@router.post("/score", response_model=RealtimeInferenceResponse)
def score_transaction_realtime(
    payload: RealtimeInferenceRequest,
) -> RealtimeInferenceResponse:
    """Scores an incoming transaction in real time with JIT model, Redis cache hit, and circuit breaker."""
    global _consecutive_failures, _circuit_open, _circuit_opened_at

    start_time = time.perf_counter()
    now = time.time()

    # 1. Check Circuit Breaker State (60s cooldown)
    if _circuit_open:
        if now - _circuit_opened_at > 60.0:
            logger.info("Circuit Breaker cooldown elapsed. Attempting model recovery...")
            reset_circuit_breaker()
        else:
            logger.warning(
                "Circuit Breaker is OPEN (3 consecutive failures). Routing directly to heuristic fallback."
            )
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

    try:
        if payload.force_fallback:
            raise RuntimeError("Forced simulation fallback")

        # 2. Get TorchScript Model (Redis cache or JIT)
        scripted_model, from_redis = get_scripted_model()

        # 3. Construct input feature vector (10 features)
        merchant_risk = (
            0.5 if payload.merchant_category.lower() in ("crypto_exchange", "gambling") else 0.1
        )
        features = [
            payload.amount / 1000.0,
            float(payload.velocity_1h),
            merchant_risk,
            0.1,
            0.2,
            0.0,
            0.3,
            0.05,
            0.1,
            0.0,
        ]
        input_tensor = torch.FloatTensor([features])

        # 4. TorchScript JIT Inference
        with torch.no_grad():
            output = scripted_model(input_tensor)
            model_score = float(output.item()) if hasattr(output, "item") else float(output[0])

        # Reset consecutive failures on success
        _consecutive_failures = 0

        # High amount rule overlay
        reasons: list[str] = []
        final_score = model_score
        if payload.amount > 20000.0:
            final_score += 0.30
            reasons.append("High amount")
        if payload.merchant_category.lower() in ("crypto_exchange", "gambling", "p2p_cash"):
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

        # Record telemetry
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

    except Exception as exc:
        _consecutive_failures += 1
        logger.warning(
            "Primary ML inference failed for tx %s (strike %d/3: %s).",
            payload.transaction_id,
            _consecutive_failures,
            exc,
        )

        if _consecutive_failures >= 3:
            _circuit_open = True
            _circuit_opened_at = time.time()
            logger.error("Inference Circuit Breaker TRIPPED OPEN after 3 failures!")

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
            explanation=explanation,
        )
