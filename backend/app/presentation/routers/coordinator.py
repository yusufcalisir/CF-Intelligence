"""Federated Learning Coordinator Endpoints.

Exposes REST APIs for dynamic bank client handshake, status checks, capability negotiation,
and registration list lookup.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.application.services.coordinator_service import coordinator_service

router = APIRouter(prefix="/api/v1/coordinator", tags=["coordinator"])


# ── Schemas ───────────────────────────────────────────────────


class HandshakeRequest(BaseModel):
    bank_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique bank tenant ID",
    )
    pytorch_version: str = Field(
        ...,
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+.*$",
        description="PyTorch installation version",
    )
    python_version: str = Field(
        ...,
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+.*$",
        description="Python execution runtime version",
    )
    hardware_type: str = Field(
        ...,
        max_length=32,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="Available accelerator e.g. cuda or cpu",
    )
    ram_gb: float = Field(
        ...,
        ge=0.5,
        le=8192.0,
        description="Installed RAM in gigabytes [0.5, 8192]",
    )
    device_count: int = Field(
        default=1,
        ge=0,
        le=64,
        description="Number of available GPUs [0, 64]",
    )


class HandshakeResponse(BaseModel):
    registered: bool
    status: str
    reason: str | None = None
    client_profile: Any | None = None
    registered_at: float


class HeartbeatRequest(BaseModel):
    bank_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
    )


class HeartbeatResponse(BaseModel):
    success: bool
    status: str
    timestamp: float


class NegotiatedResponse(BaseModel):
    bank_id: str
    batch_size: int
    local_epochs: int
    gradient_accumulation_steps: int
    use_cuda: bool
    status: str


class ClientCapabilityResponse(BaseModel):
    bank_id: str
    pytorch_version: str
    python_version: str
    hardware_type: str
    ram_gb: float
    device_count: int
    status: str
    last_heartbeat_ago_seconds: float


class AsyncUpdateRequest(BaseModel):
    bank_id: str = Field(
        ..., min_length=3, max_length=64, description="Unique bank tenant ID"
    )
    submitted_round: int = Field(
        ..., ge=1, description="Round number client base model was trained on"
    )
    client_weights: dict[str, list[float]] = Field(
        ..., description="Flattened or 1D list of parameter weights per layer"
    )
    layer_shapes: dict[str, list[int]] | None = Field(
        default=None, description="Optional tensor dimensions to reconstruct multi-dimensional weights"
    )
    sample_count: int = Field(
        default=100, ge=1, description="Number of local training samples"
    )


class AsyncUpdateResponse(BaseModel):
    success: bool
    bank_id: str
    submitted_round: int
    current_round: int
    staleness_tau: int
    staleness_attenuation: float
    effective_alpha: float
    layer_keys: list[str]


class QuorumStatusResponse(BaseModel):
    round_number: int
    registered_nodes_count: int
    submitted_nodes_count: int
    quorum_threshold_pct: float
    current_quorum_pct: float
    state: str
    start_time: str
    target_window_seconds: int
    time_remaining_seconds: float


class AsyncFLEngineStatusResponse(BaseModel):
    current_round: int
    alpha_staleness: float
    learning_rate: float
    max_staleness: int
    staleness_function: str
    total_updates: int
    dropped_updates: int
    applied_updates: int
    average_staleness: float
    max_observed_staleness: int


# ── Endpoints ─────────────────────────────────────────────────


@router.post("/handshake", response_model=HandshakeResponse)
async def perform_handshake(req: HandshakeRequest) -> HandshakeResponse:
    """Register client capabilities dynamically to negotiate compatible execution params."""
    res = coordinator_service.register_client(
        bank_id=req.bank_id,
        pytorch_version=req.pytorch_version,
        python_version=req.python_version,
        hardware_type=req.hardware_type,
        ram_gb=req.ram_gb,
        device_count=req.device_count,
    )
    return HandshakeResponse(
        registered=res["registered"],
        status=res["status"],
        reason=res.get("reason"),
        client_profile=res.get("client_profile"),
        registered_at=time.time(),
    )


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def post_heartbeat(req: HeartbeatRequest) -> HeartbeatResponse:
    """Post heartbeat check-in to remain in the active participant registry."""
    success = coordinator_service.record_heartbeat(req.bank_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bank {req.bank_id} is not registered. Perform handshake first.",
        )
    return HeartbeatResponse(
        success=True,
        status="ONLINE",
        timestamp=time.time(),
    )


@router.get("/clients", response_model=list[ClientCapabilityResponse])
async def list_registered_clients() -> list[ClientCapabilityResponse]:
    """Retrieve capability profiles of all dynamically registered banks."""
    # Force updating statuses
    _ = coordinator_service.get_active_clients()

    now = time.time()
    results = []
    for client in coordinator_service.registry.values():
        results.append(
            ClientCapabilityResponse(
                bank_id=client.bank_id,
                pytorch_version=client.pytorch_version,
                python_version=client.python_version,
                hardware_type=client.hardware_type,
                ram_gb=client.ram_gb,
                device_count=client.device_count,
                status=client.status,
                last_heartbeat_ago_seconds=round(now - client.last_heartbeat, 1),
            )
        )
    return results


class NegotiateRequest(BaseModel):
    bank_id: str = Field(..., min_length=3, max_length=64)
    base_batch_size: int = Field(default=32, ge=1, le=4096)
    base_epochs: int = Field(default=5, ge=1, le=100)
    hardware_type: str | None = None
    available_vram_gb: float | None = None
    bandwidth_mbps: float | None = None
    local_sample_count: int | None = None


@router.get("/negotiate", response_model=NegotiatedResponse)
async def negotiate_training_params(
    bank_id: str,
    base_batch_size: int = 32,
    base_epochs: int = 5,
) -> NegotiatedResponse:
    """Get training hyper-parameters customized for a bank client's runtime capability."""
    neg = coordinator_service.negotiate_parameters(bank_id, base_batch_size, base_epochs)
    return NegotiatedResponse(
        bank_id=bank_id,
        batch_size=neg.batch_size,
        local_epochs=neg.local_epochs,
        gradient_accumulation_steps=neg.gradient_accumulation_steps,
        use_cuda=neg.use_cuda,
        status=neg.status,
    )


@router.post("/negotiate", response_model=NegotiatedResponse)
async def negotiate_training_params_post(
    req: NegotiateRequest,
) -> NegotiatedResponse:
    """Negotiate training hyper-parameters customized for a bank client via JSON payload."""
    neg = coordinator_service.negotiate_parameters(
        req.bank_id, req.base_batch_size, req.base_epochs
    )
    return NegotiatedResponse(
        bank_id=req.bank_id,
        batch_size=neg.batch_size,
        local_epochs=neg.local_epochs,
        gradient_accumulation_steps=neg.gradient_accumulation_steps,
        use_cuda=neg.use_cuda,
        status=neg.status,
    )


@router.post("/async-update", response_model=AsyncUpdateResponse)
async def submit_async_update(req: AsyncUpdateRequest) -> AsyncUpdateResponse:
    """Submit an asynchronous parameter update attenuated by staleness S(tau)."""
    import numpy as np

    numpy_weights: dict[str, np.ndarray] = {}
    for layer, vals in req.client_weights.items():
        arr = np.array(vals, dtype=np.float32)
        if req.layer_shapes and layer in req.layer_shapes:
            try:
                arr = arr.reshape(req.layer_shapes[layer])
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Shape mismatch reshaping layer '{layer}': {e}",
                ) from e
        numpy_weights[layer] = arr

    try:
        res = coordinator_service.submit_async_update(
            bank_id=req.bank_id,
            submitted_round=req.submitted_round,
            client_weights=numpy_weights,
            sample_count=req.sample_count,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    return AsyncUpdateResponse(**res)


@router.get("/async-status", response_model=AsyncFLEngineStatusResponse)
async def get_async_engine_status() -> AsyncFLEngineStatusResponse:
    """Retrieve runtime staleness damping metrics and hyperparameters of the FedAsync engine."""
    metrics = coordinator_service.async_fl_engine.get_staleness_metrics()
    return AsyncFLEngineStatusResponse(**metrics)


@router.get("/quorum-status", response_model=QuorumStatusResponse)
async def get_dynamic_quorum_status(round_id: int | None = None) -> QuorumStatusResponse:
    """Inspect dynamic quorum threshold progress and countdown for active/specified training round."""
    q_status = coordinator_service.get_quorum_status(round_id)
    return QuorumStatusResponse(
        round_number=q_status.round_number,
        registered_nodes_count=q_status.registered_nodes_count,
        submitted_nodes_count=q_status.submitted_nodes_count,
        quorum_threshold_pct=q_status.quorum_threshold_pct,
        current_quorum_pct=q_status.current_quorum_pct,
        state=q_status.state.value if hasattr(q_status.state, "value") else str(q_status.state),
        start_time=q_status.start_time,
        target_window_seconds=q_status.target_window_seconds,
        time_remaining_seconds=q_status.time_remaining_seconds,
    )


@router.post("/rounds/prune")
async def prune_historical_rounds(keep_last: int = 50) -> dict[str, Any]:
    """Prune historical in-memory round states to prevent heap memory accumulation."""
    pruned = coordinator_service.prune_completed_rounds(keep_last=keep_last)
    return {"pruned_rounds_count": pruned, "keep_last": keep_last}
