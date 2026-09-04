"""Open Banking PSD2 (XS2A) API Router.

Exposes standardized endpoints for third-party AISPs to retrieve account list,
transaction histories, and manage consent verification with JWT auth.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.config import get_settings

router = APIRouter(prefix="/api/v1/psd2", tags=["PSD2 Open Banking"])

# Simple in-memory consent store: {consent_id: consent_data}
_consents: dict[str, dict[str, Any]] = {}


class ConsentRequest(BaseModel):
    account_id: str
    permissions: list[str] = Field(default_factory=lambda: ["read_accounts", "read_transactions"])
    valid_until: float = Field(description="Epoch timestamp representing valid until date")


class ConsentResponse(BaseModel):
    consent_id: str
    status: str
    account_id: str
    permissions: list[str]
    valid_until: float


class AccountResponse(BaseModel):
    account_id: str
    iban: str
    currency: str
    balance: float
    bank_name: str


class TransactionResponse(BaseModel):
    transaction_id: str
    amount: float
    currency: str
    booking_date: str
    debtor_name: str
    creditor_name: str
    remittance_info: str


def get_jwt_subject(authorization: str | None = Header(None)) -> dict[str, Any]:
    """Dependency verifying Bearer JWT token."""
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
    try:
        payload = jwt.decode(token, settings.psd2_jwt_secret, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token signature or claims.",
        ) from exc


@router.post("/consents", response_model=ConsentResponse, status_code=status.HTTP_201_CREATED)
async def create_consent(
    payload: ConsentRequest,
    token_payload: dict[str, Any] = Depends(get_jwt_subject),
) -> ConsentResponse:
    """Create a new PSD2 consent for a third-party provider."""
    consent_id = f"consent_{int(time.time())}_{payload.account_id}"
    consent_data = {
        "consent_id": consent_id,
        "status": "valid",
        "account_id": payload.account_id,
        "permissions": payload.permissions,
        "valid_until": payload.valid_until,
        "client_id": token_payload.get("sub", "unknown_client"),
    }
    _consents[consent_id] = consent_data
    return ConsentResponse(**consent_data)


@router.get("/accounts", response_model=list[AccountResponse])
async def list_consented_accounts(
    consent_id: str = Header(...),
    token_payload: dict[str, Any] = Depends(get_jwt_subject),
) -> list[AccountResponse]:
    """Retrieve consented customer accounts using a valid consent header."""
    consent = _consents.get(consent_id)
    if not consent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent not found.",
        )
    if consent["status"] != "valid" or consent["valid_until"] < time.time():
        consent["status"] = "expired"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent has expired or is invalid.",
        )
    # Validate permissions
    if "read_accounts" not in consent["permissions"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient consent permissions for reading accounts.",
        )

    # Return consented account matching the ID
    acc_id = consent["account_id"]
    if acc_id == "acc_1":
        iban = "DE89370400440532013000"
        balance = 42000.50
        bank_name = "Nexus Digital"
    else:
        acc_hash = hashlib.sha256(acc_id.encode("utf-8")).hexdigest()
        iban = f"FR76{''.join(str(int(c, 16) % 10) for c in acc_hash[:16])}"
        balance = round(15000.0 + (int(acc_hash[:4], 16) % 500000) / 10.0, 2)
        bank_name = "Meridian National"

    return [
        AccountResponse(
            account_id=acc_id,
            iban=iban,
            currency="EUR",
            balance=balance,
            bank_name=bank_name,
        )
    ]


@router.get("/accounts/{account_id}/transactions", response_model=list[TransactionResponse])
async def list_account_transactions(
    account_id: str,
    consent_id: str = Header(...),
    token_payload: dict[str, Any] = Depends(get_jwt_subject),
) -> list[TransactionResponse]:
    """Retrieve transaction history for a consented account under PSD2 specifications."""
    consent = _consents.get(consent_id)
    if not consent or consent["account_id"] != account_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent not matching target account id.",
        )
    if consent["status"] != "valid" or consent["valid_until"] < time.time():
        consent["status"] = "expired"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent has expired or is invalid.",
        )
    # Validate permissions
    if "read_transactions" not in consent["permissions"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient consent permissions for reading transactions.",
        )

    # Return normalized consented transactions
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
            ),
            TransactionResponse(
                transaction_id="tx_psd2_1002",
                amount=1500.00,
                currency="EUR",
                booking_date="2026-07-16T15:30:00Z",
                debtor_name="John Doe",
                creditor_name="Luxury Watch Retailer",
                remittance_info="GIFT",
            ),
        ]

    # Dynamic deterministic transaction history synthesized from account_id
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
        ),
        TransactionResponse(
            transaction_id=f"tx_{account_id[:8]}_1002",
            amount=round(amount_base * 2.4, 2),
            currency="EUR",
            booking_date="2026-07-16T16:45:00Z",
            debtor_name=f"Consented Account Holder ({account_id[:8]})",
            creditor_name="Global Enterprise Services",
            remittance_info="INVOICE PAYMENT",
        ),
    ]
