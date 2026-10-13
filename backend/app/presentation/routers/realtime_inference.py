"""Low-Latency Real-Time Inference Gateway Router — Section 42.1."""

from __future__ import annotations

import contextlib
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
from app.application.services.training_provenance_service import (
    DOMAIN_MODEL_CACHE,
    derive_domain_key,
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

# Circuit Breaker State
_cb_lock = threading.Lock()
_consecutive_failures: int = 0
_circuit_open: bool = False
_circuit_opened_at: float = 0.0


class ModelCacheEntry:
    def __init__(
        self,
        model: Any,
        version: int,
        simulation_id: str,
        artifact_sha256: str,
        from_redis: bool,
        cached_at: float,
        generation: int = 1,
        issuer_key_id: str | None = None,
    ) -> None:
        self.model = model
        self.version = version
        self.simulation_id = simulation_id
        self.artifact_sha256 = artifact_sha256
        self.from_redis = from_redis
        self.cached_at = cached_at
        self.generation = generation
        self.issuer_key_id = issuer_key_id


class LocalModelCache:
    """Thread-safe, version-bound process-local champion model cache with single-flight compilation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._single_flight_lock = threading.RLock()
        self._entries: dict[str, ModelCacheEntry] = {}

    @property
    def _entry(self) -> ModelCacheEntry | None:
        with self._lock:
            if not self._entries:
                return None
            return next(iter(self._entries.values()))

    @_entry.setter
    def _entry(self, val: ModelCacheEntry | None) -> None:
        with self._lock:
            if val is None:
                self._entries.clear()
            else:
                self._entries[val.simulation_id or "default"] = val

    def get(
        self,
        current_meta: dict[str, Any] | None,
        verifier: Any | None = None,
    ) -> tuple[Any, bool] | None:
        """Return cached model if valid, matching active metadata, and signed by a currently trusted issuer."""
        with self._lock:
            if current_meta is None:
                self.clear()
                return None
            sim_key = str(current_meta.get("simulation_id") or "default")
            entry = self._entries.get(sim_key)
            if entry is None:
                return None

            # Verify current trust status of issuer key
            target_kid = entry.issuer_key_id or current_meta.get("issuer_key_id") or current_meta.get("key_id")
            if target_kid:
                from app.application.services.training_provenance_service import (
                    get_default_training_verifier,
                )

                active_verifier = verifier or get_default_training_verifier()
                try:
                    if not active_verifier.is_key_trusted(target_kid):
                        logger.warning(
                            "Evicting cached model for '%s' because issuer key '%s' is no longer trusted.",
                            sim_key,
                            target_kid,
                        )
                        self._entries.pop(sim_key, None)
                        return None
                except Exception as exc:
                    logger.warning(
                        "Evicting cached model for '%s' because trust check failed: %s",
                        sim_key,
                        exc,
                    )
                    self._entries.pop(sim_key, None)
                    return None

            meta_gen = current_meta.get("generation")
            gen_matches = True if meta_gen is None else (entry.generation == meta_gen)

            if (
                entry.version == current_meta.get("version")
                and entry.simulation_id == current_meta.get("simulation_id")
                and entry.artifact_sha256 == current_meta.get("sha256")
                and gen_matches
            ):
                return entry.model, entry.from_redis

            logger.info(
                "Local process cache is stale for '%s' (cached v%s gen %s, active v%s gen %s). Evicting.",
                sim_key,
                entry.version,
                entry.generation,
                current_meta.get("version"),
                meta_gen,
            )
            self._entries.pop(sim_key, None)
            return None

    def set(
        self,
        model: Any,
        version: int,
        simulation_id: str,
        artifact_sha256: str,
        from_redis: bool = False,
        generation: int = 1,
        issuer_key_id: str | None = None,
    ) -> None:
        """Atomically store a verified champion model bound to its version identity."""
        with self._lock:
            sim_key = simulation_id or "default"
            self._entries[sim_key] = ModelCacheEntry(
                model=model,
                version=version,
                simulation_id=simulation_id,
                artifact_sha256=artifact_sha256,
                from_redis=from_redis,
                cached_at=time.time(),
                generation=generation,
                issuer_key_id=issuer_key_id,
            )

    def clear(self, simulation_id: str | None = None) -> None:
        """Evict local cached model."""
        with self._lock:
            if simulation_id:
                self._entries.pop(simulation_id, None)
            else:
                self._entries.clear()


_local_cache = LocalModelCache()


def reset_circuit_breaker() -> None:
    """Reset circuit breaker state for testing or recovery."""
    global _consecutive_failures, _circuit_open, _circuit_opened_at
    with _cb_lock:
        _consecutive_failures = 0
        _circuit_open = False
        _circuit_opened_at = 0.0


def _get_redis_client() -> Any:
    """Retrieve Redis client via infrastructure cache."""
    try:
        from app.infrastructure.cache import get_redis_client

        return get_redis_client()
    except Exception:
        return None


def reset_model_cache(simulation_id: str | None = None) -> None:
    """Clear local and Redis cached scripted model."""
    _local_cache.clear(simulation_id)
    with contextlib.suppress(Exception):
        rc = _get_redis_client()
        if rc:
            if simulation_id:
                rc.delete(f"cfi:champion_model:{simulation_id}", f"cfi:champion_model:{simulation_id}:auth")
            rc.delete("cfi:champion_model", "cfi:champion_model:auth")


def _is_demo_mode_active() -> bool:
    """Check if demonstration/simulation fallback is explicitly permitted."""
    settings = get_settings()
    env = settings.app_env.lower()
    if env == "production":
        return False
    return env in ("demo", "simulation") or settings.enable_demo_fallback


def get_scripted_model(simulation_id: str | None = None) -> tuple[Any, bool]:
    """Retrieve or compile PyTorch TorchScript JIT champion model with secure Redis caching.

    Enforces cryptographic provenance, trusted local champion loading, version binding,
    and strict parameter verification. Single-flight compilation prevents concurrency races.
    Never falls back to unsafe deserialization (pickle) or random weights.
    """
    settings = get_settings()
    from app.application.services.model_registry import ModelRegistry
    from app.application.services.model_service import NUM_FEATURES, ModelService

    registry = ModelRegistry()

    # 1. Fast path: lightweight manifest identity check (avoids reading and hashing champion weights on every warm request - Defect F)
    champion_ident = registry.get_active_champion_identity(simulation_id=simulation_id)
    if champion_ident is not None:
        cached = _local_cache.get(champion_ident, verifier=registry._verifier)
        if cached is not None:
            return cached

    # 2. Cold start or cache miss -> Acquire single-flight lock
    with _local_cache._single_flight_lock:
        while True:
            # Re-check lightweight identity inside lock
            champion_ident = registry.get_active_champion_identity(simulation_id=simulation_id)
            if champion_ident is not None:
                cached = _local_cache.get(champion_ident, verifier=registry._verifier)
                if cached is not None:
                    return cached

            # Resolve full champion metadata and verify artifact integrity
            try:
                current_meta = registry.get_champion_metadata(simulation_id=simulation_id)
            except ModelIntegrityError:
                _local_cache.clear()
                raise
            if current_meta is None:
                _local_cache.clear()
                raise ModelNotAvailableError("No verified active champion model artifact found in registry.")

            # Double check local cache
            cached = _local_cache.get(current_meta)
            if cached is not None:
                return cached

            # 3. Try Redis cache with strict HMAC authentication AND champion identity binding
            try:
                redis_client = _get_redis_client()
                if redis_client:
                    sim_scope = current_meta.get("simulation_id")
                    scoped_model_key = f"cfi:champion_model:{sim_scope}" if sim_scope else "cfi:champion_model"
                    scoped_auth_key = f"cfi:champion_model:{sim_scope}:auth" if sim_scope else "cfi:champion_model:auth"

                    cached_bytes = redis_client.get(scoped_model_key)
                    cached_auth_bytes = redis_client.get(scoped_auth_key)
                    if not cached_bytes and not sim_scope:
                        cached_bytes = redis_client.get("cfi:champion_model")
                        cached_auth_bytes = redis_client.get("cfi:champion_model:auth")

                    if cached_bytes and cached_auth_bytes:
                        try:
                            auth_data = json.loads(
                                cached_auth_bytes.decode("utf-8")
                                if isinstance(cached_auth_bytes, bytes)
                                else cached_auth_bytes
                            )
                            claimed_hmac = (
                                auth_data.get("signature", "")
                                or auth_data.get("hmac", "")
                            )
                            claimed_key_id = auth_data.get("key_id", "cfi_model_cache_key_v1")

                            # Enforce trusted key_id
                            if claimed_key_id != "cfi_model_cache_key_v1":
                                logger.warning(
                                    "Redis cache envelope has untrusted key_id '%s'. Evicting.", claimed_key_id
                                )
                                with contextlib.suppress(Exception):
                                    redis_client.delete(scoped_model_key, scoped_auth_key)
                                    if not sim_scope:
                                        redis_client.delete("cfi:champion_model", "cfi:champion_model:auth")
                            else:
                                # Normalize payload dictionary
                                if "payload" in auth_data and isinstance(auth_data["payload"], dict):
                                    payload_dict = dict(auth_data["payload"])
                                    if "serialization_version" not in payload_dict and "serialization_version" in auth_data:
                                        payload_dict["serialization_version"] = auth_data["serialization_version"]
                                    if "key_id" not in payload_dict and "key_id" in auth_data:
                                        payload_dict["key_id"] = auth_data["key_id"]
                                    meta_to_verify = payload_dict
                                    meta_for_schema = payload_dict
                                else:
                                    meta_to_verify = {
                                        k: v for k, v in auth_data.items() if k not in ("hmac", "signature", "cached_at")
                                    }
                                    meta_for_schema = auth_data

                                canonical_meta_str_compact = json.dumps(
                                    meta_to_verify, sort_keys=True, separators=(",", ":")
                                )
                                canonical_meta_str_std = json.dumps(
                                    meta_to_verify, sort_keys=True
                                )
                                domain_key = derive_domain_key(settings.payload_signing_secret, DOMAIN_MODEL_CACHE)
                                expected_hmacs = [
                                    hmac.new(domain_key, canonical_meta_str_compact.encode("utf-8") + b":" + cached_bytes, hashlib.sha256).hexdigest(),
                                    hmac.new(domain_key, canonical_meta_str_std.encode("utf-8") + b":" + cached_bytes, hashlib.sha256).hexdigest(),
                                ]
                                if settings.app_env.lower() != "production":
                                    expected_hmacs.extend([
                                        hmac.new(settings.payload_signing_secret.encode("utf-8"), canonical_meta_str_compact.encode("utf-8") + b":" + cached_bytes, hashlib.sha256).hexdigest(),
                                        hmac.new(settings.payload_signing_secret.encode("utf-8"), canonical_meta_str_std.encode("utf-8") + b":" + cached_bytes, hashlib.sha256).hexdigest(),
                                    ])

                                hmac_ok = any(hmac.compare_digest(exp, claimed_hmac) for exp in expected_hmacs if claimed_hmac)

                                if not hmac_ok:
                                    logger.warning("Redis model cache failed HMAC authenticity verification. Evicting.")
                                    with contextlib.suppress(Exception):
                                        redis_client.delete(scoped_model_key, scoped_auth_key)
                                        if scoped_model_key != "cfi:champion_model":
                                            redis_client.delete("cfi:champion_model", "cfi:champion_model:auth")
                                else:
                                    # MANDATORY FIELD VALIDATION:
                                    # Every mandatory field must be present, non-empty, and correctly typed.
                                    mandatory_fields = {
                                        "serialization_version": int,
                                        "version": int,
                                        "generation": int,
                                        "simulation_id": str,
                                        "artifact_sha256": str,
                                        "model_sha256": str,
                                        "architecture": str,
                                        "input_dim": int,
                                    }
                                    schema_valid = True
                                    for f_name, exp_type in mandatory_fields.items():
                                        val = meta_for_schema.get(f_name)
                                        if val is None:
                                            logger.warning(
                                                "Redis cache envelope missing mandatory field '%s'. Evicting.", f_name
                                            )
                                            schema_valid = False
                                            break
                                        if not isinstance(val, exp_type):
                                            logger.warning(
                                                "Redis cache field '%s' type mismatch (expected %s, got %s). Evicting.",
                                                f_name,
                                                exp_type,
                                                type(val),
                                            )
                                            schema_valid = False
                                            break
                                        if isinstance(val, str) and not val.strip():
                                            logger.warning(
                                                "Redis cache field '%s' is empty. Evicting.", f_name
                                            )
                                            schema_valid = False
                                            break

                                    if not schema_valid:
                                        with contextlib.suppress(Exception):
                                            redis_client.delete(scoped_model_key, scoped_auth_key)
                                            if scoped_model_key != "cfi:champion_model":
                                                redis_client.delete("cfi:champion_model", "cfi:champion_model:auth")
                                    else:
                                        expected_model_sha = hashlib.sha256(cached_bytes).hexdigest()
                                        claimed_model_sha = meta_for_schema["model_sha256"]

                                        if not hmac.compare_digest(expected_model_sha, claimed_model_sha):
                                            logger.warning("Redis model cache SHA-256 digest mismatch. Evicting.")
                                            with contextlib.suppress(Exception):
                                                redis_client.delete(scoped_model_key, scoped_auth_key)
                                                if scoped_model_key != "cfi:champion_model":
                                                    redis_client.delete("cfi:champion_model", "cfi:champion_model:auth")
                                        elif (
                                            meta_for_schema.get("serialization_version") != 2
                                            or meta_for_schema.get("version") != current_meta.get("version")
                                            or meta_for_schema.get("generation", 1) != current_meta.get("generation", 1)
                                            or meta_for_schema.get("simulation_id") != current_meta.get("simulation_id")
                                            or not hmac.compare_digest(
                                                meta_for_schema.get("artifact_sha256", ""), current_meta.get("sha256", "")
                                            )
                                            or meta_for_schema.get("input_dim") != NUM_FEATURES
                                            or meta_for_schema.get("architecture") != (current_meta.get("architecture") or "FraudDetectionModel-GroupNorm")
                                            or (
                                                settings.app_env.lower() == "production"
                                                and str(meta_for_schema.get("provenance_type", "UNVERIFIED")).strip().upper()
                                                not in ("TRAINING_RECORDED", "TRAINING_EVIDENCE_VERIFIED")
                                            )
                                        ):
                                            logger.warning(
                                                "Redis cache entry does not match current champion identity or generation. Evicting."
                                            )
                                            with contextlib.suppress(Exception):
                                                redis_client.delete(scoped_model_key, scoped_auth_key)
                                                if scoped_model_key != "cfi:champion_model":
                                                    redis_client.delete("cfi:champion_model", "cfi:champion_model:auth")
                                        else:
                                            # Authenticated, complete schema, and strictly matches current active champion!
                                            buffer = io.BytesIO(cached_bytes)
                                            loaded_jit = torch.jit.load(buffer, map_location="cpu")
                                            loaded_jit.eval()

                                            dummy = torch.zeros(1, NUM_FEATURES)
                                            with torch.no_grad():
                                                test_val = loaded_jit(dummy)
                                                if torch.isfinite(test_val).all():
                                                    kid_val = (
                                                        (current_meta.get("training_receipt") or {}).get("key_id")
                                                        if isinstance(current_meta.get("training_receipt"), dict)
                                                        else (current_meta.get("issuer_key_id") or current_meta.get("key_id"))
                                                    )
                                                    _local_cache.set(
                                                        model=loaded_jit,
                                                        version=current_meta["version"],
                                                        simulation_id=current_meta["simulation_id"],
                                                        artifact_sha256=current_meta["sha256"],
                                                        from_redis=True,
                                                        generation=current_meta.get("generation", 1),
                                                        issuer_key_id=kid_val,
                                                    )
                                                    logger.info(
                                                        "Loaded authenticated champion v%d gen %d TorchScript model from Redis cache.",
                                                        current_meta["version"],
                                                        current_meta.get("generation", 1),
                                                    )
                                                    return loaded_jit, True
                        except Exception as err:
                            logger.warning(
                                "Failed to validate/load Redis cache: %s; falling back to registry.", err
                            )
            except Exception as exc:
                logger.debug("Redis cache read error: %s", exc)

            # 4. Load genuine champion model from ModelRegistry via ModelService
            svc = ModelService(settings)
            champion_model = svc.get_champion(simulation_id=simulation_id, dp_compatible=True, registry=registry)
            champion_model.eval()

            # 5. Compile TorchScript JIT model
            dummy_input = torch.zeros(1, NUM_FEATURES)
            try:
                scripted = torch.jit.trace(champion_model, dummy_input, check_trace=False)
                if isinstance(scripted, torch.nn.Module):
                    scripted.eval()
                logger.info("TorchScript JIT model compiled successfully from verified champion weights.")
            except Exception as exc:
                logger.warning("TorchScript tracing failed (%s); using verified PyTorch model directly", exc)
                scripted = champion_model

            # 6. Race check: Did a promotion happen during compilation?
            latest_meta = registry.get_champion_metadata(simulation_id=simulation_id)
            if (
                latest_meta is None
                or latest_meta.get("version") != current_meta.get("version")
                or latest_meta.get("generation") != current_meta.get("generation")
                or latest_meta.get("sha256") != current_meta.get("sha256")
                or latest_meta.get("simulation_id") != current_meta.get("simulation_id")
            ):
                logger.warning(
                    "Champion metadata changed during model compilation (v%s gen %s -> v%s gen %s); discarding stale compilation.",
                    current_meta.get("version"),
                    current_meta.get("generation"),
                    latest_meta.get("version") if latest_meta else "none",
                    latest_meta.get("generation") if latest_meta else "none",
                )
                if latest_meta is None:
                    _local_cache.clear(simulation_id)
                    raise ModelNotAvailableError("Active champion model removed during compilation.")
                continue

            # Publish to local process cache
            kid_val = (
                (current_meta.get("training_receipt") or {}).get("key_id")
                if isinstance(current_meta.get("training_receipt"), dict)
                else (current_meta.get("issuer_key_id") or current_meta.get("key_id"))
            )
            _local_cache.set(
                model=scripted,
                version=current_meta["version"],
                simulation_id=current_meta["simulation_id"],
                artifact_sha256=current_meta["sha256"],
                from_redis=False,
                generation=current_meta.get("generation", 1),
                issuer_key_id=kid_val,
            )

            # 7. Securely populate Redis cache using atomic pipeline ONLY (Defect D)
            try:
                from app.infrastructure.cache import get_redis_client

                redis_client = get_redis_client()
                if redis_client:
                    buffer = io.BytesIO()
                    torch.jit.save(scripted, buffer)
                    model_bytes = buffer.getvalue()
                    model_sha256 = hashlib.sha256(model_bytes).hexdigest()

                    sim_scope = current_meta["simulation_id"]
                    scoped_model_key = f"cfi:champion_model:{sim_scope}"
                    scoped_auth_key = f"cfi:champion_model:{sim_scope}:auth"

                    meta_to_sign = {
                        "version": current_meta["version"],
                        "generation": current_meta.get("generation", 1),
                        "simulation_id": current_meta["simulation_id"],
                        "artifact_sha256": current_meta["sha256"],
                        "model_sha256": model_sha256,
                        "architecture": current_meta.get(
                            "architecture", "FraudDetectionModel-GroupNorm"
                        ),
                        "input_dim": NUM_FEATURES,
                        "serialization_version": 2,
                        "provenance_type": current_meta.get("provenance_type", "UNVERIFIED"),
                    }
                    canonical_meta_str = json.dumps(
                        meta_to_sign, sort_keys=True, separators=(",", ":")
                    )
                    domain_key = derive_domain_key(settings.payload_signing_secret, DOMAIN_MODEL_CACHE)
                    envelope_hmac = hmac.new(
                        domain_key,
                        canonical_meta_str.encode("utf-8") + b":" + model_bytes,
                        hashlib.sha256,
                    ).hexdigest()

                    auth_envelope = dict(meta_to_sign)
                    auth_envelope["hmac"] = envelope_hmac
                    auth_envelope["cached_at"] = time.time()
                    auth_envelope_str = json.dumps(auth_envelope)

                    if hasattr(redis_client, "pipeline"):
                        try:
                            pipe = redis_client.pipeline(transaction=True)
                            pipe.set(scoped_model_key, model_bytes, ex=3600)
                            pipe.set(scoped_auth_key, auth_envelope_str, ex=3600)
                            pipe.set("cfi:champion_model", model_bytes, ex=3600)
                            pipe.set("cfi:champion_model:auth", auth_envelope_str, ex=3600)
                            pipe.execute()
                            logger.info(
                                "Cached champion v%d gen %d in Redis atomically with version-bound HMAC envelope.",
                                current_meta["version"],
                                current_meta.get("generation", 1),
                            )
                        except Exception as pipe_err:
                            logger.warning(
                                "Redis atomic pipeline write failed (%s); skipping cache publication.", pipe_err
                            )
                    else:
                        logger.warning(
                            "Redis client does not support atomic pipeline/transaction; skipping cache publication to preserve atomicity."
                        )
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

    # 3. Construct and validate feature tensor (10 features)
    from app.application.services.data_generator import preprocess_transaction

    if is_production:
        required_fields = {
            "customer_history_score": payload.customer_history_score,
            "chargeback_count": payload.chargeback_count,
            "account_age_days": payload.account_age_days,
            "country_code": payload.country_code,
            "device_type": payload.device_type,
            "merchant_risk_score": payload.merchant_risk_score,
            "hour_of_day": payload.hour_of_day,
        }
        for field_name, field_val in required_fields.items():
            if field_val is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "MISSING_REQUIRED_FEATURES",
                        "message": f"Required feature '{field_name}' is missing or None. Production scoring cannot fabricate feature inputs.",
                    },
                )

    has_simulated_features = (
        payload.customer_history_score is None
        or payload.chargeback_count is None
        or payload.account_age_days is None
        or payload.country_code is None
        or payload.device_type is None
        or payload.merchant_risk_score is None
        or payload.hour_of_day is None
    )

    cat_str = payload.merchant_category.lower() if payload.merchant_category else ""
    merchant_risk = (
        payload.merchant_risk_score
        if payload.merchant_risk_score is not None
        else (0.50 if cat_str in ("crypto_exchange", "crypto", "gambling") else 0.10)
    )
    hour_val = (
        float(payload.hour_of_day)
        if payload.hour_of_day is not None
        else float(time.gmtime().tm_hour)
    )

    txn_dict = {
        "transaction_amount": payload.amount,
        "merchant_category": payload.merchant_category,
        "country_code": payload.country_code or "US",
        "device_type": payload.device_type or "mobile_app",
        "velocity": float(payload.velocity_1h),
        "hour_of_day": hour_val,
        "merchant_risk_score": merchant_risk,
        "customer_history_score": (
            payload.customer_history_score
            if payload.customer_history_score is not None
            else 0.90
        ),
        "chargeback_count": (
            float(payload.chargeback_count)
            if payload.chargeback_count is not None
            else 0.0
        ),
        "account_age_days": (
            float(payload.account_age_days)
            if payload.account_age_days is not None
            else 365.0
        ),
    }
    try:
        input_tensor = preprocess_transaction(txn_dict, strict_categorical=is_production)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_FEATURE_INPUT",
                "message": str(err),
            },
        ) from err

    # 4. Retrieve verified champion model (fail-closed if unavailable or corrupt)
    try:
        scripted_model, from_redis = get_scripted_model(simulation_id=payload.simulation_id)
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


    # 5. Execute inference and validate model output contract
    try:
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
        sim_tag = " [Simulated Features]" if has_simulated_features else ""
        explanation = f"ML Model ({source_label}){sim_tag}: " + (
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
