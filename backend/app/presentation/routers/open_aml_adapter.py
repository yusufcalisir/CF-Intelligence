"""Enterprise Open AML OpenAPI Adapter Router (Phase 110).

Exposes standard European / International AML OpenAPI specifications:
- POST /api/v2/persons (and /api/v1/persons) — Ingest Individual or Corporate Legal Entity
- GET  /api/v2/persons/{person_id} — Lookup registered person profile
- POST /api/v1/persons/{person_id}/transactions — Ingest customer transaction
- POST /api/v1/transactions/{transaction_id}/monitoring-checks — Real-time online/offline monitoring
- POST /api/v1/persons/{person_id}/screening-checks — Screen registered customer
- POST /api/v2/screening-searches — Ad-hoc multi-watchlist fuzzy search
- POST /api/v1/aml-adapter/webhooks/subscriptions — Subscribe to webhook events
- GET  /api/v1/aml-adapter/webhooks/events — Audit recent webhook dispatches
- GET  /api/v1/aml-adapter/metrics — Adapter throughput and latency telemetry
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, status

from app.application.schemas.open_aml_schemas import (
    MonitoringCheckRequest,
    MonitoringCheckResponse,
    OpenAMLAdapterMetricsResponse,
    OpenAMLScreeningCheckRequest,
    OpenAMLScreeningCheckResponse,
    OpenAMLWebhookSubscriptionRequest,
    OpenAMLWebhookSubscriptionResponse,
    PersonCreateRequest,
    PersonResponse,
    PersonTransactionCreateRequest,
    PersonTransactionResponse,
)
from app.application.services.open_aml_service import get_open_aml_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Enterprise AML OpenAPI Adapter"])
api_router = APIRouter(tags=["Enterprise AML OpenAPI Adapter"])


# ── Helper for Tenant Extraction ─────────────────────────────────────────────

def _resolve_tenant(x_tenant_id: str | None = None, x_bank_id: str | None = None) -> str:
    if x_bank_id and x_bank_id.strip():
        return x_bank_id.strip()
    if x_tenant_id and x_tenant_id.strip():
        return x_tenant_id.strip()
    return "default_bank"



# ── 1. Person & Corporate Entity Registration ────────────────────────────────

@router.post(
    "/api/v2/persons",
    response_model=PersonResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register Person or Legal Entity (v2)",
)
@router.post(
    "/api/v1/persons",
    response_model=PersonResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register Person or Legal Entity (v1 alias)",
)
@api_router.post(
    "/v2/persons",
    response_model=PersonResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
@api_router.post(
    "/v1/persons",
    response_model=PersonResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_person(
    request: PersonCreateRequest,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_bank_id: Annotated[str | None, Header()] = None,
) -> PersonResponse:
    """Register an Individual or Legal Entity with UBO structure and identity documents."""
    tenant = _resolve_tenant(x_tenant_id, x_bank_id)
    svc = get_open_aml_service()
    try:
        return svc.register_person(request, tenant_id=tenant)
    except Exception as exc:
        logger.error("Failed to register person %s: %s", request.person_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Person registration failed: {exc}",
        ) from exc


@router.get(
    "/api/v2/persons/{person_id}",
    response_model=PersonResponse,
    summary="Get Person or Legal Entity (v2)",
)
@router.get(
    "/api/v1/persons/{person_id}",
    response_model=PersonResponse,
    summary="Get Person or Legal Entity (v1 alias)",
)
@api_router.get(
    "/v2/persons/{person_id}",
    response_model=PersonResponse,
    include_in_schema=False,
)
@api_router.get(
    "/v1/persons/{person_id}",
    response_model=PersonResponse,
    include_in_schema=False,
)
async def get_person(
    person_id: str,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_bank_id: Annotated[str | None, Header()] = None,
) -> PersonResponse:
    """Retrieve details for a registered person or legal entity."""
    tenant = _resolve_tenant(x_tenant_id, x_bank_id)
    svc = get_open_aml_service()
    res = svc.get_person(person_id, tenant_id=tenant)
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Person '{person_id}' not found for institution.",
        )
    return res


# ── 2. Transaction Ingestion ─────────────────────────────────────────────────

@router.post(
    "/api/v1/persons/{person_id}/transactions",
    response_model=PersonTransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest Person Transaction",
)
@api_router.post(
    "/v1/persons/{person_id}/transactions",
    response_model=PersonTransactionResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def ingest_person_transaction(
    person_id: str,
    request: PersonTransactionCreateRequest,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_bank_id: Annotated[str | None, Header()] = None,
) -> PersonTransactionResponse:
    """Ingest a financial transaction associated with a registered person."""
    tenant = _resolve_tenant(x_tenant_id, x_bank_id)
    svc = get_open_aml_service()
    if not svc.has_person(tenant, person_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Person '{person_id}' not found.",
        )
    try:
        return svc.ingest_transaction(person_id, request, tenant_id=tenant)
    except Exception as exc:
        logger.error("Failed to ingest transaction %s: %s", request.transaction_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Transaction ingestion failed: {exc}",
        ) from exc


# ── 3. Transaction Monitoring Checks ─────────────────────────────────────────

@router.post(
    "/api/v1/transactions/{transaction_id}/monitoring-checks",
    response_model=MonitoringCheckResponse,
    summary="Execute Real-Time Transaction Monitoring Check",
)
@api_router.post(
    "/v1/transactions/{transaction_id}/monitoring-checks",
    response_model=MonitoringCheckResponse,
    include_in_schema=False,
)
async def execute_monitoring_check(
    transaction_id: str,
    request: MonitoringCheckRequest,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_bank_id: Annotated[str | None, Header()] = None,
) -> MonitoringCheckResponse:
    """Evaluate a transaction against AML scenarios and 9-signal composite risk."""
    if not request.transaction_id:
        request.transaction_id = transaction_id
    elif request.transaction_id != transaction_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL transaction_id does not match request body transaction_id.",
        )

    tenant = _resolve_tenant(x_tenant_id, x_bank_id)
    svc = get_open_aml_service()
    if not svc.has_transaction(tenant, transaction_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction '{transaction_id}' not found for evaluation.",
        )
    try:
        return svc.execute_monitoring_check(request, tenant_id=tenant)
    except Exception as exc:
        logger.error("Monitoring check failed for txn %s: %s", transaction_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Monitoring check error: {exc}",
        ) from exc


# ── 4. Sanctions & PEP Screening Checks ──────────────────────────────────────

@router.post(
    "/api/v1/persons/{person_id}/screening-checks",
    response_model=OpenAMLScreeningCheckResponse,
    summary="Screen Registered Customer against Watchlists",
)
@api_router.post(
    "/v1/persons/{person_id}/screening-checks",
    response_model=OpenAMLScreeningCheckResponse,
    include_in_schema=False,
)
async def screen_person(
    person_id: str,
    request: OpenAMLScreeningCheckRequest,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_bank_id: Annotated[str | None, Header()] = None,
) -> OpenAMLScreeningCheckResponse:
    """Execute multi-jurisdiction watchlist screening against a registered person."""
    tenant = _resolve_tenant(x_tenant_id, x_bank_id)
    svc = get_open_aml_service()

    # Enforce person_id binding
    payload = request.model_copy(update={"person_id": person_id})
    try:
        return svc.execute_screening_check(payload, tenant_id=tenant)
    except Exception as exc:
        logger.error("Screening failed for person %s: %s", person_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Screening error: {exc}",
        ) from exc


@router.post(
    "/api/v2/screening-searches",
    response_model=OpenAMLScreeningCheckResponse,
    summary="Ad-Hoc Multi-Jurisdiction Watchlist Screening Search",
)
@api_router.post(
    "/v2/screening-searches",
    response_model=OpenAMLScreeningCheckResponse,
    include_in_schema=False,
)
async def screening_search(
    request: OpenAMLScreeningCheckRequest,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_bank_id: Annotated[str | None, Header()] = None,
) -> OpenAMLScreeningCheckResponse:
    """Execute an ad-hoc fuzzy screening query against UN, EU CFSP, OFAC SDN, and PEP lists."""
    if not request.target_name and not request.person_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Either 'target_name' or 'person_id' must be specified for screening search.",
        )

    tenant = _resolve_tenant(x_tenant_id, x_bank_id)
    svc = get_open_aml_service()
    try:
        return svc.execute_screening_check(request, tenant_id=tenant)
    except Exception as exc:
        logger.error("Screening search failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Screening search error: {exc}",
        ) from exc


# ── 5. Webhooks Management ───────────────────────────────────────────────────

@router.post(
    "/api/v1/aml-adapter/webhooks/subscriptions",
    response_model=OpenAMLWebhookSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register AML Event Webhook Subscription",
)
@api_router.post(
    "/v1/aml-adapter/webhooks/subscriptions",
    response_model=OpenAMLWebhookSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def register_webhook(
    request: OpenAMLWebhookSubscriptionRequest,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_bank_id: Annotated[str | None, Header()] = None,
) -> OpenAMLWebhookSubscriptionResponse:
    """Register an endpoint to receive HMAC-SHA256 signed AML alert notifications."""
    tenant = _resolve_tenant(x_tenant_id, x_bank_id)
    svc = get_open_aml_service()
    return svc.register_webhook_subscription(request, tenant_id=tenant)


@router.get(
    "/api/v1/aml-adapter/webhooks/events",
    response_model=list[dict[str, Any]],
    summary="List Recent Dispatched Webhook Events",
)
@api_router.get(
    "/v1/aml-adapter/webhooks/events",
    response_model=list[dict[str, Any]],
    include_in_schema=False,
)
async def list_webhook_events(
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_bank_id: Annotated[str | None, Header()] = None,
) -> list[dict[str, Any]]:
    """Retrieve audit history of recent outgoing signed webhook dispatches."""
    tenant = _resolve_tenant(x_tenant_id, x_bank_id)
    svc = get_open_aml_service()
    return svc.list_webhook_events(tenant_id=tenant)


# ── 6. Metrics & Operational Telemetry ───────────────────────────────────────

@router.get(
    "/api/v1/aml-adapter/metrics",
    response_model=OpenAMLAdapterMetricsResponse,
    summary="Open AML Adapter Operational Metrics",
)
@api_router.get(
    "/v1/aml-adapter/metrics",
    response_model=OpenAMLAdapterMetricsResponse,
    include_in_schema=False,
)
async def get_metrics(
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_bank_id: Annotated[str | None, Header()] = None,
) -> OpenAMLAdapterMetricsResponse:
    """Query telemetry metrics for drop-in AML adapter."""
    tenant = _resolve_tenant(x_tenant_id, x_bank_id)
    svc = get_open_aml_service()
    return svc.get_metrics(tenant_id=tenant)

