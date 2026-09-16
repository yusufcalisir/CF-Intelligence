"""Resilient Webhook Payload Dispatcher with Exponential Backoff Retry.

Implements reliable outbound webhook delivery with:
- Configurable exponential backoff retries with jitter
- Delivery history audit ring buffer for observability
- Non-blocking asynchronous execution via httpx.AsyncClient
- Anti-SSRF URL validation prior to every delivery attempt
- Threat-mitigated fail-closed delivery error handling
"""

from __future__ import annotations

import asyncio
import collections
import logging
import random
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from app.application.services.webhook_service import WebhookDeliveryPayload

logger = logging.getLogger(__name__)


@dataclass
class WebhookDeliveryAttempt:
    """Dataclass capturing an outbound webhook delivery execution."""

    delivery_id: str
    target_url: str
    event_type: str
    status_code: int | None = None
    success: bool = False
    attempt_count: int = 1
    error_message: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class WebhookDispatcher:
    """Outbound webhook delivery dispatcher with exponential backoff and telemetry."""

    _instance: WebhookDispatcher | None = None
    _singleton_lock = threading.RLock()

    def __init__(self, history_limit: int = 200) -> None:
        self._lock = threading.RLock()
        self._history_limit = history_limit
        self._delivery_history: collections.deque[WebhookDeliveryAttempt] = collections.deque(
            maxlen=history_limit
        )
        self._total_deliveries: int = 0
        self._successful_deliveries: int = 0
        self._failed_deliveries: int = 0

    @classmethod
    def get_instance(cls) -> WebhookDispatcher:
        """Singleton accessor for WebhookDispatcher."""
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    async def deliver_with_retry(
        self,
        target_url: str,
        delivery: WebhookDeliveryPayload,
        max_retries: int = 3,
        initial_delay: float = 0.5,
        backoff_factor: float = 2.0,
        timeout: float = 3.0,
    ) -> bool:
        """Deliver payload to target_url with exponential backoff retry.

        Args:
            target_url: Destination HTTP/HTTPS endpoint.
            delivery: Prepared WebhookDeliveryPayload with signature and headers.
            max_retries: Maximum delivery attempts before marking as failed.
            initial_delay: Initial delay seconds before first retry.
            backoff_factor: Multiplier for consecutive retry backoffs.
            timeout: HTTP request timeout in seconds per attempt.

        Returns:
            bool: True if delivered successfully (HTTP 2xx), False otherwise.
        """
        # Late import to avoid circular dependency
        from app.application.services.webhook_service import WebhookService

        if not WebhookService.validate_target_url(target_url):
            logger.warning(
                "WebhookDispatcher: Rejected delivery to SSRF-disallowed URL: %s", target_url
            )
            self._record_attempt(
                WebhookDeliveryAttempt(
                    delivery_id=delivery.event_id,
                    target_url=target_url,
                    event_type=delivery.event_type.value,
                    status_code=None,
                    success=False,
                    attempt_count=0,
                    error_message="SSRF validation rejected target URL",
                )
            )
            return False

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "CF-Intelligence-WebhookDispatcher/1.0",
            "X-CFI-Event-Id": delivery.event_id,
            "X-CFI-Event-Type": delivery.event_type.value,
            "X-CFI-Signature-256": delivery.signature,
            "X-CFI-Timestamp": delivery.timestamp.isoformat(),
        }

        last_error: str | None = None
        last_status: int | None = None
        current_delay = initial_delay

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        target_url,
                        json=delivery.payload,
                        headers=headers,
                    )
                    last_status = response.status_code

                    if response.is_success:
                        logger.info(
                            "WebhookDispatcher: Delivery %s succeeded on attempt %d/%d (Status %d)",
                            delivery.event_id,
                            attempt,
                            max_retries,
                            last_status,
                        )
                        self._record_attempt(
                            WebhookDeliveryAttempt(
                                delivery_id=delivery.event_id,
                                target_url=target_url,
                                event_type=delivery.event_type.value,
                                status_code=last_status,
                                success=True,
                                attempt_count=attempt,
                            )
                        )
                        return True

                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning(
                        "WebhookDispatcher: Delivery %s attempt %d/%d returned HTTP %d",
                        delivery.event_id,
                        attempt,
                        max_retries,
                        last_status,
                    )

            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "WebhookDispatcher: Delivery %s attempt %d/%d failed with exception: %s",
                    delivery.event_id,
                    attempt,
                    max_retries,
                    last_error,
                )

            # Apply exponential backoff with jitter if more attempts remain
            if attempt < max_retries:
                jitter = random.uniform(0.8, 1.2)  # nosec B311
                sleep_time = current_delay * jitter
                await asyncio.sleep(sleep_time)
                current_delay *= backoff_factor

        # All attempts exhausted
        self._record_attempt(
            WebhookDeliveryAttempt(
                delivery_id=delivery.event_id,
                target_url=target_url,
                event_type=delivery.event_type.value,
                status_code=last_status,
                success=False,
                attempt_count=max_retries,
                error_message=last_error,
            )
        )
        return False

    def _record_attempt(self, attempt: WebhookDeliveryAttempt) -> None:
        """Record an attempt in the thread-safe ring buffer and update stats."""
        with self._lock:
            self._delivery_history.append(attempt)
            self._total_deliveries += 1
            if attempt.success:
                self._successful_deliveries += 1
            else:
                self._failed_deliveries += 1

    def get_delivery_history(self, limit: int = 50) -> list[WebhookDeliveryAttempt]:
        """Retrieve recent delivery attempts in reverse chronological order."""
        with self._lock:
            items = list(self._delivery_history)
        items.reverse()
        return items[:limit]

    def get_metrics(self) -> dict[str, Any]:
        """Retrieve aggregate delivery metrics."""
        with self._lock:
            return {
                "total_deliveries": self._total_deliveries,
                "successful_deliveries": self._successful_deliveries,
                "failed_deliveries": self._failed_deliveries,
                "history_count": len(self._delivery_history),
            }

    def clear_history(self) -> None:
        """Reset history and counters."""
        with self._lock:
            self._delivery_history.clear()
            self._total_deliveries = 0
            self._successful_deliveries = 0
            self._failed_deliveries = 0


_dispatcher_singleton: WebhookDispatcher | None = None


def get_webhook_dispatcher() -> WebhookDispatcher:
    """Accessor for global WebhookDispatcher instance."""
    global _dispatcher_singleton
    if _dispatcher_singleton is None:
        _dispatcher_singleton = WebhookDispatcher()
    return _dispatcher_singleton
