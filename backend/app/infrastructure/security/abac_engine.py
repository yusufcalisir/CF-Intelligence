"""Dynamic Attribute-Based Access Control (ABAC) Policy Engine.

Evaluates Subject Attributes (bank_id, roles, clearance_level, shift_hours, approval_tier)
against Resource Attributes (bank_id, amount, classification, severity) and Environment
Attributes (current_time) to enforce granular compliance policies.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.security.oidc_authenticator import UserClaims

logger = logging.getLogger(__name__)


@dataclass
class ABACResource:
    """Attributes of the target resource being accessed."""

    resource_type: str  # alert, case, transaction, model, intelligence
    resource_id: str
    bank_id: str
    amount: float = 0.0
    classification_level: int = 1
    severity: str = "low"


@dataclass
class ABACEvaluationResult:
    """Decision output of an ABAC policy evaluation."""

    allowed: bool
    policy_name: str
    reason: str
    evaluated_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
    )


class ABACEngine:
    """Evaluates dynamic ABAC policies for multi-tenant banking compliance."""

    def evaluate_access(
        self,
        user: UserClaims,
        resource: ABACResource,
        action: str = "read",
        current_hour_override: int | None = None,
        client_ip: str | None = None,
    ) -> ABACEvaluationResult:
        """Evaluate all active ABAC rules for a given user action on a resource."""
        # 1. Super-admin override rule
        if "super_admin" in user.roles or "compliance_auditor" in user.roles:
            return ABACEvaluationResult(
                allowed=True,
                policy_name="RULE-SUPERADMIN-OVERRIDE",
                reason=f"Role '{user.roles[0]}' bypasses tenant restrictions.",
            )

        # 2. Multi-tenant Bank Isolation Rule
        if (
            resource.bank_id
            and resource.bank_id != "global"
            and user.bank_id != resource.bank_id
            and "cross_bank_investigator" not in user.roles
        ):
            return ABACEvaluationResult(
                allowed=False,
                policy_name="RULE-TENANT-ISOLATION",
                reason=f"Tenant Isolation Violation: User bank '{user.bank_id}' cannot access resource from '{resource.bank_id}'.",
            )

        # 2.5 IP Subnet Range Restriction Rule
        if client_ip and getattr(user, "allowed_ip_subnets", None):
            try:
                ip_obj = ipaddress.ip_address(client_ip)
                ip_allowed = False
                for subnet_str in user.allowed_ip_subnets:
                    if subnet_str in ("0.0.0.0/0", "*"):
                        ip_allowed = True
                        break
                    net = ipaddress.ip_network(subnet_str, strict=False)
                    if ip_obj in net:
                        ip_allowed = True
                        break
                if not ip_allowed:
                    return ABACEvaluationResult(
                        allowed=False,
                        policy_name="RULE-IP-RANGE-RESTRICTION",
                        reason=f"IP Range Restriction: Client IP '{client_ip}' is outside allowed subnets ({user.allowed_ip_subnets}).",
                    )
            except Exception as err:
                logger.warning("IP subnet evaluation exception: %s", err)
                return ABACEvaluationResult(
                    allowed=False,
                    policy_name="RULE-IP-RANGE-RESTRICTION",
                    reason=f"IP Range Restriction: Malformed or unparseable client IP '{client_ip}' ({err}).",
                )

        # 3. Shift Hours Window Constraint Rule
        if user.shift_hours and "-" in user.shift_hours and user.shift_hours != "00:00-24:00":
            try:
                start_h, end_h = [int(p.split(":")[0]) for p in user.shift_hours.split("-")]
                cur_h = (
                    current_hour_override
                    if current_hour_override is not None
                    else time.gmtime().tm_hour
                )
                if not (start_h <= cur_h <= end_h):
                    return ABACEvaluationResult(
                        allowed=False,
                        policy_name="RULE-SHIFT-HOURS-RESTRICTION",
                        reason=f"Shift Hours Restriction: Access at {cur_h}:00 is outside active shift window ({user.shift_hours}).",
                    )
            except Exception as err:
                logger.warning("Shift hours parse exception: %s", err)
                return ABACEvaluationResult(
                    allowed=False,
                    policy_name="RULE-SHIFT-HOURS-RESTRICTION",
                    reason=f"Shift Hours Restriction: Unable to evaluate shift window '{user.shift_hours}' ({err}).",
                )

        # 4. Approval Tier Limit for Write/Approve/Export actions
        if (
            action in ("approve", "write", "export", "override")
            and resource.amount > 0
            and resource.amount > user.approval_tier
        ):
            return ABACEvaluationResult(
                allowed=False,
                policy_name="RULE-APPROVAL-TIER-EXCEEDED",
                reason=f"Approval Tier Exceeded: Resource amount ${resource.amount:,.2f} exceeds user approval limit (${user.approval_tier:,.2f}).",
            )

        # 5. Security Clearance Level Rule
        if resource.classification_level > user.clearance_level:
            return ABACEvaluationResult(
                allowed=False,
                policy_name="RULE-CLEARANCE-LEVEL-INSUFFICIENT",
                reason=f"Insufficient Clearance: Resource level {resource.classification_level} exceeds user clearance level {user.clearance_level}.",
            )

        return ABACEvaluationResult(
            allowed=True,
            policy_name="RULE-ALL-POLICIES-PASSED",
            reason=f"ABAC Policy Check Passed: Access granted for {action} on {resource.resource_type}:{resource.resource_id}.",
        )
