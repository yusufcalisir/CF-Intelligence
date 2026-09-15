"""Adversarial Defense Service for Active Defense & Adversarial ML Training.

Implements Fast Gradient Sign Method (FGSM) and Projected Gradient Descent (PGD)
evasion perturbation generation with tabular domain constraint projections (L_inf ball).
Used during local bank model training to harden fraud detection ML against evasion attacks.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import torch
import torch.nn as nn

if TYPE_CHECKING:
    from torch.utils.data import DataLoader


@contextmanager
def _disable_opacus_hooks(model: nn.Module):
    modules_to_disable = []
    for m in model.modules():
        try:
            disable_fn = getattr(m, "disable_hooks", None)
            if callable(disable_fn):
                disable_fn()
                modules_to_disable.append(m)
        except Exception:
            pass
    try:
        yield
    finally:
        for m in modules_to_disable:
            try:
                enable_fn = getattr(m, "enable_hooks", None)
                if callable(enable_fn):
                    enable_fn()
            except Exception:
                pass


class AdversarialDefenseService:
    """Singleton service for generating adversarial perturbations and robust model evaluation."""

    _instance: AdversarialDefenseService | None = None

    @classmethod
    def get_instance(cls) -> AdversarialDefenseService:
        if cls._instance is None:
            cls._instance = AdversarialDefenseService()
        return cls._instance

    def project_tabular_constraints(
        self,
        x_adv: torch.Tensor,
        x_orig: torch.Tensor,
        epsilon: float,
        min_val: float = 0.0,
        max_val: float = 1.0,
    ) -> torch.Tensor:
        """Projects adversarial perturbation into L_inf ball [x - eps, x + eps] and feature bounds."""
        # 1. Project onto L_inf epsilon ball around original x
        x_adv = torch.max(torch.min(x_adv, x_orig + epsilon), x_orig - epsilon)
        # 2. Clip to valid tabular feature domain [min_val, max_val]
        x_adv = torch.clamp(x_adv, min=min_val, max=max_val)
        return x_adv

    def generate_fgsm_perturbation(
        self,
        model: nn.Module,
        x: torch.Tensor,
        y: torch.Tensor,
        loss_fn: nn.Module,
        epsilon: float = 0.05,
    ) -> torch.Tensor:
        """Fast Gradient Sign Method (FGSM) 1-step adversarial attack generation.

        x_adv = x + eps * sign(d_L / d_x)
        """
        if epsilon <= 0.0 or x.numel() == 0:
            return x.clone()

        model.eval()
        x_adv = x.clone().detach().requires_grad_(True)

        with _disable_opacus_hooks(model):
            outputs = model(x_adv)
            y_target = y.view_as(outputs)
            loss = loss_fn(outputs, y_target)
            model.zero_grad()
            loss.backward()

        if x_adv.grad is not None:
            gradient_sign = x_adv.grad.data.sign()
            x_perturbed = x_adv + epsilon * gradient_sign
            x_perturbed = self.project_tabular_constraints(x_perturbed, x, epsilon)
            return x_perturbed.detach()

        return x.clone()

    def generate_pgd_perturbation(
        self,
        model: nn.Module,
        x: torch.Tensor,
        y: torch.Tensor,
        loss_fn: nn.Module,
        epsilon: float = 0.05,
        alpha: float = 0.01,
        steps: int = 5,
    ) -> torch.Tensor:
        """Projected Gradient Descent (PGD) multi-step iterative attack generation.

        x^{t+1} = Proj(x^t + alpha * sign(d_L / d_x^t))
        """
        if epsilon <= 0.0 or steps <= 0 or x.numel() == 0:
            return x.clone()

        alpha = max(0.0, alpha)
        model.eval()
        # Initialize x_adv with uniform random noise inside L_inf ball
        x_adv = x.clone().detach() + torch.FloatTensor(x.shape).uniform_(-epsilon, epsilon)
        x_adv = self.project_tabular_constraints(x_adv, x, epsilon)

        with _disable_opacus_hooks(model):
            for _ in range(steps):
                x_adv.requires_grad_(True)
                outputs = model(x_adv)
                y_target = y.view_as(outputs)
                loss = loss_fn(outputs, y_target)
                model.zero_grad()
                loss.backward()

                if x_adv.grad is None:
                    break

                gradient_sign = x_adv.grad.data.sign()
                x_adv = x_adv.detach() + alpha * gradient_sign
                x_adv = self.project_tabular_constraints(x_adv, x, epsilon)

        return x_adv.detach()

    def evaluate_adversarial_robustness(
        self,
        model: nn.Module,
        test_loader: DataLoader[Any],
        loss_fn: nn.Module,
        epsilon: float = 0.05,
    ) -> dict[str, float]:
        """Evaluates model Clean Accuracy vs Robust Accuracy under FGSM and PGD evasion attacks."""
        if epsilon < 0.0:
            raise ValueError("Adversarial perturbation epsilon must be non-negative (>= 0.0)")
        model.eval()
        total_samples = 0
        clean_correct = 0
        fgsm_correct = 0
        pgd_correct = 0

        for x_batch, y_batch in test_loader:
            batch_size = x_batch.size(0)
            total_samples += batch_size

            # Clean evaluation
            with torch.no_grad():
                logits = model(x_batch)
                preds = (torch.sigmoid(logits) >= 0.5).float()
                y_target = y_batch.view_as(preds)
                clean_correct += (preds == y_target).sum().item()

            # FGSM perturbation evaluation
            x_fgsm = self.generate_fgsm_perturbation(model, x_batch, y_batch, loss_fn, epsilon)
            with torch.no_grad():
                logits_fgsm = model(x_fgsm)
                preds_fgsm = (torch.sigmoid(logits_fgsm) >= 0.5).float()
                fgsm_correct += (preds_fgsm == y_target).sum().item()

            # PGD perturbation evaluation
            x_pgd = self.generate_pgd_perturbation(
                model, x_batch, y_batch, loss_fn, epsilon=epsilon, alpha=epsilon / 4.0, steps=5
            )
            with torch.no_grad():
                logits_pgd = model(x_pgd)
                preds_pgd = (torch.sigmoid(logits_pgd) >= 0.5).float()
                pgd_correct += (preds_pgd == y_target).sum().item()

        if total_samples == 0:
            return {
                "clean_accuracy": 0.0,
                "robust_accuracy": 0.0,
                "fgsm_evasion_rate": 0.0,
                "pgd_evasion_rate": 0.0,
                "adversarial_robustness_score": 1.0,
            }

        clean_acc = clean_correct / total_samples
        fgsm_acc = fgsm_correct / total_samples
        pgd_acc = pgd_correct / total_samples

        fgsm_evasion_rate = max(0.0, (clean_correct - fgsm_correct) / total_samples)
        pgd_evasion_rate = max(0.0, (clean_correct - pgd_correct) / total_samples)
        robustness_score = (fgsm_acc + pgd_acc) / 2.0

        return {
            "clean_accuracy": round(clean_acc, 4),
            "robust_accuracy": round(robustness_score, 4),
            "fgsm_evasion_rate": round(fgsm_evasion_rate, 4),
            "pgd_evasion_rate": round(pgd_evasion_rate, 4),
            "adversarial_robustness_score": round(robustness_score, 4),
        }
