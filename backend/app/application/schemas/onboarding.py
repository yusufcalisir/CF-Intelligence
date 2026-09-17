"""Pydantic v2 schemas for Bank Onboarding and PKI management."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BankRegisterRequest(BaseModel):
    """Payload to register a new bank node."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique bank node identifier (e.g. bank_alpha)",
    )
    legal_name: str = Field(
        ...,
        min_length=2,
        max_length=256,
        description="Legal institution name",
    )
    jurisdiction: str = Field(
        ...,
        min_length=2,
        max_length=2,
        pattern=r"^[A-Z]{2}$",
        description="ISO 3166-1 alpha-2 country code (e.g. TR, US, DE)",
    )
    contact_email: str = Field(
        ...,
        min_length=5,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        description="Primary security contact email (RFC 5322 format)",
    )
    data_residency_region: str = Field(
        ...,
        min_length=3,
        max_length=32,
        pattern=r"^[a-z0-9\-]+$",
        description="Regulatory cloud region (e.g. eu-west-1)",
    )


class BankOnboardingBundleResponse(BaseModel):
    """Complete bundle returned to a bank IT team upon registration."""

    bank_id: str
    status: str
    legal_name: str
    jurisdiction: str
    contact_email: str
    data_residency_region: str
    cert_fingerprint: str
    mtls_cert_pem: str
    mtls_key_pem: str
    connector_config_yaml: str
    coordinator_endpoint: str
    # Frontend compatibility aliases
    certificate_pem: str | None = None
    private_key_pem: str | None = None


class BankStatusResponse(BaseModel):
    """Detailed status of a bank node."""

    bank_id: str
    legal_name: str
    jurisdiction: str
    status: str
    cert_fingerprint: str | None = None
    vault_key_path: str | None = None
    schema_provisioned: bool
    created_at: str
    activated_at: str | None = None
    name: str | None = None


class CertRotationResponse(BaseModel):
    """Response after rotating a bank's mTLS certificate."""

    bank_id: str
    mtls_cert_pem: str
    mtls_key_pem: str
    cert_fingerprint: str


class BankCSRSignRequest(BaseModel):
    """Payload to request consortium CA signing for an institutional CSR."""

    model_config = ConfigDict(extra="forbid")

    csr_pem: str = Field(..., description="PEM-encoded X.509 Certificate Signing Request")
    days_valid: int = Field(365, ge=1, le=1825, description="Validity period in days")


class BankCSRSignResponse(BaseModel):
    """Response returned upon signing an institutional CSR."""

    bank_id: str
    signed_cert_pem: str
    cert_fingerprint: str
    expires_at: str


class BankCABundleResponse(BaseModel):
    """Consortium Root Certificate Authority bundle."""

    root_ca_pem: str = Field(..., description="PEM-encoded Consortium Root CA certificate")
    ca_fingerprint: str = Field(..., description="SHA-256 fingerprint of the Root CA certificate")
    issuer: str = Field(..., description="Distinguished Name (DN) of the Root CA")
    valid_until: str = Field(..., description="ISO 8601 expiration timestamp of the Root CA")
    crl_distribution_point: str = Field(
        ..., description="URL endpoint for Certificate Revocation List (CRL)"
    )
