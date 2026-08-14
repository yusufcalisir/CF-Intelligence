"""Unit tests for Item 20: Advanced FL Optimizers & Public Dataset Loaders.

Run with:
    pytest tests/test_advanced_opt.py -v
"""

from __future__ import annotations

import numpy as np
import pytest

from app.application.services.dataloader import (
    load_amlsim,
    load_dataset,
    load_elliptic,
    load_paysim,
)
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.privacy_service import PrivacyService
from app.config import Settings
from app.domain.enums import AggregationMethod
from app.domain.value_objects import ModelWeights

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        fedopt_server_lr=0.01,
        fedopt_beta1=0.9,
        fedopt_beta2=0.99,
        fedopt_tau=1e-3,
    )


def _make_weights(dim: int = 100) -> ModelWeights:
    rng = np.random.default_rng(0)
    return ModelWeights(
        layer_shapes=[(dim,)],
        flat_weights=rng.standard_normal(dim).tolist(),
    )


def _make_client_weights(n: int = 3, dim: int = 100, noise: float = 0.1) -> list[ModelWeights]:
    rng = np.random.default_rng(42)
    base = rng.standard_normal(dim)
    result = []
    for _ in range(n):
        noisy = base + rng.normal(0, noise, size=dim)
        result.append(ModelWeights(layer_shapes=[(dim,)], flat_weights=noisy.tolist()))
    return result


# ===========================================================================
# Dataloader tests
# ===========================================================================


class TestEllipticLoader:
    def test_mock_returns_correct_shape(self):
        rng = np.random.default_rng(0)
        result = load_elliptic(n_mock_nodes=500, rng=rng)
        assert result["source"] == "mock"
        assert result["X"].shape == (500, 166)
        assert result["y"].shape == (500,)
        assert isinstance(result["edges"], list)

    def test_mock_labels_are_binary(self):
        result = load_elliptic(n_mock_nodes=1000, rng=np.random.default_rng(1))
        unique = set(result["y"].tolist())
        assert unique <= {0, 1}

    def test_mock_illicit_ratio_approximately_correct(self):
        result = load_elliptic(n_mock_nodes=5000, rng=np.random.default_rng(2))
        illicit_frac = result["y"].mean()
        # Within ±1% of the stated 2% ratio
        assert abs(illicit_frac - 0.021) < 0.015

    def test_mock_has_edges(self):
        result = load_elliptic(n_mock_nodes=200, rng=np.random.default_rng(3))
        assert len(result["edges"]) > 0


class TestAMLSimLoader:
    def test_mock_returns_correct_shape(self):
        result = load_amlsim(n_mock_txns=300, rng=np.random.default_rng(0))
        assert result["source"] == "mock"
        assert result["X"].shape == (300, 6)
        assert result["y"].shape == (300,)

    def test_mock_amounts_are_positive(self):
        result = load_amlsim(n_mock_txns=500, rng=np.random.default_rng(4))
        amounts = result["X"][:, 1]  # amount column
        assert (amounts >= 0).all()


class TestPaySimLoader:
    def test_mock_returns_correct_shape(self, tmp_path):
        result = load_paysim(path=tmp_path / "nonexistent", n_mock_txns=400, rng=np.random.default_rng(0))
        assert "mock" in result["source"]
        assert result["X"].shape == (400, 13)
        assert result["y"].shape == (400,)


class TestLoadDatasetRegistry:
    def test_known_datasets(self, tmp_path):
        # Elliptic takes n_mock_nodes, others take n_mock_txns
        d1 = load_dataset("elliptic", path=tmp_path / "nonexistent", n_mock_nodes=100, rng=np.random.default_rng(0))
        assert d1["X"].shape == (100, 166)

        d2 = load_dataset("amlsim", path=tmp_path / "nonexistent", n_mock_txns=100, rng=np.random.default_rng(0))
        assert d2["X"].shape == (100, 6)

        d3 = load_dataset("paysim", path=tmp_path / "nonexistent", n_mock_txns=100, rng=np.random.default_rng(0))
        assert d3["X"].shape == (100, 13)

        d4 = load_dataset("creditcard", path=tmp_path / "nonexistent", n_mock_txns=100, rng=np.random.default_rng(0))
        assert d4["X"].shape == (100, 29)

    def test_unknown_dataset_raises(self):
        with pytest.raises(ValueError, match="Unknown dataset"):
            load_dataset("nonexistent")


# ===========================================================================
# FedYogi aggregation tests
# ===========================================================================


class TestFedYogi:
    def setup_method(self):
        settings = _make_settings()
        model_svc = ModelService(settings=settings)
        privacy_svc = PrivacyService()
        self.engine = FederatedLearningEngine(
            settings=settings,
            model_service=model_svc,
            privacy_service=privacy_svc,
        )

    def test_fed_yogi_returns_correct_shape(self):
        global_w = _make_weights(100)
        clients = _make_client_weights(3, 100)
        result = self.engine.aggregate_parameters(
            client_weights=clients,
            client_samples=[100, 150, 80],
            method=AggregationMethod.FED_YOGI,
            global_weights=global_w,
        )
        assert len(result.flat_weights) == 100

    def test_fed_yogi_without_global_weights_falls_back_to_avg(self):
        clients = _make_client_weights(3, 50)
        result = self.engine.aggregate_parameters(
            client_weights=clients,
            client_samples=[100, 100, 100],
            method=AggregationMethod.FED_YOGI,
            global_weights=None,
        )
        assert len(result.flat_weights) == 50

    def test_fed_yogi_updates_server_state(self):
        global_w = _make_weights(20)
        clients = _make_client_weights(2, 20)
        sim_id = "test_yogi_sim"

        r1 = self.engine.aggregate_parameters(
            client_weights=clients,
            client_samples=[50, 50],
            method=AggregationMethod.FED_YOGI,
            global_weights=global_w,
            simulation_id=sim_id,
        )
        r2 = self.engine.aggregate_parameters(
            client_weights=clients,
            client_samples=[50, 50],
            method=AggregationMethod.FED_YOGI,
            global_weights=r1,
            simulation_id=sim_id,
        )
        # Two rounds should produce different global weights
        assert r1.flat_weights != r2.flat_weights

    def test_fed_yogi_differs_from_fed_avg(self):
        """FedYogi should give different results than plain FedAvg (server LR effect)."""
        global_w = _make_weights(50)
        clients = _make_client_weights(3, 50, noise=0.5)
        samples = [100, 100, 100]

        yogi_result = self.engine.aggregate_parameters(
            client_weights=clients,
            client_samples=samples,
            method=AggregationMethod.FED_YOGI,
            global_weights=global_w,
            simulation_id="yogi_diff_test",
        )
        avg_result = self.engine.aggregate_parameters(
            client_weights=clients,
            client_samples=samples,
            method=AggregationMethod.FED_AVG,
            global_weights=global_w,
        )
        # At least one weight should differ
        diff = np.abs(np.array(yogi_result.flat_weights) - np.array(avg_result.flat_weights))
        assert diff.max() > 1e-6


# ===========================================================================
# SCAFFOLD aggregation tests
# ===========================================================================


class TestSCAFFOLD:
    def setup_method(self):
        settings = _make_settings()
        model_svc = ModelService(settings=settings)
        privacy_svc = PrivacyService()
        self.engine = FederatedLearningEngine(
            settings=settings,
            model_service=model_svc,
            privacy_service=privacy_svc,
        )

    def test_scaffold_returns_weighted_avg(self):
        """SCAFFOLD server step is a weighted FedAvg."""
        clients = [
            ModelWeights(layer_shapes=[(2,)], flat_weights=[1.0, 2.0]),
            ModelWeights(layer_shapes=[(2,)], flat_weights=[3.0, 4.0]),
        ]
        samples = [1, 1]  # equal weight → simple mean
        result = self.engine.aggregate_parameters(
            client_weights=clients,
            client_samples=samples,
            method=AggregationMethod.SCAFFOLD,
            global_weights=None,
        )
        np.testing.assert_allclose(result.flat_weights, [2.0, 3.0], atol=1e-5)

    def test_scaffold_returns_correct_shape(self):
        clients = _make_client_weights(4, 80)
        result = self.engine.aggregate_parameters(
            client_weights=clients,
            client_samples=[50, 60, 70, 80],
            method=AggregationMethod.SCAFFOLD,
            global_weights=_make_weights(80),
        )
        assert len(result.flat_weights) == 80


# ===========================================================================
# SCAFFOLD client-side gradient correction (model_service)
# ===========================================================================


class TestSCAFFOLDClientSide:
    """Verify that the SCAFFOLD gradient correction in train_local is wired correctly."""

    def test_train_local_with_scaffold_returns_c_local(self):
        import torch

        from app.application.services.model_service import ModelService

        settings = _make_settings()
        svc = ModelService(settings=settings)
        model = svc.create_model()

        # Create zero control variates (no correction in first round)
        params = list(model.parameters())
        c_global = [torch.zeros_like(p) for p in params]
        c_local = [torch.zeros_like(p) for p in params]

        rng = np.random.default_rng(0)
        X = rng.standard_normal((64, NUM_FEATURES)).astype(np.float32)
        y = (rng.random(64) > 0.8).astype(np.float32)

        trained_model, loss_hist, updated_c = svc.train_local(
            model,
            X,
            y,
            epochs=1,
            c_global=c_global,
            c_local=c_local,
        )
        assert trained_model is not None
        assert len(loss_hist) == 1
        assert updated_c is not None
        assert len(updated_c) == len(params)

    def test_train_local_without_scaffold_returns_none(self):
        from app.application.services.model_service import ModelService

        settings = _make_settings()
        svc = ModelService(settings=settings)
        model = svc.create_model()

        rng = np.random.default_rng(1)
        X = rng.standard_normal((32, NUM_FEATURES)).astype(np.float32)
        y = (rng.random(32) > 0.8).astype(np.float32)

        _trained, _loss, c_local_out = svc.train_local(model, X, y, epochs=1)
        assert c_local_out is None
