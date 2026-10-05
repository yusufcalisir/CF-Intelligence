"""Unit tests for Opacus differential privacy integration.

Tests that:
1. DP-compatible model passes Opacus validation
2. Standard model fails Opacus validation (confirms BatchNorm is incompatible)
3. train_local_with_opacus completes and returns valid epsilon
4. Opacus-trained model produces valid (non-NaN) predictions
"""

import numpy as np
import pytest
import torch

from app.application.services.model_service import FraudDetectionModel, ModelService
from app.config import get_settings


@pytest.fixture
def model_service():
    return ModelService(get_settings())


class TestDPCompatibleModel:
    def test_dp_model_passes_opacus_validation(self) -> None:
        from opacus.validators import ModuleValidator

        model = FraudDetectionModel(input_dim=10, dp_compatible=True)
        errors = ModuleValidator.validate(model, strict=False)
        assert len(errors) == 0, f"DP model has validation errors: {errors}"

    def test_standard_model_fails_opacus_validation(self) -> None:
        from opacus.validators import ModuleValidator

        model = FraudDetectionModel(input_dim=10, dp_compatible=False)
        errors = ModuleValidator.validate(model, strict=False)
        assert len(errors) > 0, "Standard model should fail Opacus validation (BatchNorm)"

    def test_dp_model_uses_group_norm(self) -> None:
        model = FraudDetectionModel(input_dim=10, dp_compatible=True)
        has_group_norm = any(isinstance(m, torch.nn.GroupNorm) for m in model.modules())
        has_batch_norm = any(isinstance(m, torch.nn.BatchNorm1d) for m in model.modules())
        assert has_group_norm, "DP model should use GroupNorm"
        assert not has_batch_norm, "DP model should not use BatchNorm"

    def test_standard_model_uses_batch_norm(self) -> None:
        model = FraudDetectionModel(input_dim=10, dp_compatible=False)
        has_batch_norm = any(isinstance(m, torch.nn.BatchNorm1d) for m in model.modules())
        assert has_batch_norm, "Standard model should use BatchNorm"


class TestTrainLocalWithOpacus:
    def test_opacus_training_completes(self, model_service: ModelService) -> None:
        rng = np.random.default_rng(42)
        X = rng.standard_normal((200, 10)).astype(np.float32)
        y = rng.integers(0, 2, 200).astype(np.float32)

        model = model_service.create_model(dp_compatible=True)
        trained_model, loss_history, actual_epsilon = model_service.train_local_with_opacus(
            model,
            X,
            y,
            target_epsilon=2.0,
            target_delta=1e-5,
            max_grad_norm=1.0,
            epochs=1,
            learning_rate=0.001,
            batch_size=32,
        )

        assert isinstance(trained_model, FraudDetectionModel)
        assert len(loss_history) == 1
        assert actual_epsilon > 0, "Opacus should report positive epsilon spent"

    def test_opacus_model_produces_valid_predictions(self, model_service: ModelService) -> None:
        rng = np.random.default_rng(42)
        X = rng.standard_normal((200, 10)).astype(np.float32)
        y = rng.integers(0, 2, 200).astype(np.float32)

        model = model_service.create_model(dp_compatible=True)
        trained_model, _, _ = model_service.train_local_with_opacus(
            model,
            X,
            y,
            target_epsilon=2.0,
            target_delta=1e-5,
            epochs=1,
            batch_size=32,
        )

        # Evaluate the trained model
        metrics = model_service.evaluate(trained_model, X[:50], y[:50])
        assert not np.isnan(metrics["loss"]), "Model loss should not be NaN"
        assert 0 <= metrics["accuracy"] <= 1, "Accuracy should be in [0, 1]"

    def test_opacus_weight_serialization(self, model_service: ModelService) -> None:
        """Ensure Opacus-trained model weights can be serialized and deserialized."""
        rng = np.random.default_rng(42)
        X = rng.standard_normal((100, 10)).astype(np.float32)
        y = rng.integers(0, 2, 100).astype(np.float32)

        model = model_service.create_model(dp_compatible=True)
        trained_model, _, _ = model_service.train_local_with_opacus(
            model,
            X,
            y,
            target_epsilon=5.0,
            target_delta=1e-5,
            epochs=1,
            batch_size=32,
        )

        # Serialize and deserialize
        weights = model_service.get_parameters(trained_model)
        assert weights.num_parameters > 0

        new_model = model_service.create_model(dp_compatible=True)
        restored_model = model_service.set_parameters(new_model, weights)

        # Verify parameters were correctly restored
        restored_weights = model_service.get_parameters(restored_model)
        np.testing.assert_allclose(
            weights.flat_weights,
            restored_weights.flat_weights,
            atol=1e-6,
        )

    def test_warning_absence_and_clean_backward(self, model_service: ModelService) -> None:
        """Verify that train_local_with_opacus does not emit the PyTorch full backward hook UserWarning."""
        import warnings

        rng = np.random.default_rng(42)
        X = rng.standard_normal((100, 10)).astype(np.float32)
        y = rng.integers(0, 2, 100).astype(np.float32)

        model = model_service.create_model(dp_compatible=True)
        with warnings.catch_warnings(record=True) as captured_warnings:
            warnings.simplefilter("always")
            trained_model, loss_history, actual_epsilon = model_service.train_local_with_opacus(
                model,
                X,
                y,
                target_epsilon=2.0,
                target_delta=1e-5,
                epochs=1,
                batch_size=32,
            )

            # Assert that no full backward hook warnings were emitted
            hook_warnings = [
                w
                for w in captured_warnings
                if "Full backward hook is firing when gradients are computed" in str(w.message)
            ]
            assert len(hook_warnings) == 0, (
                f"Expected 0 full backward hook warnings, but found {len(hook_warnings)}: "
                f"{[str(w.message) for w in hook_warnings]}"
            )

    def test_parameter_learning_and_updates_verified(
        self, model_service: ModelService
    ) -> None:
        """Verify that loss.backward() and optimizer.step() update representative trainable parameters."""
        rng = np.random.default_rng(42)
        X = rng.standard_normal((100, 10)).astype(np.float32)
        y = rng.integers(0, 2, 100).astype(np.float32)

        model = model_service.create_model(dp_compatible=True)
        initial_params = [p.clone().detach() for p in model.parameters()]

        trained_model, _, _ = model_service.train_local_with_opacus(
            model,
            X,
            y,
            target_epsilon=2.0,
            target_delta=1e-5,
            epochs=2,
            batch_size=32,
        )

        final_params = [p.clone().detach() for p in trained_model.parameters()]
        diffs = [
            torch.norm(init - final).item()
            for init, final in zip(initial_params, final_params)
        ]

        # Every trainable layer's weights/biases must have been updated by optimizer step
        assert all(
            diff > 0.0 for diff in diffs
        ), "All trainable parameters must be updated during training"

    def test_hook_cleanup_and_no_residual_registration(
        self, model_service: ModelService
    ) -> None:
        """Verify that returning from train_local_with_opacus leaves zero residual forward or backward hooks."""
        rng = np.random.default_rng(42)
        X = rng.standard_normal((100, 10)).astype(np.float32)
        y = rng.integers(0, 2, 100).astype(np.float32)

        model = model_service.create_model(dp_compatible=True)
        trained_model, _, _ = model_service.train_local_with_opacus(
            model,
            X,
            y,
            target_epsilon=2.0,
            target_delta=1e-5,
            epochs=1,
            batch_size=32,
        )

        # Inspect all submodules for any registered hooks
        for name, submod in trained_model.named_modules():
            b_hooks = getattr(submod, "_backward_hooks", {})
            f_hooks = getattr(submod, "_forward_hooks", {})
            assert len(b_hooks) == 0, f"Module {name} has residual backward hooks: {b_hooks}"
            assert len(f_hooks) == 0, f"Module {name} has residual forward hooks: {f_hooks}"

    def test_exception_safe_hook_cleanup(self, model_service: ModelService) -> None:
        """Verify that hooks are cleaned up even if an exception occurs during training."""
        from unittest.mock import patch

        model = model_service.create_model(dp_compatible=True)
        rng = np.random.default_rng(42)
        X = rng.standard_normal((50, 10)).astype(np.float32)
        y = rng.integers(0, 2, 50).astype(np.float32)

        # Simulate an unexpected training exception during backward pass
        with (
            patch.object(
                torch.Tensor, "backward", side_effect=RuntimeError("Simulated backward failure")
            ),
            pytest.raises(RuntimeError, match="Simulated backward failure"),
        ):
            model_service.train_local_with_opacus(
                    model,
                    X,
                    y,
                    target_epsilon=2.0,
                    target_delta=1e-5,
                    epochs=1,
                    batch_size=16,
                )

        # Verify that submodules on original model do not have dangling hooks
        for name, submod in model.named_modules():
            b_hooks = getattr(submod, "_backward_hooks", {})
            assert (
                len(b_hooks) == 0
            ), f"Module {name} has dangling backward hooks after exception: {b_hooks}"
