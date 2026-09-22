"""Pydantic v2 schemas for European FIU & UNODC goAML / AMLA Regulatory Exporter (Phase 109).

Defines strict request/response data contracts for:
- UNODC goAML 4.0 XML report generation (STR, SAR, TTR, AIF)
- European AMLA Single Rulebook interchange format
- GDPR Art. 6(1)(f) legitimate interest & AMLD6 legal basis metadata
- Dual-control supervisory sign-off workflow
- Cryptographic digital envelope verification
- Official XSD schema validation results
- Immutable audit hash-chain receipts
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import (
    LegalBasisType,
    RegulatoryReportStatus,
    RegulatoryReportType,
    RegulatorySubmissionFormat,
    ReportingEntityRole,
)

# ── Validation Regular Expressions ─────────────────────────────────────────────
_BIC_REGEX = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
_IBAN_REGEX = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
_ISO_CURRENCY_REGEX = re.compile(r"^[A-Z]{3}$")


# ── Nested Models ─────────────────────────────────────────────────────────────

class PartySignatorySchema(BaseModel):
    """Signatory or individual subject associated with an account or transaction."""

    model_config = ConfigDict(frozen=True)

    signatory_id: str = Field(..., min_length=1, max_length=64, description="Unique client or subject identifier")
    name: str = Field(..., min_length=1, max_length=200, description="Full legal name or commercial entity name")
    role: str = Field(default="ACCOUNT_HOLDER", max_length=64, description="Role e.g. ACCOUNT_HOLDER, BENEFICIAL_OWNER, MANDATE")
    id_type: str = Field(default="NATIONAL_ID", max_length=64, description="Identification document type e.g. PASSPORT, TAX_ID, NATIONAL_ID")
    id_number_masked: str = Field(..., min_length=1, max_length=64, description="Masked identity document number")
    country_code: str = Field(default="EU", min_length=2, max_length=3, description="ISO 3166-1 alpha-2 or alpha-3 country code")


class ReportAccountSchema(BaseModel):
    """Bank account details participating in the reported suspicious flow."""

    model_config = ConfigDict(frozen=True)

    institution_name: str = Field(..., min_length=1, max_length=150, description="Financial institution commercial name")
    institution_bic: str = Field(..., min_length=8, max_length=11, description="SWIFT/BIC identifier (8 or 11 characters)")
    account_number: str = Field(..., min_length=5, max_length=34, description="IBAN or internal account number")
    currency: str = Field(default="EUR", min_length=3, max_length=3, description="ISO 4217 currency code")
    balance: Decimal | None = Field(default=None, description="Optional account balance at time of report")
    signatory: PartySignatorySchema | None = Field(default=None, description="Account holder / signatory details")

    @field_validator("institution_bic")
    @classmethod
    def validate_bic(cls, v: str) -> str:
        clean = v.strip().upper()
        if not _BIC_REGEX.match(clean):
            raise ValueError(f"Invalid BIC format '{v}'. Must be 8 or 11 alphanumeric characters.")
        return clean

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        clean = v.strip().upper()
        if not _ISO_CURRENCY_REGEX.match(clean):
            raise ValueError(f"Invalid currency code '{v}'. Must be 3 uppercase letters.")
        return clean


class RegulatoryTransactionSchema(BaseModel):
    """Structured transaction data element mapped to UNODC goAML 4.0 / AMLA schema."""

    model_config = ConfigDict(frozen=True)

    transaction_number: str = Field(..., min_length=1, max_length=64, description="Unique transaction reference or UETR")
    internal_ref_number: str = Field(..., min_length=1, max_length=64, description="Internal ledger or core-banking payment ID")
    transaction_location: str = Field(default="ONLINE_BANKING", max_length=100, description="Origination channel or physical branch")
    transaction_description: str = Field(..., min_length=3, max_length=500, description="Transaction narrative or payment memo")
    date_transaction: str = Field(..., description="Execution timestamp in ISO 8601 format (YYYY-MM-DDTHH:MM:SS)")
    value_date: str | None = Field(default=None, description="Settlement value date in YYYY-MM-DD format")
    transmode_code: str = Field(default="SEPA_INSTANT", max_length=50, description="Transmission mode (e.g. SEPA_INSTANT, WIRE, CASH, CARD)")
    amount_local: Decimal = Field(..., gt=Decimal("0.00"), description="Transaction amount in local currency")
    currency_code: str = Field(default="EUR", min_length=3, max_length=3, description="ISO 4217 currency code")
    from_funds_code: str = Field(default="TRANSFER", max_length=50, description="Funds origin category")
    from_account: ReportAccountSchema = Field(..., description="Debtor/Originator account")
    to_funds_code: str = Field(default="TRANSFER", max_length=50, description="Funds destination category")
    to_account: ReportAccountSchema = Field(..., description="Creditor/Beneficiary account")

    @field_validator("amount_local")
    @classmethod
    def validate_amount(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0.00"):
            raise ValueError("Transaction amount must be strictly greater than zero.")
        return v

    @field_validator("currency_code")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        clean = v.strip().upper()
        if not _ISO_CURRENCY_REGEX.match(clean):
            raise ValueError(f"Invalid currency code '{v}'. Must be 3 uppercase letters.")
        return clean


class LegalBasisSchema(BaseModel):
    """European AML & GDPR legal basis justifying cross-border financial intelligence processing."""

    model_config = ConfigDict(frozen=True)

    legal_basis_type: LegalBasisType = Field(
        default=LegalBasisType.AMLD6_ART_33,
        description="Statutory legal basis governing this regulatory filing",
    )
    regulatory_framework: str = Field(
        default="AMLD6",
        description="Applicable framework: AMLD6, EU_AMLA_SINGLE_RULEBOOK, or FATF_REC_20",
    )
    article_reference: str = Field(
        default="Directive (EU) 2018/1673 & 2015/849 Art. 33",
        description="Exact legislative article reference requiring notification",
    )
    legitimate_interest_justification: str = Field(
        ...,
        min_length=15,
        max_length=1000,
        description="GDPR Art. 6(1)(f) and Art. 9(2)(g) compliance documentation string",
    )
    reporting_entity_role: ReportingEntityRole = Field(
        default=ReportingEntityRole.CREDIT_INSTITUTION,
        description="Regulated sector role of the submitting institution",
    )


# ── Request Models ────────────────────────────────────────────────────────────

class CreateRegulatoryReportRequest(BaseModel):
    """Payload to construct a new European FIU / UNODC goAML regulatory report draft."""

    model_config = ConfigDict(frozen=True)

    report_type: RegulatoryReportType = Field(..., description="STR, SAR, TTR, or AIF")
    rentity_id: str = Field(..., min_length=2, max_length=64, description="Reporting institution FIU registration code")
    rentity_branch: str = Field(default="CENTRAL_COMPLIANCE", max_length=64, description="Institution branch or unit ID")
    entity_reference: str = Field(..., min_length=2, max_length=128, description="Internal case reference / investigation ID")
    reporting_user: str = Field(..., min_length=2, max_length=64, description="Compliance officer user ID preparing the report")
    reason: str = Field(..., min_length=20, max_length=4000, description="Factual narrative detailing grounds for suspicion or threshold trigger")
    action_taken: str = Field(default="ACCOUNT_FLAGGED_FOR_MONITORING", min_length=3, max_length=255, description="Mitigating action executed by bank")
    legal_basis: LegalBasisSchema = Field(..., description="Statutory justification and GDPR compliance basis")
    transactions: list[RegulatoryTransactionSchema] = Field(
        default_factory=list,
        description="List of suspicious or threshold transactions (required for STR and TTR)",
    )
    currency_code_local: str = Field(default="EUR", min_length=3, max_length=3, description="Primary accounting currency")
    fiu_ref_number: str | None = Field(default=None, max_length=64, description="Existing FIU reference if supplementary AIF filing")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        clean = v.strip()
        if len(clean) < 20:
            raise ValueError("Reason narrative must be at least 20 characters detailing grounds for suspicion.")
        return clean


class SupervisorySignoffRequest(BaseModel):
    """Dual-control supervisory sign-off request. The supervisor MUST differ from reporting_user."""

    model_config = ConfigDict(frozen=True)

    supervisor_id: str = Field(..., min_length=2, max_length=64, description="Supervisory compliance officer ID")
    approval_status: Literal["APPROVED", "REJECTED"] = Field(..., description="Approval decision")
    comments: str = Field(..., min_length=5, max_length=1000, description="Supervisory review notes / rationale")
    digital_signature_token: str | None = Field(default=None, description="Optional PKI signature token or HSM seal")


class TransmitReportRequest(BaseModel):
    """Request to transmit an approved regulatory report to the target European FIU / AMLA Hub."""

    model_config = ConfigDict(frozen=True)

    destination_fiu: str = Field(default="EU_AMLA_SUPERVISORY_HUB", min_length=2, max_length=128, description="Target FIU gateway")
    submission_format: RegulatorySubmissionFormat = Field(
        default=RegulatorySubmissionFormat.GOAML_XML_4_0,
        description="Format for payload export",
    )
    encryption_certificate_pem: str | None = Field(
        default=None,
        description="Optional recipient public certificate for envelope encryption",
    )


# ── Response Models ───────────────────────────────────────────────────────────

class DigitalEnvelopeResponse(BaseModel):
    """Cryptographic digital envelope guaranteeing integrity of the exported regulatory filing."""

    model_config = ConfigDict(frozen=True)

    report_id: str
    canonical_xml_sha256: str = Field(..., description="SHA-256 digest of canonicalized XML payload")
    digital_envelope_token: str = Field(..., description="HMAC-SHA256 digital envelope verification seal")
    timestamp: str = Field(..., description="Envelope sealing timestamp (ISO 8601)")
    algorithm: str = Field(default="SHA-256 / HMAC-SHA256", description="Digest and sealing algorithms")
    schema_version: str = Field(default="UNODC_goAML_v4.0", description="Target regulatory schema specification")


class SchemaValidationError(BaseModel):
    """Detailed validation issue identified during official schema verification."""

    model_config = ConfigDict(frozen=True)

    field_path: str = Field(..., description="XML element path or schema field where error occurred")
    error_message: str = Field(..., description="Human-readable description of schema non-compliance")
    severity: Literal["ERROR", "WARNING"] = Field(default="ERROR", description="Issue severity")


class SchemaValidationResponse(BaseModel):
    """Result of validating generated report payload against official UNODC / AMLA schemas."""

    model_config = ConfigDict(frozen=True)

    is_valid: bool = Field(..., description="True if payload complies 100% with the regulatory schema")
    schema_name: str = Field(..., description="Name of schema verified against")
    error_count: int = Field(default=0, ge=0)
    validation_errors: list[SchemaValidationError] = Field(default_factory=list)


class AuditTrailEntryResponse(BaseModel):
    """Immutable hash-chained audit record for regulatory compliance accountability."""

    model_config = ConfigDict(frozen=True)

    sequence_id: int = Field(..., ge=0)
    timestamp: str
    action: str
    actor_id: str
    detail: str
    entry_hash: str = Field(..., description="SHA-256 hash of this entry chained to prior entry")
    prev_hash: str = Field(..., description="SHA-256 hash of the previous audit record")


class RegulatoryReportSummaryResponse(BaseModel):
    """Summary record of a regulatory filing for dashboard lists."""

    model_config = ConfigDict(frozen=True)

    report_id: str
    report_type: RegulatoryReportType
    status: RegulatoryReportStatus
    rentity_id: str
    entity_reference: str
    created_at: str
    updated_at: str
    reporting_user: str
    supervisor_id: str | None = None
    transaction_count: int = 0
    total_amount_eur: Decimal = Decimal("0.00")
    digital_envelope_hash: str | None = None


class RegulatoryReportDetailResponse(BaseModel):
    """Comprehensive detail representation of a regulatory report with audit trail and payload preview."""

    model_config = ConfigDict(frozen=True)

    report_id: str
    report_type: RegulatoryReportType
    status: RegulatoryReportStatus
    rentity_id: str
    rentity_branch: str
    entity_reference: str
    fiu_ref_number: str | None = None
    reporting_user: str
    supervisor_id: str | None = None
    supervisor_comments: str | None = None
    created_at: str
    updated_at: str
    approved_at: str | None = None
    transmitted_at: str | None = None
    reason: str
    action_taken: str
    currency_code_local: str
    legal_basis: LegalBasisSchema
    transactions: list[RegulatoryTransactionSchema]
    digital_envelope: DigitalEnvelopeResponse | None = None
    audit_trail: list[AuditTrailEntryResponse] = Field(default_factory=list)


class TransmissionReceiptResponse(BaseModel):
    """Official cryptographic receipt confirming transmission to European FIU / AMLA Hub."""

    model_config = ConfigDict(frozen=True)

    transmission_id: str
    report_id: str
    destination_fiu: str
    submission_format: RegulatorySubmissionFormat
    transmitted_at: str
    status: str = "TRANSMITTED"
    digital_envelope_hash: str
    receipt_signature: str


class RegulatoryMetricsResponse(BaseModel):
    """Aggregated operational metrics across European FIU & AMLA regulatory filings."""

    model_config = ConfigDict(frozen=True)

    total_reports: int
    draft_reports: int
    pending_approval_reports: int
    approved_reports: int
    rejected_reports: int
    transmitted_reports: int
    reports_by_type: dict[str, int]
    total_suspicious_volume_eur: Decimal
    dual_control_enforcement_rate: float
    schema_compliance_rate: float
