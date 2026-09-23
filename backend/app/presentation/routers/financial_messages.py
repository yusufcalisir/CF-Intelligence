"""Extended ISO 20022 Financial Rails Presentation Router (camt.053, pacs.002, pacs.003).

Exposes REST endpoints for ingesting, validating, privacy-transforming, and risk-scoring
extended ISO 20022 messages:
- camt.053.001.08: Bank-to-Customer Statement batch XML with balance snapshots & entries
- pacs.002.001.10: Financial Institutional Payment Status Report with clearing reason codes
- pacs.003.001.08: Customer Direct Debit with unauthorized pull fraud risk assessment

Routes:
    POST /financial-messages/parse
    POST /api/v1/financial-messages/parse
    POST /financial-messages/camt053/parse
    POST /api/v1/financial-messages/camt053/parse
    POST /financial-messages/pacs002/parse
    POST /api/v1/financial-messages/pacs002/parse
    POST /financial-messages/pacs003/parse
    POST /api/v1/financial-messages/pacs003/parse
    POST /financial-messages/pacs003/score-risk
    POST /api/v1/financial-messages/pacs003/score-risk
    GET  /financial-messages/supported-standards
    GET  /api/v1/financial-messages/supported-standards
    GET  /financial-messages/health
    GET  /api/v1/financial-messages/health
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.application.services.financial_message_parser import (
    FinancialMessageParser,
    FinancialMessageParserError,
)

logger = logging.getLogger(__name__)

# ── Schemas ──────────────────────────────────────────────────────────────────


class GenericMessageParseRequest(BaseModel):
    """Generic payload for auto-detecting or explicitly parsing financial messages."""

    raw_content: str = Field(..., description="Raw message payload string (XML or SWIFT text)")
    message_type: str = Field("auto", description="Standard hint ('auto', 'camt.053', 'pacs.002', 'pacs.003', 'pacs.008', 'pain.001', 'mt103')")
    anonymize_pii: bool = Field(False, description="Whether to transform customer identifiers into zero-PII salted HMAC hashes")
    salt: str = Field("cf_privacy_salt_2026", description="Cryptographic salt for privacy transformations")


class Camt053ParseRequest(BaseModel):
    """Payload for parsing an ISO 20022 camt.053 Bank-to-Customer Statement XML."""

    raw_content: str = Field(..., description="camt.053 XML content")
    anonymize_pii: bool = Field(False, description="Whether to compute zero-PII privacy features")
    salt: str = Field("cf_privacy_salt_2026", description="Privacy salt")


class Pacs002ParseRequest(BaseModel):
    """Payload for parsing an ISO 20022 pacs.002 Payment Status Report XML."""

    raw_content: str = Field(..., description="pacs.002 XML content")
    anonymize_pii: bool = Field(False, description="Whether to compute zero-PII privacy features")
    salt: str = Field("cf_privacy_salt_2026", description="Privacy salt")


class Pacs003ParseRequest(BaseModel):
    """Payload for parsing an ISO 20022 pacs.003 Customer Direct Debit XML."""

    raw_content: str = Field(..., description="pacs.003 XML content")
    score_risk: bool = Field(True, description="Whether to execute automated heuristic debit-pull risk assessment")
    anonymize_pii: bool = Field(False, description="Whether to compute zero-PII privacy features")
    salt: str = Field("cf_privacy_salt_2026", description="Privacy salt")


class DirectDebitRiskScoreRequest(BaseModel):
    """Payload for scoring direct debit fraud risk from XML or pre-parsed dict."""

    raw_content: str | None = Field(None, description="Raw pacs.003 XML string")
    parsed_payload: dict[str, Any] | None = Field(None, description="Pre-parsed pacs.003 dictionary")


class StatementEntryResponse(BaseModel):
    """Individual transaction entry extracted from a camt.053 bank statement."""

    entry_reference: str
    amount: float
    currency: str
    credit_debit_indicator: str
    status: str
    booking_date: str
    value_date: str
    counterparty_name: str
    counterparty_iban: str
    counterparty_bic: str
    counterparty_country: str
    end_to_end_id: str
    remittance_info: str


class BalanceSnapshotResponse(BaseModel):
    """Opening or closing balance record."""

    balance_type: str
    amount: float
    currency: str
    credit_debit_indicator: str
    date: str


class Camt053StatementResponse(BaseModel):
    """Parsed camt.053 statement structure."""

    message_type: str = "ISO20022_CAMT053"
    statement_id: str
    transaction_id: str
    account_iban: str
    account_currency: str
    servicer_bic: str
    statement_date: str
    amount: float
    currency: str
    opening_balance: BalanceSnapshotResponse | None = None
    closing_balance: BalanceSnapshotResponse | None = None
    entries_count: int
    total_credit_amount: float
    total_debit_amount: float
    entries: list[StatementEntryResponse]
    is_valid_account_iban: bool
    privacy_features: dict[str, Any] | None = None


class Pacs002StatusResponse(BaseModel):
    """Parsed pacs.002 payment status report."""

    message_type: str = "ISO20022_PACS002"
    status_message_id: str
    transaction_id: str
    status: str
    is_rejected: bool
    reason_code: str
    reason_description: str
    additional_info: str
    amount: float
    currency: str
    date: str
    original_msg_id: str
    original_msg_name: str
    original_end_to_end_id: str
    original_tx_id: str
    sender_name: str
    sender_account: str
    sender_bic: str
    sender_country: str
    receiver_name: str
    receiver_account: str
    receiver_bic: str
    receiver_country: str
    remittance_info: str
    is_valid_debtor_iban: bool
    is_valid_creditor_iban: bool
    privacy_features: dict[str, Any] | None = None


class DirectDebitRiskResponse(BaseModel):
    """Heuristic direct debit pull risk assessment result."""

    transaction_id: str
    risk_score: float = Field(description="Normalized risk score from 0.000 (safe) to 1.000 (critical)")
    risk_level: str = Field(description="'LOW', 'MEDIUM', or 'HIGH'")
    recommended_action: str = Field(description="'ALLOW', 'FLAG_FOR_CONFIRMATION', or 'REJECT_AND_HOLD'")
    score_points: int
    risk_factors: list[str]
    sequence_type: str
    amount: float
    currency: str
    debtor_country: str
    creditor_country: str
    mandate_id: str


class Pacs003DirectDebitResponse(BaseModel):
    """Parsed pacs.003 direct debit message with optional risk evaluation."""

    message_type: str = "ISO20022_PACS003"
    transaction_id: str
    amount: float
    currency: str
    date: str
    sequence_type: str
    mandate_id: str
    mandate_signature_date: str
    creditor_scheme_id: str
    sender_name: str
    sender_account: str
    sender_bic: str
    sender_country: str
    receiver_name: str
    receiver_account: str
    receiver_bic: str
    receiver_country: str
    remittance_info: str
    is_valid_debtor_iban: bool
    is_valid_creditor_iban: bool
    risk_assessment: DirectDebitRiskResponse | None = None
    privacy_features: dict[str, Any] | None = None


class GenericFinancialMessageResponse(BaseModel):
    """Unified normalized response for parsed financial messages."""

    message_type: str
    transaction_id: str
    amount: float
    currency: str
    date: str
    sender_name: str | None = None
    sender_account: str
    sender_bic: str | None = None
    sender_country: str
    receiver_name: str | None = None
    receiver_account: str
    receiver_bic: str | None = None
    receiver_country: str
    remittance_info: str | None = None
    is_valid_debtor_iban: bool
    is_valid_creditor_iban: bool
    privacy_features: dict[str, Any] | None = None
    raw_parsed_data: dict[str, Any]


class SupportedStandardInfo(BaseModel):
    """Specification of an ingested financial standard."""

    standard_code: str
    business_name: str
    schema_version: str
    typical_channel: str
    fraud_risk_vectors: list[str]
    supported_operations: list[str]


class SupportedStandardsResponse(BaseModel):
    """Catalogue of all supported financial rail specifications."""

    total_standards: int
    standards: list[SupportedStandardInfo]


# ── Base Router Implementation ───────────────────────────────────────────────

_base_router = APIRouter()


@_base_router.post("/parse", response_model=GenericFinancialMessageResponse)
async def parse_any_financial_message(
    payload: GenericMessageParseRequest,
) -> GenericFinancialMessageResponse:
    """Auto-detect or explicitly parse ISO 20022, SEPA, or SWIFT financial messages."""
    try:
        parsed = FinancialMessageParser.parse_message(payload.raw_content, payload.message_type)
    except FinancialMessageParserError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Financial message parse error: {exc}",
        ) from exc

    sender_acct = str(parsed.get("sender_account") or parsed.get("account_iban") or "")
    receiver_acct = str(parsed.get("receiver_account") or "")

    is_valid_sender = FinancialMessageParser.validate_iban(sender_acct)
    is_valid_receiver = FinancialMessageParser.validate_iban(receiver_acct)

    privacy_feats = None
    if payload.anonymize_pii:
        privacy_feats = FinancialMessageParser.to_privacy_preserving_features(
            parsed, salt=payload.salt
        )

    return GenericFinancialMessageResponse(
        message_type=parsed.get("message_type", "UNKNOWN"),
        transaction_id=parsed.get("transaction_id", "unknown_tx_id"),
        amount=float(parsed.get("amount", 0.0)),
        currency=str(parsed.get("currency", "EUR")),
        date=str(parsed.get("date", "")),
        sender_name=parsed.get("sender_name") or None,
        sender_account=sender_acct,
        sender_bic=parsed.get("sender_bic") or parsed.get("servicer_bic") or None,
        sender_country=str(parsed.get("sender_country", "XX")),
        receiver_name=parsed.get("receiver_name") or None,
        receiver_account=receiver_acct,
        receiver_bic=parsed.get("receiver_bic") or None,
        receiver_country=str(parsed.get("receiver_country", "XX")),
        remittance_info=parsed.get("remittance_info") or None,
        is_valid_debtor_iban=is_valid_sender,
        is_valid_creditor_iban=is_valid_receiver,
        privacy_features=privacy_feats,
        raw_parsed_data=parsed,
    )


@_base_router.post("/camt053/parse", response_model=Camt053StatementResponse)
async def parse_camt053_statement(
    payload: Camt053ParseRequest,
) -> Camt053StatementResponse:
    """Parse and validate an ISO 20022 camt.053.001.08 Bank-to-Customer Statement XML."""
    try:
        parsed = FinancialMessageParser.parse_iso_20022_camt053(payload.raw_content)
    except FinancialMessageParserError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"camt.053 parse failure: {exc}",
        ) from exc

    acct_iban = str(parsed.get("account_iban") or "")
    is_valid_iban = FinancialMessageParser.validate_iban(acct_iban)

    privacy_feats = None
    if payload.anonymize_pii:
        privacy_feats = FinancialMessageParser.to_privacy_preserving_features(
            parsed, salt=payload.salt
        )

    op_bal = parsed.get("opening_balance")
    cl_bal = parsed.get("closing_balance")

    return Camt053StatementResponse(
        message_type="ISO20022_CAMT053",
        statement_id=parsed["statement_id"],
        transaction_id=parsed["transaction_id"],
        account_iban=acct_iban,
        account_currency=parsed["account_currency"],
        servicer_bic=parsed.get("servicer_bic") or "",
        statement_date=parsed.get("statement_date") or "",
        amount=parsed["amount"],
        currency=parsed["currency"],
        opening_balance=BalanceSnapshotResponse(**op_bal) if op_bal else None,
        closing_balance=BalanceSnapshotResponse(**cl_bal) if cl_bal else None,
        entries_count=parsed["entries_count"],
        total_credit_amount=parsed["total_credit_amount"],
        total_debit_amount=parsed["total_debit_amount"],
        entries=[StatementEntryResponse(**e) for e in parsed.get("entries", [])],
        is_valid_account_iban=is_valid_iban,
        privacy_features=privacy_feats,
    )


@_base_router.post("/pacs002/parse", response_model=Pacs002StatusResponse)
async def parse_pacs002_status_report(
    payload: Pacs002ParseRequest,
) -> Pacs002StatusResponse:
    """Parse an ISO 20022 pacs.002.001.10 Payment Status Report XML."""
    try:
        parsed = FinancialMessageParser.parse_iso_20022_pacs002(payload.raw_content)
    except FinancialMessageParserError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"pacs.002 parse failure: {exc}",
        ) from exc

    sender_acct = str(parsed.get("sender_account") or "")
    receiver_acct = str(parsed.get("receiver_account") or "")

    privacy_feats = None
    if payload.anonymize_pii:
        privacy_feats = FinancialMessageParser.to_privacy_preserving_features(
            parsed, salt=payload.salt
        )

    return Pacs002StatusResponse(
        message_type="ISO20022_PACS002",
        status_message_id=parsed["status_message_id"],
        transaction_id=parsed["transaction_id"],
        status=parsed["status"],
        is_rejected=parsed["is_rejected"],
        reason_code=parsed["reason_code"],
        reason_description=parsed["reason_description"],
        additional_info=parsed["additional_info"],
        amount=parsed["amount"],
        currency=parsed["currency"],
        date=parsed["date"],
        original_msg_id=parsed["original_msg_id"],
        original_msg_name=parsed["original_msg_name"],
        original_end_to_end_id=parsed["original_end_to_end_id"],
        original_tx_id=parsed["original_tx_id"],
        sender_name=parsed["sender_name"],
        sender_account=sender_acct,
        sender_bic=parsed["sender_bic"],
        sender_country=parsed["sender_country"],
        receiver_name=parsed["receiver_name"],
        receiver_account=receiver_acct,
        receiver_bic=parsed["receiver_bic"],
        receiver_country=parsed["receiver_country"],
        remittance_info=parsed["remittance_info"],
        is_valid_debtor_iban=FinancialMessageParser.validate_iban(sender_acct),
        is_valid_creditor_iban=FinancialMessageParser.validate_iban(receiver_acct),
        privacy_features=privacy_feats,
    )


@_base_router.post("/pacs003/parse", response_model=Pacs003DirectDebitResponse)
async def parse_pacs003_direct_debit(
    payload: Pacs003ParseRequest,
) -> Pacs003DirectDebitResponse:
    """Parse and optionally risk-score an ISO 20022 pacs.003.001.08 Direct Debit payload."""
    try:
        parsed = FinancialMessageParser.parse_iso_20022_pacs003(payload.raw_content)
    except FinancialMessageParserError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"pacs.003 parse failure: {exc}",
        ) from exc

    sender_acct = str(parsed.get("sender_account") or "")
    receiver_acct = str(parsed.get("receiver_account") or "")

    risk_eval: DirectDebitRiskResponse | None = None
    if payload.score_risk:
        scored = FinancialMessageParser.score_direct_debit_risk(parsed)
        risk_eval = DirectDebitRiskResponse(**scored)
        parsed["risk_score"] = scored["risk_score"]
        parsed["risk_level"] = scored["risk_level"]

    privacy_feats = None
    if payload.anonymize_pii:
        privacy_feats = FinancialMessageParser.to_privacy_preserving_features(
            parsed, salt=payload.salt
        )

    return Pacs003DirectDebitResponse(
        message_type="ISO20022_PACS003",
        transaction_id=parsed["transaction_id"],
        amount=parsed["amount"],
        currency=parsed["currency"],
        date=parsed["date"],
        sequence_type=parsed["sequence_type"],
        mandate_id=parsed["mandate_id"],
        mandate_signature_date=parsed["mandate_signature_date"],
        creditor_scheme_id=parsed["creditor_scheme_id"],
        sender_name=parsed["sender_name"],
        sender_account=sender_acct,
        sender_bic=parsed["sender_bic"],
        sender_country=parsed["sender_country"],
        receiver_name=parsed["receiver_name"],
        receiver_account=receiver_acct,
        receiver_bic=parsed["receiver_bic"],
        receiver_country=parsed["receiver_country"],
        remittance_info=parsed["remittance_info"],
        is_valid_debtor_iban=FinancialMessageParser.validate_iban(sender_acct),
        is_valid_creditor_iban=FinancialMessageParser.validate_iban(receiver_acct),
        risk_assessment=risk_eval,
        privacy_features=privacy_feats,
    )


@_base_router.post("/pacs003/score-risk", response_model=DirectDebitRiskResponse)
async def score_direct_debit_payload(
    payload: DirectDebitRiskScoreRequest,
) -> DirectDebitRiskResponse:
    """Evaluate heuristic unauthorized debit pull risk on raw pacs.003 XML or pre-parsed dict."""
    if payload.parsed_payload:
        data = payload.parsed_payload
    elif payload.raw_content:
        try:
            data = FinancialMessageParser.parse_iso_20022_pacs003(payload.raw_content)
        except FinancialMessageParserError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"pacs.003 parse failure before scoring: {exc}",
            ) from exc
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'raw_content' or 'parsed_payload' must be provided",
        )

    scored = FinancialMessageParser.score_direct_debit_risk(data)
    return DirectDebitRiskResponse(**scored)


@_base_router.get("/supported-standards", response_model=SupportedStandardsResponse)
async def get_supported_financial_standards() -> SupportedStandardsResponse:
    """List all supported ISO 20022 MX, SEPA SCT/SDD, and SWIFT MT financial messaging rails."""
    standards_catalog = [
        SupportedStandardInfo(
            standard_code="camt.053.001.08",
            business_name="Bank-to-Customer Statement",
            schema_version="camt.053.001.08",
            typical_channel="Batch Clearing / Core Banking / Treasury",
            fraud_risk_vectors=["Account Takeover Reconnaissance", "Balance Distortion", "Mule Account Velocity Spike"],
            supported_operations=["Multi-entry reconciliation", "Opening/Closing balance audit", "Privacy feature extraction"],
        ),
        SupportedStandardInfo(
            standard_code="pacs.002.001.10",
            business_name="Payment Status Report",
            schema_version="pacs.002.001.10",
            typical_channel="Inter-Bank Clearing / RT1 / TIPS / FedNow",
            fraud_risk_vectors=["Clearing Rejection Evasion", "AC04/AG01 Account Status Abuse", "Rapid Retry Storms"],
            supported_operations=["Clearing reason code resolution", "Rejection audit trail", "Cross-bank alert correlation"],
        ),
        SupportedStandardInfo(
            standard_code="pacs.003.001.08",
            business_name="Customer Direct Debit (SDD)",
            schema_version="pacs.003.001.08",
            typical_channel="SEPA Direct Debit / BACS Direct Debit",
            fraud_risk_vectors=["Unauthorized Debit Pull", "Mule Creditor Scheme Harvest", "Same-Day Mandate Exploitation"],
            supported_operations=["Mandate verification", "Sequence type risk scoring", "Provisional hold trigger"],
        ),
        SupportedStandardInfo(
            standard_code="pacs.008.001.08",
            business_name="Financial Institutional Customer Credit Transfer",
            schema_version="pacs.008.001.08",
            typical_channel="SEPA Credit Transfer (SCT) / SWIFT GPI",
            fraud_risk_vectors=["APP Fraud", "Impersonation Scams", "Money Laundering Hop Chains"],
            supported_operations=["Sub-14ms Real-Time Inference", "FedGNN Ring Analytics", "FININT Bridge Case Creation"],
        ),
        SupportedStandardInfo(
            standard_code="pain.001.001.08",
            business_name="Customer Credit Transfer Initiation",
            schema_version="pain.001.001.08",
            typical_channel="Corporate Banking / Treasury Ingress",
            fraud_risk_vectors=["Payroll Tampering", "CEO Fraud / BEC", "Unauthorized Batch Injection"],
            supported_operations=["Batch initiation parsing", "Corporate sender validation", "Pre-clearing scoring"],
        ),
        SupportedStandardInfo(
            standard_code="SWIFT_MT103",
            business_name="Single Customer Credit Transfer (Legacy SWIFT)",
            schema_version="SWIFT FIN MT103 (Category 1)",
            typical_channel="SWIFT FIN / Correspondent Banking",
            fraud_risk_vectors=["Sanctions Evasion", "Correspondent Routing Hijack", "Wire Fraud"],
            supported_operations=["Tag :20:/:32A:/:50:/:59: normalization", "BIC extraction", "Zero-PII anonymization"],
        ),
    ]

    return SupportedStandardsResponse(
        total_standards=len(standards_catalog),
        standards=standards_catalog,
    )


@_base_router.get("/health")
async def get_financial_rails_health() -> dict[str, Any]:
    """Health check probe for financial message processing engine."""
    return {
        "status": "UP",
        "service": "Extended ISO 20022 Financial Rails Engine",
        "supported_standards_count": 6,
        "xxe_protection": "STRICT_DEFUSED_XML",
        "zero_pii_transform": "HMAC_SHA256_SALTED",
    }


# ── Dual-Router Mounting for Complete Backward & Path Compatibility ──────────

router = APIRouter(prefix="/financial-messages", tags=["Extended ISO 20022 Financial Rails"])
api_router = APIRouter(prefix="/api/v1/financial-messages", tags=["Extended ISO 20022 Financial Rails"])

router.include_router(_base_router)
api_router.include_router(_base_router)
