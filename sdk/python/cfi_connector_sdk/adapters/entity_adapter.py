"""Base Entity Adapter for local customer privacy hashing and HMAC resolution."""

from __future__ import annotations

import hmac
import hashlib
from typing import Any


class BaseEntityAdapter:
    """Provides privacy-preserving HMAC-SHA256 customer identifier resolution."""

    def __init__(self, bank_salt: str = "default_secure_bank_salt") -> None:
        self.bank_salt = bank_salt

    def hash_customer_id(self, raw_id: str) -> str:
        """Computes HMAC-SHA256 entity hash locally within bank security perimeter."""
        if not raw_id:
            return ""
        key = self.bank_salt.encode("utf-8")
        msg = raw_id.encode("utf-8")
        return hmac.new(key, msg, hashlib.sha256).hexdigest()

    def mask_entity_payload(self, entity_data: dict[str, Any]) -> dict[str, Any]:
        """Masks sensitive customer identifying fields in an entity payload."""
        masked = dict(entity_data)
        if "customer_id" in masked:
            masked["customer_id"] = self.hash_customer_id(str(masked["customer_id"]))
        if "account_number" in masked:
            masked["account_number"] = self.hash_customer_id(
                str(masked["account_number"])
            )
        return masked
