"""Cross-Bank Intelligence API endpoints.

Exposes REST APIs for authorized cross-bank intelligence queries,
privacy-preserving consortium matching, and analyst investigation case evidence integration.
Supports dual routing: /api/v1/intelligence/cross-bank and /v1/intelligence/cross-bank.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.application.schemas.cross_bank_intelligence import (
    CrossBankConsortiumCapability,
    CrossBankCounterpartCapability,
    CrossBankEvidenceAttachRequest,
    CrossBankEvidenceAttachResponse,
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
    CrossBankMultiEvidenceAttachRequest,
    CrossBankMultiEvidenceAttachResponse,
    CrossBankQueryCapabilitiesResponse,
    MultiCounterpartyIntelligenceQueryRequest,
    MultiCounterpartyIntelligenceQueryResponse,
)
from app.application.services.case_service import CaseNotFoundError, TerminalCaseImmutableError
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.dependencies import PrincipalDep, enforce_tenant_isolation
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    CrossBankExecutionQuotaExceededError,
    CrossBankIdempotencyConflictError,
)
from app.domain.pilot_operations import PilotGovernanceError
from app.infrastructure.storage.durable_batch_store import (
    BatchCaseBindingConflictError,
    BatchNotFoundError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/intelligence/cross-bank", tags=["cross-bank-intelligence"])
api_router = APIRouter(prefix="/v1/intelligence/cross-bank", tags=["cross-bank-intelligence"])


def get_cross_bank_intelligence_service() -> CrossBankIntelligenceService:
    """Dependency provider for CrossBankIntelligenceService."""
    return CrossBankIntelligenceService.get_instance()


@router.post(
    "/query",
    response_model=CrossBankIntelligenceSignal,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/query",
    response_model=CrossBankIntelligenceSignal,
    status_code=status.HTTP_200_OK,
)
def query_cross_bank_intelligence(
    req: CrossBankIntelligenceQueryRequest,
    principal: PrincipalDep,
    service: CrossBankIntelligenceService = Depends(get_cross_bank_intelligence_service),
) -> CrossBankIntelligenceSignal:
    """Execute authorized, privacy-preserving cross-bank intelligence query.

    Mandates:
    - Authenticated institutional principal bound to requesting bank.
    - Verified intelligence analyst or investigator role.
    - Verified local institution-owned query entity.
    """
    caller_tenant = principal.tenant_id
    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, req.requesting_bank_id, roles=principal.roles)

    try:
        return service.query_cross_bank_intelligence(
            req=req,
            principal=principal,
        )
    except ConsortiumSecurityError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Consortium governance authorization rejected: {err}",
        ) from err
    except PilotGovernanceError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pilot governance rejected: {err}",
        ) from err
    except PermissionError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(err),
        ) from err
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except RuntimeError as err:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=str(err),
        ) from err


@router.post(
    "/query-multi",
    response_model=MultiCounterpartyIntelligenceQueryResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/query-multi",
    response_model=MultiCounterpartyIntelligenceQueryResponse,
    status_code=status.HTTP_200_OK,
)
@router.post(
    "/multi-query",
    response_model=MultiCounterpartyIntelligenceQueryResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/multi-query",
    response_model=MultiCounterpartyIntelligenceQueryResponse,
    status_code=status.HTTP_200_OK,
)
def query_multi_counterparty_intelligence(
    req: MultiCounterpartyIntelligenceQueryRequest,
    principal: PrincipalDep,
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_idempotency_key: str | None = Header(None, alias="X-Idempotency-Key"),
    service: CrossBankIntelligenceService = Depends(get_cross_bank_intelligence_service),
) -> MultiCounterpartyIntelligenceQueryResponse:
    """Execute authorized, multi-counterparty cross-bank intelligence query.

    Mandates:
    - Authenticated institutional principal bound to requesting bank.
    - Verified intelligence analyst or investigator role.
    - Verified local institution-owned query entity.
    - Pre-flight pairwise governance authorization for every target institution.
    - Optional explicit idempotency key to prevent duplicate execution across retries.
    """
    caller_tenant = principal.tenant_id
    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, req.requesting_bank_id, roles=principal.roles)

    effective_idem_key = idempotency_key or x_idempotency_key or req.idempotency_key

    try:
        res = service.query_multi_counterparty_intelligence(
            req=req,
            principal=principal,
            idempotency_key=effective_idem_key,
        )
        if getattr(res, "_idempotency_replayed", False):
            response.headers["Idempotency-Replayed"] = "true"
        return res
    except CrossBankIdempotencyConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        ) from err
    except CrossBankExecutionQuotaExceededError as err:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Execution budget quota exceeded: {err}",
        ) from err
    except ConsortiumSecurityError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Consortium governance authorization rejected: {err}",
        ) from err
    except PilotGovernanceError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pilot governance rejected: {err}",
        ) from err
    except PermissionError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(err),
        ) from err
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except RuntimeError as err:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=str(err),
        ) from err


@router.post(
    "/attach-to-case",
    response_model=CrossBankEvidenceAttachResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/attach-to-case",
    response_model=CrossBankEvidenceAttachResponse,
    status_code=status.HTTP_200_OK,
)
async def attach_intelligence_to_case(
    req: CrossBankEvidenceAttachRequest,
    principal: PrincipalDep,
    service: CrossBankIntelligenceService = Depends(get_cross_bank_intelligence_service),
) -> CrossBankEvidenceAttachResponse:
    """Attach authoritative, server-issued cross-bank intelligence signal into case evidence ledger."""
    try:
        return service.attach_signal_to_case_evidence(
            case_id=req.case_id,
            signal_id=req.signal_id,
            principal=principal,
            notes=req.notes,
            idempotency_key=req.idempotency_key,
        )
    except (CaseNotFoundError, BatchNotFoundError) as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
    except (TerminalCaseImmutableError, BatchCaseBindingConflictError, CrossBankIdempotencyConflictError) as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        ) from err
    except ConsortiumSecurityError as err:
        if "Cross-case binding violation" in str(err):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(err),
            ) from err
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(err),
        ) from err
    except PermissionError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(err),
        ) from err
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err


@router.post(
    "/attach-multi-to-case",
    response_model=CrossBankMultiEvidenceAttachResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/attach-multi-to-case",
    response_model=CrossBankMultiEvidenceAttachResponse,
    status_code=status.HTTP_200_OK,
)
async def attach_multi_intelligence_to_case(
    req: CrossBankMultiEvidenceAttachRequest,
    principal: PrincipalDep,
    service: CrossBankIntelligenceService = Depends(get_cross_bank_intelligence_service),
) -> CrossBankMultiEvidenceAttachResponse:
    """Assemble eligible cross-bank intelligence signals from an authoritative multi-counterparty batch into a case."""
    try:
        return service.attach_multi_counterparty_to_case_evidence(
            query_id=req.query_id,
            case_id=req.case_id,
            principal=principal,
            notes=req.notes,
            idempotency_key=req.idempotency_key,
        )
    except (CaseNotFoundError, BatchNotFoundError) as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
    except (TerminalCaseImmutableError, BatchCaseBindingConflictError, CrossBankIdempotencyConflictError) as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        ) from err
    except ConsortiumSecurityError as err:
        if "Cross-case binding violation" in str(err):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(err),
            ) from err
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(err),
        ) from err
    except PermissionError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(err),
        ) from err
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err


@router.get(
    "/consortiums/{consortium_id}/policy",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
@api_router.get(
    "/consortiums/{consortium_id}/policy",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def get_consortium_sharing_policy(
    consortium_id: str,
    principal: PrincipalDep,
    service: CrossBankIntelligenceService = Depends(get_cross_bank_intelligence_service),
) -> dict[str, Any]:
    """Inspect active consortium sharing policies, disclosure rules, and privacy guarantees."""
    c = service.consortium_service.get_consortium(consortium_id.lower().strip())
    if not c:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consortium '{consortium_id}' not found.",
        )

    caller_tenant = (principal.tenant_id or "").lower().strip()
    if not principal.is_privileged and (not caller_tenant or caller_tenant not in c.members):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Institution '{caller_tenant}' is not an authorized member of consortium '{consortium_id}'. Access denied.",
        )

    return {
        "consortium_id": c.consortium_id,
        "name": c.name,
        "status": c.status.value,
        "quorum_ratio": c.quorum_ratio,
        "max_epsilon": c.max_epsilon,
        "active_member_count": len(c.members),
        "removed_member_count": len(c.removed_members),
        "permitted_disclosure": {
            "allow_raw_pii_sharing": False,
            "allow_confidential_notes_sharing": False,
            "allow_institution_scoped_signals": True,
            "allow_sanitized_typology_indicators": True,
            "attestation_requirement": "OPTIONAL_FAIL_CLOSED_IF_REQUESTED",
        },
    }


@router.get(
    "/query-capabilities",
    response_model=CrossBankQueryCapabilitiesResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.get(
    "/query-capabilities",
    response_model=CrossBankQueryCapabilitiesResponse,
    status_code=status.HTTP_200_OK,
)
def get_cross_bank_query_capabilities(
    principal: PrincipalDep,
    requesting_bank_id: str | None = Query(None),
    service: CrossBankIntelligenceService = Depends(get_cross_bank_intelligence_service),
) -> CrossBankQueryCapabilitiesResponse:
    """Retrieve server-authoritative consortia, active counterpart scope, and permitted purposes for query formulation."""
    eff_bank = (requesting_bank_id or principal.tenant_id or "").lower().strip()
    if not eff_bank:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Requesting bank ID must be provided or bound to authenticated principal tenant.",
        )
    if principal.tenant_id:
        enforce_tenant_isolation(principal.tenant_id, eff_bank, roles=principal.roles)

    from app.application.services.consortium_service import ConsortiumGovernanceService
    from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
    consortium_svc = ConsortiumGovernanceService()
    intel_service = CrossBankIntelligenceService.get_instance()
    consortia_map = consortium_svc._consortia
    consortia_res: list[CrossBankConsortiumCapability] = []

    STANDARD_PURPOSES = [
        "AML_COLLABORATIVE_INVESTIGATION",
        "FRAUD_INVESTIGATION",
        "AML_HIGH_RISK_MATCHING",
        "SANCTIONS_SCREENING",
        "REGULATORY_SAR_EVIDENCE",
    ]

    for c_id, c in consortia_map.items():
        if c.status.value.lower() != "active":
            continue
        if eff_bank not in c.members or eff_bank in c.removed_members:
            continue

        p_grants_all = consortium_svc.list_pairwise_operational_grants(c.consortium_id, eff_bank)
        candidate_purposes: list[str] = []
        for g in p_grants_all:
            for purp in g.get("allowed_purposes", []) or []:
                if isinstance(purp, str) and purp.strip() and purp.strip() not in candidate_purposes:
                    candidate_purposes.append(purp.strip())
        for purp in STANDARD_PURPOSES:
            if purp not in candidate_purposes:
                candidate_purposes.append(purp)

        counterparts_list: list[CrossBankCounterpartCapability] = []
        consortium_authorized_purposes: list[str] = []
        for target_bank in sorted(c.members.keys()):
            if target_bank.lower() == eff_bank:
                continue
            if target_bank in c.removed_members:
                continue

            authorized_purposes: list[str] = []
            for purp in candidate_purposes:
                is_auth, _, _ = consortium_svc.resolve_pairwise_authorization(
                    consortium_id=c.consortium_id,
                    requesting_bank_id=eff_bank,
                    counterpart_bank_id=target_bank,
                    purpose=purp,
                )
                if is_auth:
                    authorized_purposes.append(purp)
                    if purp not in consortium_authorized_purposes:
                        consortium_authorized_purposes.append(purp)

            gov_status: Literal["ACTIVE", "REVOKED", "MISSING_GRANT", "EVICTED"]
            readiness: Literal["OPERATIONAL", "DEGRADED", "UNAVAILABLE", "UNVERIFIED", "UNKNOWN"]
            is_authorized: bool
            disclosure_modes: list[str]

            if authorized_purposes:
                gov_status = "ACTIVE"
                is_authorized = True
                disclosure_modes = ["ANONYMIZED_SUMMARY", "ATTRIBUTED_INSTITUTIONS"]

                # Observable runtime availability vs unverified execution readiness
                cb_state = intel_service.circuit_breaker_store.get_circuit_state(
                    c.consortium_id, eff_bank, target_bank
                )
                if cb_state.get("state") == "OPEN":
                    readiness = "UNAVAILABLE"
                elif cb_state.get("state") == "HALF_OPEN":
                    readiness = "DEGRADED"
                else:
                    try:
                        from app.application.services.coordinator_service import coordinator_service
                        active_clients = coordinator_service.get_active_clients()
                        is_online = any(
                            getattr(c_node, "bank_id", "").lower().strip() == target_bank.lower().strip()
                            and getattr(c_node, "status", "") == "ONLINE"
                            for c_node in active_clients
                        )
                        readiness = "OPERATIONAL" if is_online else "UNVERIFIED"
                    except Exception:
                        readiness = "UNVERIFIED"
            else:
                p_grants = consortium_svc.list_pairwise_operational_grants(c.consortium_id, eff_bank)
                target_g = [
                    g for g in p_grants
                    if (g.get("institution_a") == target_bank or g.get("institution_b") == target_bank)
                ]
                grant = target_g[0] if target_g else None
                gov_status = "REVOKED" if grant and grant.get("revoked_at") else "MISSING_GRANT"
                is_authorized = False
                readiness = "UNAVAILABLE"
                disclosure_modes = []

            counterparts_list.append(
                CrossBankCounterpartCapability(
                    counterpart_bank_id=target_bank,
                    display_name=f"{target_bank.replace('_', ' ').title()} Node",
                    is_authorized=is_authorized,
                    governance_status=gov_status,
                    permitted_purposes=authorized_purposes,
                    disclosure_modes=disclosure_modes,
                    execution_readiness=readiness,
                )
            )

        effective_consortium_purposes = list(consortium_authorized_purposes)
        for purp in STANDARD_PURPOSES:
            if purp not in effective_consortium_purposes:
                effective_consortium_purposes.append(purp)

        consortia_res.append(
            CrossBankConsortiumCapability(
                consortium_id=c.consortium_id,
                name=c.name,
                status=c.status.value,
                counterparts=counterparts_list,
                permitted_purposes=effective_consortium_purposes,
            )
        )

    return CrossBankQueryCapabilitiesResponse(
        requesting_bank_id=eff_bank,
        consortia=consortia_res,
    )

