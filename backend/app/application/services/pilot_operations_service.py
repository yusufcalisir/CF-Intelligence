"""Pilot Operations Service — Governed Two-Bank Pilot Lifecycle Management.

Adheres strictly to .agents/AGENTS.md:
- Full lifecycle: DRAFT -> PREFLIGHT -> AWAITING_APPROVAL -> AUTHORIZED -> ACTIVE -> PAUSED -> COMPLETED
- Terminal outcomes: ABORTED, FAILED
- Multi-party independent authorization: Bank A, Bank B, and Operator approvals required.
- Stale-approval rejection: any scope/config revision invalidates previous approvals.
- Kill switch: immediate PAUSE and ABORT failing closed on all new queries.
- Operational resilience & auditability: all events recorded in ImmutableAuditChain.
- Durable persistence via DurablePilotStore.
"""

from __future__ import annotations

import datetime
import logging
import os
import threading
import urllib.request
from datetime import UTC
from typing import Any

from app.application.schemas.pilot_operations import (
    PilotPreflightRequest,
    PilotProvisionRequest,
    PilotTelemetrySummary,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import ConsortiumStatus
from app.domain.institutional_identity import InstitutionalVerificationRegistry
from app.domain.pilot_operations import (
    InvalidPilotTransitionError,
    PilotApprovalRecord,
    PilotAuthorizationError,
    PilotEntity,
    PilotExecutionReceipt,
    PilotExpiredError,
    PilotOperationalConfig,
    PilotQuotaExceededError,
    PilotStatus,
    PreflightCheckResult,
    canonical_pilot_approval_bytes,
)
from app.domain.pilot_operations import (
    PilotGovernanceError as PilotGovernanceError,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_pilot_store import DurablePilotStore

logger = logging.getLogger(__name__)


class PilotOperationsService:
    """Orchestrates controlled two-bank pilot operations lifecycle and execution gating."""

    _instance: PilotOperationsService | None = None

    @classmethod
    def get_instance(cls) -> PilotOperationsService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(
        self,
        store: DurablePilotStore | None = None,
        consortium_service: ConsortiumGovernanceService | None = None,
        institutional_registry: InstitutionalVerificationRegistry | None = None,
        pilot_store: DurablePilotStore | None = None,
    ) -> None:
        self.store = store or pilot_store or DurablePilotStore.get_instance()
        self.consortium_service = consortium_service or ConsortiumGovernanceService()
        self.institutional_registry = institutional_registry or InstitutionalVerificationRegistry()
        self._audit_chain = ImmutableAuditChain.get_instance()
        self._lock = threading.RLock()

    def _probe_node_health(self, url: str | None, expected_type: str = "worker") -> tuple[bool, str]:
        """Probe actual HTTP readiness of a topology worker or coordinator."""
        if not url:
            return True, "NOT_CONFIGURED"
        clean_url = url.rstrip("/")
        probe_path = f"{clean_url}/worker/health" if expected_type == "worker" else f"{clean_url}/health"
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(probe_path, method="GET")
            with opener.open(req, timeout=0.8) as resp:
                if resp.status == 200:
                    return True, f"ONLINE (status={resp.status})"
                return False, f"UNHEALTHY (status={resp.status})"
        except Exception as exc:
            return False, f"OFFLINE ({exc})"

    def provision_pilot(
        self,
        req: PilotProvisionRequest | None = None,
        principal: AuthenticatedPrincipal | None = None,
        pilot_id: str | None = None,
        consortium_id: str | None = None,
        participating_institutions: list[str] | None = None,
        allowed_purposes: list[str] | None = None,
        allowed_jurisdictions: list[str] | None = None,
        approved_synthetic_datasets: list[str] | None = None,
        max_queries_total: int = 50,
        max_queries_per_institution: int = 25,
        duration_days: int = 14,
        creator_actor: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Provision a new two-bank pilot in DRAFT state."""
        if req is None:
            if not pilot_id or not consortium_id:
                raise ValueError("Both pilot_id and consortium_id are mandatory to provision a pilot.")
            req = PilotProvisionRequest(
                pilot_id=pilot_id,
                consortium_id=consortium_id,
                participating_institutions=participating_institutions or ["bank_a", "bank_b"],
                allowed_purposes=allowed_purposes or ["AML_INVESTIGATION"],
                allowed_jurisdictions=allowed_jurisdictions or ["EU"],
                approved_synthetic_datasets=approved_synthetic_datasets or [],
                max_queries_total=max_queries_total,
                max_queries_per_institution=max_queries_per_institution,
                duration_days=duration_days,
            )
        clean_pilot_id = req.pilot_id.strip()
        clean_consortium_id = req.consortium_id.lower().strip()

        # Check existing
        existing = self.store.get_pilot(clean_pilot_id)
        if existing is not None:
            raise ValueError(f"Pilot with ID '{clean_pilot_id}' already exists.")

        # Validate consortium exists and is active
        consortium = self.consortium_service.get_consortium(clean_consortium_id)
        if not consortium:
            raise ValueError(f"Consortium '{clean_consortium_id}' does not exist.")
        if consortium.status != ConsortiumStatus.ACTIVE:
            raise ValueError(
                f"Consortium '{clean_consortium_id}' is in non-active status '{consortium.status.value}'."
            )

        # Validate institutions belong to consortium
        clean_insts = [b.lower().strip() for b in req.participating_institutions]
        if len(clean_insts) < 2:
            raise ValueError("A two-bank pilot requires at least 2 participating institutions.")
        for inst in clean_insts:
            if inst not in consortium.members or inst in consortium.removed_members:
                raise ValueError(
                    f"Institution '{inst}' is not an active member of consortium '{clean_consortium_id}'."
                )

        # Bind operational grant from consortium metadata if not specified
        c_meta = getattr(consortium, "metadata", {}) or {}
        op_grant = c_meta.get("operational_data_sharing_grant") or {}
        grant_id = req.operational_grant_id or op_grant.get("grant_id", f"grant_{clean_consortium_id}")
        grant_revision = int(op_grant.get("revision", 1))
        grant_scope_digest = str(op_grant.get("scope_digest", ""))

        now = datetime.datetime.now(UTC)
        starts_at = req.starts_at or now.isoformat()
        expires_at = req.expires_at or (now + datetime.timedelta(days=14)).isoformat()

        config = PilotOperationalConfig(
            pilot_id=clean_pilot_id,
            revision=1,
            consortium_id=clean_consortium_id,
            participating_institutions=clean_insts,
            allowed_purposes=req.allowed_purposes,
            allowed_jurisdictions=req.allowed_jurisdictions,
            approved_datasets=req.approved_datasets,
            authorized_operators=req.authorized_operators or [principal.principal_id] if principal else ["operator_admin"],
            operational_grant_id=grant_id,
            grant_revision=grant_revision,
            grant_scope_digest=grant_scope_digest,
            topology=req.topology,
            starts_at=starts_at,
            expires_at=expires_at,
            query_quota_total=req.query_quota_total,
            query_quota_per_institution=req.query_quota_per_institution,
        )

        entity = PilotEntity(
            config=config,
            status=PilotStatus.DRAFT,
            status_reason="Provisioned in DRAFT state. Preflight checks required before approval.",
        )

        actor_id = principal.principal_id if principal else "system"
        evt = self._audit_chain.append_event(
            event_type="PILOT_PROVISIONED",
            actor_id=actor_id,
            action=f"provision_pilot:{clean_pilot_id}",
            resource_id=f"pilot:{clean_pilot_id}",
            details={
                "pilot_id": clean_pilot_id,
                "consortium_id": clean_consortium_id,
                "participating_institutions": clean_insts,
                "scope_digest": entity.scope_digest,
                "query_quota_total": config.query_quota_total,
            },
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.info("Provisioned pilot %s in consortium %s", clean_pilot_id, clean_consortium_id)
        return entity

    def run_preflight(
        self,
        pilot_id: str,
        principal: AuthenticatedPrincipal | None = None,
        preflight_req: PilotPreflightRequest | None = None,
        operator_actor: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Execute preflight readiness verification for a provisioned pilot."""
        entity = self._get_required_pilot(pilot_id)
        if entity.status not in (PilotStatus.DRAFT, PilotStatus.PREFLIGHT, PilotStatus.AWAITING_APPROVAL):
            raise InvalidPilotTransitionError(
                f"Cannot run preflight for pilot '{pilot_id}' in status '{entity.status.value}'."
            )

        if entity.status == PilotStatus.DRAFT:
            entity.transition_to(
                PilotStatus.PREFLIGHT,
                "Executing preflight readiness probes.",
            )

        checks: list[PreflightCheckResult] = []

        # 1. Consortium existence & status
        consortium = self.consortium_service.get_consortium(entity.config.consortium_id)
        c_active = consortium is not None and consortium.status == ConsortiumStatus.ACTIVE
        checks.append(PreflightCheckResult(
            check_name="CONSORTIUM_ACTIVE",
            passed=c_active,
            details=f"Consortium '{entity.config.consortium_id}' status: {consortium.status.value if consortium else 'NOT_FOUND'}",
        ))

        # 2. Member eligibility
        insts = entity.config.participating_institutions
        members_ok = True
        members_detail = []
        for inst in insts:
            if not consortium or inst not in consortium.members or inst in consortium.removed_members:
                members_ok = False
                members_detail.append(f"{inst}: NOT_ACTIVE")
            else:
                members_detail.append(f"{inst}: ACTIVE")
        checks.append(PreflightCheckResult(
            check_name="INSTITUTION_MEMBERSHIP",
            passed=members_ok,
            details="; ".join(members_detail),
        ))

        # 3. Operational Data-Sharing Grant
        c_meta = getattr(consortium, "metadata", {}) or {}
        op_grant = c_meta.get("operational_data_sharing_grant") or {}
        grant_ok = bool(
            op_grant
            and op_grant.get("is_active", True)
            and int(op_grant.get("revision", 0)) >= 1
            and not set(op_grant.get("revoked_by_institutions", [])) & set(insts)
        )
        checks.append(PreflightCheckResult(
            check_name="OPERATIONAL_GRANT_VALID",
            passed=grant_ok,
            details=f"Grant ID: {op_grant.get('grant_id', 'NONE')}, active: {op_grant.get('is_active')}, rev: {op_grant.get('revision')}",
        ))

        # 4. Institutional Verification Keys
        keys_ok = True
        keys_detail = []
        for inst in insts:
            has_k = self.institutional_registry.has_key(inst)
            keys_detail.append(f"{inst}: {'ENROLLED' if has_k else 'MISSING'}")
            if not has_k:
                keys_ok = False
        checks.append(PreflightCheckResult(
            check_name="INSTITUTIONAL_KEYS_ENROLLED",
            passed=keys_ok,
            details="; ".join(keys_detail),
        ))

        # 5. Purpose & Jurisdiction Allowlist
        purposes_ok = len(entity.config.allowed_purposes) > 0 and len(entity.config.allowed_jurisdictions) > 0
        checks.append(PreflightCheckResult(
            check_name="PURPOSE_AND_JURISDICTION_CONFIGURED",
            passed=purposes_ok,
            details=f"Purposes: {entity.config.allowed_purposes}, Jurisdictions: {entity.config.allowed_jurisdictions}",
        ))

        # 6. Quotas
        quota_ok = entity.config.query_quota_total > 0 and entity.config.query_quota_per_institution > 0
        checks.append(PreflightCheckResult(
            check_name="QUERY_QUOTA_BOUNDED",
            passed=quota_ok,
            details=f"Total: {entity.config.query_quota_total}, Per-Inst: {entity.config.query_quota_per_institution}",
        ))

        # 7. Live Infrastructure Readiness & Topology Reachability Probes
        topo = entity.config.topology or {}
        bank_a_url = topo.get("bank_a_worker_url") or topo.get("bank_a_url") or os.getenv("CFI_WORKER_A_URL") or os.getenv("CFI_BANK_A_URL")
        bank_b_url = topo.get("bank_b_worker_url") or topo.get("bank_b_url") or os.getenv("CFI_WORKER_B_URL") or os.getenv("CFI_BANK_B_URL")
        coord_url = topo.get("coordinator_url") or os.getenv("CFI_COORDINATOR_URL")

        if bank_a_url:
            a_ok, a_det = self._probe_node_health(bank_a_url, "worker")
            checks.append(PreflightCheckResult(
                check_name="BANK_A_WORKER_REACHABLE",
                passed=a_ok,
                details=f"Worker A ({bank_a_url}): {a_det}",
            ))
        if bank_b_url:
            b_ok, b_det = self._probe_node_health(bank_b_url, "worker")
            checks.append(PreflightCheckResult(
                check_name="BANK_B_WORKER_REACHABLE",
                passed=b_ok,
                details=f"Worker B ({bank_b_url}): {b_det}",
            ))
        if coord_url:
            c_ok, c_det = self._probe_node_health(coord_url, "coordinator")
            checks.append(PreflightCheckResult(
                check_name="COORDINATOR_REACHABLE",
                passed=c_ok,
                details=f"Coordinator ({coord_url}): {c_det}",
            ))

        entity.preflight_checks = checks
        all_passed = all(c.passed for c in checks)

        if all_passed:
            entity.transition_to(
                PilotStatus.AWAITING_APPROVAL,
                "Preflight readiness verified. Awaiting bilateral institutional and operator approvals.",
            )
        else:
            failed_names = [c.check_name for c in checks if not c.passed]
            entity.transition_to(
                PilotStatus.FAILED,
                f"Preflight verification failed for: {', '.join(failed_names)}.",
            )

        actor_id = principal.principal_id if principal else "system"
        evt = self._audit_chain.append_event(
            event_type="PILOT_PREFLIGHT_EXECUTED",
            actor_id=actor_id,
            action=f"preflight_pilot:{pilot_id}",
            resource_id=f"pilot:{pilot_id}",
            details={
                "pilot_id": pilot_id,
                "all_passed": all_passed,
                "checks": [{"name": c.check_name, "passed": c.passed} for c in checks],
            },
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        return entity

    def approve_pilot(
        self,
        pilot_id: str,
        approving_institution: str,
        principal: AuthenticatedPrincipal | str | None = None,
        notes: str = "",
        signature: str = "",
        approver_actor: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Record independent institutional or operator approval for a pilot."""
        clean_inst = approving_institution.lower().strip()
        is_operator = clean_inst in ("operator", "pilot_operator", "admin", "consortium")

        if not isinstance(principal, AuthenticatedPrincipal) or not principal.is_authenticated:
            raise PermissionError(
                "Authentication required: Independent institutional approval requires an authenticated principal."
            )

        caller_tenant = (principal.tenant_id or "").lower().strip()
        if not is_operator:
            # Institution approval: strictly require caller_tenant == clean_inst
            # An operator or admin cannot manufacture a bank's institutional approval!
            if caller_tenant != clean_inst:
                raise PermissionError(
                    f"Cross-bank approval rejected: Principal from institution '{caller_tenant}' "
                    f"cannot approve pilot on behalf of '{approving_institution}'. "
                    "Consortium operators and platform administrators cannot manufacture institutional approvals."
                )
            inst_roles = {"admin", "super_admin", "compliance", "compliance_officer", "security_officer", "institutional_approver"}
            roles = getattr(principal, "roles", []) or []
            if not principal.is_privileged and not any(r in inst_roles for r in roles):
                raise PermissionError(
                    f"Principal '{principal.principal_id}' lacks required institutional approval role for '{approving_institution}'."
                )
        else:
            gov_roles = {"admin", "super_admin", "ml_lead", "operator", "pilot_operator", "security_officer", "compliance", "system_admin"}
            roles = getattr(principal, "roles", []) or []
            if not principal.is_privileged and not any(r in gov_roles for r in roles):
                raise PermissionError(
                    f"Principal '{principal.principal_id}' lacks operator authority to grant OPERATOR pilot approval."
                )

        entity = self._get_required_pilot(pilot_id)
        if entity.status not in (PilotStatus.AWAITING_APPROVAL, PilotStatus.PREFLIGHT, PilotStatus.DRAFT):
            raise InvalidPilotTransitionError(
                f"Cannot record approval for pilot '{pilot_id}' in status '{entity.status.value}'. Must be in AWAITING_APPROVAL."
            )

        target_inst_key = "OPERATOR" if is_operator else clean_inst
        if not is_operator and clean_inst not in [b.lower().strip() for b in entity.config.participating_institutions]:
            raise PermissionError(
                f"Institution '{clean_inst}' is not a participating bank of pilot '{pilot_id}'."
            )

        challenge_bytes = canonical_pilot_approval_bytes(
            pilot_id=entity.config.pilot_id,
            revision=entity.config.revision,
            scope_digest=entity.config.compute_scope_digest(),
            approver_institution=target_inst_key,
            approver_id=principal.principal_id,
        )

        has_key = self.institutional_registry.has_key(clean_inst)
        if signature:
            verified = self.institutional_registry.verify_signature(clean_inst, signature, challenge_bytes)
            if not verified:
                raise PilotAuthorizationError(
                    f"Cryptographic signature verification failed for institution '{clean_inst}'. "
                    "Approval signature is invalid or does not match enrolled public key."
                )
        elif has_key and not is_operator:
            raise PilotAuthorizationError(
                f"Missing cryptographic signature: Institution '{clean_inst}' has an enrolled Ed25519 verification key. "
                "Approval must be cryptographically signed."
            )

        approver_id = principal.principal_id
        approver_role = "operator" if is_operator else (principal.roles[0] if principal.roles else "institutional_approver")

        approval_rec = PilotApprovalRecord(
            approver_id=approver_id,
            approver_institution=target_inst_key,
            approver_role=approver_role,
            scope_digest=entity.config.compute_scope_digest(),
            signature=signature,
            notes=notes,
        )

        entity.record_approval(approval_rec)

        evt = self._audit_chain.append_event(
            event_type="PILOT_APPROVAL_RECORDED",
            actor_id=approver_id,
            action=f"approve_pilot:{pilot_id}:{target_inst_key}",
            resource_id=f"pilot:{pilot_id}",
            details={
                "pilot_id": pilot_id,
                "approver_institution": target_inst_key,
                "approver_id": approver_id,
                "scope_digest": approval_rec.scope_digest,
                "is_fully_approved": entity.is_fully_approved(),
                "new_status": entity.status.value,
            },
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.info(
            "Recorded approval for pilot %s from %s. Fully approved: %s, Status: %s",
            pilot_id,
            target_inst_key,
            entity.is_fully_approved(),
            entity.status.value,
        )
        return entity

    def activate_pilot(
        self,
        pilot_id: str,
        principal: AuthenticatedPrincipal | str | None = None,
        operator_actor: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Activate an AUTHORIZED pilot for operational matching queries."""
        entity = self._get_required_pilot(pilot_id)

        auth_principal: AuthenticatedPrincipal | None = None
        if isinstance(principal, AuthenticatedPrincipal):
            auth_principal = principal
        elif isinstance(principal, str):
            operator_actor = principal

        if auth_principal:
            gov_roles = {"admin", "super_admin", "ml_lead", "operator", "pilot_operator", "security_officer", "compliance", "system_admin"}
            roles = getattr(auth_principal, "roles", []) or []
            if not auth_principal.is_privileged and not any(r in gov_roles for r in roles):
                raise PermissionError(
                    f"Principal '{auth_principal.principal_id}' lacks operator authority to activate pilot."
                )

        if entity.status == PilotStatus.ACTIVE:
            logger.info("Pilot '%s' is already ACTIVE. Idempotent activation succeeded.", pilot_id)
            return entity

        # Check approvals first before status
        if not entity.is_fully_approved():
            is_ok, missing = entity.has_all_required_approvals()
            raise PilotAuthorizationError(
                f"Pilot '{pilot_id}' cannot be activated: missing approval from {missing}. "
                "All participating institutions and operator must approve before activation."
            )

        if entity.status != PilotStatus.AUTHORIZED:
            raise InvalidPilotTransitionError(
                f"Cannot activate pilot '{pilot_id}' in status '{entity.status.value}'. "
                "Pilot must be fully approved and in AUTHORIZED status."
            )

        # Revalidate underlying consortium grant
        consortium = self.consortium_service.get_consortium(entity.config.consortium_id)
        if not consortium or consortium.status != ConsortiumStatus.ACTIVE:
            raise PilotAuthorizationError(
                f"Consortium '{entity.config.consortium_id}' is not active. Cannot activate pilot."
            )

        entity.transition_to(
            PilotStatus.ACTIVE,
            "Pilot successfully activated by authorized operator. Operational matching permitted.",
        )

        actor_id = auth_principal.principal_id if auth_principal else (operator_actor or "operator")
        evt = self._audit_chain.append_event(
            event_type="PILOT_ACTIVATED",
            actor_id=actor_id,
            action=f"activate_pilot:{pilot_id}",
            resource_id=f"pilot:{pilot_id}",
            details={
                "pilot_id": pilot_id,
                "activated_at": entity.activated_at,
                "scope_digest": entity.scope_digest,
                "participating_institutions": entity.config.participating_institutions,
            },
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.info("Activated pilot %s for operational matching", pilot_id)
        return entity

    def pause_pilot(
        self,
        pilot_id: str,
        reason: str | None = None,
        principal: AuthenticatedPrincipal | str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Effective kill switch: pause an active pilot immediately."""
        entity = self._get_required_pilot(pilot_id)
        if entity.status != PilotStatus.ACTIVE:
            raise InvalidPilotTransitionError(
                f"Cannot pause pilot '{pilot_id}' in non-active status '{entity.status.value}'."
            )

        if isinstance(reason, str) and isinstance(principal, str):
            if any(k in reason for k in ("operator", "admin", "user", "bank_")):
                actual_actor = reason
                actual_reason = principal
            else:
                actual_reason = reason
                actual_actor = principal
        else:
            actual_reason = reason or "Paused by operator"
            actual_actor = (
                principal.principal_id
                if isinstance(principal, AuthenticatedPrincipal)
                else (principal if principal else "operator")
            )

        entity.transition_to(PilotStatus.PAUSED, f"Paused by operator: {actual_reason}")

        actor_id = actual_actor
        evt = self._audit_chain.append_event(
            event_type="PILOT_PAUSED",
            actor_id=actor_id,
            action=f"pause_pilot:{pilot_id}",
            resource_id=f"pilot:{pilot_id}",
            details={"pilot_id": pilot_id, "reason": actual_reason},
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.warning("PAUSED pilot %s: %s", pilot_id, actual_reason)
        return entity

    def resume_pilot(
        self,
        pilot_id: str,
        principal: AuthenticatedPrincipal | str | None = None,
        reason: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Resume a paused pilot after revalidating infrastructure health and authority."""
        entity = self._get_required_pilot(pilot_id)
        if entity.status != PilotStatus.PAUSED:
            raise InvalidPilotTransitionError(
                f"Cannot resume pilot '{pilot_id}' in status '{entity.status.value}'. Must be PAUSED."
            )

        # Re-check authority: Consortium and grant still valid
        consortium = self.consortium_service.get_consortium(entity.config.consortium_id)
        if not consortium or consortium.status != ConsortiumStatus.ACTIVE:
            raise PilotAuthorizationError("Underlying consortium is no longer active.")

        c_meta = getattr(consortium, "metadata", {}) or {}
        op_grant = c_meta.get("operational_data_sharing_grant") or {}
        if not op_grant or not op_grant.get("is_active", True):
            raise PilotAuthorizationError("Underlying operational grant is no longer active.")

        revoked = set(op_grant.get("revoked_by_institutions", []))
        if revoked & set(entity.config.participating_institutions):
            raise PilotAuthorizationError(
                f"One or more participating institutions revoked consent: {sorted(revoked)}. Resumption denied."
            )

        # Re-check not expired
        now = datetime.datetime.now(UTC)
        if entity.config.expires_at:
            exp_dt = datetime.datetime.fromisoformat(entity.config.expires_at)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=UTC)
            if now >= exp_dt:
                raise PilotExpiredError(f"Pilot '{pilot_id}' has expired. Cannot resume.")

        # Re-check quota remaining
        if entity.queries_executed >= entity.config.query_quota_total:
            raise PilotQuotaExceededError("Pilot quota already exhausted. Cannot resume.")

        # Live infrastructure readiness verification before resumption
        topo = entity.config.topology or {}
        bank_a_url = topo.get("bank_a_worker_url") or topo.get("bank_a_url") or os.getenv("CFI_WORKER_A_URL") or os.getenv("CFI_BANK_A_URL")
        bank_b_url = topo.get("bank_b_worker_url") or topo.get("bank_b_url") or os.getenv("CFI_WORKER_B_URL") or os.getenv("CFI_BANK_B_URL")
        coord_url = topo.get("coordinator_url") or os.getenv("CFI_COORDINATOR_URL")

        if bank_a_url:
            ok, det = self._probe_node_health(bank_a_url, "worker")
            if not ok:
                raise PilotGovernanceError(f"Cannot resume pilot '{pilot_id}': Bank A worker at '{bank_a_url}' is unreachable ({det}).")
        if bank_b_url:
            ok, det = self._probe_node_health(bank_b_url, "worker")
            if not ok:
                raise PilotGovernanceError(f"Cannot resume pilot '{pilot_id}': Bank B worker at '{bank_b_url}' is unreachable ({det}).")
        if coord_url:
            ok, det = self._probe_node_health(coord_url, "coordinator")
            if not ok:
                raise PilotGovernanceError(f"Cannot resume pilot '{pilot_id}': Coordinator at '{coord_url}' is unreachable ({det}).")

        entity.transition_to(PilotStatus.ACTIVE, "Resumed after health and authorization revalidation.")

        actor_id = principal.principal_id if isinstance(principal, AuthenticatedPrincipal) else (principal if principal else "operator")
        evt = self._audit_chain.append_event(
            event_type="PILOT_RESUMED",
            actor_id=actor_id,
            action=f"resume_pilot:{pilot_id}",
            resource_id=f"pilot:{pilot_id}",
            details={"pilot_id": pilot_id},
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.info("Resumed pilot %s", pilot_id)
        return entity

    def abort_pilot(
        self,
        pilot_id: str,
        reason: str,
        principal: AuthenticatedPrincipal | str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Emergency kill switch: terminate pilot permanently in ABORTED state."""
        entity = self._get_required_pilot(pilot_id)
        entity.transition_to(PilotStatus.ABORTED, f"Aborted by operator: {reason}")

        actor_id = principal.principal_id if isinstance(principal, AuthenticatedPrincipal) else (principal if principal else "operator")
        evt = self._audit_chain.append_event(
            event_type="PILOT_ABORTED",
            actor_id=actor_id,
            action=f"abort_pilot:{pilot_id}",
            resource_id=f"pilot:{pilot_id}",
            details={"pilot_id": pilot_id, "reason": reason},
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.warning("ABORTED pilot %s: %s", pilot_id, reason)
        return entity

    def complete_pilot(
        self,
        pilot_id: str,
        principal: AuthenticatedPrincipal | str | None = None,
        reason: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Mark a pilot as successfully COMPLETED."""
        entity = self._get_required_pilot(pilot_id)
        completion_msg = reason or f"Pilot successfully completed with {entity.queries_executed} queries executed."
        entity.transition_to(
            PilotStatus.COMPLETED,
            completion_msg,
        )

        actor_id = principal.principal_id if isinstance(principal, AuthenticatedPrincipal) else (principal if principal else "operator")
        evt = self._audit_chain.append_event(
            event_type="PILOT_COMPLETED",
            actor_id=actor_id,
            action=f"complete_pilot:{pilot_id}",
            resource_id=f"pilot:{pilot_id}",
            details={
                "pilot_id": pilot_id,
                "queries_executed": entity.queries_executed,
                "queries_denied": entity.queries_denied,
            },
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.info("Completed pilot %s", pilot_id)
        return entity

    def validate_pilot_query(
        self,
        pilot_id: str,
        requesting_bank_id: str | None = None,
        target_bank_id: str | None = None,
        purpose: str = "",
        requesting_bank: str | None = None,
        target_bank: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Authoritatively validates execution gating for a matching query under this pilot."""
        requesting_bank_id = requesting_bank_id or requesting_bank or ""
        target_bank_id = target_bank_id or target_bank or ""
        entity = self._get_required_pilot(pilot_id)
        entity.validate_executable(
            requesting_bank_id=requesting_bank_id,
            target_bank_id=target_bank_id,
            purpose=purpose,
        )

        # Verify underlying consortium operational grant is still active
        consortium = self.consortium_service.get_consortium(entity.config.consortium_id)
        if not consortium or consortium.status != ConsortiumStatus.ACTIVE:
            raise PilotAuthorizationError(
                f"Underlying consortium '{entity.config.consortium_id}' is not ACTIVE. Query rejected."
            )
        c_meta = getattr(consortium, "metadata", {}) or {}
        op_grant = c_meta.get("operational_data_sharing_grant") or {}
        if not op_grant or not op_grant.get("is_active", True):
            raise PilotAuthorizationError("Underlying operational data sharing grant is inactive or revoked.")

        revoked = set(op_grant.get("revoked_by_institutions", []))
        if requesting_bank_id.lower().strip() in revoked:
            raise PilotAuthorizationError(f"Requesting bank '{requesting_bank_id}' revoked operational consent.")
        if target_bank_id.lower().strip() in revoked:
            raise PilotAuthorizationError(f"Counterpart bank '{target_bank_id}' revoked operational consent.")

        return entity

    def find_active_or_scoped_pilot(
        self,
        requesting_bank: str,
        target_bank: str,
        purpose: str,
        pilot_id: str | None = None,
    ) -> PilotEntity | None:
        """Find governing pilot for the institutional pair and purpose.

        If pilot_id is supplied, retrieves that exact pilot.
        Otherwise, inspects active pilots for institutional match.
        """
        clean_req = requesting_bank.lower().strip()
        clean_target = target_bank.lower().strip()

        if pilot_id:
            entity = self.store.get_pilot(pilot_id.strip())
            if not entity:
                raise PilotGovernanceError(f"Governing pilot '{pilot_id}' not found.")
            return entity

        active_pilots = self.store.get_active_pilots()
        for p in active_pilots:
            p_insts = {b.lower().strip() for b in p.config.participating_institutions}
            if clean_req in p_insts and clean_target in p_insts and purpose in p.config.allowed_purposes:
                return p
        return None

    def revalidate_pilot_active(
        self,
        pilot_id: str,
        requesting_bank: str,
        target_bank: str,
        purpose: str,
    ) -> PilotEntity:
        """Revalidate effective pilot authorization immediately before authoritative signal admission."""
        entity = self._get_required_pilot(pilot_id)
        if entity.status != PilotStatus.ACTIVE:
            raise PilotGovernanceError(
                f"Kill switch activated: Pilot '{pilot_id}' is in non-active status '{entity.status.value}'. "
                f"Reason: {entity.status_reason}. Authoritative signal admission rejected."
            )
        if entity.has_expired():
            raise PilotExpiredError(
                f"Kill switch activated: Pilot '{pilot_id}' expired. Authoritative signal admission rejected."
            )
        entity.validate_executable(requesting_bank, target_bank, purpose)

        consortium = self.consortium_service.get_consortium(entity.config.consortium_id)
        if not consortium or consortium.status != ConsortiumStatus.ACTIVE:
            raise PilotAuthorizationError(
                f"Underlying consortium '{entity.config.consortium_id}' is not ACTIVE. Admission rejected."
            )
        c_meta = getattr(consortium, "metadata", {}) or {}
        op_grant = c_meta.get("operational_data_sharing_grant") or {}
        if not op_grant or not op_grant.get("is_active", True):
            raise PilotAuthorizationError("Underlying operational data sharing grant is inactive or revoked.")

        revoked = set(op_grant.get("revoked_by_institutions", []))
        if requesting_bank.lower().strip() in revoked:
            raise PilotAuthorizationError(f"Requesting bank '{requesting_bank}' revoked operational consent.")
        if target_bank.lower().strip() in revoked:
            raise PilotAuthorizationError(f"Counterpart bank '{target_bank}' revoked operational consent.")

        return entity

    def reserve_query_slot(
        self,
        pilot_id: str,
        requesting_bank: str,
        target_bank: str,
        purpose: str,
        reservation_id: str | None = None,
    ) -> tuple[PilotEntity, str]:
        """Atomically reserve execution slot in durable store."""
        import uuid
        res_id = reservation_id or f"res_{uuid.uuid4().hex[:12]}"
        return self.store.reserve_query_slot(
            pilot_id=pilot_id,
            requesting_bank_id=requesting_bank,
            target_bank_id=target_bank,
            purpose=purpose,
            reservation_id=res_id,
        )

    def commit_query_reservation(
        self,
        reservation_id: str,
        pilot_id: str,
        receipt_id: str,
        latency_ms: float,
        match_confirmed: bool | None,
        signal_id: str | None = None,
        case_id: str | None = None,
    ) -> tuple[PilotEntity, PilotExecutionReceipt]:
        """Commit an in-flight reservation upon successful PSI execution and result admission."""
        return self.store.commit_query_reservation(
            reservation_id=reservation_id,
            pilot_id=pilot_id,
            receipt_id=receipt_id,
            latency_ms=latency_ms,
            match_confirmed=match_confirmed,
            signal_id=signal_id,
            case_id=case_id,
        )

    def release_query_reservation(
        self,
        reservation_id: str,
        pilot_id: str,
        reason: str = "FAILED",
        is_denied: bool = True,
    ) -> None:
        """Release reservation upon error or mid-flight abort."""
        self.store.release_query_reservation(
            reservation_id=reservation_id,
            pilot_id=pilot_id,
            reason=reason,
            is_denied=is_denied,
        )

    def record_query_outcome(
        self,
        pilot_id: str,
        requesting_bank_id: str = "",
        target_bank_id: str = "",
        purpose: str = "",
        status: str = "COMPLETED",
        latency_ms: float = 0.0,
        match_confirmed: bool | None = None,
        receipt_id: str | None = None,
        reason: str | None = None,
        success: bool | None = None,
        is_match: bool | None = None,
        requesting_bank: str | None = None,
        signal_id: str | None = None,
        case_id: str | None = None,
        **kwargs: Any,
    ) -> PilotExecutionReceipt:
        """Record query latency, counts, and receipt in persistent pilot store."""
        req_bank = requesting_bank_id or requesting_bank or "bank_a"
        target_bank = target_bank_id or kwargs.get("target_bank") or "bank_b"
        query_purpose = purpose or kwargs.get("query_purpose") or "AML_INVESTIGATION"
        outcome_status = status
        if success is not None:
            outcome_status = "COMPLETED" if success else "FAILED"
        if is_match is not None and match_confirmed is None:
            match_confirmed = is_match

        with self._lock:
            entity = self._get_required_pilot(pilot_id)
            receipt = entity.record_query_outcome(
                requesting_bank_id=req_bank,
                target_bank_id=target_bank,
                purpose=query_purpose,
                status=outcome_status,
                latency_ms=latency_ms,
                match_confirmed=match_confirmed,
                receipt_id=receipt_id,
                reason=reason,
                signal_id=signal_id,
                case_id=case_id,
            )
            self.store.save_pilot(entity)
            return receipt

    def get_pilot(self, pilot_id: str) -> PilotEntity | None:
        """Fetch pilot by ID."""
        return self.store.get_pilot(pilot_id)

    def list_pilots(self) -> list[PilotEntity]:
        """List all pilots in store."""
        return self.store.list_pilots()

    def get_active_pilots(self) -> list[PilotEntity]:
        """List currently active pilots."""
        return self.store.get_active_pilots()

    def export_audit_dossier(self, pilot_id: str) -> dict[str, Any]:
        """Export durable audit dossier with enriched immutable audit events."""
        entity = self._get_required_pilot(pilot_id)
        dossier = entity.export_audit_dossier()
        audit_events = []
        for eid in entity.audit_event_ids:
            evt = self._audit_chain.get_event(eid) if hasattr(self._audit_chain, "get_event") else None
            if evt:
                audit_events.append({
                    "event_id": getattr(evt, "event_id", eid),
                    "event_type": getattr(evt, "event_type", ""),
                    "actor_id": getattr(evt, "actor_id", getattr(evt, "actor", "")),
                    "action": getattr(evt, "action", ""),
                    "resource_id": getattr(evt, "resource_id", getattr(evt, "target_id", "")),
                    "timestamp": getattr(evt, "timestamp", ""),
                    "digest": getattr(evt, "digest", getattr(evt, "curr_hash", "")),
                    "event_digest": getattr(evt, "digest", getattr(evt, "curr_hash", "")),
                })
        dossier["audit_trail"] = audit_events
        dossier["audit_events"] = audit_events
        dossier["execution_receipts"] = entity.execution_receipts
        return dossier

    def get_telemetry_summary(self, pilot_id: str) -> PilotTelemetrySummary:
        """Compile live operational telemetry summary for pilot visibility."""
        entity = self._get_required_pilot(pilot_id)
        avg_lat = (
            sum(entity.latencies_ms) / len(entity.latencies_ms)
            if entity.latencies_ms
            else 0.0
        )
        sorted_lats = sorted(entity.latencies_ms)
        p95_lat = (
            sorted_lats[int(len(sorted_lats) * 0.95)]
            if sorted_lats
            else 0.0
        )

        return PilotTelemetrySummary(
            pilot_id=entity.config.pilot_id,
            status=entity.status.value,
            queries_executed=entity.queries_executed,
            queries_denied=entity.queries_denied,
            queries_total=entity.queries_executed + entity.queries_denied,
            queries_successful=entity.queries_executed,
            queries_failed=entity.queries_denied,
            matches_found=entity.matches_count,
            queries_by_institution=entity.institution_query_counts,
            query_quota_total=entity.config.query_quota_total,
            quota_remaining=max(0, entity.config.query_quota_total - entity.queries_executed),
            mean_latency_ms=round(avg_lat, 2),
            p95_latency_ms=round(p95_lat, 2),
            institution_query_counts=entity.institution_query_counts,
            last_operation_at=entity.updated_at if entity.queries_executed > 0 else None,
            bilateral_authorization_valid=entity.is_fully_approved() and entity.status == PilotStatus.ACTIVE,
        )

    def _get_required_pilot(self, pilot_id: str) -> PilotEntity:
        entity = self.store.get_pilot(pilot_id)
        if not entity:
            raise ValueError(f"Pilot '{pilot_id}' not found.")
        return entity
