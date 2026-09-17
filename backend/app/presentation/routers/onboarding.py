"""Bank Onboarding API Router — Multi-Prefix PKI & Institutional Lifecycle.

Admin endpoints for registering new bank nodes, issuing mTLS certificates,
provisioning tenant schemas, downloading root CA trust bundles, and retrieving onboarding bundles.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.application.schemas.onboarding import (
    BankCABundleResponse,
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

# Multi-prefix router declarations for zero-breakage backward compatibility
router = APIRouter(prefix="/api/v1/onboarding", tags=["Bank Onboarding"])
api_router = APIRouter(prefix="/v1/onboarding", tags=["Bank Onboarding"])

# Re-export models for backward compatibility
__all__ = [
    "BankCABundleResponse",
    "BankCSRSignRequest",
    "BankCSRSignResponse",
    "BankOnboardingBundleResponse",
    "BankRegisterRequest",
    "BankStatusResponse",
    "CertRotationResponse",
    "api_router",
    "router",
]


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/ca-bundle",
    response_model=BankCABundleResponse,
    summary="Download Consortium Root CA bundle for mTLS trust store",
)
@api_router.get(
    "/ca-bundle",
    response_model=BankCABundleResponse,
    summary="Download Consortium Root CA bundle for mTLS trust store",
)
async def get_ca_bundle() -> BankCABundleResponse:
    """Returns the Consortium Root Certificate Authority certificate PEM and fingerprint."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    from app.infrastructure.security.cert_generator import generate_self_signed_pem

    ca_pem, _ = generate_self_signed_pem(
        common_name="CF-Intelligence Consortium Root CA",
        days_valid=3650,
    )
    cert = x509.load_pem_x509_certificate(ca_pem.encode("utf-8"))
    fingerprint = f"SHA256:{cert.fingerprint(hashes.SHA256()).hex()}"
    valid_until = cert.not_valid_after_utc.isoformat()

    return BankCABundleResponse(
        root_ca_pem=ca_pem,
        ca_fingerprint=fingerprint,
        issuer="CN=CF-Intelligence Consortium Root CA, O=CF-Intelligence Consortium",
        valid_until=valid_until,
        crl_distribution_point="https://coordinator.cf-intelligence.io/api/v1/onboarding/crl",
    )


@router.post(
    "/register",
    response_model=BankOnboardingBundleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new bank node and receive the onboarding bundle",
)
@api_router.post(
    "/register",
    response_model=BankOnboardingBundleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new bank node and receive the onboarding bundle",
)
async def register_bank(
    payload: BankRegisterRequest,
    session: AsyncSession = Depends(get_async_session),
) -> Any:
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


@router.get(
    "/bundle/{bank_id}",
    response_model=BankOnboardingBundleResponse,
    summary="Retrieve onboarding bundle and configuration for an onboarded bank node",
)
@api_router.get(
    "/bundle/{bank_id}",
    response_model=BankOnboardingBundleResponse,
    summary="Retrieve onboarding bundle and configuration for an onboarded bank node",
)
async def get_onboarding_bundle(
    bank_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
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


@router.get(
    "/banks",
    response_model=list[BankStatusResponse],
    summary="List all registered bank nodes",
)
@api_router.get(
    "/banks",
    response_model=list[BankStatusResponse],
    summary="List all registered bank nodes",
)
async def list_banks(
    session: AsyncSession = Depends(get_async_session),
) -> Any:
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


@router.get(
    "/banks/{bank_id}/status",
    response_model=BankStatusResponse,
    summary="Get status of a single bank node",
)
@api_router.get(
    "/banks/{bank_id}/status",
    response_model=BankStatusResponse,
    summary="Get status of a single bank node",
)
async def get_bank_status(
    bank_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
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


@router.post(
    "/banks/{bank_id}/sign-csr",
    response_model=BankCSRSignResponse,
    summary="Cryptographically sign an institutional CSR via PKI wizard",
)
@api_router.post(
    "/banks/{bank_id}/sign-csr",
    response_model=BankCSRSignResponse,
    summary="Cryptographically sign an institutional CSR via PKI wizard",
)
async def sign_bank_csr(
    payload: BankCSRSignRequest,
    bank_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
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


@router.post(
    "/banks/{bank_id}/verify",
    response_model=BankStatusResponse,
    summary="Record institutional compliance verification",
)
@api_router.post(
    "/banks/{bank_id}/verify",
    response_model=BankStatusResponse,
    summary="Record institutional compliance verification",
)
async def verify_bank_node(
    bank_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
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


@router.post(
    "/banks/{bank_id}/activate",
    response_model=BankStatusResponse,
    summary="Activate bank node",
)
@api_router.post(
    "/banks/{bank_id}/activate",
    response_model=BankStatusResponse,
    summary="Activate bank node",
)
async def activate_bank_node(
    bank_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
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


@router.post(
    "/banks/{bank_id}/suspend",
    response_model=BankStatusResponse,
    summary="Suspend an active bank node",
)
@api_router.post(
    "/banks/{bank_id}/suspend",
    response_model=BankStatusResponse,
    summary="Suspend an active bank node",
)
async def suspend_bank_node(
    bank_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
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


@router.post(
    "/banks/{bank_id}/rotate-cert",
    response_model=CertRotationResponse,
    summary="Rotate mTLS certificate for a bank node",
)
@api_router.post(
    "/banks/{bank_id}/rotate-cert",
    response_model=CertRotationResponse,
    summary="Rotate mTLS certificate for a bank node",
)
async def rotate_bank_cert(
    bank_id: str = Path(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
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
