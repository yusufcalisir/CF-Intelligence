"""Unit tests for Message Queue Connectors (RabbitMQ & Apache Kafka) (Section 10.3)."""

from __future__ import annotations

import json

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
    assert init_res["topic"] == "cfi.payments.bank_a.init"

    raw_init = json.loads(init_res["raw_payload"])
    assert raw_init["bank_id"] == "bank_a"

    weights = ModelWeights(layer_shapes=[(2, 2)], flat_weights=[0.1, 0.2, 0.3, 0.4])
    train_res = connector.train(bank_id="bank_a", weights=weights, epochs=2)
    assert train_res["status"] == "COMMAND_PUBLISHED"
    assert train_res["topic"] == "cfi.payments.bank_a.train"
    assert train_res["metrics"] is None
    assert train_res["loss"] is None

    eval_res = connector.evaluate(bank_id="bank_a", weights=weights)
    assert eval_res["status"] == "COMMAND_PUBLISHED"
    assert eval_res["topic"] == "cfi.payments.bank_a.evaluate"
    assert eval_res["metrics"] is None
    assert eval_res["loss"] is None
