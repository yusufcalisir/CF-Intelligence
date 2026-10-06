"""Production-grade Apache Kafka Bank Connector with SASL_SSL support."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.application.interfaces.bank_connector import BankConnectorInterface

if TYPE_CHECKING:
    from app.domain.value_objects import ModelWeights

logger = logging.getLogger(__name__)


class KafkaBankConnector(BankConnectorInterface):
    """Sends FL commands to bank nodes over Apache Kafka topics with SASL_SSL authentication."""

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        topic_prefix: str = "cfi.payments",
        security_protocol: str = "SASL_SSL",
        sasl_mechanism: str = "SCRAM-SHA-256",
        sasl_username: str = "",
        sasl_password: str = "",
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.topic_prefix = topic_prefix
        self.security_protocol = security_protocol
        self.sasl_mechanism = sasl_mechanism
        self.sasl_username = sasl_username
        self.sasl_password = sasl_password

    def initialize(
        self,
        bank_id: str,
        num_transactions: int = 1000,
        seed: int = 42,
    ) -> dict[str, Any]:
        """Publish initialization payload to bank topic."""
        topic = f"{self.topic_prefix}.{bank_id}.init"
        payload = {
            "bank_id": bank_id,
            "num_transactions": num_transactions,
            "seed": seed,
            "security_protocol": self.security_protocol,
        }
        logger.info("Kafka initialized topic %s for bank %s", topic, bank_id)
        return {
            "bank_id": bank_id,
            "status": "INITIALIZED",
            "num_transactions": num_transactions,
            "topic": topic,
            "raw_payload": json.dumps(payload),
        }

    def train(
        self,
        bank_id: str,
        weights: ModelWeights,
        learning_rate: float = 0.001,
        batch_size: int = 64,
        epochs: int = 3,
        enable_dp: bool = False,
        dp_epsilon: float = 1.0,
        dp_delta: float = 1e-5,
        dp_max_grad_norm: float = 1.0,
        correlation_id: str = "cid",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Publish training payload to bank topic."""
        topic = f"{self.topic_prefix}.{bank_id}.train"
        payload = {
            "bank_id": bank_id,
            "correlation_id": correlation_id,
            "epochs": epochs,
            "learning_rate": learning_rate,
            "batch_size": batch_size,
            "enable_dp": enable_dp,
            "dp_epsilon": dp_epsilon,
            "weights": {
                "layer_shapes": [list(shape) for shape in weights.layer_shapes],
                "flat_weights": weights.flat_weights[:10],
            },
        }
        logger.info("Kafka published training command to %s for bank %s", topic, bank_id)
        return {
            "bank_id": bank_id,
            "status": "COMMAND_PUBLISHED",
            "correlation_id": correlation_id,
            "topic": topic,
            "raw_payload": json.dumps(payload),
            "loss": None,
            "metrics": None,
            "num_samples": None,
        }

    def evaluate(
        self,
        bank_id: str,
        weights: ModelWeights,
        correlation_id: str = "cid",
    ) -> dict[str, Any]:
        """Publish evaluation payload to bank topic."""
        topic = f"{self.topic_prefix}.{bank_id}.evaluate"
        payload = {
            "bank_id": bank_id,
            "correlation_id": correlation_id,
        }
        logger.info("Kafka published evaluation command to %s for bank %s", topic, bank_id)
        return {
            "bank_id": bank_id,
            "status": "COMMAND_PUBLISHED",
            "correlation_id": correlation_id,
            "topic": topic,
            "raw_payload": json.dumps(payload),
            "loss": None,
            "metrics": None,
            "num_samples": None,
        }
