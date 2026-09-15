# ruff: noqa: N818
"""Consortium Policy Engine and Pre-Round Enforcement Rules."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.consortium_governance import Consortium

logger = logging.getLogger(__name__)


class ConsortiumPolicyViolation(Exception):
    """Exception raised when a federated learning round violates consortium policies."""

    pass


@dataclass
class ConsortiumPolicyConfig:
    """Policy configuration limits for a consortium."""

    min_active_members: int = 2
    max_epsilon_budget: float = 5.0
    require_mtls: bool = True
    allowed_architectures: list[str] = field(
        default_factory=lambda: ["PyTorch_MLP", "GraphSAGE", "GAT", "LogisticRegression"]
    )
    allow_cross_border_sharing: bool = False
    allowed_regions: list[str] = field(
        default_factory=lambda: ["eu-central-1", "us-east-1", "apac-singapore-1"]
    )
    restricted_features: list[str] = field(
        default_factory=lambda: [
            "raw_ssn",
            "raw_iban",
            "tax_id",
            "cross_border_unmasked_flow",
            "unencrypted_pii",
        ]
    )
    min_data_samples_per_member: int = 100

    def __post_init__(self) -> None:
        if self.min_active_members < 1:
            raise ValueError(f"min_active_members must be >= 1, got {self.min_active_members}")
        if not math.isfinite(self.max_epsilon_budget) or self.max_epsilon_budget <= 0.0:
            raise ValueError(
                f"max_epsilon_budget must be positive and finite, got {self.max_epsilon_budget}"
            )
        if self.min_data_samples_per_member < 1:
            raise ValueError(
                f"min_data_samples_per_member must be >= 1, got {self.min_data_samples_per_member}"
            )


class ConsortiumPolicyEngine:
    """Evaluates consortium governance constraints prior to starting FL rounds."""

    def __init__(self, config: ConsortiumPolicyConfig | None = None) -> None:
        self.config = config or ConsortiumPolicyConfig()

    def validate_fl_round_preconditions(
        self,
        consortium: Consortium,
        participating_banks: list[str],
        round_epsilon: float,
        architecture: str = "PyTorch_MLP",
        participant_regions: dict[str, str] | None = None,
        shared_features: list[str] | None = None,
        member_sample_counts: dict[str, int] | None = None,
    ) -> tuple[bool, list[str]]:
        """Evaluates whether an FL round satisfies all consortium rules. Returns (is_valid, reasons)."""
        reasons: list[str] = []

        if not participating_banks:
            reasons.append("Participating bank list cannot be empty.")
            return False, reasons

        # 1. Quorum and duplicate verification
        unique_banks = list(dict.fromkeys(participating_banks))
        if len(unique_banks) < len(participating_banks):
            reasons.append(
                f"Duplicate bank participant IDs detected in round roster ({len(participating_banks)} submitted, {len(unique_banks)} unique)"
            )

        if len(unique_banks) < self.config.min_active_members:
            reasons.append(
                f"Insufficient participating members ({len(unique_banks)} < min {self.config.min_active_members})"
            )

        # 2. Differential privacy budget cap check
        if not math.isfinite(round_epsilon) or round_epsilon <= 0.0:
            reasons.append(
                f"Proposed DP epsilon ({round_epsilon}) is invalid. Must be strictly positive and finite (> 0.0)."
            )
        elif round_epsilon > consortium.max_epsilon or round_epsilon > self.config.max_epsilon_budget:
            max_limit = min(consortium.max_epsilon, self.config.max_epsilon_budget)
            reasons.append(
                f"Proposed DP epsilon ({round_epsilon:.2f}) exceeds max allowed limit ({max_limit:.2f})"
            )

        # 3. Member active status verification
        for bank_id in unique_banks:
            if bank_id not in consortium.members:
                reasons.append(
                    f"Bank '{bank_id}' is not an active member of consortium '{consortium.consortium_id}'"
                )

        # 4. Model architecture check
        if architecture not in self.config.allowed_architectures:
            reasons.append(
                f"Model architecture '{architecture}' is not allowed by consortium policy (Allowed: {self.config.allowed_architectures})"
            )

        # 5. Cross-Border Sovereignty & Region Restrictions
        if participant_regions:
            active_regions = set()
            for bank_id in unique_banks:
                if bank_id in participant_regions:
                    reg = participant_regions[bank_id]
                    active_regions.add(reg)
                    if reg not in self.config.allowed_regions:
                        reasons.append(
                            f"Bank '{bank_id}' operates in unapproved jurisdiction '{reg}' (Allowed: {self.config.allowed_regions})"
                        )
            if len(active_regions) > 1 and not self.config.allow_cross_border_sharing:
                reasons.append(
                    f"Cross-border feature and model sharing across distinct regional governance rings ({sorted(active_regions)}) is prohibited without explicit consortium sovereignty waiver."
                )

        # 6. Restricted Feature Governance
        if shared_features:
            forbidden_set = {f.lower() for f in self.config.restricted_features}
            for feat in shared_features:
                if feat.lower() in forbidden_set:
                    reasons.append(
                        f"Feature '{feat}' is restricted from cross-bank sharing under consortium privacy policy."
                    )

        # 7. Minimum Data Contribution per Member
        if member_sample_counts:
            for bank_id in unique_banks:
                count = member_sample_counts.get(bank_id, 0)
                if count < self.config.min_data_samples_per_member:
                    reasons.append(
                        f"Bank '{bank_id}' provides insufficient data samples ({count} < min {self.config.min_data_samples_per_member})"
                    )

        is_valid = len(reasons) == 0
        if not is_valid:
            logger.warning(
                "FL Round policy validation failed for consortium '%s': %s",
                consortium.consortium_id,
                "; ".join(reasons),
            )
        return is_valid, reasons

    def validate_cross_border_sharing(
        self,
        source_region: str,
        destination_region: str,
        features: list[str],
        is_dp_enforced: bool = True,
    ) -> tuple[bool, list[str]]:
        """Evaluates dynamic cross-border data transfer compliance rules under Schrems II and GDPR."""
        reasons: list[str] = []

        if source_region not in self.config.allowed_regions:
            reasons.append(f"Source region '{source_region}' is not in approved consortium jurisdictions.")
        if destination_region not in self.config.allowed_regions:
            reasons.append(f"Destination region '{destination_region}' is not in approved consortium jurisdictions.")

        if source_region != destination_region and not self.config.allow_cross_border_sharing and not is_dp_enforced:
            reasons.append(
                f"Cross-border transfer from '{source_region}' to '{destination_region}' without Differential Privacy scrubbing is strictly prohibited."
            )

        forbidden_set = {f.lower() for f in self.config.restricted_features}
        for feat in features:
            if feat.lower() in forbidden_set:
                reasons.append(f"Restricted feature '{feat}' cannot cross regional boundaries.")

        return len(reasons) == 0, reasons

    def enforce_fl_round_preconditions(
        self,
        consortium: Consortium,
        participating_banks: list[str],
        round_epsilon: float,
        architecture: str = "PyTorch_MLP",
        participant_regions: dict[str, str] | None = None,
        shared_features: list[str] | None = None,
        member_sample_counts: dict[str, int] | None = None,
    ) -> None:
        """Enforces policy gating, raising ConsortiumPolicyViolation if validation fails."""
        is_valid, reasons = self.validate_fl_round_preconditions(
            consortium=consortium,
            participating_banks=participating_banks,
            round_epsilon=round_epsilon,
            architecture=architecture,
            participant_regions=participant_regions,
            shared_features=shared_features,
            member_sample_counts=member_sample_counts,
        )
        if not is_valid:
            raise ConsortiumPolicyViolation(
                f"Consortium policy violation for '{consortium.consortium_id}': "
                + "; ".join(reasons)
            )
