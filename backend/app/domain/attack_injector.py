"""Adversarial Poisoning Attack Injector Domain Module.

Provides formal implementations of distributed machine learning adversarial poisoning attack
vectors for evaluating Byzantine robustness in federated learning:
  1. Targeted Label Flipping: Inverts binary classification labels (y -> 1 - y)
  2. Gradient Sign Inversion (Sign-Flip): Inverts update direction (Delta w -> -gamma * Delta w)
  3. Extreme Outlier Scaling: Magnifies update magnitude (Delta w -> alpha * Delta w, alpha >> 1)
  4. Isotropic Gaussian Noise: Disrupts optimization with high-variance noise N(0, sigma^2 I)
  5. Backdoor Trigger Injection: Subtle feature perturbation targeted at specific patterns

Theoretical Breakdown Reference:
  - Blanchard et al. (2017) "Machine Learning with Adversaries: Byzantine Tolerant Gradient Descent" (Krum)
  - Guerraoui / El Mhamdi et al. (2018) "The Hidden Vulnerability of Distributed Learning in Byzantium" (Bulyan)
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class AttackType(str, Enum):  # noqa: UP042
    """Supported distributed adversarial attack modalities."""

    SIGN_FLIP = "sign_flip"
    SIGN_INVERSION = "sign_inversion"  # Alias
    SCALED_UPDATE = "scaled_update"
    EXTREME_SCALING = "extreme_scaling"  # Alias
    OUTLIER = "outlier"  # Alias
    GAUSSIAN_NOISE = "gaussian_noise"
    LABEL_POISONING = "label_poisoning"
    LABEL_FLIPPING = "label_flipping"  # Alias
    BACKDOOR_TRIGGER = "backdoor_trigger"

    @classmethod
    def normalize(cls, value: str | AttackType) -> AttackType:
        """Normalizes alias values to canonical attack types."""
        if isinstance(value, cls):
            val_str = value.value.lower()
        elif hasattr(value, "value"):
            val_str = str(value.value).lower()
        else:
            val_str = value.lower().split(".")[-1]

        if val_str in ("sign_flip", "sign_inversion"):
            return cls.SIGN_FLIP
        if val_str in ("scaled_update", "extreme_scaling", "outlier"):
            return cls.SCALED_UPDATE
        if val_str == "gaussian_noise":
            return cls.GAUSSIAN_NOISE
        if val_str in ("label_poisoning", "label_flipping"):
            return cls.LABEL_POISONING
        if val_str == "backdoor_trigger":
            return cls.BACKDOOR_TRIGGER
        raise ValueError(f"Unsupported attack type: {value}")


class AdversarialAttackInjector:
    """Domain service for crafting and injecting adversarial Byzantine poisoning attacks."""

    @staticmethod
    def inject_sign_flip(weights: np.ndarray, scale: float = -3.0) -> np.ndarray:
        """Inverts the gradient direction to disrupt consortium model convergence.

        Args:
            weights: Honest client model weight vector or gradient.
            scale: Negative scalar multiplier (default: -3.0 for aggressive sign inversion).

        Returns:
            Poisoned weight vector directed in reverse parameter space.
        """
        arr = np.asarray(weights, dtype=np.float64)
        if scale > 0:
            scale = -abs(scale)
        return arr * scale

    @staticmethod
    def inject_scaled_update(weights: np.ndarray, scale: float = 100.0) -> np.ndarray:
        """Scales update magnitude by an extreme factor (outlier injection).

        Args:
            weights: Honest client model weight vector.
            scale: Positive scalar multiplier (default: 100.0).

        Returns:
            Poisoned weight vector with catastrophic magnitude.
        """
        arr = np.asarray(weights, dtype=np.float64)
        return arr * abs(scale)

    @staticmethod
    def inject_gaussian_noise(
        shape_or_weights: np.ndarray | tuple[int, ...],
        mean: float = 0.0,
        std: float = 10.0,
        rng: Any = None,
    ) -> np.ndarray:
        """Generates high-variance isotropic Gaussian noise to destabilize aggregation.

        Args:
            shape_or_weights: Target array to match shape of, or integer shape tuple.
            mean: Gaussian distribution mean (default: 0.0).
            std: Gaussian standard deviation (default: 10.0).
            rng: Optional NumPy random number generator.

        Returns:
            Random Gaussian noise vector.
        """
        gen = rng if rng is not None else np.random.default_rng()
        shape = shape_or_weights if isinstance(shape_or_weights, tuple) else shape_or_weights.shape

        return gen.normal(loc=mean, scale=std, size=shape).astype(np.float64)

    @staticmethod
    def inject_label_poisoning(
        labels: np.ndarray,
        poison_ratio: float = 1.0,
        rng: Any = None,
    ) -> np.ndarray:
        """Inverts binary classification labels (y -> 1 - y) for targeted poisoning.

        Args:
            labels: Array of binary labels (0 or 1).
            poison_ratio: Fraction of labels to flip (default: 1.0 = all labels).
            rng: Optional NumPy random number generator.

        Returns:
            Array with flipped labels.
        """
        arr = np.asarray(labels).copy()
        if poison_ratio <= 0.0:
            return arr

        gen = rng if rng is not None else np.random.default_rng()
        n = len(arr)

        if poison_ratio >= 1.0:
            return 1 - arr

        num_flip = int(n * poison_ratio)
        flip_indices = gen.choice(n, size=num_flip, replace=False)
        arr[flip_indices] = 1 - arr[flip_indices]
        return arr

    @classmethod
    def craft_poisoned_gradient(
        cls,
        honest_gradient: np.ndarray,
        attack_type: AttackType | str,
        intensity: float = 1.0,
        rng: Any = None,
    ) -> np.ndarray:
        """Crafts a single poisoned gradient given an honest reference gradient."""
        canonical_type = AttackType.normalize(attack_type)
        arr = np.asarray(honest_gradient, dtype=np.float64)

        if canonical_type == AttackType.SIGN_FLIP:
            scale = -3.0 * intensity
            return cls.inject_sign_flip(arr, scale=scale)

        if canonical_type == AttackType.SCALED_UPDATE:
            scale = 100.0 * intensity
            return cls.inject_scaled_update(arr, scale=scale)

        if canonical_type == AttackType.GAUSSIAN_NOISE:
            std = 10.0 * intensity
            return cls.inject_gaussian_noise(arr, std=std, rng=rng)

        if canonical_type == AttackType.LABEL_POISONING:
            # Gradients derived from flipped labels are approximately negative of honest gradient
            # with added local variance
            gen = rng if rng is not None else np.random.default_rng()
            noise = gen.normal(0.0, 0.05 * np.std(arr) if np.std(arr) > 0 else 0.05, size=arr.shape)
            return (-1.5 * intensity * arr) + noise

        if canonical_type == AttackType.BACKDOOR_TRIGGER:
            # Backdoor: preserve general direction but shift specific coordinate subset aggressively
            gen = rng if rng is not None else np.random.default_rng()
            backdoor_vec = arr.copy()
            trigger_dim = max(1, len(arr) // 10)
            trigger_idx = gen.choice(len(arr), size=trigger_dim, replace=False)
            backdoor_vec[trigger_idx] += 15.0 * intensity
            return backdoor_vec

        raise ValueError(f"Unhandled canonical attack type: {canonical_type}")

    @classmethod
    def generate_consortium_round_updates(
        cls,
        honest_updates: list[np.ndarray],
        n_byzantine: int,
        attack_type: AttackType | str,
        intensity: float = 1.0,
        rng: Any = None,
    ) -> tuple[list[np.ndarray], list[int]]:
        """Synthesizes a full federated round payload containing honest and Byzantine updates.

        Args:
            honest_updates: List of honest client update vectors.
            n_byzantine: Number of Byzantine malicious clients to inject.
            attack_type: Attack modality to deploy.
            intensity: Scaling multiplier for attack strength.
            rng: Optional NumPy random number generator.

        Returns:
            Tuple of (all_updates_list, malicious_indices_list).
        """
        if n_byzantine < 0:
            raise ValueError("n_byzantine cannot be negative")

        gen = rng if rng is not None else np.random.default_rng()
        all_updates: list[np.ndarray] = [np.asarray(u, dtype=np.float64) for u in honest_updates]
        malicious_indices: list[int] = []

        if n_byzantine == 0:
            return all_updates, malicious_indices

        # Compute honest consensus direction for reference
        honest_center = np.mean(all_updates, axis=0)

        for _ in range(n_byzantine):
            mal_idx = len(all_updates)
            poisoned_vec = cls.craft_poisoned_gradient(
                honest_gradient=honest_center,
                attack_type=attack_type,
                intensity=intensity,
                rng=gen,
            )
            all_updates.append(poisoned_vec)
            malicious_indices.append(mal_idx)

        return all_updates, malicious_indices
