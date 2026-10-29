"""Cross-Bank Intelligence Orchestration Service.

Orchestrates authorized, privacy-preserving cross-bank matching, signal exchange,
and institution-scoped risk context for AML investigations under strict consortium governance.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import hmac
import http.client
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import uuid
from datetime import UTC
from typing import Any, Literal, cast

from app.application.schemas.cross_bank_intelligence import (
    CounterpartyExecutionFailure,
    CounterpartyExecutionOutcome,
    CrossBankEvidenceAttachResponse,
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
    CrossBankMultiEvidenceAttachResponse,
    DisclosureMode,
    MatchCategory,
    MultiCounterpartyEvidenceAttachFailure,
    MultiCounterpartyEvidenceItem,
    MultiCounterpartyIntelligenceQueryRequest,
    MultiCounterpartyIntelligenceQueryResponse,
    MultiEvidenceAssemblyStatus,
    OverallQueryOutcome,
    SanitizedRiskLevel,
)
from app.application.services.case_service import (
    CaseManagementService,
    CaseNotFoundError,
    EvidenceRegistryService,
    InvalidCaseTransitionError,
    TerminalCaseImmutableError,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.entity_resolution import EntityResolutionService
from app.application.services.isolated_matching_service import (
    CrossBankMatchingCoordinator,
)
from app.domain.circuit_breaker_policy import (
    CircuitState,
    CounterpartyCircuitBreakerOpenError,
    CounterpartyCircuitBreakerPolicy,
)
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    ConsortiumStatus,
    CounterpartyDeadlineExceededError,
    CounterpartyTimeoutError,
    CounterpartyTransportError,
    CounterpartyWorkerUnavailableError,
    CrossBankExecutionQuotaExceededError,
    CrossBankIdempotencyConflictError,
)
from app.domain.consortium_policy import ConsortiumPolicyEngine
from app.domain.execution_deadline_policy import (
    MIN_PER_COUNTERPARTY_TIMEOUT_SECONDS,
    CounterpartyExecutionDeadlinePolicy,
)
from app.domain.institutional_identity import (
    InstitutionalVerificationRegistry,
    canonical_counterpart_evidence_bytes,
    canonical_evaluated_result_bytes,
)
from app.domain.pilot_operations import PilotQuotaExceededError
from app.domain.value_objects_investigation import (
    calculate_jaccard_similarity,
)
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_batch_store import (
    BatchCaseBindingConflictError,
    BatchNotFoundError,
    DurableCrossBankBatchStore,
)
from app.infrastructure.storage.durable_circuit_breaker_store import (
    DurableCrossBankCircuitBreakerStore,
)
from app.infrastructure.storage.durable_execution_budget_store import (
    REASON_BATCH_ABORTED_BEFORE_DISPATCH,
    REASON_CIRCUIT_BREAKER_BLOCKED,
    REASON_EXECUTION_COMPLETED,
    REASON_EXECUTION_INDETERMINATE,
    REASON_PILOT_PREFLIGHT_REJECTED,
    REASON_PRE_DISPATCH_DEADLINE_EXCEEDED,
    DurableCrossBankExecutionBudgetStore,
)
from app.infrastructure.storage.durable_idempotency_store import (
    DurableCrossBankIdempotencyStore,
)

logger = logging.getLogger(__name__)

PERMITTED_INTELLIGENCE_ROLES: frozenset[str] = frozenset(
    {
        "analyst",
        "investigator",
        "compliance",
        "compliance_auditor",
        "compliance_officer",
        "fraud_investigator",
        "admin",
        "ml_lead",
        "cross_bank_investigator",
        "super_admin",
    }
)


def compute_multi_counterparty_request_fingerprint(
    req: MultiCounterpartyIntelligenceQueryRequest,
    governance_scope: dict[str, Any] | None = None,
) -> str:
    """Computes a deterministic canonical SHA-256 fingerprint of the query request and its governance scope."""
    canonical_payload: dict[str, Any] = {
        "case_id": (req.case_id or "").strip(),
        "consortium_id": req.consortium_id.lower().strip(),
        "execution_mode": req.execution_mode.strip(),
        "pilot_id": (req.pilot_id or "").strip(),
        "privacy_id": (req.privacy_id or "").strip(),
        "protocol": (req.protocol or "").strip(),
        "purpose": req.purpose.strip(),
        "query_entity_id": req.query_entity_id.strip(),
        "requesting_bank_id": req.requesting_bank_id.lower().strip(),
        "require_established_psi": req.require_established_psi,
        "require_hardware_attestation": req.require_hardware_attestation,
        "target_bank_ids": sorted({t.lower().strip() for t in req.target_bank_ids}),
        "use_established_psi": req.use_established_psi,
    }
    if req.per_counterparty_timeout_seconds is not None:
        canonical_payload["per_counterparty_timeout_seconds"] = float(req.per_counterparty_timeout_seconds)
    if req.total_batch_timeout_seconds is not None:
        canonical_payload["total_batch_timeout_seconds"] = float(req.total_batch_timeout_seconds)
    if req.circuit_breaker_enabled is not None:
        canonical_payload["circuit_breaker_enabled"] = req.circuit_breaker_enabled
    if req.circuit_breaker_failure_threshold is not None:
        canonical_payload["circuit_breaker_failure_threshold"] = req.circuit_breaker_failure_threshold
    if req.circuit_breaker_cooldown_seconds is not None:
        canonical_payload["circuit_breaker_cooldown_seconds"] = float(req.circuit_breaker_cooldown_seconds)
    if governance_scope:
        canonical_payload["governance_scope"] = governance_scope

    canonical_bytes = json.dumps(
        canonical_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


class CrossBankIntelligenceService:
    """Manages authorized cross-bank intelligence queries and evidence binding."""

    _instance: CrossBankIntelligenceService | None = None
    _singleton_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> CrossBankIntelligenceService:
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Resets the singleton instance (for testing isolation)."""
        with cls._singleton_lock:
            cls._instance = None

    def __init__(
        self,
        consortium_service: ConsortiumGovernanceService | None = None,
        entity_service: EntityResolutionService | None = None,
        case_service: CaseManagementService | None = None,
        evidence_service: EvidenceRegistryService | None = None,
        policy_engine: ConsortiumPolicyEngine | None = None,
        matching_coordinator: CrossBankMatchingCoordinator | None = None,
        institutional_registry: InstitutionalVerificationRegistry | None = None,
        idempotency_store: DurableCrossBankIdempotencyStore | None = None,
        budget_store: DurableCrossBankExecutionBudgetStore | None = None,
        circuit_breaker_store: DurableCrossBankCircuitBreakerStore | None = None,
        batch_store: DurableCrossBankBatchStore | None = None,
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
        self.idempotency_store = idempotency_store or DurableCrossBankIdempotencyStore.get_instance()
        self.budget_store = budget_store or DurableCrossBankExecutionBudgetStore.get_instance()
        self.circuit_breaker_store = circuit_breaker_store or DurableCrossBankCircuitBreakerStore.get_instance()
        self.batch_store = batch_store or DurableCrossBankBatchStore.get_instance()
        self._signal_store = RedisStore("authoritative_signals")
        self._synthetic_entity_store = RedisStore("synthetic_entities")
        self._research_case_evidence_store = RedisStore("research_case_evidence")
        self._admitted_receipts_store = RedisStore("admitted_operational_psi_receipts")
        self._admitted_receipt_ids: set[str] = set()
        self._before_result_admission_hook: Any = None
        self._before_signal_publication_hook: Any = None
        self._after_publication_preparation_hook: Any = None
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

    def _resolve_execution_deadline_policy(
        self, req: MultiCounterpartyIntelligenceQueryRequest
    ) -> CounterpartyExecutionDeadlinePolicy:
        """Resolve authoritative execution deadline policy from request overrides and system settings."""
        from app.config import get_settings
        from app.domain.execution_deadline_policy import (
            DEFAULT_PER_COUNTERPARTY_TIMEOUT_SECONDS,
            DEFAULT_TOTAL_BATCH_TIMEOUT_SECONDS,
            CounterpartyExecutionDeadlinePolicy,
        )

        settings = get_settings()
        default_per_counterpart = getattr(
            settings,
            "cross_bank_per_counterparty_timeout_seconds",
            DEFAULT_PER_COUNTERPARTY_TIMEOUT_SECONDS,
        )
        default_total_batch = getattr(
            settings,
            "cross_bank_total_batch_timeout_seconds",
            DEFAULT_TOTAL_BATCH_TIMEOUT_SECONDS,
        )

        effective_per_counterpart = (
            float(req.per_counterparty_timeout_seconds)
            if req.per_counterparty_timeout_seconds is not None
            else float(default_per_counterpart)
        )
        effective_total_batch = (
            float(req.total_batch_timeout_seconds)
            if req.total_batch_timeout_seconds is not None
            else float(default_total_batch)
        )

        return CounterpartyExecutionDeadlinePolicy(
            per_counterparty_timeout_seconds=effective_per_counterpart,
            total_batch_timeout_seconds=effective_total_batch,
        )

    def _resolve_circuit_breaker_policy(
        self, req: MultiCounterpartyIntelligenceQueryRequest | CrossBankIntelligenceQueryRequest
    ) -> CounterpartyCircuitBreakerPolicy:
        """Resolve authoritative circuit breaker policy from request overrides and system settings.

        Enforces server-authoritative controls (Correction A):
        - A client cannot disable or weaken an enabled server-side circuit breaker.
        - When cfi_circuit_breaker_enabled is True on the server, circuit_breaker_enabled=False
          in the request is ignored and breaker remains enabled.
        - Client overrides cannot weaken failure thresholds (higher count) or cooldowns (shorter time).
        - When server enforcement is disabled (default), authorized client opt-in is respected.
        """
        from app.config import get_settings
        from app.domain.circuit_breaker_policy import (
            DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
            DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            DEFAULT_HALF_OPEN_PROBE_TIMEOUT_SECONDS,
            CounterpartyCircuitBreakerPolicy,
        )

        settings = get_settings()
        server_enabled = bool(getattr(settings, "cfi_circuit_breaker_enabled", False))
        server_threshold = int(
            getattr(settings, "cfi_circuit_breaker_failure_threshold", DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD)
        )
        server_cooldown = float(
            getattr(settings, "cfi_circuit_breaker_cooldown_seconds", DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS)
        )
        half_open_timeout = float(
            getattr(settings, "cfi_circuit_breaker_half_open_probe_timeout_seconds", DEFAULT_HALF_OPEN_PROBE_TIMEOUT_SECONDS)
        )

        req_enabled = getattr(req, "circuit_breaker_enabled", None)
        req_threshold = getattr(req, "circuit_breaker_failure_threshold", None)
        req_cooldown = getattr(req, "circuit_breaker_cooldown_seconds", None)

        if server_enabled:
            # Server control is authoritative: client cannot disable or weaken
            enabled = True
            if req_threshold is not None:
                failure_threshold = min(int(req_threshold), server_threshold)
            else:
                failure_threshold = server_threshold

            if req_cooldown is not None:
                cooldown_seconds = max(float(req_cooldown), server_cooldown)
            else:
                cooldown_seconds = server_cooldown
        else:
            # Server default is disabled: allow explicit client opt-in
            enabled = bool(req_enabled) if req_enabled is not None else False
            failure_threshold = int(req_threshold) if req_threshold is not None else server_threshold
            cooldown_seconds = float(req_cooldown) if req_cooldown is not None else server_cooldown

        return CounterpartyCircuitBreakerPolicy(
            enabled=enabled,
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
            half_open_probe_timeout_seconds=half_open_timeout,
        )

    def _is_verified_operational_signal(self, signal: Any) -> bool:
        """Verify that signal satisfies the authentic operational result-admission contract.

        Ensures simulated research requests or unverified responses cannot poison or reset
        authoritative operational worker availability state (Correction B).
        """
        if not isinstance(signal, CrossBankIntelligenceSignal):
            return False
        if signal.execution_mode != "VERIFIED_OPERATIONAL":
            return False
        if not signal.is_authoritative:
            return False
        return not getattr(signal, "is_synthetic", False)


    def execute_two_bank_private_matching(
        self,
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None = None,
        principal: AuthenticatedPrincipal | None = None,
        actor: str | None = None,
        timeout: float | None = None,
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
            timeout=timeout,
        )

    def _execute_verified_operational_psi(
        self,
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
        timeout: float | None = None,
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
            consortium_id=clean_consortium_id,
        )

        active_pilot_id: str | None = None
        reservation_id: str | None = None
        committed_res_id: str | None = None
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

        effective_timeout = timeout if timeout is not None else getattr(req, "timeout_seconds", None)
        est_req = req.model_copy(
            update={
                "target_bank_id": clean_target,
                "target_bank_ids": [clean_target],
                "execution_mode": "VERIFIED_OPERATIONAL",
                "use_established_psi": True,
                "timeout_seconds": effective_timeout,
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
        t0_mono = time.monotonic()
        target_deadline_mono = (
            t0_mono + float(effective_timeout) if effective_timeout is not None else None
        )
        match_timeout: float | None = None

        try:
            if coordinator_url:
                from app.application.services.isolated_matching_service import (
                    InstitutionalCoordinatorClient,
                )

                coord_client = InstitutionalCoordinatorClient(coordinator_url)

                # Monotonic per-target deadline propagation across network boundaries (Correction C)
                if target_deadline_mono is not None:
                    remaining_for_probe = target_deadline_mono - time.monotonic()
                    if remaining_for_probe <= 0:
                        raise CounterpartyDeadlineExceededError(
                            message=f"Execution deadline exceeded before coordinator probe for '{clean_target}'.",
                            target_bank_id=clean_target,
                            is_pre_dispatch=True,
                        )
                    probe_timeout = min(5.0, remaining_for_probe)
                else:
                    probe_timeout = 5.0

                try:
                    coord_client.health_check(timeout=probe_timeout)
                except (CounterpartyTimeoutError, TimeoutError) as exc:
                    raise CounterpartyDeadlineExceededError(
                        message=f"Coordinator health probe timed out before dispatch for '{clean_target}'.",
                        target_bank_id=clean_target,
                        is_pre_dispatch=True,
                    ) from exc
                except CounterpartyWorkerUnavailableError:
                    raise
                except Exception as exc:
                    raise ConsortiumSecurityError(
                        f"Operational established PSI coordinator is unreachable or offline: {type(exc).__name__}. Failing closed."
                    ) from exc

                # Recalculate remaining time before the matching HTTP request
                if target_deadline_mono is not None:
                    remaining_for_match = target_deadline_mono - time.monotonic()
                    if remaining_for_match <= 0:
                        raise CounterpartyDeadlineExceededError(
                            message=f"Execution deadline exceeded before matching invocation for '{clean_target}'.",
                            target_bank_id=clean_target,
                            is_pre_dispatch=True,
                        )
                    match_timeout = float(remaining_for_match)
                else:
                    match_timeout = float(effective_timeout) if effective_timeout is not None else None

                signal = coord_client.execute_match(
                    req=est_req,
                    auth_token=resolved_auth_token,
                    actor=audited_actor,
                    timeout=match_timeout,
                )

                resolved_receipt_id = (
                    getattr(signal, "receipt_id", None)
                    or (signal.provenance.get("receipt_id") if isinstance(getattr(signal, "provenance", None), dict) else None)
                    or ""
                )

                # Kill-switch revalidation immediately before authoritative result admission
                if self._before_result_admission_hook is not None and active_pilot_id:
                    try:
                        self._before_result_admission_hook(active_pilot_id, signal.signal_id, resolved_receipt_id)
                    except TypeError:
                        self._before_result_admission_hook(active_pilot_id)

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
                    record["admission_status"] = "PENDING_COMMIT"
                    record["publication_status"] = "PENDING_COMMIT"
                    record["is_authoritative"] = False
                    record["is_attachable"] = False

                if pilot_svc and active_pilot_id and reservation_id:
                    duration_ms = (time.perf_counter() - t0) * 1000.0
                    try:
                        pilot_svc.commit_query_reservation(
                            reservation_id=reservation_id,
                            pilot_id=active_pilot_id,
                            receipt_id=resolved_receipt_id,
                            latency_ms=duration_ms,
                            match_confirmed=bool(getattr(signal, "match_confirmed", False)),
                            signal_id=signal.signal_id,
                            case_id=req_case_id,
                            content_hash=record.get("content_hash"),
                        )
                    except Exception as commit_exc:
                        record["admission_status"] = "REJECTED"
                        record["publication_status"] = "COMMIT_FAILED"
                        record["is_authoritative"] = False
                        record["is_attachable"] = False
                        record["rejection_reason"] = f"Reservation commit failure: {commit_exc}"
                        self._signal_store.set(signal.signal_id, record)
                        raise

                    committed_res_id = reservation_id
                    reservation_id = None  # prevent release_query_reservation from treating it as uncommitted in-flight

                    # Post-commit publication boundary:
                    # Test-only synchronization hook after reservation commit and before publication authorization
                    if self._before_signal_publication_hook is not None:
                        try:
                            self._before_signal_publication_hook(active_pilot_id, signal.signal_id, resolved_receipt_id)
                        except TypeError:
                            self._before_signal_publication_hook(active_pilot_id)

                    try:
                        # 1. Prepare publication under store lock (verifies pilot ACTIVE, binds signal/receipt, generates fence)
                        pub_fence = pilot_svc.prepare_query_publication(
                            reservation_id=committed_res_id,
                            pilot_id=active_pilot_id,
                            signal_id=signal.signal_id,
                            receipt_id=resolved_receipt_id,
                        )

                        # Test-only synchronization hook after SQLite publication preparation/authorization but before publication finalization
                        if self._after_publication_preparation_hook is not None:
                            try:
                                self._after_publication_preparation_hook(active_pilot_id, signal.signal_id, resolved_receipt_id, committed_res_id)
                            except TypeError:
                                self._after_publication_preparation_hook(active_pilot_id, signal.signal_id, resolved_receipt_id)

                        # 2. Definitively finalize publication linearization point under store lock
                        pilot_svc.finalize_query_publication(
                            reservation_id=committed_res_id,
                            pilot_id=active_pilot_id,
                            signal_id=signal.signal_id,
                            receipt_id=resolved_receipt_id,
                            publication_fence=pub_fence,
                        )
                    except Exception as pub_exc:
                        record["admission_status"] = "REJECTED"
                        record["publication_status"] = "PUBLICATION_REJECTED"
                        record["is_authoritative"] = False
                        record["is_attachable"] = False
                        record["rejection_reason"] = f"Publication rejected: {pub_exc}"
                        self._signal_store.set(signal.signal_id, record)
                        raise

                # 3. Publish authoritative record to Redis only after publication finalization
                record["admission_status"] = "COMMITTED"
                record["publication_status"] = "PUBLISHED"
                record["is_authoritative"] = True
                record["is_attachable"] = True
                record["committed_at"] = datetime.datetime.now(UTC).isoformat()
                record["published_at"] = datetime.datetime.now(UTC).isoformat()
                try:
                    self._signal_store.set(signal.signal_id, record)
                except Exception as write_exc:
                    if active_pilot_id and committed_res_id:
                        pilot_svc.record_publication_failure(
                            reservation_id=committed_res_id,
                            pilot_id=active_pilot_id,
                            error_msg=str(write_exc),
                        )
                    raise
                return signal

            if self.matching_coordinator is not None:
                try:
                    self.matching_coordinator.get_worker(clean_requesting)
                    self.matching_coordinator.get_worker(clean_target)
                    if target_deadline_mono is not None:
                        remaining_for_match = target_deadline_mono - time.monotonic()
                        if remaining_for_match <= 0:
                            raise CounterpartyDeadlineExceededError(
                                message=f"Execution deadline exceeded before in-process matching for '{clean_target}'.",
                                target_bank_id=clean_target,
                                is_pre_dispatch=True,
                            )
                        match_timeout = float(remaining_for_match)
                    else:
                        match_timeout = float(effective_timeout) if effective_timeout is not None else None
                    signal = self.matching_coordinator.execute_two_bank_private_matching(
                        req=est_req,
                        auth_token=resolved_auth_token,
                        actor=audited_actor,
                        timeout=match_timeout,
                    )

                    resolved_receipt_id = (
                        getattr(signal, "receipt_id", None)
                        or (signal.provenance.get("receipt_id") if isinstance(getattr(signal, "provenance", None), dict) else None)
                        or ""
                    )

                    # Kill-switch revalidation immediately before authoritative result admission
                    if self._before_result_admission_hook is not None and active_pilot_id:
                        try:
                            self._before_result_admission_hook(active_pilot_id, signal.signal_id, resolved_receipt_id)
                        except TypeError:
                            self._before_result_admission_hook(active_pilot_id)

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
                        record["admission_status"] = "PENDING_COMMIT"
                        record["publication_status"] = "PENDING_COMMIT"
                        record["is_authoritative"] = False
                        record["is_attachable"] = False

                    if pilot_svc and active_pilot_id and reservation_id:
                        duration_ms = (time.perf_counter() - t0) * 1000.0
                        try:
                            pilot_svc.commit_query_reservation(
                                reservation_id=reservation_id,
                                pilot_id=active_pilot_id,
                                receipt_id=resolved_receipt_id,
                                latency_ms=duration_ms,
                                match_confirmed=bool(getattr(signal, "match_confirmed", False)),
                                signal_id=signal.signal_id,
                                case_id=req_case_id,
                                content_hash=record.get("content_hash"),
                            )
                        except Exception as commit_exc:
                            record["admission_status"] = "REJECTED"
                            record["publication_status"] = "COMMIT_FAILED"
                            record["is_authoritative"] = False
                            record["is_attachable"] = False
                            record["rejection_reason"] = f"Reservation commit failure: {commit_exc}"
                            self._signal_store.set(signal.signal_id, record)
                            raise

                        committed_res_id = reservation_id
                        reservation_id = None  # prevent release_query_reservation from treating it as uncommitted in-flight

                        # Post-commit publication boundary:
                        # Test-only synchronization hook after reservation commit and before publication authorization
                        if self._before_signal_publication_hook is not None:
                            try:
                                self._before_signal_publication_hook(active_pilot_id, signal.signal_id, resolved_receipt_id)
                            except TypeError:
                                self._before_signal_publication_hook(active_pilot_id)

                        try:
                            # 1. Prepare publication under store lock (verifies pilot ACTIVE, binds signal/receipt, generates fence)
                            pub_fence = pilot_svc.prepare_query_publication(
                                reservation_id=committed_res_id,
                                pilot_id=active_pilot_id,
                                signal_id=signal.signal_id,
                                receipt_id=resolved_receipt_id,
                            )

                            # Test-only synchronization hook after SQLite publication preparation/authorization but before publication finalization
                            if self._after_publication_preparation_hook is not None:
                                try:
                                    self._after_publication_preparation_hook(active_pilot_id, signal.signal_id, resolved_receipt_id, committed_res_id)
                                except TypeError:
                                    self._after_publication_preparation_hook(active_pilot_id, signal.signal_id, resolved_receipt_id)

                            # 2. Definitively finalize publication linearization point under store lock
                            pilot_svc.finalize_query_publication(
                                reservation_id=committed_res_id,
                                pilot_id=active_pilot_id,
                                signal_id=signal.signal_id,
                                receipt_id=resolved_receipt_id,
                                publication_fence=pub_fence,
                            )
                        except Exception as pub_exc:
                            record["admission_status"] = "REJECTED"
                            record["publication_status"] = "PUBLICATION_REJECTED"
                            record["is_authoritative"] = False
                            record["is_attachable"] = False
                            record["rejection_reason"] = f"Publication rejected: {pub_exc}"
                            self._signal_store.set(signal.signal_id, record)
                            raise

                    # 3. Publish authoritative record to Redis only after publication finalization
                    record["admission_status"] = "COMMITTED"
                    record["publication_status"] = "PUBLISHED"
                    record["is_authoritative"] = True
                    record["is_attachable"] = True
                    record["committed_at"] = datetime.datetime.now(UTC).isoformat()
                    record["published_at"] = datetime.datetime.now(UTC).isoformat()
                    try:
                        self._signal_store.set(signal.signal_id, record)
                    except Exception as write_exc:
                        if active_pilot_id and committed_res_id:
                            pilot_svc.record_publication_failure(
                                reservation_id=committed_res_id,
                                pilot_id=active_pilot_id,
                                error_msg=str(write_exc),
                            )
                        raise
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

        is_auth, auth_reason, resolved_grant = (
            self.consortium_service.resolve_pairwise_authorization(
                consortium_id=clean_consortium_id,
                requesting_bank_id=clean_requesting,
                counterpart_bank_id=clean_target,
                purpose=expected_req.purpose,
            )
        )
        if not is_auth or not resolved_grant:
            raise ConsortiumSecurityError(
                f"Adoption rejection: Pairwise operational authorization failed for {clean_requesting} <-> {clean_target}: {auth_reason}"
            )

        grant_id = str(resolved_grant.get("grant_id", "")).strip()
        grant_revision = int(resolved_grant.get("revision", 1))
        grant_scope_digest = str(resolved_grant.get("scope_digest", "")).strip()
        if not grant_scope_digest:
            from app.application.services.consortium_service import (
                compute_operational_grant_scope_digest,
            )
            grant_scope_digest = compute_operational_grant_scope_digest(
                grant_id=grant_id,
                revision=grant_revision,
                participating_banks=list(resolved_grant.get("participating_banks", [clean_requesting, clean_target])),
                allowed_purposes=list(resolved_grant.get("allowed_purposes", [expected_req.purpose])),
                institution_jurisdictions=dict(resolved_grant.get("institution_jurisdictions", {clean_requesting: "EU", clean_target: "EU"})),
                allowed_jurisdictions=list(resolved_grant.get("allowed_jurisdictions", ["EU", "US"])),
                allow_cross_border=bool(resolved_grant.get("allow_cross_border", False)),
                allow_institution_identity_sharing=bool(resolved_grant.get("allow_institution_identity_sharing", False)),
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

        self._admitted_receipt_ids.add(receipt_id)
        self._admitted_receipts_store.set(
            receipt_id,
            {
                "admitted_at": now.isoformat(),
                "signal_id": signal.signal_id,
                "receipt_id": receipt_id,
                "content_hash": content_hash,
                "requesting_bank_id": clean_requesting,
                "target_bank_id": clean_target,
                "consortium_id": clean_consortium_id,
                "query_entity_id": expected_req.query_entity_id,
                "execution_mode": "VERIFIED_OPERATIONAL",
                "assurance_level": signal_dict.get("assurance_level", "REVIEWED_ESTABLISHED_PSI_VERIFIED"),
                "grant_id": ticket.get("grant_id"),
                "ticket_id": ticket.get("ticket_id"),
            },
        )

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
        timeout: float | None = None,
        _circuit_breaker_managed: bool = False,
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

        cb_probe_id: str | None = None
        cb_policy_single: CounterpartyCircuitBreakerPolicy | None = None
        clean_target_single: str | None = None

        if req.target_bank_id:
            clean_target_single = req.target_bank_id.lower().strip()
            if not _circuit_breaker_managed:
                cb_policy_single = self._resolve_circuit_breaker_policy(req)
                if cb_policy_single.enabled:
                    can_dispatch, cb_state, cb_probe_or_reason = (
                        self.circuit_breaker_store.evaluate_and_acquire_dispatch(
                            consortium_id=clean_consortium_id,
                            requesting_bank_id=clean_requesting,
                            target_bank_id=clean_target_single,
                            policy=cb_policy_single,
                            allow_probe=(req.execution_mode == "VERIFIED_OPERATIONAL"),
                        )
                    )
                    if not can_dispatch:
                        raise CounterpartyCircuitBreakerOpenError(
                            message=f"Circuit breaker is OPEN for counterpart '{clean_target_single}' ({cb_probe_or_reason}). Dispatch rejected.",
                            target_bank_id=clean_target_single,
                            consortium_id=clean_consortium_id,
                            requesting_bank_id=clean_requesting,
                        )
                    if cb_state == CircuitState.HALF_OPEN.value and cb_probe_or_reason:
                        cb_probe_id = cb_probe_or_reason

        try:
            if req.execution_mode == "VERIFIED_OPERATIONAL":
                if not req.target_bank_id:
                    raise RuntimeError(
                        "Operational privacy attestation unavailable: Target counterpart institution (target_bank_id) missing, "
                        "and hardware enclaves (Intel SGX) or distributed multi-party cryptographic PSI across independent "
                        "physical bank nodes are not available in this software runtime. Operation fails closed to prevent fabricated operational claims."
                    )
                signal = self._execute_verified_operational_psi(
                    req=req,
                    auth_token=auth_token,
                    auth_principal=auth_principal,
                    audited_actor=audited_actor,
                )
            else:
                signal = self._execute_simulated_research_query(
                    req=req,
                    auth_token=auth_token,
                    auth_principal=auth_principal,
                    audited_actor=audited_actor,
                    clean_requesting=clean_requesting,
                    clean_consortium_id=clean_consortium_id,
                )

            if not _circuit_breaker_managed and cb_policy_single and cb_policy_single.enabled and clean_target_single:
                if req.execution_mode == "VERIFIED_OPERATIONAL" and self._is_verified_operational_signal(signal):
                    self.circuit_breaker_store.record_success(
                        consortium_id=clean_consortium_id,
                        requesting_bank_id=clean_requesting,
                        target_bank_id=clean_target_single,
                        probe_id=cb_probe_id,
                    )
                elif cb_probe_id:
                    self.circuit_breaker_store.record_probe_aborted(
                        consortium_id=clean_consortium_id,
                        requesting_bank_id=clean_requesting,
                        target_bank_id=clean_target_single,
                        probe_id=cb_probe_id,
                        reason="SECURITY_REJECTED",
                        policy=cb_policy_single,
                    )
            return signal
        except Exception as exc:
            if not _circuit_breaker_managed and cb_policy_single and cb_policy_single.enabled and clean_target_single:
                if req.execution_mode == "VERIFIED_OPERATIONAL":
                    recoverable = self._classify_recoverable_counterparty_failure(clean_target_single, exc)
                    if recoverable is not None:
                        self.circuit_breaker_store.record_failure(
                            consortium_id=clean_consortium_id,
                            requesting_bank_id=clean_requesting,
                            target_bank_id=clean_target_single,
                            error_category=recoverable.error_category,
                            probe_id=cb_probe_id,
                            policy=cb_policy_single,
                        )
                    elif cb_probe_id:
                        self.circuit_breaker_store.record_probe_aborted(
                            consortium_id=clean_consortium_id,
                            requesting_bank_id=clean_requesting,
                            target_bank_id=clean_target_single,
                            probe_id=cb_probe_id,
                            reason="SECURITY_REJECTED",
                            policy=cb_policy_single,
                        )
                elif cb_probe_id:
                    self.circuit_breaker_store.record_probe_aborted(
                        consortium_id=clean_consortium_id,
                        requesting_bank_id=clean_requesting,
                        target_bank_id=clean_target_single,
                        probe_id=cb_probe_id,
                        reason="SECURITY_REJECTED",
                        policy=cb_policy_single,
                    )
            raise

    def _execute_simulated_research_query(
        self,
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
        clean_requesting: str,
        clean_consortium_id: str,
    ) -> CrossBankIntelligenceSignal:

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

        authoritative_record: dict[str, Any] = {
            "signal_id": signal_id,
            "signal_data": signal_dict,
            "requesting_bank_id": clean_requesting,
            "target_bank_id": (req.target_bank_id or "").lower().strip() if req.target_bank_id else None,
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

    @classmethod
    def _classify_recoverable_counterparty_failure(
        cls,
        target_bank_id: str,
        exc: Exception,
    ) -> CounterpartyExecutionFailure | None:
        """Classify genuine, recoverable transport/availability failures into sanitized failure records.

        Fail-Closed Security Boundary:
        Returns CounterpartyExecutionFailure ONLY for recognized transport, timeout, or worker
        availability errors. Returns None for ANY governance, authorization, cryptographic verification,
        ticket/receipt integrity, publication-admission, or unexpected generic errors so that the
        caller can immediately propagate the failure and terminate the batch request.
        """
        # 1. Never recover from governance, permission, or admission errors
        if cls._has_security_or_governance_cause(exc):
            return None

        # 2. Extract underlying root causes from exception chain (__cause__ or __context__)
        underlying_causes: list[BaseException] = []
        curr: BaseException | None = exc
        while curr is not None and len(underlying_causes) < 5:
            underlying_causes.append(curr)
            curr = curr.__cause__ or curr.__context__

        # 3. Check for explicit timeout errors
        is_timeout = False
        for cause in underlying_causes:
            if isinstance(
                cause,
                (
                    CounterpartyDeadlineExceededError,
                    CounterpartyTimeoutError,
                    TimeoutError,
                    socket.timeout,
                ),
            ):
                is_timeout = True
                break
            if isinstance(cause, urllib.error.URLError) and isinstance(
                getattr(cause, "reason", None), (TimeoutError, socket.timeout)
            ):
                is_timeout = True
                break

        # 4. Check for explicit worker unavailable / connection refused errors
        is_worker_unavailable = False
        for cause in underlying_causes:
            if isinstance(
                cause,
                (
                    CounterpartyWorkerUnavailableError,
                    ConnectionRefusedError,
                    ConnectionResetError,
                    ConnectionAbortedError,
                    BrokenPipeError,
                    ConnectionError,
                    http.client.RemoteDisconnected,
                    socket.gaierror,
                    socket.herror,
                ),
            ):
                is_worker_unavailable = True
                break
            if isinstance(cause, urllib.error.URLError):
                reason = getattr(cause, "reason", None)
                if isinstance(
                    reason,
                    (
                        ConnectionRefusedError,
                        ConnectionResetError,
                        ConnectionAbortedError,
                        BrokenPipeError,
                        ConnectionError,
                        http.client.RemoteDisconnected,
                        socket.gaierror,
                        socket.herror,
                    ),
                ):
                    is_worker_unavailable = True
                    break
                if not isinstance(cause, urllib.error.HTTPError):
                    is_worker_unavailable = True
                    break

        # 5. Check for explicit HTTP gateway / service unavailable (502, 503, 504)
        is_transport_failure = False
        for cause in underlying_causes:
            if isinstance(cause, CounterpartyTransportError):
                is_transport_failure = True
                break
            if isinstance(cause, urllib.error.HTTPError) and cause.code in (502, 503, 504):
                is_transport_failure = True
                break

        # 6. Check for coordinator wrapped message for worker unavailability
        # When coordinator executes in Docker, it wraps worker unreachable errors as:
        # "Institutional bank worker for '{target_bank_id}' is unreachable or timed out:"
        if not is_worker_unavailable and not is_timeout and not is_transport_failure:
            exc_str = str(exc)
            worker_prefix = (
                f"institutional bank worker for '{target_bank_id.lower()}' is unreachable or timed out:"
            )
            if worker_prefix in exc_str.lower():
                if "timed out" in exc_str.lower() or "timeout" in exc_str.lower():
                    is_timeout = True
                else:
                    is_worker_unavailable = True

        if is_timeout:
            is_pre_dispatch = any(
                getattr(c, "is_pre_dispatch", False) for c in underlying_causes
            )
            msg = (
                f"Execution deadline exceeded before counterpart dispatch for '{target_bank_id}'."
                if is_pre_dispatch
                else f"Execution timed out communicating with counterpart worker for '{target_bank_id}'."
            )
            return CounterpartyExecutionFailure(
                target_bank_id=target_bank_id,
                error_category="TIMEOUT",
                error_message=msg,
                failed_at=datetime.datetime.now(UTC).isoformat(),
                retryable=True,
            )

        if is_worker_unavailable:
            return CounterpartyExecutionFailure(
                target_bank_id=target_bank_id,
                error_category="WORKER_UNAVAILABLE",
                error_message=f"Counterpart worker node for institution '{target_bank_id}' is unreachable or offline.",
                failed_at=datetime.datetime.now(UTC).isoformat(),
                retryable=True,
            )

        if is_transport_failure:
            return CounterpartyExecutionFailure(
                target_bank_id=target_bank_id,
                error_category="TRANSPORT_FAILURE",
                error_message=f"Transport service for counterpart institution '{target_bank_id}' is temporarily unavailable.",
                failed_at=datetime.datetime.now(UTC).isoformat(),
                retryable=True,
            )

        # Non-recoverable: Governance rejections, cryptographic evidence mismatches,
        # ticket/receipt mismatches, admission failures, or unexpected errors return None.
        return None

    @classmethod
    def _sanitize_counterparty_failure(
        cls,
        target_bank_id: str,
        exc: Exception,
    ) -> CounterpartyExecutionFailure:
        """Construct a sanitized, non-leaking execution failure record for an authorized counterpart.

        Only recognized transport, timeout, or worker availability failures may be sanitized.
        Unexpected or security-critical exceptions fail closed immediately.
        """
        classified = cls._classify_recoverable_counterparty_failure(
            target_bank_id=target_bank_id,
            exc=exc,
        )
        if classified is not None:
            return classified

        # Fail-closed: If invoked directly on non-recoverable exception, do NOT invent EXECUTION_ERROR
        raise exc

    @staticmethod
    def _is_governance_authorization_failure(exc: BaseException) -> bool:
        """Determine whether an error represents a fail-closed governance/authorization rejection.

        Preserved for backward-compatibility; in the revised architecture, any failure that is
        not explicitly a recognized recoverable transport error is treated as fail-closed.
        """
        if isinstance(exc, PermissionError):
            return True
        msg = str(exc).lower()
        gov_markers = (
            "revoked",
            "evicted",
            "not an active member",
            "non-active status",
            "pairwise operational authorization rejected",
            "policy rejected",
            "participation authority denied",
            "grant revision mismatch",
            "scope digest mismatch",
            "impersonation",
            "cross-tenant",
            "pilot governance",
            "not found in institution",
            "terminal state",
            "hardware attestation unavailable",
            "simulation mode is strictly prohibited",
            "arbitrary privacy-hash",
            "adoption rejection",
            "publication rejected",
            "reservation commit failure",
            "cryptographic",
            "signature",
        )
        return any(marker in msg for marker in gov_markers)

    @classmethod
    def _is_security_or_governance_failure(cls, exc: BaseException) -> bool:
        """Determine whether an error represents a non-recoverable security, governance,
        cryptographic integrity, ticket/receipt, or authorization failure.
        """
        if isinstance(exc, PermissionError):
            return True
        if cls._is_governance_authorization_failure(exc):
            return True
        return isinstance(exc, ConsortiumSecurityError) and not isinstance(
            exc,
            (
                CounterpartyTimeoutError,
                CounterpartyWorkerUnavailableError,
                CounterpartyTransportError,
            ),
        )

    @classmethod
    def _has_security_or_governance_cause(cls, exc: BaseException) -> bool:
        """Check whether the exception or any cause/context in its chain represents a security failure."""
        curr: BaseException | None = exc
        depth = 0
        while curr is not None and depth < 10:
            if cls._is_security_or_governance_failure(curr):
                return True
            curr = curr.__cause__ or curr.__context__
            depth += 1
        return False

    def query_multi_counterparty_intelligence(
        self,
        req: MultiCounterpartyIntelligenceQueryRequest,
        auth_token: str | None = None,
        principal: AuthenticatedPrincipal | None = None,
        actor: str | None = None,
        idempotency_key: str | None = None,
    ) -> MultiCounterpartyIntelligenceQueryResponse:
        """Execute authorized multi-counterparty cross-bank intelligence queries.

        Enforces:
        - Authenticated institutional principal bound to requesting bank.
        - Permitted intelligence query analyst roles.
        - Actor integrity.
        - Requesting bank entity ownership in local registry.
        - Active consortium membership and absence of eviction.
        - Explicit, non-empty, distinct target_bank_ids without self-targeting.
        - Independent pre-flight pairwise governance authorization for EVERY target.
          Fails closed immediately if any target lacks bilateral authorization; never silently omits.
        - Optional explicit idempotency key with atomic concurrency-safe reservation acquisition.
        - Automatic rejection of conflicting payloads or principals under the same key.
        - Revalidation of active consortium membership, bilateral grants, and revisions on cached replay.
        - Independent operational OpenMined PSI execution per target bank.
        - Per-signal cryptographic verification, admission, and linearized publication.
        - Separately identifiable, sanitized results returned per counterpart.
        - Explicit, typed per-counterparty execution outcomes (SUCCESS / FAILED).
        - Preservation of independent results when one counterpart experiences transport / worker failure.
        - Overall query execution outcome (COMPLETED, PARTIAL_FAILURE, or FAILED).
        - Absolute prohibition of fabricated signals or match statuses for failed counterpart executions.
        """
        clean_requesting = req.requesting_bank_id.lower().strip()
        clean_consortium_id = req.consortium_id.lower().strip()
        clean_purpose = req.purpose.strip()

        # 1. Authoritative Institutional Principal & Role Verification
        auth_principal = self._authenticate_principal(principal=principal, auth_token=auth_token)
        caller_bank = (auth_principal.tenant_id or "").lower().strip()

        if not auth_principal.is_privileged and caller_bank != clean_requesting:
            raise PermissionError(
                f"Institutional impersonation rejected: Authenticated principal belongs to '{caller_bank}', "
                f"cannot execute multi-counterparty queries on behalf of institution '{req.requesting_bank_id}'."
            )

        if not any(r in PERMITTED_INTELLIGENCE_ROLES for r in auth_principal.roles):
            raise PermissionError(
                f"Principal '{auth_principal.principal_id}' lacks permitted intelligence query role. "
                f"Permitted roles: {sorted(PERMITTED_INTELLIGENCE_ROLES)}."
            )

        audited_actor = auth_principal.principal_id
        if actor and actor.lower().strip() != audited_actor.lower().strip():
            raise PermissionError(
                f"Caller-supplied actor identity ('{actor}') does not match authenticated principal ('{audited_actor}')."
            )

        # 2. Hardware Enclave & Operational Mode Attestation (Fail-Closed)
        if req.require_hardware_attestation:
            raise RuntimeError(
                "Hardware attestation unavailable: Physical hardware enclave is absent in this runtime. "
                "Production hardware attestation requirement failed closed."
            )

        if req.execution_mode == "SIMULATED_RESEARCH":
            from app.config import get_settings

            settings = get_settings()
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

        # 4. Local Entity Verification & Anti-Enumeration Protection
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

        query_privacy_id = source_entity.privacy_id
        if req.privacy_id and req.privacy_id.strip() != query_privacy_id:
            raise ValueError(
                f"Supplied privacy_id does not match the authoritative hash for local entity '{req.query_entity_id}'."
            )

        if req.execution_mode == "SIMULATED_RESEARCH" and bool(
            (source_entity.attributes or {}).get("is_production_operational", False)
        ):
            raise ConsortiumSecurityError(
                "Operational data leak prevention: Local entity is tagged as operational production data. "
                "Querying operational data via SIMULATED_RESEARCH mode is strictly prohibited."
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

        # 6. Target Institutions Validation & Pre-flight Pairwise Governance Verification
        if not req.target_bank_ids:
            raise ValueError("target_bank_ids must contain at least one counterpart institution.")
        if len(req.target_bank_ids) > 10:
            raise ValueError("target_bank_ids exceeds maximum permitted counterpart institutions (10).")

        clean_targets: list[str] = []
        seen_targets: set[str] = set()
        governance_snapshot: dict[str, Any] = {}
        grant_budgets: dict[str, int] = {}

        for t in req.target_bank_ids:
            clean_t = t.lower().strip()
            if not clean_t:
                raise ValueError("Target bank ID must be non-empty.")
            if clean_t in seen_targets:
                raise ValueError(f"Duplicate target bank ID '{clean_t}' detected in query request.")
            if clean_t == clean_requesting:
                raise ValueError(
                    f"Self-targeting is prohibited: requesting institution '{clean_requesting}' cannot be a target."
                )
            if clean_t in consortium.removed_members:
                raise ConsortiumSecurityError(
                    f"Target institution '{clean_t}' has been revoked or evicted from consortium '{clean_consortium_id}'."
                )
            if clean_t not in consortium.members:
                raise ConsortiumSecurityError(
                    f"Target institution '{clean_t}' is not an active member of consortium '{clean_consortium_id}'."
                )

            # Independent Pairwise Governance Authorization Pre-flight check
            is_auth, auth_reason, resolved_grant = (
                self.consortium_service.resolve_pairwise_authorization(
                    consortium_id=clean_consortium_id,
                    requesting_bank_id=clean_requesting,
                    counterpart_bank_id=clean_t,
                    purpose=clean_purpose,
                )
            )
            if not is_auth or not resolved_grant:
                raise ConsortiumSecurityError(
                    f"Pairwise operational authorization rejected for {clean_requesting} <-> {clean_t}: {auth_reason}"
                )

            governance_snapshot[clean_t] = {
                "grant_id": resolved_grant.get("grant_id"),
                "revision": resolved_grant.get("revision", 1),
                "scope_digest": resolved_grant.get("scope_digest", ""),
            }

            # Extract bilateral grant execution budget if explicitly declared
            grant_budget_val = (
                resolved_grant.get("execution_budget")
                or resolved_grant.get("max_executions")
                or resolved_grant.get("query_quota")
            )
            if grant_budget_val is not None:
                with contextlib.suppress(ValueError, TypeError):
                    grant_budgets[clean_t] = int(grant_budget_val)

            seen_targets.add(clean_t)
            clean_targets.append(clean_t)

        # 6.5. Durable Request Idempotency & Duplicate Execution Prevention
        effective_idem_key = (idempotency_key or req.idempotency_key or "").strip() or None
        idem_owner_id: str | None = None

        if effective_idem_key:
            req_fingerprint = compute_multi_counterparty_request_fingerprint(req=req)
            idem_owner_id = str(uuid.uuid4())
            acquire_status, cached_resp_data, cached_gov_snapshot = self.idempotency_store.acquire(
                consortium_id=clean_consortium_id,
                requesting_bank_id=clean_requesting,
                principal_id=audited_actor,
                idempotency_key=effective_idem_key,
                request_fingerprint=req_fingerprint,
                governance_snapshot=governance_snapshot,
                owner_id=idem_owner_id,
            )

            if acquire_status in (
                "INSTITUTION_MISMATCH",
                "CONSORTIUM_MISMATCH",
                "PRINCIPAL_MISMATCH",
                "FINGERPRINT_MISMATCH",
            ):
                raise CrossBankIdempotencyConflictError(
                    f"Idempotency key conflict: key '{effective_idem_key}' was previously used "
                    "with a different request payload, principal, or institution."
                )

            if acquire_status == "AMBIGUOUS_EXECUTION":
                raise CrossBankIdempotencyConflictError(
                    f"Previous execution under idempotency key '{effective_idem_key}' terminated "
                    "in an indeterminate state. Replay rejected."
                )

            if acquire_status == "IN_PROGRESS":
                # Wait briefly for in-progress concurrent request to resolve
                await_status, cached_resp_data, cached_gov_snapshot = self.idempotency_store.await_completion(
                    consortium_id=clean_consortium_id,
                    requesting_bank_id=clean_requesting,
                    principal_id=audited_actor,
                    idempotency_key=effective_idem_key,
                    request_fingerprint=req_fingerprint,
                    timeout=5.0,
                )
                if await_status == "COMPLETED":
                    acquire_status = "COMPLETED"
                elif await_status in ("FINGERPRINT_MISMATCH", "PRINCIPAL_MISMATCH"):
                    raise CrossBankIdempotencyConflictError(
                        f"Idempotency key conflict: key '{effective_idem_key}' was previously used "
                        "with a different request payload, principal, or institution."
                    )
                elif await_status == "AMBIGUOUS_EXECUTION":
                    raise CrossBankIdempotencyConflictError(
                        f"Concurrent execution under idempotency key '{effective_idem_key}' failed "
                        "in an indeterminate state. Replay rejected."
                    )
                else:
                    raise CrossBankIdempotencyConflictError(
                        f"A query with idempotency key '{effective_idem_key}' is currently in progress. "
                        "Concurrent duplicate execution prevented."
                    )

            if acquire_status == "COMPLETED" and cached_resp_data:
                # Revalidate current consortium membership, bilateral grants, revocation state, and revisions
                for clean_t in clean_targets:
                    if clean_t in consortium.removed_members or clean_t not in consortium.members:
                        raise ConsortiumSecurityError(
                            f"Counterpart '{clean_t}' is no longer an active member of consortium '{clean_consortium_id}'. "
                            "Cached idempotency replay rejected."
                        )
                    is_auth, auth_reason, current_grant = (
                        self.consortium_service.resolve_pairwise_authorization(
                            consortium_id=clean_consortium_id,
                            requesting_bank_id=clean_requesting,
                            counterpart_bank_id=clean_t,
                            purpose=clean_purpose,
                        )
                    )
                    if not is_auth or not current_grant:
                        raise ConsortiumSecurityError(
                            f"Pairwise operational authorization for {clean_requesting} <-> {clean_t} is no longer valid: {auth_reason}. "
                            "Cached idempotency replay rejected."
                        )
                    recorded_gov = (cached_gov_snapshot or {}).get(clean_t, {})
                    recorded_rev = recorded_gov.get("revision")
                    recorded_digest = recorded_gov.get("scope_digest")
                    current_rev = current_grant.get("revision", 1)
                    current_digest = current_grant.get("scope_digest", "")
                    if recorded_rev is not None and current_rev != recorded_rev:
                        raise ConsortiumSecurityError(
                            f"Operational grant for counterpart '{clean_t}' was modified since execution "
                            f"(revision {recorded_rev} -> {current_rev}). Stale result replay rejected."
                        )
                    if recorded_digest and current_digest != recorded_digest:
                        raise ConsortiumSecurityError(
                            f"Operational grant scope digest for counterpart '{clean_t}' was modified. "
                            "Stale result replay rejected."
                        )

                replayed_resp = MultiCounterpartyIntelligenceQueryResponse(**cached_resp_data)
                replayed_resp._idempotency_replayed = True
                return replayed_resp

        # 6.6. Durable Per-Counterparty Execution Budgets & Atomic Batch Reservation
        budget_batch_id = str(uuid.uuid4())
        has_budget, assigned_bid, exhausted_targets = self.budget_store.reserve_batch(
            consortium_id=clean_consortium_id,
            requesting_bank_id=clean_requesting,
            target_bank_ids=clean_targets,
            batch_id=budget_batch_id,
            grant_budgets=grant_budgets if grant_budgets else None,
        )
        if not has_budget:
            if effective_idem_key and idem_owner_id:
                self.idempotency_store.fail_or_abort(
                    consortium_id=clean_consortium_id,
                    requesting_bank_id=clean_requesting,
                    idempotency_key=effective_idem_key,
                    owner_id=idem_owner_id,
                    error_message=f"Execution budget quota exceeded for targets: {', '.join(sorted(exhausted_targets))}",
                    indeterminate=False,
                )
            raise CrossBankExecutionQuotaExceededError(
                f"Cross-bank execution quota exceeded for counterpart(s): {', '.join(sorted(exhausted_targets))} "
                f"in consortium '{clean_consortium_id}'. Batch rejected before dispatch.",
                consortium_id=clean_consortium_id,
                requesting_bank_id=clean_requesting,
                exhausted_targets=exhausted_targets,
            )

        # 6.7. Authoritative Execution Deadline Policy & Monotonic Time Horizon
        deadline_policy = self._resolve_execution_deadline_policy(req)
        cb_policy = self._resolve_circuit_breaker_policy(req)
        batch_start_mono = time.monotonic()
        batch_deadline_mono = batch_start_mono + deadline_policy.total_batch_timeout_seconds

        # 7. Execute Verified Operational Matching / Established PSI Per Counterpart
        dispatched_targets: list[str] = []
        completed_targets: list[str] = []
        try:
            results: dict[str, CrossBankIntelligenceSignal] = {}
            failures: dict[str, CounterpartyExecutionFailure] = {}
            execution_outcomes: dict[str, CounterpartyExecutionOutcome] = {}

            for clean_target in clean_targets:
                # Pre-dispatch execution deadline check using monotonic time
                now_mono = time.monotonic()
                remaining_batch_time = batch_deadline_mono - now_mono
                effective_target_timeout = min(
                    deadline_policy.per_counterparty_timeout_seconds,
                    remaining_batch_time,
                )
                if (
                    remaining_batch_time <= 0
                    or effective_target_timeout < MIN_PER_COUNTERPARTY_TIMEOUT_SECONDS
                ):
                    # Total batch deadline exhausted or insufficient remaining time (< 1.0s) before counterpart dispatch
                    self.budget_store.release_reservations(
                        batch_id=budget_batch_id,
                        targets=[clean_target],
                        reason=REASON_PRE_DISPATCH_DEADLINE_EXCEEDED,
                    )
                    pre_dispatch_failure = CounterpartyExecutionFailure(
                        target_bank_id=clean_target,
                        error_category="TIMEOUT",
                        error_message=f"Execution deadline exceeded before counterpart dispatch for '{clean_target}'.",
                        failed_at=datetime.datetime.now(UTC).isoformat(),
                        retryable=True,
                    )
                    failures[clean_target] = pre_dispatch_failure
                    execution_outcomes[clean_target] = CounterpartyExecutionOutcome(
                        target_bank_id=clean_target,
                        status="FAILED",
                        signal=None,
                        failure=pre_dispatch_failure,
                    )
                    logger.warning(
                        "Counterpart '%s' skipped: insufficient remaining execution deadline prior to dispatch (remaining=%.3fs, min=%.1fs).",
                        clean_target,
                        remaining_batch_time,
                        MIN_PER_COUNTERPARTY_TIMEOUT_SECONDS,
                    )
                    continue

                # Pre-dispatch circuit breaker check & recovery probe admission gate
                cb_probe_id: str | None = None
                if cb_policy.enabled:
                    can_dispatch, cb_state, cb_probe_or_reason = (
                        self.circuit_breaker_store.evaluate_and_acquire_dispatch(
                            consortium_id=clean_consortium_id,
                            requesting_bank_id=clean_requesting,
                            target_bank_id=clean_target,
                            policy=cb_policy,
                            allow_probe=(req.execution_mode == "VERIFIED_OPERATIONAL"),
                        )
                    )
                    if not can_dispatch:
                        self.budget_store.release_reservations(
                            batch_id=budget_batch_id,
                            targets=[clean_target],
                            reason=REASON_CIRCUIT_BREAKER_BLOCKED,
                        )
                        cb_failure = CounterpartyExecutionFailure(
                            target_bank_id=clean_target,
                            error_category="CIRCUIT_BREAKER_OPEN",
                            error_message=(
                                f"Circuit breaker is OPEN for counterpart '{clean_target}' "
                                f"({cb_probe_or_reason}). Dispatch rejected."
                            ),
                            failed_at=datetime.datetime.now(UTC).isoformat(),
                            retryable=False,
                        )
                        failures[clean_target] = cb_failure
                        execution_outcomes[clean_target] = CounterpartyExecutionOutcome(
                            target_bank_id=clean_target,
                            status="FAILED",
                            signal=None,
                            failure=cb_failure,
                        )
                        logger.warning(
                            "Counterpart '%s' skipped: circuit breaker is %s (%s).",
                            clean_target,
                            cb_state,
                            cb_probe_or_reason,
                        )
                        continue

                    if cb_state == CircuitState.HALF_OPEN.value and cb_probe_or_reason:
                        cb_probe_id = cb_probe_or_reason

                single_req = CrossBankIntelligenceQueryRequest(
                    requesting_bank_id=clean_requesting,
                    query_entity_id=req.query_entity_id,
                    privacy_id=req.privacy_id,
                    consortium_id=clean_consortium_id,
                    target_bank_id=clean_target,
                    case_id=req.case_id,
                    purpose=req.purpose,
                    execution_mode=req.execution_mode,
                    require_hardware_attestation=req.require_hardware_attestation,
                    use_established_psi=req.use_established_psi,
                    require_established_psi=req.require_established_psi,
                    protocol=req.protocol,
                    pilot_id=req.pilot_id,
                    timeout_seconds=effective_target_timeout,
                    circuit_breaker_enabled=req.circuit_breaker_enabled,
                    circuit_breaker_failure_threshold=req.circuit_breaker_failure_threshold,
                    circuit_breaker_cooldown_seconds=req.circuit_breaker_cooldown_seconds,
                )
                try:
                    dispatched_targets.append(clean_target)
                    if req.execution_mode == "VERIFIED_OPERATIONAL":
                        signal = self._execute_verified_operational_psi(
                            req=single_req,
                            auth_token=auth_token,
                            auth_principal=auth_principal,
                            audited_actor=audited_actor,
                        )
                    else:
                        signal = self.query_cross_bank_intelligence(
                            req=single_req,
                            auth_token=auth_token,
                            principal=auth_principal,
                            actor=audited_actor,
                            _circuit_breaker_managed=True,
                        )
                    results[clean_target] = signal
                    execution_outcomes[clean_target] = CounterpartyExecutionOutcome(
                        target_bank_id=clean_target,
                        status="SUCCESS",
                        signal=signal,
                        failure=None,
                    )
                    completed_targets.append(clean_target)
                    if cb_policy.enabled:
                        if req.execution_mode == "VERIFIED_OPERATIONAL" and self._is_verified_operational_signal(signal):
                            self.circuit_breaker_store.record_success(
                                consortium_id=clean_consortium_id,
                                requesting_bank_id=clean_requesting,
                                target_bank_id=clean_target,
                                probe_id=cb_probe_id,
                            )
                        elif cb_probe_id:
                            self.circuit_breaker_store.record_probe_aborted(
                                consortium_id=clean_consortium_id,
                                requesting_bank_id=clean_requesting,
                                target_bank_id=clean_target,
                                probe_id=cb_probe_id,
                                reason="SECURITY_REJECTED",
                                policy=cb_policy,
                            )
                except Exception as exc:
                    # Security-First Classification Rule (M6.4 Task 4 Correction A):
                    # 1. Explicit security, integrity, authorization, and governance failures take absolute
                    #    precedence over timeout causes and must NEVER be treated as pre-dispatch timeouts.
                    # 2. A pre-dispatch designation is trusted ONLY when the top-level exception itself is
                    #    a CounterpartyDeadlineExceededError with is_pre_dispatch=True and contains no security cause.
                    is_security_failure = self._has_security_or_governance_cause(exc)
                    is_pre_dispatch = (
                        not is_security_failure
                        and isinstance(exc, CounterpartyDeadlineExceededError)
                        and getattr(exc, "is_pre_dispatch", False) is True
                    )

                    if is_pre_dispatch:
                        if clean_target in dispatched_targets:
                            dispatched_targets.remove(clean_target)
                        if cb_policy.enabled and cb_probe_id:
                            self.circuit_breaker_store.record_probe_aborted(
                                consortium_id=clean_consortium_id,
                                requesting_bank_id=clean_requesting,
                                target_bank_id=clean_target,
                                probe_id=cb_probe_id,
                                reason="PROBE_FAILED",
                                policy=cb_policy,
                            )
                        self.budget_store.release_reservations(
                            batch_id=budget_batch_id,
                            targets=[clean_target],
                            reason=REASON_PRE_DISPATCH_DEADLINE_EXCEEDED,
                        )
                        pre_dispatch_failure = CounterpartyExecutionFailure(
                            target_bank_id=clean_target,
                            error_category="TIMEOUT",
                            error_message=f"Execution deadline exceeded before counterpart dispatch for '{clean_target}'.",
                            failed_at=datetime.datetime.now(UTC).isoformat(),
                            retryable=True,
                        )
                        failures[clean_target] = pre_dispatch_failure
                        execution_outcomes[clean_target] = CounterpartyExecutionOutcome(
                            target_bank_id=clean_target,
                            status="FAILED",
                            signal=None,
                            failure=pre_dispatch_failure,
                        )
                        logger.warning(
                            "Counterpart '%s' failed pre-dispatch deadline check: %s. Releasing reservation.",
                            clean_target,
                            exc,
                        )
                        continue
                    # Handle demonstrably pre-dispatch pilot quota rejections without consuming pairwise budget
                    if isinstance(exc, PilotQuotaExceededError):
                        if clean_target in dispatched_targets:
                            dispatched_targets.remove(clean_target)
                        if cb_policy.enabled and cb_probe_id:
                            self.circuit_breaker_store.record_probe_aborted(
                                consortium_id=clean_consortium_id,
                                requesting_bank_id=clean_requesting,
                                target_bank_id=clean_target,
                                probe_id=cb_probe_id,
                                reason="SECURITY_REJECTED",
                                policy=cb_policy,
                            )
                        logger.warning(
                            "Counterpart '%s' rejected in pre-dispatch pilot quota check: %s. Releasing reservation.",
                            clean_target,
                            exc,
                        )
                        undispatched = [t for t in clean_targets if t not in completed_targets]
                        if undispatched:
                            self.budget_store.release_reservations(
                                batch_id=budget_batch_id,
                                targets=undispatched,
                                reason=REASON_PILOT_PREFLIGHT_REJECTED,
                            )
                        if completed_targets:
                            self.budget_store.commit_reservations(
                                batch_id=budget_batch_id,
                                completed_targets=completed_targets,
                                reason=REASON_EXECUTION_COMPLETED,
                            )
                        raise

                    recoverable_failure = self._classify_recoverable_counterparty_failure(
                        target_bank_id=clean_target,
                        exc=exc,
                    )
                    if recoverable_failure is None:
                        # Security boundary: governance, authorization, cryptographic verification,
                        # ticket/receipt integrity, and admission failures MUST fail closed and terminate the request.
                        logger.error(
                            "Counterpart '%s' failed with indeterminate or security boundary error: %s",
                            clean_target,
                            exc,
                        )
                        if cb_policy.enabled and cb_probe_id:
                            self.circuit_breaker_store.record_probe_aborted(
                                consortium_id=clean_consortium_id,
                                requesting_bank_id=clean_requesting,
                                target_bank_id=clean_target,
                                probe_id=cb_probe_id,
                                reason="SECURITY_REJECTED",
                                policy=cb_policy,
                            )
                        undispatched = [t for t in clean_targets if t not in dispatched_targets]
                        if undispatched:
                            self.budget_store.release_reservations(
                                batch_id=budget_batch_id,
                                targets=undispatched,
                                reason=REASON_BATCH_ABORTED_BEFORE_DISPATCH,
                            )
                        self.budget_store.fail_closed_reservations(
                            batch_id=budget_batch_id,
                            targets=[clean_target],
                            reason=REASON_EXECUTION_INDETERMINATE,
                        )
                        if completed_targets:
                            self.budget_store.commit_reservations(
                                batch_id=budget_batch_id,
                                completed_targets=completed_targets,
                                reason=REASON_EXECUTION_COMPLETED,
                            )
                        raise

                    failures[clean_target] = recoverable_failure
                    execution_outcomes[clean_target] = CounterpartyExecutionOutcome(
                        target_bank_id=clean_target,
                        status="FAILED",
                        signal=None,
                        failure=recoverable_failure,
                    )
                    completed_targets.append(clean_target)
                    if cb_policy.enabled:
                        if req.execution_mode == "VERIFIED_OPERATIONAL":
                            self.circuit_breaker_store.record_failure(
                                consortium_id=clean_consortium_id,
                                requesting_bank_id=clean_requesting,
                                target_bank_id=clean_target,
                                error_category=recoverable_failure.error_category,
                                probe_id=cb_probe_id,
                                policy=cb_policy,
                            )
                        elif cb_probe_id:
                            self.circuit_breaker_store.record_probe_aborted(
                                consortium_id=clean_consortium_id,
                                requesting_bank_id=clean_requesting,
                                target_bank_id=clean_target,
                                probe_id=cb_probe_id,
                                reason="SECURITY_REJECTED",
                                policy=cb_policy,
                            )
                    logger.warning(
                        "Counterpart '%s' execution failed with recoverable transport error: [%s] %s",
                        clean_target,
                        recoverable_failure.error_category,
                        recoverable_failure.error_message,
                    )

            # All targets processed: commit all completed reservations for dispatched targets
            if completed_targets:
                self.budget_store.commit_reservations(
                    batch_id=budget_batch_id,
                    completed_targets=completed_targets,
                    reason=REASON_EXECUTION_COMPLETED,
                )

            # 8. Determine overall execution outcome across queried counterparts
            total_targets = len(clean_targets)
            success_count = len(results)
            if success_count == total_targets:
                overall_outcome: OverallQueryOutcome = "COMPLETED"
            elif success_count > 0:
                overall_outcome = "PARTIAL_FAILURE"
            else:
                overall_outcome = "FAILED"

            now_iso = datetime.datetime.now(UTC).isoformat()
            response_obj = MultiCounterpartyIntelligenceQueryResponse(
                query_id=str(uuid.uuid4()),
                consortium_id=clean_consortium_id,
                requesting_bank_id=clean_requesting,
                query_entity_id=req.query_entity_id,
                counterpart_count=len(clean_targets),
                overall_outcome=overall_outcome,
                outcome=overall_outcome,
                results=results,
                failures=failures,
                execution_outcomes=execution_outcomes,
                executed_at=now_iso,
            )

            # Record server-authoritative multi-counterparty batch execution record
            successful_signal_ids: dict[str, str] = {}
            receipt_ids: dict[str, str] = {}
            for t_bank, sig in results.items():
                successful_signal_ids[t_bank] = sig.signal_id
                prov = sig.provenance if isinstance(sig.provenance, dict) else {}
                rcp = prov.get("receipt_id")
                if rcp:
                    receipt_ids[t_bank] = str(rcp)

            execution_outcomes_dict: dict[str, Any] = {
                t_bank: outcome.model_dump(mode="json")
                for t_bank, outcome in execution_outcomes.items()
            }

            self.batch_store.record_batch(
                query_id=response_obj.query_id,
                requesting_bank_id=clean_requesting,
                consortium_id=clean_consortium_id,
                query_entity_id=req.query_entity_id,
                principal_id=auth_principal.principal_id,
                principal_tenant_id=caller_bank,
                case_id=req.case_id,
                target_bank_ids=clean_targets,
                execution_outcomes=execution_outcomes_dict,
                successful_signal_ids=successful_signal_ids,
                receipt_ids=receipt_ids,
                overall_outcome=overall_outcome,
            )

            if effective_idem_key and idem_owner_id:
                self.idempotency_store.complete(
                    consortium_id=clean_consortium_id,
                    requesting_bank_id=clean_requesting,
                    idempotency_key=effective_idem_key,
                    owner_id=idem_owner_id,
                    response_data=response_obj.model_dump(mode="json"),
                    governance_snapshot=governance_snapshot,
                )

            return response_obj
        except Exception as exec_exc:
            undispatched = [t for t in clean_targets if t not in dispatched_targets and t not in completed_targets]
            if undispatched:
                self.budget_store.release_reservations(
                    batch_id=budget_batch_id,
                    targets=undispatched,
                    reason=REASON_BATCH_ABORTED_BEFORE_DISPATCH,
                )
            if effective_idem_key and idem_owner_id:
                has_executed_side_effects = len(completed_targets) > 0 or len(dispatched_targets) > 0
                self.idempotency_store.fail_or_abort(
                    consortium_id=clean_consortium_id,
                    requesting_bank_id=clean_requesting,
                    idempotency_key=effective_idem_key,
                    owner_id=idem_owner_id,
                    error_message=str(exec_exc),
                    indeterminate=has_executed_side_effects,
                )
            raise


    def _audit_attachment_rejection(
        self,
        case_id: str,
        signal_id: str | None,
        reason: str,
        error_type: str,
        uploader: str = "unknown",
        caller_bank: str = "unknown",
        consortium_id: str | None = None,
    ) -> None:
        """Record attributable immutable audit event for rejected evidence attachment attempts."""
        try:
            actor = (
                f"user:{uploader}@{caller_bank}"
                if uploader not in ("unknown", "anonymous", "")
                else f"user:anonymous@{caller_bank}"
            )
            ImmutableAuditChain.get_instance().append_event(
                event_type="CROSS_BANK_INTELLIGENCE_EVIDENCE_ATTACHMENT_REJECTED",
                actor_id=actor,
                action=f"attach_cross_bank_evidence:{case_id}",
                resource_id=f"case:{case_id}",
                actor_institution=caller_bank or "unknown",
                details={
                    "case_id": case_id,
                    "signal_id": signal_id,
                    "rejection_reason": reason,
                    "error_type": error_type,
                    "consortium_id": consortium_id,
                },
            )
        except Exception as audit_err:
            logger.warning("Failed to record attachment rejection audit event: %s", audit_err)

    SAFE_TYPOLOGY_ALLOWLIST: set[str] = {
        "CROSS_BANK_ENTITY_OBSERVED",
        "MULTI_INSTITUTION_EXPOSURE",
        "RAPID_HOP_OVERLAP",
        "BURST_ACTIVITY_OBSERVED",
        "HIGH_RISK_CORRIDOR",
        "CIRCULAR_FLOW_OBSERVED",
        "VELOCITY_ANOMALY",
        "STRUCTURING_INDICATOR",
        "COUNTERPARTY_RISK_ELEVATED",
        "EXACT_MATCH_OBSERVED",
        "NO_MATCH_OBSERVED",
        "CROSS_INSTITUTION_ACTIVITY",
        "HIGH_RISK_JURISDICTION_ACTIVITY",
    }

    @classmethod
    def _project_evidence_signal_content(
        cls,
        signal_data: dict[str, Any] | None,
        disclosure_mode: str,
        target_bank_id: str | None,
    ) -> dict[str, Any]:
        """Project an allowlisted, safe signal content representation for case evidence persistence.

        Enforces consortium disclosure policy: In non-attributed (anonymized) disclosure modes,
        counterparty identities (e.g. target_bank_id, contributing_institutions, counterpart references
        in typologies and provenance) are strictly excluded from nested provenance, graph context,
        and payload fields.
        """
        if not isinstance(signal_data, dict):
            return {}

        is_attributed = (disclosure_mode == "ATTRIBUTED_INSTITUTIONS")
        clean_target = (target_bank_id or "").lower().strip()

        # Validate and sanitize typology indicators
        raw_typologies = signal_data.get("sanitized_typology_indicators") or []
        filtered_typologies: list[str] = []
        for typ in raw_typologies:
            if not isinstance(typ, str):
                continue
            typ_str = typ.strip()
            # In non-attributed mode, strictly reject any typology leaking counterpart identity
            if not is_attributed and clean_target and clean_target in typ_str.lower():
                continue
            # Validate against safe typology allowlist
            if typ_str in cls.SAFE_TYPOLOGY_ALLOWLIST or is_attributed:
                filtered_typologies.append(typ_str)

        projected: dict[str, Any] = {
            "signal_id": signal_data.get("signal_id"),
            "consortium_id": signal_data.get("consortium_id"),
            "query_entity_id": signal_data.get("query_entity_id"),
            "requesting_bank_id": signal_data.get("requesting_bank_id"),
            "privacy_hash_truncated": signal_data.get("privacy_hash_truncated"),
            "match_confirmed": bool(signal_data.get("match_confirmed", False)),
            "match_category": signal_data.get("match_category", "NO_MATCH"),
            "match_confidence": float(signal_data.get("match_confidence", 0.0)),
            "contributing_institutions_count": int(signal_data.get("contributing_institutions_count", 0)),
            "disclosure_mode": disclosure_mode,
            "sanitized_risk_level": signal_data.get("sanitized_risk_level", "UNOBSERVED"),
            "sanitized_typology_indicators": sorted(filtered_typologies),
            "execution_mode": signal_data.get("execution_mode", "VERIFIED_OPERATIONAL"),
            "assurance_level": signal_data.get("assurance_level", "REVIEWED_ESTABLISHED_PSI_VERIFIED"),
            "attestation_status": signal_data.get("attestation_status", "SOFTWARE_CONTAINER_VERIFIED"),
            "is_authoritative": True,
            "is_synthetic": False,
        }

        # Graph context: preserve topological indicators without institution identities
        raw_graph = signal_data.get("graph_context") or {}
        if isinstance(raw_graph, dict):
            projected["graph_context"] = {
                "has_incident_cross_bank_correlations": bool(raw_graph.get("has_incident_cross_bank_correlations", False)),
                "sanitized_degree_band": raw_graph.get("sanitized_degree_band", "ISOLATED"),
            }
        else:
            projected["graph_context"] = {}

        # Attribution-dependent projection
        if is_attributed:
            projected["target_bank_id"] = target_bank_id or signal_data.get("target_bank_id")
            projected["contributing_institutions"] = list(signal_data.get("contributing_institutions", []))
        else:
            projected["target_bank_id"] = None
            projected["contributing_institutions"] = []

        # Provenance: safe allowlisted references only (no secret signing keys or raw tickets)
        raw_prov = signal_data.get("provenance") or {}
        if isinstance(raw_prov, dict):
            sanitized_prov: dict[str, Any] = {
                "execution_mode": raw_prov.get("execution_mode", "VERIFIED_OPERATIONAL"),
                "assurance_level": raw_prov.get("assurance_level", "REVIEWED_ESTABLISHED_PSI_VERIFIED"),
                "executed_at": raw_prov.get("executed_at"),
                "disclosure_mode": disclosure_mode,
            }
            if is_attributed:
                sanitized_prov["receipt_id"] = raw_prov.get("receipt_id")
                sanitized_prov["ticket_id"] = raw_prov.get("ticket_id")
                sanitized_prov["grant_id"] = raw_prov.get("grant_id")
                sanitized_prov["grant_revision"] = raw_prov.get("grant_revision")
                sanitized_prov["target_bank_id"] = target_bank_id or raw_prov.get("target_bank_id")
                sanitized_prov["contributing_institutions"] = projected["contributing_institutions"]
            else:
                sanitized_prov["target_bank_id"] = None
                sanitized_prov["contributing_institutions"] = []
                sanitized_prov["receipt_id"] = None
                sanitized_prov["ticket_id"] = None
                sanitized_prov["grant_id"] = None
                sanitized_prov["grant_revision"] = None
                sanitized_prov["opaque_provenance_ref"] = f"prov_ref_{uuid.uuid4().hex[:16]}"
            projected["provenance"] = sanitized_prov

        return projected

    @staticmethod
    def _compute_evidence_attachment_fingerprint(
        case_id: str,
        signal_id: str,
        notes: str | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        """Compute a canonical SHA-256 fingerprint over case ID, signal ID, notes, and material payload fields."""
        canonical_data: dict[str, Any] = {
            "case_id": case_id.strip(),
            "signal_id": signal_id.strip(),
            "notes": notes.strip() if notes is not None else None,
        }
        if extra_payload:
            for k, v in sorted(extra_payload.items()):
                if k not in (
                    "auth_token",
                    "principal",
                    "idempotency_key",
                    "actor",
                    "caller_bank_id",
                    "case_bank",
                ):
                    canonical_data[k] = v
        serialized = json.dumps(canonical_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    @classmethod
    def _validate_anonymous_notes_policy(
        cls,
        notes: str | None,
        consortium: Any,
        requesting_bank: str,
        target_bank: str | None,
    ) -> None:
        """Enforce strict anonymity policy on free-text analyst notes for anonymous evidence admission.

        Rejects notes containing counterpart institution names, consortium member identities,
        or institutional identifier patterns (fail closed).
        """
        if not notes or not isinstance(notes, str):
            return

        clean_notes = notes.strip()
        if not clean_notes:
            return

        # 1. Identify forbidden counterparties & member banks
        forbidden_terms: set[str] = set()
        clean_req = requesting_bank.lower().strip()

        if target_bank:
            tb = target_bank.lower().strip()
            if tb and tb != clean_req:
                forbidden_terms.add(tb)

        # Extract consortium member banks
        c_members = getattr(consortium, "member_bank_ids", None) or []
        if not c_members and hasattr(consortium, "metadata") and isinstance(consortium.metadata, dict):
            c_members = consortium.metadata.get("member_bank_ids") or []
        for m in c_members:
            if isinstance(m, str):
                m_clean = m.lower().strip()
                if m_clean and m_clean != clean_req:
                    forbidden_terms.add(m_clean)

        # 2. Check for exact counterpart / member name occurrences (with word boundaries and separators)
        for term in forbidden_terms:
            variants = [
                re.escape(term),
                re.escape(term.replace("_", " ")),
                re.escape(term.replace("_", "-")),
                re.escape(term.replace("-", " ")),
            ]
            pattern = r"(?i)\b(" + "|".join(variants) + r")\b"
            if re.search(pattern, clean_notes):
                raise ConsortiumSecurityError(
                    f"Analyst notes violate anonymity policy for disclosure mode 'ANONYMOUS_SUMMARY': "
                    f"Counterpart or institution identifier '{term}' detected in free-text notes. "
                    "Anonymous case evidence strictly prohibits persisting counterparty identities."
                )

        # 3. Detect general institutional identifier patterns referring to non-requesting banks
        inst_pattern = r"(?i)\b(?:(?:bank|institution)[_\-]([a-zA-Z0-9]+)|(?:bank|institution)\s+([a-zA-Z0-9]{1,2}))\b"
        for match in re.finditer(inst_pattern, clean_notes):
            matched_id = match.group(0).lower().replace(" ", "_").replace("-", "_")
            req_normalized = clean_req.replace(" ", "_").replace("-", "_")
            sub_id = (match.group(1) or match.group(2) or "").lower()
            if matched_id != req_normalized and sub_id != req_normalized.split("_")[-1]:
                raise ConsortiumSecurityError(
                    f"Analyst notes violate anonymity policy for disclosure mode 'ANONYMOUS_SUMMARY': "
                    f"Institutional identifier '{match.group(0)}' detected in free-text notes. "
                    "Anonymous case evidence strictly prohibits persisting counterparty identities."
                )

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
        case_bank: str | None = None,
        **kwargs: Any,
    ) -> CrossBankEvidenceAttachResponse:
        """Attach verified authoritative cross-bank intelligence signal into case evidence ledger."""
        resolved_signal_id = signal_id or (signal.signal_id if signal else None)
        uploader = "unknown"
        caller_bank = "unknown"
        consortium_id: str | None = None

        try:
            # 1. Caller Authentication & Institutional Role Verification
            auth_principal = self._authenticate_principal(principal=principal, auth_token=auth_token)
            caller_bank = (auth_principal.tenant_id or "").lower().strip()
            if not caller_bank:
                raise PermissionError(
                    "Unauthenticated or unassociated principal: Institutional tenant_id is required for evidence attachment."
                )
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
            initial_case_version = getattr(case, "version", 1)

            eff_case_bank = str(getattr(case, "bank_id", "") or "").lower().strip()
            if not eff_case_bank:
                raise ValueError(f"Case '{case_id}' lacks authoritative institution binding (bank_id).")

            # Correction A: Strict Case-Write Tenant Ownership
            # Authoritative evidence writes require caller-bank ownership of the existing case
            # regardless of generalized cross-bank administrative roles (e.g. super_admin, cross_bank_investigator, compliance_auditor).
            if caller_bank != eff_case_bank:
                raise PermissionError(
                    f"Tenant isolation violation: Caller institution '{caller_bank}' cannot modify "
                    f"or attach evidence to case '{case_id}' owned by institution '{eff_case_bank}'. "
                    "Authoritative case evidence writes require caller-bank ownership regardless of administrative roles."
                )

            # 3. Retrieve Authoritative Signal Server-Side (Anti-Forgery & Tamper Check)
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
            recomputed_hash: str | None = None
            signal_data = record.get("signal_data")
            if signal_data and isinstance(signal_data, dict):
                recomputed_hash = hashlib.sha256(
                    json.dumps(signal_data, sort_keys=True).encode("utf-8")
                ).hexdigest()
                if record.get("content_hash") and recomputed_hash != record.get("content_hash"):
                    raise ConsortiumSecurityError(
                        f"Authoritative signal failed server-side tamper verification for '{resolved_signal_id}': "
                        f"canonical content digest mismatch. Stored record has been tampered with or corrupted."
                    )

            # 3b. Required Institutional and Principal Bindings
            sig_requesting_bank = str(record.get("requesting_bank_id", "")).lower().strip()
            if not sig_requesting_bank or sig_requesting_bank != eff_case_bank:
                raise PermissionError(
                    f"Cross-tenant signal violation: Authoritative signal '{resolved_signal_id}' was issued to institution "
                    f"'{sig_requesting_bank}', cannot be attached to case belonging to institution '{eff_case_bank}'."
                )

            consortium_id = str(record.get("consortium_id", "")).lower().strip()
            if not consortium_id:
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

                # Enforce durable reservation binding
                res_id = record.get("reservation_id")
                if not res_id:
                    raise ConsortiumSecurityError(
                        f"Signal '{resolved_signal_id}' governed by pilot '{pilot_id}' lacks durable execution reservation binding."
                    )
                durable_res = pilot_svc.store.get_query_reservation(res_id)
                if not durable_res:
                    raise ConsortiumSecurityError(
                        f"Durable execution reservation '{res_id}' for pilot '{pilot_id}' not found."
                    )
                res_status = durable_res.get("status")
                if res_status != "PUBLISHED":
                    raise ConsortiumSecurityError(
                        f"Execution reservation '{res_id}' for pilot '{pilot_id}' publication status is '{res_status}', "
                        f"not PUBLISHED. Evidence admission requires genuinely finalized authoritative publication."
                    )
                if durable_res.get("signal_id") != resolved_signal_id:
                    raise ConsortiumSecurityError(
                        f"Execution reservation '{res_id}' is bound to signal '{durable_res.get('signal_id')}', does not match '{resolved_signal_id}'."
                    )
                if durable_res.get("pilot_id") != pilot_id:
                    raise ConsortiumSecurityError(
                        f"Execution reservation '{res_id}' is bound to pilot '{durable_res.get('pilot_id')}', does not match '{pilot_id}'."
                    )
                record_rcp = record.get("receipt_id")
                if record_rcp and durable_res.get("receipt_id") and durable_res.get("receipt_id") != record_rcp:
                    raise ConsortiumSecurityError(
                        f"Execution reservation '{res_id}' receipt '{durable_res.get('receipt_id')}' does not match record receipt '{record_rcp}'."
                    )

            # 3c. Assurance Level & Execution Mode Boundary
            assurance_level = (
                (signal_data.get("assurance_level") if isinstance(signal_data, dict) else None)
                or record.get("assurance_level")
            )
            execution_mode = (
                (signal_data.get("execution_mode") if isinstance(signal_data, dict) else None)
                or record.get("execution_mode")
            )

            if (
                assurance_level in ("PROTOCOL_PROTOTYPE", "RESEARCH_PROTOTYPE", "LOCAL_PROTOTYPE")
                or execution_mode in ("RESEARCH_PROTOTYPE", "PROTOTYPE")
            ):
                raise ConsortiumSecurityError(
                    "Protocol prototype matching results cannot enter case evidence in any case state "
                    "(ordinary open, escalated, or SAR). Defensible evidence requires verified operational "
                    "execution and independent cryptographic audit."
                )

            if (
                execution_mode == "SIMULATED_RESEARCH"
                or record.get("is_synthetic") is True
                or record.get("is_authoritative") is False
            ):
                raise ConsortiumSecurityError(
                    "Simulated research signals cannot be attached to authoritative fraud "
                    "investigation evidence or regulatory filings."
                )

            # 3d. Admission State Transition Checks (Correction B: Strict Affirmative Values Required)
            adm_status = record.get("admission_status")
            if adm_status != "COMMITTED":
                raise ConsortiumSecurityError(
                    f"Authoritative signal '{resolved_signal_id}' admission status is '{adm_status}', "
                    f"not COMMITTED. Unfinalized, rejected, or absent admission states cannot enter case evidence."
                )

            pub_status = record.get("publication_status")
            if pub_status != "PUBLISHED":
                raise ConsortiumSecurityError(
                    f"Authoritative signal '{resolved_signal_id}' publication status is '{pub_status}', "
                    f"not PUBLISHED. Evidence admission requires genuinely finalized authoritative publication."
                )

            if record.get("is_attachable") is not True:
                raise ConsortiumSecurityError(
                    f"Authoritative signal '{resolved_signal_id}' is marked non-authoritative or unattachable (is_attachable={record.get('is_attachable')})."
                )

            if record.get("is_authoritative") is not True:
                raise ConsortiumSecurityError(
                    f"Authoritative signal '{resolved_signal_id}' lacks affirmative is_authoritative=True (got {record.get('is_authoritative')})."
                )

            if record.get("is_synthetic") is not False:
                raise ConsortiumSecurityError(
                    f"Authoritative signal '{resolved_signal_id}' has non-false is_synthetic flag (got {record.get('is_synthetic')})."
                )

            if execution_mode != "VERIFIED_OPERATIONAL":
                raise ConsortiumSecurityError(
                    f"Authoritative signal '{resolved_signal_id}' execution_mode must be 'VERIFIED_OPERATIONAL' (got '{execution_mode}')."
                )

            if isinstance(signal_data, dict):
                if signal_data.get("is_authoritative") is not True:
                    raise ConsortiumSecurityError(
                        f"Authoritative signal '{resolved_signal_id}' signal_data lacks affirmative is_authoritative=True."
                    )
                if signal_data.get("is_synthetic") is not False:
                    raise ConsortiumSecurityError(
                        f"Authoritative signal '{resolved_signal_id}' signal_data has non-false is_synthetic ({signal_data.get('is_synthetic')})."
                    )
                if signal_data.get("execution_mode") != "VERIFIED_OPERATIONAL":
                    raise ConsortiumSecurityError(
                        f"Authoritative signal '{resolved_signal_id}' signal_data execution_mode is not 'VERIFIED_OPERATIONAL' ({signal_data.get('execution_mode')})."
                    )

            # 3f. Revalidate Current Consortium Governance & Active Membership
            consortium = self.consortium_service.get_consortium(consortium_id)
            if not consortium:
                raise ConsortiumSecurityError(f"Consortium '{consortium_id}' no longer exists.")
            if consortium.status != ConsortiumStatus.ACTIVE:
                raise ConsortiumSecurityError(
                    f"Consortium '{consortium_id}' is in non-active status '{consortium.status.value}'."
                )
            if eff_case_bank in consortium.removed_members:
                raise ConsortiumSecurityError(
                    f"Institution '{eff_case_bank}' has been revoked/evicted from consortium '{consortium_id}'."
                )
            if eff_case_bank not in consortium.members:
                raise ConsortiumSecurityError(
                    f"Institution '{eff_case_bank}' is not an active member of consortium '{consortium_id}'."
                )

            # 3g. Authoritative Issuance / Cryptographic Admission Chain Verification
            receipt_id = (
                record.get("receipt_id")
                or (record.get("receipt", {}).get("receipt_id") if isinstance(record.get("receipt"), dict) else None)
                or (signal_data.get("provenance", {}).get("receipt_id") if isinstance(signal_data, dict) and isinstance(signal_data.get("provenance"), dict) else None)
                or (record.get("provenance", {}).get("receipt_id") if isinstance(record.get("provenance"), dict) else None)
            )
            if not receipt_id:
                raise ConsortiumSecurityError(
                    f"Authoritative signal '{resolved_signal_id}' lacks authentic cryptographic receipt reference. "
                    "Unattested signals cannot be admitted into institutional evidence."
                )

            # Look up authoritative cryptographic admission record
            admitted_receipt = self._admitted_receipts_store.get(receipt_id)
            if not admitted_receipt and (record.get("reservation_id") and record.get("pilot_id")):
                from app.application.services.pilot_operations_service import PilotOperationsService
                pilot_svc = PilotOperationsService.get_instance()
                durable_res = pilot_svc.store.get_query_reservation(record["reservation_id"])
                if durable_res and durable_res.get("status") == "PUBLISHED" and durable_res.get("signal_id") == resolved_signal_id:
                    pilot_obj = pilot_svc.store.get_pilot(durable_res.get("pilot_id", ""))
                    pilot_consortium = pilot_obj.consortium_id if pilot_obj else None
                    admitted_receipt = {
                        "receipt_id": durable_res.get("receipt_id"),
                        "signal_id": durable_res.get("signal_id"),
                        "requesting_bank_id": durable_res.get("requesting_bank_id"),
                        "target_bank_id": durable_res.get("target_bank_id"),
                        "consortium_id": pilot_consortium,
                        "content_hash": durable_res.get("content_hash"),
                        "execution_mode": "VERIFIED_OPERATIONAL",
                    }

            if not admitted_receipt or not isinstance(admitted_receipt, dict):
                raise ConsortiumSecurityError(
                    f"Authoritative issuance verification failed for signal '{resolved_signal_id}': "
                    f"Receipt '{receipt_id}' has no authentic admission entry in cryptographic registry "
                    "or finalized pilot reservation. "
                    "Defensible evidence requires verified operational execution and independent cryptographic admission."
                )

            # Strict Receipt-to-Signal Binding (Correction A)
            adm_receipt_id = admitted_receipt.get("receipt_id")
            if not adm_receipt_id or adm_receipt_id != receipt_id:
                raise ConsortiumSecurityError(
                    f"Cryptographic admission receipt mismatch: Admitted receipt '{adm_receipt_id}' does not match requested receipt '{receipt_id}'."
                )

            adm_signal_id = admitted_receipt.get("signal_id")
            if not adm_signal_id or adm_signal_id != resolved_signal_id:
                raise ConsortiumSecurityError(
                    f"Cryptographic admission binding mismatch: Receipt '{receipt_id}' was issued for signal "
                    f"'{adm_signal_id}', cannot be bound to requested signal '{resolved_signal_id}'."
                )

            # Trusted Issuance Content Digest Verification (Correction A)
            trusted_content_hash = admitted_receipt.get("content_hash")
            if not trusted_content_hash or not isinstance(trusted_content_hash, str) or len(trusted_content_hash) != 64:
                raise ConsortiumSecurityError(
                    f"Authoritative signal '{resolved_signal_id}' lacks a valid, independently established issuance digest "
                    f"in cryptographic registry (got '{trusted_content_hash}'). "
                    "Historic or un-attested pilot records lacking immutable content digest cannot provide "
                    "independently verified issuance integrity and cannot enter institutional case evidence."
                )

            stored_hash = record.get("content_hash")
            if not stored_hash or stored_hash != trusted_content_hash:
                raise ConsortiumSecurityError(
                    f"Authoritative signal '{resolved_signal_id}' content hash '{stored_hash}' "
                    f"diverges from cryptographically admitted issuance digest '{trusted_content_hash}' for receipt '{receipt_id}'. "
                    "Stored record has been mutated or tampered with."
                )
            if not recomputed_hash or recomputed_hash != trusted_content_hash:
                raise ConsortiumSecurityError(
                    f"Authoritative signal '{resolved_signal_id}' canonical content digest '{recomputed_hash}' "
                    f"diverges from cryptographically admitted issuance digest '{trusted_content_hash}' for receipt '{receipt_id}'. "
                    "Stored signal data has been mutated or tampered with."
                )

            # Consistency across Consortium, Requesting Bank, and Counterpart Scope (Mandatory)
            adm_consortium = admitted_receipt.get("consortium_id")
            if not adm_consortium or adm_consortium.lower() != consortium_id.lower():
                raise ConsortiumSecurityError(
                    f"Admitted receipt '{receipt_id}' consortium scope '{adm_consortium}' does not match signal consortium '{consortium_id}'."
                )

            adm_requesting = admitted_receipt.get("requesting_bank_id")
            if not adm_requesting or adm_requesting.lower() != eff_case_bank.lower():
                raise ConsortiumSecurityError(
                    f"Admitted receipt '{receipt_id}' requesting bank scope '{adm_requesting}' does not match case bank '{eff_case_bank}'."
                )
            sig_requesting = (record.get("requesting_bank_id") or "").lower().strip()
            if not sig_requesting or sig_requesting != eff_case_bank.lower():
                raise ConsortiumSecurityError(
                    f"Signal '{resolved_signal_id}' requesting bank '{sig_requesting}' does not match case bank '{eff_case_bank}'."
                )

            adm_target = admitted_receipt.get("target_bank_id")
            raw_target = (
                record.get("target_bank_id")
                or (record.get("signal_data") or {}).get("target_bank_id")
                or (
                    record["signal_data"]["contributing_institutions"][0]
                    if isinstance(record.get("signal_data"), dict)
                    and record["signal_data"].get("contributing_institutions")
                    and len(record["signal_data"]["contributing_institutions"]) == 1
                    else ""
                )
                or ""
            )
            target_bank = str(raw_target).lower().strip()
            if not adm_target or not target_bank or adm_target.lower() != target_bank:
                raise ConsortiumSecurityError(
                    f"Admitted receipt '{receipt_id}' counterpart scope '{adm_target}' does not match signal target bank '{target_bank}'."
                )

            # Consistency check with Finalized Pilot Reservation if present
            if record.get("reservation_id") and record.get("pilot_id"):
                from app.application.services.pilot_operations_service import PilotOperationsService
                pilot_svc = PilotOperationsService.get_instance()
                durable_res = pilot_svc.store.get_query_reservation(record["reservation_id"])
                if not durable_res:
                    raise ConsortiumSecurityError(f"Pilot reservation '{record['reservation_id']}' not found in durable store.")
                if durable_res.get("status") != "PUBLISHED":
                    raise ConsortiumSecurityError(f"Pilot reservation '{record['reservation_id']}' status is '{durable_res.get('status')}', not PUBLISHED.")
                if durable_res.get("signal_id") != resolved_signal_id:
                    raise ConsortiumSecurityError(f"Pilot reservation '{record['reservation_id']}' signal_id '{durable_res.get('signal_id')}' does not match '{resolved_signal_id}'.")
                if durable_res.get("receipt_id") != receipt_id:
                    raise ConsortiumSecurityError(f"Pilot reservation '{record['reservation_id']}' receipt_id '{durable_res.get('receipt_id')}' does not match '{receipt_id}'.")
                if durable_res.get("requesting_bank_id", "").lower() != eff_case_bank.lower():
                    raise ConsortiumSecurityError(f"Pilot reservation requesting bank '{durable_res.get('requesting_bank_id')}' does not match case bank '{eff_case_bank}'.")
                if durable_res.get("target_bank_id", "").lower() != target_bank.lower():
                    raise ConsortiumSecurityError(f"Pilot reservation target bank '{durable_res.get('target_bank_id')}' does not match signal target bank '{target_bank}'.")
                res_content_hash = durable_res.get("content_hash")
                if res_content_hash and res_content_hash != trusted_content_hash:
                    raise ConsortiumSecurityError(
                        f"Pilot reservation '{record['reservation_id']}' content hash '{res_content_hash}' "
                        f"diverges from admitted issuance digest '{trusted_content_hash}'."
                    )

            # 4. Revalidate Operational Data-Sharing Grant & Bilateral Consent
            grant_id = record.get("grant_id") or (
                signal_data.get("provenance", {}).get("grant_id")
                if isinstance(signal_data, dict)
                else None
            )
            if grant_id:
                grant: dict[str, Any] = {}
                if hasattr(consortium, "get_pairwise_operational_grant"):
                    grant = consortium.get_pairwise_operational_grant(grant_id) or {}
                if not grant and hasattr(consortium, "pairwise_operational_grants"):
                    grant = (consortium.pairwise_operational_grants or {}).get(grant_id) or {}
                if not grant and hasattr(consortium, "metadata"):
                    grant = (consortium.metadata or {}).get("pairwise_operational_grants", {}).get(grant_id) or {}
                if not grant and hasattr(consortium, "metadata"):
                    global_grant = (consortium.metadata or {}).get("operational_data_sharing_grant") or {}
                    if global_grant.get("grant_id") == grant_id:
                        grant = global_grant

                is_grant_active = bool(grant.get("is_active", True) and grant.get("status") != "REVOKED")
                revoked_institutions = set(grant.get("revoked_by_institutions") or [])
                target_bank_override = record.get("target_bank_id") or (
                    signal_data.get("provenance", {}).get("target_bank_id")
                    if isinstance(signal_data, dict)
                    else None
                )
                target_bank_check = str(target_bank_override).lower().strip() if target_bank_override else target_bank
                if (
                    grant.get("grant_id") != grant_id
                    or not is_grant_active
                    or (eff_case_bank in revoked_institutions)
                    or (target_bank_check and target_bank_check in revoked_institutions)
                ):
                    raise ConsortiumSecurityError(
                        f"Operational consent for grant '{grant_id}' has been revoked or is no longer active in consortium '{consortium_id}'. "
                        "Evidence attachment denied."
                    )
                expected_rev = record.get("grant_revision") or (
                    signal_data.get("provenance", {}).get("grant_revision")
                    if isinstance(signal_data, dict)
                    else None
                )
                if expected_rev is not None and grant.get("revision") != expected_rev:
                    raise ConsortiumSecurityError(
                        f"Operational grant '{grant_id}' revision changed (was {expected_rev}, now {grant.get('revision')}). "
                        "Prior consent no longer authorizes evidence attachment without reapproval."
                    )
                approvals = grant.get("approvals") or {}
                if eff_case_bank not in approvals:
                    raise ConsortiumSecurityError(
                        f"Institution '{eff_case_bank}' has not approved current grant revision for grant '{grant_id}'. "
                        "Evidence attachment denied."
                    )
                if target_bank_check and target_bank_check not in approvals:
                    raise ConsortiumSecurityError(
                        f"Target counterpart institution '{target_bank_check}' has not approved current grant revision for grant '{grant_id}'. "
                        "Evidence attachment denied."
                    )

            # 5. Idempotent Submission Check (Prevent Duplicate Evidence Creation & Enforce Subject/Material Binding)
            attachment_fingerprint = self._compute_evidence_attachment_fingerprint(
                case_id=case_id,
                signal_id=resolved_signal_id,
                notes=notes,
                extra_payload=kwargs,
            )
            existing_evidence = self.evidence_service.get_case_evidence(case_id)
            for ev in existing_evidence:
                ev_meta = ev.get("metadata") or {}
                matched_by_key = bool(idempotency_key and ev_meta.get("idempotency_key") == idempotency_key)
                matched_by_signal = bool(ev_meta.get("signal_id") == resolved_signal_id)

                if matched_by_key:
                    if ev_meta.get("signal_id") != resolved_signal_id:
                        raise CrossBankIdempotencyConflictError(
                            f"Idempotency conflict for key '{idempotency_key}': Key was already used on case '{case_id}' "
                            f"for evidence '{ev['id']}' with signal '{ev_meta.get('signal_id')}', "
                            f"cannot be reused for differing signal '{resolved_signal_id}'."
                        )
                    ev_fp = ev_meta.get("attachment_fingerprint")
                    if ev_fp and ev_fp != attachment_fingerprint:
                        raise CrossBankIdempotencyConflictError(
                            f"Idempotency conflict for key '{idempotency_key}': Key was already used on case '{case_id}' "
                            f"for evidence '{ev['id']}' with attachment fingerprint '{ev_fp}', "
                            f"cannot be reused with altered material attachment payload (fingerprint '{attachment_fingerprint}')."
                        )
                    logger.info(
                        "Idempotent attachment replay detected for signal %s on case %s (evidence_id=%s, fingerprint=%s)",
                        resolved_signal_id,
                        case_id,
                        ev["id"],
                        attachment_fingerprint,
                    )
                    return CrossBankEvidenceAttachResponse(
                        evidence_id=ev["id"],
                        case_id=case_id,
                        signal_id=ev_meta["signal_id"],
                        evidence_type=ev.get("evidence_type", "CROSS_BANK_INTELLIGENCE"),
                        title=ev.get("title", ""),
                        content_hash=ev.get("content_hash", ""),
                        uploaded_at=ev.get("uploaded_at", ""),
                        uploaded_by=ev.get("uploaded_by", uploader),
                        case_version=getattr(case, "version", 1),
                        timeline_event_recorded=False,
                        is_duplicate_submission=True,
                    )

                if matched_by_signal:
                    logger.info(
                        "Same-signal duplicate attachment detected for signal %s on case %s (evidence_id=%s)",
                        resolved_signal_id,
                        case_id,
                        ev["id"],
                    )
                    return CrossBankEvidenceAttachResponse(
                        evidence_id=ev["id"],
                        case_id=case_id,
                        signal_id=ev_meta["signal_id"],
                        evidence_type=ev.get("evidence_type", "CROSS_BANK_INTELLIGENCE"),
                        title=ev.get("title", ""),
                        content_hash=ev.get("content_hash", ""),
                        uploaded_at=ev.get("uploaded_at", ""),
                        uploaded_by=ev.get("uploaded_by", uploader),
                        case_version=getattr(case, "version", 1),
                        timeline_event_recorded=False,
                        is_duplicate_submission=True,
                    )

            # 6. Durably Register Evidence with CAS Concurrency & Policy-Aware Projected Content
            effective_disclosure_mode = (
                (signal_data.get("disclosure_mode") if isinstance(signal_data, dict) else None)
                or record.get("disclosure_mode")
                or "ANONYMOUS_SUMMARY"
            )

            # Ensure attributed disclosure mode is authorized by consortium policy
            c_meta = getattr(consortium, "metadata", {}) or {}
            sharing_policy = getattr(consortium, "sharing_policy", None) or c_meta.get("sharing_policy", {})
            if effective_disclosure_mode == "ATTRIBUTED_INSTITUTIONS" and (
                sharing_policy.get("allow_institution_identity_sharing") is False
                or c_meta.get("allow_institution_identity_sharing") is False
            ):
                    raise ConsortiumSecurityError(
                        f"Attributed disclosure mode 'ATTRIBUTED_INSTITUTIONS' denied for signal '{resolved_signal_id}': "
                        f"Consortium '{consortium_id}' sharing policy explicitly prohibits institution identity sharing."
                    )

            # Build safe allowlisted signal content projection (Correction C)
            projected_signal_content = self._project_evidence_signal_content(
                signal_data=signal_data,
                disclosure_mode=effective_disclosure_mode,
                target_bank_id=record.get("target_bank_id"),
            )

            # Validate anonymous analyst notes policy (Correction C)
            if effective_disclosure_mode != "ATTRIBUTED_INSTITUTIONS" and notes:
                self._validate_anonymous_notes_policy(
                    notes=notes,
                    consortium=consortium,
                    requesting_bank=eff_case_bank,
                    target_bank=record.get("target_bank_id"),
                )

            sanitized_provenance: dict[str, Any] = {
                "signal_id": resolved_signal_id,
                "consortium_id": consortium_id,
                "requesting_bank_id": sig_requesting_bank,
                "query_entity_id": record.get("query_entity_id"),
                "disclosure_mode": effective_disclosure_mode,
                "execution_mode": execution_mode,
                "assurance_level": assurance_level,
            }
            if effective_disclosure_mode == "ATTRIBUTED_INSTITUTIONS":
                sanitized_provenance["target_bank_id"] = record.get("target_bank_id")
                sanitized_provenance["receipt_id"] = receipt_id
                sanitized_provenance["ticket_id"] = record.get("ticket_id")
                sanitized_provenance["grant_id"] = record.get("grant_id")
                sanitized_provenance["grant_revision"] = record.get("grant_revision")
                if isinstance(signal_data, dict):
                    sanitized_provenance["contributing_institutions"] = list(
                        signal_data.get("contributing_institutions", [])
                    )
            else:
                sanitized_provenance["target_bank_id"] = None
                sanitized_provenance["contributing_institutions"] = []
                sanitized_provenance["receipt_id"] = None
                sanitized_provenance["ticket_id"] = None
                sanitized_provenance["grant_id"] = None
                sanitized_provenance["grant_revision"] = None
                sanitized_provenance["opaque_provenance_ref"] = f"prov_ref_{uuid.uuid4().hex[:16]}"

            payload = {
                "signal_id": resolved_signal_id,
                "consortium_id": consortium_id,
                "requesting_bank_id": sig_requesting_bank,
                "query_entity_id": record.get("query_entity_id"),
                "signal_content": projected_signal_content,
                "provenance": sanitized_provenance,
                "analyst_notes": notes,
                "attached_by": uploader,
                "attached_at": now.isoformat(),
                "idempotency_key": idempotency_key,
                "attachment_fingerprint": attachment_fingerprint,
                "is_authoritative": True,
                "operational_status": "OPERATIONAL",
                "batch_query_id": kwargs.get("batch_query_id"),
            }
            content_json = json.dumps(payload, sort_keys=True, indent=2)
            payload_digest = hashlib.sha256(content_json.encode("utf-8")).hexdigest()
            file_path = f"storage/evidence/case_{case_id}_signal_{resolved_signal_id}_{payload_digest[:16]}.json"
            title = f"Consortium Intelligence Evidence ({consortium_id.upper()})"

            try:
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
                        "attachment_fingerprint": attachment_fingerprint,
                        "is_authoritative": True,
                        "operational_status": "OPERATIONAL",
                        "disclosure_mode": effective_disclosure_mode,
                        "notes": notes,
                        "batch_query_id": kwargs.get("batch_query_id"),
                        "batch_idempotency_key": kwargs.get("batch_idempotency_key"),
                        "batch_fingerprint": kwargs.get("batch_fingerprint"),
                    },
                    expected_case_version=initial_case_version,
                )
            except InvalidCaseTransitionError:
                fresh_evidence = self.evidence_service.get_case_evidence(case_id)
                for fresh_ev in fresh_evidence:
                    fresh_meta = fresh_ev.get("metadata") or {}
                    matched_by_key = bool(idempotency_key and fresh_meta.get("idempotency_key") == idempotency_key)
                    matched_by_signal = bool(fresh_meta.get("signal_id") == resolved_signal_id)

                    if matched_by_key:
                        if fresh_meta.get("signal_id") != resolved_signal_id:
                            raise CrossBankIdempotencyConflictError(
                                f"Idempotency conflict for key '{idempotency_key}' during concurrent submission on case '{case_id}': "
                                f"Key was committed for evidence '{fresh_ev['id']}' with signal '{fresh_meta.get('signal_id')}', "
                                f"cannot be reused for differing signal '{resolved_signal_id}'."
                            )
                        fresh_fp = fresh_meta.get("attachment_fingerprint")
                        if fresh_fp and fresh_fp != attachment_fingerprint:
                            raise CrossBankIdempotencyConflictError(
                                f"Idempotency conflict for key '{idempotency_key}' during concurrent submission on case '{case_id}': "
                                f"Key was committed for evidence '{fresh_ev['id']}' with attachment fingerprint '{fresh_fp}', "
                                f"cannot be reused with altered material attachment payload (fingerprint '{attachment_fingerprint}')."
                            )
                        fresh_case = self.case_service.get_case(case_id)
                        return CrossBankEvidenceAttachResponse(
                            evidence_id=fresh_ev["id"],
                            case_id=case_id,
                            signal_id=fresh_meta["signal_id"],
                            evidence_type=fresh_ev.get("evidence_type", "CROSS_BANK_INTELLIGENCE"),
                            title=fresh_ev.get("title", ""),
                            content_hash=fresh_ev.get("content_hash", ""),
                            uploaded_at=fresh_ev.get("uploaded_at", ""),
                            uploaded_by=fresh_ev.get("uploaded_by", uploader),
                            case_version=getattr(fresh_case, "version", getattr(case, "version", 1)),
                            timeline_event_recorded=False,
                            is_duplicate_submission=True,
                        )

                    if matched_by_signal:
                        fresh_case = self.case_service.get_case(case_id)
                        return CrossBankEvidenceAttachResponse(
                            evidence_id=fresh_ev["id"],
                            case_id=case_id,
                            signal_id=fresh_meta["signal_id"],
                            evidence_type=fresh_ev.get("evidence_type", "CROSS_BANK_INTELLIGENCE"),
                            title=fresh_ev.get("title", ""),
                            content_hash=fresh_ev.get("content_hash", ""),
                            uploaded_at=fresh_ev.get("uploaded_at", ""),
                            uploaded_by=fresh_ev.get("uploaded_by", uploader),
                            case_version=getattr(fresh_case, "version", getattr(case, "version", 1)),
                            timeline_event_recorded=False,
                            is_duplicate_submission=True,
                        )
                raise

            # Record attachment in authoritative record
            record.setdefault("attached_to_cases", []).append(case_id)
            self._signal_store.set(resolved_signal_id, record)

            # Refresh updated case version
            updated_case = self.case_service.get_case(case_id)
            current_version = getattr(updated_case, "version", 1)

            ImmutableAuditChain.get_instance().append_event(
                event_type="CROSS_BANK_INTELLIGENCE_EVIDENCE_ATTACHED",
                actor_id=f"user:{uploader}@{eff_case_bank}",
                action=f"attach_cross_bank_evidence:{case_id}",
                resource_id=f"evidence:{ev['id']}",
                actor_institution=eff_case_bank,
                details={
                    "case_id": case_id,
                    "evidence_id": ev["id"],
                    "content_hash": ev["content_hash"],
                    "signal_id": resolved_signal_id,
                    "consortium_id": consortium_id,
                    "disclosure_mode": effective_disclosure_mode,
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
        except Exception as exc:
            self._audit_attachment_rejection(
                case_id=case_id,
                signal_id=resolved_signal_id,
                reason=str(exc),
                error_type=exc.__class__.__name__,
                uploader=uploader,
                caller_bank=caller_bank,
                consortium_id=consortium_id,
            )
            raise

    def _is_counterparty_attributed_authorized(
        self,
        consortium: Any,
        requesting_bank: str,
        target_bank: str,
        signal_record: dict[str, Any] | None = None,
    ) -> bool:
        """Evaluate whether counterpart identity disclosure is explicitly authorized under governance policy.

        Fails closed (returns False) unless the governing operational grant or the admitted signal record
        explicitly permits ATTRIBUTED_INSTITUTIONS disclosure mode.
        """
        if signal_record:
            raw_sig_data = signal_record.get("signal_data")
            sig_data: dict[str, Any] = raw_sig_data if isinstance(raw_sig_data, dict) else {}
            mode = sig_data.get("disclosure_mode") or signal_record.get("disclosure_mode")
            if mode == "ATTRIBUTED_INSTITUTIONS":
                return True
            if mode in ("ANONYMOUS_SUMMARY", "ANONYMIZED_SUMMARY", "ANONYMOUS_STATISTICAL", "PSEUDONYMIZED"):
                return False

        if consortium:
            if hasattr(consortium, "find_pairwise_operational_grant"):
                g = consortium.find_pairwise_operational_grant(requesting_bank, target_bank)
                if g and isinstance(g, dict):
                    is_active = g.get("is_active", True) and not g.get("revoked_by_institutions")
                    if is_active and bool(g.get("allow_institution_identity_sharing", False)):
                        return True

            # Also check all operational data sharing grants as fallback
            req_clean = requesting_bank.lower().strip()
            tgt_clean = target_bank.lower().strip()
            all_grants = getattr(consortium, "operational_data_sharing_grants", {}) or {}
            for g in all_grants.values():
                if isinstance(g, dict):
                    parts = {str(b).lower().strip() for b in (g.get("participating_banks") or g.get("member_institutions") or [])}
                    is_active = g.get("is_active", True) and not g.get("revoked_by_institutions")
                    if is_active and req_clean in parts and tgt_clean in parts and bool(g.get("allow_institution_identity_sharing", False)):
                        return True
        return False

    def _project_safe_attach_failure(
        self,
        target_bank: str,
        category: str,
        raw_reason: str,
        consortium: Any,
        requesting_bank: str,
        signal_id: str | None = None,
        signal_record: dict[str, Any] | None = None,
    ) -> MultiCounterpartyEvidenceAttachFailure:
        """Project a disclosure-safe, sanitized attach failure record without leaking sensitive identifiers.

        Ensures:
        - target_bank_id and signal_id are omitted (None) unless attributed disclosure is authorized.
        - reason is mapped to carefully authored stable domain text, never raw exception strings or secrets.
        - internal detailed errors are logged server-side for restricted auditability.
        """
        is_attributed = self._is_counterparty_attributed_authorized(
            consortium=consortium,
            requesting_bank=requesting_bank,
            target_bank=target_bank,
            signal_record=signal_record,
        )

        safe_target: str | None = target_bank if is_attributed else None
        safe_sig_id: str | None = signal_id if is_attributed else None

        # Map to stable public category and sanitized message
        safe_category = category
        raw_lower = raw_reason.lower()
        if "timed out" in raw_lower or "timeout" in raw_lower or category in ("WORKER_TIMEOUT", "COUNTERPART_TIMEOUT"):
            safe_category = "WORKER_TIMEOUT" if category == "WORKER_TIMEOUT" else "COUNTERPART_TIMEOUT"
            safe_reason = "Counterpart execution timed out."
        elif "circuit breaker" in raw_lower or category in ("CIRCUIT_BREAKER_ACTIVE", "CIRCUIT_BREAKER_OPEN"):
            safe_category = "CIRCUIT_BREAKER_ACTIVE"
            safe_reason = "Counterpart circuit breaker active."
        elif "unavailable" in raw_lower or "offline" in raw_lower or "transport" in raw_lower or "refused" in raw_lower or category == "COUNTERPART_UNAVAILABLE":
            safe_category = "COUNTERPART_UNAVAILABLE"
            safe_reason = "Counterpart worker unavailable."
        elif "revoked" in raw_lower:
            safe_category = "GOVERNANCE_OR_ADMISSION_REJECTED"
            safe_reason = "Operational grant was revoked or expired."
        elif "consent" in raw_lower or "grant" in raw_lower:
            safe_category = "GOVERNANCE_OR_ADMISSION_REJECTED"
            safe_reason = "Consortium data sharing grant or consent is not active."
        elif (
            "admission" in raw_lower
            or "digest" in raw_lower
            or "binding" in raw_lower
            or "tamper" in raw_lower
            or "receipt" in raw_lower
            or "integrity" in raw_lower
        ):
            safe_category = "GOVERNANCE_OR_ADMISSION_REJECTED"
            safe_reason = "Signal cryptographic integrity or admission verification failed."
        elif safe_category == "GOVERNANCE_OR_ADMISSION_REJECTED":
            safe_reason = "Signal admission rejected under consortium governance or integrity policy."
        elif safe_category == "NO_ADMISSIBLE_SIGNALS":
            safe_reason = "Batch contains zero admissible operational signals for case evidence."
        else:
            safe_category = "COUNTERPART_EXECUTION_FAILURE"
            safe_reason = "Counterpart execution did not produce an admissible operational signal."

        logger.info(
            "Sanitized attach failure projected for counterpart (attributed=%s, category=%s): raw_reason='%s'",
            is_attributed,
            safe_category,
            raw_reason,
        )

        return MultiCounterpartyEvidenceAttachFailure(
            target_bank_id=safe_target,
            signal_id=safe_sig_id,
            category=safe_category,
            reason=safe_reason,
        )

    def attach_multi_counterparty_to_case_evidence(
        self,
        query_id: str,
        case_id: str,
        principal: AuthenticatedPrincipal | None = None,
        auth_token: str | None = None,
        actor: str | None = None,
        notes: str | None = None,
        idempotency_key: str | None = None,
    ) -> CrossBankMultiEvidenceAttachResponse:
        """Assemble eligible cross-bank intelligence signals from an authoritative multi-counterparty batch into a case."""
        uploader = "unknown"
        caller_bank = "unknown"
        batch_consortium_id: str | None = None

        try:
            # 1. Caller Authentication & Institutional Role Verification
            auth_principal = self._authenticate_principal(principal=principal, auth_token=auth_token)
            caller_bank = (auth_principal.tenant_id or "").lower().strip()
            if not caller_bank:
                raise PermissionError(
                    "Unauthenticated or unassociated principal: Institutional tenant_id is required for evidence attachment."
                )
            uploader = auth_principal.principal_id

            if not any(r in PERMITTED_INTELLIGENCE_ROLES for r in auth_principal.roles):
                raise PermissionError(
                    f"Principal '{auth_principal.principal_id}' lacks permitted role to attach evidence. "
                    f"Permitted roles: {sorted(PERMITTED_INTELLIGENCE_ROLES)}."
                )

            if actor and actor.lower().strip() != uploader.lower().strip():
                raise PermissionError(
                    f"Caller-supplied actor identity ('{actor}') does not match authenticated principal ('{uploader}')."
                )

            # 2. Case Verification & Tenant Isolation
            case = self.case_service.get_case(case_id)
            if not case:
                raise CaseNotFoundError(f"Case '{case_id}' not found.")

            eff_case_bank = str(getattr(case, "bank_id", "") or "").lower().strip()
            if not eff_case_bank:
                raise ValueError(f"Case '{case_id}' lacks authoritative institution binding (bank_id).")

            # Strict Case-Write Tenant Ownership: caller bank MUST match case bank
            if caller_bank != eff_case_bank:
                raise PermissionError(
                    f"Tenant isolation violation: Caller institution '{caller_bank}' cannot modify "
                    f"or attach evidence to case '{case_id}' owned by institution '{eff_case_bank}'. "
                    "Authoritative case evidence writes require caller-bank ownership regardless of administrative roles."
                )

            # 3. Retrieve Server-Authoritative Batch Execution Record
            clean_qid = (query_id or "").strip()
            if not clean_qid:
                raise ValueError(
                    "Mandatory query_id missing. Multi-counterparty evidence assembly requires an authoritative server-issued query_id."
                )

            batch = self.batch_store.get_batch(clean_qid)
            if not batch:
                raise BatchNotFoundError(
                    f"Authoritative multi-counterparty query '{clean_qid}' not found, has expired, or was never issued. "
                    "Client-fabricated or unauthorized batch attachment rejected."
                )

            batch_req_bank = str(batch.get("requesting_bank_id") or "").lower().strip()
            if not batch_req_bank or batch_req_bank != eff_case_bank or batch_req_bank != caller_bank:
                raise PermissionError(
                    f"Cross-tenant batch violation: Authoritative query '{clean_qid}' was issued to institution "
                    f"'{batch_req_bank}', cannot be assembled into case belonging to institution '{eff_case_bank}'."
                )

            batch_consortium_id = str(batch.get("consortium_id") or "").lower().strip()
            if not batch_consortium_id:
                raise ValueError(f"Corrupted batch record '{clean_qid}': missing consortium_id binding.")

            # Atomically establish single destination case binding (Correction B)
            try:
                self.batch_store.bind_case(query_id=clean_qid, case_id=case_id)
            except BatchCaseBindingConflictError as e:
                raise ConsortiumSecurityError(str(e)) from e

            # Revalidate Consortium Active Membership
            consortium = self.consortium_service.get_consortium(batch_consortium_id)
            if not consortium:
                raise ConsortiumSecurityError(f"Consortium '{batch_consortium_id}' no longer exists.")
            if consortium.status != ConsortiumStatus.ACTIVE:
                raise ConsortiumSecurityError(
                    f"Consortium '{batch_consortium_id}' is in non-active status '{consortium.status.value}'."
                )
            if eff_case_bank in consortium.removed_members:
                raise ConsortiumSecurityError(
                    f"Institution '{eff_case_bank}' has been revoked/evicted from consortium '{batch_consortium_id}'."
                )
            if eff_case_bank not in consortium.members:
                raise ConsortiumSecurityError(
                    f"Institution '{eff_case_bank}' is not an active member of consortium '{batch_consortium_id}'."
                )

            if not case.is_open:
                raise TerminalCaseImmutableError(
                    f"Cannot attach cross-bank intelligence: Case '{case_id}' is finalized in terminal state '{case.status.value}'."
                )

            # 4. Batch-Level Idempotency and Conflict Check
            batch_fingerprint = hashlib.sha256(
                f"{clean_qid}:{case_id}:{notes or ''}".encode()
            ).hexdigest()

            existing_evidence = self.evidence_service.get_case_evidence(case_id)
            if idempotency_key:
                for ev in existing_evidence:
                    ev_meta = ev.get("metadata") or {}
                    if ev_meta.get("batch_idempotency_key") == idempotency_key:
                        if ev_meta.get("batch_query_id") != clean_qid:
                            raise CrossBankIdempotencyConflictError(
                                f"Idempotency conflict for key '{idempotency_key}': Key was already used on case '{case_id}' "
                                f"for batch query '{ev_meta.get('batch_query_id')}', cannot be reused for differing query '{clean_qid}'."
                            )
                        if ev_meta.get("batch_fingerprint") and ev_meta.get("batch_fingerprint") != batch_fingerprint:
                            raise CrossBankIdempotencyConflictError(
                                f"Idempotency conflict for key '{idempotency_key}': Key was already used on case '{case_id}' "
                                f"with differing material batch payload or analyst notes."
                            )

            target_bank_ids = list(batch.get("target_bank_ids") or [])
            successful_signal_ids: dict[str, str] = dict(batch.get("successful_signal_ids") or {})
            raw_failures: dict[str, Any] = dict(batch.get("execution_outcomes") or {})

            # 5. Pre-validate Shared Analyst Notes Policy against Any Anonymous Target
            if notes:
                for t in target_bank_ids:
                    sig_id = successful_signal_ids.get(t)
                    if sig_id:
                        sig_rec = self._signal_store.get(sig_id)
                        if sig_rec:
                            sig_data = sig_rec.get("signal_data") if isinstance(sig_rec.get("signal_data"), dict) else {}
                            sig_mode = sig_data.get("disclosure_mode") or sig_rec.get("disclosure_mode") or "ANONYMIZED_SUMMARY"
                            if sig_mode != "ATTRIBUTED_INSTITUTIONS":
                                self._validate_anonymous_notes_policy(
                                    notes=notes,
                                    consortium=consortium,
                                    requesting_bank=eff_case_bank,
                                    target_bank=t,
                                )

            # 6. Handle Batch with Zero Admissible/Successful Signals (Correction C: Disclosure-Safe Projection)
            if not successful_signal_ids:
                omitted_failures: list[MultiCounterpartyEvidenceAttachFailure] = []
                for t in target_bank_ids:
                    t_out = raw_failures.get(t) or {}
                    t_fail = t_out.get("failure") or {}
                    omitted_failures.append(
                        self._project_safe_attach_failure(
                            target_bank=t,
                            category=t_fail.get("error_category", "COUNTERPART_EXECUTION_FAILURE"),
                            raw_reason=t_fail.get("error_message", "Counterparty execution did not produce an admissible operational signal."),
                            consortium=consortium,
                            requesting_bank=eff_case_bank,
                        )
                    )
                self._audit_attachment_rejection(
                    case_id=case_id,
                    signal_id=None,
                    reason="Batch contains zero successful operational signals for evidence assembly.",
                    error_type="NO_ADMISSIBLE_SIGNALS",
                    uploader=uploader,
                    caller_bank=caller_bank,
                    consortium_id=batch_consortium_id,
                )
                return CrossBankMultiEvidenceAttachResponse(
                    query_id=clean_qid,
                    case_id=case_id,
                    assembly_status="FAILED" if omitted_failures else "NO_ADMISSIBLE_SIGNALS",
                    attached_count=0,
                    replayed_count=0,
                    failed_count=len(target_bank_ids),
                    attached_evidence=[],
                    omitted_or_failed=omitted_failures,
                    case_version=getattr(case, "version", 1),
                    timeline_event_recorded=False,
                    is_duplicate_submission=False,
                )

            # 7. Assemble Eligible Signals into Case Evidence
            attached_items: list[MultiCounterpartyEvidenceItem] = []
            omitted_failures = []
            newly_attached_count = 0
            replayed_count = 0

            batch_receipt_ids: dict[str, str] = dict(batch.get("receipt_ids") or {})

            for t in target_bank_ids:
                if t not in successful_signal_ids:
                    t_out = raw_failures.get(t) or {}
                    t_fail = t_out.get("failure") or {}
                    omitted_failures.append(
                        self._project_safe_attach_failure(
                            target_bank=t,
                            category=t_fail.get("error_category", "COUNTERPART_EXECUTION_FAILURE"),
                            raw_reason=t_fail.get("error_message", "Counterpart execution failed; no authoritative signal produced."),
                            consortium=consortium,
                            requesting_bank=eff_case_bank,
                        )
                    )
                    continue

                sig_id = successful_signal_ids[t]
                sig_rec = self._signal_store.get(sig_id)

                # Correction A: Exact Receipt Binding Verification before attaching
                expected_rcp_id = batch_receipt_ids.get(t)
                if not expected_rcp_id:
                    omitted_failures.append(
                        self._project_safe_attach_failure(
                            target_bank=t,
                            category="GOVERNANCE_OR_ADMISSION_REJECTED",
                            raw_reason="Batch execution record lacks authentic cryptographic receipt binding.",
                            consortium=consortium,
                            requesting_bank=eff_case_bank,
                            signal_id=sig_id,
                            signal_record=sig_rec,
                        )
                    )
                    continue

                admitted_rcp = self._admitted_receipts_store.get(expected_rcp_id)
                if not admitted_rcp:
                    omitted_failures.append(
                        self._project_safe_attach_failure(
                            target_bank=t,
                            category="GOVERNANCE_OR_ADMISSION_REJECTED",
                            raw_reason=f"Admitted receipt '{expected_rcp_id}' not found in cryptographic registry.",
                            consortium=consortium,
                            requesting_bank=eff_case_bank,
                            signal_id=sig_id,
                            signal_record=sig_rec,
                        )
                    )
                    continue

                # Verify consistent binding across query_id membership, signal, receipt, banks, and entity
                if (
                    admitted_rcp.get("signal_id") != sig_id
                    or admitted_rcp.get("receipt_id") != expected_rcp_id
                    or (admitted_rcp.get("requesting_bank_id") or "").lower().strip() != eff_case_bank.lower().strip()
                    or (admitted_rcp.get("consortium_id") or "").lower().strip() != batch_consortium_id.lower().strip()
                    or (admitted_rcp.get("target_bank_id") or "").lower().strip() != t.lower().strip()
                    or (
                        admitted_rcp.get("query_entity_id")
                        and batch.get("query_entity_id")
                        and str(admitted_rcp.get("query_entity_id")).strip() != str(batch.get("query_entity_id") or "").strip()
                    )
                ):
                    omitted_failures.append(
                        self._project_safe_attach_failure(
                            target_bank=t,
                            category="GOVERNANCE_OR_ADMISSION_REJECTED",
                            raw_reason=f"Receipt '{expected_rcp_id}' binding mismatch with batch execution identity.",
                            consortium=consortium,
                            requesting_bank=eff_case_bank,
                            signal_id=sig_id,
                            signal_record=sig_rec,
                        )
                    )
                    continue

                derived_signal_idem_key = (
                    f"{idempotency_key}:sig:{sig_id}" if idempotency_key else None
                )

                try:
                    attach_res = self.attach_signal_to_case_evidence(
                        case_id=case_id,
                        signal_id=sig_id,
                        principal=auth_principal,
                        notes=notes,
                        idempotency_key=derived_signal_idem_key,
                        batch_query_id=clean_qid,
                        batch_idempotency_key=idempotency_key,
                        batch_fingerprint=batch_fingerprint if idempotency_key else None,
                    )
                    sig_dict: dict[str, Any] = sig_rec if isinstance(sig_rec, dict) else {}
                    raw_sig_data = sig_dict.get("signal_data")
                    attached_sig_data: dict[str, Any] = raw_sig_data if isinstance(raw_sig_data, dict) else {}
                    raw_mode = attached_sig_data.get("disclosure_mode") or sig_dict.get("disclosure_mode")
                    eff_mode: DisclosureMode = (
                        "ATTRIBUTED_INSTITUTIONS"
                        if raw_mode == "ATTRIBUTED_INSTITUTIONS"
                        else "ANONYMIZED_SUMMARY"
                    )

                    reported_target = t if eff_mode == "ATTRIBUTED_INSTITUTIONS" else None

                    status_val: Literal["ATTACHED", "ALREADY_ATTACHED"]
                    if attach_res.is_duplicate_submission:
                        replayed_count += 1
                        status_val = "ALREADY_ATTACHED"
                    else:
                        newly_attached_count += 1
                        status_val = "ATTACHED"

                    attached_items.append(
                        MultiCounterpartyEvidenceItem(
                            target_bank_id=reported_target,
                            signal_id=sig_id,
                            evidence_id=attach_res.evidence_id,
                            status=status_val,
                            content_hash=attach_res.content_hash,
                            title=attach_res.title,
                            disclosure_mode=eff_mode,
                        )
                    )
                except (ConsortiumSecurityError, ValueError) as err:
                    omitted_failures.append(
                        self._project_safe_attach_failure(
                            target_bank=t,
                            category="GOVERNANCE_OR_ADMISSION_REJECTED",
                            raw_reason=str(err),
                            consortium=consortium,
                            requesting_bank=eff_case_bank,
                            signal_id=sig_id,
                            signal_record=sig_rec,
                        )
                    )

            # 8. Compute Overall Assembly Status
            total_targets = len(target_bank_ids)
            successful_attachments = len(attached_items)

            if successful_attachments == total_targets and not omitted_failures:
                overall_status: MultiEvidenceAssemblyStatus = "COMPLETED"
            elif successful_attachments > 0:
                overall_status = "PARTIAL"
            else:
                overall_status = "FAILED"

            is_all_duplicates = newly_attached_count == 0 and replayed_count > 0

            # 9. Record Immutable Audit Event
            if newly_attached_count > 0 or replayed_count > 0:
                ImmutableAuditChain.get_instance().append_event(
                    event_type="CROSS_BANK_INTELLIGENCE_MULTI_EVIDENCE_ATTACHED",
                    actor_id=f"user:{uploader}@{eff_case_bank}",
                    action=f"attach_cross_bank_multi_evidence:{case_id}",
                    resource_id=f"batch:{clean_qid}",
                    actor_institution=eff_case_bank,
                    details={
                        "case_id": case_id,
                        "query_id": clean_qid,
                        "consortium_id": batch_consortium_id,
                        "assembly_status": overall_status,
                        "attached_count": newly_attached_count,
                        "replayed_count": replayed_count,
                        "failed_count": len(omitted_failures),
                        "is_duplicate_submission": is_all_duplicates,
                    },
                )

            updated_case = self.case_service.get_case(case_id)
            current_version = getattr(updated_case, "version", getattr(case, "version", 1))

            return CrossBankMultiEvidenceAttachResponse(
                query_id=clean_qid,
                case_id=case_id,
                assembly_status=overall_status,
                attached_count=newly_attached_count,
                replayed_count=replayed_count,
                failed_count=len(omitted_failures),
                attached_evidence=attached_items,
                omitted_or_failed=omitted_failures,
                case_version=current_version,
                timeline_event_recorded=newly_attached_count > 0,
                is_duplicate_submission=is_all_duplicates,
            )
        except Exception as exc:
            self._audit_attachment_rejection(
                case_id=case_id,
                signal_id=None,
                reason=str(exc),
                error_type=exc.__class__.__name__,
                uploader=uploader,
                caller_bank=caller_bank,
                consortium_id=batch_consortium_id,
            )
            raise

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

