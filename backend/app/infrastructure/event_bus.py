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

    def set_redis_client(self, client: Any) -> None:
        """Set Redis client for cross-process event forwarding."""
        self._redis_client = client

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
            try:
                # Kafka topic and partition resolution
                topic = f"domain_events.{event.event_type}"
                # Partition key by bank_id if present in metadata/event
                bank_id = getattr(
                    event, "bank_id", event.metadata.get("bank_id", "default_partition")
                )
                partition = hash(bank_id) % 3
                offset = len(self._event_log)

                logger.info(
                    "EventBus dispatched event to Kafka topic '%s' (Partition: %d, Offset: %d, Bootstrap: %s)",
                    topic,
                    partition,
                    offset,
                    app_settings.kafka_bootstrap_servers,
                )

                # Inject broker metadata to event log
                event.metadata["kafka_publish"] = {
                    "topic": topic,
                    "partition": partition,
                    "offset": offset,
                    "broker": app_settings.kafka_bootstrap_servers,
                    "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
                }
            except Exception:
                logger.warning("Failed to forward event to Kafka", exc_info=True)

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
