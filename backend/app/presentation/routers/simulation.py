"""Simulation API endpoints.

Handles creating, listing, and retrieving simulation runs.
Simulation execution runs in background threads within the web process.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from app.application.schemas.simulation import (
    AIActReportResponse,
    BankComparisonResponse,
    BankResponse,
    ComparisonResponse,
    DataProfileResponse,
    MetricsResponse,
    POCPresetsResponse,
    POCReplayRequest,
    SimulationConfigRequest,
    SimulationCreateResponse,
    SimulationDetailResponse,
    SimulationStatusResponse,
    SimulationStopRequest,
    SimulationStopResponse,
    SimulationSummaryResponse,
    TrainingRoundResponse,
)
from app.application.services.multi_bank_simulator import get_multi_bank_simulator
from app.domain.enums import PrivacyMechanism, SimulationStatus
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.rate_limiter import limiter
from app.presentation.websockets.manager import training_ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/simulations", tags=["simulations"])
api_router = APIRouter(prefix="/v1/simulations", tags=["simulations"])
singular_router = APIRouter(prefix="/api/v1/simulation", tags=["simulation"])
singular_api_router = APIRouter(prefix="/v1/simulation", tags=["simulation"])


# ── Redis-backed stores ───────────────────────────
_simulation_results = RedisStore("sim_results")
_simulation_events = RedisStore("sim_events")
_stop_events: dict[str, threading.Event] = {}
_stop_events_lock = threading.Lock()


def _seed_canonical_simulation() -> None:
    """Seed default canonical baseline simulation ('sim_fed_01') if not present.

    Ensures that fresh deployments (e.g. Hugging Face Spaces ephemeral environments)
    and dashboard entry routes have immediate access to baseline federated metrics,
    training rounds, and EU AI Act compliance telemetry without returning 404s.
    """
    if _simulation_results.get("sim_fed_01"):
        return

    sim_id = "sim_fed_01"
    now_iso = "2026-09-28T08:00:00Z"
    completed_iso = "2026-09-28T08:02:15Z"

    canonical_banks = [
        {
            "id": "bank_a",
            "name": "Bank Alpha",
            "tier": "Tier 1",
            "fraud_ratio": 0.015,
            "num_transactions": 50000,
            "status": "active",
            "local_metrics": {
                "accuracy": 0.965,
                "precision": 0.88,
                "recall": 0.82,
                "f1_score": 0.85,
                "auc_roc": 0.820,
                "loss": 0.32,
                "confusion_matrix": [[49200, 50], [135, 615]],
                "roc_fpr": [0.0, 0.05, 0.1, 0.2, 0.5, 1.0],
                "roc_tpr": [0.0, 0.65, 0.78, 0.86, 0.95, 1.0],
                "roc_thresholds": [1.0, 0.8, 0.6, 0.4, 0.2, 0.0],
            },
            "federated_metrics": {
                "accuracy": 0.985,
                "precision": 0.94,
                "recall": 0.91,
                "f1_score": 0.925,
                "auc_roc": 0.935,
                "loss": 0.16,
                "confusion_matrix": [[49230, 20], [67, 683]],
                "roc_fpr": [0.0, 0.02, 0.05, 0.1, 0.3, 1.0],
                "roc_tpr": [0.0, 0.78, 0.89, 0.94, 0.98, 1.0],
                "roc_thresholds": [1.0, 0.8, 0.6, 0.4, 0.2, 0.0],
            },
            "improvement": {"auc": 0.115, "f1_score": 0.075, "recall": 0.090},
            "contribution_score": 0.94,
            "quarantined": False,
            "data_profile": {
                "bank_name": "Bank Alpha",
                "num_transactions": 50000,
                "fraud_ratio": 0.015,
                "mean_transaction_amount": 142.50,
                "std_transaction_amount": 312.0,
                "top_merchant_categories": ["retail", "online_shopping", "groceries"],
                "top_countries": ["US", "GB", "DE"],
                "mean_account_age_days": 420.5,
                "mean_velocity": 2.4,
            },
        },
        {
            "id": "bank_b",
            "name": "Bank Beta",
            "tier": "Tier 2",
            "fraud_ratio": 0.022,
            "num_transactions": 30000,
            "status": "active",
            "local_metrics": {
                "accuracy": 0.958,
                "precision": 0.86,
                "recall": 0.80,
                "f1_score": 0.83,
                "auc_roc": 0.812,
                "loss": 0.35,
                "confusion_matrix": [[29300, 40], [132, 528]],
                "roc_fpr": [0.0, 0.06, 0.12, 0.22, 0.55, 1.0],
                "roc_tpr": [0.0, 0.62, 0.75, 0.84, 0.93, 1.0],
                "roc_thresholds": [1.0, 0.8, 0.6, 0.4, 0.2, 0.0],
            },
            "federated_metrics": {
                "accuracy": 0.982,
                "precision": 0.93,
                "recall": 0.90,
                "f1_score": 0.915,
                "auc_roc": 0.928,
                "loss": 0.17,
                "confusion_matrix": [[29320, 20], [66, 594]],
                "roc_fpr": [0.0, 0.02, 0.06, 0.12, 0.35, 1.0],
                "roc_tpr": [0.0, 0.76, 0.87, 0.93, 0.97, 1.0],
                "roc_thresholds": [1.0, 0.8, 0.6, 0.4, 0.2, 0.0],
            },
            "improvement": {"auc": 0.116, "f1_score": 0.085, "recall": 0.100},
            "contribution_score": 0.91,
            "quarantined": False,
            "data_profile": {
                "bank_name": "Bank Beta",
                "num_transactions": 30000,
                "fraud_ratio": 0.022,
                "mean_transaction_amount": 118.40,
                "std_transaction_amount": 245.0,
                "top_merchant_categories": ["travel", "dining", "entertainment"],
                "top_countries": ["DE", "FR", "NL"],
                "mean_account_age_days": 380.2,
                "mean_velocity": 3.1,
            },
        },
        {
            "id": "bank_c",
            "name": "Bank Gamma",
            "tier": "Tier 3",
            "fraud_ratio": 0.038,
            "num_transactions": 20000,
            "status": "active",
            "local_metrics": {
                "accuracy": 0.942,
                "precision": 0.84,
                "recall": 0.78,
                "f1_score": 0.81,
                "auc_roc": 0.795,
                "loss": 0.38,
                "confusion_matrix": [[19200, 40], [167, 593]],
                "roc_fpr": [0.0, 0.07, 0.15, 0.25, 0.6, 1.0],
                "roc_tpr": [0.0, 0.58, 0.72, 0.82, 0.91, 1.0],
                "roc_thresholds": [1.0, 0.8, 0.6, 0.4, 0.2, 0.0],
            },
            "federated_metrics": {
                "accuracy": 0.978,
                "precision": 0.92,
                "recall": 0.89,
                "f1_score": 0.905,
                "auc_roc": 0.919,
                "loss": 0.19,
                "confusion_matrix": [[19215, 25], [83, 677]],
                "roc_fpr": [0.0, 0.03, 0.07, 0.14, 0.38, 1.0],
                "roc_tpr": [0.0, 0.74, 0.85, 0.92, 0.96, 1.0],
                "roc_thresholds": [1.0, 0.8, 0.6, 0.4, 0.2, 0.0],
            },
            "improvement": {"auc": 0.124, "f1_score": 0.095, "recall": 0.110},
            "contribution_score": 0.88,
            "quarantined": False,
            "data_profile": {
                "bank_name": "Bank Gamma",
                "num_transactions": 20000,
                "fraud_ratio": 0.038,
                "mean_transaction_amount": 95.80,
                "std_transaction_amount": 198.0,
                "top_merchant_categories": ["crypto", "gaming", "wire_transfer"],
                "top_countries": ["TR", "CH", "LU"],
                "mean_account_age_days": 290.0,
                "mean_velocity": 4.2,
            },
        },
    ]

    sim_doc = {
        "id": sim_id,
        "status": SimulationStatus.COMPLETED.value,
        "is_canonical_reference": True,
        "provenance": "CANONICAL_BENCHMARK_REFERENCE",
        "execution_mode": "REFERENCE_RUN",
        "tee_is_hardware_backed": False,
        "tee_driver_mode": "CANONICAL_BENCHMARK_ATTESTATION",
        "current_round": 10,
        "total_rounds": 10,
        "progress_pct": 100.0,
        "created_at": now_iso,
        "started_at": now_iso,
        "completed_at": completed_iso,
        "duration_seconds": 135.0,
        "error_message": None,
        "config": {
            "num_rounds": 10,
            "local_epochs": 3,
            "learning_rate": 0.001,
            "batch_size": 64,
            "min_clients_per_round": 3,
            "enable_latency_simulation": True,
            "latency_range_ms": (50, 250),
            "enable_dropout_simulation": False,
            "dropout_probability": 0.0,
            "enable_reconnect_simulation": True,
            "enable_differential_privacy": True,
            "enable_secure_aggregation": True,
            "dp_epsilon": 1.0,
            "dp_delta": 1e-5,
            "dp_max_grad_norm": 1.0,
            "dp_mode": "rdp",
            "bank_a_transactions": 50000,
            "bank_b_transactions": 30000,
            "bank_c_transactions": 20000,
            "aggregation_method": "fed_avg_weighted",
            "fl_engine_type": "custom",
            "enable_poisoning_simulation": False,
            "hardware_isolation_mode": "intel_sgx",
            "enable_streaming_gnn": True,
            "enable_web3_settlement": True,
            "settlement_currency": "wCBDC",
            "smart_contract_address": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
        },
        "banks": canonical_banks,
        "tee_mrenclave": "b4f8c2e14d9b72dd3f01ae56c820194857361284950372615483920174628391",
        "tee_mrsigner": "a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
        "tee_attestation_signature": "0x9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b",
        "fhe_poly_degree": 8192,
        "fhe_noise_bound": 3.2e-6,
        "fhe_key_id": "ckks_key_sim_fed_01",
        "streaming_gnn_node_count": 1284,
        "streaming_gnn_edge_count": 4592,
        "streaming_gnn_loss_history": [0.45, 0.38, 0.31, 0.26, 0.22, 0.19, 0.16, 0.14, 0.13, 0.11],
        "settlement_tx_hash": "0x8d5c41f92e624c8872f2e5a4bb10b64f9cb3897103ecf459e951079d86a60db2",
        "settlement_block_number": 18920412,
        "settlement_status": "CONFIRMED",
        "on_chain_payouts": [
            {
                "bank_id": "bank_a",
                "bank_name": "Bank Alpha (Retail)",
                "wallet_address": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
                "shapley_score": 0.42,
                "shapley_basis_points": 4200,
                "share_percent": 42.0,
                "amount": 4200.0,
                "payout_usd": 4200.0,
                "payout_wei": "4200000000000000000000",
                "currency": "wCBDC",
                "is_quarantined": False,
                "status": "DISTRIBUTED",
            },
            {
                "bank_id": "bank_b",
                "bank_name": "Bank Beta (Commercial)",
                "wallet_address": "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
                "shapley_score": 0.28,
                "shapley_basis_points": 2800,
                "share_percent": 28.0,
                "amount": 2800.0,
                "payout_usd": 2800.0,
                "payout_wei": "2800000000000000000000",
                "currency": "wCBDC",
                "is_quarantined": False,
                "status": "DISTRIBUTED",
            },
            {
                "bank_id": "bank_c",
                "bank_name": "Bank Gamma (Cross-Border)",
                "wallet_address": "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc",
                "shapley_score": 0.16,
                "shapley_basis_points": 1600,
                "share_percent": 16.0,
                "amount": 1600.0,
                "payout_usd": 1600.0,
                "payout_wei": "1600000000000000000000",
                "currency": "wCBDC",
                "is_quarantined": False,
                "status": "DISTRIBUTED",
            },
        ],
    }

    _simulation_results.set(sim_id, sim_doc)

    # Seed 10 completed training round events for /training/{simulation_id}/rounds
    round_losses = [0.68, 0.58, 0.50, 0.44, 0.38, 0.32, 0.27, 0.23, 0.19, 0.16]
    round_aucs = [0.810, 0.842, 0.865, 0.883, 0.898, 0.911, 0.920, 0.927, 0.932, 0.935]

    for r_idx in range(1, 11):
        loss_val = round_losses[r_idx - 1]
        auc_val = round_aucs[r_idx - 1]
        round_payload = {
            "event_type": "round_complete",
            "data": {
                "round": r_idx,
                "total": 10,
                "loss": loss_val,
                "auc": auc_val,
                "per_bank_auc": {
                    "bank_a": round(auc_val + 0.005, 4),
                    "bank_b": round(auc_val - 0.003, 4),
                    "bank_c": round(auc_val - 0.008, 4),
                },
                "per_bank_loss": {
                    "bank_a": round(loss_val - 0.01, 4),
                    "bank_b": round(loss_val + 0.005, 4),
                    "bank_c": round(loss_val + 0.015, 4),
                },
                "participants": ["bank_a", "bank_b", "bank_c"],
                "dropped": [],
                "duration_ms": 12500.0 + r_idx * 100.0,
                "privacy_budget": round(r_idx * 0.1, 2),
                "feature_importance": {
                    "transaction_amount": 0.32,
                    "velocity": 0.28,
                    "merchant_risk_score": 0.18,
                    "account_age_days": 0.12,
                    "chargeback_count": 0.10,
                },
                "canary_info": {"status": "HEALTHY", "divergence_score": 0.012},
            },
        }
        _simulation_events.push_list(sim_id, round_payload)

    logger.info("Canonical simulation 'sim_fed_01' initialized with 10 completed training rounds.")


# Automatically seed only when explicitly configured or in test environments
if os.environ.get("CF_SEED_CANONICAL_BENCHMARK", "0").lower() in ("1", "true"):
    _seed_canonical_simulation()


@router.post(
    "",
    response_model=SimulationCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@router.post(
    "/start",
    response_model=SimulationCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@api_router.post(
    "",
    response_model=SimulationCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@api_router.post(
    "/start",
    response_model=SimulationCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@singular_router.post(
    "",
    response_model=SimulationCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@singular_router.post(
    "/start",
    response_model=SimulationCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@singular_api_router.post(
    "",
    response_model=SimulationCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@singular_api_router.post(
    "/start",
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
    Poll GET /simulations/{id} or GET /simulation/{id}/status for progress.
    """
    simulation_id = str(uuid.uuid4())
    with _stop_events_lock:
        if len(_stop_events) > 500:
            for k in list(_stop_events.keys())[:-200]:
                _stop_events.pop(k, None)
        _stop_events[simulation_id] = threading.Event()

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
            "is_canonical_reference": False,
            "provenance": "LIVE_ORCHESTRATED_RUN",
            "execution_mode": "LIVE_RUNTIME",
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
@api_router.get("", response_model=list[SimulationSummaryResponse])
@singular_router.get("", response_model=list[SimulationSummaryResponse])
@singular_api_router.get("", response_model=list[SimulationSummaryResponse])
async def list_simulations() -> list[SimulationSummaryResponse]:
    """List all simulation runs."""
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
                is_canonical_reference=sim.get("is_canonical_reference", False),
                provenance=sim.get("provenance", "LIVE_ORCHESTRATED_RUN"),
                execution_mode=sim.get("execution_mode", "LIVE_RUNTIME"),
            )
        )
    return summaries


@router.post("/stop", response_model=SimulationStopResponse)
@api_router.post("/stop", response_model=SimulationStopResponse)
@singular_router.post("/stop", response_model=SimulationStopResponse)
@singular_api_router.post("/stop", response_model=SimulationStopResponse)
async def stop_simulation_body(
    payload: SimulationStopRequest,
) -> SimulationStopResponse:
    """Gracefully terminate a running simulation via JSON body."""
    return await stop_simulation(simulation_id=payload.simulation_id, reason=payload.reason)


@router.get("/{simulation_id}/status", response_model=SimulationStatusResponse)
@api_router.get("/{simulation_id}/status", response_model=SimulationStatusResponse)
@singular_router.get("/{simulation_id}/status", response_model=SimulationStatusResponse)
@singular_api_router.get("/{simulation_id}/status", response_model=SimulationStatusResponse)
async def get_simulation_status(
    simulation_id: str = Path(..., min_length=1, description="Simulation run identifier"),
) -> SimulationStatusResponse:
    """Get lightweight progress status and execution phase of a simulation."""
    sim = _simulation_results.get(simulation_id)
    if not sim and simulation_id == "sim_fed_01":
        _seed_canonical_simulation()
        sim = _simulation_results.get(simulation_id)
    if not sim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation '{simulation_id}' not found",
        )
    return SimulationStatusResponse(
        id=sim["id"],
        status=SimulationStatus(sim["status"]),
        current_round=sim.get("current_round", 0),
        total_rounds=sim.get("total_rounds", 10),
        progress_pct=_calc_progress(sim),
        error_message=sim.get("error_message"),
    )


@router.post("/{simulation_id}/stop", response_model=SimulationStopResponse)
@api_router.post("/{simulation_id}/stop", response_model=SimulationStopResponse)
@singular_router.post("/{simulation_id}/stop", response_model=SimulationStopResponse)
@singular_api_router.post("/{simulation_id}/stop", response_model=SimulationStopResponse)
async def stop_simulation(
    simulation_id: str = Path(..., min_length=1, description="Simulation run identifier"),
    reason: str | None = Query(default=None, max_length=255, description="Optional cancellation reason"),
) -> SimulationStopResponse:
    """Gracefully terminate a running federated learning simulation."""
    sim = _simulation_results.get(simulation_id)
    if not sim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation '{simulation_id}' not found",
        )
    current_status = sim.get("status")
    if current_status in (
        SimulationStatus.COMPLETED.value,
        SimulationStatus.FAILED.value,
        SimulationStatus.STOPPED.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Simulation '{simulation_id}' is already in terminal state '{current_status}'.",
        )

    # Signal stop event to background thread
    with _stop_events_lock:
        stop_event = _stop_events.get(simulation_id)
    if stop_event:
        stop_event.set()

    now_iso = datetime.now(UTC).isoformat()
    sim["status"] = SimulationStatus.STOPPED.value
    sim["completed_at"] = now_iso
    sim["error_message"] = reason or "Simulation stopped gracefully by operator."
    sim["progress_pct"] = 100.0
    _simulation_results.set(simulation_id, sim)

    # Broadcast stop event
    stop_payload = {
        "event_type": "simulation_stopped",
        "data": {
            "simulation_id": simulation_id,
            "reason": reason or "Operator termination request",
            "stopped_at": now_iso,
        },
    }
    _simulation_events.push_list(simulation_id, stop_payload)
    training_ws_manager.broadcast_to_room_sync(f"simulation:{simulation_id}", stop_payload)
    training_ws_manager.broadcast_to_room_sync("simulation:live_prod_v2", stop_payload)

    return SimulationStopResponse(
        simulation_id=simulation_id,
        status=SimulationStatus.STOPPED,
        message=f"Simulation '{simulation_id}' successfully stopped.",
        stopped_at=now_iso,
    )


@router.get("/{simulation_id}/rounds", response_model=list[TrainingRoundResponse])
@api_router.get("/{simulation_id}/rounds", response_model=list[TrainingRoundResponse])
@singular_router.get("/{simulation_id}/rounds", response_model=list[TrainingRoundResponse])
@singular_api_router.get("/{simulation_id}/rounds", response_model=list[TrainingRoundResponse])
async def get_simulation_rounds(
    simulation_id: str = Path(..., min_length=1, description="Simulation run identifier"),
) -> list[TrainingRoundResponse]:
    """Retrieve all completed training rounds for a simulation."""
    sim = _simulation_results.get(simulation_id)
    if not sim and simulation_id == "sim_fed_01":
        _seed_canonical_simulation()
        sim = _simulation_results.get(simulation_id)
    if not sim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation '{simulation_id}' not found",
        )

    events = _simulation_events.get_list(simulation_id)
    rounds: list[TrainingRoundResponse] = []
    for event in events:
        if event.get("event_type") == "round_complete":
            data = event.get("data", {})
            rounds.append(
                TrainingRoundResponse(
                    round_number=data.get("round", 0),
                    total_rounds=data.get("total", sim.get("total_rounds", 10)),
                    global_loss=float(data.get("loss", 0.0)),
                    auc=float(data.get("auc", 0.0)),
                    per_bank_auc=data.get("per_bank_auc", {}),
                    per_bank_loss=data.get("per_bank_loss", {}),
                    participating_banks=data.get("participants", []),
                    dropped_banks=data.get("dropped", []),
                    duration_ms=float(data.get("duration_ms", 0.0)),
                    privacy_budget=float(data.get("privacy_budget", 0.0)),
                    feature_importance=data.get("feature_importance", {}),
                    canary_info=data.get("canary_info", {}),
                )
            )
    return rounds


@router.get("/{simulation_id}/comparison", response_model=ComparisonResponse)
@api_router.get("/{simulation_id}/comparison", response_model=ComparisonResponse)
@singular_router.get("/{simulation_id}/comparison", response_model=ComparisonResponse)
@singular_api_router.get("/{simulation_id}/comparison", response_model=ComparisonResponse)
async def get_comparison(simulation_id: str) -> ComparisonResponse:
    """Get local vs federated comparison for all banks."""

    sim = _simulation_results.get(simulation_id)
    if not sim and simulation_id == "sim_fed_01":
        _seed_canonical_simulation()
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


@router.get("/{simulation_id}", response_model=SimulationDetailResponse)
@api_router.get("/{simulation_id}", response_model=SimulationDetailResponse)
@singular_router.get("/{simulation_id}", response_model=SimulationDetailResponse)
@singular_api_router.get("/{simulation_id}", response_model=SimulationDetailResponse)
async def get_simulation(simulation_id: str) -> SimulationDetailResponse:
    """Get full simulation details including metrics."""

    sim = _simulation_results.get(simulation_id)
    if not sim and simulation_id == "sim_fed_01":
        _seed_canonical_simulation()
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
        is_canonical_reference=sim.get("is_canonical_reference", False),
        provenance=sim.get("provenance", "LIVE_ORCHESTRATED_RUN"),
        execution_mode=sim.get("execution_mode", "LIVE_RUNTIME"),
        tee_is_hardware_backed=sim.get("tee_is_hardware_backed", False),
        tee_driver_mode=sim.get("tee_driver_mode", "SOFTWARE_EMULATION_SANDBOX"),
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


# ── Helpers ─────────────────────────────────────


def _run_simulation_in_process(simulation_id: str, config_dict: dict) -> None:
    """Run the full simulation pipeline in a background thread.

    Updates ``_simulation_results`` in-place so the polling endpoints
    can return real-time progress without Celery or Redis.
    """
    from dataclasses import asdict

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
            with _stop_events_lock:
                stop_evt = _stop_events.get(simulation_id)
            if stop_evt and stop_evt.is_set():
                logger.info("Simulation %s received stop signal. Aborting progress callback.", simulation_id)
                return

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
            event_envelope = {"event_type": event_type, "data": data, "simulation_id": simulation_id}
            _simulation_events.push_list(simulation_id, event_envelope)

            # Always broadcast in-process so clients receive events without Redis
            training_ws_manager.broadcast_to_room_sync(
                f"simulation:{simulation_id}", event_envelope
            )
            training_ws_manager.broadcast_to_room_sync(
                "simulation:live_prod_v2", event_envelope
            )

            # Also publish to Redis pub/sub when available (primary path)
            c = _simulation_events.client
            if c:
                try:
                    payload_str = json.dumps(event_envelope)
                    c.publish(f"training:{simulation_id}", payload_str)
                    c.publish("training:live_prod_v2", payload_str)
                    c.rpush(f"simulation:{simulation_id}:events", payload_str)
                    c.rpush("simulation:live_prod_v2:events", payload_str)
                    c.expire(f"simulation:{simulation_id}:events", 3600)
                    c.expire("simulation:live_prod_v2:events", 3600)
                except Exception as exc:
                    logger.debug("Failed to publish in-process sim event to Redis: %s", exc)

        simulation = simulation_service.run_simulation(
            config=config,
            progress_callback=progress_cb,
            simulation_id=simulation_id,
        )

        # Serialize and store results
        result: dict[str, Any] = {
            "id": simulation_id,
            "status": simulation.status.value,
            "current_round": simulation.current_round,
            "total_rounds": simulation.total_rounds,
            "progress_pct": _calc_progress(
                {
                    "status": simulation.status.value,
                    "current_round": simulation.current_round,
                    "total_rounds": simulation.total_rounds,
                }
            ),
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
            "is_canonical_reference": False,
            "provenance": "LIVE_ORCHESTRATED_RUN",
            "execution_mode": "LIVE_RUNTIME",
            "tee_is_hardware_backed": False,
            "tee_driver_mode": "SOFTWARE_EMULATION_SANDBOX",
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
    if status in (
        SimulationStatus.COMPLETED.value,
        SimulationStatus.FAILED.value,
        SimulationStatus.STOPPED.value,
    ):
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


@router.get("/{simulation_id}/ai-act-report", response_model=AIActReportResponse)
@api_router.get("/{simulation_id}/ai-act-report", response_model=AIActReportResponse)
@singular_router.get("/{simulation_id}/ai-act-report", response_model=AIActReportResponse)
@singular_api_router.get("/{simulation_id}/ai-act-report", response_model=AIActReportResponse)
async def get_ai_act_report(
    simulation_id: str = Path(..., min_length=1, description="Simulation run identifier"),
) -> dict:
    """Retrieve the generated EU AI Act Compliance Report JSON log."""
    import hashlib
    import json
    import os
    from datetime import datetime

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


# ---------------------------------------------------------------------------
# Interactive POC Sandbox Replay Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/poc/presets",
    response_model=POCPresetsResponse,
    summary="List available POC sandbox presets and participating banks",
)
@api_router.get(
    "/poc/presets",
    response_model=POCPresetsResponse,
    summary="List available POC sandbox presets and participating banks",
)
@singular_router.get(
    "/poc/presets",
    response_model=POCPresetsResponse,
    summary="List available POC sandbox presets and participating banks",
)
@singular_api_router.get(
    "/poc/presets",
    response_model=POCPresetsResponse,
    summary="List available POC sandbox presets and participating banks",
)
def get_poc_presets() -> POCPresetsResponse:
    sim = get_multi_bank_simulator()
    return POCPresetsResponse(
        presets=sim.get_presets(),
        participating_banks=sim.get_participating_banks(),
    )


@router.post(
    "/poc/replay",
    summary="Execute deterministic POC multi-bank simulation replay",
)
@api_router.post(
    "/poc/replay",
    summary="Execute deterministic POC multi-bank simulation replay",
)
@singular_router.post(
    "/poc/replay",
    summary="Execute deterministic POC multi-bank simulation replay",
)
@singular_api_router.post(
    "/poc/replay",
    summary="Execute deterministic POC multi-bank simulation replay",
)
def execute_poc_replay(req: POCReplayRequest) -> dict[str, Any]:
    sim = get_multi_bank_simulator()
    summary = sim.execute_poc_replay(preset_id=req.preset_id, random_seed=req.seed)
    return summary.to_dict()


@router.get(
    "/poc/status/{session_id}",
    summary="Get POC replay status and telemetry",
)
@api_router.get(
    "/poc/status/{session_id}",
    summary="Get POC replay status and telemetry",
)
@singular_router.get(
    "/poc/status/{session_id}",
    summary="Get POC replay status and telemetry",
)
@singular_api_router.get(
    "/poc/status/{session_id}",
    summary="Get POC replay status and telemetry",
)
def get_poc_status(session_id: str = Path(..., description="POC session identifier")) -> dict[str, Any]:
    sim = get_multi_bank_simulator()
    status_data = sim.get_session_status(session_id)
    if not status_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"POC session '{session_id}' not found",
        )
    return status_data


@router.get(
    "/poc/summary/{session_id}",
    summary="Get final executive POC evaluation report",
)
@api_router.get(
    "/poc/summary/{session_id}",
    summary="Get final executive POC evaluation report",
)
@singular_router.get(
    "/poc/summary/{session_id}",
    summary="Get final executive POC evaluation report",
)
@singular_api_router.get(
    "/poc/summary/{session_id}",
    summary="Get final executive POC evaluation report",
)
def get_poc_summary(session_id: str = Path(..., description="POC session identifier")) -> dict[str, Any]:
    sim = get_multi_bank_simulator()
    status_data = sim.get_session_status(session_id)
    if not status_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"POC session '{session_id}' not found",
        )
    return status_data

