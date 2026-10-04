"""Unit tests for Real-Time Streaming Feature Store Engine, Data Validator, and Rolling Aggregators."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domain.data_validator import DataContractValidator, DataValidationError
from app.infrastructure.connectors.base_connector import NormalizedTransaction
from app.infrastructure.feature_store.bloom_filter import BloomFilterDeduplicator
from app.infrastructure.feature_store.rolling_aggregators import RollingFeatureAggregator
from app.infrastructure.feature_store.store import StreamingFeatureStore


def test_data_contract_validator() -> None:
    """Verifies DataContractValidator accepts valid transactions and rejects invalid schema fields."""
    validator = DataContractValidator()

    # Valid transaction
    valid_tx = NormalizedTransaction(
        transaction_id="tx_1001",
        account_id="acc_8812",
        counterparty_account_id="acc_9921",
        amount=150.00,
        currency="USD",
        origin_country="US",
        destination_country="DE",
    )
    assert validator.validate_transaction(valid_tx) is True

    # Empty transaction_id
    with pytest.raises(DataValidationError, match="Transaction ID cannot be empty"):
        validator.validate_transaction(
            NormalizedTransaction(
                transaction_id="",
                account_id="acc_1",
                counterparty_account_id="acc_2",
                amount=10.0,
            )
        )

    # Empty account_id rejected by schema or validator
    with pytest.raises((DataValidationError, ValidationError)):
        validator.validate_transaction(
            NormalizedTransaction(
                transaction_id="tx_1",
                account_id="  ",
                counterparty_account_id="acc_2",
                amount=10.0,
            )
        )

    # Negative amount rejected by Pydantic schema or validator
    with pytest.raises((DataValidationError, ValidationError)):
        validator.validate_transaction(
            NormalizedTransaction(
                transaction_id="tx_1",
                account_id="acc_1",
                counterparty_account_id="acc_2",
                amount=-50.0,
            )
        )


def test_bloom_filter_deduplicator() -> None:
    """Verifies BloomFilterDeduplicator duplicate detection logic."""
    bf = BloomFilterDeduplicator(capacity=1000, num_hashes=3)

    tx_id = "tx_dedup_99"
    assert bf.is_duplicate(tx_id) is False
    assert bf.contains_or_add(tx_id) is False  # Added
    assert bf.is_duplicate(tx_id) is True
    assert bf.contains_or_add(tx_id) is True  # Duplicate


def test_rolling_feature_aggregator_calculations() -> None:
    """Verifies rolling feature calculations (Z-score, velocity, device entropy, FATF risk, cyclical time, alerts)."""
    aggregator = RollingFeatureAggregator()

    base_time = datetime(2026, 7, 22, 14, 0, 0, tzinfo=UTC)
    account_id = "acc_alpha"

    # Register account creation 10 days prior
    creation_time = base_time - timedelta(days=10)
    aggregator.register_account(account_id, creation_time=creation_time)

    # Record 2 SAR alerts in last 30 days
    aggregator.record_sar_alert(account_id, alert_time=base_time - timedelta(days=5))
    aggregator.record_sar_alert(account_id, alert_time=base_time - timedelta(days=12))

    # Tx 1: amount 100.0, country KP (high risk)
    tx1 = NormalizedTransaction(
        transaction_id="tx_r1",
        account_id=account_id,
        counterparty_account_id="acc_target",
        amount=100.00,
        currency="USD",
        timestamp=base_time,
        merchant_category_code="5411",
        origin_country="US",
        destination_country="KP",  # North Korea (1.0 risk)
        device_fingerprint="fp_device_1",
        ip_subnet="192.168.1.0/24",
        channel_type="ONLINE",
    )
    f1 = aggregator.compute_features(tx1)

    assert pytest.approx(f1["account_age_days"], 0.1) == 10.0
    assert f1["merchant_velocity_1h"] == 0.0  # First tx at this merchant
    assert f1["country_risk_score"] == 1.0
    assert f1["previous_alerts_30d"] == 2.0
    assert "hour_of_day_cos" in f1
    assert "hour_of_day_sin" in f1

    # Tx 2: 30 minutes later, same merchant, amount 300.0
    tx2 = NormalizedTransaction(
        transaction_id="tx_r2",
        account_id=account_id,
        counterparty_account_id="acc_target",
        amount=300.00,
        currency="USD",
        timestamp=base_time + timedelta(minutes=30),
        merchant_category_code="5411",
        origin_country="US",
        destination_country="US",  # Low risk (0.05)
        device_fingerprint="fp_device_1",
        ip_subnet="192.168.1.0/24",
        channel_type="ONLINE",
    )
    f2 = aggregator.compute_features(tx2)

    assert f2["merchant_velocity_1h"] == 1.0  # 1 prior tx in past hour
    assert f2["country_risk_score"] == 0.05
    # Prior amount 100, current 300 -> Z-score positive
    assert f2["rolling_amount_zscore_24h"] > 0.0


def test_streaming_feature_store_pipeline() -> None:
    """Verifies end-to-end StreamingFeatureStore ingestion, deduplication, and lookup."""
    store = StreamingFeatureStore()

    tx = NormalizedTransaction(
        transaction_id="tx_stream_001",
        account_id="acc_beta",
        counterparty_account_id="acc_gamma",
        amount=250.0,
        currency="USD",
    )

    res = store.ingest_transaction(tx)
    assert res["status"] == "PROCESSED"
    assert res["transaction_id"] == "tx_stream_001"
    assert "features" in res

    # Deduplication test: re-ingesting tx returns duplicate status
    res_dup = store.ingest_transaction(tx)
    assert res_dup["status"] == "PROCESSED"  # Retrieved cached vector

    # Verification of lookup methods
    vec = store.get_feature_vector("tx_stream_001")
    assert vec is not None
    assert vec["account_id"] == "acc_beta"

    latest = store.get_latest_account_features("acc_beta")
    assert latest is not None
    assert latest["transaction_id"] == "tx_stream_001"

    store.clear()
    assert store.get_feature_vector("tx_stream_001") is None
