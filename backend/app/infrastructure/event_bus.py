"""Domain event bus.

Lightweight in-process event bus for decoupling domain events from
their handlers. Backed by Redis pub/sub for cross-process delivery
when running in multi-worker mode.

Design decision: We use a simple observer pattern rather than a full
message broker (Kafka, RabbitMQ) because:
1. The simulator runs as a single deployment unit
2. Event volume is low (hundreds/hour, not millions)
3. Redis pub/sub already exists for WebSocket delivery
4. Adding a message broker would add operational complexity
   disproportionate to the simulator's scale

In production, you'd replace this with a proper event streaming
platform (Kafka, AWS EventBridge, etc.).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DomainEvent:
    """Base class for all domain events."""

    event_type: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict = field(default_factory=dict)


@dataclass
class AlertCreated(DomainEvent):
    event_type: str = "alert.created"
    alert_id: str = ""
    bank_id: str = ""
    severity: str = ""
    risk_score: float = 0.0


@dataclass
class CaseOpened(DomainEvent):
    event_type: str = "case.opened"
    case_id: str = ""
    title: str = ""
    priority: str = ""
    alert_count: int = 0


@dataclass
class CaseStatusChanged(DomainEvent):
    event_type: str = "case.status_changed"
    case_id: str = ""
    old_status: str = ""
    new_status: str = ""
    actor: str = ""


@dataclass
class IntelligencePublished(DomainEvent):
    event_type: str = "intelligence.published"
    intelligence_id: str = ""
    source_bank_id: str = ""
    intelligence_type: str = ""
    risk_indicator: float = 0.0


@dataclass
class EntityResolved(DomainEvent):
    event_type: str = "entity.resolved"
    entity_id: str = ""
    privacy_hash: str = ""
    banks_matched: list[str] = field(default_factory=list)
    match_count: int = 0


# Type alias for event handlers
EventHandler = Callable[[DomainEvent], None]


class EventBus:
    """In-process domain event bus.

    Publishes domain events to registered handlers. Also forwards
    events to Redis pub/sub for cross-process consumption.

    Usage:
        bus = EventBus()
        bus.subscribe("alert.created", my_handler)
        bus.publish(AlertCreated(alert_id="123", ...))
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._event_log: list[DomainEvent] = []
        self._redis_client = None
        self._kafka_producer = None

    def set_redis_client(self, client: Any) -> None:
        """Set Redis client for cross-process event forwarding."""
        self._redis_client = client

    def set_kafka_producer(self, producer: Any) -> None:
        """Set Kafka producer client for broker event forwarding."""
        self._kafka_producer = producer

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for a specific event type."""
        self._handlers[event_type].append(handler)
        logger.debug("Subscribed handler for %s", event_type)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Remove a handler."""
        if handler in self._handlers.get(event_type, []):
            self._handlers[event_type].remove(handler)

    def publish(self, event: DomainEvent) -> None:
        """Publish a domain event to all registered handlers.

        Events are logged for audit purposes and optionally forwarded
        to Redis pub/sub.
        """
        self._event_log.append(event)
        handlers = self._handlers.get(event.event_type, [])

        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Handler failed for event %s",
                    event.event_type,
                )

        # Forward to Redis if available
        if self._redis_client:
            try:
                channel = f"domain_events:{event.event_type}"
                import redis as sync_redis

                if isinstance(self._redis_client, sync_redis.Redis):
                    self._redis_client.publish(channel, json.dumps(asdict(event)))
            except Exception:
                logger.warning("Failed to forward event to Redis", exc_info=True)

        # Forward to Kafka/Redpanda if enabled
        from app.config import get_settings

        app_settings = get_settings()
        if app_settings.use_kafka:
            topic = f"domain_events.{event.event_type}"
            if self._kafka_producer is not None:
                try:
                    bank_id = getattr(
                        event, "bank_id", event.metadata.get("bank_id", "default_partition")
                    )
                    payload_bytes = json.dumps(asdict(event)).encode("utf-8")
                    if hasattr(self._kafka_producer, "send"):
                        future = self._kafka_producer.send(
                            topic, key=str(bank_id).encode("utf-8"), value=payload_bytes
                        )
                        if hasattr(future, "get"):
                            rm = future.get(timeout=2.0)
                            event.metadata["kafka_publish"] = {
                                "status": "PUBLISHED",
                                "topic": getattr(rm, "topic", topic),
                                "partition": getattr(rm, "partition", None),
                                "offset": getattr(rm, "offset", None),
                                "broker": app_settings.kafka_bootstrap_servers,
                            }
                        else:
                            event.metadata["kafka_publish"] = {
                                "status": "DISPATCHED",
                                "topic": topic,
                                "broker": app_settings.kafka_bootstrap_servers,
                            }
                except Exception as exc:
                    logger.warning("Failed to forward event to Kafka: %s", exc)
                    event.metadata["kafka_publish"] = {
                        "status": "FAILED",
                        "error": str(exc),
                        "topic": topic,
                    }
            else:
                logger.debug(
                    "Kafka is enabled in settings (bootstrap=%s), but no active Kafka producer client is connected. Event logged locally without fabricated offset.",
                    app_settings.kafka_bootstrap_servers,
                )
                event.metadata["kafka_publish"] = {
                    "status": "UNAVAILABLE",
                    "reason": "NO_ACTIVE_KAFKA_PRODUCER_CLIENT",
                    "topic": topic,
                    "broker": app_settings.kafka_bootstrap_servers,
                }

        logger.debug("Published %s (%d handlers)", event.event_type, len(handlers))

    def get_event_log(self, event_type: str | None = None, limit: int = 100) -> list[DomainEvent]:
        """Retrieve recent events from the audit log."""
        events = self._event_log
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events[-limit:]

    @property
    def event_count(self) -> int:
        return len(self._event_log)


# Global singleton instance
event_bus = EventBus()
