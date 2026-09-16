# ruff: noqa: TC001, TC003
"""Pydantic v2 schemas for Developer Webhook Gateway.

Validates subscription payloads, event types, signature verifications,
delivery logs, and error responses with strict bounds.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.application.services.webhook_service import WebhookEventType


class WebhookSubscriptionRequest(BaseModel):
    """Schema for registering a developer webhook subscription."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
        json_schema_extra={"example": "bank_alpha"},
    )
    target_url: str = Field(
        ...,
        min_length=8,
        max_length=2048,
        json_schema_extra={"example": "https://api.bank-alpha.com/webhooks/cfi"},
    )
    events: list[WebhookEventType] = Field(
        ...,
        min_length=1,
        max_length=10,
        json_schema_extra={"example": ["ALERT_CREATED", "CASE_RESOLVED"]},
    )


class WebhookSubscriptionResponse(BaseModel):
    """Schema for webhook registration response with secret key."""

    model_config = ConfigDict(from_attributes=True)

    subscription_id: str = Field(..., max_length=64)
    tenant_id: str = Field(..., max_length=64)
    target_url: str = Field(..., max_length=2048)
    secret_key: str = Field(..., max_length=128)
    events: list[WebhookEventType]
    created_at: datetime | None = None
    is_active: bool = True


class WebhookSubscriptionItem(BaseModel):
    """Schema for listing webhook subscriptions (secret_key masked for security)."""

    model_config = ConfigDict(from_attributes=True)

    subscription_id: str
    tenant_id: str
    target_url: str
    events: list[WebhookEventType]
    created_at: datetime | None = None
    is_active: bool = True


class WebhookSubscriptionListResponse(BaseModel):
    """Schema for listing tenant webhook subscriptions."""

    tenant_id: str | None = None
    subscriptions: list[WebhookSubscriptionItem]
    total_count: int


class WebhookTestDispatchResponse(BaseModel):
    """Schema for test dispatch payload execution."""

    dispatched_count: int
    event_type: str
    sample_signature: str | None = None


class WebhookVerifyRequest(BaseModel):
    """Schema for validating an incoming webhook signature."""

    payload: dict[str, Any]
    signature: str = Field(..., min_length=16, max_length=256)
    secret_key: str = Field(..., min_length=8, max_length=256)


class WebhookVerifyResponse(BaseModel):
    """Schema for signature verification result."""

    valid: bool
    algorithm: str = "HMAC-SHA256"


class WebhookDeliveryLogItem(BaseModel):
    """Schema for an individual webhook delivery attempt log."""

    delivery_id: str
    target_url: str
    event_type: str
    status_code: int | None = None
    success: bool
    attempt_count: int
    error_message: str | None = None
    timestamp: datetime


class WebhookDeliveryLogsResponse(BaseModel):
    """Schema for querying recent webhook delivery logs."""

    deliveries: list[WebhookDeliveryLogItem]
    total_count: int


class WebhookDeleteResponse(BaseModel):
    """Schema for subscription deletion response."""

    subscription_id: str
    deleted: bool
    message: str


class WebhookHealthResponse(BaseModel):
    """Schema for webhook subsystem health probe."""

    status: str = "ok"
    service: str = "webhook_gateway"
    active_subscriptions: int
    total_deliveries: int
    timestamp: str
