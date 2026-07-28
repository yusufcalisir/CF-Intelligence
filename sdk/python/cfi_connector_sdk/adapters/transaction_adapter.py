"""Base Transaction Adapter for core banking schema normalization."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class NormalizedTransaction(BaseModel):
    """Standardized payment transaction schema across all bank connectors."""

    transaction_id: str = Field(..., description="Unique transaction identifier")
    account_id: str = Field(..., description="Debtor / Originating account identifier")
    counterparty_account_id: str = Field(
        ..., description="Creditor / Destination account identifier"
    )
    amount: float = Field(..., gt=0, description="Transaction monetary amount")
    currency: str = Field(default="USD", description="ISO 4217 currency code")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC transaction timestamp",
    )
    merchant_category_code: str = Field(
        default="0000", description="ISO 18245 Merchant Category Code"
    )
    origin_country: str = Field(
        default="US", description="ISO 3166-1 alpha-2 origin country code"
    )
    destination_country: str = Field(
        default="US", description="ISO 3166-1 alpha-2 destination country code"
    )
    device_fingerprint: str = Field(
        default="", description="Cryptographic device or browser fingerprint"
    )
    ip_subnet: str = Field(
        default="", description="Masked IP subnet (e.g. 192.168.1.0/24)"
    )
    channel_type: str = Field(
        default="ONLINE",
        description="Transaction channel (ONLINE, MOBILE, ATM, POS, SWIFT)",
    )


class BaseTransactionAdapter(ABC):
    """Abstract Base Class for core banking system payment feed adapters."""

    @abstractmethod
    def parse_native_payload(self, payload: dict[str, Any]) -> NormalizedTransaction:
        """Transform native core banking payload (JSON, XML dict) to NormalizedTransaction."""
        pass

    def validate_schema(self, tx: NormalizedTransaction) -> bool:
        """Validate transaction schema against basic compliance rules."""
        if tx.amount <= 0:
            return False
        if len(tx.currency) != 3:
            return False
        return True
