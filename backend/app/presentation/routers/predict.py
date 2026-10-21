"""Predict and serving API router.

Evaluates transaction payloads in real-time using the active global model,
computes risk scores, generates explainability reports, and triggers alerts.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import threading
import time
import uuid
from typing import Any

import torch
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)

from app.application.schemas.transaction import (
    AlertDetails,
    BatchPredictionItem,
    BatchPredictionResponse,
    BatchTransactionPredictRequest,
    CounterfactualPathItem,
    ExplainTransactionRequest,
    ExplainTransactionResponse,
    FeatureAttributionItem,
    FeatureContributionItem,
    RelatedEntityItem,
    ScoreTransactionRequest,
    ScoreTransactionResponse,
    SignalBreakdown,
    TransactionFeedbackRequest,
    TransactionPredictRequest,
    TransactionPredictResponse,
)
from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.data_generator import (
    preprocess_transaction,
)
from app.application.services.explainability_service import ExplainabilityService
from app.application.services.feature_store_service import FeatureStoreService
from app.application.services.model_registry import (
    ModelEvaluationEngine,
    ModelRegistry,
    validate_production_admission,
)
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.model_serving_authorization import (
    authorize_model_serving,
    resolve_effective_caller_tenant,
)
from app.application.services.risk_engine import RiskScoringEngine
from app.config import get_settings
from app.dependencies import (
    SessionDep,
    TenantDep,
    enforce_tenant_quota,
)
from app.domain.model_serving_errors import (
    ModelAuthorizationError,
    ModelCompatibilityError,
    ModelExecutionError,
    ModelIntegrityError,
    ModelNotAvailableError,
)
from app.infrastructure.security.rate_limiter import (
    is_benchmark_mode,
    limiter,
    reset_rate_limiter,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["prediction"])
api_router = APIRouter(prefix="/v1", tags=["prediction"])

# Singleton instances (matching backend router architectures)
_settings = get_settings()

DEV_DEFAULTS: dict[str, Any] = {
    "merchant_category": "grocery",
    "country_code": "US",
    "device_type": "web_browser",
    "velocity": 1.0,
    "hour_of_day": 12,
    "merchant_risk_score": 0.05,
    "customer_history_score": 0.95,
    "chargeback_count": 0,
    "account_age_days": 365,
}

REQUIRED_ML_FEATURES: list[str] = [
    "transaction_amount",
    "merchant_category",
    "country_code",
    "device_type",
    "velocity",
    "hour_of_day",
    "merchant_risk_score",
    "customer_history_score",
    "chargeback_count",
    "account_age_days",
]


def _eval_model(model: torch.nn.Module, input_tensor: torch.Tensor) -> float:
    """Evaluate PyTorch model forward pass synchronously with strict output validation."""
    if not isinstance(input_tensor, torch.Tensor):
        raise ModelExecutionError(f"Model input must be a torch.Tensor, got {type(input_tensor).__name__}")
    if input_tensor.dim() != 2 or input_tensor.shape[0] != 1:
        raise ModelExecutionError(f"Model input must have shape (1, features), got {tuple(input_tensor.shape)}")

    with torch.no_grad():
        out = model(input_tensor)
        if not isinstance(out, torch.Tensor):
            raise ModelExecutionError(f"Model forward pass must return a torch.Tensor, got {type(out).__name__}")
        if out.numel() != 1:
            raise ModelExecutionError(f"Model forward pass produced unexpected output shape: {tuple(out.shape)} (expected 1 scalar output)")
        val = float(out.item())
        if not math.isfinite(val):
            raise ModelExecutionError(f"Model inference produced non-finite output: {val}")
        if not (0.0 <= val <= 1.0):
            raise ModelExecutionError(f"Model inference produced out-of-bounds probability: {val} (expected range [0.0, 1.0])")
        return val


def _eval_model_batch(model: torch.nn.Module, input_tensors: torch.Tensor) -> list[float]:
    """Evaluate PyTorch model forward pass synchronously for a batch of input tensors with strict validation."""
    if not isinstance(input_tensors, torch.Tensor):
        raise ModelExecutionError(f"Model batch input must be a torch.Tensor, got {type(input_tensors).__name__}")
    if input_tensors.dim() != 2:
        raise ModelExecutionError(f"Model batch input must have shape (batch_size, features), got {tuple(input_tensors.shape)}")

    batch_size = input_tensors.shape[0]
    with torch.no_grad():
        outputs = model(input_tensors)
        if not isinstance(outputs, torch.Tensor):
            raise ModelExecutionError(f"Model batch forward pass must return a torch.Tensor, got {type(outputs).__name__}")

        if outputs.dim() == 0 and batch_size == 1:
            res = [float(outputs.item())]
        elif outputs.dim() == 1:
            if outputs.shape[0] != batch_size:
                raise ModelExecutionError(f"Model batch output count mismatch: expected {batch_size}, got {outputs.shape[0]}")
            res = [float(v) for v in outputs.tolist()]
        elif outputs.dim() == 2:
            if outputs.shape[0] != batch_size or outputs.shape[1] != 1:
                raise ModelExecutionError(f"Model batch output shape mismatch: expected ({batch_size}, 1), got {tuple(outputs.shape)}")
            res = [float(v[0]) for v in outputs.tolist()]
        else:
            raise ModelExecutionError(f"Model batch output unexpected shape: {tuple(outputs.shape)} for batch size {batch_size}")

        if len(res) != batch_size:
            raise ModelExecutionError(f"Model batch result count mismatch: expected {batch_size}, got {len(res)}")

        for i, v in enumerate(res):
            if not math.isfinite(v):
                raise ModelExecutionError(f"Model batch inference item {i} produced non-finite output: {v}")
            if not (0.0 <= v <= 1.0):
                raise ModelExecutionError(f"Model batch inference item {i} produced out-of-bounds probability: {v} (expected range [0.0, 1.0])")
        return res



_model_service = ModelService(_settings)
_registry = ModelRegistry()
_eval_engine = ModelEvaluationEngine(_registry)
_risk_engine = RiskScoringEngine()
_alert_service = AlertIntelligenceService()
_explainability_service = ExplainabilityService()
_feature_store = FeatureStoreService()

_cached_serving_model: torch.nn.Module | None = None
_cached_serving_model_mtime: float = 0.0
_cached_serving_model_kid: str | None = None
_cached_serving_model_version: int | None = None

_challenger_cache_lock = threading.RLock()
_challenger_model_cache: dict[tuple[str, int, bool], tuple[torch.nn.Module, str | None]] = {}


def _load_challenger_model(simulation_id: str, version: int, dp_compatible: bool) -> torch.nn.Module:
    """Load and cache challenger model by simulation_id + version with issuer trust verification.

    Avoids per-request disk I/O for challenger shadow evaluation while guaranteeing that
    revoked issuer keys immediately evict cached models.
    """
    cache_key = (simulation_id, version, dp_compatible)
    with _challenger_cache_lock:
        if cache_key in _challenger_model_cache:
            cached_m, cached_kid = _challenger_model_cache[cache_key]
            if cached_kid:
                from app.application.services.training_provenance_service import (
                    get_default_training_verifier,
                )

                verifier = _registry._verifier or get_default_training_verifier()
                try:
                    if not verifier.is_key_trusted(cached_kid):
                        logger.warning(
                            "Evicting cached challenger model for sim '%s' v%d: issuer key '%s' revoked.",
                            simulation_id,
                            version,
                            cached_kid,
                        )
                        _challenger_model_cache.pop(cache_key, None)
                    else:
                        return cached_m
                except Exception as exc:
                    logger.warning("Trust check failed for cached challenger: %s", exc)
                    _challenger_model_cache.pop(cache_key, None)
            else:
                return cached_m

    res = _registry.resolve_and_verify_challenger(simulation_id)
    kid: str | None = None
    if res is not None:
        _, bytes_data, entry = res
        if entry.get("version") != version:
            raise ModelIntegrityError(
                f"Challenger version mismatch: requested {version}, resolved active challenger is {entry.get('version')}"
            )
        rcpt = entry.get("training_receipt")
        if isinstance(rcpt, dict):
            kid = rcpt.get("key_id")
        state_dict = torch.load(io.BytesIO(bytes_data), map_location="cpu", weights_only=True)
    else:
        from app.config import get_settings

        if get_settings().app_env.lower() == "production":
            raise ModelNotAvailableError(
                f"No verified challenger model found for simulation '{simulation_id}' v{version} in production."
            )
        state_dict = _registry.load_version(simulation_id, version)

    chall_dp = dp_compatible
    for key in state_dict:
        if "running_mean" in key or "running_var" in key:
            chall_dp = False
            break
    chall_input_dim = NUM_FEATURES
    for weight_key in ("network.0.weight", "module.network.0.weight"):
        if (
            weight_key in state_dict
            and hasattr(state_dict[weight_key], "shape")
            and len(state_dict[weight_key].shape) >= 2
        ):
            chall_input_dim = int(state_dict[weight_key].shape[1])
            break
    model = _model_service.create_model(input_dim=chall_input_dim, dp_compatible=chall_dp)
    model.load_state_dict(state_dict)
    model.eval()
    logger.debug("Challenger model v%d loaded and cached (sim=%s).", version, simulation_id[:8])

    with _challenger_cache_lock:
        if len(_challenger_model_cache) >= 8:
            _challenger_model_cache.pop(next(iter(_challenger_model_cache)), None)
        _challenger_model_cache[cache_key] = (model, kid)

    return model


def _clear_challenger_model_cache() -> None:
    with _challenger_cache_lock:
        _challenger_model_cache.clear()


_load_challenger_model.cache_clear = _clear_challenger_model_cache  # type: ignore[attr-defined]  # pyright: ignore[reportFunctionMemberAccess]


_serving_model_cache: dict[str | None, tuple[torch.nn.Module, dict[str, Any]]] = {}
_serving_cache_lock = threading.Lock()


def clear_all_serving_caches() -> None:
    """Clear all process-local model serving caches."""
    global _cached_serving_model, _cached_serving_model_mtime, _cached_serving_model_kid, _cached_serving_model_version
    with _serving_cache_lock:
        _serving_model_cache.clear()
    _cached_serving_model = None
    _cached_serving_model_mtime = 0.0
    _cached_serving_model_kid = None
    _cached_serving_model_version = None
    _clear_challenger_model_cache()


def _get_cached_serving_model_with_ident(
    simulation_id: str | None = None,
) -> tuple[torch.nn.Module, dict[str, Any]]:
    """Resolve, verify, and cache active serving neural network model in memory with identity snapshot.

    Enforces:
    - Authoritative loading through ModelService.get_champion(simulation_id).
    - In-memory snapshot SHA-256 verification and cryptographic provenance checks.
    - Zero unverified global_model.pt direct disk reads.
    - Zero random untrained model synthesis (no fallback weights or checkpoint overwriting).
    - Controlled HTTP 503 / 404 error responses with sanitized details.
    - Fast lightweight champion identity revalidation to preserve sub-millisecond warm serving latency.
    """
    global _cached_serving_model, _cached_serving_model_mtime, _cached_serving_model_kid, _cached_serving_model_version

    # 1. Revalidate active champion identity from manifest
    try:
        active_ident = _registry.get_active_champion_identity(simulation_id=simulation_id)
    except Exception as exc:
        logger.error(
            "Failed to query active champion identity for simulation %s: %s",
            simulation_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serving model unavailable: champion manifest resolution failed.",
        ) from exc

    if active_ident is None:
        with _serving_cache_lock:
            _serving_model_cache.pop(simulation_id, None)
            if _cached_serving_model is not None and (
                simulation_id is None or simulation_id == get_settings().default_simulation_id
            ):
                _cached_serving_model = None

        if simulation_id:
            try:
                versions = _registry.list_versions(simulation_id)
            except Exception:
                versions = []
            if not versions:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No trained models found for simulation ID: {simulation_id}",
                )
            if any(v.get("status") == "champion" for v in versions):
                champ = next((v for v in versions if v.get("status") == "champion"), None)
                if champ:
                    rcpt = champ.get("training_receipt")
                    kid = rcpt.get("key_id") if isinstance(rcpt, dict) else None
                    if kid:
                        from app.application.services.training_provenance_service import (
                            get_default_training_verifier,
                        )

                        verifier = _registry._verifier or get_default_training_verifier()
                        if not verifier.is_key_trusted(kid):
                            raise HTTPException(
                                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="Serving model rejected: model authority or signing key has been revoked.",
                            )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Serving model unavailable: champion authorization or integrity verification failed.",
                )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No verified active champion model artifact found in registry.",
        )

    # 2. Check process-local serving cache
    with _serving_cache_lock:
        cached_entry = _serving_model_cache.get(simulation_id)
        if cached_entry is not None:
            cached_model, cached_ident = cached_entry
            if (
                cached_ident.get("version") == active_ident.get("version")
                and cached_ident.get("sha256") == active_ident.get("sha256")
                and cached_ident.get("generation") == active_ident.get("generation")
            ):
                # Verify issuer key validity if key_id is present
                kid = active_ident.get("issuer_key_id")
                if kid:
                    from app.application.services.training_provenance_service import (
                        get_default_training_verifier,
                    )

                    verifier = _registry._verifier or get_default_training_verifier()
                    if not verifier.is_key_trusted(kid):
                        _serving_model_cache.pop(simulation_id, None)
                        if _cached_serving_model is cached_model:
                            _cached_serving_model = None
                        logger.error(
                            "Serving model rejected for simulation %s: issuer key %s revoked",
                            simulation_id,
                            kid,
                        )
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Serving model rejected: model authority or signing key has been revoked.",
                        )
                _cached_serving_model_version = cached_ident.get("version")
                return cached_model, cached_ident

    # 3. Load genuine champion model via authoritative ModelService
    try:
        model = _model_service.get_champion(
            simulation_id=simulation_id,
            dp_compatible=True,
            registry=_registry,
        )
    except ModelNotAvailableError as exc:
        logger.error(
            "Serving model unavailable for simulation %s: %s",
            simulation_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serving model unavailable: active champion artifact is missing or inaccessible.",
        ) from exc
    except ModelIntegrityError as exc:
        logger.error(
            "Serving model rejected for simulation %s due to integrity failure: %s",
            simulation_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serving model rejected: artifact integrity verification failed.",
        ) from exc
    except ModelAuthorizationError as exc:
        logger.error(
            "Serving model rejected for simulation %s due to authorization failure: %s",
            simulation_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serving model rejected: model authorization verification failed.",
        ) from exc
    except ModelCompatibilityError as exc:
        logger.error(
            "Serving model rejected for simulation %s due to compatibility failure: %s",
            simulation_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serving model unavailable: model architecture or specification is incompatible.",
        ) from exc
    except Exception as exc:
        logger.error(
            "Unexpected failure loading verified champion model for simulation %s: %s",
            simulation_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serving model unavailable due to verification failure.",
        ) from exc

    with _serving_cache_lock:
        _serving_model_cache[simulation_id] = (model, active_ident)
        if simulation_id is None or simulation_id == get_settings().default_simulation_id:
            _cached_serving_model = model
            _cached_serving_model_mtime = time.time()
            _cached_serving_model_kid = active_ident.get("issuer_key_id")
            _cached_serving_model_version = active_ident.get("version")

    return model, active_ident


def _get_cached_serving_model(simulation_id: str | None = None) -> torch.nn.Module:
    """Resolve, verify, and cache active serving neural network model in memory."""
    model, _ = _get_cached_serving_model_with_ident(simulation_id)
    return model



@router.post("/predict", response_model=TransactionPredictResponse)
@api_router.post("/predict", response_model=TransactionPredictResponse)
@limiter.limit("60/minute")
async def predict_transaction(
    request: Request,
    response: Response,
    payload: TransactionPredictRequest,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    caller_tenant: TenantDep = None,
    metered_tenant: str = Depends(enforce_tenant_quota),
) -> TransactionPredictResponse:
    """Evaluate a transaction in real-time.

    Loads the currently active global model (or versioned model of a simulation),
    evaluates fraud probability, computes composite risk score, and
    generates alerts if fraud is suspected.
    """
    settings = get_settings()
    is_production = settings.app_env.lower() in ("production", "prod")

    txn_dict = payload.model_dump()
    if is_production and not settings.feature_store_enabled:
        missing_feats = [f for f in REQUIRED_ML_FEATURES if txn_dict.get(f) is None]
        if missing_feats:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Production feature contract violation: Missing required features: {missing_feats}. "
                    f"Features must be explicitly supplied or observed in online feature store."
                ),
            )
        try:
            preprocess_transaction(txn_dict, strict_categorical=True)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Production categorical validation failed: {exc}",
            )

    principal, effective_tenant = resolve_effective_caller_tenant(
        request=request,
        caller_tenant=caller_tenant,
        requested_bank_id=payload.bank_id,
        action_name="predict_transaction",
    )
    auth_sim_id, ownership = authorize_model_serving(
        principal=principal,
        caller_tenant=effective_tenant,
        simulation_id=payload.simulation_id,
        registry=_registry,
        action_name="predict_transaction",
    )

    bank_id = effective_tenant or payload.bank_id
    if not bank_id:
        if is_production:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Institutional tenant identity is required for production prediction.",
            )
        bank_id = "serving_client"

    txn_id = payload.transaction_id or str(uuid.uuid4())
    customer_ref = payload.customer_id or payload.account_id or "customer_1"
    entity_hash = f"serving:{bank_id}:{customer_ref}"

    txn_dict = payload.model_dump()

    # Online Feature Store: query real customer and merchant historical features if enabled
    if settings.feature_store_enabled:
        merch_id = f"merch_{payload.merchant_category}" if payload.merchant_category else None
        try:
            online_feats = _feature_store.get_online_features(
                [{"customer_id": entity_hash, **({"merchant_id": merch_id} if merch_id else {})}],
                [
                    "rolling_velocity_1h",
                    "avg_amount_24h",
                    "customer_history_score",
                    "account_age_days",
                    "chargeback_count",
                    "merchant_risk_score",
                    "merchant_category",
                ],
                tenant_id=bank_id,
            )
            if online_feats:
                feats = online_feats[0]
                if txn_dict.get("velocity") is None and feats.get("rolling_velocity_1h") is not None:
                    txn_dict["velocity"] = feats["rolling_velocity_1h"]
                if txn_dict.get("customer_history_score") is None and feats.get("customer_history_score") is not None:
                    txn_dict["customer_history_score"] = feats["customer_history_score"]
                if txn_dict.get("account_age_days") is None and feats.get("account_age_days") is not None:
                    txn_dict["account_age_days"] = feats["account_age_days"]
                if txn_dict.get("chargeback_count") is None and feats.get("chargeback_count") is not None:
                    txn_dict["chargeback_count"] = feats["chargeback_count"]
                if txn_dict.get("merchant_risk_score") is None and feats.get("merchant_risk_score") is not None:
                    txn_dict["merchant_risk_score"] = feats["merchant_risk_score"]
                if txn_dict.get("merchant_category") is None and feats.get("merchant_category") is not None:
                    txn_dict["merchant_category"] = feats["merchant_category"]
        except Exception as exc:
            logger.debug("Feature Store online query failed in predict_transaction: %s", exc)

        # Schedule asynchronous feature ingestion — runs after response is sent
        raw_m_risk = txn_dict.get("merchant_risk_score")
        m_risk_val: float = float(raw_m_risk) if raw_m_risk is not None else 0.05
        raw_c_hist = txn_dict.get("customer_history_score")
        c_hist_val: float = float(raw_c_hist) if raw_c_hist is not None else 0.95
        raw_cb = txn_dict.get("chargeback_count")
        cb_val: int = int(raw_cb) if raw_cb is not None else 0
        raw_age = txn_dict.get("account_age_days")
        age_val: int = int(raw_age) if raw_age is not None else 365

        background_tasks.add_task(
            _feature_store.ingest_transaction,
            customer_id=entity_hash,
            amount=payload.transaction_amount,
            merchant_id=merch_id or f"merch_{txn_dict.get('merchant_category', 'general')}",
            merchant_category=str(txn_dict.get("merchant_category") or "grocery"),
            merchant_risk_score=m_risk_val,
            customer_history_score=c_hist_val,
            chargeback_count=cb_val,
            account_age_days=age_val,
            transaction_id=txn_id,
            tenant_id=bank_id,
        )

    # Enforce strict production feature contract vs dev defaults
    if is_production:
        missing_feats = [f for f in REQUIRED_ML_FEATURES if txn_dict.get(f) is None]
        if missing_feats:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Production feature contract violation: Missing required features: {missing_feats}. "
                    f"Features must be explicitly supplied or observed in online feature store."
                ),
            )
        try:
            preprocess_transaction(txn_dict, strict_categorical=True)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Production categorical validation failed: {exc}",
            )
    else:
        for f, default_val in DEV_DEFAULTS.items():
            if txn_dict.get(f) is None:
                txn_dict[f] = default_val

    # 1. Resolve cached active model instance and identity snapshot
    try:
        model, active_ident = _get_cached_serving_model_with_ident(auth_sim_id)
        network = getattr(model, "network", None)
        first_layer = None
        if isinstance(network, (torch.nn.Sequential, torch.nn.ModuleList, list)) and len(network) > 0:
            first_layer = network[0]
        input_dim = int(getattr(first_layer, "in_features", NUM_FEATURES))
        input_tensor = preprocess_transaction(txn_dict, strict_categorical=is_production).to(_model_service.device)
        if input_tensor.shape[1] != input_dim:
            raise ModelCompatibilityError(
                f"Feature tensor dimension mismatch: input has {input_tensor.shape[1]} features, but model expects {input_dim}."
            )

        # Measure Champion Latency
        champ_start = time.perf_counter()
        champ_prob = _eval_model(model, input_tensor)
        champ_latency = (time.perf_counter() - champ_start) * 1000

        # Measure Challenger Latency (Shadow Deployment ONLY)
        chall_prob = None
        chall_latency = None
        challenger_ver = None
        active_entry = None
        if auth_sim_id:
            manifest = _registry._load_manifest(auth_sim_id)
            active_entry = next((e for e in manifest if (e.get("status") in ("champion", "active") and e.get("is_active")) or e.get("status") == "active"), None)
            challenger_entry = next((e for e in manifest if e.get("status") == "challenger"), None)
            if challenger_entry:
                challenger_ver = challenger_entry["version"]
                try:
                    if is_production:
                        validate_production_admission(challenger_entry, context="predict_challenger_serving")

                    chall_model = _load_challenger_model(
                        auth_sim_id, challenger_ver, dp_compatible=True
                    )
                    chall_net = getattr(chall_model, "network", None)
                    chall_first = (
                        chall_net[0]
                        if isinstance(chall_net, (torch.nn.Sequential, torch.nn.ModuleList, list)) and len(chall_net) > 0
                        else None
                    )
                    chall_input_dim = int(getattr(chall_first, "in_features", NUM_FEATURES))

                    chall_tensor = preprocess_transaction(txn_dict, strict_categorical=is_production).to(_model_service.device)
                    if chall_tensor.shape[1] != chall_input_dim:
                        logger.warning(
                            "Challenger tensor dimension %d mismatches model expected dimension %d; skipping shadow evaluation.",
                            chall_tensor.shape[1],
                            chall_input_dim,
                        )
                    else:
                        chall_start = time.perf_counter()
                        chall_prob = _eval_model(chall_model, chall_tensor)
                        chall_latency = (time.perf_counter() - chall_start) * 1000
                except Exception as exc:
                    logger.warning(
                        "Failed to run challenger model version %d: %s",
                        challenger_ver,
                        exc,
                    )
                    chall_prob = None
                    challenger_ver = None

        # Authoritative production prediction must ALWAYS use currently authorized champion.
        # Shadow predictions must NEVER replace champion probabilities in actual production risk scores,
        # decisions, alerts, policy enforcement, or customer responses.
        fraud_prob = champ_prob

        # Log prediction for shadow evaluation and rollback engine
        sim_id_eval = auth_sim_id or "live_prod_v2"
        _eval_engine.log_prediction(
            simulation_id=sim_id_eval,
            transaction_id=txn_id,
            champion_version=active_entry["version"] if active_entry else (active_ident.get("version") if active_ident else 1),
            champion_prob=champ_prob,
            champion_latency_ms=champ_latency,
            challenger_version=challenger_ver,
            challenger_prob=chall_prob,
            challenger_latency_ms=chall_latency,
            routed_to="champion",
            bank_id=bank_id,
        )


    except HTTPException:
        raise
    except ModelCompatibilityError as exc:
        logger.error("Model compatibility error during prediction: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Serving model unavailable: model architecture or dimension is incompatible: {exc}",
        ) from exc
    except ModelExecutionError as exc:
        logger.error("Model execution error during prediction: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Serving model execution error: {exc}",
        ) from exc
    except ValueError as e:
        logger.error("Inference validation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Transaction feature validation error: {e}",
        )
    except Exception as e:
        logger.error("Inference execution failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inference pipeline execution error.",
        )

    # 4. Compute composite risk score
    # Register historical signals context dynamically for the entity hash
    cb_count = txn_dict.get("chargeback_count") or 0
    if cb_count > 0:
        _risk_engine.register_chargeback(entity_hash, min(1.0, cb_count / 10.0), tenant_id=bank_id)
        for _ in range(min(5, cb_count)):
            _risk_engine.register_alert(entity_hash, tenant_id=bank_id)
    _risk_engine.register_baseline(entity_hash, {"mean_amount": 100.0, "std_amount": 50.0}, tenant_id=bank_id)

    risk_score_obj = _risk_engine.score_transaction(
        transaction=txn_dict,
        ml_prediction=fraud_prob,
        entity_hash=entity_hash,
        tenant_id=bank_id,
    )

    score = risk_score_obj.score
    is_fraud_suspected = score >= 600.0
    if risk_score_obj.signals and all(s.weight == 0.0 for s in risk_score_obj.signals):
        risk_level = "UNASSESSED"
    else:
        risk_level = (
            "CRITICAL"
            if score >= 850.0
            else "HIGH"
            if score >= 700.0
            else "MEDIUM"
            if score >= 500.0
            else "LOW"
        )

    # 5. Generate and publish alert in the shared intelligence layer if fraud suspected
    alert_details = None
    if is_fraud_suspected:
        try:
            features_dict = {
                "transaction_id": txn_id,
                "customer_id": payload.customer_id or payload.account_id or customer_ref,
                "merchant_category": txn_dict.get("merchant_category"),
                "country_code": txn_dict.get("country_code"),
                "device_type": txn_dict.get("device_type"),
                "transaction_amount": payload.transaction_amount,
                "merchant_risk_score": txn_dict.get("merchant_risk_score"),
                "customer_history_score": txn_dict.get("customer_history_score"),
                "chargeback_count": cb_count,
                "account_age_days": txn_dict.get("account_age_days"),
            }

            # Generate and persist the alert in Redis
            alerts = _alert_service.generate_alerts(
                bank_id=bank_id,
                transactions=[features_dict],
                predictions=[score / 1000.0],
                threshold=0.01,  # Set low threshold to guarantee alert creation
            )

            if alerts:
                active_alert = alerts[0]
                # Publish anonymized indicator to the shared intelligence layer in background
                background_tasks.add_task(_alert_service.publish_intelligence, active_alert)

                # Generate the SHAP attribution explanation report
                explanation_report = _explainability_service.explain_alert(
                    active_alert,
                    risk_signals=risk_score_obj.signals,
                )

                alert_details = AlertDetails(
                    alert_id=active_alert.id,
                    severity=active_alert.severity.value,
                    status=active_alert.status.value,
                    reason_codes=active_alert.reason_codes,
                    explanation=explanation_report.explanation_text,
                    top_features=active_alert.top_features,
                    risk_factors=active_alert.risk_factors,
                )
        except Exception as e:
            logger.warning("Alert/Explainability pipeline degraded: %s", e)
    elif risk_level == "UNASSESSED":
        try:
            features_dict = {
                "transaction_id": txn_id,
                "customer_id": payload.customer_id or payload.account_id or customer_ref,
                "merchant_category": txn_dict.get("merchant_category"),
                "country_code": txn_dict.get("country_code"),
                "device_type": txn_dict.get("device_type"),
                "transaction_amount": payload.transaction_amount,
                "merchant_risk_score": txn_dict.get("merchant_risk_score"),
                "customer_history_score": txn_dict.get("customer_history_score"),
                "chargeback_count": cb_count,
                "account_age_days": txn_dict.get("account_age_days"),
            }
            unassessed_alert = _alert_service.generate_unassessed_hold_alert(
                bank_id=bank_id,
                transaction=features_dict,
            )
            alert_details = AlertDetails(
                alert_id=unassessed_alert.id,
                severity=unassessed_alert.severity.value,
                status=unassessed_alert.status.value,
                reason_codes=unassessed_alert.reason_codes,
                explanation="Risk assessment unavailable — transaction held for review",
                top_features=[],
                risk_factors=unassessed_alert.risk_factors,
            )
        except Exception as e:
            logger.warning("Unassessed alert generation degraded: %s", e)

    breakdown = [
        SignalBreakdown(
            signal_name=s.signal_name,
            weight=s.weight,
            raw_value=s.raw_value,
            normalized_score=s.normalized_score,
            explanation=s.explanation,
        )
        for s in risk_score_obj.signals
    ]

    # ── Dynamic Policy Rule Evaluation ────────
    policy_action = "HOLD_FOR_REVIEW" if risk_level == "UNASSESSED" else "ALLOW"
    triggered_rules = []
    try:
        from app.application.services.policy_engine import PolicyEngineService

        policy_service = PolicyEngineService()
        active_rules = await policy_service.get_active_rules(session)

        # Context for rule evaluation
        eval_context = {
            "composite_risk_score": score,
            "country_code": payload.country_code,
            "velocity": txn_dict.get("velocity"),
            "transaction_amount": payload.transaction_amount,
            "merchant_category": payload.merchant_category,
            "device_type": payload.device_type,
            "fraud_probability": fraud_prob,
            "bank_id": bank_id,
        }

        for rule in active_rules:
            if policy_service.test_rule(rule.condition, eval_context):
                triggered_rules.append(rule.rule_name)
                if rule.action == "BLOCK_TRANSACTION":
                    policy_action = "BLOCK_TRANSACTION"
    except Exception as exc:
        logger.warning("Dynamic Policy Engine evaluation failed: %s", exc)

    return TransactionPredictResponse(
        transaction_id=txn_id,
        fraud_probability=fraud_prob,
        risk_score=score,
        is_fraud_suspected=is_fraud_suspected or (policy_action == "BLOCK_TRANSACTION"),
        risk_level=risk_level,
        breakdown=breakdown,
        alert_details=alert_details,
        policy_action=policy_action,
        triggered_rules=triggered_rules,
        latency_ms=round((time.perf_counter() - champ_start) * 1000, 2),
    )


@router.post("/predict/batch", response_model=BatchPredictionResponse)
@api_router.post("/predict/batch", response_model=BatchPredictionResponse)
@limiter.limit("30/minute")
async def predict_batch(
    request: Request,
    payload: BatchTransactionPredictRequest,
    session: SessionDep,
    caller_tenant: TenantDep = None,
    metered_tenant: str = Depends(enforce_tenant_quota),
) -> BatchPredictionResponse:
    """Evaluate a batch of transactions in real-time.

    Batch processes up to 1,000 transaction payloads in a single forward pass,
    evaluates risk scores, and returns itemized decisions with batch latency metrics.
    """
    req_bank_id = payload.bank_id
    if not req_bank_id and payload.transactions:
        item_banks = {getattr(t, "bank_id", None) for t in payload.transactions if getattr(t, "bank_id", None)}
        if len(item_banks) == 1:
            req_bank_id = next(iter(item_banks))
        elif len(item_banks) > 1:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Batch contains mixed bank_ids across transactions; all items in a batch must belong to the same institution.",
            )

    principal, effective_tenant = resolve_effective_caller_tenant(
        request=request,
        caller_tenant=caller_tenant,
        requested_bank_id=req_bank_id,
        action_name="predict_batch",
    )
    auth_sim_id, ownership = authorize_model_serving(
        principal=principal,
        caller_tenant=effective_tenant,
        simulation_id=payload.simulation_id,
        registry=_registry,
        action_name="predict_batch",
    )

    settings = get_settings()
    is_production = settings.app_env.lower() in ("production", "prod")

    target_tenant = effective_tenant or req_bank_id
    if not target_tenant:
        if is_production:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Institutional tenant identity is required for production batch prediction.",
            )
        target_tenant = metered_tenant or "serving_client"

    # Enforce item-level bank_id agreement with authorized scope
    for idx, txn_item in enumerate(payload.transactions):
        item_bank = getattr(txn_item, "bank_id", None)
        if item_bank:
            norm_item = item_bank.lower().replace("-", "_")
            norm_target = target_tenant.lower().replace("-", "_")
            if norm_item != norm_target:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Batch item {idx} bank_id '{item_bank}' does not match "
                        f"authorized batch tenant '{target_tenant}'."
                    ),
                )

    from app.application.services.tenant_metering import get_tenant_metering_service

    metering = get_tenant_metering_service()
    additional_count = max(0, len(payload.transactions) - 1)
    if additional_count > 0:
        allowed, reason = metering.acquire_quota(
            target_tenant, "INFERENCE", count=additional_count
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Batch inference quota exceeded: {reason}",
                headers={"Retry-After": "3600", "X-Quota-Exceeded": "true"},
            )


    start_batch = time.perf_counter()
    settings = get_settings()
    is_production = settings.app_env.lower() in ("production", "prod")

    txn_dicts = [t.model_dump() for t in payload.transactions]
    if settings.feature_store_enabled:
        entity_rows = [
            {
                "customer_id": f"serving:{target_tenant}:{t.customer_id or t.account_id or f'customer_{idx+1}'}",
                "merchant_id": f"merch_{t.merchant_category}" if t.merchant_category else None,
            }
            for idx, t in enumerate(payload.transactions)
        ]
        try:
            online_feats = _feature_store.get_online_features(
                entity_rows,
                [
                    "rolling_velocity_1h",
                    "avg_amount_24h",
                    "customer_history_score",
                    "account_age_days",
                    "chargeback_count",
                    "merchant_risk_score",
                    "merchant_category",
                ],
                tenant_id=target_tenant,
            )
            for idx, feats in enumerate(online_feats):
                td = txn_dicts[idx]
                if td.get("velocity") is None and feats.get("rolling_velocity_1h") is not None:
                    td["velocity"] = feats["rolling_velocity_1h"]
                if td.get("customer_history_score") is None and feats.get("customer_history_score") is not None:
                    td["customer_history_score"] = feats["customer_history_score"]
                if td.get("account_age_days") is None and feats.get("account_age_days") is not None:
                    td["account_age_days"] = feats["account_age_days"]
                if td.get("chargeback_count") is None and feats.get("chargeback_count") is not None:
                    td["chargeback_count"] = feats["chargeback_count"]
                if td.get("merchant_risk_score") is None and feats.get("merchant_risk_score") is not None:
                    td["merchant_risk_score"] = feats["merchant_risk_score"]
                if td.get("merchant_category") is None and feats.get("merchant_category") is not None:
                    td["merchant_category"] = feats["merchant_category"]
        except Exception as exc:
            logger.debug("Feature Store online query failed in predict_batch: %s", exc)

    if is_production:
        for idx, td in enumerate(txn_dicts):
            missing_feats = [f for f in REQUIRED_ML_FEATURES if td.get(f) is None]
            if missing_feats:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Production feature contract violation in batch item {idx}: Missing required features: "
                        f"{missing_feats}. Features must be explicitly supplied or observed in online feature store."
                    ),
                )
            try:
                preprocess_transaction(td, strict_categorical=True)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Production categorical validation failed in batch item {idx}: {exc}",
                )
    else:
        for td in txn_dicts:
            for f, default_val in DEV_DEFAULTS.items():
                if td.get(f) is None:
                    td[f] = default_val

    try:
        model, active_ident = _get_cached_serving_model_with_ident(auth_sim_id)
        network = getattr(model, "network", None)
        first_layer = (
            network[0]
            if isinstance(network, (torch.nn.Sequential, torch.nn.ModuleList, list))
            and len(network) > 0
            else None
        )
        input_dim = int(getattr(first_layer, "in_features", NUM_FEATURES))

        tensors = []
        for idx, td in enumerate(txn_dicts):

            t_tensor = preprocess_transaction(td, strict_categorical=is_production)
            if t_tensor.shape[1] != input_dim:
                raise ModelCompatibilityError(
                    f"Feature tensor dimension mismatch in batch item {idx}: input has {t_tensor.shape[1]} features, but model expects {input_dim}."
                )
            tensors.append(t_tensor)

        batch_tensor = torch.cat(tensors, dim=0).to(_model_service.device)
        probs = await asyncio.to_thread(_eval_model_batch, model, batch_tensor)
    except HTTPException:
        raise
    except ModelCompatibilityError as exc:
        logger.error("Model compatibility error during batch inference: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Serving model unavailable: model architecture or dimension is incompatible: {exc}",
        ) from exc
    except ModelExecutionError as exc:
        logger.error("Model execution error during batch inference: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Serving model execution error: {exc}",
        ) from exc
    except ValueError as exc:
        logger.error("Batch feature validation error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Batch feature validation error: {exc}",
        ) from exc
    except Exception as exc:
        logger.error("Batch inference execution failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Batch inference pipeline execution error.",
        )

    items: list[BatchPredictionItem] = []
    fraud_count = 0
    batch_item_latency = round(
        (time.perf_counter() - start_batch) * 1000 / max(1, len(payload.transactions)), 2
    )

    for i, (t_req, prob) in enumerate(zip(payload.transactions, probs)):
        t_dict = txn_dicts[i]
        txn_id = t_req.transaction_id or f"batch_tx_{uuid.uuid4().hex[:8]}"
        e_hash = f"batch:{target_tenant}:{txn_id}"

        cb_cnt = t_dict.get("chargeback_count") or 0
        if cb_cnt > 0:
            _risk_engine.register_chargeback(e_hash, min(1.0, cb_cnt / 10.0), tenant_id=target_tenant)
            for _ in range(min(5, cb_cnt)):
                _risk_engine.register_alert(e_hash, tenant_id=target_tenant)
        _risk_engine.register_baseline(e_hash, {"mean_amount": 100.0, "std_amount": 50.0}, tenant_id=target_tenant)

        risk_obj = _risk_engine.score_transaction(
            transaction=t_dict,
            ml_prediction=prob,
            entity_hash=e_hash,
            tenant_id=target_tenant,
        )
        score = risk_obj.score

        is_fraud = score >= 600.0
        if is_fraud:
            fraud_count += 1

        if risk_obj.signals and all(s.weight == 0.0 for s in risk_obj.signals):
            level = "UNASSESSED"
            decision = "REVIEW"
            policy_action = "HOLD_FOR_REVIEW"
        else:
            level = (
                "CRITICAL"
                if score >= 850.0
                else "HIGH"
                if score >= 700.0
                else "MEDIUM"
                if score >= 500.0
                else "LOW"
            )
            decision = "BLOCK" if score >= 850.0 else "REVIEW" if score >= 600.0 else "ALLOW"
            policy_action = "BLOCK_TRANSACTION" if decision == "BLOCK" else "ALLOW"

        items.append(
            BatchPredictionItem(
                transaction_id=txn_id,
                fraud_probability=round(prob, 4),
                risk_score=round(score, 1),
                decision=decision,
                risk_level=level,
                is_fraud_suspected=is_fraud,
                policy_action=policy_action,
                latency_ms=batch_item_latency,
            )
        )

    total_batch_ms = round((time.perf_counter() - start_batch) * 1000, 2)
    return BatchPredictionResponse(
        total_processed=len(payload.transactions),
        fraud_suspected_count=fraud_count,
        predictions=items,
        batch_latency_ms=total_batch_ms,
    )


@router.post("/predict/explain", response_model=ExplainTransactionResponse)
@api_router.post("/predict/explain", response_model=ExplainTransactionResponse)
@limiter.limit("60/minute")
async def explain_transaction(
    request: Request,
    payload: ExplainTransactionRequest,
    caller_tenant: TenantDep = None,
    metered_tenant: str = Depends(enforce_tenant_quota),
) -> ExplainTransactionResponse:
    """Generate SHAP feature attributions and counterfactual remediation paths for a transaction."""
    principal, effective_tenant = resolve_effective_caller_tenant(
        request=request,
        caller_tenant=caller_tenant,
        requested_bank_id=payload.transaction.bank_id,
        action_name="explain_transaction",
    )
    sim_id_req = getattr(payload, "simulation_id", None) or getattr(payload.transaction, "simulation_id", None)
    auth_sim_id, ownership = authorize_model_serving(
        principal=principal,
        caller_tenant=effective_tenant,
        simulation_id=sim_id_req,
        registry=_registry,
        action_name="explain_transaction",
    )

    start_time = time.perf_counter()
    txn_dict = payload.transaction.model_dump()
    txn_id = payload.transaction_id or payload.transaction.transaction_id or f"tx_{uuid.uuid4().hex[:8]}"

    is_production = _settings.app_env.lower() in ("production", "prod")

    bank_id = effective_tenant or payload.transaction.bank_id
    if not bank_id:
        if is_production:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Institutional tenant identity is required for production explanation.",
            )
        bank_id = "serving_client"

    # Online Feature Store: query real customer and merchant historical features if enabled
    if _settings.feature_store_enabled:
        customer_ref = payload.transaction.customer_id or payload.transaction.account_id or "customer_1"
        entity_hash = f"serving:{bank_id}:{customer_ref}"

        merch_id = f"merch_{payload.transaction.merchant_category}" if payload.transaction.merchant_category else None
        try:
            online_feats = _feature_store.get_online_features(
                [{"customer_id": entity_hash, **({"merchant_id": merch_id} if merch_id else {})}],
                [
                    "rolling_velocity_1h",
                    "avg_amount_24h",
                    "customer_history_score",
                    "account_age_days",
                    "chargeback_count",
                    "merchant_risk_score",
                    "merchant_category",
                ],
                tenant_id=bank_id,
            )
            if online_feats:
                feats = online_feats[0]
                if txn_dict.get("velocity") is None and feats.get("rolling_velocity_1h") is not None:
                    txn_dict["velocity"] = feats["rolling_velocity_1h"]
                if txn_dict.get("customer_history_score") is None and feats.get("customer_history_score") is not None:
                    txn_dict["customer_history_score"] = feats["customer_history_score"]
                if txn_dict.get("account_age_days") is None and feats.get("account_age_days") is not None:
                    txn_dict["account_age_days"] = feats["account_age_days"]
                if txn_dict.get("chargeback_count") is None and feats.get("chargeback_count") is not None:
                    txn_dict["chargeback_count"] = feats["chargeback_count"]
                if txn_dict.get("merchant_risk_score") is None and feats.get("merchant_risk_score") is not None:
                    txn_dict["merchant_risk_score"] = feats["merchant_risk_score"]
                if txn_dict.get("merchant_category") is None and feats.get("merchant_category") is not None:
                    txn_dict["merchant_category"] = feats["merchant_category"]
        except Exception as exc:
            logger.debug("Feature Store query in explain_transaction failed: %s", exc)

    if is_production:
        missing_feats = [f for f in REQUIRED_ML_FEATURES if txn_dict.get(f) is None]
        if missing_feats:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Production feature contract violation: Missing required features: {missing_feats}. "
                    f"Features must be explicitly supplied or observed in online feature store."
                ),
            )
        try:
            preprocess_transaction(txn_dict, strict_categorical=True)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Production categorical validation failed: {exc}",
            )
    else:
        for f, default_val in DEV_DEFAULTS.items():
            if txn_dict.get(f) is None:
                txn_dict[f] = default_val

    try:
        # Resolve active serving model bound to the simulation/tenant
        serving_model = _get_cached_serving_model(auth_sim_id)

        # Offload CPU-heavy SHAP kernel explanation to threadpool to preserve event loop health
        shap_features = await asyncio.to_thread(_explainability_service.compute_shap_values, txn_dict, model=serving_model)

        attributions: list[FeatureAttributionItem] = []
        base_val = 0.50
        predicted_val = 0.50
        explanation_method = payload.method

        for f_item in shap_features:
            name = f_item.get("feature", "unknown")
            contrib = float(f_item.get("contribution", 0.0))
            raw_cand = f_item.get("raw_value")
            if isinstance(raw_cand, (int, float)):
                raw_val = float(raw_cand)
            else:
                raw_val = float(f_item.get("value", 0.0))
            base_val = float(f_item.get("base_value", base_val))
            predicted_val = float(f_item.get("model_output", predicted_val))
            method_used = f_item.get("explanation_method", "shap_kernel_explainer")
            if method_used == "fallback_heuristic":
                explanation_method = "fallback_heuristic"

            direction = "INCREASES_RISK" if contrib > 0 else "DECREASES_RISK"
            desc = (
                f"Feature '{name}' increases fraud probability by {abs(contrib):.2%}"
                if contrib > 0
                else f"Feature '{name}' decreases fraud risk by {abs(contrib):.2%}"
            )
            attributions.append(
                FeatureAttributionItem(
                    feature=name,
                    value=raw_val,
                    contribution=round(contrib, 4),
                    direction=direction,
                    description=desc,
                )
            )

        counterfactual_paths: list[CounterfactualPathItem] = []
        if payload.transaction.transaction_amount > 5000.0:
            counterfactual_paths.append(
                CounterfactualPathItem(
                    feature="transaction_amount",
                    original_value=payload.transaction.transaction_amount,
                    target_value=2500.0,
                    description="Reduce single transfer amount below $5,000 threshold to reduce risk score.",
                )
            )
        if payload.transaction.velocity is not None and payload.transaction.velocity > 5.0:
            counterfactual_paths.append(
                CounterfactualPathItem(
                    feature="velocity",
                    original_value=payload.transaction.velocity,
                    target_value=2.0,
                    description="Throttle customer transaction velocity to under 3 txns/hr.",
                )
            )

        if explanation_method == "fallback_heuristic":
            summary = (
                f"Explanation generated via fallback_heuristic (SHAP unavailable). "
                f"Top contributing risk driver: {attributions[0].feature if attributions else 'none'}."
            )
        else:
            summary = (
                f"Explanation generated via {explanation_method}. "
                f"Top contributing risk driver: {attributions[0].feature if attributions else 'none'}."
            )

        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return ExplainTransactionResponse(
            transaction_id=txn_id,
            method=explanation_method,
            base_value=round(base_val, 4),
            predicted_score=round(predicted_val, 4),
            attributions=attributions,
            summary=summary,
            counterfactual_paths=counterfactual_paths,
            latency_ms=latency_ms,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Explainability pipeline execution failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Explainability execution failed.",
        )


@router.post("/predict/feedback")
@api_router.post("/predict/feedback")
async def submit_transaction_feedback(
    payload: TransactionFeedbackRequest,
    request: Request,
    caller_tenant: TenantDep = None,
) -> dict[str, Any]:
    """Ingest ground truth label feedback for evaluation of Champion/Challenger models."""
    principal, effective_tenant = resolve_effective_caller_tenant(
        request=request,
        caller_tenant=caller_tenant,
        requested_bank_id=None,
        action_name="submit_transaction_feedback",
    )
    auth_sim_id, ownership = authorize_model_serving(
        principal=principal,
        caller_tenant=effective_tenant,
        simulation_id=payload.effective_simulation_id,
        registry=_registry,
        action_name="submit_transaction_feedback",
    )

    target_sim = auth_sim_id or payload.effective_simulation_id or "default"

    # Validate prediction record evidence in evaluation store
    record = _eval_engine.get_prediction_record(
        simulation_id=target_sim,
        transaction_id=payload.transaction_id,
        bank_id=effective_tenant,
    )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prediction for transaction '{payload.transaction_id}' not found in evaluation store.",
        )

    # Verify institutional tenant match against evidence
    is_privileged = bool(principal and principal.is_privileged)
    if not is_privileged and effective_tenant:
        record_bank = record.get("bank_id")
        if record_bank:
            norm_rec = str(record_bank).lower().replace("-", "_")
            norm_eff = str(effective_tenant).lower().replace("-", "_")
            if norm_rec != norm_eff:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Cross-tenant feedback rejected. Prediction '{payload.transaction_id}' belongs to bank '{record_bank}'.",
                )
        else:
            settings = get_settings()
            if settings.app_env.lower() in ("production", "prod"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Prediction evidence lacks authoritative institutional tenant binding.",
                )

    try:
        metrics = _eval_engine.log_feedback(
            simulation_id=target_sim,
            transaction_id=payload.transaction_id,
            actual_label=payload.actual_label,
            bank_id=effective_tenant,
        )
        return {"status": "success", "metrics": metrics}
    except KeyError as e:
        logger.warning("Feedback transaction not found: %s", e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found.",
        )
    except PermissionError as e:
        logger.warning("Feedback authorization mismatch: %s", e)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to process transaction feedback: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record transaction feedback.",
        )


@router.post(
    "/score-transaction",
    response_model=ScoreTransactionResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/score-transaction",
    response_model=ScoreTransactionResponse,
    status_code=status.HTTP_200_OK,
)
@router.post(
    "/transactions/score",
    response_model=ScoreTransactionResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/transactions/score",
    response_model=ScoreTransactionResponse,
    status_code=status.HTTP_200_OK,
)
@router.post(
    "/predict/score",
    response_model=ScoreTransactionResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/predict/score",
    response_model=ScoreTransactionResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def score_transaction(
    request: Request,
    payload: ScoreTransactionRequest,
    x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    caller_tenant: TenantDep = None,
    metered_tenant: str = Depends(enforce_tenant_quota),
) -> ScoreTransactionResponse:
    """Low-Latency Real-Time Risk Decision API providing sub-10ms risk evaluation against the globally trained model."""
    start_time = time.perf_counter()

    settings = get_settings()
    is_production = settings.app_env.lower() in ("production", "prod")
    fs_enabled = getattr(_settings, "feature_store_enabled", False) or settings.feature_store_enabled

    country = payload.country or payload.country_code
    if is_production:
        if country is None or not str(country).strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Production scoring rejected: missing required model features: ['country']. Features must be explicitly supplied or observed in online feature store.",
            )
        if not fs_enabled:
            missing = []
            if payload.merchant_category is None:
                missing.append("merchant_category")
            if payload.device_type is None:
                missing.append("device_type")
            if payload.velocity is None:
                missing.append("velocity")
            if payload.customer_history_score is None:
                missing.append("customer_history_score")
            if payload.chargeback_count is None:
                missing.append("chargeback_count")
            if payload.account_age_days is None:
                missing.append("account_age_days")
            if payload.merchant_risk_score is None:
                missing.append("merchant_risk_score")
            if payload.hour_of_day is None:
                missing.append("hour_of_day")
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Production scoring rejected: missing required model features: {missing}. "
                        f"Features must be explicitly supplied or observed in online feature store."
                    ),
                )

    principal, effective_tenant = resolve_effective_caller_tenant(
        request=request,
        caller_tenant=caller_tenant,
        requested_bank_id=x_bank_id,
        action_name="score_transaction",
    )
    auth_sim_id, ownership = authorize_model_serving(
        principal=principal,
        caller_tenant=effective_tenant,
        simulation_id=payload.simulation_id,
        registry=_registry,
        action_name="score_transaction",
    )

    tenant_scope = effective_tenant or x_bank_id
    if not tenant_scope:
        if is_production:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Institutional tenant identity is required for production transaction scoring.",
            )
        tenant_scope = "serving_client"


    # Initial explicit feature values
    merchant_category = payload.merchant_category
    device_type = payload.device_type
    velocity = payload.velocity
    customer_history_score = payload.customer_history_score
    chargeback_count = payload.chargeback_count
    account_age_days = payload.account_age_days
    merchant_risk_score = payload.merchant_risk_score
    hour_of_day = payload.hour_of_day
    country = payload.country or payload.country_code

    # Online Feature Store: query real customer and merchant historical features if enabled
    fs_enabled = getattr(_settings, "feature_store_enabled", False) or settings.feature_store_enabled
    if fs_enabled:
        try:
            entity_rows = [
                {
                    "customer_id": f"serving:{tenant_scope}:{payload.account_id}",
                    "merchant_id": payload.merchant_id,
                }
            ]
            online_feats = await asyncio.to_thread(
                _feature_store.get_online_features,
                entity_rows,
                [
                    "customer_history_score",
                    "account_age_days",
                    "chargeback_count",
                    "rolling_velocity_1h",
                    "merchant_risk_score",
                    "merchant_category",
                    "device_type",
                    "country_code",
                ],
                tenant_id=tenant_scope,
            )
            if online_feats:
                feats = online_feats[0]
                if customer_history_score is None and feats.get("customer_history_score") is not None:
                    customer_history_score = feats["customer_history_score"]
                if chargeback_count is None and feats.get("chargeback_count") is not None:
                    chargeback_count = feats["chargeback_count"]
                if account_age_days is None and feats.get("account_age_days") is not None:
                    account_age_days = feats["account_age_days"]
                if velocity is None and feats.get("rolling_velocity_1h") is not None:
                    velocity = float(feats["rolling_velocity_1h"])
                if merchant_risk_score is None and feats.get("merchant_risk_score") is not None:
                    merchant_risk_score = float(feats["merchant_risk_score"])
                if merchant_category is None and feats.get("merchant_category") is not None:
                    merchant_category = feats["merchant_category"]
                if device_type is None and feats.get("device_type") is not None:
                    device_type = feats["device_type"]
                if country is None:
                    if feats.get("country_code") is not None:
                        country = str(feats["country_code"]).strip()
                    elif feats.get("country") is not None:
                        country = str(feats["country"]).strip()
        except Exception as exc:
            logger.debug("Feature Store online query failed in score_transaction: %s", exc)

    if is_production:
        # Strict Production Feature Contract: do not fabricate or guess features in production
        missing = []
        if country is None or not country.strip():
            missing.append("country")
        if merchant_category is None:
            missing.append("merchant_category")
        if device_type is None:
            missing.append("device_type")
        if velocity is None:
            missing.append("velocity")
        if customer_history_score is None:
            missing.append("customer_history_score")
        if chargeback_count is None:
            missing.append("chargeback_count")
        if account_age_days is None:
            missing.append("account_age_days")
        if merchant_risk_score is None:
            missing.append("merchant_risk_score")
        if hour_of_day is None:
            missing.append("hour_of_day")
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Production scoring rejected: missing required model features: {missing}. "
                    f"Features must be explicitly supplied or observed in online feature store."
                ),
            )

        # Validate country code format if provided
        if country is not None:
            c_str = country.strip().upper()
            if len(c_str) != 2 or not c_str.isalpha():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Production scoring rejected: invalid country code format '{country}'. Must be ISO 3166-1 alpha-2.",
                )
    else:
        # Non-production demo/development fallback logic: preserve EE backward compatibility default only outside production
        if country is None or not country.strip():
            country = "EE"

        if merchant_category is None:
            _merchant_id_lower = payload.merchant_id.lower()
            if "crypto" in _merchant_id_lower or "bitcoin" in _merchant_id_lower or "exchange" in _merchant_id_lower:
                merchant_category = "crypto"
            elif "gambling" in _merchant_id_lower or "casino" in _merchant_id_lower or "bet" in _merchant_id_lower:
                merchant_category = "gambling"
            elif "wire" in _merchant_id_lower or "transfer" in _merchant_id_lower:
                merchant_category = "wire_transfer"
            elif "jewelry" in _merchant_id_lower or "jewel" in _merchant_id_lower:
                merchant_category = "jewelry"
            elif "grocery" in _merchant_id_lower or "supermarket" in _merchant_id_lower or "rewe" in _merchant_id_lower:
                merchant_category = "grocery"
            else:
                merchant_category = "online_marketplace"

        if merchant_risk_score is None:
            cat_risk_map = {
                "crypto": 0.85,
                "gambling": 0.90,
                "wire_transfer": 0.75,
                "jewelry": 0.60,
                "grocery": 0.03,
                "online_marketplace": 0.45,
            }
            merchant_risk_score = cat_risk_map.get(merchant_category, 0.45)

        if device_type is None:
            device_type = "web_browser"

        if velocity is None:
            velocity = 7.5 if merchant_category in ("crypto", "gambling", "wire_transfer") else 2.0

        if customer_history_score is None:
            customer_history_score = 0.90
        if chargeback_count is None:
            chargeback_count = 0
        if account_age_days is None:
            account_age_days = 365
        if hour_of_day is None:
            hour_of_day = time.gmtime().tm_hour

    # FATF high-risk jurisdiction supplement: countries not in engine's lookup table
    _HIGH_RISK_COUNTRIES = {"KP", "IR", "MM", "SY", "YE", "LY", "SS", "SO", "CF", "ER"}
    _ELEVATED_RISK_COUNTRIES = {"PK", "TZ", "VU", "NG", "HT", "PH", "AO", "RU", "BY"}
    country_upper = country.strip().upper() if country else ""
    is_fatf_blacklist = country_upper in _HIGH_RISK_COUNTRIES
    is_fatf_greylist = country_upper in _ELEVATED_RISK_COUNTRIES
    country_risk_override = (
        0.95 if is_fatf_blacklist
        else 0.72 if is_fatf_greylist
        else None
    )

    txn_dict = {
        "transaction_amount": payload.amount,
        "merchant_category": merchant_category,
        "country_code": country,
        "device_type": device_type,
        "velocity": velocity,
        "hour_of_day": hour_of_day,
        "merchant_risk_score": merchant_risk_score,
        "customer_history_score": customer_history_score,
        "chargeback_count": chargeback_count,
        "account_age_days": account_age_days,
        **({"country_risk_score": country_risk_override} if country_risk_override else {}),
    }

    # 1. Real ML model inference via cached serving neural network
    try:
        serving_model, model_ident = _get_cached_serving_model_with_ident(auth_sim_id)
        input_tensor = preprocess_transaction(txn_dict, strict_categorical=is_production).to(_model_service.device)
        network = getattr(serving_model, "network", None)
        first_layer = (
            network[0]
            if isinstance(network, (torch.nn.Sequential, torch.nn.ModuleList, list)) and len(network) > 0
            else None
        )
        input_dim = int(getattr(first_layer, "in_features", NUM_FEATURES))
        if input_tensor.shape[1] != input_dim:
            raise ModelCompatibilityError(
                f"Feature tensor dimension mismatch: input has {input_tensor.shape[1]} features, but model expects {input_dim}."
            )
        ml_prediction = _eval_model(serving_model, input_tensor)
    except ModelCompatibilityError as exc:
        logger.error("Serving model compatibility error in score_transaction: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Serving model incompatible: {exc}",
        )
    except (ModelAuthorizationError, ModelNotAvailableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Serving model unavailable: {exc}",
        )
    except ModelExecutionError as exc:
        logger.error("Serving model execution error in score_transaction: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Model inference execution failure: {exc}",
        )
    except ValueError as exc:
        # Categorical parsing/validation error from preprocess_transaction
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Production categorical validation failed: {exc}",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Serving model inference failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inference pipeline execution error.",
        )

    # Run composite risk scoring engine with genuine ML confidence
    entity_hash = f"scoring:{tenant_scope}:{payload.account_id}"
    risk_score_obj = _risk_engine.score_transaction(txn_dict, ml_prediction, entity_hash, tenant_id=tenant_scope)
    raw_score = risk_score_obj.score

    # Normalize integer risk score [0, 1000]
    risk_score = max(0, min(1000, round(raw_score)))

    # Hard escalations that override the ML composite score:
    # 1. FATF NCCT blacklisted jurisdiction -> mandatory BLOCK
    if is_fatf_blacklist:
        risk_score = max(risk_score, 950)
    # 2. FATF greylisted jurisdiction -> minimum REVIEW
    elif is_fatf_greylist:
        risk_score = max(risk_score, 350)
    # 3. High-value transactions on high-risk merchants -> REVIEW
    if payload.amount >= 10000.0 and (merchant_risk_score or 0.0) >= 0.35:
        risk_score = max(risk_score, 350)
    # 4. Very large amounts -> always at least REVIEW
    if payload.amount >= 50000.0:
        risk_score = max(risk_score, 320)

    # Determine risk_level
    if risk_score < 300:
        risk_level = "LOW"
    elif risk_score <= 700:
        risk_level = "MEDIUM"
    else:
        risk_level = "HIGH"

    # Determine decision
    if risk_score > 900:
        decision = "BLOCK"
    elif risk_score >= 300:
        decision = "REVIEW"
    else:
        decision = "ALLOW"

    # Model version tag — resolve truth from active_ident snapshot bound to the execution
    ident_ver = model_ident.get("version")
    model_ver = f"v{ident_ver}.0.0" if ident_ver is not None else "unknown"

    # Map internal risk scoring signal names to canonical API / SHAP feature names
    SIGNAL_FEATURE_MAP = {
        "velocity_rules": "merchant_velocity_1h",
        "device_anomaly": "cross_entity_device_link",
        "merchant_reputation": "merchant_risk_score",
        "country_risk": "country_risk",
        "customer_history": "customer_history_score",
        "ml_prediction": "ml_prediction",
        "previous_alerts": "prior_aml_alerts",
        "chargeback_history": "chargeback_history_rate",
        "behavior_anomaly": "behavioral_deviation",
    }

    # Build dynamic explanations from actual risk signals mapped to canonical feature names
    explanations = [
        FeatureContributionItem(
            feature=SIGNAL_FEATURE_MAP.get(s.signal_name, s.signal_name),
            contribution=round(s.weight * s.normalized_score, 3),
        )
        for s in sorted(risk_score_obj.signals, key=lambda x: abs(x.weight * x.normalized_score), reverse=True)[:5]
    ] if risk_score_obj.signals else [
        FeatureContributionItem(feature="composite_risk_score", contribution=round(raw_score / 1000.0, 3)),
        FeatureContributionItem(feature="country_risk", contribution=round(country_risk_override or 0.0, 3)),
    ]

    # Add FATF flag explanation if applicable
    if is_fatf_blacklist:
        explanations.insert(0, FeatureContributionItem(feature="fatf_ncct_blacklist", contribution=0.95))
    elif is_fatf_greylist:
        explanations.insert(0, FeatureContributionItem(feature="fatf_greylist", contribution=0.72))

    # Connected entity risk levels
    device_risk = "HIGH" if risk_score > 700 else ("MEDIUM" if risk_score > 300 else "LOW")
    related_entities = [
        RelatedEntityItem(entity_type="device", risk=device_risk),
    ]

    latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

    return ScoreTransactionResponse(
        risk_score=risk_score,
        risk_level=risk_level,
        decision=decision,
        model_version=model_ver,
        artifact_sha256=model_ident.get("sha256"),
        simulation_id=model_ident.get("simulation_id") or payload.simulation_id,
        explanations=explanations,
        related_entities=related_entities,
        latency_ms=latency_ms,
    )


@router.post(
    "/benchmark/reset",
    status_code=status.HTTP_200_OK,
    summary="Deterministic benchmark state reset",
    include_in_schema=False,
)
async def reset_benchmark_state() -> dict[str, str]:
    """Resets rate limiter storage and tenant metering quotas between benchmark tiers/repetitions.

    Only functional when CFI_BENCHMARK_MODE=1 is set. Returns 404 in production.
    """
    if not is_benchmark_mode():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Benchmark state reset is only available when CFI_BENCHMARK_MODE=1",
        )

    import gc

    from app.application.services.tenant_metering import get_tenant_metering_service

    reset_rate_limiter()
    get_tenant_metering_service().reset_all()
    gc.collect()
    return {"status": "ok", "message": "Benchmark state deterministically reset"}
