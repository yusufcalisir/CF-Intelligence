"""Cross-Bank Intelligence API endpoints.

Exposes REST APIs for authorized cross-bank intelligence queries,
privacy-preserving consortium matching, and analyst investigation case evidence integration.
Supports dual routing: /api/v1/intelligence/cross-bank and /v1/intelligence/cross-bank.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.schemas.cross_bank_intelligence import (
    CrossBankEvidenceAttachRequest,
    CrossBankEvidenceAttachResponse,
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
)
from app.application.services.case_service import CaseNotFoundError, TerminalCaseImmutableError
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.dependencies import PrincipalDep, enforce_tenant_isolation
from app.domain.consortium_governance import ConsortiumSecurityError
from app.domain.pilot_operations import PilotGovernanceError

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
    except CaseNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
    except TerminalCaseImmutableError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except ConsortiumSecurityError as err:
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
