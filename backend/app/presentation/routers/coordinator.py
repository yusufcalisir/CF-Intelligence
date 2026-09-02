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
