"""Bank Onboarding Pydantic v2 Application Schemas.

Strict validation models for bank node registration, mTLS certificate issuance,
tenant schema provisioning, and X.509 CSR signing.
"""

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

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(..., description="Bank node identifier")
    status: str = Field(..., description="Current onboarding lifecycle status")
    legal_name: str = Field(..., description="Legal institution name")
    jurisdiction: str = Field(..., description="ISO 3166-1 alpha-2 country code")
    contact_email: str = Field(..., description="Security contact email")
    data_residency_region: str = Field(..., description="Assigned cloud data residency region")
    cert_fingerprint: str = Field(..., description="SHA-256 fingerprint of issued mTLS certificate")
    mtls_cert_pem: str = Field(..., description="PEM-encoded X.509 client certificate")
    mtls_key_pem: str = Field(..., description="PEM-encoded RSA/ECDSA private key")
    connector_config_yaml: str = Field(..., description="Ready-to-deploy YAML connector configuration")
    coordinator_endpoint: str = Field(..., description="mTLS gRPC/HTTPS coordinator endpoint URL")
    certificate_pem: str | None = Field(default=None, description="Frontend compatibility alias for mtls_cert_pem")
    private_key_pem: str | None = Field(default=None, description="Frontend compatibility alias for mtls_key_pem")


class BankStatusResponse(BaseModel):
    """Detailed status of a bank node."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(..., description="Bank node identifier")
    legal_name: str = Field(..., description="Legal institution name")
    jurisdiction: str = Field(..., description="ISO 3166-1 alpha-2 country code")
    status: str = Field(..., description="Current status (e.g. ACTIVE, PENDING_VERIFICATION, SUSPENDED)")
    cert_fingerprint: str | None = Field(default=None, description="Certificate SHA-256 fingerprint")
    vault_key_path: str | None = Field(default=None, description="HashiCorp Vault KMS transit path")
    schema_provisioned: bool = Field(..., description="Whether tenant PostgreSQL schema is provisioned")
    created_at: str = Field(..., description="ISO timestamp of bank record registration")
    activated_at: str | None = Field(default=None, description="ISO timestamp of activation")
    name: str | None = Field(default=None, description="Frontend compatibility alias for legal_name")


class CertRotationResponse(BaseModel):
    """Response after rotating a bank's mTLS certificate."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(..., description="Bank node identifier")
    mtls_cert_pem: str = Field(..., description="PEM-encoded renewed X.509 certificate")
    mtls_key_pem: str = Field(..., description="PEM-encoded renewed private key")
    cert_fingerprint: str = Field(..., description="SHA-256 fingerprint of new certificate")


class BankCSRSignRequest(BaseModel):
    """Payload to request consortium CA signing for an institutional CSR."""

    model_config = ConfigDict(extra="forbid")

    csr_pem: str = Field(..., description="PEM-encoded X.509 Certificate Signing Request")
    days_valid: int = Field(365, ge=1, le=1825, description="Validity period in days")


class BankCSRSignResponse(BaseModel):
    """Response returned upon signing an institutional CSR."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(..., description="Bank node identifier")
    signed_cert_pem: str = Field(..., description="PEM-encoded consortium-signed X.509 certificate")
    cert_fingerprint: str = Field(..., description="SHA-256 fingerprint of signed certificate")
    expires_at: str = Field(..., description="ISO timestamp of certificate expiration")
