"""Open Banking PSD2 and ISO 20022 Application Schemas.

Defines Pydantic v2 validation models for Berlin Group NextGenPSD2 AISP/PISP
flows, consent management, ISO 20022 XML message parsing, and payment initiation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConsentRequest(BaseModel):
    """Request payload for establishing a PSD2 XS2A consent."""

    account_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        description="Debtor account identifier or primary IBAN for consent access",
    )
    permissions: list[str] = Field(
        default_factory=lambda: ["read_accounts", "read_transactions"],
        description="List of requested scopes (e.g. read_accounts, read_transactions, initiate_payments)",
    )
    valid_until: float = Field(
        ...,
        description="Epoch timestamp representing the consent expiration deadline",
    )
    debtor_iban: str | None = Field(
        default=None,
        description="Optional ISO 13616 IBAN associated with this consent",
    )
    tenant_id: str | None = Field(
        default=None,
        description="Bank participant tenant identifier (defaults to debtor bank or active tenant)",
    )

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConsentResponse(BaseModel):
    """Response payload representing established or queried PSD2 consent state."""

    consent_id: str = Field(..., description="Unique consent identifier token")
    status: str = Field(..., description="Consent status: 'valid', 'expired', or 'revoked'")
    account_id: str = Field(..., description="Consented account identifier")
    permissions: list[str] = Field(..., description="Granted access scopes")
    valid_until: float = Field(..., description="Expiration epoch timestamp")
    debtor_iban: str | None = Field(default=None, description="Associated account IBAN")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    client_id: str = Field(..., description="AISP/PISP third-party provider client ID")
    tenant_id: str = Field(..., description="Host bank tenant ID")

    model_config = ConfigDict(extra="ignore")


class AccountResponse(BaseModel):
    """Response model for consented customer bank accounts."""

    account_id: str = Field(..., description="Unique account identifier")
    iban: str = Field(..., description="Normalized ISO 13616 IBAN")
    currency: str = Field(default="EUR", description="ISO 4217 currency code")
    balance: float = Field(..., description="Current cleared account balance")
    bank_name: str = Field(..., description="Managing financial institution name")
    status: str = Field(default="enabled", description="Account operating status")

    model_config = ConfigDict(extra="ignore")


class TransactionResponse(BaseModel):
    """Response model for consented account transaction histories."""

    transaction_id: str = Field(..., description="Unique transaction ID")
    amount: float = Field(..., description="Transaction transfer amount")
    currency: str = Field(default="EUR", description="ISO 4217 currency code")
    booking_date: str = Field(..., description="Booking date/time in ISO 8601")
    debtor_name: str = Field(..., description="Debtor originator display name")
    creditor_name: str = Field(..., description="Creditor beneficiary display name")
    remittance_info: str = Field(..., description="Remittance / reference information")
    status: str = Field(default="booked", description="Transaction booking status ('booked', 'pending')")

    model_config = ConfigDict(extra="ignore")


class PaymentInitiationRequest(BaseModel):
    """Request payload for NextGenPSD2 payment initiation (PISP)."""

    debtor_account: str = Field(
        ...,
        min_length=15,
        max_length=34,
        description="Debtor account IBAN (validated with ISO 13616 Mod-97 checksum)",
    )
    creditor_account: str = Field(
        ...,
        min_length=15,
        max_length=34,
        description="Creditor account IBAN (validated with ISO 13616 Mod-97 checksum)",
    )
    instructed_amount: float = Field(
        ...,
        gt=0.0,
        description="Payment instruction amount, must be strictly greater than 0",
    )
    currency: str = Field(
        default="EUR",
        min_length=3,
        max_length=3,
        description="ISO 4217 3-letter currency code (e.g. EUR, USD, GBP)",
    )
    creditor_name: str = Field(
        ...,
        min_length=1,
        max_length=140,
        description="Legal or commercial name of the creditor beneficiary",
    )
    debtor_name: str = Field(
        default="Consented Customer",
        min_length=1,
        max_length=140,
        description="Legal or registered name of the debtor originator",
    )
    remittance_information: str | None = Field(
        default=None,
        max_length=140,
        description="Unstructured remittance reference or invoice note",
    )
    payment_product: str = Field(
        default="sepa-credit-transfers",
        description="Payment rail: 'sepa-credit-transfers', 'instant-sepa-credit-transfers', 'cross-border-credit-transfers'",
    )
    consent_id: str | None = Field(
        default=None,
        description="Pre-authorized PSD2 consent token validating payment initiation rights",
    )
    debtor_agent_bic: str | None = Field(
        default=None,
        min_length=8,
        max_length=11,
        description="Debtor bank ISO 9362 BIC/SWIFT code",
    )
    creditor_agent_bic: str | None = Field(
        default=None,
        min_length=8,
        max_length=11,
        description="Creditor bank ISO 9362 BIC/SWIFT code",
    )
    tenant_id: str | None = Field(
        default=None,
        description="Target bank tenant ID for multi-tenant isolation",
    )

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PaymentInitiationResponse(BaseModel):
    """Response payload for initiated PSD2 payment transfer."""

    payment_id: str = Field(..., description="Unique payment identifier (e.g. pmt_...)")
    transaction_status: str = Field(
        ...,
        description="Berlin Group payment status: 'RCVD' (Received), 'ACTC' (Accepted Technical), 'ACSP' (Settlement in Process), 'ACCP' (Accepted Customer Profile), 'RJCT' (Rejected)",
    )
    debtor_account: str = Field(..., description="Debtor account IBAN")
    creditor_account: str = Field(..., description="Creditor account IBAN")
    instructed_amount: float = Field(..., description="Settlement amount")
    currency: str = Field(..., description="ISO 4217 currency code")
    creditor_name: str = Field(..., description="Beneficiary name")
    payment_product: str = Field(..., description="Payment execution rail")
    risk_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Real-time AML/fraud risk score evaluated by CFI inference engine",
    )
    is_flagged_for_review: bool = Field(
        ...,
        description="True if transaction velocity or score triggered four-eyes compliance review",
    )
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    estimated_settlement: str = Field(..., description="Estimated settlement timeframe or status")
    tenant_id: str = Field(..., description="Bank tenant identifier")

    model_config = ConfigDict(extra="ignore")


class PaymentStatusResponse(BaseModel):
    """Response payload representing current lifecycle status of a payment."""

    payment_id: str = Field(..., description="Unique payment reference")
    transaction_status: str = Field(
        ...,
        description="Current status: 'RCVD', 'ACTC', 'ACSP', 'ACCP', 'RJCT'",
    )
    debtor_account: str = Field(..., description="Debtor account IBAN")
    creditor_account: str = Field(..., description="Creditor account IBAN")
    instructed_amount: float = Field(..., description="Settlement amount")
    currency: str = Field(..., description="ISO 4217 currency code")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    last_updated: str = Field(..., description="ISO 8601 status update timestamp")
    clearing_system_ref: str | None = Field(
        default=None,
        description="Target clearing system reference (e.g. TARGET2, EBA STEP2)",
    )
    tenant_id: str = Field(..., description="Bank tenant identifier")

    model_config = ConfigDict(extra="ignore")


class ISO20022ParseRequest(BaseModel):
    """Request payload for parsing and validating ISO 20022 or SWIFT MT103 financial messages."""

    raw_content: str = Field(
        ...,
        min_length=10,
        description="Raw message text (XML document or SWIFT MT103 message block)",
    )
    message_type: str = Field(
        default="auto",
        description="Message standard hint: 'auto', 'pacs.008', 'pain.001', 'mt103'",
    )
    anonymize_pii: bool = Field(
        default=True,
        description="Whether to generate zero-PII HMAC-SHA256 privacy-preserving features",
    )
    salt: str = Field(
        default="cf_privacy_salt_2026",
        description="HMAC cryptographic salt for zero-PII feature transformation",
    )

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ISO20022ParseResponse(BaseModel):
    """Normalized response payload parsed from standard financial messages."""

    message_type: str = Field(..., description="Identified standard (e.g. ISO20022_PACS008, SEPA_SCT, SWIFT_MT103)")
    transaction_id: str = Field(..., description="Parsed end-to-end or transaction reference")
    amount: float = Field(..., description="Extracted instruction/settlement amount")
    currency: str = Field(..., description="ISO 4217 currency code")
    date: str = Field(..., description="Settlement or booking date")
    sender_name: str | None = Field(default=None, description="Sender/debtor legal name")
    sender_account: str = Field(..., description="Sender account number or IBAN")
    sender_bic: str | None = Field(default=None, description="Sender financial institution BIC")
    sender_country: str = Field(..., description="Extracted ISO 3166-1 alpha-2 country code")
    receiver_name: str | None = Field(default=None, description="Receiver/creditor legal name")
    receiver_account: str = Field(..., description="Receiver account number or IBAN")
    receiver_bic: str | None = Field(default=None, description="Receiver financial institution BIC")
    receiver_country: str = Field(..., description="Extracted ISO 3166-1 alpha-2 country code")
    remittance_info: str | None = Field(default=None, description="Remittance unstructured description")
    is_valid_debtor_iban: bool = Field(..., description="ISO 13616 Mod-97 validation for debtor account")
    is_valid_creditor_iban: bool = Field(..., description="ISO 13616 Mod-97 validation for creditor account")
    privacy_features: dict[str, Any] | None = Field(
        default=None,
        description="HMAC-SHA256 transformed zero-PII feature vector for downstream FL models",
    )

    model_config = ConfigDict(extra="ignore")
