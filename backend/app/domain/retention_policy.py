# ruff: noqa: UP042
"""Domain models for Automated Retention & Erasure Policy Engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

logger = logging.getLogger(__name__)


class DataCategory(str, Enum):
    """Data category classification for TTL and retention governance."""

    TRANSACTION_LOGS = "TRANSACTION_LOGS"
    INFERENCE_AUDITS = "INFERENCE_AUDITS"
    GRAPH_EDGES = "GRAPH_EDGES"
    EXPLAINABILITY_REPORTS = "EXPLAINABILITY_REPORTS"
    CUSTOMER_ENTITIES = "CUSTOMER_ENTITIES"


class ErasureMethod(str, Enum):
    """Method enum for data deletion/sanitization."""

    HARD_DELETE = "HARD_DELETE"
    CRYPTOGRAPHIC_ZEROIZATION = "CRYPTOGRAPHIC_ZEROIZATION"
    ANONYMIZATION = "ANONYMIZATION"


class RetentionErasureError(Exception):
    """Domain exception raised when retention configuration or erasure fails."""


@dataclass
class RetentionPolicy:
    """Dataclass configuring data retention TTL rules per tenant and category."""

    category: DataCategory
    ttl_days: int
    erasure_method: ErasureMethod = ErasureMethod.CRYPTOGRAPHIC_ZEROIZATION

    def __post_init__(self) -> None:
        if self.ttl_days <= 0:
            raise ValueError("Retention policy ttl_days must be positive.")


@dataclass
class ErasureAuditRecord:
    """Dataclass tracking an executed cryptographic erasure event with tamper-evident chaining."""

    erasure_id: str
    tenant_id: str
    category: DataCategory
    records_erased_count: int
    erasure_hash: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: str = "VERIFIED_ERASED"
    prev_erasure_hash: str | None = None
    affected_tables: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serializes the record into a JSON-compatible dictionary."""
        return {
            "erasure_id": self.erasure_id,
            "tenant_id": self.tenant_id,
            "category": self.category.value,
            "records_erased_count": self.records_erased_count,
            "erasure_hash": self.erasure_hash,
            "timestamp": self.timestamp.isoformat(),
            "status": self.status,
            "prev_erasure_hash": self.prev_erasure_hash,
            "affected_tables": list(self.affected_tables),
        }

    @classmethod
    def from_dict(cls, data: dict) -> ErasureAuditRecord:
        """Deserializes a dictionary into an ErasureAuditRecord instance."""
        ts_val = data.get("timestamp")
        ts = datetime.fromisoformat(ts_val) if isinstance(ts_val, str) else datetime.now(UTC)
        cat_str = data.get("category", "TRANSACTION_LOGS")
        cat = DataCategory(cat_str) if cat_str in DataCategory._value2member_map_ else DataCategory.TRANSACTION_LOGS
        return cls(
            erasure_id=data.get("erasure_id", ""),
            tenant_id=data.get("tenant_id", ""),
            category=cat,
            records_erased_count=int(data.get("records_erased_count", 0)),
            erasure_hash=data.get("erasure_hash", ""),
            timestamp=ts,
            status=data.get("status", "VERIFIED_ERASED"),
            prev_erasure_hash=data.get("prev_erasure_hash"),
            affected_tables=list(data.get("affected_tables", [])),
        )
