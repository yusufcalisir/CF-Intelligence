"""Unit tests for CFI Connector SDK Adapters."""

from datetime import datetime, timezone
import pytest
from cfi_connector_sdk.adapters.entity_adapter import BaseEntityAdapter
from cfi_connector_sdk.adapters.feature_adapter import BaseFeatureAdapter
from cfi_connector_sdk.adapters.transaction_adapter import (
    BaseTransactionAdapter,
    NormalizedTransaction,
)


class DummyTransactionAdapter(BaseTransactionAdapter):
    """Concrete dummy implementation of BaseTransactionAdapter for testing."""

    def parse_native_payload(self, payload: dict) -> NormalizedTransaction:
        return NormalizedTransaction(
            transaction_id=payload["tx_id"],
            account_id=payload["acc_from"],
            counterparty_account_id=payload["acc_to"],
            amount=float(payload["amt"]),
            currency=payload.get("curr", "USD"),
        )


def test_normalized_transaction_validation():
    tx = NormalizedTransaction(
        transaction_id="tx_1001",
        account_id="acc_A",
        counterparty_account_id="acc_B",
        amount=150.50,
        currency="USD",
    )
    assert tx.transaction_id == "tx_1001"
    assert tx.amount == 150.50
    assert tx.channel_type == "ONLINE"

    adapter = DummyTransactionAdapter()
    assert adapter.validate_schema(tx) is True


def test_normalized_transaction_invalid_schema():
    adapter = DummyTransactionAdapter()
    tx_invalid_amount = NormalizedTransaction(
        transaction_id="tx_1002",
        account_id="acc_A",
        counterparty_account_id="acc_B",
        amount=1.0,
        currency="INVALID_CURRENCY_LENGTH",
    )
    assert adapter.validate_schema(tx_invalid_amount) is False


def test_feature_adapter_velocity_extraction():
    feature_adapter = BaseFeatureAdapter()
    now_dt = datetime.now(timezone.utc)

    history = [
        NormalizedTransaction(
            transaction_id="tx_h1",
            account_id="acc_1",
            counterparty_account_id="acc_2",
            amount=100.0,
            timestamp=now_dt,
        ),
        NormalizedTransaction(
            transaction_id="tx_h2",
            account_id="acc_1",
            counterparty_account_id="acc_3",
            amount=200.0,
            timestamp=now_dt,
        ),
    ]

    current_tx = NormalizedTransaction(
        transaction_id="tx_curr",
        account_id="acc_1",
        counterparty_account_id="acc_4",
        amount=150.0,
        timestamp=now_dt,
    )

    features = feature_adapter.extract_velocity_features(current_tx, history)
    assert features["amount"] == 150.0
    assert features["tx_count_1h"] == 2.0
    assert features["tx_count_24h"] == 2.0
    assert features["amount_sum_24h"] == 300.0
    assert features["amount_ratio_24h"] == 1.0  # 150 / 150


def test_entity_adapter_hmac_hashing():
    entity_adapter = BaseEntityAdapter(bank_salt="secret_salt_xyz")
    hashed_id_1 = entity_adapter.hash_customer_id("CUST_88492")
    hashed_id_2 = entity_adapter.hash_customer_id("CUST_88492")

    assert len(hashed_id_1) == 64  # SHA-256 hex string
    assert hashed_id_1 == hashed_id_2  # Deterministic
    assert entity_adapter.hash_customer_id("") == ""

    payload = {
        "customer_id": "CUST_88492",
        "account_number": "ACC_10928392",
        "name": "Jane Doe",
    }
    masked = entity_adapter.mask_entity_payload(payload)
    assert masked["customer_id"] == hashed_id_1
    assert masked["account_number"] != "ACC_10928392"
    assert masked["name"] == "Jane Doe"
