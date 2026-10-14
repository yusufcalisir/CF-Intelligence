"""Unit tests for Production Inference Error Sanitization & Information Disclosure Defense.

Verifies that public inference HTTP endpoints return generic, sanitized error messages:
- Never disclose absolute filesystem paths, storage roots, or directory layouts.
- Never disclose internal model filenames, artifact hashes, or cryptographic digests.
- Never disclose training receipt keys, issuer IDs, or signing infrastructure metadata.
- Never disclose internal stack traces or unhandled exception details.
- Fail closed with HTTP 503 on model unavailability, rejection, or revocation.
- Return HTTP 422 on genuine client input validation failures without server internals.
- Fail closed with HTTP 500 on unexpected pipeline errors with generic messages.
- Legitimate production inference continues to succeed with HTTP 200.
"""

from __future__ import annotations

import os
import re
from typing import Any
from unittest.mock import patch

import pytest
import torch
from fastapi.testclient import TestClient

import app.presentation.routers.predict as predict_module
from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import FraudDetectionModel, ModelService
from app.config import get_settings
from app.main import app
from app.presentation.routers.predict import clear_all_serving_caches


def _create_test_model(weight_val: float = 1.0) -> FraudDetectionModel:
    model = FraudDetectionModel(input_dim=len(FEATURE_NAMES), dp_compatible=True)
    with torch.no_grad():
        for param in model.parameters():
            param.fill_(weight_val)
    model.eval()
    return model


def _valid_transaction_payload(simulation_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "transaction_amount": 125.50,
        "merchant_category": "grocery",
        "country_code": "US",
        "device_type": "web_browser",
        "velocity": 1.5,
        "hour_of_day": 14,
        "merchant_risk_score": 0.12,
        "customer_history_score": 0.88,
        "chargeback_count": 0,
        "account_age_days": 365,
        "transaction_id": "tx_sanitization_test_01",
    }
    if simulation_id is not None:
        payload["simulation_id"] = simulation_id
    return payload


@pytest.fixture(autouse=True)
def clean_serving_state(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> ModelRegistry:
    clear_all_serving_caches()
    storage_dir = str(tmp_path / "model_storage")
    os.makedirs(storage_dir, exist_ok=True)
    registry = ModelRegistry(storage_dir=storage_dir)

    monkeypatch.setattr("app.presentation.routers.predict._registry", registry)
    monkeypatch.setattr(
        "app.presentation.routers.predict._model_service",
        ModelService(get_settings()),
    )
    return registry


class TestProductionInferenceErrorSanitization:
    """Verifies that public inference responses never disclose internal server state."""

    def test_missing_model_artifact_returns_sanitized_503_without_filesystem_paths(
        self, clean_serving_state: ModelRegistry
    ) -> None:
        """A missing active champion artifact must fail closed with HTTP 503 and no paths in response."""
        client = TestClient(app)
        registry = clean_serving_state
        sim_id = "sim_missing_artifact_test"

        model = _create_test_model()
        registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
            status="champion",
            is_promoted=True,
        )

        sim_dir = registry._get_sim_dir(sim_id, create=False)
        artifact_file = os.path.join(sim_dir, "model_v1.pt")
        assert os.path.isfile(artifact_file)
        os.remove(artifact_file)
        assert not os.path.exists(artifact_file)

        response = client.post("/api/v1/predict", json=_valid_transaction_payload(sim_id))

        assert response.status_code == 503
        body = response.json()
        assert body["detail"] == "Serving model unavailable: active champion artifact is missing or inaccessible."

        # Information disclosure checks: no filesystem paths or internal filenames in response text
        raw_text = response.text
        assert "model_v1.pt" not in raw_text
        assert "model_storage" not in raw_text
        assert "\\" not in raw_text
        assert "C:" not in raw_text
        assert "ModelNotAvailableError" not in raw_text
        assert "does not exist on disk" not in raw_text

    def test_corrupted_model_artifact_returns_sanitized_503_without_cryptographic_hashes(
        self, clean_serving_state: ModelRegistry
    ) -> None:
        """A corrupted model artifact must fail closed with HTTP 503 and zero cryptographic hashes."""
        client = TestClient(app)
        registry = clean_serving_state
        sim_id = "sim_corrupted_artifact_test"

        model = _create_test_model()
        saved_entry = registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
            status="champion",
            is_promoted=True,
        )
        expected_sha = saved_entry["sha256"]

        sim_dir = registry._get_sim_dir(sim_id, create=False)
        artifact_file = os.path.join(sim_dir, "model_v1.pt")
        with open(artifact_file, "wb") as f:
            f.write(b"CORRUPTED_ADVERSARIAL_WEIGHT_DATA")

        response = client.post("/api/v1/predict", json=_valid_transaction_payload(sim_id))

        assert response.status_code == 503
        body = response.json()
        assert body["detail"] == "Serving model rejected: artifact integrity verification failed."

        # Information disclosure checks: no cryptographic hashes or mismatch details in response text
        raw_text = response.text
        assert expected_sha not in raw_text
        assert "digest mismatch" not in raw_text
        assert "expected" not in raw_text
        assert "actual" not in raw_text
        assert "ModelIntegrityError" not in raw_text
        assert not re.search(r"\b[a-f0-9]{32,64}\b", raw_text)

    def test_revoked_issuer_key_returns_sanitized_503_without_key_identity(
        self, clean_serving_state: ModelRegistry
    ) -> None:
        """A revoked signing key must fail closed with HTTP 503 without revealing key identifier."""
        client = TestClient(app)
        registry = clean_serving_state
        sim_id = "sim_revoked_key_test"
        private_key_id = "hsm_inst_corp_alpha_priv_2026_sec_key"

        class VerifierStub:
            def __init__(self) -> None:
                self.trusted = True

            def is_key_trusted(self, key_id: str) -> bool:
                return self.trusted

            def verify_receipt(self, receipt: Any, **kwargs: Any) -> tuple[bool, str]:
                return True, "Valid"

        verifier = VerifierStub()
        registry._verifier = verifier  # type: ignore[assignment]

        model = _create_test_model()
        registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
            status="champion",
            is_promoted=True,
            training_receipt={"key_id": private_key_id},
        )

        # First request succeeds (populates serving cache)
        initial_res = client.post("/api/v1/predict", json=_valid_transaction_payload(sim_id))
        assert initial_res.status_code == 200

        # Key is revoked
        verifier.trusted = False

        # Next request must fail closed with sanitized error
        response = client.post("/api/v1/predict", json=_valid_transaction_payload(sim_id))
        assert response.status_code == 503
        body = response.json()
        assert body["detail"] in (
            "Serving model rejected: model authority or signing key has been revoked.",
            "No verified active champion model artifact found in registry.",
        )

        # Information disclosure checks: private key identity must not be leaked
        raw_text = response.text
        assert private_key_id not in raw_text
        assert "hsm_inst" not in raw_text
        assert "2026_sec_key" not in raw_text

        # Also verify step 2 cache-revalidation direct path when manifest returns identity but key is untrusted
        fake_ident = {
            "version": 1,
            "sha256": "f" * 64,
            "generation": 1,
            "issuer_key_id": private_key_id,
        }
        with predict_module._serving_cache_lock:
            predict_module._serving_model_cache[sim_id] = (model, fake_ident)

        with patch.object(
            registry,
            "get_active_champion_identity",
            return_value=fake_ident,
        ):
            with pytest.raises(predict_module.HTTPException) as exc_info:
                predict_module._get_cached_serving_model(sim_id)
            assert exc_info.value.status_code == 503
            assert exc_info.value.detail == "Serving model rejected: model authority or signing key has been revoked."
            assert private_key_id not in str(exc_info.value.detail)

    def test_unexpected_inference_pipeline_failure_returns_sanitized_500_without_internal_exception(
        self, clean_serving_state: ModelRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unexpected runtime exception in inference pipeline must return sanitized HTTP 500."""
        client = TestClient(app)
        registry = clean_serving_state
        sim_id = "sim_pipeline_failure_test"

        model = _create_test_model()
        registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
            status="champion",
            is_promoted=True,
        )

        def _exploding_eval(*args: Any, **kwargs: Any) -> float:
            raise RuntimeError(
                "Internal CUDA kernel crash at 0x7fff33a0 with tensor buffer layout [128, 512] on accelerator unit 0"
            )

        monkeypatch.setattr("app.presentation.routers.predict._eval_model", _exploding_eval)

        response = client.post("/api/v1/predict", json=_valid_transaction_payload(sim_id))
        assert response.status_code == 500
        body = response.json()
        assert body["detail"] == "Inference pipeline execution error."

        # Information disclosure checks: raw internal exception string must not leak
        raw_text = response.text
        assert "CUDA" not in raw_text
        assert "0x7fff33a0" not in raw_text
        assert "tensor buffer layout" not in raw_text
        assert "accelerator unit" not in raw_text
        assert "RuntimeError" not in raw_text

    def test_unexpected_batch_inference_failure_returns_sanitized_500(
        self, clean_serving_state: ModelRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unexpected runtime exception in batch inference must return sanitized HTTP 500."""
        client = TestClient(app)
        registry = clean_serving_state
        sim_id = "sim_batch_failure_test"

        model = _create_test_model()
        registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
            status="champion",
            is_promoted=True,
        )

        def _exploding_batch_eval(*args: Any, **kwargs: Any) -> list[float]:
            raise RuntimeError("Internal GPU scheduler deadlock in threadpool cluster 4")

        monkeypatch.setattr("app.presentation.routers.predict._eval_model_batch", _exploding_batch_eval)

        batch_payload = {
            "simulation_id": sim_id,
            "transactions": [_valid_transaction_payload(sim_id)],
        }
        response = client.post("/api/v1/predict/batch", json=batch_payload)
        assert response.status_code == 500
        body = response.json()
        assert body["detail"] == "Batch inference pipeline execution error."

        raw_text = response.text
        assert "deadlock" not in raw_text
        assert "cluster 4" not in raw_text
        assert "GPU scheduler" not in raw_text

    def test_unexpected_explainability_failure_returns_sanitized_500(
        self, clean_serving_state: ModelRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unexpected runtime exception in explainability pipeline must return sanitized HTTP 500."""
        client = TestClient(app)
        registry = clean_serving_state
        sim_id = "sim_explain_failure_test"

        model = _create_test_model()
        registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
            status="champion",
            is_promoted=True,
        )

        def _exploding_shap(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("Internal C++ SHAP kernel extension segmentation fault at 0x9000a1")

        monkeypatch.setattr(
            predict_module._explainability_service,
            "compute_shap_values",
            _exploding_shap,
        )

        explain_payload = {
            "transaction_id": "tx_explain_sanitized",
            "transaction": _valid_transaction_payload(sim_id),
            "method": "shap_kernel_explainer",
        }
        response = client.post("/api/v1/predict/explain", json=explain_payload)
        assert response.status_code == 500
        body = response.json()
        assert body["detail"] == "Explainability execution failed."

        raw_text = response.text
        assert "segmentation fault" not in raw_text
        assert "0x9000a1" not in raw_text
        assert "C++ SHAP" not in raw_text

    def test_missing_champion_manifest_returns_sanitized_503(
        self, clean_serving_state: ModelRegistry
    ) -> None:
        """When no champion exists in registry, response is generic HTTP 503."""
        client = TestClient(app)
        response = client.post("/api/v1/predict", json=_valid_transaction_payload())

        assert response.status_code == 503
        body = response.json()
        assert body["detail"] == "No verified active champion model artifact found in registry."

        raw_text = response.text
        assert "\\" not in raw_text
        assert "C:" not in raw_text
        assert "model_storage" not in raw_text

    def test_legitimate_production_inference_continues_to_succeed(
        self, clean_serving_state: ModelRegistry
    ) -> None:
        """Legitimate verified inference succeeds with HTTP 200 and valid business response."""
        client = TestClient(app)
        registry = clean_serving_state
        sim_id = "sim_legitimate_success"

        model = _create_test_model(0.5)
        registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.96},
            status="champion",
            is_promoted=True,
        )

        response = client.post("/api/v1/predict", json=_valid_transaction_payload(sim_id))
        assert response.status_code == 200
        body = response.json()

        assert "fraud_probability" in body
        assert isinstance(body["fraud_probability"], float)
        assert 0.0 <= body["fraud_probability"] <= 1.0
        assert "risk_score" in body
        assert 0 <= body["risk_score"] <= 1000
        assert "is_fraud_suspected" in body
        assert "breakdown" in body
        assert isinstance(body["breakdown"], list)
        assert len(body["breakdown"]) > 0

    def test_genuine_client_input_validation_error_returns_422_without_server_internals(
        self, clean_serving_state: ModelRegistry
    ) -> None:
        """Malformed client transaction input returns HTTP 422 without exposing server filesystem internals."""
        client = TestClient(app)
        registry = clean_serving_state
        sim_id = "sim_validation_err_test"

        model = _create_test_model()
        registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
            status="champion",
            is_promoted=True,
        )

        # Non-finite transaction amount (NaN/Inf) is rejected
        invalid_payload = _valid_transaction_payload(sim_id)
        invalid_payload["transaction_amount"] = "NOT_A_VALID_FLOAT"

        response = client.post("/api/v1/predict", json=invalid_payload)
        assert response.status_code == 422
        raw_text = response.text

        # Standard FastAPI/Pydantic validation without internal paths
        assert "model_storage" not in raw_text
        assert "C:\\" not in raw_text
        assert "Traceback" not in raw_text

    def test_score_transaction_endpoint_sanitizes_inference_failure_to_500(
        self, clean_serving_state: ModelRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Alternative score-transaction endpoint sanitizes internal inference failure to HTTP 500."""
        client = TestClient(app)
        registry = clean_serving_state
        sim_id = "sim_score_txn_failure"

        model = _create_test_model()
        registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
            status="champion",
            is_promoted=True,
        )

        def _exploding_eval(*args: Any, **kwargs: Any) -> float:
            raise RuntimeError("Secret proprietary inference coprocessor bus fault at 0xfa3910")

        monkeypatch.setattr("app.presentation.routers.predict._eval_model", _exploding_eval)

        score_payload = {
            "transaction_id": "tx_score_test_001",
            "account_id": "acc_score_001",
            "amount": 250.0,
            "merchant_id": "merch_electronics_01",
            "country": "US",
            "device_id": "web_browser",
        }
        response = client.post("/api/v1/score-transaction", json=score_payload)
        assert response.status_code == 500
        body = response.json()
        assert body["detail"] == "Inference pipeline execution error."

        raw_text = response.text
        assert "bus fault" not in raw_text
        assert "0xfa3910" not in raw_text
        assert "coprocessor" not in raw_text
