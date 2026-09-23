"""Cloud Core Banking Connector Gateway Router.

Exposes REST and Webhook endpoints for integrating Tier 1/2 cloud core banking
platforms (Mambu & Thought Machine Vault Core) into the CFI fraud detection fabric.

Routes:
    POST /connectors/core-banking/mambu/webhook
    POST /api/v1/connectors/core-banking/mambu/webhook
    POST /connectors/core-banking/thought-machine/webhook
    POST /api/v1/connectors/core-banking/thought-machine/webhook
    POST /connectors/core-banking/holds/provisional
    POST /api/v1/connectors/core-banking/holds/provisional
    GET  /connectors/core-banking/health
    GET  /api/v1/connectors/core-banking/health
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.infrastructure.connectors.mambu_connector import (
    MambuConnector,
    MambuWebhookSignatureError,
)
from app.infrastructure.connectors.thought_machine_connector import (
    ThoughtMachineConnector,
    ThoughtMachineSignatureError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors/core-banking", tags=["Core Banking Gateway"])
api_router = APIRouter(prefix="/api/v1/connectors/core-banking", tags=["Core Banking Gateway"])

# Global Singletons for In-Process Connectors
_mambu_connector: MambuConnector | None = None
_thought_machine_connector: ThoughtMachineConnector | None = None


def get_mambu_connector() -> MambuConnector:
    """Return or initialize singleton Mambu connector instance."""
    global _mambu_connector
    if _mambu_connector is None:
        _mambu_connector = MambuConnector()
    return _mambu_connector


def get_thought_machine_connector() -> ThoughtMachineConnector:
    """Return or initialize singleton Thought Machine connector instance."""
    global _thought_machine_connector
    if _thought_machine_connector is None:
        _thought_machine_connector = ThoughtMachineConnector()
    return _thought_machine_connector


# ── Pydantic Request & Response Models ────────────────────────────────────────


class MambuWebhookResponse(BaseModel):
    """Response returned upon processing a Mambu webhook event."""

    status: str = Field(..., description="Ingestion status (ACCEPTED / PROCESSED)")
    event_type: str = Field(..., description="Processed Mambu event type")
    transaction_id: str | None = Field(default=None, description="Normalized transaction ID")
    account_id: str | None = Field(default=None, description="Account identifier")
    amount: float | None = Field(default=None, description="Transaction monetary amount")
    currency: str | None = Field(default=None, description="Transaction currency")
    client_pseudonym: str | None = Field(default=None, description="Type-salted HMAC pseudonymized client key")
    received_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ThoughtMachineWebhookResponse(BaseModel):
    """Response returned upon processing a Thought Machine posting batch."""

    status: str = Field(..., description="Ingestion status")
    batch_id: str = Field(..., description="Posting instruction batch identifier")
    transactions_count: int = Field(..., description="Number of normalized transactions extracted")
    transaction_ids: list[str] = Field(default_factory=list, description="Extracted transaction IDs")
    received_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ProvisionalHoldRequest(BaseModel):
    """Command payload to place an automated provisional hold on core banking accounts."""

    account_id: str = Field(..., min_length=1, description="Target bank account ID to freeze/restrict")
    amount: float = Field(..., gt=0, description="Provisional hold amount in account currency")
    reason: str = Field(..., min_length=3, description="Fraud investigation justification or recall reference")
    provider: str = Field(
        default="mambu",
        description="Target core banking system: 'mambu' or 'thought_machine'",
    )
    idempotency_key: str | None = Field(default=None, description="Client idempotency token")
    reference_ticket_id: str | None = Field(default=None, description="FININT case or alert ticket ID")


class ProvisionalHoldResponse(BaseModel):
    """Receipt returned after executing a provisional account hold."""

    status: str = Field(..., description="Hold status: HOLD_APPLIED or RESTRICTION_COMMITTED")
    provider: str = Field(..., description="Executing core banking provider")
    hold_id: str = Field(..., description="Unique hold/restriction identifier")
    account_id: str = Field(..., description="Target account identifier")
    amount: float = Field(..., description="Held amount")
    reason: str = Field(..., description="Hold justification")
    reference_id: str = Field(default="", description="Case or ticket reference ID")
    applied_at: str = Field(..., description="UTC ISO-8601 timestamp")
    audit_hash: str = Field(..., description="Cryptographic SHA-256 audit digest")
    external_status: str = Field(default="SIMULATED_LOOPBACK", description="API dispatch outcome")
    deduplicated: bool = Field(default=False, description="True if idempotency cache hit")


class CoreBankingHealthResponse(BaseModel):
    """Health, configuration, and telemetry status of core banking connectors."""

    status: str = Field(default="HEALTHY")
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    mambu: dict[str, Any]
    thought_machine: dict[str, Any]


# ── Handlers ──────────────────────────────────────────────────────────────────


async def _handle_mambu_webhook(
    request: Request,
    x_mambu_signature: str | None,
) -> MambuWebhookResponse:
    """Internal implementation for Mambu webhook ingestion."""
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed JSON payload: {exc}",
        ) from exc

    connector = get_mambu_connector()
    try:
        result = connector.parse_webhook_event(
            payload=payload,
            signature_header=x_mambu_signature,
            raw_body=body_bytes,
        )
    except MambuWebhookSignatureError as sig_err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(sig_err),
        ) from sig_err

    if hasattr(result, "transaction_id"):
        return MambuWebhookResponse(
            status="ACCEPTED",
            event_type="TRANSACTION",
            transaction_id=result.transaction_id,
            account_id=result.account_id,
            amount=result.amount,
            currency=result.currency,
        )
    elif isinstance(result, dict) and result.get("event_type") == "CLIENT_PSEUDONYMIZED":
        return MambuWebhookResponse(
            status="ACCEPTED",
            event_type="CLIENT_PSEUDONYMIZED",
            client_pseudonym=result.get("client_pseudonym"),
        )
    else:
        return MambuWebhookResponse(
            status="ACCEPTED",
            event_type=str(payload.get("type", "UNKNOWN")),
            account_id=payload.get("accountId"),
        )


async def _handle_thought_machine_webhook(
    request: Request,
    x_vault_signature: str | None,
) -> ThoughtMachineWebhookResponse:
    """Internal implementation for Thought Machine Vault Core webhook ingestion."""
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed JSON payload: {exc}",
        ) from exc

    connector = get_thought_machine_connector()
    try:
        normalized_txs = connector.parse_webhook_event(
            payload=payload,
            signature_header=x_vault_signature,
            raw_body=body_bytes,
        )
    except ThoughtMachineSignatureError as sig_err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(sig_err),
        ) from sig_err

    batch_id = str(payload.get("id") or payload.get("posting_instruction_batch", {}).get("id") or "pib_unknown")
    tx_ids = [tx.transaction_id for tx in normalized_txs]

    return ThoughtMachineWebhookResponse(
        status="ACCEPTED",
        batch_id=batch_id,
        transactions_count=len(normalized_txs),
        transaction_ids=tx_ids,
    )


async def _handle_provisional_hold(cmd: ProvisionalHoldRequest) -> ProvisionalHoldResponse:
    """Internal implementation for outbound provisional hold dispatch."""
    provider_key = cmd.provider.lower().replace("-", "_")

    if provider_key == "mambu":
        connector = get_mambu_connector()
        record = await connector.apply_provisional_hold(
            account_id=cmd.account_id,
            amount=cmd.amount,
            reason=cmd.reason,
            idempotency_token=cmd.idempotency_key,
            reference_id=cmd.reference_ticket_id,
        )
    elif provider_key in ("thought_machine", "thoughtmachine"):
        tm_connector = get_thought_machine_connector()
        record = await tm_connector.apply_provisional_hold(
            account_id=cmd.account_id,
            amount=cmd.amount,
            reason=cmd.reason,
            idempotency_token=cmd.idempotency_key,
            reference_id=cmd.reference_ticket_id,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported core banking provider: '{cmd.provider}'. Valid options: 'mambu', 'thought_machine'.",
        )

    return ProvisionalHoldResponse(
        status=record.get("status", "HOLD_APPLIED"),
        provider=record.get("provider", cmd.provider.upper()),
        hold_id=record.get("hold_id", ""),
        account_id=record.get("account_id", cmd.account_id),
        amount=record.get("amount", cmd.amount),
        reason=record.get("reason", cmd.reason),
        reference_id=record.get("reference_id", cmd.reference_ticket_id or ""),
        applied_at=record.get("applied_at", datetime.now(UTC).isoformat()),
        audit_hash=record.get("audit_hash", ""),
        external_status=record.get("external_status", "SIMULATED_LOOPBACK"),
        deduplicated=bool(record.get("deduplicated", False)),
    )


def _handle_health() -> CoreBankingHealthResponse:
    """Return health check telemetry across core banking connectors."""
    mambu = get_mambu_connector()
    tm = get_thought_machine_connector()
    return CoreBankingHealthResponse(
        status="HEALTHY",
        mambu=mambu.health_check(),
        thought_machine=tm.health_check(),
    )


# ── Route Declarations (Dual-Prefix Parity) ───────────────────────────────────


@router.post("/mambu/webhook", response_model=MambuWebhookResponse, status_code=status.HTTP_200_OK)
async def mambu_webhook(
    request: Request,
    x_mambu_signature: str | None = Header(None, alias="X-Mambu-Signature"),
) -> MambuWebhookResponse:
    """Ingest real-time Mambu v2 transactions, client registrations, and hold notifications."""
    return await _handle_mambu_webhook(request, x_mambu_signature)


@api_router.post("/mambu/webhook", response_model=MambuWebhookResponse, status_code=status.HTTP_200_OK)
async def mambu_webhook_v1(
    request: Request,
    x_mambu_signature: str | None = Header(None, alias="X-Mambu-Signature"),
) -> MambuWebhookResponse:
    """Ingest real-time Mambu v2 webhook (API v1 prefix)."""
    return await _handle_mambu_webhook(request, x_mambu_signature)


@router.post("/thought-machine/webhook", response_model=ThoughtMachineWebhookResponse, status_code=status.HTTP_200_OK)
async def thought_machine_webhook(
    request: Request,
    x_vault_signature: str | None = Header(None, alias="X-Vault-Signature"),
) -> ThoughtMachineWebhookResponse:
    """Ingest Thought Machine Vault Core posting instruction batches."""
    return await _handle_thought_machine_webhook(request, x_vault_signature)


@api_router.post("/thought-machine/webhook", response_model=ThoughtMachineWebhookResponse, status_code=status.HTTP_200_OK)
async def thought_machine_webhook_v1(
    request: Request,
    x_vault_signature: str | None = Header(None, alias="X-Vault-Signature"),
) -> ThoughtMachineWebhookResponse:
    """Ingest Thought Machine Vault Core webhook (API v1 prefix)."""
    return await _handle_thought_machine_webhook(request, x_vault_signature)


@router.post("/holds/provisional", response_model=ProvisionalHoldResponse, status_code=status.HTTP_201_CREATED)
async def create_provisional_hold(cmd: ProvisionalHoldRequest, response: Response) -> ProvisionalHoldResponse:
    """Dispatch an automated provisional hold to Mambu or Thought Machine."""
    return await _handle_provisional_hold(cmd)


@api_router.post("/holds/provisional", response_model=ProvisionalHoldResponse, status_code=status.HTTP_201_CREATED)
async def create_provisional_hold_v1(cmd: ProvisionalHoldRequest, response: Response) -> ProvisionalHoldResponse:
    """Dispatch an automated provisional hold (API v1 prefix)."""
    return await _handle_provisional_hold(cmd)


@router.get("/health", response_model=CoreBankingHealthResponse)
async def core_banking_health() -> CoreBankingHealthResponse:
    """Inspect core banking connectors connectivity and telemetry."""
    return _handle_health()


@api_router.get("/health", response_model=CoreBankingHealthResponse)
async def core_banking_health_v1() -> CoreBankingHealthResponse:
    """Inspect core banking connectors connectivity and telemetry (API v1 prefix)."""
    return _handle_health()
