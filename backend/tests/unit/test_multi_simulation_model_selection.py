"""Focused adversarial tests for multi-simulation champion resolution and model selection.

Verifies:
- Two independent simulations with active champions resolve their respective models when scoped.
- Scoped resolution strictly isolates artifacts and prevents cross-simulation contamination.
- Unscoped resolution with multiple active champions rejects ambiguous resolution (fails closed).
- Explicit platform-default configuration (CFI_DEFAULT_SIMULATION_ID) resolves cleanly.
- Single-simulation environments preserve backward compatibility when only one champion is active.
- Production HTTP inference endpoint (/predict) respects simulation scoping and default bindings.
"""

from __future__ import annotations

import os
import pytest
import torch
from fastapi.testclient import TestClient

from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import FraudDetectionModel, ModelService
from app.config import get_settings
from app.domain.model_serving_errors import ModelCompatibilityError
from app.main import app
from app.presentation.routers.predict import clear_all_serving_caches


@pytest.fixture
def clean_registry(tmp_path: os.PathLike[str]) -> ModelRegistry:
    storage_dir = str(tmp_path / "storage")
    reg = ModelRegistry(storage_dir=storage_dir)
    return reg


def _create_distinct_model(bias_val: float) -> FraudDetectionModel:
    model = FraudDetectionModel(input_dim=10, dp_compatible=True)
    with torch.no_grad():
        for param in model.parameters():
            param.fill_(bias_val)
    model.eval()
    return model


class TestMultiSimulationChampionResolution:
    """Verifies ModelRegistry champion resolution across multiple simulations."""

    def test_scoped_resolution_returns_respective_champion(
        self, clean_registry: ModelRegistry
    ) -> None:
        model_a = _create_distinct_model(1.0)
        model_b = _create_distinct_model(2.0)

        # Register simulation A
        clean_registry.save_version(
            simulation_id="sim_bank_a",
            state_dict=model_a.state_dict(),
            metrics={"accuracy": 0.91},
            is_promoted=True,
            status="champion",
        )
        # Register simulation B
        clean_registry.save_version(
            simulation_id="sim_bank_b",
            state_dict=model_b.state_dict(),
            metrics={"accuracy": 0.94},
            is_promoted=True,
            status="champion",
        )

        # Scoped query for sim_bank_a
        resolved_a = clean_registry.resolve_and_verify_champion("sim_bank_a")
        assert resolved_a is not None
        path_a, bytes_a, meta_a = resolved_a
        assert "sim_bank_a" in path_a
        assert meta_a["simulation_id"] == "sim_bank_a"

        # Scoped query for sim_bank_b
        resolved_b = clean_registry.resolve_and_verify_champion("sim_bank_b")
        assert resolved_b is not None
        path_b, bytes_b, meta_b = resolved_b
        assert "sim_bank_b" in path_b
        assert meta_b["simulation_id"] == "sim_bank_b"

        # Verify weights are strictly isolated and not identical
        loaded_a = torch.load(path_a, weights_only=True)
        loaded_b = torch.load(path_b, weights_only=True)
        assert torch.allclose(loaded_a["network.0.weight"], torch.tensor(1.0))
        assert torch.allclose(loaded_b["network.0.weight"], torch.tensor(2.0))
        assert not torch.equal(loaded_a["network.0.weight"], loaded_b["network.0.weight"])

    def test_unscoped_resolution_with_multiple_champions_fails_without_guessing(
        self, clean_registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CFI_DEFAULT_SIMULATION_ID", raising=False)

        model_a = _create_distinct_model(1.0)
        model_b = _create_distinct_model(2.0)

        clean_registry.save_version(
            simulation_id="sim_bank_a",
            state_dict=model_a.state_dict(),
            metrics={"accuracy": 0.91},
            is_promoted=True,
            status="champion",
        )
        clean_registry.save_version(
            simulation_id="sim_bank_b",
            state_dict=model_b.state_dict(),
            metrics={"accuracy": 0.94},
            is_promoted=True,
            status="champion",
        )

        # Unscoped resolution must fail closed with ModelCompatibilityError
        with pytest.raises(
            ModelCompatibilityError,
            match="Ambiguous unscoped champion resolution: multiple active champions exist across simulations",
        ):
            clean_registry.resolve_and_verify_champion(simulation_id=None)

        # Lightweight identity resolution must return None rather than guessing
        assert clean_registry.get_active_champion_identity(simulation_id=None) is None

    def test_unscoped_resolution_resolves_configured_platform_default(
        self, clean_registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model_a = _create_distinct_model(1.0)
        model_b = _create_distinct_model(2.0)

        clean_registry.save_version(
            simulation_id="sim_bank_a",
            state_dict=model_a.state_dict(),
            metrics={"accuracy": 0.91},
            is_promoted=True,
            status="champion",
        )
        clean_registry.save_version(
            simulation_id="sim_bank_b",
            state_dict=model_b.state_dict(),
            metrics={"accuracy": 0.94},
            is_promoted=True,
            status="champion",
        )

        # Configure default simulation to sim_bank_a
        monkeypatch.setenv("CFI_DEFAULT_SIMULATION_ID", "sim_bank_a")

        resolved = clean_registry.resolve_and_verify_champion(simulation_id=None)
        assert resolved is not None
        path, _, meta = resolved
        assert "sim_bank_a" in path
        assert meta["simulation_id"] == "sim_bank_a"

        ident = clean_registry.get_active_champion_identity(simulation_id=None)
        assert ident is not None
        assert ident["simulation_id"] == "sim_bank_a"

        # Change default to sim_bank_b
        monkeypatch.setenv("CFI_DEFAULT_SIMULATION_ID", "sim_bank_b")

        resolved_b = clean_registry.resolve_and_verify_champion(simulation_id=None)
        assert resolved_b is not None
        path_b, _, meta_b = resolved_b
        assert "sim_bank_b" in path_b
        assert meta_b["simulation_id"] == "sim_bank_b"

    def test_unscoped_resolution_with_missing_configured_default_fails_closed(
        self, clean_registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model_a = _create_distinct_model(1.0)
        clean_registry.save_version(
            simulation_id="sim_bank_a",
            state_dict=model_a.state_dict(),
            metrics={"accuracy": 0.91},
            is_promoted=True,
            status="champion",
        )

        # Set default to a simulation that does not exist
        monkeypatch.setenv("CFI_DEFAULT_SIMULATION_ID", "sim_nonexistent")

        resolved = clean_registry.resolve_and_verify_champion(simulation_id=None)
        assert resolved is None

        ident = clean_registry.get_active_champion_identity(simulation_id=None)
        assert ident is None

    def test_unscoped_resolution_with_single_active_champion_backward_compatible(
        self, clean_registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CFI_DEFAULT_SIMULATION_ID", raising=False)

        model_a = _create_distinct_model(1.0)
        model_b = _create_distinct_model(2.0)

        # Register both
        clean_registry.save_version(
            simulation_id="sim_bank_a",
            state_dict=model_a.state_dict(),
            metrics={"accuracy": 0.91},
            is_promoted=True,
            status="champion",
        )
        clean_registry.save_version(
            simulation_id="sim_bank_b",
            state_dict=model_b.state_dict(),
            metrics={"accuracy": 0.94},
            is_promoted=True,
            status="champion",
        )

        # Revoke sim_bank_b so only sim_bank_a remains active
        clean_registry.revoke_version("sim_bank_b", version=1, reason="Test deactivation")

        # Now only sim_bank_a has an active champion
        resolved = clean_registry.resolve_and_verify_champion(simulation_id=None)
        assert resolved is not None
        path, _, meta = resolved
        assert "sim_bank_a" in path
        assert meta["simulation_id"] == "sim_bank_a"


class TestMultiSimulationServingEndpoint:
    """Verifies that production HTTP inference (/predict) respects multi-simulation boundaries."""

    @pytest.fixture(autouse=True)
    def setup_serving_env(
        self, tmp_path: os.PathLike[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clear_all_serving_caches()
        storage_dir = str(tmp_path / "storage")
        registry = ModelRegistry(storage_dir=storage_dir)

        monkeypatch.setattr("app.presentation.routers.predict._registry", registry)
        monkeypatch.setattr(
            "app.presentation.routers.predict._model_service",
            ModelService(get_settings()),
        )

        self.registry = registry
        self.client = TestClient(app)

    def test_scoped_predict_resolves_correct_simulation_champion(self) -> None:
        model_a = _create_distinct_model(0.5)
        model_b = _create_distinct_model(1.5)

        self.registry.save_version(
            simulation_id="sim_corp_1",
            state_dict=model_a.state_dict(),
            metrics={"accuracy": 0.91},
            is_promoted=True,
            status="champion",
        )
        self.registry.save_version(
            simulation_id="sim_corp_2",
            state_dict=model_b.state_dict(),
            metrics={"accuracy": 0.94},
            is_promoted=True,
            status="champion",
        )

        payload_a = {
            "simulation_id": "sim_corp_1",
            "transaction_amount": 150.0,
            "merchant_category": "grocery",
            "country_code": "US",
            "device_type": "web_browser",
            "velocity": 1.0,
            "hour_of_day": 12,
            "merchant_risk_score": 0.1,
            "customer_history_score": 0.9,
            "chargeback_count": 0,
            "account_age_days": 300,
            "transaction_id": "tx_scoped_a",
        }
        res_a = self.client.post("/api/v1/predict", json=payload_a)
        assert res_a.status_code == 200
        data_a = res_a.json()
        assert "fraud_probability" in data_a

        payload_b = {
            "simulation_id": "sim_corp_2",
            "transaction_amount": 250.0,
            "merchant_category": "electronics",
            "country_code": "US",
            "device_type": "mobile_app",
            "velocity": 2.0,
            "hour_of_day": 15,
            "merchant_risk_score": 0.2,
            "customer_history_score": 0.85,
            "chargeback_count": 0,
            "account_age_days": 180,
            "transaction_id": "tx_scoped_b",
        }
        res_b = self.client.post("/api/v1/predict", json=payload_b)
        assert res_b.status_code == 200
        data_b = res_b.json()
        assert "fraud_probability" in data_b

    def test_unscoped_predict_with_ambiguous_champions_returns_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CFI_DEFAULT_SIMULATION_ID", raising=False)

        model_a = _create_distinct_model(0.5)
        model_b = _create_distinct_model(1.5)

        self.registry.save_version(
            simulation_id="sim_corp_1",
            state_dict=model_a.state_dict(),
            metrics={"accuracy": 0.91},
            is_promoted=True,
            status="champion",
        )
        self.registry.save_version(
            simulation_id="sim_corp_2",
            state_dict=model_b.state_dict(),
            metrics={"accuracy": 0.94},
            is_promoted=True,
            status="champion",
        )

        # Unscoped request: no simulation_id provided
        payload_unscoped = {
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
            "transaction_id": "tx_unscoped_ambiguous",
        }
        res = self.client.post("/api/v1/predict", json=payload_unscoped)
        assert res.status_code == 503
        assert "No verified active champion model artifact found" in res.json()["detail"]

    def test_unscoped_predict_with_configured_default_serves_successfully(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model_a = _create_distinct_model(0.5)
        model_b = _create_distinct_model(1.5)

        self.registry.save_version(
            simulation_id="sim_corp_1",
            state_dict=model_a.state_dict(),
            metrics={"accuracy": 0.91},
            is_promoted=True,
            status="champion",
        )
        self.registry.save_version(
            simulation_id="sim_corp_2",
            state_dict=model_b.state_dict(),
            metrics={"accuracy": 0.94},
            is_promoted=True,
            status="champion",
        )

        # Set default simulation
        monkeypatch.setenv("CFI_DEFAULT_SIMULATION_ID", "sim_corp_1")

        payload_unscoped = {
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
            "transaction_id": "tx_unscoped_default",
        }
        res = self.client.post("/api/v1/predict", json=payload_unscoped)
        assert res.status_code == 200
        assert "fraud_probability" in res.json()
