"""Cross-Bank Intelligence Orchestration Service.

Orchestrates authorized, privacy-preserving cross-bank matching, signal exchange,
and institution-scoped risk context for AML investigations under strict consortium governance.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from datetime import UTC
from typing import Any, cast

from app.application.schemas.cross_bank_intelligence import (
    CrossBankEvidenceAttachResponse,
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
    DisclosureMode,
    MatchCategory,
    SanitizedRiskLevel,
)
from app.application.services.case_service import (
    CaseManagementService,
    CaseNotFoundError,
    EvidenceRegistryService,
    TerminalCaseImmutableError,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.entity_resolution import EntityResolutionService
from app.application.services.isolated_matching_service import (
    CrossBankMatchingCoordinator,
)
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    ConsortiumStatus,
)
from app.domain.consortium_policy import ConsortiumPolicyEngine
from app.domain.institutional_identity import (
    InstitutionalVerificationRegistry,
    canonical_counterpart_evidence_bytes,
    canonical_evaluated_result_bytes,
)
from app.domain.value_objects_investigation import (
    calculate_jaccard_similarity,
)
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal

logger = logging.getLogger(__name__)

PERMITTED_INTELLIGENCE_ROLES: frozenset[str] = frozenset(
    {
        "analyst",
        "investigator",
        "compliance",
        "compliance_auditor",
        "admin",
        "ml_lead",
        "cross_bank_investigator",
        "super_admin",
    }
)


class CrossBankIntelligenceService:
    """Manages authorized cross-bank intelligence queries and evidence binding."""

    def __init__(
        self,
        consortium_service: ConsortiumGovernanceService | None = None,
        entity_service: EntityResolutionService | None = None,
        case_service: CaseManagementService | None = None,
        evidence_service: EvidenceRegistryService | None = None,
        policy_engine: ConsortiumPolicyEngine | None = None,
        matching_coordinator: CrossBankMatchingCoordinator | None = None,
        institutional_registry: InstitutionalVerificationRegistry | None = None,
    ) -> None:
        self.consortium_service = consortium_service or ConsortiumGovernanceService()
        self.entity_service = entity_service or EntityResolutionService()
        self.case_service = case_service or CaseManagementService()
        self.evidence_service = evidence_service or EvidenceRegistryService(
            case_service=self.case_service
        )
        self.policy_engine = policy_engine or ConsortiumPolicyEngine()
        self.matching_coordinator = matching_coordinator or CrossBankMatchingCoordinator(
            consortium_service=self.consortium_service,
            policy_engine=self.policy_engine,
        )
        self.institutional_registry = institutional_registry or InstitutionalVerificationRegistry()
        self._signal_store = RedisStore("authoritative_signals")
        self._synthetic_entity_store = RedisStore("synthetic_entities")
        self._research_case_evidence_store = RedisStore("research_case_evidence")
        self._admitted_receipts_store = RedisStore("admitted_operational_psi_receipts")
        self._admitted_receipt_ids: set[str] = set()
        self._lock = threading.RLock()

    def _authenticate_principal(
        self,
        principal: AuthenticatedPrincipal | None = None,
        auth_token: str | None = None,
    ) -> AuthenticatedPrincipal:
        """Resolve and authoritatively validate principal, enforcing mandatory authentication."""
        if principal is not None:
            if not principal.is_authenticated or principal.principal_id == "anonymous":
                raise PermissionError(
                    "Unauthenticated principal rejected: Institutional credentials required."
                )
            return principal

        if not auth_token or not auth_token.strip():
            raise PermissionError(
                "Authentication required: Missing Bearer token or institutional principal."
            )

        clean_token = auth_token.strip()
        if clean_token.lower().startswith("bearer "):
            clean_token = clean_token[7:].strip()

        from app.infrastructure.security.auth_service import AuthenticationService

        auth_svc = AuthenticationService.get_instance()
        is_valid, claims, err = auth_svc.verify_access_token(clean_token)
        if not is_valid or not claims:
            raise PermissionError(f"Authentication token verification failed: {err}")

        username = str(claims.get("username") or claims.get("sub") or "")
        tenant_id = claims.get("bank_id")
        raw_roles = claims.get("roles")
        if isinstance(raw_roles, str):
            roles = [raw_roles]
        elif isinstance(raw_roles, (list, tuple, set, frozenset)):
            roles = [r for r in raw_roles if isinstance(r, str)]
        else:
            roles = []

        return AuthenticatedPrincipal(
            principal_id=username,
            tenant_id=tenant_id,
            roles=roles,
            auth_method="bearer_platform",
            is_authenticated=True,
            clearance_level=int(claims.get("clearance_level", 1)),
            token_claims=claims,
        )

    def execute_two_bank_private_matching(
        self,
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None = None,
        principal: AuthenticatedPrincipal | None = None,
        actor: str | None = None,
    ) -> CrossBankIntelligenceSignal:
        """Execute authentic two-bank private matching across isolated institutional bank nodes.

        Enforces institutional tenant isolation, permitted analyst roles, actor integrity,
        and requesting-bank entity ownership.
        """
        clean_requesting = req.requesting_bank_id.lower().strip()
        auth_principal = self._authenticate_principal(principal=principal, auth_token=auth_token)
        caller_bank = (auth_principal.tenant_id or "").lower().strip()

        if not auth_principal.is_privileged and caller_bank != clean_requesting:
            raise PermissionError(
                f"Institutional impersonation rejected: Authenticated principal belongs to '{caller_bank}', "
                f"cannot execute matching on behalf of institution '{req.requesting_bank_id}'."
            )

        if not any(r in PERMITTED_INTELLIGENCE_ROLES for r in auth_principal.roles):
            raise PermissionError(
                f"Principal '{auth_principal.principal_id}' lacks permitted intelligence query role. "
                f"Permitted roles: {sorted(PERMITTED_INTELLIGENCE_ROLES)}."
            )

        if actor and actor.lower().strip() != auth_principal.principal_id.lower().strip():
            raise PermissionError(
                f"Caller-supplied actor identity ('{actor}') does not match authenticated principal ('{auth_principal.principal_id}')."
            )

        audited_actor = auth_principal.principal_id

        # Verify entity ownership if entity is tracked in the registry
        if req.query_entity_id and hasattr(self, "entity_service") and self.entity_service:
            try:
                ent = self.entity_service.get_entity(req.query_entity_id)
                if ent and ent.bank_id.lower().strip() != clean_requesting:
                    raise PermissionError(
                        f"Requesting bank '{clean_requesting}' does not own entity '{req.query_entity_id}'."
                    )
            except Exception as exc:
                if isinstance(exc, PermissionError):
                    raise

        return self.matching_coordinator.execute_two_bank_private_matching(
            req=req,
            auth_token=auth_token,
            actor=audited_actor,
        )

    def _execute_verified_operational_psi(
        self,
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        """Route authorized intelligence query to established PSI operational worker topology."""
        clean_requesting = req.requesting_bank_id.lower().strip()
        clean_consortium_id = req.consortium_id.lower().strip()

        # 1. Authoritative Consortium Governance & Active Membership
        consortium = self.consortium_service.get_consortium(clean_consortium_id)
        if not consortium:
            raise ConsortiumSecurityError(
                f"Consortium '{clean_consortium_id}' does not exist or has not been established."
            )
        if consortium.status != ConsortiumStatus.ACTIVE:
            raise ConsortiumSecurityError(
                f"Consortium '{clean_consortium_id}' is in non-active status '{consortium.status.value}'. "
                "Cross-bank intelligence queries are prohibited."
            )
        if clean_requesting in consortium.removed_members:
            raise ConsortiumSecurityError(
                f"Institution '{clean_requesting}' has been revoked or evicted from consortium '{clean_consortium_id}'."
            )
        if clean_requesting not in consortium.members:
            raise ConsortiumSecurityError(
                f"Institution '{clean_requesting}' is not an active member of consortium '{clean_consortium_id}'."
            )

        # 2. Counterpart institution eligibility
        if not req.target_bank_id:
            raise ValueError(
                "Mandatory target_bank_id missing: Operational established PSI requires an explicit counterpart institution."
            )
        clean_target = req.target_bank_id.lower().strip()
        if clean_target == clean_requesting:
            raise ValueError(
                "Requesting institution and target counterpart institution cannot be identical."
            )
        if clean_target in consortium.removed_members:
            raise ConsortiumSecurityError(
                f"Target institution '{clean_target}' has been revoked from consortium '{clean_consortium_id}'."
            )
        if clean_target not in consortium.members:
            raise ConsortiumSecurityError(
                f"Target institution '{clean_target}' is not an active member of consortium '{clean_consortium_id}'."
            )

        # 3. Enforce Strict Bilateral Operational Data-Sharing Grant & Jurisdiction
        is_valid_policy, policy_reasons, disclosure_scope = (
            self.policy_engine.validate_intelligence_sharing(
                consortium=consortium,
                requesting_bank=clean_requesting,
                target_banks=[clean_target],
                purpose=req.purpose,
                is_operational=True,
            )
        )
        if not is_valid_policy:
            raise ConsortiumSecurityError(
                f"Consortium operational policy rejected query: {'; '.join(policy_reasons)}"
            )

        # 4. Local Entity Verification
        if not req.query_entity_id or not req.query_entity_id.strip():
            raise ValueError(
                "Mandatory query_entity_id missing: Cross-bank queries must reference a verified local entity belonging to the requesting institution."
            )

        val = self.entity_service._entities.get(req.query_entity_id)
        if not val:
            raise ValueError(
                f"Local entity '{req.query_entity_id}' not found in institution '{clean_requesting}' database. "
                "Arbitrary privacy-hash or unverified entity probing is prohibited."
            )

        from app.application.services.entity_resolution import _dict_to_entity

        source_entity = _dict_to_entity(val)
        if source_entity.bank_id.lower().strip() != clean_requesting:
            raise PermissionError(
                f"Cross-tenant access violation: Entity '{req.query_entity_id}' belongs to "
                f"'{source_entity.bank_id}', not requesting institution '{clean_requesting}'."
            )

        # 5. Case Verification if case_id specified
        req_case_id = getattr(req, "case_id", None)
        if req_case_id:
            case = self.case_service.get_case(req_case_id)
            if not case:
                raise ValueError(f"Investigation case '{req_case_id}' not found.")
            case_bank = getattr(case, "bank_id", None)
            if case_bank and str(case_bank).lower().strip() != clean_requesting:
                raise PermissionError(
                    f"Cross-tenant access violation: Case '{req_case_id}' belongs to '{case_bank}', not '{clean_requesting}'."
                )
            if not case.is_open:
                raise TerminalCaseImmutableError(
                    f"Invalid query: Case '{req_case_id}' is finalized in terminal state."
                )

        # 6. Route to Established PSI Coordinator Topology with Pilot Governance Enforcement
        from app.application.services.pilot_operations_service import (
            PilotOperationsService,
        )

        pilot_svc = PilotOperationsService.get_instance()
        governing_pilot = pilot_svc.find_active_or_scoped_pilot(
            requesting_bank=clean_requesting,
            target_bank=clean_target,
            purpose=req.purpose,
            pilot_id=req.pilot_id,
        )

        active_pilot_id: str | None = None
        reservation_id: str | None = None
        if governing_pilot is not None:
            active_pilot_id = governing_pilot.config.pilot_id
            pilot_svc.validate_pilot_query(
                pilot_id=active_pilot_id,
                requesting_bank=clean_requesting,
                target_bank=clean_target,
                purpose=req.purpose,
            )
            governing_pilot, reservation_id = pilot_svc.reserve_query_slot(
                pilot_id=active_pilot_id,
                requesting_bank=clean_requesting,
                target_bank=clean_target,
                purpose=req.purpose,
            )

        from app.config import get_settings

        settings = get_settings()
        coordinator_url = getattr(settings, "cfi_coordinator_url", None) or os.getenv(
            "CFI_COORDINATOR_URL"
        )

        est_req = req.model_copy(
            update={
                "target_bank_id": clean_target,
                "target_bank_ids": [clean_target],
                "execution_mode": "VERIFIED_OPERATIONAL",
                "use_established_psi": True,
            }
        )

        resolved_auth_token = auth_token or ""
        if not resolved_auth_token:
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()
            user = auth_svc.get_user(audited_actor)
            if user:
                resolved_auth_token = auth_svc.create_token_bundle(user).access_token

        t0 = time.perf_counter()
        try:
            if coordinator_url:
                from app.application.services.isolated_matching_service import (
                    InstitutionalCoordinatorClient,
                )

                coord_client = InstitutionalCoordinatorClient(coordinator_url)
                try:
                    coord_client.health_check()
                except Exception as exc:
                    raise ConsortiumSecurityError(
                        f"Operational established PSI coordinator at '{coordinator_url}' is unreachable or offline: {exc}. Failing closed."
                    ) from exc
                signal = coord_client.execute_match(
                    req=est_req,
                    auth_token=resolved_auth_token,
                    actor=audited_actor,
                )

                # Kill-switch revalidation immediately before authoritative result admission
                if active_pilot_id and pilot_svc:
                    pilot_svc.revalidate_pilot_active(
                        pilot_id=active_pilot_id,
                        requesting_bank=clean_requesting,
                        target_bank=clean_target,
                        purpose=req.purpose,
                    )

                # Enforce Verifiable Result-Admission Contract before local storage
                record = self._admit_and_verify_operational_signal(
                    signal=signal,
                    expected_req=est_req,
                    audited_actor=audited_actor,
                )
                resolved_receipt_id = (
                    getattr(signal, "receipt_id", None)
                    or (signal.provenance.get("receipt_id") if isinstance(getattr(signal, "provenance", None), dict) else None)
                    or ""
                )
                if active_pilot_id:
                    record["pilot_id"] = active_pilot_id
                    if governing_pilot:
                        record["pilot_revision"] = governing_pilot.config.revision
                        record["pilot_scope_digest"] = governing_pilot.scope_digest
                    record["reservation_id"] = reservation_id
                    record["target_bank_id"] = clean_target
                    record["requesting_bank_id"] = clean_requesting
                    record["query_entity_id"] = getattr(est_req, "query_entity_id", getattr(est_req, "entity_id", ""))
                    record["receipt_id"] = resolved_receipt_id
                self._signal_store.set(signal.signal_id, record)

                if pilot_svc and active_pilot_id and reservation_id:
                    duration_ms = (time.perf_counter() - t0) * 1000.0
                    pilot_svc.commit_query_reservation(
                        reservation_id=reservation_id,
                        pilot_id=active_pilot_id,
                        receipt_id=resolved_receipt_id,
                        latency_ms=duration_ms,
                        match_confirmed=bool(getattr(signal, "match_confirmed", False)),
                        signal_id=signal.signal_id,
                        case_id=req_case_id,
                    )
                    reservation_id = None
                return signal

            if self.matching_coordinator is not None:
                try:
                    self.matching_coordinator.get_worker(clean_requesting)
                    self.matching_coordinator.get_worker(clean_target)
                    signal = self.matching_coordinator.execute_two_bank_private_matching(
                        req=est_req,
                        auth_token=resolved_auth_token,
                        actor=audited_actor,
                    )

                    # Kill-switch revalidation immediately before authoritative result admission
                    if active_pilot_id and pilot_svc:
                        pilot_svc.revalidate_pilot_active(
                            pilot_id=active_pilot_id,
                            requesting_bank=clean_requesting,
                            target_bank=clean_target,
                            purpose=req.purpose,
                        )

                    record = self._admit_and_verify_operational_signal(
                        signal=signal,
                        expected_req=est_req,
                        audited_actor=audited_actor,
                    )
                    resolved_receipt_id = (
                        getattr(signal, "receipt_id", None)
                        or (signal.provenance.get("receipt_id") if isinstance(getattr(signal, "provenance", None), dict) else None)
                        or ""
                    )
                    if active_pilot_id:
                        record["pilot_id"] = active_pilot_id
                        if governing_pilot:
                            record["pilot_revision"] = governing_pilot.config.revision
                            record["pilot_scope_digest"] = governing_pilot.scope_digest
                        record["reservation_id"] = reservation_id
                        record["target_bank_id"] = clean_target
                        record["requesting_bank_id"] = clean_requesting
                        record["query_entity_id"] = getattr(est_req, "query_entity_id", getattr(est_req, "entity_id", ""))
                        record["receipt_id"] = resolved_receipt_id
                    self._signal_store.set(signal.signal_id, record)

                    if pilot_svc and active_pilot_id and reservation_id:
                        duration_ms = (time.perf_counter() - t0) * 1000.0
                        pilot_svc.commit_query_reservation(
                            reservation_id=reservation_id,
                            pilot_id=active_pilot_id,
                            receipt_id=resolved_receipt_id,
                            latency_ms=duration_ms,
                            match_confirmed=bool(getattr(signal, "match_confirmed", False)),
                            signal_id=signal.signal_id,
                            case_id=req_case_id,
                        )
                        reservation_id = None
                    return signal
                except ConsortiumSecurityError:
                    raise

            raise ConsortiumSecurityError(
                "Established PSI operational worker topology is unavailable. "
                "Neither CFI_COORDINATOR_URL nor registered operational worker nodes are configured. "
                "Operation fails closed to prevent unverified prototype or simulated matching."
            )
        except Exception as exc:
            if pilot_svc and active_pilot_id and reservation_id:
                pilot_svc.release_query_reservation(
                    reservation_id=reservation_id,
                    pilot_id=active_pilot_id,
                    reason=str(exc),
                )
            raise

    def _admit_and_verify_operational_signal(
        self,
        signal: CrossBankIntelligenceSignal,
        expected_req: CrossBankIntelligenceQueryRequest,
        audited_actor: str,
    ) -> dict[str, Any]:
        """Cryptographically and governably admit an operational match signal.

        Enforces:
        1. Query Correlation: Requesting bank, target bank, entity ID, and consortium ID match expected_req.
        2. Bilateral Governance & Consent: Consortium is active, member banks are enrolled, and bilateral grant terms match.
        3. Identity & Entity Binding: Ticket ID, Job ID, and Receipt ID consistently bind the queried entity.
        4. Established PSI Protocol: Must be OPENMINED-PSI-2.0.6-ECDH with REVIEWED_ESTABLISHED_PSI_VERIFIED assurance.
        5. Participant Cryptographic Evidence:
           - Worker A signature over receipt and transcript digest matches Bank A's institutional key.
           - Worker B ticket authorization signature matches Bank B's enrolled verification key.
           - Coordinator signature over receipt matches coordinator key.
        6. Anti-Replay & Freshness: Receipt ID cannot be replayed, executed_at must be within 300s.
        7. Fail-Closed on any mismatch or invalid cryptographic signature.
        """
        clean_requesting = expected_req.requesting_bank_id.lower().strip()
        clean_target = (expected_req.target_bank_id or "").lower().strip()
        clean_consortium_id = expected_req.consortium_id.lower().strip()

        # 1. Query Correlation
        if signal.consortium_id.lower().strip() != clean_consortium_id:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Consortium ID mismatch (expected '{clean_consortium_id}', got '{signal.consortium_id}')."
            )
        if signal.requesting_bank_id.lower().strip() != clean_requesting:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Requesting bank mismatch (expected '{clean_requesting}', got '{signal.requesting_bank_id}')."
            )
        if signal.query_entity_id != expected_req.query_entity_id:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Query entity ID mismatch (expected '{expected_req.query_entity_id}', got '{signal.query_entity_id}')."
            )

        provenance = signal.provenance or {}
        prov_target = str(provenance.get("target_bank_id", "")).lower().strip()
        if prov_target != clean_target:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Target counterpart bank mismatch (expected '{clean_target}', got '{prov_target}')."
            )

        # 2. Execution Evidence Presence
        receipt = provenance.get("receipt")
        ticket = provenance.get("ticket")
        if not receipt or not isinstance(receipt, dict):
            raise ConsortiumSecurityError(
                "Adoption rejection: Missing or corrupt authentic worker execution receipt."
            )
        if not ticket or not isinstance(ticket, dict):
            raise ConsortiumSecurityError(
                "Adoption rejection: Missing or corrupt operational authorization ticket."
            )

        # 3. Bilateral Governance & Active Consent
        consortium = self.consortium_service.get_consortium(clean_consortium_id)
        if not consortium:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Consortium '{clean_consortium_id}' does not exist."
            )
        if consortium.status.value.upper() != "ACTIVE":
            raise ConsortiumSecurityError(
                f"Adoption rejection: Consortium '{clean_consortium_id}' is not active."
            )
        if clean_requesting in consortium.removed_members or clean_target in consortium.removed_members:
            raise ConsortiumSecurityError(
                "Adoption rejection: One or more participating banks have been evicted from the consortium."
            )
        if clean_requesting not in consortium.members or clean_target not in consortium.members:
            raise ConsortiumSecurityError(
                "Adoption rejection: Participating banks are not active members of the consortium."
            )

        operational_grant = consortium.metadata.get("operational_data_sharing_grant") or {}
        grant_id = operational_grant.get("grant_id", f"grant_{clean_consortium_id}")
        grant_revision = int(operational_grant.get("revision", 1))
        grant_scope_digest = operational_grant.get("scope_digest", "")
        if not grant_scope_digest:
            from app.application.services.consortium_service import (
                compute_operational_grant_scope_digest,
            )
            grant_scope_digest = compute_operational_grant_scope_digest(
                grant_id=grant_id,
                revision=grant_revision,
                participating_banks=list(operational_grant.get("participating_banks", [clean_requesting, clean_target])),
                allowed_purposes=list(operational_grant.get("allowed_purposes", [expected_req.purpose])),
                institution_jurisdictions=dict(operational_grant.get("institution_jurisdictions", {clean_requesting: "EU", clean_target: "EU"})),
                allowed_jurisdictions=list(operational_grant.get("allowed_jurisdictions", ["EU", "US"])),
                allow_cross_border=bool(operational_grant.get("allow_cross_border", False)),
                allow_institution_identity_sharing=bool(operational_grant.get("allow_institution_identity_sharing", False)),
            )

        if ticket.get("grant_id") != grant_id:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Ticket grant ID mismatch (expected '{grant_id}', got '{ticket.get('grant_id')}')."
            )
        if int(ticket.get("grant_revision", 0)) != grant_revision:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Ticket grant revision mismatch (expected {grant_revision}, got {ticket.get('grant_revision')})."
            )
        if ticket.get("grant_scope_digest") != grant_scope_digest:
            raise ConsortiumSecurityError(
                "Adoption rejection: Ticket grant scope digest mismatch with active consortium grant."
            )

        is_grant_active = bool(operational_grant.get("is_active", False) and operational_grant.get("status") != "REVOKED")
        revoked_institutions = set(operational_grant.get("revoked_by_institutions") or [])
        if not is_grant_active or clean_requesting in revoked_institutions or clean_target in revoked_institutions:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Bilateral operational consent for grant '{grant_id}' has been revoked."
            )

        # 4. Identity & Session Consistency Binding
        prov_job_id = provenance.get("job_id")
        prov_ticket_id = provenance.get("ticket_id")
        prov_receipt_id = provenance.get("receipt_id")
        if not prov_job_id or not prov_ticket_id or not prov_receipt_id:
            raise ConsortiumSecurityError("Adoption rejection: Missing session binding identifiers in provenance.")

        if ticket.get("ticket_id") != prov_ticket_id:
            raise ConsortiumSecurityError("Adoption rejection: Ticket ID mismatch between ticket and provenance.")
        if ticket.get("job_id") != prov_job_id or receipt.get("job_id") != prov_job_id:
            raise ConsortiumSecurityError("Adoption rejection: Job ID mismatch across session artifacts.")
        if receipt.get("receipt_id") != prov_receipt_id:
            raise ConsortiumSecurityError("Adoption rejection: Receipt ID mismatch between receipt and provenance.")
        if ticket.get("query_scope") != f"entity:{expected_req.query_entity_id}":
            raise ConsortiumSecurityError(
                f"Adoption rejection: Ticket query scope '{ticket.get('query_scope')}' does not match entity '{expected_req.query_entity_id}'."
            )
        if (
            receipt.get("query_entity_id") != expected_req.query_entity_id
            or signal.query_entity_id != expected_req.query_entity_id
            or (provenance.get("query_entity_id") is not None and provenance.get("query_entity_id") != expected_req.query_entity_id)
        ):
            raise ConsortiumSecurityError(
                "Adoption rejection: Query entity identifier mismatch between signal/receipt/provenance and expected request."
            )

        # 5. Established PSI Protocol & Classification Invariants
        if receipt.get("protocol") != "OPENMINED-PSI-2.0.6-ECDH" or provenance.get("protocol") != "OPENMINED-PSI-2.0.6-ECDH":
            raise ConsortiumSecurityError(
                f"Adoption rejection: Prohibited protocol '{receipt.get('protocol')}'. Established PSI requires 'OPENMINED-PSI-2.0.6-ECDH'."
            )
        if receipt.get("match_confirmed") != signal.match_confirmed:
            raise ConsortiumSecurityError("Adoption rejection: Match confirmation status mismatch between signal and receipt.")
        if receipt.get("match_category") != signal.match_category:
            raise ConsortiumSecurityError("Adoption rejection: Match category mismatch between signal and receipt.")
        if abs(float(receipt.get("match_confidence", 0.0)) - float(signal.match_confidence)) > 1e-6:
            raise ConsortiumSecurityError("Adoption rejection: Match confidence mismatch between signal and receipt.")
        if signal.execution_mode != "VERIFIED_OPERATIONAL":
            raise ConsortiumSecurityError(
                f"Adoption rejection: Invalid execution mode '{signal.execution_mode}'. Operational admission requires 'VERIFIED_OPERATIONAL'."
            )
        if not signal.is_authoritative or signal.is_synthetic:
            raise ConsortiumSecurityError("Adoption rejection: Operational signal must be authoritative and non-synthetic.")
        if signal.assurance_level != "REVIEWED_ESTABLISHED_PSI_VERIFIED" or receipt.get("assurance_level") != "REVIEWED_ESTABLISHED_PSI_VERIFIED":
            raise ConsortiumSecurityError(
                f"Adoption rejection: Unverified assurance level (signal: '{signal.assurance_level}', receipt: '{receipt.get('assurance_level')}')."
            )
        allowed_attestation_statuses = {
            "ISOLATED_MULTI_PROCESS",
            "SOFTWARE_EMULATED",
            "UNAVAILABLE_NO_HARDWARE_ENCLAVE",
        }
        if signal.attestation_status not in allowed_attestation_statuses:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Elevated or unverified attestation status '{signal.attestation_status}'. Operational runtime lacks validated hardware enclave evidence."
            )

        # Cryptographically bound signal fields validation
        receipt_risk = receipt.get(
            "matched_risk_level", "UNAVAILABLE" if signal.match_confirmed else "UNOBSERVED"
        )
        if signal.sanitized_risk_level != receipt_risk:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Sanitized risk level mismatch (signal: '{signal.sanitized_risk_level}', receipt: '{receipt_risk}')."
            )

        receipt_typologies = set(receipt.get("matched_typologies") or [])
        signal_typologies = set(signal.sanitized_typology_indicators or [])
        if receipt_typologies != signal_typologies:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Typology mismatch (signal: {signal_typologies}, receipt: {receipt_typologies})."
            )

        receipt_disclosure_mode = receipt.get(
            "disclosure_mode",
            "ATTRIBUTED_INSTITUTIONS" if signal.match_confirmed else "ANONYMIZED_SUMMARY",
        )
        if signal.disclosure_mode != receipt_disclosure_mode:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Disclosure mode mismatch (signal: '{signal.disclosure_mode}', receipt: '{receipt_disclosure_mode}')."
            )

        receipt_institutions = list(receipt.get("contributing_institutions") or [])
        signal_institutions = list(signal.contributing_institutions or [])
        if signal.disclosure_mode == "ANONYMIZED_SUMMARY":
            if len(signal_institutions) > 0:
                raise ConsortiumSecurityError(
                    "Adoption rejection: Attributed institutions injected into signal under anonymous disclosure mode."
                )
            if len(receipt_institutions) > 0:
                raise ConsortiumSecurityError(
                    "Adoption rejection: Attributed institutions present in receipt under anonymous disclosure mode."
                )
        if set(receipt_institutions) != set(signal_institutions):
            raise ConsortiumSecurityError(
                f"Adoption rejection: Contributing institutions mismatch (signal: {signal_institutions}, receipt: {receipt_institutions})."
            )

        expected_count = len(receipt_institutions) if receipt_disclosure_mode == "ATTRIBUTED_INSTITUTIONS" else (1 if signal.match_confirmed else 0)
        if signal.contributing_institutions_count != expected_count:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Contributing institutions count mismatch (signal: {signal.contributing_institutions_count}, expected: {expected_count})."
            )

        # Graph Context Integrity Verification
        graph_ctx = signal.graph_context or {}
        has_correlations = graph_ctx.get("has_incident_cross_bank_correlations")
        if has_correlations is not None and bool(has_correlations) != signal.match_confirmed:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Graph context correlation forgery detected (graph correlation: {has_correlations}, match confirmed: {signal.match_confirmed})."
            )
        degree_band = graph_ctx.get("sanitized_degree_band")
        if not signal.match_confirmed and degree_band not in (None, "", "ISOLATED"):
            raise ConsortiumSecurityError(
                f"Adoption rejection: Inflated graph relationship degree '{degree_band}' for unconfirmed match."
            )
        if signal.match_confirmed and degree_band not in (None, "", "BILATERAL"):
            raise ConsortiumSecurityError(
                f"Adoption rejection: Inflated graph relationship degree '{degree_band}' beyond verified bilateral execution."
            )

        # 6. Anti-Replay & Freshness
        receipt_id = str(receipt.get("receipt_id", "")).strip()
        if not receipt_id:
            raise ConsortiumSecurityError("Adoption rejection: Empty receipt_id in execution evidence.")

        if receipt_id in self._admitted_receipt_ids or self._admitted_receipts_store.get(receipt_id):
            raise ConsortiumSecurityError(
                f"Adoption rejection: Replay attack detected. Receipt '{receipt_id}' has already been admitted."
            )

        now = datetime.datetime.now(UTC)
        executed_at_str = receipt.get("executed_at")
        if not executed_at_str:
            raise ConsortiumSecurityError("Adoption rejection: Missing executed_at timestamp in receipt.")
        executed_at = datetime.datetime.fromisoformat(executed_at_str)
        if executed_at.tzinfo is None:
            executed_at = executed_at.replace(tzinfo=UTC)
        if abs((now - executed_at).total_seconds()) > 300:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Execution evidence expired (timestamp: {executed_at_str})."
            )

        # 7. Asymmetric Cryptographic Verification of Bank B Counterpart Evidence and Bank A Evaluated Outcome
        # 7a. Bank B Counterpart Evidence Verification
        counterpart_evidence = receipt.get("counterpart_evidence") or provenance.get("counterpart_evidence")
        if not counterpart_evidence or not isinstance(counterpart_evidence, dict):
            raise ConsortiumSecurityError(
                "Adoption rejection: Missing authenticated counterpart execution evidence from target institution."
            )

        from app.application.services.isolated_matching_service import OperationAuthorizationTicket

        unsigned_ticket = OperationAuthorizationTicket(
            ticket_id=ticket["ticket_id"],
            consortium_id=ticket["consortium_id"],
            requesting_bank_id=ticket["requesting_bank_id"],
            target_bank_id=ticket["target_bank_id"],
            operation=ticket["operation"],
            purpose=ticket["purpose"],
            query_scope=ticket["query_scope"],
            grant_id=ticket["grant_id"],
            grant_revision=int(ticket["grant_revision"]),
            grant_scope_digest=ticket["grant_scope_digest"],
            job_id=ticket["job_id"],
            issued_at=ticket["issued_at"],
            expires_at=ticket["expires_at"],
            bank_approvals={},
        )
        ticket_canonical_digest = unsigned_ticket.canonical_digest()

        canonical_b_evidence = canonical_counterpart_evidence_bytes(
            version=str(counterpart_evidence.get("version", "1.0")),
            consortium_id=clean_consortium_id,
            grant_id=ticket["grant_id"],
            grant_revision=int(ticket["grant_revision"]),
            grant_scope_digest=ticket["grant_scope_digest"],
            requesting_bank_id=clean_requesting,
            target_bank_id=clean_target,
            job_id=ticket["job_id"],
            ticket_digest=ticket_canonical_digest,
            psi_server_setup_hash=str(counterpart_evidence.get("psi_server_setup_hash", "")),
            psi_response_hash=str(counterpart_evidence.get("psi_response_hash", "")),
            protocol="OPENMINED-PSI-2.0.6-ECDH",
            nonce=str(counterpart_evidence.get("nonce", "")),
            timestamp=str(counterpart_evidence.get("timestamp", "")),
        )
        expected_counterpart_evidence_digest = hashlib.sha256(canonical_b_evidence).hexdigest()
        receipt_counterpart_digest = receipt.get("counterpart_evidence_digest", "")
        if receipt_counterpart_digest and receipt_counterpart_digest != expected_counterpart_evidence_digest:
            raise ConsortiumSecurityError(
                "Adoption rejection: Counterpart evidence digest mismatch in execution receipt."
            )

        worker_b_sig = counterpart_evidence.get("worker_b_signature") or receipt.get("worker_b_signature", "")
        if not worker_b_sig:
            raise ConsortiumSecurityError(
                "Adoption rejection: Missing Bank B cryptographic signature on counterpart PSI evidence."
            )

        if not self.institutional_registry.verify_signature(clean_target, worker_b_sig, canonical_b_evidence):
            raise ConsortiumSecurityError(
                "Adoption rejection: Cryptographic verification failed for Counterpart Bank B execution evidence."
            )

        # 7b. Bank A Evaluated Outcome Verification
        canonical_a_evaluated = canonical_evaluated_result_bytes(
            version=str(receipt.get("version", "1.0")),
            receipt_id=receipt_id,
            job_id=ticket["job_id"],
            consortium_id=clean_consortium_id,
            grant_id=ticket["grant_id"],
            grant_revision=int(ticket["grant_revision"]),
            grant_scope_digest=ticket["grant_scope_digest"],
            requesting_bank_id=clean_requesting,
            target_bank_id=clean_target,
            query_entity_id=expected_req.query_entity_id,
            ticket_digest=ticket_canonical_digest,
            counterpart_evidence_digest=expected_counterpart_evidence_digest,
            match_confirmed=signal.match_confirmed,
            match_category=signal.match_category,
            match_confidence=signal.match_confidence,
            matched_risk_level=receipt_risk,
            matched_typologies=tuple(receipt.get("matched_typologies") or ()),
            disclosure_mode=receipt_disclosure_mode,
            contributing_institutions=tuple(receipt.get("contributing_institutions") or ()),
            protocol="OPENMINED-PSI-2.0.6-ECDH",
            assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
            executed_at=executed_at_str,
        )
        worker_a_sig = receipt.get("worker_a_signature", "")
        if not worker_a_sig:
            raise ConsortiumSecurityError(
                "Adoption rejection: Missing Bank A cryptographic signature on evaluated result."
            )

        if not self.institutional_registry.verify_signature(clean_requesting, worker_a_sig, canonical_a_evaluated):
            raise ConsortiumSecurityError(
                "Adoption rejection: Cryptographic verification failed for Requesting Bank A evaluated outcome signature."
            )

        # 7c. Coordinator Routing Attestation (if coordinator secret configured)
        coord_secret = (
            os.getenv("CFI_COORDINATOR_SECRET")
            or os.getenv("COORDINATOR_AUTH_SECRET")
            or getattr(self.matching_coordinator, "_coordinator_secret", "")
        )
        coord_sig = provenance.get("coordinator_signature") or receipt.get("coordinator_signature", "")
        if coord_secret and coord_sig:
            expected_coord_sig = hmac.new(
                coord_secret.encode("utf-8"),
                f"{receipt['receipt_id']}:{receipt['transcript_digest']}:{clean_consortium_id}".encode(),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected_coord_sig, coord_sig):
                raise ConsortiumSecurityError(
                    "Adoption rejection: Cryptographic verification failed for Coordinator relay execution signature."
                )

        self._admitted_receipt_ids.add(receipt_id)
        self._admitted_receipts_store.set(receipt_id, {"admitted_at": now.isoformat(), "signal_id": signal.signal_id})

        signal_dict = signal.model_dump()
        # Authoritative server-side normalization before persistence
        signal_dict["contributing_institutions_count"] = expected_count
        signal_dict["contributing_institutions"] = list(receipt.get("contributing_institutions") or [])
        signal_dict["disclosure_mode"] = receipt_disclosure_mode
        signal_dict["sanitized_risk_level"] = receipt_risk
        signal_dict["sanitized_typology_indicators"] = sorted(list(receipt_typologies))
        signal_dict["match_confirmed"] = bool(receipt.get("match_confirmed", False))
        signal_dict["match_category"] = receipt.get("match_category", "NO_MATCH")
        signal_dict["match_confidence"] = float(receipt.get("match_confidence", 0.0))
        signal_dict["assurance_level"] = receipt.get("assurance_level", "REVIEWED_ESTABLISHED_PSI_VERIFIED")
        signal_dict["execution_mode"] = "VERIFIED_OPERATIONAL"
        signal_dict["is_authoritative"] = True
        signal_dict["is_synthetic"] = False
        signal_dict["graph_context"] = {
            "has_incident_cross_bank_correlations": bool(receipt.get("match_confirmed", False)),
            "sanitized_degree_band": "BILATERAL" if receipt.get("match_confirmed", False) else "ISOLATED",
        }
        expires_at = (now + datetime.timedelta(hours=24)).isoformat()
        content_hash = hashlib.sha256(
            json.dumps(signal_dict, sort_keys=True).encode("utf-8")
        ).hexdigest()

        return {
            "signal_id": signal.signal_id,
            "signal_data": signal_dict,
            "requesting_bank_id": clean_requesting,
            "target_bank_id": clean_target,
            "consortium_id": clean_consortium_id,
            "query_entity_id": expected_req.query_entity_id,
            "grant_id": ticket["grant_id"],
            "grant_revision": ticket["grant_revision"],
            "grant_scope_digest": ticket["grant_scope_digest"],
            "ticket_id": ticket["ticket_id"],
            "job_id": ticket["job_id"],
            "receipt_id": receipt["receipt_id"],
            "issued_to_principal": audited_actor,
            "issued_at": now.isoformat(),
            "expires_at": expires_at,
            "content_hash": content_hash,
            "is_authoritative": signal.is_authoritative,
            "is_synthetic": False,
            "operational_status": "OPERATIONAL",
            "assurance_level": signal.assurance_level,
            "attached_to_cases": [],
            "receipt": receipt,
            "ticket": ticket,
            "pilot_id": getattr(expected_req, "pilot_id", None),
        }

    def query_cross_bank_intelligence(
        self,
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None = None,
        principal: AuthenticatedPrincipal | None = None,
        actor: str | None = None,
    ) -> CrossBankIntelligenceSignal:
        """Execute authorized cross-bank intelligence query under consortium policy."""
        clean_requesting = req.requesting_bank_id.lower().strip()
        clean_consortium_id = req.consortium_id.lower().strip()

        # 1. Authoritative Institutional Principal & Role Verification
        auth_principal = self._authenticate_principal(principal=principal, auth_token=auth_token)
        caller_bank = (auth_principal.tenant_id or "").lower().strip()

        if not auth_principal.is_privileged and caller_bank != clean_requesting:
            raise PermissionError(
                f"Institutional impersonation rejected: Authenticated principal belongs to '{caller_bank}', "
                f"cannot execute intelligence queries on behalf of institution '{req.requesting_bank_id}'."
            )

        if not any(r in PERMITTED_INTELLIGENCE_ROLES for r in auth_principal.roles):
            raise PermissionError(
                f"Principal '{auth_principal.principal_id}' lacks permitted intelligence query role. "
                f"Permitted roles: {sorted(PERMITTED_INTELLIGENCE_ROLES)}."
            )

        audited_actor = auth_principal.principal_id

        # 2. Hardware Enclave & Operational Mode Attestation (Fail-Closed)
        if req.require_hardware_attestation:
            raise RuntimeError(
                "Hardware attestation unavailable: Physical hardware enclave is absent in this runtime. "
                "Production hardware attestation requirement failed closed."
            )

        if req.execution_mode == "VERIFIED_OPERATIONAL":
            if not req.target_bank_id:
                raise RuntimeError(
                    "Operational privacy attestation unavailable: Target counterpart institution (target_bank_id) missing, "
                    "and hardware enclaves (Intel SGX) or distributed multi-party cryptographic PSI across independent "
                    "physical bank nodes are not available in this software runtime. Operation fails closed to prevent fabricated operational claims."
                )
            return self._execute_verified_operational_psi(
                req=req,
                auth_token=auth_token,
                auth_principal=auth_principal,
                audited_actor=audited_actor,
            )

        # Enforce Research Simulation Boundary (Fail-Closed in Production)
        from app.config import get_settings

        settings = get_settings()
        if req.execution_mode == "SIMULATED_RESEARCH":
            if settings.is_production or settings.app_env.lower() in ("production", "prod"):
                raise ConsortiumSecurityError(
                    "Research simulation mode is strictly prohibited in production environments. "
                    "Production operations require verified operational matching with cryptographic attestation."
                )
            if not settings.is_cross_bank_simulation_allowed():
                raise ConsortiumSecurityError(
                    "Research simulation mode is disabled in this environment. Explicit configuration opt-in "
                    "(enable_cross_bank_simulation) in an approved research/development environment is required."
                )

        # 3. Authoritative Consortium Governance & Active Membership
        consortium = self.consortium_service.get_consortium(clean_consortium_id)
        if not consortium:
            raise ConsortiumSecurityError(
                f"Consortium '{clean_consortium_id}' does not exist or has not been established."
            )
        if consortium.status != ConsortiumStatus.ACTIVE:
            raise ConsortiumSecurityError(
                f"Consortium '{clean_consortium_id}' is in non-active status '{consortium.status.value}'. "
                f"Cross-bank intelligence queries are prohibited."
            )

        # Revocation / eviction check
        if clean_requesting in consortium.removed_members:
            raise ConsortiumSecurityError(
                f"Institution '{clean_requesting}' has been revoked or evicted from consortium '{clean_consortium_id}'."
            )
        if clean_requesting not in consortium.members:
            raise ConsortiumSecurityError(
                f"Institution '{clean_requesting}' is not an active member of consortium '{clean_consortium_id}'."
            )

        # Counterpart institution eligibility
        eligible_target_banks: list[str] = []
        if req.target_bank_id:
            clean_target = req.target_bank_id.lower().strip()
            if clean_target == clean_requesting:
                raise ValueError(
                    "Requesting institution and target counterpart institution cannot be identical."
                )
            if clean_target in consortium.removed_members:
                raise ConsortiumSecurityError(
                    f"Target institution '{clean_target}' has been revoked from consortium '{clean_consortium_id}'."
                )
            if clean_target not in consortium.members:
                raise ConsortiumSecurityError(
                    f"Target institution '{clean_target}' is not an active member of consortium '{clean_consortium_id}'."
                )
            eligible_target_banks = [clean_target]
        else:
            eligible_target_banks = [
                b
                for b in consortium.members
                if b != clean_requesting and b not in consortium.removed_members
            ]

        # 4. Enforce Real Consortium Data-Sharing Policy
        is_valid_policy, policy_reasons, disclosure_scope = (
            self.policy_engine.validate_intelligence_sharing(
                consortium=consortium,
                requesting_bank=clean_requesting,
                target_banks=eligible_target_banks,
                purpose=req.purpose,
            )
        )
        if not is_valid_policy:
            raise ConsortiumSecurityError(
                f"Consortium data-sharing policy rejected query: {'; '.join(policy_reasons)}"
            )

        # 5. Local Entity Verification & Anti-Enumeration Protection
        if not req.query_entity_id or not req.query_entity_id.strip():
            raise ValueError(
                "Mandatory query_entity_id missing: Cross-bank queries must reference a verified local entity belonging to the requesting institution."
            )

        val = self.entity_service._entities.get(req.query_entity_id)
        if not val:
            raise ValueError(
                f"Local entity '{req.query_entity_id}' not found in institution '{clean_requesting}' database. "
                f"Arbitrary privacy-hash or unverified entity probing is prohibited."
            )

        from app.application.services.entity_resolution import _dict_to_entity

        source_entity = _dict_to_entity(val)
        if source_entity.bank_id.lower().strip() != clean_requesting:
            raise PermissionError(
                f"Cross-tenant access violation: Entity '{req.query_entity_id}' belongs to "
                f"'{source_entity.bank_id}', not requesting institution '{clean_requesting}'."
            )

        query_privacy_id = source_entity.privacy_id
        if req.privacy_id and req.privacy_id.strip() != query_privacy_id:
            raise ValueError(
                f"Supplied privacy_id does not match the authoritative hash for local entity '{req.query_entity_id}'."
            )

        # Prevent querying operational institutional entities via simulation mode
        if req.execution_mode == "SIMULATED_RESEARCH" and bool(
            (source_entity.attributes or {}).get("is_production_operational", False)
        ):
            raise ConsortiumSecurityError(
                "Operational institutional entities cannot be queried via research simulation mode."
            )

        # 6. Execute Privacy-Preserving Matching Across Eligible Consortium Members
        contributing_banks: set[str] = set()
        matched_category: str = "NO_MATCH"
        matched_confidence: float = 0.0
        max_risk_level: str = "UNOBSERVED"
        observation_timestamps: list[datetime.datetime] = []
        sanitized_typologies: set[str] = set()

        if eligible_target_banks:
            # Query cross-institution match index
            matching_entities = self.entity_service.resolve_cross_institution(query_privacy_id)
            for m in matching_entities:
                m_bank = m.bank_id.lower().strip()
                if m_bank in eligible_target_banks:
                    contributing_banks.add(m_bank)
                    matched_category = "EXACT_MATCH"
                    matched_confidence = 1.0
                    observation_timestamps.append(m.last_seen)
                    if m.risk_level.value in ("critical", "high"):
                        max_risk_level = "CRITICAL" if m.risk_level.value == "critical" else "HIGH"
                        sanitized_typologies.add("HIGH_RISK_SYNDICATE_OVERLAP")
                    elif (
                        max_risk_level not in ("CRITICAL", "HIGH")
                        and m.risk_level.value == "medium"
                    ):
                        max_risk_level = "MEDIUM"
                    elif max_risk_level == "UNOBSERVED":
                        max_risk_level = "LOW"

            # Fuzzy MinHash matching fallback if no exact hash match found
            if not contributing_banks and disclosure_scope.get("allow_fuzzy_matching", True):
                raw_sig = None
                if source_entity and source_entity.attributes:
                    raw_sig = source_entity.attributes.get("minhash_signature")

                if raw_sig:
                    all_entities = self.entity_service.get_entities(limit=1000)
                    min_thresh = float(disclosure_scope.get("min_fuzzy_confidence_threshold", 0.60))
                    for candidate in all_entities:
                        c_bank = candidate.bank_id.lower().strip()
                        if c_bank in eligible_target_banks and candidate.attributes:
                            c_sig = candidate.attributes.get("minhash_signature")
                            if c_sig:
                                sim = calculate_jaccard_similarity(raw_sig, c_sig)
                                if sim >= min_thresh:
                                    contributing_banks.add(c_bank)
                                    matched_category = "PROBABILISTIC_FUZZY"
                                    matched_confidence = max(matched_confidence, round(sim, 2))
                                    observation_timestamps.append(candidate.last_seen)
                                    if candidate.risk_level.value in ("critical", "high"):
                                        max_risk_level = "HIGH"
                                        sanitized_typologies.add("FUZZY_IDENTITY_REPLICA")
                                    elif max_risk_level == "UNOBSERVED":
                                        max_risk_level = "MEDIUM"

        if len(contributing_banks) >= 2:
            sanitized_typologies.add("MULTI_INSTITUTION_EXPOSURE")
        if contributing_banks:
            sanitized_typologies.add("CROSS_BANK_ENTITY_OBSERVED")

        # 7. Apply Data Minimization & Institution Attribution Policy
        allow_institution_names = bool(disclosure_scope.get("allow_institution_identity", False))
        disclosed_institutions = sorted(list(contributing_banks)) if allow_institution_names else []
        disclosure_mode_literal: DisclosureMode = (
            "ATTRIBUTED_INSTITUTIONS" if allow_institution_names else "ANONYMIZED_SUMMARY"
        )

        signal_id = str(uuid.uuid4())
        now = datetime.datetime.now(UTC)
        first_obs = (
            min(observation_timestamps).isoformat() if observation_timestamps else now.isoformat()
        )
        last_obs = (
            max(observation_timestamps).isoformat() if observation_timestamps else now.isoformat()
        )

        # Truncate privacy hash to avoid arbitrary enumeration/linkage
        truncated_hash = (
            f"{query_privacy_id[:12]}..." if len(query_privacy_id) > 12 else query_privacy_id
        )

        category_literal: MatchCategory = (
            cast("MatchCategory", matched_category)
            if matched_category in ("EXACT_MATCH", "PROBABILISTIC_FUZZY", "NO_MATCH")
            else "NO_MATCH"
        )
        risk_literal: SanitizedRiskLevel = (
            cast("SanitizedRiskLevel", max_risk_level)
            if max_risk_level in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNOBSERVED")
            else "UNOBSERVED"
        )

        is_synthetic = bool(
            req.execution_mode == "SIMULATED_RESEARCH"
            or (source_entity.attributes or {}).get("is_synthetic_research", False)
        )
        is_authoritative = (
            req.execution_mode == "VERIFIED_OPERATIONAL" and not is_synthetic
        )

        signal = CrossBankIntelligenceSignal(
            signal_id=signal_id,
            consortium_id=clean_consortium_id,
            requesting_bank_id=clean_requesting,
            query_entity_id=req.query_entity_id,
            privacy_hash_truncated=truncated_hash,
            match_confirmed=len(contributing_banks) > 0,
            match_category=category_literal,
            match_confidence=matched_confidence,
            contributing_institutions_count=len(contributing_banks),
            contributing_institutions=disclosed_institutions,
            disclosure_mode=disclosure_mode_literal,
            sanitized_risk_level=risk_literal,
            sanitized_typology_indicators=sorted(list(sanitized_typologies)),
            graph_context={
                "has_incident_cross_bank_correlations": len(contributing_banks) > 0,
                "sanitized_degree_band": "MULTI_INSTITUTION"
                if len(contributing_banks) > 1
                else ("BILATERAL" if contributing_banks else "ISOLATED"),
            },
            provenance={
                "query_timestamp": now.isoformat(),
                "earliest_consortium_observation": first_obs,
                "latest_consortium_observation": last_obs,
                "governance_authority": f"consortium:{clean_consortium_id}",
                "execution_environment": "SIMULATED_RESEARCH_SANDBOX"
                if not is_authoritative
                else "VERIFIED_OPERATIONAL",
                "operational_status": "OPERATIONAL"
                if is_authoritative
                else "NON_OPERATIONAL_SYNTHETIC",
            },
            execution_mode=req.execution_mode,
            attestation_status="SOFTWARE_EMULATED",
            is_authoritative=is_authoritative,
            is_synthetic=is_synthetic,
        )

        # 8. Record in Durable Authoritative Signal Store (Anti-Forgery Registry)
        signal_dict = signal.model_dump()
        expires_at = (now + datetime.timedelta(hours=24)).isoformat()
        content_hash = hashlib.sha256(
            json.dumps(signal_dict, sort_keys=True).encode("utf-8")
        ).hexdigest()

        authoritative_record = {
            "signal_id": signal_id,
            "signal_data": signal_dict,
            "requesting_bank_id": clean_requesting,
            "consortium_id": clean_consortium_id,
            "query_entity_id": req.query_entity_id,
            "issued_to_principal": audited_actor,
            "issued_at": now.isoformat(),
            "expires_at": expires_at,
            "content_hash": content_hash,
            "is_authoritative": is_authoritative,
            "is_synthetic": is_synthetic,
            "operational_status": "OPERATIONAL"
            if is_authoritative
            else "NON_OPERATIONAL_SYNTHETIC",
            "attached_to_cases": [],
        }
        self._signal_store.set(signal_id, authoritative_record)

        # 9. Append Immutable Audit Event
        ImmutableAuditChain.get_instance().append_event(
            event_type="CROSS_BANK_INTELLIGENCE_QUERY",
            actor_id=f"user:{audited_actor}@{clean_requesting}",
            action=f"cross_bank_intelligence_query:{clean_consortium_id}",
            resource_id=f"signal:{signal_id}",
            details={
                "consortium_id": clean_consortium_id,
                "requesting_bank_id": clean_requesting,
                "target_bank_id": req.target_bank_id,
                "privacy_hash_truncated": truncated_hash,
                "match_confirmed": signal.match_confirmed,
                "match_category": signal.match_category,
                "contributing_institutions_count": signal.contributing_institutions_count,
                "disclosure_mode": signal.disclosure_mode,
            },
        )

        logger.info(
            "Cross-bank intelligence query completed for %s in consortium %s: match=%s (conf=%.2f, contributors=%d, mode=%s)",
            clean_requesting,
            clean_consortium_id,
            signal.match_confirmed,
            signal.match_confidence,
            signal.contributing_institutions_count,
            signal.disclosure_mode,
        )
        return signal

    def attach_signal_to_case_evidence(
        self,
        case_id: str,
        signal_id: str | None = None,
        signal: CrossBankIntelligenceSignal | None = None,
        auth_token: str | None = None,
        principal: AuthenticatedPrincipal | None = None,
        actor: str | None = None,
        caller_bank_id: str | None = None,
        notes: str | None = None,
        idempotency_key: str | None = None,
    ) -> CrossBankEvidenceAttachResponse:
        """Attach verified authoritative cross-bank intelligence signal into case evidence ledger."""
        # 1. Caller Authentication & Institutional Role Verification
        auth_principal = self._authenticate_principal(principal=principal, auth_token=auth_token)
        caller_bank = (auth_principal.tenant_id or caller_bank_id or "").lower().strip()
        uploader = auth_principal.principal_id

        if not any(r in PERMITTED_INTELLIGENCE_ROLES for r in auth_principal.roles):
            raise PermissionError(
                f"Principal '{auth_principal.principal_id}' lacks permitted role to attach evidence. "
                f"Permitted roles: {sorted(PERMITTED_INTELLIGENCE_ROLES)}."
            )

        # 2. Case Verification & Tenant Isolation
        case = self.case_service.get_case(case_id)
        if not case:
            raise CaseNotFoundError(f"Case '{case_id}' not found.")

        case_bank = (getattr(case, "bank_id", None) or "").lower().strip()
        if not auth_principal.is_privileged and caller_bank != case_bank:
            raise PermissionError(
                f"Tenant isolation violation: Caller institution '{caller_bank}' cannot modify "
                f"or attach evidence to case '{case_id}' owned by institution '{case.bank_id}'."
            )

        # 3. Retrieve Authoritative Signal Server-Side (Anti-Forgery & Tamper Check)
        # Block prototype results at the source across ALL case states (do not rely exclusively on case status)
        resolved_signal_id = signal_id or (signal.signal_id if signal else None)
        if not resolved_signal_id:
            raise ValueError(
                "Mandatory signal_id missing. Evidence attachment must reference an authoritative server-issued signal."
            )

        record = self._signal_store.get(resolved_signal_id)
        if not record:
            from app.infrastructure.redis_store import RedisStore

            research_store = RedisStore("research_prototype_signals")
            research_record = research_store.get(resolved_signal_id)
            if research_record:
                raise ConsortiumSecurityError(
                    f"Signal '{resolved_signal_id}' is a research prototype result and cannot enter "
                    "case evidence in any case state (ordinary open, escalated, or SAR). "
                    "Defensible evidence requires verified operational execution and independent cryptographic audit."
                )
            raise ValueError(
                f"Authoritative signal '{resolved_signal_id}' not found or was never issued by an authorized intelligence query. "
                f"Client-fabricated or unauthorized signal attachment rejected."
            )

        if not case.is_open:
            raise TerminalCaseImmutableError(
                f"Cannot attach cross-bank intelligence: Case '{case_id}' is finalized in terminal state '{case.status.value}'."
            )

        # 3a. Authoritative Signal Content Integrity & Tamper Verification
        signal_data = record.get("signal_data")
        if not signal_data or not isinstance(signal_data, dict):
            raise ValueError(
                f"Corrupted authoritative signal record '{resolved_signal_id}': missing signal_data payload."
            )

        recomputed_hash = hashlib.sha256(
            json.dumps(signal_data, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if recomputed_hash != record.get("content_hash"):
            raise ConsortiumSecurityError(
                f"Authoritative signal failed server-side tamper verification for '{resolved_signal_id}': "
                f"canonical content digest mismatch. Stored record has been tampered with or corrupted."
            )

        # 3b. Required Institutional and Principal Bindings
        sig_requesting_bank = str(record.get("requesting_bank_id", "")).lower().strip()
        if not sig_requesting_bank or sig_requesting_bank != case_bank:
            raise PermissionError(
                f"Cross-tenant signal violation: Authoritative signal '{resolved_signal_id}' was issued to institution "
                f"'{sig_requesting_bank}', cannot be attached to case belonging to institution '{case_bank}'."
            )

        if not record.get("consortium_id"):
            raise ValueError(f"Corrupted signal '{resolved_signal_id}': missing consortium_id binding.")
        if not record.get("query_entity_id"):
            raise ValueError(f"Corrupted signal '{resolved_signal_id}': missing query_entity_id binding.")
        if not record.get("issued_to_principal"):
            raise ValueError(f"Corrupted signal '{resolved_signal_id}': missing issuing principal binding.")

        # 3c. Expiry check
        now = datetime.datetime.now(UTC)
        expires_at = datetime.datetime.fromisoformat(record["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if now >= expires_at:
            raise ValueError(
                f"Authoritative signal '{resolved_signal_id}' has expired. Queries must be refreshed before attachment."
            )

        # 3c-ii. Pilot Governance & Kill Switch Enforcement
        from app.application.services.pilot_operations_service import (
            PilotOperationsService,
        )
        from app.domain.consortium_governance import ConsortiumStatus
        from app.domain.pilot_operations import PilotStatus

        pilot_svc = PilotOperationsService.get_instance()
        pilot_id = record.get("pilot_id")
        req_b = str(record.get("requesting_bank_id") or sig_requesting_bank).lower().strip()
        tgt_b = str(record.get("target_bank_id") or "").lower().strip()
        purp = str(record.get("purpose") or "")

        if not pilot_id and req_b and tgt_b:
            p_scoped = pilot_svc.find_active_or_scoped_pilot(
                requesting_bank=req_b,
                target_bank=tgt_b,
                purpose=purp,
            )
            if p_scoped:
                pilot_id = p_scoped.config.pilot_id

        if pilot_id:
            governing_pilot = pilot_svc.get_pilot(pilot_id)
            if not governing_pilot:
                raise ConsortiumSecurityError(
                    f"Governing pilot '{pilot_id}' not found. Cannot attach unverified pilot signal."
                )
            if governing_pilot.status != PilotStatus.ACTIVE:
                raise ConsortiumSecurityError(
                    f"Governing pilot '{pilot_id}' is in non-active status '{governing_pilot.status.value}'. "
                    f"Reason: {governing_pilot.status_reason}. Evidence attachment rejected."
                )
            if governing_pilot.has_expired():
                raise ConsortiumSecurityError(
                    f"Governing pilot '{pilot_id}' has expired. Evidence attachment rejected."
                )

            # Recheck consortium and operational consent
            consortium = pilot_svc.consortium_service.get_consortium(governing_pilot.config.consortium_id)
            if not consortium or consortium.status != ConsortiumStatus.ACTIVE:
                raise ConsortiumSecurityError(
                    f"Underlying consortium '{governing_pilot.config.consortium_id}' is not ACTIVE. Evidence attachment rejected."
                )
            c_meta = getattr(consortium, "metadata", {}) or {}
            op_grant = c_meta.get("operational_data_sharing_grant") or {}
            if not op_grant.get("is_active", True):
                raise ConsortiumSecurityError("Operational data sharing grant is inactive or revoked. Evidence attachment rejected.")
            revoked = set(op_grant.get("revoked_by_institutions", []))
            if req_b in revoked or tgt_b in revoked:
                raise ConsortiumSecurityError("Operational consent has been revoked by participating institution. Evidence attachment rejected.")

        # 3d. Prevent Prototype Matching Results from Entering Case Evidence in Any Case State
        assurance_level = signal_data.get("assurance_level") or record.get("assurance_level")
        execution_mode = signal_data.get("execution_mode") or record.get("execution_mode")
        is_authoritative = bool(record.get("is_authoritative", True) and signal_data.get("is_authoritative", True))

        if (
            assurance_level in ("PROTOCOL_PROTOTYPE", "RESEARCH_PROTOTYPE", "LOCAL_PROTOTYPE")
            or execution_mode in ("RESEARCH_PROTOTYPE", "PROTOTYPE")
        ):
            raise ConsortiumSecurityError(
                "Protocol prototype matching results cannot enter case evidence in any case state "
                "(ordinary open, escalated, or SAR). Defensible evidence requires verified operational "
                "execution and independent cryptographic audit."
            )

        # 3e. Prevent Simulated Research Intelligence from Entering Authoritative Case Evidence or Production Filings
        from app.config import get_settings
        from app.domain.enums import CaseStatus

        settings = get_settings()
        is_signal_simulated = bool(
            execution_mode == "SIMULATED_RESEARCH"
            or not is_authoritative
            or record.get("is_synthetic", False)
            or signal_data.get("is_synthetic", False)
        )

        if is_signal_simulated:
            if settings.is_production or settings.app_env.lower() in ("production", "prod"):
                raise ValueError(
                    "Simulated research intelligence cannot be attached to fraud cases in production environments."
                )

            is_authoritative_case = bool(
                getattr(case, "is_authoritative", False)
                or (getattr(case, "signature_metadata", {}) or {}).get("is_authoritative", False)
                or case.status in (CaseStatus.ESCALATED, CaseStatus.SAR_FILED)
            )
            if is_authoritative_case:
                raise ConsortiumSecurityError(
                    "Simulated research signals cannot be attached to authoritative fraud investigation evidence or regulatory filings."
                )

        # 4. Revalidate Current Consortium Governance & Revocation Status
        consortium_id = str(record.get("consortium_id", "")).lower().strip()
        consortium = self.consortium_service.get_consortium(consortium_id)
        if not consortium:
            raise ConsortiumSecurityError(f"Consortium '{consortium_id}' no longer exists.")
        if consortium.status != ConsortiumStatus.ACTIVE:
            raise ConsortiumSecurityError(
                f"Consortium '{consortium_id}' is in non-active status '{consortium.status.value}'."
            )
        if case_bank in consortium.removed_members:
            raise ConsortiumSecurityError(
                f"Institution '{case_bank}' has been revoked/evicted from consortium '{consortium_id}'."
            )
        if case_bank not in consortium.members:
            raise ConsortiumSecurityError(
                f"Institution '{case_bank}' is not an active member of consortium '{consortium_id}'."
            )

        # 4b. Revalidate Operational Data-Sharing Grant & Bilateral Consent
        grant_id = record.get("grant_id") or signal_data.get("provenance", {}).get("grant_id")
        if grant_id:
            grant = consortium.metadata.get("operational_data_sharing_grant") or {}
            is_grant_active = bool(grant.get("is_active", False) and grant.get("status") != "REVOKED")
            revoked_institutions = set(grant.get("revoked_by_institutions") or [])
            target_bank = record.get("target_bank_id") or signal_data.get("provenance", {}).get("target_bank_id")
            if (
                grant.get("grant_id") != grant_id
                or not is_grant_active
                or (case_bank in revoked_institutions)
                or (target_bank and target_bank in revoked_institutions)
            ):
                raise ConsortiumSecurityError(
                    f"Operational consent for grant '{grant_id}' has been revoked or is no longer active in consortium '{consortium_id}'. "
                    "Evidence attachment denied."
                )
            expected_rev = record.get("grant_revision") or signal_data.get("provenance", {}).get("grant_revision")
            if expected_rev is not None and grant.get("revision") != expected_rev:
                raise ConsortiumSecurityError(
                    f"Operational grant '{grant_id}' revision changed (was {expected_rev}, now {grant.get('revision')}). "
                    "Prior consent no longer authorizes evidence attachment without reapproval."
                )
            approvals = grant.get("approvals") or {}
            if case_bank not in approvals:
                raise ConsortiumSecurityError(
                    f"Institution '{case_bank}' has not approved current grant revision for grant '{grant_id}'. "
                    "Evidence attachment denied."
                )
            target_bank = record.get("target_bank_id") or signal_data.get("provenance", {}).get("target_bank_id")
            if target_bank and target_bank not in approvals:
                raise ConsortiumSecurityError(
                    f"Target counterpart institution '{target_bank}' has not approved current grant revision for grant '{grant_id}'. "
                    "Evidence attachment denied."
                )

        # 5. Idempotent Submission Check (Prevent Duplicate Evidence Creation)
        existing_evidence = self.evidence_service.get_case_evidence(case_id)
        for ev in existing_evidence:
            ev_meta = ev.get("metadata") or {}
            if ev_meta.get("signal_id") == resolved_signal_id or (
                idempotency_key and ev_meta.get("idempotency_key") == idempotency_key
            ):
                logger.info(
                    "Idempotent attachment replay detected for signal %s on case %s (evidence_id=%s)",
                    resolved_signal_id,
                    case_id,
                    ev["id"],
                )
                return CrossBankEvidenceAttachResponse(
                    evidence_id=ev["id"],
                    case_id=case_id,
                    signal_id=resolved_signal_id,
                    evidence_type=ev.get("evidence_type", "CROSS_BANK_INTELLIGENCE"),
                    title=ev.get("title", ""),
                    content_hash=ev.get("content_hash", ""),
                    uploaded_at=ev.get("uploaded_at", ""),
                    uploaded_by=ev.get("uploaded_by", uploader),
                    case_version=getattr(case, "version", 1),
                    timeline_event_recorded=False,
                    is_duplicate_submission=True,
                )

        # 6. Durably Register Evidence with CAS Rollback Protection & Collision-Resistant Paths
        payload = {
            "signal_id": resolved_signal_id,
            "consortium_id": consortium_id,
            "requesting_bank_id": sig_requesting_bank,
            "query_entity_id": record.get("query_entity_id"),
            "signal_content": signal_data,
            "analyst_notes": notes,
            "attached_by": uploader,
            "attached_at": now.isoformat(),
            "idempotency_key": idempotency_key,
            "is_authoritative": is_authoritative,
            "operational_status": "OPERATIONAL"
            if is_authoritative
            else "NON_OPERATIONAL_SYNTHETIC",
        }
        content_json = json.dumps(payload, sort_keys=True, indent=2)
        payload_digest = hashlib.sha256(content_json.encode("utf-8")).hexdigest()
        file_path = f"storage/evidence/case_{case_id}_signal_{resolved_signal_id}_{payload_digest[:16]}.json"
        title = f"Consortium Intelligence Evidence ({consortium_id.upper()})"

        ev = self.evidence_service.register_evidence(
            case_id=case_id,
            evidence_type="CROSS_BANK_INTELLIGENCE",
            title=title,
            file_path=file_path,
            content=content_json,
            uploaded_by=uploader,
            metadata={
                "signal_id": resolved_signal_id,
                "consortium_id": consortium_id,
                "idempotency_key": idempotency_key,
                "is_authoritative": is_authoritative,
                "operational_status": "OPERATIONAL"
                if is_authoritative
                else "NON_OPERATIONAL_SYNTHETIC",
            },
        )

        # Record attachment in authoritative record
        record.setdefault("attached_to_cases", []).append(case_id)
        self._signal_store.set(resolved_signal_id, record)

        # Refresh updated case version
        updated_case = self.case_service.get_case(case_id)
        current_version = getattr(updated_case, "version", 1)

        ImmutableAuditChain.get_instance().append_event(
            event_type="CROSS_BANK_INTELLIGENCE_EVIDENCE_ATTACHED",
            actor_id=f"user:{uploader}@{case_bank}",
            action=f"attach_cross_bank_evidence:{case_id}",
            resource_id=f"evidence:{ev['id']}",
            details={
                "case_id": case_id,
                "evidence_id": ev["id"],
                "content_hash": ev["content_hash"],
                "signal_id": resolved_signal_id,
                "consortium_id": consortium_id,
            },
        )

        logger.info(
            "Attached authoritative cross-bank intelligence signal %s to case %s (evidence_id=%s, hash=%s)",
            resolved_signal_id,
            case_id,
            ev["id"],
            ev["content_hash"][:8],
        )

        return CrossBankEvidenceAttachResponse(
            evidence_id=ev["id"],
            case_id=case_id,
            signal_id=resolved_signal_id,
            evidence_type=ev["evidence_type"],
            title=ev["title"],
            content_hash=ev["content_hash"],
            uploaded_at=ev["uploaded_at"],
            uploaded_by=ev["uploaded_by"],
            case_version=current_version,
            timeline_event_recorded=True,
            is_duplicate_submission=False,
        )

    def attach_signal_to_research_case_evidence(
        self,
        case_id: str,
        signal_id: str | None = None,
        auth_token: str | None = None,
        principal: AuthenticatedPrincipal | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Attach research simulation signal into isolated non-operational research evidence store.

        Maintains strict non-operational namespace isolation: research evidence is never written
        to authoritative case evidence_ids or consumable by SAR regulatory pipelines.
        """
        auth_principal = self._authenticate_principal(principal=principal, auth_token=auth_token)
        if not signal_id:
            raise ValueError("Mandatory signal_id missing.")
        record = self._signal_store.get(signal_id)
        if not record:
            raise ValueError(f"Signal '{signal_id}' not found in registry.")

        ev_id = str(uuid.uuid4())
        ev_entry: dict[str, Any] = {
            "id": ev_id,
            "case_id": case_id,
            "signal_id": signal_id,
            "notes": notes,
            "attached_by": auth_principal.principal_id,
            "attached_at": datetime.datetime.now(UTC).isoformat(),
            "is_operational": False,
            "evidence_type": "RESEARCH_CROSS_BANK_SIMULATION",
            "metadata": record.get("signal_data", {}),
        }
        self._research_case_evidence_store.set(ev_id, ev_entry)
        return ev_entry

