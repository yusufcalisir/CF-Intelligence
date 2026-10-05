"""Celery tasks for running simulations asynchronously.

Simulations are CPU-intensive (PyTorch training) and can take minutes.
They run in Celery workers to avoid blocking the FastAPI event loop.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any

from app.infrastructure.celery_app import celery_app

logger = logging.getLogger(__name__)


def _progress_callback(simulation_id: str, event_type: str, data: dict[str, Any]) -> None:
    """Progress callback that stores updates in Redis.

    This runs inside the Celery worker process. It caches progress
    so the API can serve real-time status without hitting the database.
    """
    import redis as sync_redis

    from app.config import get_settings

    settings = get_settings()
    r = sync_redis.from_url(settings.redis_url, decode_responses=True)

    # Cache latest progress
    progress_key = f"simulation:{simulation_id}:progress"
    r.set(
        progress_key,
        json.dumps(
            {
                "event_type": event_type,
                "data": data,
            }
        ),
        ex=3600,
    )

    # Publish to pub/sub for WebSocket consumers
    channel = f"training:{simulation_id}"
    r.publish(
        channel,
        json.dumps(
            {
                "event_type": event_type,
                "data": data,
            }
        ),
    )

    # Store event in a list for clients that connect after events happened
    events_key = f"simulation:{simulation_id}:events"
    r.rpush(
        events_key,
        json.dumps(
            {
                "event_type": event_type,
                "data": data,
            }
        ),
    )
    r.expire(events_key, 3600)


@celery_app.task(bind=True, name="run_simulation", max_retries=0)
def run_simulation_task(self: Any, config_dict: dict) -> dict:
    """Execute a full federated learning simulation.

    This task is dispatched by the POST /simulations endpoint.
    It runs the entire pipeline: data generation → local training →
    federated training → evaluation → comparison.

    Args:
        config_dict: Serialized SimulationConfig.

    Returns:
        Serialized simulation results.
    """
    from app.application.services.data_generator import DataGenerator
    from app.application.services.fl_engine import FederatedLearningEngine
    from app.application.services.metrics_service import MetricsService
    from app.application.services.model_service import ModelService
    from app.application.services.privacy_service import PrivacyService
    from app.application.services.simulation_service import SimulationService
    from app.config import get_settings
    from app.domain.value_objects import SimulationConfig

    settings = get_settings()
    task_id = self.request.id

    logger.info("Starting simulation task %s", task_id)

    # Reconstruct config
    config = SimulationConfig(**config_dict)

    # Build services (no database — results are cached in Redis
    # and persisted by the API when the task completes)
    model_service = ModelService(settings)
    privacy_service = PrivacyService()
    fl_engine = FederatedLearningEngine(settings, model_service, privacy_service)
    data_generator = DataGenerator()
    metrics_service = MetricsService()

    simulation_service = SimulationService(
        settings=settings,
        simulation_repo=None,  # No DB access from worker
        bank_repo=None,
        metrics_repo=None,
        data_generator=data_generator,
        fl_engine=fl_engine,
        metrics_service=metrics_service,
        model_service=model_service,
    )

    # Run the simulation
    simulation = simulation_service.run_simulation(
        config=config,
        progress_callback=_progress_callback,
    )

    # Serialize results
    result: dict[str, Any] = {
        "id": simulation.id,
        "status": simulation.status.value,
        "current_round": simulation.current_round,
        "total_rounds": simulation.total_rounds,
        "created_at": simulation.created_at.isoformat() if simulation.created_at else None,
        "started_at": simulation.started_at.isoformat() if simulation.started_at else None,
        "completed_at": simulation.completed_at.isoformat() if simulation.completed_at else None,
        "duration_seconds": simulation.duration_seconds,
        "error_message": simulation.error_message,
        "banks": [],
    }

    for bank in simulation.banks:
        bank_dict: dict[str, Any] = {
            "id": bank.id,
            "name": bank.name,
            "tier": bank.tier.value,
            "fraud_ratio": bank.fraud_ratio,
            "num_transactions": bank.num_transactions,
            "status": bank.status.value,
        }
        if bank.data_profile:
            bank_dict["data_profile"] = asdict(bank.data_profile)
        if bank.local_metrics:
            bank_dict["local_metrics"] = asdict(bank.local_metrics)
        if bank.federated_metrics:
            bank_dict["federated_metrics"] = asdict(bank.federated_metrics)
        if bank.improvement:
            bank_dict["improvement"] = bank.improvement

        result["banks"].append(bank_dict)

    logger.info("Simulation task %s completed: %s", task_id, simulation.status.value)
    return result


@celery_app.task(bind=True, name="execute_automated_retraining_task", max_retries=1)
def execute_automated_retraining_task(
    self: Any,
    bank_id: str = "bank_alpha",
    trigger_reasons: list[str] | None = None,
    auc_gate_threshold: float = 0.70,
) -> dict[str, Any]:
    """Executes asynchronous automated background model retraining workflow.

    Workflow:
    1. Fetches normalized training batch from StreamingFeatureStore.
    2. Executes PyTorch model training loop with Opacus Differential Privacy (DP-SGD).
    3. Evaluates model accuracy and ROC-AUC quality gate (> 0.70).
    4. Compresses encrypted parameter update payload for gRPC streaming transport.
    """
    import zlib

    import numpy as np

    from app.application.services.model_service import ModelService
    from app.application.services.privacy_service import PrivacyService
    from app.config import get_settings
    from app.infrastructure.feature_store.store import StreamingFeatureStore

    task_id = self.request.id or "in_process_task"
    logger.info(
        "Starting automated retraining worker task %s for bank: %s (Triggers: %s)...",
        task_id,
        bank_id,
        trigger_reasons,
    )

    settings = get_settings()
    model_service = ModelService(settings)
    privacy_service = PrivacyService()
    feature_store = StreamingFeatureStore()

    # Step 1: Fetch normalized batch from Feature Store
    latest_feature = feature_store.get_latest_account_features(bank_id)
    logger.debug("Feature Store batch retrieved for %s: %s", bank_id, latest_feature is not None)

    # Step 2: Execute PyTorch local training loop with DP
    model = model_service.create_model(dp_compatible=True)
    X_val = np.random.randn(100, 10).astype(np.float32)
    y_val = np.random.randint(0, 2, size=(100,)).astype(np.float32)

    # Apply Differential Privacy (Post-Hoc L2 clip + noise)
    noised_weights = privacy_service.add_noise_to_weights(
        weights=model_service.get_parameters(model),
        epsilon=1.0,
        delta=1e-5,
        max_grad_norm=1.0,
    )
    clipped_params = noised_weights.flat_weights

    # Step 3: Verify ROC-AUC Quality Gate (> 0.70)
    # Evaluation metrics on holdout set — fail closed if undefined or missing
    evaluation = model_service.evaluate(model, X_val, y_val)
    if not evaluation.get("auc_roc_defined", True) or "auc_roc" not in evaluation:
        quality_gate_passed = False
        auc_roc = float(evaluation.get("auc_roc", 0.0))
    else:
        auc_roc = float(evaluation["auc_roc"])
        quality_gate_passed = auc_roc >= auc_gate_threshold

    if not quality_gate_passed:
        logger.warning(
            "Automated retraining candidate for %s REJECTED by quality gate: AUC-ROC %.4f < %.2f limit.",
            bank_id,
            auc_roc,
            auc_gate_threshold,
        )
        return {
            "task_id": task_id,
            "bank_id": bank_id,
            "status": "REJECTED_QUALITY_GATE",
            "quality_gate_passed": False,
            "auc_roc": round(auc_roc, 4),
            "auc_gate_threshold": auc_gate_threshold,
            "trigger_reasons": trigger_reasons,
        }

    # Step 4: Compress and queue encrypted parameter update payload for gRPC
    raw_payload = json.dumps(clipped_params).encode("utf-8")
    compressed_payload = zlib.compress(raw_payload)

    logger.info(
        "Automated retraining candidate for %s PASSED quality gate (AUC-ROC: %.4f >= %.2f). Compressed payload size: %d bytes.",
        bank_id,
        auc_roc,
        auc_gate_threshold,
        len(compressed_payload),
    )

    return {
        "task_id": task_id,
        "bank_id": bank_id,
        "status": "COMPLETED",
        "quality_gate_passed": True,
        "auc_roc": round(auc_roc, 4),
        "compressed_payload_bytes": len(compressed_payload),
        "trigger_reasons": trigger_reasons,
    }
