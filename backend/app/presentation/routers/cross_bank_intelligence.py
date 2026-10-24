"""Cross-Bank Intelligence API endpoints.

Exposes REST APIs for authorized cross-bank intelligence queries,
privacy-preserving consortium matching, and analyst investigation case evidence integration.
Supports dual routing: /api/v1/intelligence/cross-bank and /v1/intelligence/cross-bank.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.application.schemas.cross_bank_intelligence import (
    CrossBankEvidenceAttachRequest,
    CrossBankEvidenceAttachResponse,
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
)
from app.application.services.case_service import CaseNotFoundError, TerminalCaseImmutableError
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.dependencies import TenantDep, enforce_tenant_isolation
from app.domain.consortium_governance import ConsortiumSecurityError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/intelligence/cross-bank", tags=["cross-bank-intelligence"])
api_router = APIRouter(prefix="/v1/intelligence/cross-bank", tags=["cross-bank-intelligence"])


def get_cross_bank_intelligence_service() -> CrossBankIntelligenceService:
    """Dependency provider for CrossBankIntelligenceService."""
    return CrossBankIntelligenceService()


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
async def query_cross_bank_intelligence(
    req: CrossBankIntelligenceQueryRequest,
    authorization: str | None = Header(None),
    actor: str = Query("analyst", description="Audited operator initiating query"),
    caller_tenant: TenantDep = None,
    service: CrossBankIntelligenceService = Depends(get_cross_bank_intelligence_service),
) -> CrossBankIntelligenceSignal:
    """Execute authorized, privacy-preserving cross-bank intelligence query."""
    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, req.requesting_bank_id)

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    try:
        return service.query_cross_bank_intelligence(
            req=req,
            auth_token=token,
            actor=actor,
        )
    except ConsortiumSecurityError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Consortium governance authorization rejected: {err}",
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
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
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
    actor: str = Query("analyst", description="Audited investigator attaching evidence"),
    caller_tenant: TenantDep = None,
    service: CrossBankIntelligenceService = Depends(get_cross_bank_intelligence_service),
) -> CrossBankEvidenceAttachResponse:
    """Attach verified cross-bank intelligence signal into case evidence ledger."""
    try:
        return service.attach_signal_to_case_evidence(
            case_id=req.case_id,
            signal=req.signal,
            actor=actor,
            caller_bank_id=caller_tenant or req.signal.requesting_bank_id,
            notes=req.notes,
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
    caller_tenant: TenantDep = None,
    service: CrossBankIntelligenceService = Depends(get_cross_bank_intelligence_service),
) -> dict[str, Any]:
    """Inspect active consortium sharing policies, disclosure rules, and privacy guarantees."""
    c = service.consortium_service.get_consortium(consortium_id.lower().strip())
    if not c:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consortium '{consortium_id}' not found.",
        )
    if caller_tenant and caller_tenant.lower() not in c.members:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Institution '{caller_tenant}' is not an authorized member of consortium '{consortium_id}'.",
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
