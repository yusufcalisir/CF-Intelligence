"""Unit tests for Message Queue Connectors (RabbitMQ & Apache Kafka) (Section 10.3)."""

from __future__ import annotations

import json

import pytest

from app.domain.value_objects import ModelWeights
from app.infrastructure.connectors.kafka_connector import KafkaBankConnector
from app.infrastructure.connectors.rabbitmq_connector import RabbitMQBankConnector


def test_rabbitmq_connector_amqp_ssl_configuration() -> None:
    """Verifies RabbitMQBankConnector AMQP SSL/TLS connection parameter setup."""
    connector = RabbitMQBankConnector(
        host="rabbitmq.consortium.org",
        port=5671,
        username="bank_user",
        password="secure_password",
        use_ssl=True,
    )

    assert connector.host == "rabbitmq.consortium.org"
    assert connector.port == 5671
    assert connector.use_ssl is True


def test_kafka_connector_sasl_ssl_configuration() -> None:
    """Verifies KafkaBankConnector SASL_SSL configuration parameters."""
    connector = KafkaBankConnector(
        bootstrap_servers="kafka.consortium.org:9093",
        topic_prefix="cfi.payments",
        security_protocol="SASL_SSL",
        sasl_mechanism="SCRAM-SHA-256",
        sasl_username="bank_alpha",
        sasl_password="secret_password",
    )

    assert connector.bootstrap_servers == "kafka.consortium.org:9093"
    assert connector.security_protocol == "SASL_SSL"
    assert connector.sasl_mechanism == "SCRAM-SHA-256"


def test_message_queue_stream_batch_parsing() -> None:
    """Verifies Kafka Bank Connector stream payload serialization and execution interface."""
    connector = KafkaBankConnector(bootstrap_servers="localhost:9092")

    init_res = connector.initialize(bank_id="bank_a", num_transactions=500)
    assert init_res["status"] == "INITIALIZED"
    assert init_res["delivery_status"] == "SEND_REQUESTED"
    assert init_res["topic"] == "cfi.payments.bank_a.init"

    raw_init = json.loads(init_res["raw_payload"])
    assert raw_init["bank_id"] == "bank_a"

    weights = ModelWeights(layer_shapes=[(2, 2)], flat_weights=[0.1, 0.2, 0.3, 0.4])
    train_res = connector.train(bank_id="bank_a", weights=weights, epochs=2)
    assert train_res["status"] == "COMMAND_PUBLISHED"
    assert train_res["delivery_status"] == "SEND_REQUESTED"
    assert train_res["topic"] == "cfi.payments.bank_a.train"
    assert train_res["metrics"] is None
    assert train_res["loss"] is None

    eval_res = connector.evaluate(bank_id="bank_a", weights=weights)
    assert eval_res["status"] == "COMMAND_PUBLISHED"
    assert eval_res["delivery_status"] == "SEND_REQUESTED"
    assert eval_res["topic"] == "cfi.payments.bank_a.evaluate"
    assert eval_res["metrics"] is None
    assert eval_res["loss"] is None


def test_kafka_connector_with_producer_dispatch_and_full_weights() -> None:
    """Verifies that an active producer receives dispatches and full weights are serialized without truncation."""
    dispatched_records: list[tuple[str, bytes]] = []

    class MockKafkaProducer:
        def send(self, topic: str, value: bytes) -> None:
            dispatched_records.append((topic, value))

    producer = MockKafkaProducer()
    connector = KafkaBankConnector(bootstrap_servers="kafka.internal:9092", producer=producer)

    large_weights = ModelWeights(
        layer_shapes=[(5, 5)],
        flat_weights=[float(i) for i in range(25)],
    )

    train_res = connector.train(bank_id="bank_b", weights=large_weights, epochs=3)
    assert train_res["delivery_status"] == "BROKER_ACKNOWLEDGED"
    assert len(dispatched_records) == 1
    topic, raw_bytes = dispatched_records[0]
    assert topic == "cfi.payments.bank_b.train"
    payload = json.loads(raw_bytes.decode("utf-8"))
    assert len(payload["weights"]["flat_weights"]) == 25
    assert payload["weights"]["flat_weights"] == [float(i) for i in range(25)]

    eval_res = connector.evaluate(bank_id="bank_b", weights=large_weights)
    assert eval_res["delivery_status"] == "BROKER_ACKNOWLEDGED"
    assert len(dispatched_records) == 2
    eval_topic, eval_bytes = dispatched_records[1]
    assert eval_topic == "cfi.payments.bank_b.evaluate"
    eval_payload = json.loads(eval_bytes.decode("utf-8"))
    assert "weights" in eval_payload
    assert eval_payload["weights"]["flat_weights"] == [float(i) for i in range(25)]


def test_kafka_connector_production_fail_closed_without_producer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that KafkaBankConnector strictly fails closed in production when no producer is connected."""
    import pytest

    monkeypatch.setenv("APP_ENV", "production")
    connector = KafkaBankConnector(bootstrap_servers="kafka.prod.bank:9092", producer=None)

    with pytest.raises(RuntimeError, match="Production KafkaBankConnector requires an active Kafka producer connection"):
        connector.initialize(bank_id="bank_prod", num_transactions=100)

    weights = ModelWeights(layer_shapes=[(1, 1)], flat_weights=[0.5])
    with pytest.raises(RuntimeError, match="Production KafkaBankConnector requires an active Kafka producer connection"):
        connector.train(bank_id="bank_prod", weights=weights, epochs=1)

    with pytest.raises(RuntimeError, match="Production KafkaBankConnector requires an active Kafka producer connection"):
        connector.evaluate(bank_id="bank_prod", weights=weights)

