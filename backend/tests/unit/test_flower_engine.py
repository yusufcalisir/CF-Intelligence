"""Unit tests for the Flower FL framework adapter.

Tests that:
1. FraudFlowerClient fits (trains) and evaluates correctly.
2. CallbackFedAvg strategy aggregates fit metrics and triggers callbacks.
3. FlowerFLEngine runs a full multi-round simulation via start_simulation.
4. Flower engine integrates successfully with Opacus differential privacy.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from app.application.services.flower_engine import FlowerFLEngine
from app.application.services.model_service import ModelService
from app.config import get_settings
from app.domain.value_objects import SimulationConfig


@pytest.fixture
def model_service() -> ModelService:
    return ModelService(get_settings())


@pytest.fixture
def dummy_bank_data() -> dict[str, dict[str, np.ndarray]]:
    rng = np.random.default_rng(42)
    # 3 banks, 100 train / 20 test samples each, 10 features
    data: dict[str, dict[str, np.ndarray]] = {}
    for bid in ["bank_a", "bank_b", "bank_c"]:
        data[bid] = {
            "X_train": rng.standard_normal((100, 10)).astype(np.float32),
            "y_train": rng.integers(0, 2, 100).astype(np.float32),
            "X_test": rng.standard_normal((20, 10)).astype(np.float32),
            "y_test": rng.integers(0, 2, 20).astype(np.float32),
        }
    return data


@pytest.fixture(autouse=True)
def cleanup_ray() -> None:
    import sys

    if "ray" in sys.modules:
        ray = sys.modules["ray"]
        if hasattr(ray, "is_initialized") and ray.is_initialized():
            ray.shutdown()
    yield
    if "ray" in sys.modules:
        ray = sys.modules["ray"]
        if hasattr(ray, "is_initialized") and ray.is_initialized():
            ray.shutdown()


class TestFlowerEngine:
    def test_flower_engine_runs_successfully(
        self,
        model_service: ModelService,
        dummy_bank_data: dict[str, dict[str, np.ndarray]],
    ) -> None:
        engine = FlowerFLEngine(model_service)
        config = SimulationConfig(
            num_rounds=2,
            local_epochs=1,
            learning_rate=0.001,
            batch_size=32,
            fl_engine_type="flower",
        )

        global_model = model_service.create_model(dp_compatible=False)

        callback_data: list[dict[str, Any]] = []

        def dummy_callback(sim_id: str, event_type: str, data: dict[str, Any]) -> None:
            if event_type == "round_complete":
                callback_data.append(data)

        result = engine.run_federated_training(
            config=config,
            bank_data=dummy_bank_data,
            global_model=global_model,
            progress_callback=dummy_callback,
            simulation_id="test_sim_id",
        )

        assert "rounds" in result
        assert len(result["rounds"]) == 2
        assert len(callback_data) == 2
        for r in result["rounds"]:
            assert r["round_number"] in [1, 2]
            assert r["global_loss"] > 0
            assert "bank_a" in r["per_bank_loss"]
            assert "bank_b" in r["per_bank_loss"]
            assert "bank_c" in r["per_bank_loss"]
            assert r["per_bank_samples"]["bank_a"] == 100

    def test_flower_engine_with_opacus_dp(
        self,
        model_service: ModelService,
        dummy_bank_data: dict[str, dict[str, np.ndarray]],
    ) -> None:
        engine = FlowerFLEngine(model_service)
        config = SimulationConfig(
            num_rounds=2,
            local_epochs=1,
            learning_rate=0.001,
            batch_size=32,
            fl_engine_type="flower",
            enable_differential_privacy=True,
            dp_mode="opacus",
            dp_epsilon=10.0,
            dp_delta=1e-5,
            dp_max_grad_norm=1.0,
        )

        global_model = model_service.create_model(dp_compatible=True)

        result = engine.run_federated_training(
            config=config,
            bank_data=dummy_bank_data,
            global_model=global_model,
            simulation_id="test_sim_dp",
        )

        assert len(result["rounds"]) == 2
        for r in result["rounds"]:
            assert r["global_loss"] > 0

    def test_flower_engine_native_production_fl_fallback(
        self,
        model_service: ModelService,
        dummy_bank_data: dict[str, dict[str, np.ndarray]],
    ) -> None:
        """Native production fallback must train PyTorch models with FedAvg and zero mocks."""
        engine = FlowerFLEngine(model_service)
        config = SimulationConfig(
            num_rounds=2,
            local_epochs=1,
            learning_rate=0.001,
            batch_size=32,
            fl_engine_type="flower",
        )
        global_model = model_service.create_model(dp_compatible=False)
        callback_data: list[dict[str, Any]] = []

        def dummy_callback(sim_id: str, event_type: str, data: dict[str, Any]) -> None:
            if event_type == "round_complete":
                callback_data.append(data)

        result = engine._run_native_production_fl(
            config=config,
            bank_data=dummy_bank_data,
            global_model=global_model,
            progress_callback=dummy_callback,
            simulation_id="test_sim_fallback",
            use_opacus_dp=False,
        )

        assert "rounds" in result
        assert len(result["rounds"]) == 2
        assert len(callback_data) == 2
        for r in result["rounds"]:
            assert r["round_number"] in [1, 2]
            assert r["global_loss"] > 0
            assert "bank_a" in r["per_bank_loss"]
            assert r["per_bank_samples"]["bank_a"] == 100

    def test_flower_engine_run_p2p_federated_training(
        self,
        model_service: ModelService,
        dummy_bank_data: dict[str, dict[str, np.ndarray]],
    ) -> None:
        """FlowerFLEngine.run_p2p_federated_training delegates to FlowerP2PEngine with real metrics."""
        engine = FlowerFLEngine(model_service)
        config = SimulationConfig(
            num_rounds=2,
            local_epochs=1,
            learning_rate=0.001,
            batch_size=32,
            p2p_mode=True,
            topology="RING",
        )
        callback_data: list[dict[str, Any]] = []

        def dummy_callback(sim_id: str, event_type: str, data: dict[str, Any]) -> None:
            if event_type == "round_complete":
                callback_data.append(data)

        result = engine.run_p2p_federated_training(
            config=config,
            bank_data=dummy_bank_data,
            progress_callback=dummy_callback,
            simulation_id="test_p2p_sim",
            topology="RING",
        )

        assert result["status"] == "SUCCESS"
        assert len(result["rounds"]) == 2
        assert len(callback_data) == 1
        assert result["final_loss"] > 0
