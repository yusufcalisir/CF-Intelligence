"""Unit tests for RedisBankConnector and MQSkeletonBankConnector."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.domain.value_objects import ModelWeights
from app.infrastructure.connectors.mq_skeleton_connector import MQSkeletonBankConnector
from app.infrastructure.connectors.redis_connector import RedisBankConnector


def test_mq_skeleton_connector_lifecycle():
    """Verify MQSkeletonBankConnector initialize, train, evaluate, and metrics."""
    conn = MQSkeletonBankConnector(broker_uri="amqp://test:test@localhost:5672//")
    assert conn.broker_uri == "amqp://test:test@localhost:5672//"

    # Initialize
    init_res = conn.initialize("bank_a", 1000)
    assert init_res["status"] == "initialized"

    # Train
    weights = ModelWeights(layer_shapes=[(10, 64)], flat_weights=[0.1] * 640)
    train_res = conn.train("bank_a", weights, 0.001, 32, 2, False, 1.0, 1e-5, 1.0, "corr_123")
    assert "weights" in train_res
    assert train_res["loss"] == 0.15

    # Evaluate
    eval_res = conn.evaluate("bank_a", weights, correlation_id="eval_123")
    assert "loss" in eval_res
    assert eval_res["accuracy"] == 0.92


def test_redis_bank_connector_initialization_and_pubsub():
    """Verify RedisBankConnector publishes initialize commands and parses pub/sub replies."""
    with patch("redis.Redis.from_url") as mock_redis_from_url:
        mock_redis = MagicMock()
        mock_pubsub = MagicMock()

        # Simulate pub/sub response
        cid = "init_conn_bank_a_123"
        mock_pubsub.get_message.side_effect = [
            {
                "channel": "bank_client_bank_a_init_response",
                "data": json.dumps({
                    "status": "success",
                    "bank_id": "bank_a",
                    "correlation_id": cid,
                }),
            }
        ]
        mock_redis.pubsub.return_value = mock_pubsub
        mock_redis_from_url.return_value = mock_redis

        with patch("time.time", return_value=123):
            conn = RedisBankConnector("redis://localhost:6379/0")
            res = conn.initialize("bank_a", 500)

            assert res["status"] == "success"
            assert res["bank_id"] == "bank_a"
            mock_redis.publish.assert_called_once()
