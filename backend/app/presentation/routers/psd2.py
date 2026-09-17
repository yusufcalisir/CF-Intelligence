"""Open Banking PSD2 (XS2A) and ISO 20022 API Router.

Exposes standardized endpoints for third-party AISPs/PISPs to manage customer
consent, retrieve account lists and transaction histories, initiate SEPA/ISO 20022
payments, and parse financial message standards with cryptographic zero-PII privacy transforms.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.application.schemas.psd2 import (
    AccountResponse,
    ConsentRequest,
    ConsentResponse,
    ISO20022ParseRequest,
    ISO20022ParseResponse,
    PaymentInitiationRequest,
    PaymentInitiationResponse,
    PaymentStatusResponse,
    TransactionResponse,
)
from app.application.services.financial_message_parser import (
    FinancialMessageParser,
    FinancialMessageParserError,
)
from app.config import get_settings

# Thread-safe in-memory state stores for consents and payments
_store_lock = threading.Lock()
_consents: dict[str, dict[str, Any]] = {}
_payments: dict[str, dict[str, Any]] = {}

_base_router = APIRouter()


def get_jwt_subject(authorization: str | None = Header(None)) -> dict[str, Any]:
    """Dependency verifying Bearer JWT token from either PSD2 TPP key or platform signing key."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is missing.",
        )
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme. Bearer required.",
        )
    token = authorization.split(" ")[1]
    settings = get_settings()

    decoded_payload: dict[str, Any] | None = None
    last_exc: Exception | None = None

    # Evaluate against psd2_jwt_secret first, then fallback to payload_signing_secret
    for secret in (settings.psd2_jwt_secret, getattr(settings, "payload_signing_secret", None)):
        if not secret:
            continue
        try:
            decoded_payload = jwt.decode(token, secret, algorithms=["HS256"])
            break
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired.",
            ) from exc
        except jwt.PyJWTError as exc:
            last_exc = exc
            continue

    if decoded_payload is not None:
        return decoded_payload

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token signature or claims.",
    ) from last_exc


# ── Consent Management ────────────────────────────────────────────────────────


@_base_router.post("/consents", response_model=ConsentResponse, status_code=status.HTTP_201_CREATED)
async def create_consent(
    payload: ConsentRequest,
    token_payload: dict[str, Any] = Depends(get_jwt_subject),
) -> ConsentResponse:
    """Create a new PSD2 XS2A consent for a third-party provider."""
    now = time.time()
    if payload.valid_until <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Consent expiration 'valid_until' must be in the future.",
        )

    if payload.debtor_iban and not FinancialMessageParser.validate_iban(payload.debtor_iban):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid debtor IBAN specified in consent payload.",
        )

    consent_id = f"consent_{int(now)}_{payload.account_id}"
    client_id = token_payload.get("sub", "unknown_client")
    tenant_id = payload.tenant_id or token_payload.get("tenant_id", "bank_a")
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    consent_data = {
        "consent_id": consent_id,
        "status": "valid",
        "account_id": payload.account_id,
        "permissions": payload.permissions,
        "valid_until": payload.valid_until,
        "debtor_iban": payload.debtor_iban,
        "created_at": now_iso,
        "client_id": client_id,
        "tenant_id": tenant_id,
    }

    with _store_lock:
        _consents[consent_id] = consent_data

    return ConsentResponse(**consent_data)


@_base_router.get("/consents/{consent_id}", response_model=ConsentResponse)
async def get_consent_details(
    consent_id: str,
    token_payload: dict[str, Any] = Depends(get_jwt_subject),
) -> ConsentResponse:
    """Retrieve details and verification status of a PSD2 consent."""
    with _store_lock:
        consent = _consents.get(consent_id)

    if not consent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consent '{consent_id}' was not found.",
        )

    if consent["status"] == "valid" and consent["valid_until"] < time.time():
        with _store_lock:
            consent["status"] = "expired"

    return ConsentResponse(**consent)


@_base_router.delete("/consents/{consent_id}", response_model=ConsentResponse)
async def revoke_consent(
    consent_id: str,
    token_payload: dict[str, Any] = Depends(get_jwt_subject),
) -> ConsentResponse:
    """Revoke an active PSD2 customer consent."""
    with _store_lock:
        consent = _consents.get(consent_id)
        if not consent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Consent '{consent_id}' was not found.",
            )
        consent["status"] = "revoked"

    return ConsentResponse(**consent)


# ── Account Information Services (AISP) ──────────────────────────────────────


@_base_router.get("/accounts", response_model=list[AccountResponse])
async def list_consented_accounts(
    consent_id: str = Header(..., alias="consent-id"),
    token_payload: dict[str, Any] = Depends(get_jwt_subject),
) -> list[AccountResponse]:
    """Retrieve consented customer accounts using a valid consent header."""
    with _store_lock:
        consent = _consents.get(consent_id)

    if not consent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent not found.",
        )
    if consent["status"] != "valid" or consent["valid_until"] < time.time():
        with _store_lock:
            consent["status"] = "expired"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent has expired or is invalid.",
        )
    if "read_accounts" not in consent["permissions"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient consent permissions for reading accounts.",
        )

    acc_id = consent["account_id"]
    if acc_id == "acc_1":
        iban = "DE89370400440532013000"
        balance = 42000.50
        bank_name = "Nexus Digital"
    else:
        acc_hash = hashlib.sha256(acc_id.encode("utf-8")).hexdigest()
        bban = f"30006{acc_hash[:16]}"
        iban = FinancialMessageParser.generate_valid_iban("FR", bban)
        balance = round(15000.0 + (int(acc_hash[:4], 16) % 500000) / 10.0, 2)
        bank_name = "Meridian National"

    return [
        AccountResponse(
            account_id=acc_id,
            iban=iban,
            currency="EUR",
            balance=balance,
            bank_name=bank_name,
            status="enabled",
        )
    ]


@_base_router.get("/accounts/{account_id}/transactions", response_model=list[TransactionResponse])
async def list_account_transactions(
    account_id: str,
    consent_id: str = Header(..., alias="consent-id"),
    token_payload: dict[str, Any] = Depends(get_jwt_subject),
) -> list[TransactionResponse]:
    """Retrieve transaction history for a consented account under PSD2 specifications."""
    with _store_lock:
        consent = _consents.get(consent_id)

    if not consent or consent["account_id"] != account_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent not matching target account id.",
        )
    if consent["status"] != "valid" or consent["valid_until"] < time.time():
        with _store_lock:
            consent["status"] = "expired"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent has expired or is invalid.",
        )
    if "read_transactions" not in consent["permissions"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient consent permissions for reading transactions.",
        )

    if account_id == "acc_1":
        return [
            TransactionResponse(
                transaction_id="tx_psd2_1001",
                amount=250.00,
                currency="EUR",
                booking_date="2026-07-16T12:00:00Z",
                debtor_name="John Doe",
                creditor_name="Crypto Exchange Ltd",
                remittance_info="SEPA INSTANT TRANSFER DEB-1",
                status="booked",
            ),
            TransactionResponse(
                transaction_id="tx_psd2_1002",
                amount=1500.00,
                currency="EUR",
                booking_date="2026-07-16T15:30:00Z",
                debtor_name="John Doe",
                creditor_name="Luxury Watch Retailer",
                remittance_info="GIFT",
                status="booked",
            ),
        ]

    acc_hash = hashlib.sha256(account_id.encode("utf-8")).hexdigest()
    amount_base = (int(acc_hash[:4], 16) % 1000) + 50.0
    return [
        TransactionResponse(
            transaction_id=f"tx_{account_id[:8]}_1001",
            amount=round(amount_base, 2),
            currency="EUR",
            booking_date="2026-07-16T12:00:00Z",
            debtor_name=f"Consented Account Holder ({account_id[:8]})",
            creditor_name="Verified Counterparty AG",
            remittance_info="SEPA INSTANT SETTLEMENT",
            status="booked",
        ),
        TransactionResponse(
            transaction_id=f"tx_{account_id[:8]}_1002",
            amount=round(amount_base * 2.4, 2),
            currency="EUR",
            booking_date="2026-07-16T16:45:00Z",
            debtor_name=f"Consented Account Holder ({account_id[:8]})",
            creditor_name="Global Enterprise Services",
            remittance_info="INVOICE PAYMENT",
            status="booked",
        ),
    ]


# ── Payment Initiation Services (PISP) ───────────────────────────────────────


@_base_router.post("/payments", response_model=PaymentInitiationResponse, status_code=status.HTTP_201_CREATED)
async def initiate_payment(
    payload: PaymentInitiationRequest,
    token_payload: dict[str, Any] = Depends(get_jwt_subject),
) -> PaymentInitiationResponse:
    """Initiate a payment transfer under Berlin Group NextGenPSD2 / ISO 20022 PISP specifications."""
    # Mod-97 IBAN checksum validation
    if not FinancialMessageParser.validate_iban(payload.debtor_account):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid debtor IBAN according to ISO 13616 Mod-97 checksum.",
        )
    if not FinancialMessageParser.validate_iban(payload.creditor_account):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid creditor IBAN according to ISO 13616 Mod-97 checksum.",
        )
    if payload.instructed_amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Instructed amount must be strictly greater than zero.",
        )

    # Optional consent validation
    tenant_id = payload.tenant_id or token_payload.get("tenant_id", "bank_a")
    if payload.consent_id:
        with _store_lock:
            consent = _consents.get(payload.consent_id)
        if not consent:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Consent '{payload.consent_id}' not found.",
            )
        if consent.get("status") != "valid" or consent.get("valid_until", 0) < time.time():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Consent has expired or is no longer valid for payment initiation.",
            )
        perms = consent.get("permissions", [])
        if "initiate_payments" not in perms and "write_payments" not in perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Consent lacks required 'initiate_payments' permission.",
            )

    # Dynamic risk assessment based on payment parameters
    risk_score = 0.05
    if payload.instructed_amount > 50000.0:
        risk_score += 0.45
    if payload.instructed_amount > 10000.0 and payload.payment_product == "instant-sepa-credit-transfers":
        risk_score += 0.30
    if payload.payment_product == "cross-border-credit-transfers":
        risk_score += 0.20
    risk_score = min(0.99, round(risk_score, 2))
    is_flagged = risk_score >= 0.70

    tx_status = "RCVD" if is_flagged else "ACTC"
    payment_id = f"pmt_{int(time.time())}_{hashlib.sha256(payload.debtor_account.encode()).hexdigest()[:8]}"
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    payment_record = {
        "payment_id": payment_id,
        "transaction_status": tx_status,
        "debtor_account": payload.debtor_account,
        "creditor_account": payload.creditor_account,
        "instructed_amount": payload.instructed_amount,
        "currency": payload.currency.upper(),
        "creditor_name": payload.creditor_name,
        "payment_product": payload.payment_product,
        "risk_score": risk_score,
        "is_flagged_for_review": is_flagged,
        "created_at": now_iso,
        "last_updated": now_iso,
        "estimated_settlement": (
            "Instant" if payload.payment_product == "instant-sepa-credit-transfers" else "T+1 Business Day"
        ),
        "tenant_id": tenant_id,
        "clearing_system_ref": f"STEP2-{payment_id[:12].upper()}",
    }

    with _store_lock:
        _payments[payment_id] = payment_record

    return PaymentInitiationResponse(**payment_record)


@_base_router.get("/payments/{payment_id}", response_model=PaymentStatusResponse)
async def get_payment_status(
    payment_id: str,
    token_payload: dict[str, Any] = Depends(get_jwt_subject),
) -> PaymentStatusResponse:
    """Retrieve the current processing and clearing status of a payment transfer."""
    with _store_lock:
        payment = _payments.get(payment_id)

    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment transfer '{payment_id}' was not found.",
        )

    return PaymentStatusResponse(**payment)


# ── ISO 20022 Financial Message Ingestion ────────────────────────────────────


@_base_router.post("/iso20022/parse", response_model=ISO20022ParseResponse)
async def parse_financial_message(
    payload: ISO20022ParseRequest,
    token_payload: dict[str, Any] = Depends(get_jwt_subject),
) -> ISO20022ParseResponse:
    """Parse, validate, and normalize standard ISO 20022, SEPA, or SWIFT MT103 financial messages."""
    try:
        parsed = FinancialMessageParser.parse_message(payload.raw_content, payload.message_type)
    except FinancialMessageParserError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Financial message parsing error: {exc}",
        ) from exc

    sender_acct = str(parsed.get("sender_account") or "")
    receiver_acct = str(parsed.get("receiver_account") or "")

    is_valid_debtor = FinancialMessageParser.validate_iban(sender_acct)
    is_valid_creditor = FinancialMessageParser.validate_iban(receiver_acct)

    privacy_feats = None
    if payload.anonymize_pii:
        privacy_feats = FinancialMessageParser.to_privacy_preserving_features(parsed, salt=payload.salt)

    return ISO20022ParseResponse(
        message_type=parsed.get("message_type", "UNKNOWN"),
        transaction_id=parsed.get("transaction_id", "unknown_tx_id"),
        amount=float(parsed.get("amount", 0.0)),
        currency=str(parsed.get("currency", "EUR")),
        date=str(parsed.get("date", "")),
        sender_name=parsed.get("sender_name") or None,
        sender_account=sender_acct,
        sender_bic=parsed.get("sender_bic") or None,
        sender_country=str(parsed.get("sender_country", "XX")),
        receiver_name=parsed.get("receiver_name") or None,
        receiver_account=receiver_acct,
        receiver_bic=parsed.get("receiver_bic") or None,
        receiver_country=str(parsed.get("receiver_country", "XX")),
        remittance_info=parsed.get("remittance_info") or None,
        is_valid_debtor_iban=is_valid_debtor,
        is_valid_creditor_iban=is_valid_creditor,
        privacy_features=privacy_feats,
    )


# ── Dual-Router Mounting for Complete Backward & Path Compatibility ──────────

router = APIRouter(prefix="/api/v1/psd2", tags=["PSD2 Open Banking"])
api_router = APIRouter(prefix="/v1/psd2", tags=["PSD2 Open Banking"])

router.include_router(_base_router)
api_router.include_router(_base_router)
