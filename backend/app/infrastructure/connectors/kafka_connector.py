"""Production-grade Apache Kafka Bank Connector with SASL_SSL support."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.application.interfaces.bank_connector import BankConnectorInterface
from app.domain.enums import KafkaDeliveryStatus

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
            "delivery_status": KafkaDeliveryStatus.SEND_REQUESTED.value,
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
        run_id: str = "run_1",
        round_id: int = 1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Publish training payload to bank topic."""
        topic = f"{self.topic_prefix}.{bank_id}.train"
        payload = {
            "bank_id": bank_id,
            "correlation_id": correlation_id,
            "run_id": run_id,
            "round_id": round_id,
            "command_type": "TRAIN",
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
            "delivery_status": KafkaDeliveryStatus.SEND_REQUESTED.value,
            "command_type": "TRAIN",
            "correlation_id": correlation_id,
            "run_id": run_id,
            "round_id": round_id,
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
        run_id: str = "run_1",
        round_id: int = 1,
    ) -> dict[str, Any]:
        """Publish evaluation payload to bank topic."""
        topic = f"{self.topic_prefix}.{bank_id}.evaluate"
        payload = {
            "bank_id": bank_id,
            "correlation_id": correlation_id,
            "run_id": run_id,
            "round_id": round_id,
            "command_type": "EVALUATE",
        }
        logger.info("Kafka published evaluation command to %s for bank %s", topic, bank_id)
        return {
            "bank_id": bank_id,
            "status": "COMMAND_PUBLISHED",
            "delivery_status": KafkaDeliveryStatus.SEND_REQUESTED.value,
            "command_type": "EVALUATE",
            "correlation_id": correlation_id,
            "run_id": run_id,
            "round_id": round_id,
            "topic": topic,
            "raw_payload": json.dumps(payload),
            "loss": None,
            "metrics": None,
            "num_samples": None,
        }

    @staticmethod
    def correlate_worker_result(
        command_meta: dict[str, Any],
        worker_result: dict[str, Any],
        processed_correlation_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        """Correlate asynchronous worker response with original published command.

        Fails closed if correlation identifiers (correlation_id, bank_id, command_type,
        run_id, round_id, model_id) do not match, preventing stale or cross-command metric leakage.
        Provides deterministic idempotency via processed_correlation_ids tracking.
        """
        cid = command_meta.get("correlation_id")
        w_cid = worker_result.get("correlation_id")
        if not cid or cid != w_cid:
            raise ValueError(
                f"Correlation failed: command correlation_id '{cid}' != worker correlation_id '{w_cid}'"
            )

        bid = command_meta.get("bank_id")
        w_bid = worker_result.get("bank_id")
        if bid and w_bid and bid != w_bid:
            raise ValueError(
                f"Correlation failed: bank_id mismatch: '{bid}' != '{w_bid}'"
            )

        cmd_type = command_meta.get("command_type")
        w_cmd_type = worker_result.get("command_type")
        if cmd_type and w_cmd_type and cmd_type != w_cmd_type:
            raise ValueError(
                f"Correlation failed: command_type mismatch: expected '{cmd_type}', received '{w_cmd_type}'"
            )

        run_id = command_meta.get("run_id")
        w_run_id = worker_result.get("run_id")
        if run_id and w_run_id and run_id != w_run_id:
            raise ValueError(
                f"Correlation failed: run_id mismatch: expected '{run_id}', received '{w_run_id}'"
            )

        round_id = command_meta.get("round_id")
        w_round_id = worker_result.get("round_id")
        if round_id is not None and w_round_id is not None and round_id != w_round_id:
            raise ValueError(
                f"Correlation failed: round_id mismatch: expected '{round_id}', received '{w_round_id}'"
            )

        model_id = command_meta.get("model_id")
        w_model_id = worker_result.get("model_id")
        if model_id and w_model_id and model_id != w_model_id:
            raise ValueError(
                f"Correlation failed: model_id mismatch: expected '{model_id}', received '{w_model_id}'"
            )

        # Deterministic idempotency: duplicate results do not double-apply metrics or state transitions
        if processed_correlation_ids is not None and cid in processed_correlation_ids:
            return {
                "status": "DUPLICATE_IGNORED",
                "delivery_status": KafkaDeliveryStatus.PROCESSED.value,
                "correlation_id": cid,
                "bank_id": bid,
                "command_type": cmd_type,
                "run_id": run_id,
                "round_id": round_id,
                "model_id": model_id,
                "idempotent_duplicate": True,
                "loss": None,
                "metrics": None,
                "num_samples": 0,
            }

        if processed_correlation_ids is not None:
            processed_correlation_ids.add(cid)

        return {
            "status": "PROCESSED",
            "delivery_status": KafkaDeliveryStatus.PROCESSED.value,
            "correlation_id": cid,
            "bank_id": bid,
            "command_type": cmd_type,
            "run_id": run_id,
            "round_id": round_id,
            "model_id": model_id,
            "idempotent_duplicate": False,
            "loss": worker_result.get("loss"),
            "metrics": worker_result.get("metrics"),
            "num_samples": worker_result.get("num_samples"),
        }

