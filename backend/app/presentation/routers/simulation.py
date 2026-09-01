"""Simulation API endpoints.

Handles creating, listing, and retrieving simulation runs.
Simulation execution runs in background threads within the web process.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.application.schemas.simulation import (
    BankComparisonResponse,
    BankResponse,
    ComparisonResponse,
    DataProfileResponse,
    MetricsResponse,
    SimulationConfigRequest,
    SimulationCreateResponse,
    SimulationDetailResponse,
    SimulationSummaryResponse,
)
from app.domain.enums import PrivacyMechanism, SimulationStatus
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.rate_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/simulations", tags=["simulations"])


# ── Redis-backed stores ───────────────────────────
_simulation_results = RedisStore("sim_results")
_simulation_events = RedisStore("sim_events")


@router.post(
    "",
    response_model=SimulationCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("10/minute")
async def create_simulation(
    request: Request,
    config: SimulationConfigRequest,
) -> SimulationCreateResponse:
    """Start a new federated learning simulation.

    Runs the simulation in a background thread within the web process.
    Poll GET /simulations/{id} for progress.
    """
    import threading
    import uuid

    simulation_id = str(uuid.uuid4())

    # Build config dict
    config_dict = {
        "num_rounds": config.num_rounds,
        "local_epochs": config.local_epochs,
        "learning_rate": config.learning_rate,
        "batch_size": config.batch_size,
        "min_clients_per_round": config.min_clients_per_round,
        "enable_latency_simulation": config.enable_latency_simulation,
        "latency_range_ms": (config.latency_min_ms, config.latency_max_ms),
        "enable_dropout_simulation": config.enable_dropout_simulation,
        "dropout_probability": config.dropout_probability,
        "enable_reconnect_simulation": config.enable_reconnect_simulation,
        "enable_differential_privacy": config.privacy_mechanism
        in (
            PrivacyMechanism.DIFFERENTIAL_PRIVACY,
            PrivacyMechanism.BOTH,
        ),
        "dp_epsilon": config.dp_epsilon,
        "dp_epsilon_limit": config.dp_epsilon_limit,
        "dp_delta": config.dp_delta,
        "dp_max_grad_norm": config.dp_max_grad_norm,
        "dp_mode": config.dp_mode,
        "enable_secure_aggregation": config.privacy_mechanism
        in (
            PrivacyMechanism.SECURE_AGGREGATION,
            PrivacyMechanism.BOTH,
        ),
        "dataset": getattr(config, "dataset", "synthetic"),
        "bank_a_transactions": config.bank_a_transactions,
        "bank_b_transactions": config.bank_b_transactions,
        "bank_c_transactions": config.bank_c_transactions,
        "aggregation_method": config.aggregation_method,
        "fl_engine_type": config.fl_engine_type,
        "enable_poisoning_simulation": config.enable_poisoning_simulation,
        "poisoning_bank_id": config.poisoning_bank_id,
        "poisoning_scale": config.poisoning_scale,
        "fedprox_mu": config.fedprox_mu,
        "moon_mu": config.moon_mu,
        "moon_temperature": config.moon_temperature,
        "fedopt_server_lr": config.fedopt_server_lr,
        "fedopt_beta1": config.fedopt_beta1,
        "fedopt_beta2": config.fedopt_beta2,
        "fedopt_tau": config.fedopt_tau,
        "enable_bias_mitigation": config.enable_bias_mitigation,
        "fairness_lambda": config.fairness_lambda,
        "hardware_isolation_mode": config.hardware_isolation_mode,
        "enable_streaming_gnn": config.enable_streaming_gnn,
    }

    # Store pending status
    _simulation_results.set(
        simulation_id,
        {
            "id": simulation_id,
            "status": SimulationStatus.PENDING.value,
            "config": config_dict,
            "current_round": 0,
            "total_rounds": config.num_rounds,
            "banks": [],
            "rounds": [],
        },
    )

    # Run simulation in a background thread (no Celery worker needed)
    thread = threading.Thread(
        target=_run_simulation_in_process,
        args=(simulation_id, config_dict),
        daemon=True,
    )
    thread.start()

    logger.info("Started in-process simulation %s", simulation_id)

    return SimulationCreateResponse(
        id=simulation_id,
        status=SimulationStatus.PENDING,
        message=f"Simulation started in-process. ID: {simulation_id}",
    )


@router.get("", response_model=list[SimulationSummaryResponse])
async def list_simulations() -> list[SimulationSummaryResponse]:
    """List all simulation runs."""
    # Results are updated in-place by background threads

    summaries = []
    for sim in _simulation_results.list_values():
        summaries.append(
            SimulationSummaryResponse(
                id=sim["id"],
                status=SimulationStatus(sim["status"]),
                current_round=sim.get("current_round", 0),
                total_rounds=sim.get("total_rounds", 10),
                progress_pct=_calc_progress(sim),
                created_at=sim.get("created_at", "2026-01-01T00:00:00Z"),
                completed_at=sim.get("completed_at"),
                duration_seconds=sim.get("duration_seconds"),
            )
        )

    return summaries


@router.get("/{simulation_id}", response_model=SimulationDetailResponse)
async def get_simulation(simulation_id: str) -> SimulationDetailResponse:
    """Get full simulation details including metrics."""

    sim = _simulation_results.get(simulation_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")

    # Build bank responses
    bank_responses = []
    for bank_data in sim.get("banks", []):
        bank_resp = BankResponse(
            id=bank_data["id"],
            name=bank_data["name"],
            tier=bank_data["tier"],
            fraud_ratio=bank_data["fraud_ratio"],
            num_transactions=bank_data["num_transactions"],
            status=bank_data.get("status", "active"),
            local_metrics=_build_metrics_response(bank_data.get("local_metrics")),
            federated_metrics=_build_metrics_response(bank_data.get("federated_metrics")),
            improvement=bank_data.get("improvement"),
            data_profile=_build_profile_response(bank_data.get("data_profile")),
            contribution_score=bank_data.get("contribution_score", 0.0),
            quarantined=bank_data.get("quarantined", False),
        )
        bank_responses.append(bank_resp)

    config = sim.get("config", {})

    return SimulationDetailResponse(
        id=sim["id"],
        status=SimulationStatus(sim["status"]),
        config=SimulationConfigRequest(
            num_rounds=config.get("num_rounds", 10),
            local_epochs=config.get("local_epochs", 3),
            learning_rate=config.get("learning_rate", 0.001),
            batch_size=config.get("batch_size", 64),
            min_clients_per_round=config.get("min_clients_per_round", 2),
            enable_latency_simulation=config.get("enable_latency_simulation", False),
            latency_min_ms=config.get("latency_range_ms", (50, 500))[0]
            if isinstance(config.get("latency_range_ms"), (list, tuple))
            else 50,
            latency_max_ms=config.get("latency_range_ms", (50, 500))[1]
            if isinstance(config.get("latency_range_ms"), (list, tuple))
            else 500,
            enable_dropout_simulation=config.get("enable_dropout_simulation", False),
            dropout_probability=config.get("dropout_probability", 0.2),
            enable_reconnect_simulation=config.get("enable_reconnect_simulation", True),
            privacy_mechanism=(
                PrivacyMechanism.BOTH
                if config.get("enable_differential_privacy")
                and config.get("enable_secure_aggregation")
                else PrivacyMechanism.DIFFERENTIAL_PRIVACY
                if config.get("enable_differential_privacy")
                else PrivacyMechanism.SECURE_AGGREGATION
                if config.get("enable_secure_aggregation")
                else PrivacyMechanism.NONE
            ),
            dp_epsilon=config.get("dp_epsilon", 1.0),
            dp_delta=config.get("dp_delta", 1e-5),
            dp_max_grad_norm=config.get("dp_max_grad_norm", 1.0),
            dp_mode=config.get("dp_mode", "post_hoc"),
            bank_a_transactions=config.get("bank_a_transactions", 50000),
            bank_b_transactions=config.get("bank_b_transactions", 30000),
            bank_c_transactions=config.get("bank_c_transactions", 20000),
            aggregation_method=config.get("aggregation_method", "fed_avg_weighted"),
            fl_engine_type=config.get("fl_engine_type", "custom"),
            enable_poisoning_simulation=config.get("enable_poisoning_simulation", False),
            poisoning_bank_id=config.get("poisoning_bank_id", "bank_c"),
            poisoning_scale=config.get("poisoning_scale", 5.0),
            hardware_isolation_mode=config.get("hardware_isolation_mode", "none"),
            enable_streaming_gnn=config.get("enable_streaming_gnn", False),
            enable_web3_settlement=config.get("enable_web3_settlement", False),
            settlement_currency=config.get("settlement_currency", "wCBDC"),
            smart_contract_address=config.get(
                "smart_contract_address", "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
            ),
        ),
        current_round=sim.get("current_round", 0),
        total_rounds=sim.get("total_rounds", 10),
        progress_pct=_calc_progress(sim),
        created_at=sim.get("created_at", "2026-01-01T00:00:00Z"),
        started_at=sim.get("started_at"),
        completed_at=sim.get("completed_at"),
        duration_seconds=sim.get("duration_seconds"),
        error_message=sim.get("error_message"),
        banks=bank_responses,
        rounds=[],  # Rounds are in the training router
        tee_mrenclave=sim.get("tee_mrenclave"),
        tee_mrsigner=sim.get("tee_mrsigner"),
        tee_attestation_signature=sim.get("tee_attestation_signature"),
        fhe_poly_degree=sim.get("fhe_poly_degree"),
        fhe_noise_bound=sim.get("fhe_noise_bound"),
        fhe_key_id=sim.get("fhe_key_id"),
        streaming_gnn_node_count=sim.get("streaming_gnn_node_count", 0),
        streaming_gnn_edge_count=sim.get("streaming_gnn_edge_count", 0),
        streaming_gnn_loss_history=sim.get("streaming_gnn_loss_history", []),
        settlement_tx_hash=sim.get("settlement_tx_hash"),
        settlement_block_number=sim.get("settlement_block_number"),
        settlement_status=sim.get("settlement_status"),
        on_chain_payouts=sim.get("on_chain_payouts", []),
    )


@router.get("/{simulation_id}/comparison", response_model=ComparisonResponse)
async def get_comparison(simulation_id: str) -> ComparisonResponse:
    """Get local vs federated comparison for all banks."""

    sim = _simulation_results.get(simulation_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")

    if sim["status"] != SimulationStatus.COMPLETED.value:
        raise HTTPException(status_code=400, detail="Simulation not yet completed")

    bank_comparisons = []
    total_improvement: dict[str, float] = {}

    for bank_data in sim.get("banks", []):
        local = bank_data.get("local_metrics")
        federated = bank_data.get("federated_metrics")
        if not local or not federated:
            continue

        local_resp = _build_metrics_response(local)
        fed_resp = _build_metrics_response(federated)
        if local_resp is None or fed_resp is None:
            continue

        improvement = bank_data.get("improvement", {})
        bank_comparisons.append(
            BankComparisonResponse(
                bank_id=bank_data["id"],
                bank_name=bank_data["name"],
                local_metrics=local_resp,
                federated_metrics=fed_resp,
                improvement=improvement,
            )
        )

        for k, v in improvement.items():
            total_improvement[k] = total_improvement.get(k, 0) + v

    n = len(bank_comparisons) or 1
    avg_improvement = {k: round(v / n, 4) for k, v in total_improvement.items()}

    return ComparisonResponse(
        simulation_id=simulation_id,
        banks=bank_comparisons,
        aggregate_improvement=avg_improvement,
    )


# ── Helpers ─────────────────────────────────────


def _run_simulation_in_process(simulation_id: str, config_dict: dict) -> None:
    """Run the full simulation pipeline in a background thread.

    Updates ``_simulation_results`` in-place so the polling endpoints
    can return real-time progress without Celery or Redis.
    """
    from dataclasses import asdict
    from typing import Any

    from app.application.services.data_generator import DataGenerator
    from app.application.services.fl_engine import FederatedLearningEngine
    from app.application.services.metrics_service import MetricsService
    from app.application.services.model_service import ModelService
    from app.application.services.privacy_service import PrivacyService
    from app.application.services.simulation_service import SimulationService
    from app.config import get_settings
    from app.domain.value_objects import SimulationConfig

    settings = get_settings()
    logger.info("Background simulation %s starting", simulation_id)

    try:
        # Mark as running
        sim = _simulation_results.get(simulation_id)
        if sim:
            sim["status"] = SimulationStatus.GENERATING_DATA.value
            _simulation_results.set(simulation_id, sim)

        config = SimulationConfig(**config_dict)

        model_service = ModelService(settings)
        privacy_service = PrivacyService()
        fl_engine = FederatedLearningEngine(settings, model_service, privacy_service)
        data_generator = DataGenerator()
        metrics_service = MetricsService()

        simulation_service = SimulationService(
            settings=settings,
            simulation_repo=None,
            bank_repo=None,
            metrics_repo=None,
            data_generator=data_generator,
            fl_engine=fl_engine,
            metrics_service=metrics_service,
            model_service=model_service,
        )

        # Progress callback: updates in-memory state and event log
        # NOTE: simulation_service creates SimulationRun with its own uuid, so sim_id
        # passed by the service may differ from our simulation_id.  We always look up
        # by our own simulation_id (the key stored in _simulation_results).
        def progress_cb(_sim_id: str, event_type: str, data: dict[str, Any]) -> None:
            sim = _simulation_results.get(simulation_id)
            if sim:
                if event_type == "status":
                    status_val = data.get("status")
                    if status_val:
                        if hasattr(status_val, "value"):
                            sim["status"] = status_val.value
                        else:
                            sim["status"] = str(status_val)
                    logger.info(
                        "Sim %s status -> %s: %s",
                        simulation_id,
                        sim["status"],
                        data.get("message", ""),
                    )
                elif event_type in ("round_start", "round_complete"):
                    sim["current_round"] = data.get("round", sim.get("current_round", 0))
                    sim["status"] = SimulationStatus.TRAINING_FEDERATED.value
                elif event_type == "banks_generated":
                    sim["banks"] = data.get("banks", [])
                elif event_type == "completed":
                    sim["status"] = SimulationStatus.COMPLETED.value
                elif event_type == "error":
                    sim["status"] = SimulationStatus.FAILED.value
                    sim["error_message"] = data.get("error", "Simulation failed.")

                # Keep progress_pct fresh for polling endpoints
                sim["progress_pct"] = _calc_progress(sim)
                _simulation_results.set(simulation_id, sim)

            # Store every event so the training router can serve them
            _simulation_events.push_list(simulation_id, {"event_type": event_type, "data": data})

        simulation = simulation_service.run_simulation(
            config=config,
            progress_callback=progress_cb,
        )

        # Serialize and store results
        result: dict[str, Any] = {
            "id": simulation_id,
            "status": simulation.status.value,
            "current_round": simulation.current_round,
            "total_rounds": simulation.total_rounds,
            "created_at": simulation.created_at.isoformat() if simulation.created_at else None,
            "started_at": simulation.started_at.isoformat() if simulation.started_at else None,
            "completed_at": simulation.completed_at.isoformat()
            if simulation.completed_at
            else None,
            "duration_seconds": simulation.duration_seconds,
            "error_message": simulation.error_message,
            "banks": [],
            "tee_mrenclave": simulation.tee_mrenclave,
            "tee_mrsigner": simulation.tee_mrsigner,
            "tee_attestation_signature": simulation.tee_attestation_signature,
            "fhe_poly_degree": simulation.fhe_poly_degree,
            "fhe_noise_bound": simulation.fhe_noise_bound,
            "fhe_key_id": simulation.fhe_key_id,
            "streaming_gnn_node_count": simulation.streaming_gnn_node_count,
            "streaming_gnn_edge_count": simulation.streaming_gnn_edge_count,
            "streaming_gnn_loss_history": simulation.streaming_gnn_loss_history,
            "settlement_tx_hash": simulation.settlement_tx_hash,
            "settlement_block_number": simulation.settlement_block_number,
            "settlement_status": simulation.settlement_status,
            "on_chain_payouts": simulation.on_chain_payouts,
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
            bank_dict["contribution_score"] = float(getattr(bank, "contribution_score", 0.0))
            bank_dict["quarantined"] = bool(getattr(bank, "quarantined", False))
            result["banks"].append(bank_dict)

        # Preserve config in the stored result
        result["config"] = config_dict
        _simulation_results.set(simulation_id, result)

        logger.info(
            "Background simulation %s completed: %s", simulation_id, simulation.status.value
        )

    except Exception as exc:
        import traceback

        tb = traceback.format_exc()
        logger.exception("Background simulation %s failed", simulation_id)
        sim = _simulation_results.get(simulation_id) or {}
        sim["status"] = SimulationStatus.FAILED.value
        # Surface the real error to the frontend so it can be debugged
        sim["error_message"] = f"{type(exc).__name__}: {exc}\n{tb[-500:]}"
        _simulation_results.set(simulation_id, sim)


def _calc_progress(sim: dict) -> float:
    status = sim.get("status")
    if status == SimulationStatus.COMPLETED.value:
        return 100.0
    if status == SimulationStatus.FAILED.value:
        return 100.0
    if status == SimulationStatus.EVALUATING.value:
        return 95.0

    total = sim.get("total_rounds", 10)
    current = sim.get("current_round", 0)

    if status == SimulationStatus.GENERATING_DATA.value:
        return 5.0
    if status == SimulationStatus.TRAINING_LOCAL.value:
        return 15.0

    # For training_federated, scale from 15% to 90%
    if total == 0:
        return 15.0
    fed_progress = 15.0 + (current / total) * 75.0
    return min(90.0, fed_progress)


def _build_metrics_response(data: dict | None) -> MetricsResponse | None:
    if not data:
        return None
    return MetricsResponse(
        accuracy=data.get("accuracy", 0),
        precision=data.get("precision", 0),
        recall=data.get("recall", 0),
        f1_score=data.get("f1_score", 0),
        auc_roc=data.get("auc_roc", 0),
        loss=data.get("loss", 0),
        confusion_matrix=data.get("confusion_matrix", [[0, 0], [0, 0]]),
        roc_fpr=data.get("roc_fpr", []),
        roc_tpr=data.get("roc_tpr", []),
        roc_thresholds=data.get("roc_thresholds", []),
        feature_importance=data.get("feature_importance", {}),
        disparate_impact=data.get("disparate_impact", 1.0),
        equal_opportunity_diff=data.get("equal_opportunity_diff", 0.0),
        protected_selection_rate=data.get("protected_selection_rate", 1.0),
        reference_selection_rate=data.get("reference_selection_rate", 1.0),
    )


def _build_profile_response(data: dict | None) -> DataProfileResponse | None:
    if not data:
        return None
    return DataProfileResponse(**data)


@router.get("/{simulation_id}/ai-act-report")
async def get_ai_act_report(simulation_id: str) -> dict:
    """Retrieve the generated EU AI Act Compliance Report JSON log."""
    import hashlib
    import json
    import os
    from datetime import datetime, timezone

    from app.infrastructure.storage.storage_utils import get_storage_dir

    storage_dir = get_storage_dir()
    report_path = os.path.join(storage_dir, f"ai_act_compliance_report_{simulation_id}.json")
    if os.path.exists(report_path):
        try:
            with open(report_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read compliance report file: {exc}",
            ) from exc

    # ── Synthetic fallback: deterministic demo report seeded from simulation_id ──
    # Avoids 404 on HF Spaces (ephemeral filesystem) and on fresh deployments
    # before any training run has been completed.
    seed = int(hashlib.sha256(simulation_id.encode()).hexdigest(), 16)
    acc = 0.92 + (seed % 7) / 100.0
    loss = 0.08 - (seed % 5) / 1000.0
    epsilon = 1.0 + (seed % 4) / 2.0
    di_ratio = 0.921 + (seed % 6) / 1000.0
    eq_opp = 0.032 + (seed % 4) / 1000.0
    score = round(min(1.0, 0.87 + (seed % 12) / 100.0), 4)
    timestamp = datetime.now(timezone.utc).isoformat()  # noqa: UP017

    return {
        "simulation_id": simulation_id,
        "report_type": "EU_AI_ACT_COMPLIANCE",
        "generated_at": timestamp,
        "note": "Demo report — run a federated training simulation to generate a real report.",
        "regulation_version": "EU AI Act 2024/1689",
        "system_metadata": {
            "system_name": "CF-Intelligence Federated Fraud Detection",
            "high_risk_classification": "HIGH_RISK_SYSTEM",
            "intended_purpose": "Cross-bank privacy-preserving fraud detection via Federated Learning",
        },
        "training_summary": {
            "federated_rounds_completed": 5,
            "participating_banks": ["bank_a", "bank_b", "bank_c"],
            "aggregation_strategy": "FedAvg",
            "privacy_mechanism": "Differential Privacy (Gaussian)",
            "privacy_budget_epsilon": epsilon,
            "privacy_budget_delta": 1e-5,
            "final_global_accuracy": acc,
            "final_global_loss": loss,
        },
        "bias_audit": {
            "disparate_impact_ratio": di_ratio,
            "equal_opportunity_difference": eq_opp,
            "eeoc_80_percent_rule": "PASSED" if di_ratio >= 0.8 else "FAILED",
            "overall_bias_status": "LOW_RISK",
        },
        "article_compliance": {
            "overall_status": "COMPLIANT",
            "compliance_score": score,
            "clauses": [
                {"clause": "Article 10 (Data & Governance)", "status": "PASSED"},
                {"clause": "Article 13 (Transparency)", "status": "PASSED"},
                {"clause": "Article 14 (Human Oversight)", "status": "PASSED"},
                {"clause": "Article 15 (Accuracy and Robustness)", "status": "PASSED"},
            ],
        },
        "compliance_certification": {
            "eu_ai_act_compliance_score": score,
            "audit_sign_off_status": "APPROVED_BY_SYSTEM",
        },
    }
