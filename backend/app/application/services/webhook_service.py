# ruff: noqa: UP042
"""Developer Webhook Notification Service."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import socket
import urllib.parse
from urllib.parse import urlparse
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class WebhookEventType(str, Enum):
    """Event types available for developer webhook subscriptions."""

    ALERT_CREATED = "ALERT_CREATED"
    CASE_RESOLVED = "CASE_RESOLVED"
    MODEL_PROMOTED = "MODEL_PROMOTED"
    DRIFT_DETECTED = "DRIFT_DETECTED"


@dataclass
class WebhookSubscription:
    """Dataclass representing a developer webhook subscription."""

    subscription_id: str
    tenant_id: str
    target_url: str
    secret_key: str
    events: list[WebhookEventType]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class WebhookDeliveryPayload:
    """Dataclass tracking an outgoing signed webhook payload."""

    event_id: str
    event_type: WebhookEventType
    payload: dict[str, Any]
    signature: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class WebhookService:
    """Manages developer webhook subscriptions, HMAC-SHA256 payload signing,

    receiver signature verification, and SSRF-hardened payload dispatch.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, list[WebhookSubscription]] = {}

    def register_subscription(
        self,
        tenant_id: str,
        target_url: str,
        events: list[WebhookEventType],
    ) -> WebhookSubscription:
        """Registers a developer webhook subscription endpoint and generates a secret key.

        Validates target URL upfront to reject dangerous SSRF targets (private IPs, loopback,
        cloud metadata endpoints).
        """
        if not self.validate_target_url(target_url):
            raise ValueError(
                f"Invalid or disallowed webhook target URL (SSRF protection rejected: {target_url})"
            )

        subscription_id = f"sub_{uuid.uuid4().hex[:8]}"
        secret_key = f"whsec_{uuid.uuid4().hex[:16]}"

        subscription = WebhookSubscription(
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            target_url=target_url,
            secret_key=secret_key,
            events=events,
        )

        if tenant_id not in self._subscriptions:
            self._subscriptions[tenant_id] = []
        self._subscriptions[tenant_id].append(subscription)

        logger.info(
            "Registered webhook subscription '%s' for tenant '%s' (Target: %s, Events: %s)",
            subscription_id,
            tenant_id,
            target_url,
            [e.value for e in events],
        )
        return subscription

    def compute_hmac_signature(self, secret_key: str, payload_bytes: bytes) -> str:
        """Computes HMAC-SHA256 signature string for payload authentication."""
        signature = hmac.new(
            secret_key.encode("utf-8"),
            payload_bytes,
            digestmod="sha256",
        ).hexdigest()
        return f"sha256={signature}"

    @staticmethod
    def verify_signature(
        payload_bytes: bytes,
        received_signature: str,
        secret_key: str,
    ) -> bool:
        """Verifies that an incoming webhook signature matches the expected HMAC-SHA256 digest.

        Uses constant-time comparison (hmac.compare_digest) to prevent timing attacks.
        Supports both 'sha256=<hex>' format and raw hex signature strings.
        """
        if not received_signature or not secret_key:
            return False
        clean_sig = received_signature.strip()
        expected_raw = hmac.new(
            secret_key.encode("utf-8"),
            payload_bytes,
            digestmod="sha256",
        ).hexdigest()
        expected_prefixed = f"sha256={expected_raw}"

        if clean_sig.startswith("sha256="):
            return hmac.compare_digest(clean_sig, expected_prefixed)
        return hmac.compare_digest(clean_sig, expected_raw)

    def dispatch_event(
        self,
        tenant_id: str,
        event_type: WebhookEventType,
        payload: dict[str, Any],
    ) -> list[WebhookDeliveryPayload]:
        """Signs and dispatches event notification payloads to matching subscribers."""
        tenant_subs = self._subscriptions.get(tenant_id, [])
        delivered: list[WebhookDeliveryPayload] = []

        payload_json_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")

        for sub in tenant_subs:
            if event_type in sub.events:
                event_id = f"evt_{uuid.uuid4().hex[:8]}"
                signature = self.compute_hmac_signature(
                    secret_key=sub.secret_key,
                    payload_bytes=payload_json_bytes,
                )

                delivery = WebhookDeliveryPayload(
                    event_id=event_id,
                    event_type=event_type,
                    payload=payload,
                    signature=signature,
                )
                delivered.append(delivery)

                logger.info(
                    "Dispatched webhook event %s (%s) to %s (Signature: %s)",
                    event_id,
                    event_type.value,
                    sub.target_url,
                    signature[:16],
                )

        return delivered

    @staticmethod
    def validate_target_url(url: str) -> bool:
        """Validates webhook target URL to prevent Server-Side Request Forgery (SSRF).

        Enforces:
        1. Scheme validation: strictly 'http' or 'https'.
        2. Direct hostname blocking: rejects localhost, .local, .internal, .lan, .corp, .test, .onion.
        3. IP literal inspection: blocks private (RFC 1918), loopback, link-local, multicast,
           reserved, and unspecified IP literals.
        4. DNS resolution validation: resolves hostname via socket.getaddrinfo and inspects all
           resolved IP addresses to prevent DNS rebinding to internal/private IPs.
        5. Fail-Closed Security Policy: if DNS resolution fails (socket.gaierror, socket.herror,
           OSError), the URL is strictly REJECTED (returns False). Never falls back to allow-by-default
           hostname heuristics, preventing DNS-evasion SSRF bypasses.
        """
        if not url or not isinstance(url, str):
            return False

        try:
            parsed = urlparse(url.strip())
        except Exception:
            return False

        # 1. Scheme validation
        if parsed.scheme.lower() not in ("http", "https"):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        host_lower = hostname.lower()

        # 2. Block well-known internal/local hostnames directly
        if host_lower in ("localhost", "localhost.localdomain") or host_lower.endswith(
            (".local", ".internal", ".lan", ".corp", ".test", ".onion")
        ):
            return False

        # 3. Check if hostname is an IP literal
        try:
            ip_str = host_lower.strip("[]")
            ip_obj = ipaddress.ip_address(ip_str)
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_reserved
                or ip_obj.is_multicast
                or ip_obj.is_unspecified
            ):
                return False
            return True
        except ValueError:
            pass

        # 4. DNS resolution validation (DNS rebinding / resolving to internal IPs)
        # Fail-closed: on resolution failure (socket.gaierror, socket.herror, OSError), reject URL immediately
        try:
            port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
            addr_info = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            if not addr_info:
                return False
            for entry in addr_info:
                sockaddr = entry[4]
                ip_obj = ipaddress.ip_address(sockaddr[0])
                if (
                    ip_obj.is_private
                    or ip_obj.is_loopback
                    or ip_obj.is_link_local
                    or ip_obj.is_reserved
                    or ip_obj.is_multicast
                    or ip_obj.is_unspecified
                ):
                    return False
            return True
        except (socket.gaierror, socket.herror, OSError) as exc:
            logger.warning(
                "SSRF validation rejected URL '%s': DNS resolution failed (fail-closed policy enforced): %s",
                url,
                exc,
            )
            return False

    async def deliver_payload_async(
        self,
        target_url: str,
        delivery: WebhookDeliveryPayload,
        timeout: float = 3.0,
    ) -> bool:
        """Safely delivers a signed webhook payload with strict timeout and security headers."""
        if not self.validate_target_url(target_url):
            logger.warning("Rejected webhook delivery to invalid URL: %s", target_url)
            return False

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "CF-Intelligence-Webhook/1.0",
            "X-CFI-Event-Id": delivery.event_id,
            "X-CFI-Event-Type": delivery.event_type.value,
            "X-CFI-Signature-256": delivery.signature,
            "X-CFI-Timestamp": delivery.timestamp.isoformat(),
        }

        try:
            import httpx

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    target_url,
                    json=delivery.payload,
                    headers=headers,
                )
                logger.info(
                    "Webhook delivery %s to %s returned HTTP %d",
                    delivery.event_id,
                    target_url,
                    response.status_code,
                )
                return response.is_success
        except Exception as exc:
            logger.warning(
                "Webhook delivery %s to %s failed (%s: %s)",
                delivery.event_id,
                target_url,
                type(exc).__name__,
                exc,
            )
            return False

    def get_subscriptions(self, tenant_id: str) -> list[WebhookSubscription]:
        """Retrieves tenant active webhook subscriptions."""
        return list(self._subscriptions.get(tenant_id, []))
