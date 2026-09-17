"""Bank Onboarding API Router — Phase 36.1 / Phase 89.

Admin endpoints for registering new bank nodes, issuing mTLS certificates,
provisioning tenant schemas, and retrieving onboarding bundles.
Supports dual-prefix mounting: `/api/v1/onboarding` and `/v1/onboarding`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.application.schemas.onboarding import (
    BankCSRSignRequest,
    BankCSRSignResponse,
    BankOnboardingBundleResponse,
    BankRegisterRequest,
    BankStatusResponse,
    CertRotationResponse,
)
from app.application.services.bank_onboarding_service import (
    BankAlreadyExistsError,
    BankNotFoundError,
    BankOnboardingService,
    InvalidBankStateError,
    InvalidCSRError,
)
from app.infrastructure.database import get_async_session

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = [
    "BankCSRSignRequest",
    "BankCSRSignResponse",
    "BankOnboardingBundleResponse",
    "BankRegisterRequest",
    "BankStatusResponse",
    "CertRotationResponse",
    "api_router",
    "router",
]

_base_router = APIRouter(tags=["Bank Onboarding"])


# ── Endpoints ─────────────────────────────────────────────────────────────────


@_base_router.post(
    "/register",
    response_model=BankOnboardingBundleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new bank node and receive the onboarding bundle",
)
async def register_bank(
    payload: BankRegisterRequest,
    session: AsyncSession = Depends(get_async_session),
) -> BankOnboardingBundleResponse:
    """Run full automated bank onboarding pipeline:

    1. Register bank record (PENDING_VERIFICATION)
    2. Issue mTLS certificate & key
    3. Provision tenant database schema
    4. Provision KMS key path
    5. Generate connector config YAML
    6. Activate bank node (ACTIVE)
    """
    service = BankOnboardingService(session)
    try:
        # Step 1: Register
        await service.register_bank(
            bank_id=payload.bank_id,
            legal_name=payload.legal_name,
            jurisdiction=payload.jurisdiction,
            contact_email=payload.contact_email,
            data_residency_region=payload.data_residency_region,
        )

        # Step 2: Issue Cert
        cert_pem, key_pem = await service.issue_mtls_certificate(payload.bank_id)

        # Step 3: Provision Schema
        await service.provision_tenant_schema(payload.bank_id)

        # Step 4: Provision KMS
        await service.provision_kms_key(payload.bank_id)

        # Step 5: Config
        config_yaml = service.generate_connector_config(payload.bank_id)

        # Step 6: Activate
        activated = await service.activate_bank(payload.bank_id)

        activated_status = (
            activated.status.value
            if hasattr(activated.status, "value")
            else str(activated.status)
        )

        return BankOnboardingBundleResponse(
            bank_id=payload.bank_id,
            status=activated_status,
            legal_name=payload.legal_name,
            jurisdiction=payload.jurisdiction,
            contact_email=payload.contact_email,
            data_residency_region=payload.data_residency_region,
            cert_fingerprint=activated.cert_fingerprint or "",
            mtls_cert_pem=cert_pem,
            mtls_key_pem=key_pem,
            connector_config_yaml=config_yaml,
            coordinator_endpoint="https://coordinator.cf-intelligence.io:50051",
            certificate_pem=cert_pem,
            private_key_pem=key_pem,
        )
    except BankAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Bank registration pipeline failure for bank_id=%s: %s",
            payload.bank_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bank onboarding pipeline failed: {exc}",
        ) from exc


@_base_router.get(
    "/bundle/{bank_id}",
    response_model=BankOnboardingBundleResponse,
    summary="Retrieve onboarding bundle and configuration for an onboarded bank node",
)
async def get_onboarding_bundle(
    bank_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> BankOnboardingBundleResponse:
    """Retrieve existing configuration bundle for a registered bank node."""
    service = BankOnboardingService(session)
    b = await service.get_bank(bank_id)
    if not b:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bank node {bank_id!r} not found.",
        )

    config_yaml = service.generate_connector_config(bank_id)
    status_str = b.status.value if hasattr(b.status, "value") else str(b.status)

    return BankOnboardingBundleResponse(
        bank_id=b.bank_id,
        status=status_str,
        legal_name=b.legal_name,
        jurisdiction=b.jurisdiction,
        contact_email=b.contact_email,
        data_residency_region=b.data_residency_region,
        cert_fingerprint=b.cert_fingerprint or "",
        mtls_cert_pem="",
        mtls_key_pem="",
        connector_config_yaml=config_yaml,
        coordinator_endpoint="https://coordinator.cf-intelligence.io:50051",
        certificate_pem="",
        private_key_pem="",
    )


@_base_router.get(
    "/banks",
    response_model=list[BankStatusResponse],
    summary="List all registered bank nodes",
)
async def list_banks(
    session: AsyncSession = Depends(get_async_session),
) -> list[BankStatusResponse]:
    """Return all bank node registrations from persistent storage."""
    service = BankOnboardingService(session)
    banks = await service.list_banks()
    return [
        BankStatusResponse(
            bank_id=b.bank_id,
            legal_name=b.legal_name,
            name=b.legal_name,
            jurisdiction=b.jurisdiction,
            status=b.status.value if hasattr(b.status, "value") else str(b.status),
            cert_fingerprint=b.cert_fingerprint,
            vault_key_path=b.vault_key_path,
            schema_provisioned=b.schema_provisioned,
            created_at=b.created_at.isoformat(),
            activated_at=b.activated_at.isoformat() if b.activated_at else None,
        )
        for b in banks
    ]


@_base_router.get(
    "/banks/{bank_id}/status",
    response_model=BankStatusResponse,
    summary="Get status of a single bank node",
)
async def get_bank_status(
    bank_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> BankStatusResponse:
    """Return detailed status for a specific bank node."""
    service = BankOnboardingService(session)
    b = await service.get_bank(bank_id)
    if not b:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bank node {bank_id!r} not found.",
        )

    return BankStatusResponse(
        bank_id=b.bank_id,
        legal_name=b.legal_name,
        name=b.legal_name,
        jurisdiction=b.jurisdiction,
        status=b.status.value if hasattr(b.status, "value") else str(b.status),
        cert_fingerprint=b.cert_fingerprint,
        vault_key_path=b.vault_key_path,
        schema_provisioned=b.schema_provisioned,
        created_at=b.created_at.isoformat(),
        activated_at=b.activated_at.isoformat() if b.activated_at else None,
    )


@_base_router.post(
    "/banks/{bank_id}/sign-csr",
    response_model=BankCSRSignResponse,
    summary="Cryptographically sign an institutional CSR via PKI wizard",
)
async def sign_bank_csr(
    bank_id: str,
    payload: BankCSRSignRequest,
    session: AsyncSession = Depends(get_async_session),
) -> BankCSRSignResponse:
    """Signs an institutional X.509 Certificate Signing Request (CSR) for an onboarded bank node."""
    service = BankOnboardingService(session)
    try:
        cert_pem, fingerprint, expires_at = await service.sign_csr(
            bank_id=bank_id,
            csr_pem=payload.csr_pem,
            days_valid=payload.days_valid,
        )
        return BankCSRSignResponse(
            bank_id=bank_id,
            signed_cert_pem=cert_pem,
            cert_fingerprint=fingerprint,
            expires_at=expires_at.isoformat(),
        )
    except BankNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InvalidCSRError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("CSR signing failure for bank_id=%s: %s", bank_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"CSR signing failed: {exc}",
        ) from exc


@_base_router.post(
    "/banks/{bank_id}/verify",
    response_model=BankStatusResponse,
    summary="Record institutional compliance verification",
)
async def verify_bank_node(
    bank_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> BankStatusResponse:
    """Record institutional compliance verification for a bank node."""
    service = BankOnboardingService(session)
    try:
        b = await service.verify_bank(bank_id)
        return BankStatusResponse(
            bank_id=b.bank_id,
            legal_name=b.legal_name,
            name=b.legal_name,
            jurisdiction=b.jurisdiction,
            status=b.status.value if hasattr(b.status, "value") else str(b.status),
            cert_fingerprint=b.cert_fingerprint,
            vault_key_path=b.vault_key_path,
            schema_provisioned=b.schema_provisioned,
            created_at=b.created_at.isoformat(),
            activated_at=b.activated_at.isoformat() if b.activated_at else None,
        )
    except BankNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidBankStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Bank verification failure for bank_id=%s: %s", bank_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bank verification failed: {exc}",
        ) from exc


@_base_router.post(
    "/banks/{bank_id}/activate",
    response_model=BankStatusResponse,
    summary="Activate bank node",
)
async def activate_bank_node(
    bank_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> BankStatusResponse:
    """Activate an onboarded bank node."""
    service = BankOnboardingService(session)
    try:
        b = await service.activate_bank(bank_id)
        return BankStatusResponse(
            bank_id=b.bank_id,
            legal_name=b.legal_name,
            name=b.legal_name,
            jurisdiction=b.jurisdiction,
            status=b.status.value if hasattr(b.status, "value") else str(b.status),
            cert_fingerprint=b.cert_fingerprint,
            vault_key_path=b.vault_key_path,
            schema_provisioned=b.schema_provisioned,
            created_at=b.created_at.isoformat(),
            activated_at=b.activated_at.isoformat() if b.activated_at else None,
        )
    except BankNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidBankStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Bank activation failure for bank_id=%s: %s", bank_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bank activation failed: {exc}",
        ) from exc


@_base_router.post(
    "/banks/{bank_id}/suspend",
    response_model=BankStatusResponse,
    summary="Suspend an active bank node",
)
async def suspend_bank_node(
    bank_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> BankStatusResponse:
    """Suspend an active bank node."""
    service = BankOnboardingService(session)
    try:
        b = await service.suspend_bank(bank_id)
        return BankStatusResponse(
            bank_id=b.bank_id,
            legal_name=b.legal_name,
            name=b.legal_name,
            jurisdiction=b.jurisdiction,
            status=b.status.value if hasattr(b.status, "value") else str(b.status),
            cert_fingerprint=b.cert_fingerprint,
            vault_key_path=b.vault_key_path,
            schema_provisioned=b.schema_provisioned,
            created_at=b.created_at.isoformat(),
            activated_at=b.activated_at.isoformat() if b.activated_at else None,
        )
    except BankNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Bank suspension failure for bank_id=%s: %s", bank_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bank suspension failed: {exc}",
        ) from exc


@_base_router.post(
    "/banks/{bank_id}/rotate-cert",
    response_model=CertRotationResponse,
    summary="Rotate mTLS certificate for a bank node",
)
async def rotate_bank_cert(
    bank_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> CertRotationResponse:
    """Rotate mTLS certificate for an active bank node."""
    service = BankOnboardingService(session)
    b = await service.get_bank(bank_id)
    if not b:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bank node {bank_id!r} not found.",
        )

    cert_pem, key_pem = await service.issue_mtls_certificate(bank_id)
    updated = await service.get_bank(bank_id)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bank node {bank_id!r} disappeared after cert rotation — state inconsistency.",
        )

    return CertRotationResponse(
        bank_id=bank_id,
        mtls_cert_pem=cert_pem,
        mtls_key_pem=key_pem,
        cert_fingerprint=updated.cert_fingerprint or "",
    )


# ── Multi-Prefix Router Exports ───────────────────────────────────────────────
router = APIRouter(prefix="/api/v1/onboarding", tags=["Bank Onboarding"])
api_router = APIRouter(prefix="/v1/onboarding", tags=["Bank Onboarding"])

router.include_router(_base_router)
api_router.include_router(_base_router)
