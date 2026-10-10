"""Unit and integration tests for the Distributed HTTP Federated Learning Engine."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, cast
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.application.services.data_generator import DataGenerator
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.metrics_service import MetricsService
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import PrivacyService
from app.application.services.simulation_service import SimulationService
from app.config import get_settings
from app.domain.value_objects import SimulationConfig
from app.main import app

client = TestClient(app)


# ── Signature Helpers ────────────────────────────────────────────────────────


def _sign_payload(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    """Generate valid HMAC-SHA256 signature headers and canonical body bytes."""
    settings = get_settings()
    secret = settings.payload_signing_secret
    timestamp = str(time.time())
    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sign_data = timestamp.encode("utf-8") + b"." + body_bytes
    signature = hmac.new(
        secret.encode("utf-8"),
        sign_data,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-Payload-Signature": signature,
        "X-Payload-Timestamp": timestamp,
        "Content-Type": "application/json",
    }
    return body_bytes, headers


def _signed_post(url: str, payload: dict[str, Any]) -> Any:
    """Post a signed JSON payload to the TestClient using raw bytes."""
    body_bytes, headers = _sign_payload(payload)
    return client.post(url, content=body_bytes, headers=headers)


# ── Lifecycle Tests ──────────────────────────────────────────────────────────


def test_bank_client_endpoints_lifecycle() -> None:
    """Verify that initialize, train, and evaluate endpoints function in sequence."""
    # 1. Initialize dataset
    init_payload = {
        "bank_id": "bank_a",
        "num_transactions": 500,
        "seed": 42,
    }
    init_resp = _signed_post("/api/v1/bank-client/initialize", init_payload)
    assert init_resp.status_code == 200
    init_data = init_resp.json()
    assert init_data["status"] == "initialized"
    assert init_data["bank_id"] == "bank_a"
    assert init_data["train_samples"] > 0

    # Retrieve initial weights from monolith model service to match input schema
    settings = get_settings()
    model_service = ModelService(settings)
    model = model_service.create_model()
    weights = model_service.get_parameters(model)

    weights_payload = {
        "layer_shapes": [list(shape) for shape in weights.layer_shapes],
        "flat_weights": weights.flat_weights,
    }

    # 2. Train local model on initialized dataset
    train_payload = {
        "weights": weights_payload,
        "learning_rate": 0.01,
        "batch_size": 32,
        "epochs": 1,
        "enable_dp": False,
        "dp_epsilon": 1.0,
        "dp_delta": 1e-5,
        "dp_max_grad_norm": 1.0,
    }
    train_resp = _signed_post("/api/v1/bank-client/train", train_payload)
    assert train_resp.status_code == 200
    train_data = train_resp.json()
    assert "loss" in train_data
    assert train_data["num_samples"] == init_data["train_samples"]
    assert len(train_data["weights"]["flat_weights"]) == len(weights.flat_weights)

    # 3. Evaluate weights
    eval_payload = {
        "weights": weights_payload,
    }
    eval_resp = _signed_post("/api/v1/bank-client/evaluate", eval_payload)
    assert eval_resp.status_code == 200
    eval_data = eval_resp.json()
    assert "loss" in eval_data
    assert "accuracy" in eval_data
    assert "f1_score" in eval_data
    assert "roc_fpr" in eval_data
    assert eval_data["num_samples"] == init_data["test_samples"]


# ── Signature Rejection Tests ────────────────────────────────────────────────


def test_request_without_signature_is_rejected() -> None:
    """Requests missing signature headers must be rejected with 401."""
    payload = {"bank_id": "bank_a", "num_transactions": 500, "seed": 42}
    resp = client.post("/api/v1/bank-client/initialize", json=payload)
    assert resp.status_code == 401
    assert "Missing payload signature" in resp.json()["detail"]


def test_request_with_invalid_signature_is_rejected() -> None:
    """Requests with a tampered signature must be rejected with 401."""
    payload = {"bank_id": "bank_a", "num_transactions": 500, "seed": 42}
    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    headers = {
        "X-Payload-Signature": "deadbeef" * 8,  # invalid hex
        "X-Payload-Timestamp": str(time.time()),
        "Content-Type": "application/json",
    }
    resp = client.post("/api/v1/bank-client/initialize", content=body_bytes, headers=headers)
    assert resp.status_code == 401
    assert "Invalid payload signature" in resp.json()["detail"]


def test_request_with_expired_timestamp_is_rejected() -> None:
    """Requests with a timestamp older than 300s must be rejected with 401."""
    payload = {"bank_id": "bank_a", "num_transactions": 500, "seed": 42}
    settings = get_settings()
    secret = settings.payload_signing_secret
    old_timestamp = str(time.time() - 600)  # 10 minutes ago
    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sign_data = old_timestamp.encode("utf-8") + b"." + body_bytes
    signature = hmac.new(secret.encode("utf-8"), sign_data, hashlib.sha256).hexdigest()
    headers = {
        "X-Payload-Signature": signature,
        "X-Payload-Timestamp": old_timestamp,
        "Content-Type": "application/json",
    }
    resp = client.post("/api/v1/bank-client/initialize", content=body_bytes, headers=headers)
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"]


def test_unconfigured_signing_secret_on_node_fails_closed(monkeypatch: Any) -> None:
    """If the bank client node has an empty signing secret, requests fail-closed with 500."""
    from app.presentation.routers import bank_client
    monkeypatch.setattr(bank_client._settings, "payload_signing_secret", "")

    payload = {"bank_id": "bank_a", "num_transactions": 500, "seed": 42}
    resp = client.post("/api/v1/bank-client/initialize", json=payload)
    assert resp.status_code == 500
    assert "Payload signing secret is not configured" in resp.json()["detail"]


# ── Distributed Simulation Tests ─────────────────────────────────────────────


def mock_get_client(*args: Any, **kwargs: Any) -> Any:
    """Mock get_client inside RESTBankConnector to redirect to FastAPI TestClient."""
    import httpx

    class MockResponse:
        def __init__(self, r: Any) -> None:
            self._r = r
            self.status_code = r.status_code

        def json(self) -> Any:
            return self._r.json()

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "Error", request=cast("Any", None), response=cast("Any", self)
                )

    class MockClient:
        def __enter__(self) -> MockClient:
            return self

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

        def post(
            self,
            url: str,
            json: Any = None,
            content: Any = None,
            headers: Any = None,
            timeout: Any = None,
            **kwargs: Any,
        ) -> MockResponse:
            # Resolve URL path mapping
            path_parts = url.split("api/v1/")
            path = (
                "/api/v1/" + path_parts[1]
                if len(path_parts) > 1
                else "/api/v1/bank-client/initialize"
            )
            # Forward signature headers and either json or content
            if content is not None:
                resp = client.post(path, content=content, headers=headers)
            else:
                resp = client.post(path, json=json, headers=headers)
            return MockResponse(resp)

    return MockClient()


@patch(
    "app.infrastructure.connectors.rest_connector.RESTBankConnector._get_client",
    side_effect=mock_get_client,
)
def test_distributed_simulation_orchestration(mock_get_client_patch: Any) -> None:
    """Verify that a full distributed federated training run aggregates weights correctly."""
    settings = get_settings()
    model_service = ModelService(settings)
    privacy_service = PrivacyService()
    fl_engine = FederatedLearningEngine(settings, model_service, privacy_service)
    data_generator = DataGenerator()
    metrics_service = MetricsService()

    simulation_service = SimulationService(
        settings=settings,
        simulation_repo=None,
        bank_repo=None,
        metrics_repo=None,
        data_generator=data_generator,
        fl_engine=fl_engine,
        metrics_service=metrics_service,
        model_service=model_service,
    )

    # Setup SimulationConfig with fl_engine_type = "distributed"
    config = SimulationConfig(
        num_rounds=2,
        local_epochs=1,
        learning_rate=0.01,
        batch_size=32,
        min_clients_per_round=2,
        enable_latency_simulation=False,
        enable_dropout_simulation=False,
        enable_reconnect_simulation=True,
        enable_differential_privacy=False,
        dp_epsilon=1.0,
        dp_delta=1e-5,
        dp_max_grad_norm=1.0,
        bank_a_transactions=1000,
        bank_b_transactions=1000,
        bank_c_transactions=1000,
        fl_engine_type="distributed",
    )

    # Execute simulation
    sim_run = simulation_service.run_simulation(config)
    assert sim_run.status.value == "completed"
    assert len(sim_run.rounds) == 2

    # Assert model weights aggregated and round outputs generated successfully
    first_round = sim_run.rounds[0]
    assert first_round.round_number == 1
    assert len(first_round.participating_bank_ids) == 3
    assert first_round.global_loss > 0.0
