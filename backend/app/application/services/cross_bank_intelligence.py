"""Cross-Bank Intelligence Orchestration Service.

Orchestrates authorized, privacy-preserving cross-bank matching, signal exchange,
and institution-scoped risk context for AML investigations under strict consortium governance.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import threading
import uuid
from datetime import UTC
from typing import cast

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
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    ConsortiumStatus,
)
from app.domain.consortium_policy import ConsortiumPolicyEngine
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
    ) -> None:
        self.consortium_service = consortium_service or ConsortiumGovernanceService()
        self.entity_service = entity_service or EntityResolutionService()
        self.case_service = case_service or CaseManagementService()
        self.evidence_service = evidence_service or EvidenceRegistryService(
            case_service=self.case_service
        )
        self.policy_engine = policy_engine or ConsortiumPolicyEngine()
        self._signal_store = RedisStore("authoritative_signals")
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
        if req.execution_mode == "VERIFIED_OPERATIONAL":
            raise RuntimeError(
                "Operational privacy attestation unavailable: Hardware enclaves (Intel SGX) and distributed "
                "multi-party cryptographic PSI across independent physical bank nodes are not available in this software runtime. "
                "Operation fails closed to prevent fabricated operational claims."
            )

        if req.require_hardware_attestation:
            raise RuntimeError(
                "Hardware attestation unavailable: Physical hardware enclave is absent in this runtime. "
                "Production hardware attestation requirement failed closed."
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
            },
            execution_mode="SIMULATED_RESEARCH",
            attestation_status="SOFTWARE_EMULATED",
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

        if not case.is_open:
            raise TerminalCaseImmutableError(
                f"Cannot attach cross-bank intelligence: Case '{case_id}' is finalized in terminal state '{case.status.value}'."
            )

        # 3. Retrieve Authoritative Signal Server-Side (Anti-Forgery & Tamper Check)
        resolved_signal_id = signal_id or (signal.signal_id if signal else None)
        if not resolved_signal_id:
            raise ValueError(
                "Mandatory signal_id missing. Evidence attachment must reference an authoritative server-issued signal."
            )

        record = self._signal_store.get(resolved_signal_id)
        if not record:
            raise ValueError(
                f"Authoritative signal '{resolved_signal_id}' not found or was never issued by an authorized intelligence query. "
                f"Client-fabricated or unauthorized signal attachment rejected."
            )

        # Expiry check
        now = datetime.datetime.now(UTC)
        expires_at = datetime.datetime.fromisoformat(record["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if now >= expires_at:
            raise ValueError(
                f"Authoritative signal '{resolved_signal_id}' has expired. Queries must be refreshed before attachment."
            )

        # Institutional binding check
        sig_requesting_bank = str(record.get("requesting_bank_id", "")).lower().strip()
        if sig_requesting_bank != case_bank:
            raise PermissionError(
                f"Cross-tenant signal violation: Authoritative signal '{resolved_signal_id}' was issued to institution "
                f"'{sig_requesting_bank}', cannot be attached to case belonging to institution '{case_bank}'."
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

        # 6. Durably Register Evidence with CAS Rollback Protection
        signal_data = record["signal_data"]
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
        }
        content_json = json.dumps(payload, sort_keys=True, indent=2)
        title = f"Consortium Intelligence Evidence ({consortium_id.upper()})"

        ev = self.evidence_service.register_evidence(
            case_id=case_id,
            evidence_type="CROSS_BANK_INTELLIGENCE",
            title=title,
            file_path=f"storage/evidence/consortium_signal_{resolved_signal_id[:8]}.json",
            content=content_json,
            uploaded_by=uploader,
            metadata={
                "signal_id": resolved_signal_id,
                "consortium_id": consortium_id,
                "idempotency_key": idempotency_key,
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
