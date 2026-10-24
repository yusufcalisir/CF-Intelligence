"""Cross-Bank Intelligence Orchestration Service.

Orchestrates authorized, privacy-preserving cross-bank matching, signal exchange,
and institution-scoped risk context for AML investigations under strict consortium governance.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import cast

from app.application.schemas.cross_bank_intelligence import (
    CrossBankEvidenceAttachResponse,
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
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
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

logger = logging.getLogger(__name__)


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
        self.evidence_service = evidence_service or EvidenceRegistryService()
        self.policy_engine = policy_engine or ConsortiumPolicyEngine()
        self._lock = threading.RLock()

    def query_cross_bank_intelligence(
        self,
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None = None,
        actor: str = "analyst",
    ) -> CrossBankIntelligenceSignal:
        """Execute authorized cross-bank intelligence query under consortium policy."""
        clean_requesting = req.requesting_bank_id.lower().strip()
        clean_consortium_id = req.consortium_id.lower().strip()

        # 1. Caller Authentication & Institution Non-Spoofing Check
        if auth_token:
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()
            is_valid, claims, err = auth_svc.verify_access_token(auth_token)
            if not is_valid or not claims:
                raise PermissionError(f"Authentication failed: {err}")
            caller_bank = (claims.get("bank_id") or "").lower().strip()
            if caller_bank and caller_bank != clean_requesting:
                raise PermissionError(
                    f"Institutional impersonation rejected: Authenticated principal belongs to '{caller_bank}', "
                    f"cannot execute intelligence queries on behalf of institution '{req.requesting_bank_id}'."
                )

        # 2. Hardware Enclave Attestation Check (Fail-Closed)
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
                raise ValueError("Requesting institution and target counterpart institution cannot be identical.")
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
                b for b in consortium.members
                if b != clean_requesting and b not in consortium.removed_members
            ]

        # 4. Resolve Query Identifier
        query_privacy_id: str | None = None
        source_entity = None

        if req.query_entity_id:
            # Look up internal entity in requesting bank's storage
            val = self.entity_service._entities.get(req.query_entity_id)
            if val:
                from app.application.services.entity_resolution import _dict_to_entity
                e = _dict_to_entity(val)
                if e.bank_id.lower().strip() != clean_requesting:
                    raise PermissionError(
                        f"Cross-tenant access violation: Entity '{req.query_entity_id}' belongs to "
                        f"'{e.bank_id}', not requesting institution '{clean_requesting}'."
                    )
                query_privacy_id = e.privacy_id
                source_entity = e

        if not query_privacy_id and req.privacy_id:
            query_privacy_id = req.privacy_id

        if not query_privacy_id:
            raise ValueError(
                "Either a valid local query_entity_id or privacy_id must be provided for cross-bank matching."
            )

        # 5. Execute Privacy-Preserving Matching Across Eligible Consortium Members
        contributing_banks: set[str] = set()
        matched_category: str = "NO_MATCH"
        matched_confidence: float = 0.0
        max_risk_level: str = "UNOBSERVED"
        observation_timestamps: list[datetime] = []
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
                    elif max_risk_level not in ("CRITICAL", "HIGH") and m.risk_level.value == "medium":
                        max_risk_level = "MEDIUM"
                    elif max_risk_level == "UNOBSERVED":
                        max_risk_level = "LOW"

            # Fuzzy MinHash matching fallback if no exact hash match found
            if not contributing_banks:
                raw_sig = None
                if source_entity and source_entity.attributes:
                    raw_sig = source_entity.attributes.get("minhash_signature")

                if raw_sig:
                    all_entities = self.entity_service.get_entities(limit=1000)
                    for candidate in all_entities:
                        c_bank = candidate.bank_id.lower().strip()
                        if c_bank in eligible_target_banks and candidate.attributes:
                            c_sig = candidate.attributes.get("minhash_signature")
                            if c_sig:
                                sim = calculate_jaccard_similarity(raw_sig, c_sig)
                                if sim >= 0.6:
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

        # 6. Build Sanitized, Institution-Scoped Signal (Zero Counterpart PII)
        signal_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        first_obs = min(observation_timestamps).isoformat() if observation_timestamps else now.isoformat()
        last_obs = max(observation_timestamps).isoformat() if observation_timestamps else now.isoformat()

        # Truncate privacy hash to avoid arbitrary enumeration/linkage
        truncated_hash = f"{query_privacy_id[:12]}..." if len(query_privacy_id) > 12 else query_privacy_id

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
            contributing_institutions=sorted(list(contributing_banks)),
            sanitized_risk_level=risk_literal,
            sanitized_typology_indicators=sorted(list(sanitized_typologies)),
            graph_context={
                "has_incident_cross_bank_correlations": len(contributing_banks) > 0,
                "sanitized_degree_band": "MULTI_INSTITUTION" if len(contributing_banks) > 1 else ("BILATERAL" if contributing_banks else "ISOLATED"),
            },
            provenance={
                "query_timestamp": now.isoformat(),
                "earliest_consortium_observation": first_obs,
                "latest_consortium_observation": last_obs,
                "governance_authority": f"consortium:{clean_consortium_id}",
            },
            execution_mode=req.execution_mode,
            attestation_status="UNAVAILABLE_NO_HARDWARE_ENCLAVE",
        )

        # 7. Append Immutable Audit Event
        ImmutableAuditChain.get_instance().append_event(
            event_type="CROSS_BANK_INTELLIGENCE_QUERY",
            actor_id=f"user:{actor}@{clean_requesting}",
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
            },
        )

        logger.info(
            "Cross-bank intelligence query completed for %s in consortium %s: match=%s (conf=%.2f, contributors=%d)",
            clean_requesting,
            clean_consortium_id,
            signal.match_confirmed,
            signal.match_confidence,
            signal.contributing_institutions_count,
        )
        return signal

    def attach_signal_to_case_evidence(
        self,
        case_id: str,
        signal: CrossBankIntelligenceSignal,
        actor: str = "analyst",
        caller_bank_id: str | None = None,
        notes: str | None = None,
    ) -> CrossBankEvidenceAttachResponse:
        """Attach verified cross-bank intelligence signal into an AML investigation case."""
        case = self.case_service.get_case(case_id)
        if not case:
            raise CaseNotFoundError(f"Case '{case_id}' not found.")

        clean_caller_bank = (caller_bank_id or "").lower().strip()
        case_bank = (getattr(case, "bank_id", None) or "").lower().strip()

        if clean_caller_bank and case_bank and clean_caller_bank != case_bank:
            raise PermissionError(
                f"Tenant isolation violation: Caller institution '{caller_bank_id}' cannot modify "
                f"or attach evidence to case '{case_id}' owned by institution '{case.bank_id}'."
            )

        if not case.is_open:
            raise TerminalCaseImmutableError(
                f"Cannot attach cross-bank intelligence: Case '{case_id}' is finalized in terminal state '{case.status.value}'."
            )

        payload = {
            "signal_id": signal.signal_id,
            "consortium_id": signal.consortium_id,
            "requesting_bank_id": signal.requesting_bank_id,
            "query_entity_id": signal.query_entity_id,
            "privacy_hash_truncated": signal.privacy_hash_truncated,
            "match_confirmed": signal.match_confirmed,
            "match_category": signal.match_category,
            "match_confidence": signal.match_confidence,
            "contributing_institutions_count": signal.contributing_institutions_count,
            "contributing_institutions": signal.contributing_institutions,
            "sanitized_risk_level": signal.sanitized_risk_level,
            "sanitized_typology_indicators": signal.sanitized_typology_indicators,
            "provenance": signal.provenance,
            "execution_mode": signal.execution_mode,
            "attestation_status": signal.attestation_status,
            "disclaimer": signal.disclaimer,
            "analyst_notes": notes,
        }
        content_json = json.dumps(payload, sort_keys=True, indent=2)

        title = f"Consortium Intelligence Evidence ({signal.consortium_id.upper()})"
        ev = self.evidence_service.register_evidence(
            case_id=case_id,
            evidence_type="CROSS_BANK_INTELLIGENCE",
            title=title,
            file_path=f"storage/evidence/consortium_signal_{signal.signal_id[:8]}.json",
            content=content_json,
            uploaded_by=actor,
        )

        # Refresh updated case version
        updated_case = self.case_service.get_case(case_id)
        current_version = getattr(updated_case, "version", 1)

        ImmutableAuditChain.get_instance().append_event(
            event_type="CROSS_BANK_INTELLIGENCE_EVIDENCE_ATTACHED",
            actor_id=f"user:{actor}@{case_bank}",
            action=f"attach_cross_bank_evidence:{case_id}",
            resource_id=f"evidence:{ev['id']}",
            details={
                "case_id": case_id,
                "evidence_id": ev["id"],
                "content_hash": ev["content_hash"],
                "signal_id": signal.signal_id,
                "consortium_id": signal.consortium_id,
            },
        )

        logger.info(
            "Attached cross-bank intelligence signal %s to case %s (evidence_id=%s, hash=%s)",
            signal.signal_id,
            case_id,
            ev["id"],
            ev["content_hash"][:8],
        )

        return CrossBankEvidenceAttachResponse(
            evidence_id=ev["id"],
            case_id=case_id,
            evidence_type=ev["evidence_type"],
            title=ev["title"],
            content_hash=ev["content_hash"],
            uploaded_at=ev["uploaded_at"],
            uploaded_by=ev["uploaded_by"],
            case_version=current_version,
            timeline_event_recorded=True,
        )
