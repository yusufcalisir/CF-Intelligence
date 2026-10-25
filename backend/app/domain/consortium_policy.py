# ruff: noqa: N818
"""Consortium Policy Engine and Pre-Round Enforcement Rules."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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


@dataclass
class IntelligenceSharingPolicyConfig:
    """Policy rules governing cross-bank intelligence queries and disclosures."""

    allowed_purposes: list[str] = field(
        default_factory=lambda: [
            "AML_COLLABORATIVE_INVESTIGATION",
            "FRAUD_DEFENSE",
            "SAR_PREPARATION",
        ]
    )
    allow_institution_identity_sharing: bool = False
    allow_fuzzy_matching: bool = True
    min_fuzzy_confidence_threshold: float = 0.60
    allow_cross_border_intelligence: bool = False
    allowed_jurisdictions: list[str] = field(
        default_factory=lambda: ["eu-central-1", "us-east-1", "apac-singapore-1"]
    )


class ConsortiumPolicyEngine:
    """Evaluates consortium governance constraints prior to starting FL rounds and intelligence disclosures."""

    def __init__(
        self,
        config: ConsortiumPolicyConfig | None = None,
        intelligence_config: IntelligenceSharingPolicyConfig | None = None,
    ) -> None:
        self.config = config or ConsortiumPolicyConfig()
        self.intelligence_config = intelligence_config or IntelligenceSharingPolicyConfig()

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
        elif (
            round_epsilon > consortium.max_epsilon or round_epsilon > self.config.max_epsilon_budget
        ):
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
            reasons.append(
                f"Source region '{source_region}' is not in approved consortium jurisdictions."
            )
        if destination_region not in self.config.allowed_regions:
            reasons.append(
                f"Destination region '{destination_region}' is not in approved consortium jurisdictions."
            )

        if (
            source_region != destination_region
            and not self.config.allow_cross_border_sharing
            and not is_dp_enforced
        ):
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

    def validate_intelligence_sharing(
        self,
        consortium: Consortium,
        requesting_bank: str,
        target_banks: list[str],
        purpose: str = "AML_COLLABORATIVE_INVESTIGATION",
        requesting_jurisdiction: str | None = None,
        target_jurisdictions: dict[str, str] | None = None,
        is_operational: bool = False,
    ) -> tuple[bool, list[str], dict[str, Any]]:
        """Evaluates whether an intelligence query satisfies consortium sharing governance policies.

        Returns:
            tuple: (is_valid, reasons, disclosure_scope)
        """
        reasons: list[str] = []
        clean_requesting = requesting_bank.lower().strip()
        c_meta = getattr(consortium, "metadata", {}) or {}

        # 1. Operational Sharing Grant Enforcement (Fail-closed: No fallback to permissive defaults)
        operational_grant = c_meta.get("operational_data_sharing_grant")
        if is_operational:
            if not operational_grant or not isinstance(operational_grant, dict):
                reasons.append(
                    f"Consortium '{consortium.consortium_id}' lacks an explicit operational data-sharing grant. "
                    "Operational intelligence queries are denied by default without explicit institutional consent."
                )
                return False, reasons, {}

            if not operational_grant.get("is_active", True):
                reasons.append(
                    f"Operational data-sharing grant '{operational_grant.get('grant_id')}' is inactive or revoked."
                )
                return False, reasons, {}

            if not operational_grant.get("authority") or not str(operational_grant.get("authority")).strip():
                reasons.append(
                    f"Operational data-sharing grant '{operational_grant.get('grant_id')}' lacks valid governing authority."
                )
                return False, reasons, {}

            if not operational_grant.get("revision") or int(operational_grant.get("revision", 0)) < 1:
                reasons.append(
                    f"Operational data-sharing grant '{operational_grant.get('grant_id')}' lacks valid revision history."
                )
                return False, reasons, {}

            # Bilateral / Multilateral Institutional Consent Validation
            approvals = operational_grant.get("approvals", {})
            revoked_institutions = set(operational_grant.get("revoked_by_institutions", []))

            if clean_requesting in revoked_institutions:
                reasons.append(
                    f"Operational data-sharing consent was revoked by requesting institution '{clean_requesting}'."
                )
                return False, reasons, {}

            if clean_requesting not in approvals:
                reasons.append(
                    f"Requesting bank '{clean_requesting}' has not approved operational data-sharing grant '{operational_grant.get('grant_id')}'."
                )
                return False, reasons, {}

            for tb in target_banks:
                clean_tb = tb.lower().strip()
                if clean_tb in revoked_institutions:
                    reasons.append(
                        f"Operational data-sharing consent was revoked by counterpart institution '{clean_tb}'."
                    )
                    return False, reasons, {}
                if clean_tb not in approvals:
                    reasons.append(
                        f"Target institution '{clean_tb}' has not approved operational data-sharing grant '{operational_grant.get('grant_id')}'. "
                        f"Unilateral approval by '{clean_requesting}' does not constitute bilateral institutional consent."
                    )
                    return False, reasons, {}

        sharing_policy = operational_grant if (is_operational and operational_grant) else c_meta.get("intelligence_sharing_policy", {})

        # 2. Consortium active status
        from app.domain.consortium_governance import ConsortiumStatus

        if consortium.status != ConsortiumStatus.ACTIVE:
            reasons.append(
                f"Consortium '{consortium.consortium_id}' is in non-active status '{consortium.status.value}'. Intelligence queries are prohibited."
            )

        # 3. Requesting bank membership and eviction check (Immediate Revocation Enforcement)
        if clean_requesting in consortium.removed_members:
            reasons.append(
                f"Institution '{clean_requesting}' has been evicted/revoked from consortium '{consortium.consortium_id}'."
            )
        elif clean_requesting not in consortium.members:
            reasons.append(
                f"Institution '{clean_requesting}' is not an active member of consortium '{consortium.consortium_id}'."
            )

        # 4. Purpose authorization (Authoritative per-consortium policy or default treaty, Deny by default)
        allowed_purposes = (
            sharing_policy.get("allowed_purposes")
            if (sharing_policy and "allowed_purposes" in sharing_policy)
            else (
                None if is_operational else self.intelligence_config.allowed_purposes
            )
        )
        if not allowed_purposes or purpose not in allowed_purposes:
            reasons.append(
                f"Intelligence query purpose '{purpose}' is not authorized under consortium governance policy. Permitted: {allowed_purposes or []}. Denied by default."
            )

        # 5. Target banks membership and eviction check
        for tb in target_banks:
            clean_tb = tb.lower().strip()
            if clean_tb in consortium.removed_members:
                reasons.append(
                    f"Target institution '{clean_tb}' has been evicted/revoked from consortium '{consortium.consortium_id}'."
                )
            elif clean_tb not in consortium.members:
                reasons.append(
                    f"Target institution '{clean_tb}' is not an active member of consortium '{consortium.consortium_id}'."
                )

        # 6. Cross-border sovereignty checks (Mandatory for operational queries; no silent region skipping)
        inst_jurisdictions = (
            sharing_policy.get("institution_jurisdictions")
            or c_meta.get("institution_jurisdictions")
            or {}
        )
        allowed_jurisdictions = (
            sharing_policy.get("allowed_jurisdictions")
            or self.intelligence_config.allowed_jurisdictions
        )
        allow_cross_border = (
            sharing_policy.get("allow_cross_border_intelligence")
            if "allow_cross_border_intelligence" in sharing_policy
            else (
                sharing_policy.get("allow_cross_border_sharing")
                if "allow_cross_border_sharing" in sharing_policy
                else (
                    sharing_policy.get("allow_cross_border")
                    if "allow_cross_border" in sharing_policy
                    else self.intelligence_config.allow_cross_border_intelligence
                )
            )
        )

        has_jurisdiction_context = bool(
            is_operational
            or requesting_jurisdiction
            or target_jurisdictions
            or inst_jurisdictions
            or sharing_policy.get("enforce_jurisdiction", False)
            or ("allow_cross_border_sharing" in sharing_policy and not sharing_policy["allow_cross_border_sharing"])
            or ("allow_cross_border_intelligence" in sharing_policy and not sharing_policy["allow_cross_border_intelligence"])
            or ("allow_cross_border" in sharing_policy and not sharing_policy["allow_cross_border"])
        )

        if has_jurisdiction_context:
            req_jur = requesting_jurisdiction or inst_jurisdictions.get(clean_requesting)
            if not req_jur:
                reasons.append(
                    f"Authoritative jurisdiction for requesting institution '{clean_requesting}' is not registered and was not supplied. Denied by default."
                )
            elif req_jur not in allowed_jurisdictions:
                reasons.append(
                    f"Requesting institution operates in unapproved jurisdiction '{req_jur}' (Allowed: {allowed_jurisdictions})."
                )

            for tb in target_banks:
                clean_tb = tb.lower().strip()
                tb_jur = (target_jurisdictions or {}).get(clean_tb) or inst_jurisdictions.get(clean_tb)
                if not tb_jur:
                    reasons.append(
                        f"Authoritative jurisdiction for target institution '{clean_tb}' is not registered and was not supplied. Denied by default."
                    )
                elif tb_jur not in allowed_jurisdictions:
                    reasons.append(
                        f"Target institution '{clean_tb}' operates in unapproved jurisdiction '{tb_jur}' (Allowed: {allowed_jurisdictions})."
                    )
                elif req_jur and tb_jur != req_jur and not allow_cross_border:
                    reasons.append(
                        f"Cross-border intelligence disclosure between '{req_jur}' and '{tb_jur}' is prohibited without explicit consortium sovereignty treaty."
                    )

        # 6. Disclosure scope determination (Per-consortium governance overrides)
        allow_identities = bool(
            sharing_policy.get("allow_institution_identity_sharing")
            if "allow_institution_identity_sharing" in sharing_policy
            else (
                c_meta.get("allow_institution_identity_sharing")
                or self.intelligence_config.allow_institution_identity_sharing
            )
        )
        allow_fuzzy = bool(
            sharing_policy.get("allow_fuzzy_matching")
            if "allow_fuzzy_matching" in sharing_policy
            else self.intelligence_config.allow_fuzzy_matching
        )
        min_fuzzy_thresh = float(
            sharing_policy.get("min_fuzzy_confidence_threshold")
            if "min_fuzzy_confidence_threshold" in sharing_policy
            else self.intelligence_config.min_fuzzy_confidence_threshold
        )

        disclosure_scope: dict[str, Any] = {
            "allow_institution_identity": allow_identities,
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS"
            if allow_identities
            else "ANONYMIZED_SUMMARY",
            "allow_fuzzy_matching": allow_fuzzy,
            "min_fuzzy_confidence_threshold": min_fuzzy_thresh,
        }

        is_valid = len(reasons) == 0
        return is_valid, reasons, disclosure_scope
