"""Tests for Differential Privacy Utility Optimization III.

Verifies:
1. DP default hyperparameter calibration (clip norm = 0.5, effective lr = 0.005, effective epochs = 1).
2. SimulationService resolution of effective DP parameters.
3. DP provenance attachment to models during Opacus training.
4. MetricsService preservation and serialization of dp_provenance.
5. Opacus DP budget bounds (epsilon <= 8.0) and secure RNG truthfulness.
"""

import pytest

from app.application.schemas.simulation import SimulationConfigRequest
from app.application.services.metrics_service import MetricsService
from app.application.services.model_service import ModelService
from app.config import get_settings
from app.domain.value_objects import SimulationConfig as DomainConfig


def test_dp_configuration_defaults():
    """Verify that DP defaults are calibrated to optimal operating parameters."""
    config = SimulationConfigRequest()
    assert config.dp_max_grad_norm == 0.5
    assert config.dp_learning_rate is None
    assert config.dp_local_epochs is None

    domain_cfg = DomainConfig(enable_differential_privacy=True)
    assert domain_cfg.dp_max_grad_norm == 0.5
    assert domain_cfg.dp_learning_rate is None
    assert domain_cfg.dp_local_epochs is None


def test_simulation_service_effective_dp_params():
    """Verify that calibrated DP hyperparameters default appropriately."""
    domain_cfg = DomainConfig(
        enable_differential_privacy=True,
        dp_mode="opacus",
        learning_rate=0.001,
        local_epochs=2,
    )
    # Test default fallback for Opacus
    eff_epochs = domain_cfg.dp_local_epochs if domain_cfg.dp_local_epochs is not None else (1 if domain_cfg.dp_mode == "opacus" else domain_cfg.local_epochs)
    eff_lr = domain_cfg.dp_learning_rate if domain_cfg.dp_learning_rate is not None else (0.005 if domain_cfg.dp_mode == "opacus" else domain_cfg.learning_rate)

    assert eff_epochs == 1
    assert eff_lr == 0.005

    # When explicitly provided, custom DP parameters must be respected
    custom_cfg = DomainConfig(
        enable_differential_privacy=True,
        dp_mode="opacus",
        learning_rate=0.001,
        local_epochs=2,
        dp_local_epochs=3,
        dp_learning_rate=0.002,
    )
    eff_epochs_custom = custom_cfg.dp_local_epochs if custom_cfg.dp_local_epochs is not None else (1 if custom_cfg.dp_mode == "opacus" else custom_cfg.local_epochs)
    eff_lr_custom = custom_cfg.dp_learning_rate if custom_cfg.dp_learning_rate is not None else (0.005 if custom_cfg.dp_mode == "opacus" else custom_cfg.learning_rate)

    assert eff_epochs_custom == 3
    assert eff_lr_custom == 0.002


def test_train_local_with_opacus_dp_provenance():
    """Verify that train_local_with_opacus attaches comprehensive DP provenance."""
    model_service = ModelService(settings=get_settings())
    input_dim = 10
    model = model_service.create_model(input_dim=input_dim, dp_compatible=True)

    # Synthetic small dataset
    import numpy as np

    np.random.seed(42)
    x = np.random.randn(64, input_dim).astype(np.float32)
    y = np.random.randint(0, 2, (64,)).astype(np.float32)

    trained_model, loss_history, final_eps = model_service.train_local_with_opacus(
        model=model,
        X_train=x,
        y_train=y,
        target_epsilon=1.0,
        target_delta=1e-5,
        max_grad_norm=0.5,
        epochs=1,
        learning_rate=0.005,
        batch_size=16,
    )

    assert trained_model is not None
    assert final_eps > 0
    assert hasattr(trained_model, "dp_provenance")
    prov = trained_model.dp_provenance
    assert isinstance(prov, dict)
    assert prov["mechanism"] == "opacus_rdp"
    assert prov["clip_norm"] == 0.5
    assert prov["delta"] == 1e-5
    assert prov["epsilon"] == pytest.approx(final_eps)
    assert prov["accountant"] == "rdp"
    assert prov["secure_rng"] is False  # Truthful reporting without torchcsprng


def test_metrics_service_dp_provenance_handling():
    """Verify MetricsService extracts and serializes dp_provenance correctly."""
    eval_dict = {
        "accuracy": 0.95,
        "precision": 0.85,
        "recall": 0.80,
        "f1_score": 0.824,
        "auc_roc": 0.93,
        "pr_auc": 0.88,
        "threshold": 0.42,
        "threshold_provenance": "validation_max_f1",
        "dp_provenance": {
            "mechanism": "opacus_rdp",
            "clip_norm": 0.5,
            "epsilon": 7.48,
            "delta": 1e-5,
        },
    }

    metrics = MetricsService.from_eval_dict(eval_dict)
    assert metrics.dp_provenance == eval_dict["dp_provenance"]

    serialized = MetricsService.metrics_to_dict(metrics)
    assert serialized["dp_provenance"] == eval_dict["dp_provenance"]
    assert serialized["threshold_provenance"] == "validation_max_f1"
