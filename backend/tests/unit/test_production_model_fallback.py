"""Focused adversarial tests for elimination of untrusted production model fallbacks.

Verifies:
- Production inference fails closed with HTTP 503 when no verified champion is available.
- Absence of unverified global_model.pt fallback.
- No synthetic random weights generated on loading failures.
- No checkpoint file overwriting upon deserialization or dimension error.
- Immediate eviction and 503 failure upon issuer key revocation.
- Explainability service (SHAP & LIME) fails closed without fabricating untrained models.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import torch
from fastapi import HTTPException

from app.application.services.explainability_service import ExplainabilityService
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import FraudDetectionModel, ModelService
from app.config import get_settings
from app.domain.model_serving_errors import ModelNotAvailableError
from app.presentation.routers.predict import (
    _get_cached_serving_model,
    clear_all_serving_caches,
)


@pytest.fixture(autouse=True)
def clean_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    clear_all_serving_caches()
    storage_dir = str(tmp_path / "storage")
    os.makedirs(storage_dir, exist_ok=True)
    monkeypatch.setenv("CFI_STORAGE_DIR", storage_dir)
    # Reinitialize global registry and service in router
    import app.presentation.routers.predict as predict_module

    predict_module._registry = ModelRegistry(storage_dir=storage_dir)
    predict_module._model_service = ModelService(get_settings())
    clear_all_serving_caches()
    yield
    clear_all_serving_caches()


class TestProductionModelFallbackElimination:
    """Verifies that production model serving never falls back to untrained weights or unverified files."""

    def test_missing_champion_fails_closed_with_503_and_no_files_created(
        self,
    ) -> None:
        import app.presentation.routers.predict as predict_module

        global_path = os.path.join(predict_module._registry.storage_dir, "global_model.pt")
        assert not os.path.exists(global_path)

        # Serving model resolution must raise 503
        with pytest.raises(HTTPException) as exc_info:
            _get_cached_serving_model()

        assert exc_info.value.status_code == 503
        assert "No verified active champion model artifact found" in exc_info.value.detail

        # Runtime truth invariant: must NOT generate or write a fallback global_model.pt file
        assert not os.path.exists(global_path), "Untrained fallback checkpoint was created on disk!"

    def test_unverified_global_model_pt_is_not_blindly_served(
        self,
    ) -> None:
        """An attacker places an unverified global_model.pt on disk; serving must reject it without manifest."""
        import app.presentation.routers.predict as predict_module

        global_path = os.path.join(predict_module._registry.storage_dir, "global_model.pt")
        fake_model = FraudDetectionModel(input_dim=10)
        torch.save(fake_model.state_dict(), global_path)
        assert os.path.exists(global_path)

        # Serving must reject since it is not backed by an authoritative active champion in registry
        with pytest.raises(HTTPException) as exc_info:
            _get_cached_serving_model()

        assert exc_info.value.status_code == 503

    def test_corrupt_champion_artifact_fails_closed_without_regenerating_weights(
        self,
    ) -> None:
        import app.presentation.routers.predict as predict_module

        reg = predict_module._registry
        sim_id = "sim_corrupt_test"
        model = FraudDetectionModel(input_dim=10, dp_compatible=True)
        reg.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
            status="champion",
            is_promoted=True,
        )

        sim_dir = reg._get_sim_dir(sim_id, create=False)
        artifact_path = os.path.join(sim_dir, "model_v1.pt")

        # Corrupt the artifact bytes on disk
        with open(artifact_path, "wb") as f:
            f.write(b"NOT_A_VALID_PYTORCH_STATE_DICT")

        with pytest.raises(HTTPException) as exc_info:
            _get_cached_serving_model(simulation_id=sim_id)

        assert exc_info.value.status_code == 503

        # Must not have overwritten the artifact with random weights
        with open(artifact_path, "rb") as f:
            content = f.read()
        assert content == b"NOT_A_VALID_PYTORCH_STATE_DICT"

    def test_valid_champion_loads_and_serves_warm_cache(self) -> None:
        import app.presentation.routers.predict as predict_module

        reg = predict_module._registry
        sim_id = "sim_valid_serving"
        model = FraudDetectionModel(input_dim=10, dp_compatible=True)
        reg.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
            status="champion",
            is_promoted=True,
        )

        # First request (cold load)
        loaded1 = _get_cached_serving_model(simulation_id=sim_id)
        assert loaded1 is not None

        # Warm request (from verified cache)
        loaded2 = _get_cached_serving_model(simulation_id=sim_id)
        assert loaded2 is loaded1

    def test_issuer_key_revocation_evicts_cache_and_fails_closed(self) -> None:
        import app.presentation.routers.predict as predict_module

        reg = predict_module._registry
        sim_id = "sim_key_revocation"
        model = FraudDetectionModel(input_dim=10, dp_compatible=True)

        class VerifierStub:
            def __init__(self) -> None:
                self.trusted = True

            def is_key_trusted(self, key_id: str) -> bool:
                return self.trusted

            def verify_receipt(self, receipt: Any, **kwargs: Any) -> tuple[bool, str]:
                return True, "Valid"

        verifier = VerifierStub()
        reg._verifier = verifier  # type: ignore[assignment]

        reg.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"accuracy": 0.95},
            status="champion",
            is_promoted=True,
            training_receipt={"key_id": "issuer_key_alpha"},
        )

        # Successfully loads initially
        loaded = _get_cached_serving_model(simulation_id=sim_id)
        assert loaded is not None

        # Revoke the issuer key
        verifier.trusted = False

        # Next scoring request must reject with HTTP 503
        with pytest.raises(HTTPException) as exc_info:
            _get_cached_serving_model(simulation_id=sim_id)

        assert exc_info.value.status_code == 503


class TestExplainabilityUntrainedFallbackElimination:
    """Verifies that ExplainabilityService never synthesizes random untrained weights."""

    def test_shap_fails_closed_when_no_champion_available(self) -> None:
        service = ExplainabilityService()
        sample_txn = {
            "transaction_amount": 100.0,
            "merchant_category": "grocery",
            "country_code": "US",
            "device_type": "web_browser",
            "velocity": 1.0,
            "hour_of_day": 12,
            "merchant_risk_score": 0.1,
            "customer_history_score": 0.9,
            "chargeback_count": 0,
            "account_age_days": 300,
        }

        with pytest.raises(ModelNotAvailableError, match="requires a verified active model"):
            service.compute_shap_values(sample_txn, model=None)

    def test_lime_fails_closed_when_no_champion_available(self) -> None:
        service = ExplainabilityService()
        sample_txn = {
            "transaction_amount": 100.0,
            "merchant_category": "grocery",
            "country_code": "US",
            "device_type": "web_browser",
            "velocity": 1.0,
            "hour_of_day": 12,
            "merchant_risk_score": 0.1,
            "customer_history_score": 0.9,
            "chargeback_count": 0,
            "account_age_days": 300,
        }

        with pytest.raises(ModelNotAvailableError, match="requires a verified active model"):
            service.compute_lime_explanation(transaction=sample_txn, model=None)
