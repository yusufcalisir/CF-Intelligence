"""Developer Webhook Gateway Router.

Provides subscription management, HMAC-SHA256 signature verification,
real-time test dispatching, delivery audit log retrieval, and health diagnostics.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.application.schemas.webhook import (
    WebhookDeleteResponse,
    WebhookDeliveryLogItem,
    WebhookDeliveryLogsResponse,
    WebhookHealthResponse,
    WebhookSubscriptionItem,
    WebhookSubscriptionListResponse,
    WebhookSubscriptionRequest,
    WebhookSubscriptionResponse,
    WebhookTestDispatchResponse,
    WebhookVerifyRequest,
    WebhookVerifyResponse,
)
from app.application.services.webhook_service import (
    WebhookEventType,
    WebhookService,
)
from app.infrastructure.webhook_dispatcher import get_webhook_dispatcher

logger = logging.getLogger(__name__)

# Base route container
_base_router = APIRouter()
webhook_service = WebhookService()
webhook_dispatcher = get_webhook_dispatcher()


@_base_router.post(
    "/subscriptions",
    response_model=WebhookSubscriptionResponse,
    status_code=status.HTTP_200_OK,
    summary="Register Webhook Subscription",
)
def register_webhook_subscription(
    payload: WebhookSubscriptionRequest,
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
) -> WebhookSubscriptionResponse:
    """Registers a developer webhook subscription endpoint for real-time notifications.

    Rejects private network IPs, cloud metadata endpoints, and non-HTTP schemes via strict SSRF validation.
    """
    # Vector 7: Multi-tenant boundary check
    if x_tenant_id and x_tenant_id != "global" and x_tenant_id != payload.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: X-Tenant-ID '{x_tenant_id}' cannot register webhooks for tenant '{payload.tenant_id}'.",
        )

    try:
        sub = webhook_service.register_subscription(
            tenant_id=payload.tenant_id,
            target_url=payload.target_url,
            events=payload.events,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return WebhookSubscriptionResponse(
        subscription_id=sub.subscription_id,
        tenant_id=sub.tenant_id,
        target_url=sub.target_url,
        secret_key=sub.secret_key,
        events=sub.events,
        created_at=sub.created_at,
        is_active=True,
    )


@_base_router.get(
    "/subscriptions",
    response_model=WebhookSubscriptionListResponse,
    summary="List Webhook Subscriptions",
)
def list_webhook_subscriptions(
    tenant_id: str | None = Query(None, description="Optional tenant ID filter"),
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
) -> WebhookSubscriptionListResponse:
    """Retrieves active webhook subscriptions with secret keys masked for security."""
    effective_tenant = tenant_id or (x_tenant_id if x_tenant_id != "global" else None)
    subs = webhook_service.get_subscriptions(tenant_id=effective_tenant)

    items = [
        WebhookSubscriptionItem(
            subscription_id=s.subscription_id,
            tenant_id=s.tenant_id,
            target_url=s.target_url,
            events=s.events,
            created_at=s.created_at,
            is_active=True,
        )
        for s in subs
    ]

    return WebhookSubscriptionListResponse(
        tenant_id=effective_tenant,
        subscriptions=items,
        total_count=len(items),
    )


@_base_router.delete(
    "/subscriptions/{subscription_id}",
    response_model=WebhookDeleteResponse,
    summary="Delete Webhook Subscription",
)
def delete_webhook_subscription(
    subscription_id: str,
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
) -> WebhookDeleteResponse | JSONResponse:
    """Removes a registered developer webhook subscription by ID."""
    deleted = webhook_service.delete_subscription(
        subscription_id=subscription_id,
        tenant_id=x_tenant_id if x_tenant_id != "global" else None,
    )
    if not deleted:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "type": "https://cfi-platform.org/errors/NotFound",
                "title": "Webhook Subscription Not Found",
                "status": 404,
                "detail": f"Webhook subscription '{subscription_id}' was not found or already deleted.",
            },
            media_type="application/problem+json",
        )

    return WebhookDeleteResponse(
        subscription_id=subscription_id,
        deleted=True,
        message=f"Webhook subscription '{subscription_id}' successfully deleted.",
    )


@_base_router.post(
    "/test-dispatch",
    response_model=WebhookTestDispatchResponse,
    summary="Dispatch Test Webhook Notification",
)
def dispatch_test_webhook(
    tenant_id: str = "bank_alpha",
    event_type: WebhookEventType = WebhookEventType.ALERT_CREATED,
) -> WebhookTestDispatchResponse:
    """Dispatches a synthetic test event notification to registered tenant webhook endpoints."""
    payload = {
        "test": True,
        "message": "CFI Simulator Webhook Dispatch Test",
        "sample_tx": "tx_test_1001",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    deliveries = webhook_service.dispatch_event(
        tenant_id=tenant_id,
        event_type=event_type,
        payload=payload,
    )
    sample_sig = deliveries[0].signature if deliveries else None

    return WebhookTestDispatchResponse(
        dispatched_count=len(deliveries),
        event_type=event_type.value,
        sample_signature=sample_sig,
    )


@_base_router.post(
    "/verify",
    response_model=WebhookVerifyResponse,
    summary="Verify Inbound Webhook Signature",
)
def verify_incoming_webhook(req: WebhookVerifyRequest) -> WebhookVerifyResponse:
    """Receiver-side utility endpoint to verify authenticity of signed webhook payloads.

    Uses constant-time HMAC-SHA256 digest comparison (hmac.compare_digest) to prevent timing attacks.
    """
    payload_bytes = json.dumps(req.payload, sort_keys=True).encode("utf-8")
    is_valid = WebhookService.verify_signature(
        payload_bytes=payload_bytes,
        received_signature=req.signature,
        secret_key=req.secret_key,
    )
    return WebhookVerifyResponse(valid=is_valid, algorithm="HMAC-SHA256")


@_base_router.get(
    "/deliveries",
    response_model=WebhookDeliveryLogsResponse,
    summary="Query Delivery Audit History",
)
def get_webhook_delivery_logs(
    limit: int = Query(50, ge=1, le=200, description="Max delivery logs to retrieve"),
) -> WebhookDeliveryLogsResponse:
    """Retrieves recent outbound webhook delivery execution attempts from the dispatcher buffer."""
    attempts = webhook_dispatcher.get_delivery_history(limit=limit)
    logs = [
        WebhookDeliveryLogItem(
            delivery_id=a.delivery_id,
            target_url=a.target_url,
            event_type=a.event_type,
            status_code=a.status_code,
            success=a.success,
            attempt_count=a.attempt_count,
            error_message=a.error_message,
            timestamp=a.timestamp,
        )
        for a in attempts
    ]
    return WebhookDeliveryLogsResponse(
        deliveries=logs,
        total_count=len(logs),
    )


@_base_router.get(
    "/health",
    response_model=WebhookHealthResponse,
    summary="Webhook Subsystem Health Probe",
)
def webhook_health() -> WebhookHealthResponse:
    """Liveness probe for the webhook gateway and dispatcher subsystem."""
    all_subs = webhook_service.get_subscriptions()
    metrics = webhook_dispatcher.get_metrics()
    return WebhookHealthResponse(
        status="ok",
        service="webhook_gateway",
        active_subscriptions=len(all_subs),
        total_deliveries=metrics["total_deliveries"],
        timestamp=datetime.now(UTC).isoformat(),
    )


# ── Dual-Prefix Routers for Complete Backward & Canonical Parity ──

# Legacy & existing test suite prefix: /v1/webhooks
router = APIRouter(prefix="/v1/webhooks", tags=["Developer Webhooks"])
router.include_router(_base_router)

# Canonical platform prefix: /api/v1/webhooks
api_router = APIRouter(prefix="/api/v1/webhooks", tags=["Developer Webhooks"])
api_router.include_router(_base_router)
