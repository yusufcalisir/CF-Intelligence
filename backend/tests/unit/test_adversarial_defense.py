"""Unit tests for Active Defense & Adversarial Training (FGSM / PGD evasion robustness)."""

from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from app.application.services.adversarial_service import AdversarialDefenseService
from app.application.services.model_service import FraudDetectionModel, ModelService


def test_adversarial_service_singleton():
    service1 = AdversarialDefenseService.get_instance()
    service2 = AdversarialDefenseService.get_instance()
    assert service1 is service2


def test_tabular_constraint_projection():
    service = AdversarialDefenseService.get_instance()
    x_orig = torch.tensor([[0.5, 0.2], [0.9, 0.0]])
    x_adv = torch.tensor([[0.8, -0.1], [1.2, -0.5]])

    x_proj = service.project_tabular_constraints(
        x_adv, x_orig, epsilon=0.1, min_val=0.0, max_val=1.0
    )
    # Check max perturbation bound epsilon = 0.1
    assert torch.all(x_proj <= x_orig + 0.1)
    assert torch.all(x_proj >= x_orig - 0.1)
    # Check feature bounds [0.0, 1.0]
    assert torch.all(x_proj >= 0.0)
    assert torch.all(x_proj <= 1.0)


def test_fgsm_perturbation_generation():
    service = AdversarialDefenseService.get_instance()
    model = FraudDetectionModel(input_dim=10)
    x = torch.randn(4, 10)
    y = torch.tensor([[1.0], [0.0], [1.0], [0.0]])
    loss_fn = nn.BCELoss()

    x_fgsm = service.generate_fgsm_perturbation(model, x, y, loss_fn, epsilon=0.05)
    assert x_fgsm.shape == x.shape
    assert not torch.allclose(x, x_fgsm)


def test_pgd_multi_step_perturbation():
    service = AdversarialDefenseService.get_instance()
    model = FraudDetectionModel(input_dim=10)
    x = torch.randn(4, 10)
    y = torch.tensor([[1.0], [0.0], [1.0], [0.0]])
    loss_fn = nn.BCELoss()

    x_pgd = service.generate_pgd_perturbation(
        model, x, y, loss_fn, epsilon=0.05, alpha=0.01, steps=5
    )
    assert x_pgd.shape == x.shape
    assert not torch.allclose(x, x_pgd)


def test_evaluate_adversarial_robustness():
    service = AdversarialDefenseService.get_instance()
    model = FraudDetectionModel(input_dim=10)
    x = torch.randn(20, 10)
    y = torch.randint(0, 2, (20, 1)).float()

    dataset = TensorDataset(x, y)
    loader = DataLoader(dataset, batch_size=5)

    report = service.evaluate_adversarial_robustness(model, loader, nn.BCELoss(), epsilon=0.05)

    assert "clean_accuracy" in report
    assert "robust_accuracy" in report
    assert "fgsm_evasion_rate" in report
    assert "pgd_evasion_rate" in report
    assert 0.0 <= report["clean_accuracy"] <= 1.0
    assert 0.0 <= report["robust_accuracy"] <= 1.0


def test_train_local_with_adversarial_training():
    from app.config import get_settings

    model_service = ModelService(settings=get_settings())
    model = model_service.create_model()

    np.random.seed(42)
    X_train = np.random.randn(40, 10).astype(np.float32)
    y_train = np.random.randint(0, 2, size=40).astype(np.float32)

    trained_model, loss_history, _ = model_service.train_local(
        model,
        X_train,
        y_train,
        epochs=2,
        batch_size=10,
        enable_adversarial_training=True,
        adversarial_attack_type="fgsm",
        adversarial_epsilon=0.05,
    )

    assert len(loss_history) == 2
    assert isinstance(trained_model, FraudDetectionModel)


def test_adversarial_defense_edge_cases_and_validations():
    import pytest

    service = AdversarialDefenseService.get_instance()
    model = FraudDetectionModel(input_dim=10)
    x = torch.randn(4, 10)
    y = torch.tensor([[1.0], [0.0], [1.0], [0.0]])
    loss_fn = nn.BCELoss()

    # Epsilon <= 0 should return clone
    x_zero_eps = service.generate_fgsm_perturbation(model, x, y, loss_fn, epsilon=0.0)
    assert torch.allclose(x, x_zero_eps)

    # Empty tensor should return empty clone
    empty_x = torch.empty(0, 10)
    empty_y = torch.empty(0, 1)
    res_empty = service.generate_fgsm_perturbation(model, empty_x, empty_y, loss_fn, epsilon=0.05)
    assert res_empty.shape == empty_x.shape

    # PGD steps = 0 should return clone
    x_pgd_zero = service.generate_pgd_perturbation(model, x, y, loss_fn, epsilon=0.05, steps=0)
    assert torch.allclose(x, x_pgd_zero)

    # Negative epsilon in evaluate_adversarial_robustness should raise ValueError
    loader = DataLoader(TensorDataset(x, y), batch_size=2)
    with pytest.raises(ValueError, match="Adversarial perturbation epsilon must be non-negative"):
        service.evaluate_adversarial_robustness(model, loader, loss_fn, epsilon=-0.01)
