"""Standardized Bank Connector SDK for Collaborative Fraud Intelligence (CFI) Network."""

from __future__ import annotations

from cfi_connector_sdk.adapters.entity_adapter import BaseEntityAdapter
from cfi_connector_sdk.adapters.feature_adapter import BaseFeatureAdapter
from cfi_connector_sdk.adapters.transaction_adapter import (
    BaseTransactionAdapter,
    NormalizedTransaction,
)
from cfi_connector_sdk.client.local_fl_client import LocalFLClient
from cfi_connector_sdk.health import ConnectorHealthMonitor, ConnectorHealthStatus

__version__ = "1.0.0"

__all__ = [
    "BaseEntityAdapter",
    "BaseFeatureAdapter",
    "BaseTransactionAdapter",
    "ConnectorHealthMonitor",
    "ConnectorHealthStatus",
    "LocalFLClient",
    "NormalizedTransaction",
    "__version__",
]
