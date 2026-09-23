"""Comprehensive unit and integration test suite for Enterprise CloudEvents 1.0 & Apache Kafka Streaming Connector.

Validates:
1. CloudEvents 1.0 specification adherence, mandatory attributes, and RFC compliance.
2. Structured financial domain payloads (Transactions, Alerts, SEPA Recalls, FININT Tickets).
3. In-memory virtual Kafka broker loopback engine (partitions, offsets, consumer groups).
4. At-least-once message delivery and sequential batch publishing.
5. Distributed idempotency engine with deduplication key enforcement.
6. Dead Letter Queue (DLQ) automatic isolation and quarantine for poisoned payloads.
7. End-to-end integration with StreamingEngine and BankConnectorFactory.
8. Operational health check and telemetry metrics.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.application.schemas.event_schemas import (
    EVENT_TYPE_ALERT,
    EVENT_TYPE_TRANSACTION,
    AlertEventData,
    CloudEvent,
    DLQEnvelope,
    RecallEventData,
    TransactionEventData,
)
from app.application.services.streaming_engine import StreamingEngine
from app.infrastructure.connectors.factory import (
    APPROVED_PRODUCTION_CONNECTORS,
    CONNECTOR_REGISTRY,
)
from app.infrastructure.connectors.kafka_streaming_connector import (
    IdempotencyEngine,
    InMemoryKafkaBroker,
    KafkaStreamingConnector,
)

# ── 1. CloudEvents 1.0 Schemas Test Suite ─────────────────────────────────────

class TestCloudEvents10Schemas:
    """Validates CloudEvents 1.0 specification invariants and domain event models."""

    def test_cloudevent_creation_valid(self) -> None:
        """Verify standard CloudEvent 1.0 envelope creation with defaults."""
        event = CloudEvent(
            source="urn:cfi:bank:bank_alpha",
            type=EVENT_TYPE_TRANSACTION,
            data={"amount": 1250.50, "currency": "EUR"},
            bank_id="bank_alpha",
            correlation_id="corr-12345",
        )
        assert event.specversion == "1.0"
        assert event.id is not None
        assert event.source == "urn:cfi:bank:bank_alpha"
        assert event.type == EVENT_TYPE_TRANSACTION
        assert event.datacontenttype == "application/json"
        assert event.ce_bank_id == "bank_alpha"
        assert event.ce_correlation_id == "corr-12345"

    def test_cloudevent_validation_missing_fields(self) -> None:
        """Verify that empty or missing mandatory fields raise validation errors."""
        with pytest.raises(ValueError):
            CloudEvent(source="", type=EVENT_TYPE_TRANSACTION)

        with pytest.raises(ValueError):
            CloudEvent(source="urn:cfi:bank:bank_alpha", type="")

        with pytest.raises(ValueError):
            CloudEvent(id="   ", source="urn:cfi:bank:bank_alpha", type=EVENT_TYPE_TRANSACTION)

    def test_cloudevent_serialization_roundtrip(self) -> None:
        """Verify JSON round-trip serialization and deserialization."""
        original = CloudEvent(
            id="evt-unique-001",
            source="urn:cfi:node:core-gateway",
            type=EVENT_TYPE_ALERT,
            subject="alert:ALT-999",
            data={"severity": "HIGH", "score": 875.0},
            bank_id="bank_beta",
            idempotency_key="idemp-key-999",
        )
        json_bytes = original.to_json_bytes()
        assert isinstance(json_bytes, bytes)

        restored = CloudEvent.from_raw_json(json_bytes)
        assert restored.id == original.id
        assert restored.source == original.source
        assert restored.type == original.type
        assert restored.subject == "alert:ALT-999"
        assert restored.ce_bank_id == "bank_beta"
        assert restored.ce_idempotency_key == "idemp-key-999"

    def test_transaction_payload_schema(self) -> None:
        """Verify TransactionEventData schema validation and constraints."""
        txn = TransactionEventData(
            transaction_id="TX-EUR-1001",
            amount=Decimal("4500.00"),
            currency="EUR",
            originator_iban_hash="hash_orig_123",
            beneficiary_iban_hash="hash_bene_456",
            origin_country="DE",
            destination_country="FR",
            payment_rail="SEPA_INSTANT",
            risk_score=150.0,
            merchant_category="crypto_gateway",
            direction="OUTBOUND",
        )
        assert txn.amount == Decimal("4500.00")
        assert txn.currency == "EUR"
        assert txn.direction == "OUTBOUND"

        # Invalid currency code
        with pytest.raises(ValueError):
            TransactionEventData(
                transaction_id="TX-BAD",
                amount=Decimal("100.00"),
                currency="EUROPE",
                originator_iban_hash="h1",
                beneficiary_iban_hash="h2",
            )

        # Negative amount
        with pytest.raises(ValueError):
            TransactionEventData(
                transaction_id="TX-BAD",
                amount=Decimal("-50.00"),
                currency="EUR",
                originator_iban_hash="h1",
                beneficiary_iban_hash="h2",
            )

    def test_alert_and_recall_payload_schemas(self) -> None:
        """Verify AlertEventData and RecallEventData schemas."""
        alert = AlertEventData(
            alert_id="ALT-001",
            transaction_id="TX-001",
            bank_id="bank_alpha",
            severity="CRITICAL",
            composite_risk_score=940.0,
            triggered_rules=["RULE_MANDATORY_SANCTIONS_BLOCK"],
            action_recommended="IMMEDIATE_BLOCK",
        )
        assert alert.severity == "CRITICAL"
        assert alert.action_recommended == "IMMEDIATE_BLOCK"

        recall = RecallEventData(
            recall_id="REC-001",
            message_id="MSG-CAMT-056-001",
            original_instruction_id="INST-999",
            amount_eur=Decimal("125000.00"),
            reason_code="FRAD",
            status="INITIATED",
            originating_bank="bank_alpha",
            target_bank="bank_beta",
        )
        assert recall.reason_code == "FRAD"
        assert recall.amount_eur == Decimal("125000.00")


# ── 2. In-Memory Kafka Broker Loopback Suite ──────────────────────────────────

class TestInMemoryKafkaBroker:
    """Validates the in-memory virtual Kafka broker engine."""

    @pytest.mark.asyncio
    async def test_publish_and_offset_tracking(self) -> None:
        """Verify topic partitioning and monotonic offset incrementation."""
        broker = InMemoryKafkaBroker()
        topic = "test.topic.orders"

        p0, off0 = await broker.publish(topic, b"msg-1")
        p1, off1 = await broker.publish(topic, b"msg-2")
        p2, off2 = await broker.publish(topic, b"msg-3")

        assert p0 == 0 and off0 == 0
        assert p1 == 0 and off1 == 1
        assert p2 == 0 and off2 == 2

    @pytest.mark.asyncio
    async def test_consumer_group_offset_fetch(self) -> None:
        """Verify consumer group offset progression across sequential batch fetches."""
        broker = InMemoryKafkaBroker()
        topic = "test.topic.payments"
        group_id = "group-audit-workers"

        for i in range(5):
            await broker.publish(topic, f"payload-{i}".encode())

        # First fetch: 3 messages
        batch1 = await broker.fetch_messages(topic, group_id, max_messages=3)
        assert len(batch1) == 3
        assert [m.decode() for m in batch1] == ["payload-0", "payload-1", "payload-2"]

        # Second fetch: remaining 2 messages
        batch2 = await broker.fetch_messages(topic, group_id, max_messages=3)
        assert len(batch2) == 2
        assert [m.decode() for m in batch2] == ["payload-3", "payload-4"]

        # Third fetch: empty
        batch3 = await broker.fetch_messages(topic, group_id, max_messages=3)
        assert len(batch3) == 0

    @pytest.mark.asyncio
    async def test_realtime_subscription_listener(self) -> None:
        """Verify real-time queue subscription dispatch."""
        broker = InMemoryKafkaBroker()
        topic = "test.topic.telemetry"

        queue = await broker.subscribe(topic)
        await broker.publish(topic, b"telemetry-tick-1")

        msg = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert msg == b"telemetry-tick-1"
        broker.unsubscribe(topic, queue)


# ── 3. Kafka Streaming Connector Producer & Idempotency Suite ────────────────

class TestKafkaStreamingConnectorPublishing:
    """Validates KafkaStreamingConnector publishing and deduplication."""

    @pytest.fixture
    def broker(self) -> InMemoryKafkaBroker:
        return InMemoryKafkaBroker()

    @pytest.fixture
    def connector(self, broker: InMemoryKafkaBroker) -> KafkaStreamingConnector:
        return KafkaStreamingConnector(
            bootstrap_servers="localhost:9092",
            in_memory_broker=broker,
            enable_idempotency=True,
        )

    @pytest.mark.asyncio
    async def test_publish_cloudevent_success(self, connector: KafkaStreamingConnector) -> None:
        """Verify successful publishing of a CloudEvent with receipt."""
        event = CloudEvent(
            id="ce-tx-001",
            source="urn:cfi:bank:ALPHA",
            type=EVENT_TYPE_TRANSACTION,
            data={"tx_id": "TX-1", "amount": 100.0},
        )
        receipt = await connector.publish(event)

        assert receipt.event_id == "ce-tx-001"
        assert receipt.topic == connector.transaction_topic
        assert receipt.status == "COMMITTED"
        assert receipt.offset == 0
        assert receipt.idempotent_duplicate is False

    @pytest.mark.asyncio
    async def test_publish_batch(self, connector: KafkaStreamingConnector) -> None:
        """Verify batch publishing of multiple CloudEvents."""
        events = [
            CloudEvent(
                id=f"batch-evt-{i}",
                source="urn:cfi:bank:ALPHA",
                type=EVENT_TYPE_TRANSACTION,
                data={"index": i},
            )
            for i in range(4)
        ]
        receipts = await connector.publish_batch(events)

        assert len(receipts) == 4
        assert [r.offset for r in receipts] == [0, 1, 2, 3]
        assert all(r.status == "COMMITTED" for r in receipts)

    @pytest.mark.asyncio
    async def test_distributed_idempotency_deduplication(
        self, connector: KafkaStreamingConnector
    ) -> None:
        """Verify that resending the same event ID is recognized as a duplicate and skipped."""
        event = CloudEvent(
            id="duplicate-candidate-99",
            source="urn:cfi:bank:ALPHA",
            type=EVENT_TYPE_TRANSACTION,
            data={"amount": 5000.0},
            idempotency_key="idemp-key-unique-777",
        )

        # First delivery -> COMMITTED
        receipt1 = await connector.publish(event)
        assert receipt1.status == "COMMITTED"
        assert receipt1.idempotent_duplicate is False

        # Duplicate delivery -> DUPLICATE_IGNORED
        receipt2 = await connector.publish(event)
        assert receipt2.status == "DUPLICATE_IGNORED"
        assert receipt2.idempotent_duplicate is True

        # Telemetry metrics reflect the rejection
        health = connector.health_check()
        assert health["duplicates_rejected"] == 1
        assert health["total_published"] == 1


# ── 4. Dead Letter Queue (DLQ) Error Isolation Suite ──────────────────────────

class TestDeadLetterQueueIsolation:
    """Validates DLQ routing on malformed payloads and parsing exceptions."""

    @pytest.fixture
    def broker(self) -> InMemoryKafkaBroker:
        return InMemoryKafkaBroker()

    @pytest.fixture
    def connector(self, broker: InMemoryKafkaBroker) -> KafkaStreamingConnector:
        return KafkaStreamingConnector(
            in_memory_broker=broker,
            dlq_topic="cfi.dlq.unparseable",
        )

    @pytest.mark.asyncio
    async def test_publish_malformed_dict_routes_to_dlq(
        self, connector: KafkaStreamingConnector
    ) -> None:
        """Publishing a dictionary missing CloudEvents mandatory fields must route to DLQ."""
        bad_payload: dict[str, Any] = {
            "not_a_valid_cloudevent": True,
            # Missing mandatory 'source' and 'type'
        }
        receipt = await connector.publish(bad_payload)

        assert receipt.status == "DLQ_ROUTED"
        assert receipt.topic == "cfi.dlq.unparseable"

        health = connector.health_check()
        assert health["total_dlq_quarantined"] == 1

    @pytest.mark.asyncio
    async def test_process_incoming_raw_corrupted_json(
        self, connector: KafkaStreamingConnector
    ) -> None:
        """Processing completely corrupted raw bytes returns DLQEnvelope and commits to DLQ topic."""
        corrupted_bytes = b"<<corrupted-binary-payload>>"

        result = await connector.process_incoming_raw(corrupted_bytes, topic="cfi.finint.transactions.v1")

        assert isinstance(result, DLQEnvelope)
        assert result.original_topic == "cfi.finint.transactions.v1"
        assert result.dlq_topic == "cfi.dlq.unparseable"
        assert result.can_retry is False
        assert "JSONDecodeError" in result.error_type or "ValueError" in result.error_type


# ── 5. Kafka Streaming Consumer Suite ─────────────────────────────────────────

class TestKafkaStreamingConsumer:
    """Validates batch consuming and offset progression."""

    @pytest.fixture
    def broker(self) -> InMemoryKafkaBroker:
        return InMemoryKafkaBroker()

    @pytest.fixture
    def connector(self, broker: InMemoryKafkaBroker) -> KafkaStreamingConnector:
        return KafkaStreamingConnector(in_memory_broker=broker)

    @pytest.mark.asyncio
    async def test_consume_batch_success(self, connector: KafkaStreamingConnector) -> None:
        """Publishing events and consuming them in batch preserves order and properties."""
        topic = "cfi.finint.transactions.v1"
        for i in range(3):
            ev = CloudEvent(
                id=f"consume-ev-{i}",
                source="urn:cfi:bank:ALPHA",
                type=EVENT_TYPE_TRANSACTION,
                data={"seq": i, "amount": 100.0 * (i + 1)},
            )
            await connector.publish(ev, topic=topic)

        consumed = await connector.consume_batch(topic, max_messages=10)
        assert len(consumed) == 3
        assert [c.id for c in consumed] == ["consume-ev-0", "consume-ev-1", "consume-ev-2"]
        assert consumed[0].data["amount"] == 100.0


# ── 6. Streaming Engine & Connector Factory Integration ───────────────────────

class TestStreamingEngineAndFactoryIntegration:
    """Validates integration with StreamingEngine and BankConnectorFactory."""

    @pytest.mark.asyncio
    async def test_streaming_engine_has_kafka_connector(self) -> None:
        """Verify StreamingEngine initializes with a functional KafkaStreamingConnector."""
        engine = StreamingEngine()
        assert engine.kafka_connector is not None
        assert isinstance(engine.kafka_connector, KafkaStreamingConnector)

        # Publish a standalone CloudEvent through StreamingEngine
        receipt = await engine.stream_cloudevent(
            {
                "id": str(uuid.uuid4()),
                "source": "urn:cfi:scenario:test",
                "type": EVENT_TYPE_TRANSACTION,
                "data": {"amount": 500.0, "currency": "EUR"},
            }
        )
        assert receipt.status == "COMMITTED"

    def test_factory_registry_contains_kafka_streaming(self) -> None:
        """Verify BankConnectorFactory exposes kafka_streaming in registry and approved list."""
        assert "kafka_streaming" in APPROVED_PRODUCTION_CONNECTORS
        assert "cloudevents" in APPROVED_PRODUCTION_CONNECTORS
        assert CONNECTOR_REGISTRY["kafka_streaming"] is KafkaStreamingConnector
        assert CONNECTOR_REGISTRY["cloudevents"] is KafkaStreamingConnector


# ── 7. Health & Telemetry Metrics Suite ───────────────────────────────────────

class TestHealthAndTelemetryMetrics:
    """Validates health check telemetry output."""

    def test_health_check_payload(self) -> None:
        """Verify health check returns comprehensive telemetry dictionary."""
        connector = KafkaStreamingConnector(
            bootstrap_servers="kafka-broker:9092",
            client_id="test-client-1",
        )
        health = connector.health_check()
        assert health["status"] == "HEALTHY"
        assert health["bootstrap_servers"] == "kafka-broker:9092"
        assert health["client_id"] == "test-client-1"
        assert "total_published" in health
        assert "total_consumed" in health
        assert "total_dlq_quarantined" in health
        assert "duplicates_rejected" in health
        assert "avg_latency_ms" in health

    @pytest.mark.asyncio
    async def test_process_incoming_raw_valid_payload(self) -> None:
        """Verify process_incoming_raw successfully deserializes valid CloudEvent JSON."""
        connector = KafkaStreamingConnector()
        ev = CloudEvent(
            id="raw-valid-01",
            source="urn:cfi:bank:ALPHA",
            type=EVENT_TYPE_TRANSACTION,
            data={"amount": 999.0},
        )
        res = await connector.process_incoming_raw(ev.to_json_bytes(), topic=connector.transaction_topic)
        assert isinstance(res, CloudEvent)
        assert res.id == "raw-valid-01"
        assert res.data["amount"] == 999.0

    @pytest.mark.asyncio
    async def test_cloudevent_with_pydantic_model_data(self) -> None:
        """Verify CloudEvent serialization when data is a Pydantic BaseModel instance."""
        txn_data = TransactionEventData(
            transaction_id="TX-PYD-99",
            amount=Decimal("1500.00"),
            currency="EUR",
            originator_iban_hash="orig_hash",
            beneficiary_iban_hash="bene_hash",
            origin_country="DE",
            destination_country="FR",
        )
        ce = CloudEvent(
            source="urn:cfi:bank:ALPHA",
            type=EVENT_TYPE_TRANSACTION,
            data=txn_data,
        )
        ce_dict = ce.to_cloudevent_dict()
        assert ce_dict["data"]["transaction_id"] == "TX-PYD-99"
        assert ce_dict["data"]["amount"] == "1500.00"

    def test_idempotency_engine_manual_eviction(self) -> None:
        """Verify IdempotencyEngine clear and key lifecycle."""
        engine = IdempotencyEngine(ttl_seconds=3600)
        assert engine.is_duplicate("key-1") is False
        engine.mark_processed("key-1")
        assert engine.is_duplicate("key-1") is True
        engine.clear()
        assert engine.is_duplicate("key-1") is False

    @pytest.mark.asyncio
    async def test_reset_consumer_offsets(self) -> None:
        """Verify consumer offset reset allows re-reading committed messages."""
        connector = KafkaStreamingConnector()
        topic = "cfi.finint.replayed.v1"
        ev = CloudEvent(
            id="replay-01",
            source="urn:cfi:bank:ALPHA",
            type=EVENT_TYPE_TRANSACTION,
            data={"counter": 1},
        )
        await connector.publish(ev, topic=topic)

        # First read
        batch1 = await connector.consume_batch(topic, max_messages=5)
        assert len(batch1) == 1

        # Second read without reset is empty
        batch2 = await connector.consume_batch(topic, max_messages=5)
        assert len(batch2) == 0

        # Reset offsets and re-read
        connector.reset_offsets(topic)
        batch3 = await connector.consume_batch(topic, max_messages=5)
        assert len(batch3) == 1
        assert batch3[0].id == "replay-01"
