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

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.infrastructure.connectors.base_connector import NormalizedTransaction
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
    risk_score: float | None = Field(default=None, description="Composite transaction risk score (0-1000)")
    decision: str | None = Field(default=None, description="Automated triage decision: APPROVED, FLAG, or HOLD")
    alert_id: str | None = Field(default=None, description="FININT alert ID if risk flagged or critical")
    evaluated_by: str | None = Field(default=None, description="Evaluation engine routing or champion version descriptor")


MCC_TO_MERCHANT_CATEGORY: dict[str, str] = {
    "5411": "grocery",
    "5732": "electronics",
    "4511": "travel",
    "5812": "dining",
    "5541": "fuel",
    "7832": "entertainment",
    "8099": "healthcare",
    "5651": "clothing",
    "5021": "home",
    "5511": "automotive",
    "5944": "jewelry",
    "7995": "gambling",
    "6051": "crypto",
    "4829": "wire_transfer",
    "5311": "online_marketplace",
    "4899": "subscription",
    "6300": "insurance",
    "8220": "education",
    "8398": "charity",
    "6011": "atm_withdrawal",
}

CHANNEL_TO_DEVICE_TYPE: dict[str, str] = {
    "ONLINE": "web_browser",
    "MOBILE": "mobile_app",
    "ATM": "atm",
    "POS": "pos_terminal",
    "SWIFT": "phone_banking",
}


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
    simulation: bool = Field(default=False, description="Explicit simulation flag")


class ProvisionalHoldResponse(BaseModel):
    """Receipt returned after executing a provisional account hold."""

    status: str = Field(..., description="Hold status: HOLD_APPLIED, RESTRICTION_COMMITTED, or SIMULATION_RESULT")
    provider: str = Field(..., description="Executing core banking provider")
    hold_id: str = Field(..., description="Unique hold/restriction identifier")
    account_id: str = Field(..., description="Target account identifier")
    amount: float = Field(..., description="Held amount")
    reason: str = Field(..., description="Hold justification")
    reference_id: str = Field(default="", description="Case or ticket reference ID")
    applied_at: str = Field(..., description="UTC ISO-8601 timestamp")
    audit_hash: str = Field(..., description="Cryptographic SHA-256 audit digest")
    external_status: str = Field(..., description="API dispatch outcome")
    deduplicated: bool = Field(default=False, description="True if idempotency cache hit")


class CoreBankingHealthResponse(BaseModel):
    """Health, configuration, and telemetry status of core banking connectors."""

    status: str = Field(...)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    mambu: dict[str, Any]
    thought_machine: dict[str, Any]


# ── Handlers ──────────────────────────────────────────────────────────────────


async def _handle_mambu_webhook(
    request: Request,
    x_mambu_signature: str | None,
    background_tasks: BackgroundTasks | None = None,
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
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(val_err),
        ) from val_err

    if isinstance(result, NormalizedTransaction):
        mcc_raw = str(result.merchant_category_code or "5411")
        mapped_category = MCC_TO_MERCHANT_CATEGORY.get(mcc_raw, "grocery")
        mapped_channel = CHANNEL_TO_DEVICE_TYPE.get(str(result.channel_type).upper(), "web_browser")
        effective_bank = result.bank_id or "bank_alpha"
        hour_val = result.timestamp.hour if result.timestamp else 12

        raw_cb = payload.get("chargeback_count")
        cb_val = int(raw_cb) if raw_cb is not None else 0

        raw_vel = payload.get("velocity")
        vel_val = float(raw_vel) if raw_vel is not None else 1.0

        raw_c_hist = payload.get("customer_history_score")
        c_hist_val = float(raw_c_hist) if raw_c_hist is not None else 0.95

        raw_age = payload.get("account_age_days")
        age_val = int(raw_age) if raw_age is not None else 365

        raw_m_risk = payload.get("merchant_risk_score")
        if raw_m_risk is not None:
            m_risk_val = float(raw_m_risk)
        elif mapped_category in ("crypto", "gambling", "wire_transfer", "jewelry"):
            m_risk_val = 0.85
        else:
            m_risk_val = 0.15

        txn_features: dict[str, Any] = {
            "transaction_id": result.transaction_id,
            "customer_id": result.account_id,
            "transaction_amount": float(result.amount),
            "merchant_category": mapped_category,
            "country_code": result.origin_country or "US",
            "device_type": mapped_channel,
            "velocity": vel_val,
            "hour_of_day": hour_val,
            "merchant_risk_score": m_risk_val,
            "customer_history_score": c_hist_val,
            "chargeback_count": cb_val,
            "account_age_days": age_val,
        }

        from app.presentation.routers.predict import evaluate_transaction_risk_and_alerts

        fraud_prob, score, risk_level, active_alert, alert_details, breakdown = (
            evaluate_transaction_risk_and_alerts(
                txn_dict=txn_features,
                bank_id=effective_bank,
                customer_ref=result.account_id,
                background_tasks=background_tasks,
                strict_production=False,
            )
        )

        if score >= 700.0 or risk_level in ("CRITICAL", "HIGH"):
            decision = "HOLD"
        elif score >= 600.0 or risk_level == "MEDIUM":
            decision = "FLAG"
        else:
            decision = "APPROVED"

        eval_desc = (
            f"champion_eval:{round(fraud_prob, 4)}"
            if active_alert is None
            else f"champion_alert:{active_alert.id[:8]}"
        )

        return MambuWebhookResponse(
            status="ACCEPTED",
            event_type="TRANSACTION",
            transaction_id=result.transaction_id,
            account_id=result.account_id,
            amount=result.amount,
            currency=result.currency,
            risk_score=round(score, 2),
            decision=decision,
            alert_id=active_alert.id if active_alert else None,
            evaluated_by=eval_desc,
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
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(val_err),
        ) from val_err

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
            simulation=cmd.simulation,
        )
    elif provider_key in ("thought_machine", "thoughtmachine"):
        tm_connector = get_thought_machine_connector()
        record = await tm_connector.apply_provisional_hold(
            account_id=cmd.account_id,
            amount=cmd.amount,
            reason=cmd.reason,
            idempotency_token=cmd.idempotency_key,
            reference_id=cmd.reference_ticket_id,
            simulation=cmd.simulation,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported core banking provider: '{cmd.provider}'. Valid options: 'mambu', 'thought_machine'.",
        )

    hold_status = record.get("status", "HOLD_FAILED")
    external_status = record.get("external_status", "UNKNOWN")

    if not cmd.simulation and hold_status not in ("HOLD_APPLIED", "RESTRICTION_COMMITTED"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Core banking provisional hold failed ({hold_status}, {external_status})",
        )

    return ProvisionalHoldResponse(
        status=hold_status,
        provider=record.get("provider", cmd.provider.upper()),
        hold_id=record.get("hold_id", ""),
        account_id=record.get("account_id", cmd.account_id),
        amount=record.get("amount", cmd.amount),
        reason=record.get("reason", cmd.reason),
        reference_id=record.get("reference_id", cmd.reference_ticket_id or ""),
        applied_at=record.get("applied_at", datetime.now(UTC).isoformat()),
        audit_hash=record.get("audit_hash", ""),
        external_status=external_status,
        deduplicated=bool(record.get("deduplicated", False)),
    )


def _handle_health() -> CoreBankingHealthResponse:
    """Return health check telemetry across core banking connectors."""
    mambu = get_mambu_connector()
    tm = get_thought_machine_connector()
    mambu_health = mambu.health_check()
    tm_health = tm.health_check()

    statuses = [mambu_health.get("status", "UNKNOWN"), tm_health.get("status", "UNKNOWN")]
    if any(s in ("UNAVAILABLE", "OPEN") for s in statuses):
        if all(s in ("UNAVAILABLE", "OPEN") for s in statuses):
            agg_status = "UNAVAILABLE"
        else:
            agg_status = "DEGRADED"
    elif any(s in ("DEGRADED", "HALF_OPEN", "AUTHENTICATION_UNAVAILABLE") for s in statuses):
        agg_status = "DEGRADED"
    elif all(s == "HEALTHY" for s in statuses):
        agg_status = "HEALTHY"
    else:
        agg_status = "DEGRADED"

    return CoreBankingHealthResponse(
        status=agg_status,
        mambu=mambu_health,
        thought_machine=tm_health,
    )


# ── Route Declarations (Dual-Prefix Parity) ───────────────────────────────────


@router.post("/mambu/webhook", response_model=MambuWebhookResponse, status_code=status.HTTP_200_OK)
async def mambu_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_mambu_signature: str | None = Header(None, alias="X-Mambu-Signature"),
) -> MambuWebhookResponse:
    """Ingest real-time Mambu v2 transactions, client registrations, and hold notifications."""
    return await _handle_mambu_webhook(request, x_mambu_signature, background_tasks=background_tasks)


@api_router.post("/mambu/webhook", response_model=MambuWebhookResponse, status_code=status.HTTP_200_OK)
async def mambu_webhook_v1(
    request: Request,
    background_tasks: BackgroundTasks,
    x_mambu_signature: str | None = Header(None, alias="X-Mambu-Signature"),
) -> MambuWebhookResponse:
    """Ingest real-time Mambu v2 webhook (API v1 prefix)."""
    return await _handle_mambu_webhook(request, x_mambu_signature, background_tasks=background_tasks)


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
