"""Adversarial Byzantine attack implementations on local model deltas.

Implements:
1. Scaled Sign Inversion (Delta_mal = -lambda * Delta_local)
2. Isotropic Gaussian Noise (Delta_mal ~ N(0, sigma^2 I))
3. ALIE (A Little Is Enough - Baruch et al., 2019): coordinate-wise tail-evading shift.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import scipy.stats as stats  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]
import torch

from benchmarks.byzantine.config import AttackConfig, ByzantineAttackType

logger = logging.getLogger(__name__)


class BaseByzantineAttack(ABC):
    """Abstract interface for distributed machine learning adversarial update attacks."""

    def __init__(self, config: AttackConfig) -> None:
        self.config = config
        self.attack_type = config.attack_type

    @property
    def knowledge_model(self) -> dict[str, bool]:
        """Explicit machine-readable attacker capability and knowledge model."""
        return {
            "knows_global_model": self.config.knows_global_model,
            "knows_own_local_data": True,
            "knows_own_update": self.config.knows_own_update,
            "knows_other_byzantine_updates": self.config.knows_other_byzantine_updates,
            "knows_honest_updates": self.config.knows_honest_updates,
            "knows_aggregation_rule": self.config.knows_aggregation_rule,
            "knows_client_count": True,
            "knows_byzantine_count": True,
            "knows_test_data": False,  # Strict Invariant: Attacker NEVER has test-set access
            "byzantine_collusion": self.config.byzantine_collusion,
        }

    @abstractmethod
    def apply(
        self,
        local_delta: torch.Tensor,
        client_id: int,
        round_idx: int,
        consortium_deltas: list[torch.Tensor] | None = None,
        seed: int = 42,
    ) -> torch.Tensor:
        """Transforms legitimate local model delta into an adversarial update."""
        pass


class SignFlipAttack(BaseByzantineAttack):
    """Scaled sign-inversion attack: Delta_malicious = -lambda * Delta_local.

    Operates strictly on the legitimate model delta produced by local client training.
    """

    def __init__(self, config: AttackConfig | None = None, scale: float = 3.0) -> None:
        cfg = config or AttackConfig(
            attack_type=ByzantineAttackType.SIGN_FLIP,
            scale=scale,
            knows_honest_updates=False,
        )
        super().__init__(cfg)
        self.scale = abs(cfg.scale)

    def apply(
        self,
        local_delta: torch.Tensor,
        client_id: int,
        round_idx: int,
        consortium_deltas: list[torch.Tensor] | None = None,
        seed: int = 42,
    ) -> torch.Tensor:
        """Inverts and scales the client's legitimate model delta."""
        return -self.scale * local_delta.clone()


class GaussianNoiseAttack(BaseByzantineAttack):
    """Isotropic Gaussian noise disruption attack."""

    def __init__(self, config: AttackConfig | None = None, noise_std: float = 1.0) -> None:
        cfg = config or AttackConfig(
            attack_type=ByzantineAttackType.GAUSSIAN_NOISE,
            noise_std=noise_std,
            knows_honest_updates=False,
        )
        super().__init__(cfg)
        self.noise_std = cfg.noise_std

    def apply(
        self,
        local_delta: torch.Tensor,
        client_id: int,
        round_idx: int,
        consortium_deltas: list[torch.Tensor] | None = None,
        seed: int = 42,
    ) -> torch.Tensor:
        """Injects calibrated Gaussian noise scaled to the attacker's own observed local delta magnitude.

        Threat Model:
            - knows_own_update = True (computes std(local_delta))
            - knows_honest_updates = False (does NOT inspect other clients' updates)
            - knows_aggregation_rule = False
            - knows_test_data = False

        Note: Scaled to match local update dispersion; does NOT claim to reside on the honest parameter manifold.
        """
        g = torch.Generator(device=local_delta.device)
        g.manual_seed(seed + round_idx * 1000 + client_id)

        # Scale relative to local delta standard deviation: sigma_eff = max(1e-4, std(local_delta) * noise_std)
        delta_std = float(torch.std(local_delta).item())
        effective_sigma = max(1e-4, delta_std * self.noise_std)
        noise = torch.randn(local_delta.shape, generator=g, device=local_delta.device, dtype=local_delta.dtype)
        return noise * effective_sigma


class ALIEAttack(BaseByzantineAttack):
    """Omniscient A Little Is Enough (ALIE) adaptive Byzantine attack (Baruch et al., NeurIPS 2019).

    Evaluates an omniscient ALIE variant where colluding Byzantine workers have access to the honest
    consortium updates for the current round, computing coordinate-wise mean mu and sample standard
    deviation sigma (with Bessel's correction = 1) across honest updates, and perturbing coordinates by:
        malicious_delta = mu - z_evaluated * sigma

    Authoritative Threat Model:
        - attack_variant: "OMNISCIENT_ALIE"
        - statistics_source: "HONEST_CONSORTIUM_UPDATES"
        - std_estimator: "SAMPLE_STANDARD_DEVIATION" (correction=1)
        - direction_rule: "CONSTANT_NEGATIVE_OFFSET" (Baruch et al. 2019, Algorithm 3, Line 5)
        - byzantine_collusion: True (Byzantine clients coordinate and submit identical malicious vectors)
        - knows_global_model = True
        - knows_own_local_data = True
        - knows_own_update = True
        - knows_other_byzantine_updates = True
        - knows_honest_updates = True (exact honest consortium updates inspected for mu and sigma)
        - knows_aggregation_rule = True (targeted against statistical aggregation rules)
        - knows_client_count = True (knows consortium n = 12)
        - knows_byzantine_count = True (knows Byzantine f = 2)
        - knows_test_data = False (strictly zero access to holdout test set)

    Parameterization:
        - Evaluated z: Predeclared fixed z = 1.0 (alie_evaluated_z). Evaluated as an explicit strong-information
          MODEL_DELTA stress-test condition.
        - Reference z (Baruch et al. NeurIPS 2019, Algorithm 3):
              For n=12, m=2:
              s = floor(n / 2 + 1) - m = floor(12/2 + 1) - 2 = 7 - 2 = 5 supporters.
              p = (n - m - s) / (n - m) = (12 - 2 - 5) / (12 - 2) = 5 / 10 = 0.500000.
              z_boundary = Phi^{-1}(0.50) = 0.0.
              Note on strict inequality: Algorithm 3 specifies Phi(z) < (n - m - s) / (n - m).
              The supremum / boundary of this open admissible set is Phi^{-1}(0.50) = 0.0.
              The benchmark evaluates z = 1.0 as a fixed project-predeclared parameter distinct from
              this paper-derived reference boundary.
        - Model-Delta Adaptation Semantics: In Baruch et al. (2019), Algorithm 3 operates on synchronous
          SGD worker gradients. In our federated learning protocol, workers run multiple local epochs and submit
          model parameter deltas (w_local - w_global). The ALIE coordinate perturbation mu - z * sigma is adapted
          to MODEL_DELTA space as mu_delta - z * sigma_delta, applying a deterministic negative offset.
        - Sample Standard Deviation: Baruch et al. (2019) specifies "std (sigma_j)" without fixing estimator correction.
          The choice of sample standard deviation with Bessel correction (correction=1) is an explicit project convention
          (std_convention_source = "PROJECT_PREDECLARED_CONVENTION").
        - Zero-Variance Semantics: If sigma_j == 0 across honest updates, malicious_delta_j == mu_j (exact consensus, zero perturbation).
    """

    def __init__(self, config: AttackConfig | None = None, z_max: float | None = None) -> None:
        cfg = config or AttackConfig(
            attack_type=ByzantineAttackType.ALIE,
            attack_variant="OMNISCIENT_ALIE",
            statistics_source="HONEST_CONSORTIUM_UPDATES",
            std_estimator="SAMPLE_STANDARD_DEVIATION",
            std_correction=1,
            std_convention_source="PROJECT_PREDECLARED_CONVENTION",
            direction_rule="CONSTANT_NEGATIVE_OFFSET",
            byzantine_collusion=True,
            alie_evaluated_z=z_max if z_max is not None else 1.0,
            alie_reference_supporters=5,
            alie_reference_probability_boundary=0.5,
            alie_reference_z_boundary=0.0,
            alie_reference_z_max=0.0,
            alie_z_source="PREDECLARED_FIXED_PARAMETER",
            alie_z_max=z_max if z_max is not None else 1.0,
            knows_honest_updates=True,
            knows_other_byzantine_updates=True,
            knows_aggregation_rule=True,
        )
        cfg.knows_honest_updates = True
        cfg.knows_other_byzantine_updates = True
        cfg.knows_aggregation_rule = True
        cfg.byzantine_collusion = True
        super().__init__(cfg)
        self.attack_variant = cfg.attack_variant
        self.statistics_source = cfg.statistics_source
        self.std_estimator = cfg.std_estimator
        self.std_correction = cfg.std_correction
        self.std_convention_source = getattr(cfg, "std_convention_source", "PROJECT_PREDECLARED_CONVENTION")
        self.direction_rule = cfg.direction_rule
        self.byzantine_collusion = cfg.byzantine_collusion
        self.evaluated_z = float(z_max if z_max is not None else cfg.alie_evaluated_z)
        self.reference_supporters = int(getattr(cfg, "alie_reference_supporters", 5))
        self.reference_probability_boundary = float(getattr(cfg, "alie_reference_probability_boundary", 0.5))
        self.reference_z_boundary = float(getattr(cfg, "alie_reference_z_boundary", 0.0))
        self.reference_z_max = float(getattr(cfg, "alie_reference_z_max", 0.0))

    @staticmethod
    def compute_reference_supporters(n: int, m: int) -> int:
        """Computes minimal number s of non-corrupted supporters (Baruch et al. 2019, Algorithm 3, Line 1).

        Formula: s = floor(n / 2 + 1) - m
        """
        import math
        return math.floor(n / 2.0 + 1) - m

    @classmethod
    def compute_reference_probability_boundary(cls, n: int, m: int) -> float:
        """Computes probability boundary (n - m - s) / (n - m) (Baruch et al. 2019, Algorithm 3, Line 2)."""
        s = cls.compute_reference_supporters(n, m)
        return float(n - m - s) / float(n - m)

    @classmethod
    def compute_reference_z_boundary(cls, n: int, m: int) -> float:
        """Computes reference z boundary / supremum: Phi^{-1}((n - m - s) / (n - m)).

        Note on strict inequality: Algorithm 3 specifies Phi(z) < (n - m - s) / (n - m).
        The supremum / boundary of this open admissible set is Phi^{-1}((n - m - s) / (n - m)).
        For n=12, m=2: s = floor(12/2 + 1) - 2 = 5 supporters.
        Probability boundary = (12 - 2 - 5) / (12 - 2) = 5 / 10 = 0.50.
        Normal quantile boundary = Phi^{-1}(0.50) = 0.0.
        """
        p_val = cls.compute_reference_probability_boundary(n, m)
        return float(stats.norm.ppf(p_val))

    def compute_reference_z_max(self, n: int, f: int) -> float:
        """Backward-compatible alias for compute_reference_z_boundary."""
        return self.compute_reference_z_boundary(n=n, m=f)

    def apply(
        self,
        local_delta: torch.Tensor,
        client_id: int,
        round_idx: int,
        consortium_deltas: list[torch.Tensor] | None = None,
        seed: int = 42,
    ) -> torch.Tensor:
        """Crafts coordinate-wise stealth perturbation within honest standard deviation bounds."""
        if not consortium_deltas:
            raise ValueError("ALIE (Omniscient variant) requires non-empty consortium_deltas to compute statistics.")

        if len(consortium_deltas) < 2 and self.std_correction == 1:
            raise ValueError(
                f"Sample standard deviation (correction=1) requires at least 2 deltas, got {len(consortium_deltas)}"
            )

        for d in consortium_deltas:
            if torch.isnan(d).any() or torch.isinf(d).any():
                raise ValueError("Consortium deltas contain NaN or Inf values.")

        stacked = torch.stack(consortium_deltas, dim=0)  # shape (n_honest, D)
        mu = torch.mean(stacked, dim=0)
        # Explicit standard deviation with specified correction (default correction=1: sample std with Bessel's correction)
        # Note: No arbitrary 1e-8 inflation added; if sigma == 0, perturbation is exactly 0
        sigma = torch.std(stacked, dim=0, correction=self.std_correction)

        # Evaluated attack uses predeclared fixed z = 1.0 (self.evaluated_z)
        # Mathematical attack equation (Baruch et al. 2019, Algorithm 3, Line 5):
        # Delta_mal = mu - z * sigma
        malicious_delta = mu - self.evaluated_z * sigma
        return malicious_delta

    def estimate_from_corrupted_workers(self, corrupted_deltas: list[torch.Tensor]) -> torch.Tensor:
        """Estimates mu and sigma strictly from corrupted workers (Baruch et al., 2019, non-omniscient setting).

        Provided as a reference implementation for comparative analysis.
        """
        if not corrupted_deltas:
            raise ValueError("Cannot estimate statistics from empty corrupted deltas list.")
        if len(corrupted_deltas) < 2 and self.std_correction == 1:
            raise ValueError("Sample standard deviation requires at least 2 corrupted updates.")
        for d in corrupted_deltas:
            if torch.isnan(d).any() or torch.isinf(d).any():
                raise ValueError("Corrupted deltas contain NaN or Inf values.")
        stacked = torch.stack(corrupted_deltas, dim=0)
        mu = torch.mean(stacked, dim=0)
        sigma = torch.std(stacked, dim=0, correction=self.std_correction)
        return mu - self.evaluated_z * sigma


def create_attack(config: AttackConfig) -> BaseByzantineAttack:
    """Factory creating appropriate attack instance from configuration."""
    if config.attack_type == ByzantineAttackType.SIGN_FLIP:
        return SignFlipAttack(config)
    if config.attack_type == ByzantineAttackType.GAUSSIAN_NOISE:
        return GaussianNoiseAttack(config)
    if config.attack_type == ByzantineAttackType.ALIE:
        return ALIEAttack(config)
    raise ValueError(f"Unsupported attack type: {config.attack_type}")
