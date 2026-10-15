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
import json
import multiprocessing
import os
import sys
import time
import uuid
from decimal import Decimal
from typing import Any

import pytest
import redis

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

_backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if "PYTHONPATH" in os.environ:
    if _backend_dir not in os.environ["PYTHONPATH"]:
        os.environ["PYTHONPATH"] = f"{_backend_dir};{os.environ['PYTHONPATH']}"
else:
    os.environ["PYTHONPATH"] = _backend_dir


def _mp_worker_race_entry(
    worker_idx: int,
    key: str,
    ttl: int,
    barrier: Any,
    out_q: Any,
) -> None:
    """Independent spawned OS worker competing for an idempotency key."""
    import os

    from app.application.services.idempotency import IdempotencyService
    from app.infrastructure.connectors.kafka_streaming_connector import IdempotencyEngine

    os.environ["APP_ENV"] = "production"
    IdempotencyService._instance = None
    engine = IdempotencyEngine(ttl_seconds=ttl, engine_id=f"worker_race_{worker_idx}_{os.getpid()}")
    try:
        barrier.wait(timeout=10.0)
        acquired = engine.try_acquire(key)
        out_q.put((worker_idx, os.getpid(), engine._engine_id, acquired))
    except Exception as exc:
        out_q.put((worker_idx, os.getpid(), "error", str(exc)))


def _mp_worker_process_controller(
    in_q: Any,
    out_q: Any,
) -> None:
    """Controllable spawned OS worker process executing sequential idempotency operations."""
    import os

    from app.application.services.idempotency import IdempotencyService
    from app.infrastructure.connectors.kafka_streaming_connector import IdempotencyEngine

    engine: IdempotencyEngine | None = None

    while True:
        try:
            msg = in_q.get(timeout=20.0)
        except Exception:
            break

        cmd = msg[0]
        if cmd == "init":
            _, ttl, engine_id, env = msg
            os.environ["APP_ENV"] = env
            IdempotencyService._instance = None
            engine = IdempotencyEngine(ttl_seconds=ttl, engine_id=engine_id)
            out_q.put(("init_ok", os.getpid(), engine._engine_id))
        elif cmd == "acquire":
            _, key = msg
            assert engine is not None
            res = engine.try_acquire(key)
            out_q.put(("acquire_res", res))
        elif cmd == "release":
            _, key = msg
            assert engine is not None
            engine.release(key)
            out_q.put(("release_ok", True))
        elif cmd == "mark_processed":
            _, key = msg
            assert engine is not None
            engine.mark_processed(key)
            out_q.put(("mark_processed_ok", True))
        elif cmd == "is_duplicate":
            _, key = msg
            assert engine is not None
            res = engine.is_duplicate(key)
            out_q.put(("is_duplicate_res", res))
        elif cmd == "clear":
            assert engine is not None
            engine.clear()
            out_q.put(("clear_ok", True))
        elif cmd == "stop":
            out_q.put(("stopped", True))
            break

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
    def connector(self, broker: InMemoryKafkaBroker):
        c = KafkaStreamingConnector(
            bootstrap_servers="localhost:9092",
            in_memory_broker=broker,
            enable_idempotency=True,
            idempotency_prefix=f"test_pub_{uuid.uuid4().hex[:8]}",
        )
        yield c
        c.clear_idempotency()

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
    def connector(self, broker: InMemoryKafkaBroker):
        c = KafkaStreamingConnector(
            in_memory_broker=broker,
            idempotency_prefix=f"test_cons_{uuid.uuid4().hex[:8]}",
        )
        yield c
        c.clear_idempotency()

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
        engine = IdempotencyEngine(ttl_seconds=3600, key_prefix=f"test_evict_{uuid.uuid4().hex[:8]}")
        try:
            assert engine.is_duplicate("key-1") is False
            engine.mark_processed("key-1")
            assert engine.is_duplicate("key-1") is True
            engine.clear()
            assert engine.is_duplicate("key-1") is False
        finally:
            engine.clear()

    @pytest.mark.asyncio
    async def test_reset_consumer_offsets(self) -> None:
        """Verify consumer offset reset allows re-reading committed messages."""
        connector = KafkaStreamingConnector(idempotency_prefix=f"test_reset_{uuid.uuid4().hex[:8]}")
        try:
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
        finally:
            connector.clear_idempotency()


# ── 9. Production Reality & Zero-Mock Invariant Tests ─────────────────────────

class TestKafkaStreamingZeroMockInvariants:
    """Verifies fail-closed production semantics, truthful receipts, and strict parsing."""

    def test_production_mode_refuses_in_memory_kafka(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Production environment strictly refuses in-memory loopback without real cluster."""
        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(RuntimeError, match="Production KafkaStreamingConnector requires an external Apache Kafka cluster"):
            KafkaStreamingConnector()

    @pytest.mark.asyncio
    async def test_publish_receipt_contains_truthful_durability(self) -> None:
        """PublishReceipt must truthfully declare IN_MEMORY_LOOPBACK and VOLATILE_PROCESS_MEMORY."""
        broker = InMemoryKafkaBroker()
        connector = KafkaStreamingConnector(in_memory_broker=broker, idempotency_prefix=f"test_truth_{uuid.uuid4().hex[:8]}")
        try:
            ev = CloudEvent(
                id="truth-evt-01",
                source="urn:cfi:bank:ALPHA",
                type=EVENT_TYPE_TRANSACTION,
                data={"amount": 100.0, "currency": "EUR"},
            )
            receipt = await connector.publish(ev)
            assert receipt.status == "COMMITTED"
            assert receipt.delivery_mode == "IN_MEMORY_LOOPBACK"
            assert receipt.durability == "VOLATILE_PROCESS_MEMORY"
        finally:
            connector.clear_idempotency()

    def test_parse_batch_rejects_missing_fields_without_fake_defaults(self) -> None:
        """Batch parsing strictly rejects missing account_id or amount without substituting fake 1.0."""
        connector = KafkaStreamingConnector()

        # Missing account_id
        with pytest.raises(ValueError, match="missing mandatory account_id"):
            connector.parse_batch([{"counterparty_account_id": "cpty1", "amount": 500.0}])

        # Missing counterparty_account_id
        with pytest.raises(ValueError, match="missing mandatory counterparty_account_id"):
            connector.parse_batch([{"account_id": "acc1", "amount": 500.0}])

        # Missing amount
        with pytest.raises(ValueError, match="missing mandatory amount"):
            connector.parse_batch([{"account_id": "acc1", "counterparty_account_id": "cpty1"}])

        # Non-positive amount
        with pytest.raises(ValueError, match="amount must be positive and finite"):
            connector.parse_batch([{"account_id": "acc1", "counterparty_account_id": "cpty1", "amount": -10.0}])


# ── 10. Distributed Redis Idempotency Multi-Worker Safety & Sentinel Preservation ─

class TestKafkaDistributedIdempotencyRedis:
    """Verifies multi-worker Redis idempotency, genuine OS process exclusivity, and stale worker safety."""

    @pytest.fixture(autouse=True)
    def ensure_genuine_redis(self) -> None:
        from app.application.services.idempotency import IdempotencyService

        idem_svc = IdempotencyService.get()
        if idem_svc._redis_client is None:
            idem_svc._init_redis()
        if idem_svc._redis_client is None:
            pytest.fail("Genuine Redis is required for distributed multi-process idempotency tests")
        try:
            idem_svc._redis_client.ping()
        except Exception as exc:
            pytest.fail(f"Genuine Redis is unreachable: {exc}")

    def test_in_progress_reservation_recognized_as_duplicate(self) -> None:
        """An acquired in-progress key must be immediately recognized as a duplicate by other checks."""
        key = f"test-in-prog-{uuid.uuid4().hex}"
        engine = IdempotencyEngine(ttl_seconds=60)
        try:
            acquired = engine.try_acquire(key)
            assert acquired is True
            assert engine.is_duplicate(key) is True
        finally:
            engine.clear()

    def test_concurrent_spawned_os_workers_competing_for_single_key_exactly_one_winner(self) -> None:
        """Genuine spawned OS worker processes with distinct Redis connections race: exactly one wins."""
        ctx = multiprocessing.get_context("spawn")
        key = f"mp-race-{uuid.uuid4().hex}"
        num_workers = 5
        barrier = ctx.Barrier(num_workers)
        out_q = ctx.Queue()

        workers = [
            ctx.Process(target=_mp_worker_race_entry, args=(i, key, 60, barrier, out_q))
            for i in range(num_workers)
        ]
        for w in workers:
            w.start()

        results = [out_q.get(timeout=25.0) for _ in range(num_workers)]
        for w in workers:
            w.join(timeout=5.0)

        # Validate that no worker encountered an error
        for r in results:
            assert r[2] != "error", f"Worker encountered exception: {r}"

        # Exactly 1 worker acquired the reservation; exactly 4 failed
        winners = [r for r in results if r[3] is True]
        losers = [r for r in results if r[3] is False]

        assert len(winners) == 1, f"Expected exactly 1 winner, got {len(winners)}: {results}"
        assert len(losers) == num_workers - 1

        # Clean up key from Redis
        from app.application.services.idempotency import IdempotencyService

        idem_svc = IdempotencyService.get()
        redis_key = idem_svc._build_redis_key(f"kafka:{key}")
        idem_svc._redis_client.delete(redis_key)

    def test_connection_loss_after_initially_successful_redis_fails_closed_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In production, when Redis connection drops mid-flight, acquisition fails closed (no local fallback)."""
        from app.application.services.idempotency import IdempotencyService

        monkeypatch.setenv("APP_ENV", "production")
        idem_svc = IdempotencyService.get()
        assert idem_svc._redis_client is not None
        orig_client = idem_svc._redis_client

        engine = IdempotencyEngine(ttl_seconds=60)
        key = f"test-conn-loss-{uuid.uuid4().hex}"

        # Inject simulated connection drop into live client
        original_set = orig_client.set

        def failing_set(*args: Any, **kwargs: Any) -> Any:
            raise redis.ConnectionError("Simulated socket connection loss to Redis cluster")

        monkeypatch.setattr(orig_client, "set", failing_set)

        try:
            # Must return False (fail closed), NEVER falling back to local memory and returning True
            acquired = engine.try_acquire(key)
            assert acquired is False, "Production acquisition must fail closed on Redis connection loss"

            # Must not be stored in local process memory
            assert key not in engine._processed_keys

            # is_duplicate must fail closed (treat as unsafe/duplicate in production)
            assert engine.is_duplicate(key) is True
        finally:
            idem_svc._redis_client = orig_client
            monkeypatch.setattr(orig_client, "set", original_set)
            engine.clear()

    def test_timeout_with_uncertain_redis_outcome_retry_cannot_create_competing_owners(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Redis command timeout with uncertain outcome fails closed and prevents split-brain ownership."""
        from app.application.services.idempotency import IdempotencyService

        monkeypatch.setenv("APP_ENV", "production")
        idem_svc = IdempotencyService.get()
        orig_client = idem_svc._redis_client
        assert orig_client is not None

        engine_1 = IdempotencyEngine(ttl_seconds=60, engine_id="worker_timeout_1")
        engine_2 = IdempotencyEngine(ttl_seconds=60, engine_id="worker_timeout_2")
        key = f"test-timeout-race-{uuid.uuid4().hex}"

        # Case A: Redis operation times out before completing
        original_set = orig_client.set

        def timeout_set(*args: Any, **kwargs: Any) -> Any:
            raise redis.TimeoutError("Redis command timed out waiting for master ACK")

        monkeypatch.setattr(orig_client, "set", timeout_set)
        try:
            acquired_1 = engine_1.try_acquire(key)
            assert acquired_1 is False, "Timed-out operation must not be reported as acquired"
        finally:
            idem_svc._redis_client = orig_client
            monkeypatch.setattr(orig_client, "set", original_set)

        # Case B: If key was partially registered in Redis before timeout response failed,
        # a competing worker's retry cannot create a second owner
        idem_svc._redis_client.set(
            idem_svc._build_redis_key(f"kafka:{key}"),
            "IN_PROGRESS:worker_timeout_1_partial",
            ex=60,
        )
        acquired_2 = engine_2.try_acquire(key)
        assert acquired_2 is False, "Competing worker cannot acquire key already held in Redis"

        # Cleanup
        idem_svc._redis_client.delete(idem_svc._build_redis_key(f"kafka:{key}"))
        engine_1.clear()
        engine_2.clear()

    def test_worker_a_expires_worker_b_acquires_worker_a_release_and_clear_survives(self) -> None:
        """Worker A reservation expires; Worker B acquires; Worker A release and clear cannot erase Worker B."""
        ctx = multiprocessing.get_context("spawn")
        key = f"test-expiry-race-{uuid.uuid4().hex}"

        in_a, out_a = ctx.Queue(), ctx.Queue()
        in_b, out_b = ctx.Queue(), ctx.Queue()

        proc_a = ctx.Process(target=_mp_worker_process_controller, args=(in_a, out_a))
        proc_b = ctx.Process(target=_mp_worker_process_controller, args=(in_b, out_b))
        proc_a.start()
        proc_b.start()

        try:
            # 1. Initialize Worker A with short TTL (1 second) and Worker B with standard TTL (60 seconds)
            in_a.put(("init", 1, "worker_proc_A", "production"))
            assert out_a.get(timeout=10.0)[0] == "init_ok"

            in_b.put(("init", 60, "worker_proc_B", "production"))
            assert out_b.get(timeout=10.0)[0] == "init_ok"

            # 2. Worker A acquires key with 1-second TTL
            in_a.put(("acquire", key))
            assert out_a.get(timeout=10.0)[1] is True

            # 3. Wait 1.8 seconds for Worker A's reservation to expire in Redis
            time.sleep(1.8)

            # 4. Worker B acquires the expired key
            in_b.put(("acquire", key))
            assert out_b.get(timeout=10.0)[1] is True, "Worker B must acquire key after Worker A expired"

            # 5. Stale Worker A attempts release and clear on the key it no longer owns
            in_a.put(("release", key))
            assert out_a.get(timeout=10.0)[0] == "release_ok"

            in_a.put(("clear",))
            assert out_a.get(timeout=10.0)[0] == "clear_ok"

            # 6. Verify Worker B's reservation in Redis survived Worker A's release and clear!
            from app.application.services.idempotency import IdempotencyService

            idem_svc = IdempotencyService.get()
            raw_val = idem_svc._redis_client.get(idem_svc._build_redis_key(f"kafka:{key}"))
            assert raw_val is not None, "Worker B's reservation must not be deleted by stale Worker A"
            val_str = raw_val.decode("utf-8") if isinstance(raw_val, bytes) else str(raw_val)
            assert "worker_proc_B" in val_str, f"Worker B must still own the reservation, found: {val_str}"

            # 7. Worker B successfully marks it processed (COMMITTED)
            in_b.put(("mark_processed", key))
            assert out_b.get(timeout=10.0)[0] == "mark_processed_ok"

            # Verify in Redis that Worker B successfully committed
            raw_after = idem_svc._redis_client.get(idem_svc._build_redis_key(f"kafka:{key}"))
            val_after = raw_after.decode("utf-8") if isinstance(raw_after, bytes) else str(raw_after)
            assert "COMMITTED" in val_after
            assert "worker_proc_B" in val_after
        finally:
            in_a.put(("stop",))
            in_b.put(("stop",))
            proc_a.join(timeout=5.0)
            proc_b.join(timeout=5.0)
            from app.application.services.idempotency import IdempotencyService

            IdempotencyService.get()._redis_client.delete(
                IdempotencyService.get()._build_redis_key(f"kafka:{key}")
            )

    def test_worker_a_stale_completion_rejected_after_worker_b_takes_ownership(self) -> None:
        """Worker A attempts stale completion after Worker B takes ownership: rejected, no overwrite."""
        ctx = multiprocessing.get_context("spawn")
        key = f"test-stale-complete-{uuid.uuid4().hex}"

        in_a, out_a = ctx.Queue(), ctx.Queue()
        in_b, out_b = ctx.Queue(), ctx.Queue()

        proc_a = ctx.Process(target=_mp_worker_process_controller, args=(in_a, out_a))
        proc_b = ctx.Process(target=_mp_worker_process_controller, args=(in_b, out_b))
        proc_a.start()
        proc_b.start()

        try:
            # 1. Initialize Worker A with 1-second TTL, Worker B with 60-second TTL
            in_a.put(("init", 1, "worker_stale_A", "production"))
            assert out_a.get(timeout=10.0)[0] == "init_ok"

            in_b.put(("init", 60, "worker_stale_B", "production"))
            assert out_b.get(timeout=10.0)[0] == "init_ok"

            # 2. Worker A acquires key
            in_a.put(("acquire", key))
            assert out_a.get(timeout=10.0)[1] is True

            # 3. Wait for Worker A's reservation to expire
            time.sleep(1.8)

            # 4. Worker B acquires key
            in_b.put(("acquire", key))
            assert out_b.get(timeout=10.0)[1] is True

            # 5. Worker A attempts stale completion
            in_a.put(("mark_processed", key))
            assert out_a.get(timeout=10.0)[0] == "mark_processed_ok"

            # 6. Verify in Redis: Worker B's reservation was NOT overwritten!
            from app.application.services.idempotency import IdempotencyService

            idem_svc = IdempotencyService.get()
            raw_val = idem_svc._redis_client.get(idem_svc._build_redis_key(f"kafka:{key}"))
            assert raw_val is not None
            val_str = raw_val.decode("utf-8") if isinstance(raw_val, bytes) else str(raw_val)
            assert "worker_stale_B" in val_str, f"Worker B must remain owner; Worker A must not overwrite: {val_str}"
            assert val_str.startswith("IN_PROGRESS:"), f"Reservation must still be in progress for Worker B: {val_str}"

            # 7. Worker B marks it processed
            in_b.put(("mark_processed", key))
            assert out_b.get(timeout=10.0)[0] == "mark_processed_ok"

            # Now Worker B's committed envelope is in Redis
            raw_final = idem_svc._redis_client.get(idem_svc._build_redis_key(f"kafka:{key}"))
            val_final = raw_final.decode("utf-8") if isinstance(raw_final, bytes) else str(raw_final)
            assert "COMMITTED" in val_final
            assert "worker_stale_B" in val_final
        finally:
            in_a.put(("stop",))
            in_b.put(("stop",))
            proc_a.join(timeout=5.0)
            proc_b.join(timeout=5.0)
            from app.application.services.idempotency import IdempotencyService

            IdempotencyService.get()._redis_client.delete(
                IdempotencyService.get()._build_redis_key(f"kafka:{key}")
            )

    def test_completion_release_and_retry_behavior(self) -> None:
        """Releasing an in-progress reservation allows retry; completing prevents duplicate acquisition."""
        key = f"test-release-{uuid.uuid4().hex}"
        worker_1 = IdempotencyEngine(ttl_seconds=60)
        worker_2 = IdempotencyEngine(ttl_seconds=60)
        try:
            # 1. Worker 1 acquires and then releases on failure
            assert worker_1.try_acquire(key) is True
            worker_1.release(key)

            # 2. Worker 2 can now acquire the released key
            assert worker_2.try_acquire(key) is True

            # 3. Worker 2 marks it processed (COMMITTED)
            worker_2.mark_processed(key)

            # 4. Worker 1 cannot acquire it now
            assert worker_1.try_acquire(key) is False
            assert worker_1.is_duplicate(key) is True
        finally:
            worker_1.clear()
            worker_2.clear()

    def test_clear_only_evicts_own_registered_keys_and_preserves_other_workers(self) -> None:
        """Worker 1 clear() must NOT erase Worker 2's valid idempotency reservations or unrelated keys."""
        from app.application.services.idempotency import IdempotencyService

        key_w1 = f"test-clear-w1-{uuid.uuid4().hex}"
        key_w2 = f"test-clear-w2-{uuid.uuid4().hex}"
        sentinel_key = "unrelated_system:sentinel_marker_survives"

        worker_1 = IdempotencyEngine(ttl_seconds=60)
        worker_2 = IdempotencyEngine(ttl_seconds=60)
        idem_svc = IdempotencyService.get()

        idem_svc._redis_client.set(sentinel_key, "sentinel_value_active")

        try:
            assert worker_1.try_acquire(key_w1) is True
            assert worker_2.try_acquire(key_w2) is True

            # Worker 1 clears its state
            worker_1.clear()

            # Worker 1's key is evicted; Worker 2's reservation remains strictly preserved
            assert worker_2.is_duplicate(key_w2) is True
            assert worker_1.try_acquire(key_w2) is False

            # Sentinel key in unrelated namespace survived unharmed
            sentinel_val = idem_svc._redis_client.get(sentinel_key)
            assert sentinel_val in (b"sentinel_value_active", "sentinel_value_active")
        finally:
            worker_1.clear()
            worker_2.clear()
            idem_svc._redis_client.delete(sentinel_key)

    def test_unrelated_redis_sentinel_survives_all_cleanup(self) -> None:
        """An unrelated production Redis sentinel key strictly survives all engine operations and clear()."""
        from app.application.services.idempotency import IdempotencyService

        sentinel_key = f"unrelated_prod_sentinel:key_{uuid.uuid4().hex}"
        idem_svc = IdempotencyService.get()
        idem_svc._redis_client.set(sentinel_key, "sentinel_marker_value_unaltered")

        engine = IdempotencyEngine(ttl_seconds=60)
        key = f"test-sentinel-survival-{uuid.uuid4().hex}"

        try:
            assert engine.try_acquire(key) is True
            assert engine.is_duplicate(key) is True
            engine.mark_processed(key)
            engine.clear()

            # Sentinel must remain completely untouched
            sentinel_val = idem_svc._redis_client.get(sentinel_key)
            assert sentinel_val in (b"sentinel_marker_value_unaltered", "sentinel_marker_value_unaltered")
        finally:
            engine.clear()
            idem_svc._redis_client.delete(sentinel_key)

    def test_production_mode_fails_closed_without_redis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Under production mode, when Redis authority is unavailable, try_acquire fails closed."""
        from app.application.services.idempotency import IdempotencyService

        monkeypatch.setenv("APP_ENV", "production")
        idem_svc = IdempotencyService.get()
        orig_client = idem_svc._redis_client
        try:
            idem_svc._redis_client = None  # Simulate Redis authority outage in production
            engine = IdempotencyEngine(ttl_seconds=60)
            key = f"test-prod-failclosed-{uuid.uuid4().hex}"

            # Must fail closed — returning False rather than silently executing process-locally
            assert engine.try_acquire(key) is False
            assert engine.is_duplicate(key) is True
        finally:
            idem_svc._redis_client = orig_client

    @pytest.mark.asyncio
    async def test_successful_broker_publish_transitions_reservation_to_committed_state(self) -> None:
        """Successful broker publish transitions the owned reservation into a committed state with full TTL."""
        from app.application.services.idempotency import IdempotencyService

        prefix = f"test_pub_commit_{uuid.uuid4().hex[:8]}"
        topic = f"cfi.test.commit.{uuid.uuid4().hex[:8]}"
        connector = KafkaStreamingConnector(idempotency_prefix=prefix, idempotency_ttl=3600)
        idem_svc = IdempotencyService.get()
        ev_id = f"pub-commit-{uuid.uuid4().hex[:8]}"
        ev = CloudEvent(
            id=ev_id,
            source="urn:cfi:bank:ALPHA",
            type=EVENT_TYPE_TRANSACTION,
            ce_tenant_id="ALPHA",
            data={"amount": "250.00", "currency": "EUR"},
        )

        try:
            receipt = await connector.publish(ev, topic=topic)
            assert receipt.status == "COMMITTED"
            assert receipt.idempotency_committed is True
            assert receipt.partition == 0
            assert receipt.offset == 0

            # Verify in Redis: key must be committed envelope, not in-progress reservation
            dedup_key = f"{prefix}:ALPHA:{ev_id}"
            redis_key = idem_svc._build_redis_key(dedup_key)
            raw = idem_svc._redis_client.get(redis_key)
            assert raw is not None
            val_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            assert not val_str.startswith("IN_PROGRESS:")
            parsed = json.loads(val_str)
            assert parsed.get("__idempotency_envelope__") is True
            assert parsed.get("response") == "COMMITTED"

            # Check that TTL reflects the committed window (~3600s), not the temporary 30s timeout
            ttl = idem_svc._redis_client.ttl(redis_key)
            assert ttl > 3000
        finally:
            connector._idempotency.clear()

    @pytest.mark.asyncio
    async def test_ordinary_duplicate_after_reservation_ttl_rejected_in_dedup_window(self) -> None:
        """An ordinary duplicate after original reservation TTL cannot be published again within deduplication window."""
        prefix = f"test_dedup_win_{uuid.uuid4().hex[:8]}"
        topic = f"cfi.test.dedup.{uuid.uuid4().hex[:8]}"
        connector = KafkaStreamingConnector(idempotency_prefix=prefix, idempotency_ttl=3600)
        ev_id = f"dup-window-{uuid.uuid4().hex[:8]}"
        ev = CloudEvent(
            id=ev_id,
            source="urn:cfi:bank:BETA",
            type=EVENT_TYPE_TRANSACTION,
            ce_tenant_id="BETA",
            data={"amount": "500.00"},
        )

        try:
            # 1. Initial publish succeeds and commits
            receipt1 = await connector.publish(ev, topic=topic)
            assert receipt1.status == "COMMITTED"
            assert receipt1.offset == 0

            # 2. Subsequent publish within dedup window is rejected even after reservation phase
            receipt2 = await connector.publish(ev, topic=topic)
            assert receipt2.status == "DUPLICATE_IGNORED"
            assert receipt2.idempotent_duplicate is True

            # 3. Verify broker only received the single genuine message
            broker_msgs = await connector._broker.fetch_messages(
                topic,
                group_id="verify-dedup-group",
                max_messages=10,
            )
            assert len(broker_msgs) == 1
        finally:
            connector._idempotency.clear()

    @pytest.mark.asyncio
    async def test_broker_failure_before_ack_permits_retry(self) -> None:
        """Broker failure before acknowledgment releases reservation and permits a legitimate retry."""
        from app.application.services.idempotency import IdempotencyService

        prefix = f"test_retry_fail_{uuid.uuid4().hex[:8]}"
        topic = f"cfi.test.retry.{uuid.uuid4().hex[:8]}"
        connector = KafkaStreamingConnector(idempotency_prefix=prefix, idempotency_ttl=3600)
        idem_svc = IdempotencyService.get()
        ev_id = f"retry-fail-{uuid.uuid4().hex[:8]}"
        ev = CloudEvent(
            id=ev_id,
            source="urn:cfi:bank:GAMMA",
            type=EVENT_TYPE_TRANSACTION,
            ce_tenant_id="GAMMA",
            data={"amount": "1200.00"},
        )

        class FailingBroker:
            def __init__(self, real_broker: Any) -> None:
                self._real = real_broker
                self.fail_next = True

            async def publish(self, topic: str, message: bytes, partition: int = 0) -> tuple[int, int]:
                if self.fail_next:
                    self.fail_next = False
                    raise RuntimeError("Simulated broker transport failure before acknowledgment")
                return await self._real.publish(topic, message, partition)

        orig_broker = connector._broker
        connector._broker = FailingBroker(orig_broker)  # type: ignore[assignment]
        dedup_key = f"{prefix}:GAMMA:{ev_id}"
        redis_key = idem_svc._build_redis_key(dedup_key)

        try:
            # 1. First publish fails at broker level
            with pytest.raises(RuntimeError, match="Simulated broker transport failure"):
                await connector.publish(ev, topic=topic)

            # 2. Reservation must be released from Redis, allowing retry
            raw_after_failure = idem_svc._redis_client.get(redis_key)
            assert raw_after_failure is None, "Reservation was not released after broker failure!"

            # 3. Legitimate retry must succeed now that broker is operational
            retry_receipt = await connector.publish(ev, topic=topic)
            assert retry_receipt.status == "COMMITTED"
            assert retry_receipt.idempotency_committed is True

            # 4. Redis is now committed
            raw_after_retry = idem_svc._redis_client.get(redis_key)
            assert raw_after_retry is not None
            assert b"COMMITTED" in raw_after_retry
        finally:
            connector._broker = orig_broker
            connector._idempotency.clear()

    @pytest.mark.asyncio
    async def test_redis_completion_outage_after_broker_ack_reports_uncertainty_truthfully(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Redis completion outage after broker ACK cannot be silently reported as a successful distributed commit."""
        from app.application.services.idempotency import IdempotencyService

        prefix = f"test_ack_degraded_{uuid.uuid4().hex[:8]}"
        topic = f"cfi.test.degraded.{uuid.uuid4().hex[:8]}"
        connector = KafkaStreamingConnector(idempotency_prefix=prefix, idempotency_ttl=3600)
        monkeypatch.setenv("APP_ENV", "production")
        idem_svc = IdempotencyService.get()
        ev_id = f"degraded-ack-{uuid.uuid4().hex[:8]}"
        ev = CloudEvent(
            id=ev_id,
            source="urn:cfi:bank:DELTA",
            type=EVENT_TYPE_TRANSACTION,
            ce_tenant_id="DELTA",
            data={"amount": "3000.00"},
        )

        orig_complete = idem_svc.complete

        def failing_complete(*args: Any, **kwargs: Any) -> bool:
            return False

        monkeypatch.setattr(idem_svc, "complete", failing_complete)

        try:
            receipt = await connector.publish(ev, topic=topic)
            # Must NOT report as clean COMMITTED
            assert receipt.status != "COMMITTED"
            assert receipt.status == "COMMITTED_IDEMPOTENCY_UNCERTAIN"
            assert receipt.idempotency_committed is False

            # Must NOT pretend broker publication was rolled back (broker acknowledged)
            assert receipt.status != "FAILED"
            assert receipt.partition == 0
            assert receipt.offset == 0
        finally:
            monkeypatch.setattr(idem_svc, "complete", orig_complete)
            connector._idempotency.clear()

    def test_stale_worker_cannot_complete_another_worker_reservation(self) -> None:
        """Stale worker whose reservation expired cannot complete another worker's newer reservation."""
        from app.application.services.idempotency import IdempotencyService

        key = f"test-stale-complete-{uuid.uuid4().hex}"
        worker_1 = IdempotencyEngine(ttl_seconds=60, engine_id="worker_stale_1")
        worker_2 = IdempotencyEngine(ttl_seconds=60, engine_id="worker_stale_2")
        idem_svc = IdempotencyService.get()
        redis_key = idem_svc._build_redis_key(f"kafka:{key}")

        try:
            # 1. Worker 1 acquires reservation
            assert worker_1.try_acquire(key) is True
            owner_1 = worker_1._owned_reservations[key]

            # 2. Simulate Worker 1 expiration: Redis key is deleted
            idem_svc._redis_client.delete(redis_key)

            # 3. Worker 2 acquires the freed reservation
            assert worker_2.try_acquire(key) is True
            owner_2 = worker_2._owned_reservations[key]
            assert owner_1 != owner_2

            # 4. Stale Worker 1 attempts completion: must be rejected atomically by Lua
            completed_w1 = worker_1.mark_processed(key)
            assert completed_w1 is False

            # 5. Active Worker 2 reservation in Redis is intact and untouched
            val = idem_svc._redis_client.get(redis_key)
            val_str = val.decode("utf-8") if isinstance(val, bytes) else str(val)
            assert f"IN_PROGRESS:{owner_2}" in val_str

            # 6. Worker 2 completes successfully
            completed_w2 = worker_2.mark_processed(key)
            assert completed_w2 is True

            # 7. Stale Worker 1 attempts completion again: still rejected
            assert worker_1.mark_processed(key) is False
        finally:
            worker_1.clear()
            worker_2.clear()

    def test_setnx_failure_followed_by_key_disappearance_retries_safely_without_phantom_ownership(self) -> None:
        """SET NX failure followed by key disappearance cannot produce ownership without a successful new acquisition."""
        from app.application.services.idempotency import IdempotencyService

        idem_svc = IdempotencyService.get()
        key = f"test-disappear-{uuid.uuid4().hex}"

        call_count = 0
        orig_set = idem_svc._redis_client.set
        orig_get = idem_svc._redis_client.get

        def mock_set_flaky(k: str, v: Any, **kwargs: Any) -> bool:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return False  # First SET NX fails
            return orig_set(k, v, **kwargs)  # Second SET NX succeeds

        def mock_get_flaky(k: str) -> Any:
            if call_count == 1:
                return None  # Key disappeared before GET
            return orig_get(k)

        idem_svc._redis_client.set = mock_set_flaky
        idem_svc._redis_client.get = mock_get_flaky

        try:
            status, _ = idem_svc.acquire(key, owner_id="test_worker_flaky", require_redis=True)
            assert status == "ACQUIRED"
            assert call_count == 2, f"Expected 2 attempts for safe retry, got {call_count}"
        finally:
            idem_svc._redis_client.set = orig_set
            idem_svc._redis_client.get = orig_get
            idem_svc.release(key, owner_id="test_worker_flaky")

        def mock_set_always_fail(k: str, v: Any, **kwargs: Any) -> bool:
            return False

        def mock_get_always_none(k: str) -> Any:
            return None

        idem_svc._redis_client.set = mock_set_always_fail
        idem_svc._redis_client.get = mock_get_always_none

        try:
            key_fail = f"test-always-fail-{uuid.uuid4().hex}"
            status_fail, _ = idem_svc.acquire(key_fail, owner_id="test_worker_fail", require_redis=True)
            assert status_fail != "ACQUIRED", "Phantom ACQUIRED returned without successful reservation!"
            assert status_fail == "UNAVAILABLE"
        finally:
            idem_svc._redis_client.set = orig_set
            idem_svc._redis_client.get = orig_get

