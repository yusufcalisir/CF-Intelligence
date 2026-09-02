"""Real-time Serving Bank Client Router.

Simulates the local bank nodes in a distributed federated learning network,
allowing the coordinator to trigger local training and validation over HTTP.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import numpy as np

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.application.services.data_generator import DataGenerator
from app.application.services.model_service import ModelService
from app.config import get_settings
from app.domain.value_objects import ModelWeights

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/bank-client", tags=["bank-client"])

# Initialize singleton model service matching our training context
_settings = get_settings()
_model_service = ModelService(_settings)

# ── Payload Signature Verification ───────────────────────────────────────────
# Maximum age (seconds) of a signed request before it is considered expired.
_SIGNATURE_MAX_AGE_SECONDS = 300


async def verify_payload_signature(request: Request) -> None:
    """Validate HMAC-SHA256 payload signature sent by the coordinator.

    The coordinator signs every outbound REST payload with the shared
    ``payload_signing_secret``.  Bank clients verify the signature to
    guarantee authenticity and reject tampered / replayed requests.

    If the secret is empty (local dev mode), verification is skipped.
    """
    secret = _settings.payload_signing_secret
    if not secret:
        return  # signing disabled in local dev

    signature = request.headers.get("X-Payload-Signature")
    timestamp = request.headers.get("X-Payload-Timestamp")

    if not signature or not timestamp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing payload signature headers.",
        )

    # Replay protection – reject requests older than max age
    import time

    try:
        ts = float(timestamp)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid payload timestamp.",
        )

    if abs(time.time() - ts) > _SIGNATURE_MAX_AGE_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Payload signature expired.",
        )

    # Recompute HMAC over raw body
    import hashlib
    import hmac

    body_bytes = await request.body()
    sign_data = timestamp.encode("utf-8") + b"." + body_bytes
    expected = hmac.new(
        secret.encode("utf-8"),
        sign_data,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid payload signature.",
        )


class BankClientState:
    """In-memory dataset cache for the local bank container."""

    def __init__(self) -> None:
        self.bank_id: str | None = None
        self.X_train: np.ndarray | None = None
        self.y_train: np.ndarray | None = None
        self.X_test: np.ndarray | None = None
        self.y_test: np.ndarray | None = None

    def clear(self) -> None:
        self.bank_id = None
        self.X_train = None
        self.y_train = None
        self.X_test = None
        self.y_test = None


# Global in-memory client state
_client_state = BankClientState()


# ── Pydantic Request & Response Schemas ──────────────────────────────────────


class BankInitializeRequest(BaseModel):
    bank_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique bank node identifier",
    )
    num_transactions: int = Field(
        ...,
        ge=10,
        le=1_000_000,
        description="Number of synthetic transactions to generate [10, 1M]",
    )
    seed: int | None = Field(
        42,
        ge=0,
        le=2**32 - 1,
        description="RNG seed for reproducible data generation",
    )


class ModelWeightsSchema(BaseModel):
    layer_shapes: list[list[int]]
    flat_weights: list[float]


class BankTrainRequest(BaseModel):
    weights: ModelWeightsSchema
    learning_rate: float = Field(..., gt=0.0, le=1.0)
    batch_size: int = Field(..., ge=1, le=8192)
    epochs: int = Field(..., ge=1, le=200)
    enable_dp: bool
    dp_epsilon: float = Field(..., gt=0.0, le=100.0)
    dp_delta: float = Field(..., gt=0.0, le=1.0)
    dp_max_grad_norm: float = Field(..., gt=0.0, le=100.0)
    dp_mode: str = Field(
        "opacus",
        max_length=32,
        pattern=r"^[a-zA-Z_]+$",
    )
    fedprox_mu: float = Field(0.0, ge=0.0, le=10.0)
    moon_mu: float = Field(0.0, ge=0.0, le=10.0)
    moon_temperature: float = Field(0.5, gt=0.0, le=10.0)
    prev_local_weights: ModelWeightsSchema | None = None


class BankTrainResponse(BaseModel):
    weights: ModelWeightsSchema
    num_samples: int
    loss: float
    actual_epsilon: float | None = None


class BankEvaluateRequest(BaseModel):
    weights: ModelWeightsSchema


class BankEvaluateResponse(BaseModel):
    loss: float
    num_samples: int
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    auc_roc: float
    confusion_matrix: list[list[int]]
    roc_fpr: list[float]
    roc_tpr: list[float]
    roc_thresholds: list[float]


# ── API Endpoint Implementations ─────────────────────────────────────────────


@router.post(
    "/initialize", response_model=dict[str, Any], dependencies=[Depends(verify_payload_signature)]
)
async def initialize_dataset(payload: BankInitializeRequest) -> dict[str, Any]:
    """Deterministically generate and cache the dataset partition for this bank client."""
    try:
        generator = DataGenerator(seed=payload.seed or 42)
        # Use default partitions sizes for other banks to construct target partition
        sizes = {"bank_a": 5000, "bank_b": 3000, "bank_c": 2000}
        sizes[payload.bank_id] = payload.num_transactions

        logger.info(
            "Initializing dataset for %s with sizes %s",
            payload.bank_id,
            sizes,
        )
        datasets = generator.generate_bank_datasets(
            bank_a_size=sizes["bank_a"],
            bank_b_size=sizes["bank_b"],
            bank_c_size=sizes["bank_c"],
        )

        if payload.bank_id not in datasets:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid bank_id '{payload.bank_id}'. Must be bank_a, bank_b, or bank_c.",
            )

        df, labels = datasets[payload.bank_id]
        X = DataGenerator.encode_features(df)
        y = labels.values

        # Perform train-test split
        from sklearn.model_selection import train_test_split

        try:
            X_train, X_test, y_train, y_test = train_test_split(
                X,
                y,
                test_size=0.2,
                random_state=42,
                stratify=y,  # type: ignore[arg-type]
            )
        except Exception:
            # Fallback for small class samples sizes
            X_train, X_test, y_train, y_test = train_test_split(
                X,
                y,
                test_size=0.2,
                random_state=42,
            )

        _client_state.bank_id = payload.bank_id
        _client_state.X_train = X_train
        _client_state.y_train = y_train
        _client_state.X_test = X_test
        _client_state.y_test = y_test

        return {
            "status": "initialized",
            "bank_id": payload.bank_id,
            "train_samples": len(X_train),
            "test_samples": len(X_test),
        }

    except Exception as exc:
        logger.error("Dataset generation failed for bank %s: %s", payload.bank_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate datasets: {exc}",
        )


@router.post(
    "/train", response_model=BankTrainResponse, dependencies=[Depends(verify_payload_signature)]
)
async def train_local_weights(payload: BankTrainRequest) -> BankTrainResponse:
    """Train the model locally on the bank client cached dataset partition."""
    if _client_state.X_train is None or _client_state.y_train is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bank client datasets not initialized. Call /initialize first.",
        )

    # Convert Pydantic layer_shapes to list of tuples expected by model service
    converted_shapes = [tuple(shape) for shape in payload.weights.layer_shapes]
    input_weights = ModelWeights(
        layer_shapes=converted_shapes,
        flat_weights=payload.weights.flat_weights,
    )

    use_opacus_dp = payload.enable_dp and payload.dp_mode == "opacus"

    prev_weights = None
    if payload.prev_local_weights:
        prev_shapes = [tuple(shape) for shape in payload.prev_local_weights.layer_shapes]
        prev_weights = ModelWeights(
            layer_shapes=prev_shapes,
            flat_weights=payload.prev_local_weights.flat_weights,
        )

    try:
        # Load weights into local neural structure
        local_model = _model_service.create_model(dp_compatible=use_opacus_dp)
        local_model = _model_service.set_parameters(local_model, input_weights)

        actual_epsilon = None
        if use_opacus_dp:
            local_model, loss_hist, actual_epsilon = _model_service.train_local_with_opacus(
                local_model,
                _client_state.X_train,
                _client_state.y_train,
                target_epsilon=payload.dp_epsilon,
                target_delta=payload.dp_delta,
                max_grad_norm=payload.dp_max_grad_norm,
                epochs=payload.epochs,
                learning_rate=payload.learning_rate,
                batch_size=payload.batch_size,
                fedprox_mu=payload.fedprox_mu,
                moon_mu=payload.moon_mu,
                moon_temperature=payload.moon_temperature,
                global_weights=input_weights,
                prev_local_weights=prev_weights,
            )
        else:
            local_model, loss_hist, _ = _model_service.train_local(
                local_model,
                _client_state.X_train,
                _client_state.y_train,
                epochs=payload.epochs,
                learning_rate=payload.learning_rate,
                batch_size=payload.batch_size,
                fedprox_mu=payload.fedprox_mu,
                moon_mu=payload.moon_mu,
                moon_temperature=payload.moon_temperature,
                global_weights=input_weights,
                prev_local_weights=prev_weights,
            )

        updated_weights = _model_service.get_parameters(local_model)
        final_loss = loss_hist[-1] if loss_hist else 0.0

        schema_weights = ModelWeightsSchema(
            layer_shapes=[list(shape) for shape in updated_weights.layer_shapes],
            flat_weights=updated_weights.flat_weights,
        )

        return BankTrainResponse(
            weights=schema_weights,
            num_samples=len(_client_state.X_train),
            loss=final_loss,
            actual_epsilon=actual_epsilon,
        )

    except Exception as exc:
        logger.error("Local training failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Local training session failed: {exc}",
        )


@router.post(
    "/evaluate",
    response_model=BankEvaluateResponse,
    dependencies=[Depends(verify_payload_signature)],
)
async def evaluate_global_weights(payload: BankEvaluateRequest) -> BankEvaluateResponse:
    """Evaluate the global weights on this bank client local test partition."""
    if _client_state.X_test is None or _client_state.y_test is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bank client datasets not initialized. Call /initialize first.",
        )

    converted_shapes = [tuple(shape) for shape in payload.weights.layer_shapes]
    input_weights = ModelWeights(
        layer_shapes=converted_shapes,
        flat_weights=payload.weights.flat_weights,
    )

    try:
        # Load parameters and run evaluation metrics
        model = _model_service.create_model(dp_compatible=False)
        model = _model_service.set_parameters(model, input_weights)

        eval_result = cast(
            "dict[str, Any]",
            _model_service.evaluate(
                model,
                _client_state.X_test,
                _client_state.y_test,
            ),
        )

        return BankEvaluateResponse(
            loss=eval_result["loss"],
            num_samples=len(_client_state.X_test),
            accuracy=eval_result["accuracy"],
            precision=eval_result["precision"],
            recall=eval_result["recall"],
            f1_score=eval_result["f1_score"],
            auc_roc=eval_result["auc_roc"],
            confusion_matrix=eval_result["confusion_matrix"],
            roc_fpr=eval_result["roc_fpr"],
            roc_tpr=eval_result["roc_tpr"],
            roc_thresholds=eval_result["roc_thresholds"],
        )

    except Exception as exc:
        logger.error("Local evaluation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Local evaluation failed: {exc}",
        )
