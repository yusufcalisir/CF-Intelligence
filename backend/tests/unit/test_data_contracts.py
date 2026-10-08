"""Unit tests for Data validation layer (Pandera, Great Expectations), CockroachDB retries, and Kafka Event Bus."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.application.services.data_validator import (
    DataContractValidationError,
    DataValidatorService,
)
from app.config import get_settings
from app.infrastructure.event_bus import AlertCreated, event_bus

# ── Helper: build a valid transaction dataframe ──────────────────────


def _valid_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "transaction_amount": [150.0, 200.0, 50.0],
            "velocity": [1.5, 2.0, 0.5],
            "hour_of_day": [12, 14, 23],
            "merchant_risk_score": [0.1, 0.4, 0.05],
            "customer_history_score": [0.9, 0.8, 0.95],
            "chargeback_count": [0, 0, 1],
            "account_age_days": [365, 730, 90],
            "country_code": ["US", "UK", "DE"],
            "merchant_category": ["grocery", "electronics", "travel"],
            "device_type": ["mobile_app", "web_browser", "pos_terminal"],
        }
    )


# ── Pandera Streaming Batch Validation Tests ─────────────────────────


class TestPanderaValidation:
    def test_valid_batch_passes(self):
        service = DataValidatorService()
        validated = service.validate_streaming_batch(_valid_df(), "bank_a")
        assert validated is not None
        assert len(validated) == 3

    def test_negative_amount_fails(self):
        service = DataValidatorService()
        df = _valid_df()
        df.loc[1, "transaction_amount"] = -50.0
        with pytest.raises(DataContractValidationError, match="Pandera schema validation failed"):
            service.validate_streaming_batch(df, "bank_a")

    def test_invalid_country_code_length_fails(self):
        service = DataValidatorService()
        df = _valid_df()
        df.loc[0, "country_code"] = "USA"
        with pytest.raises(DataContractValidationError):
            service.validate_streaming_batch(df, "bank_a")

    def test_quarantine_store_populated_on_failure(self):
        service = DataValidatorService()
        df = _valid_df()
        df.loc[0, "transaction_amount"] = -1.0
        with pytest.raises(DataContractValidationError):
            service.validate_streaming_batch(df, "bank_a")
        assert "bank_a" in service._quarantine_store
        assert len(service._quarantine_store["bank_a"]) == 1


# ── Great Expectations Data Contract Gating Tests ────────────────────


class TestGreatExpectationsGating:
    def test_valid_data_passes(self):
        service = DataValidatorService()
        # Mean is ~133 which is within [10, 1000]
        service.gate_data_contract(_valid_df(), "bank_test_pass")

    def test_null_velocity_fails(self):
        service = DataValidatorService()
        df = _valid_df()
        df.loc[1, "velocity"] = None
        with pytest.raises(DataContractValidationError, match="Great Expectations contract"):
            service.gate_data_contract(df, "bank_test_null")

    def test_mean_amount_out_of_bounds_fails(self):
        service = DataValidatorService()
        df = _valid_df()
        df["transaction_amount"] = [5000.0, 5000.0, 5000.0]
        with pytest.raises(DataContractValidationError, match="Great Expectations contract"):
            service.gate_data_contract(
                df, "bank_test_mean", amount_mean_min=10.0, amount_mean_max=1000.0
            )

    def test_invalid_device_type_fails(self):
        service = DataValidatorService()
        df = _valid_df()
        df.loc[0, "device_type"] = "carrier_pigeon"
        with pytest.raises(DataContractValidationError, match="Great Expectations contract"):
            service.gate_data_contract(df, "bank_test_device")


# ── Kafka Event Bus Publishing Tests ─────────────────────────────────


class TestKafkaEventBus:
    def test_kafka_metadata_injected_when_enabled(self):
        settings = get_settings()
        original_use_kafka = settings.use_kafka
        original_producer = event_bus._kafka_producer
        settings.use_kafka = True

        class MockRecordMetadata:
            topic = "domain_events.alert.created"
            partition = 2
            offset = 42

        class MockFuture:
            def get(self, timeout=None):
                return MockRecordMetadata()

        class MockProducer:
            def send(self, topic, key=None, value=None):
                return MockFuture()

        event_bus.set_kafka_producer(MockProducer())
        try:
            event = AlertCreated(
                alert_id="alert_kafka_test",
                bank_id="bank_b",
                severity="HIGH",
                risk_score=920.0,
            )
            event_bus.publish(event)

            matching = event_bus.get_event_log(event_type="alert.created")
            target = next(e for e in matching if getattr(e, "alert_id", "") == "alert_kafka_test")
            assert "kafka_publish" in target.metadata
            k = target.metadata["kafka_publish"]
            assert k["status"] == "BROKER_ACKNOWLEDGED"
            assert k["topic"] == "domain_events.alert.created"
            assert k["partition"] == 2
            assert k["offset"] == 42
            assert k["broker"] == settings.kafka_bootstrap_servers
        finally:
            settings.use_kafka = original_use_kafka
            event_bus.set_kafka_producer(original_producer)

    def test_kafka_metadata_absent_when_disabled(self):
        settings = get_settings()
        original_use_kafka = settings.use_kafka
        settings.use_kafka = False
        try:
            event = AlertCreated(
                alert_id="alert_no_kafka",
                bank_id="bank_c",
                severity="LOW",
                risk_score=100.0,
            )
            event_bus.publish(event)

            matching = event_bus.get_event_log(event_type="alert.created")
            target = next(e for e in matching if getattr(e, "alert_id", "") == "alert_no_kafka")
            assert "kafka_publish" in target.metadata
            k = target.metadata["kafka_publish"]
            assert k["status"] == "IN_MEMORY_EVENT_BUS"
            assert k["provenance"] == "IN_MEMORY"
            assert k["broker"] is None
        finally:
            settings.use_kafka = original_use_kafka


# ── CockroachDB Transaction Retry Tests ──────────────────────────────


class TestCockroachTransactionRetry:
    @pytest.mark.asyncio
    async def test_retries_on_serializable_conflict(self):
        """Verify run_cockroach_transaction retries on SQLSTATE 40001."""
        from sqlalchemy.exc import DBAPIError

        from app.infrastructure.database import run_cockroach_transaction

        call_count = 0

        class MockOrigError:
            pgcode = "40001"

        async def callback(session):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise DBAPIError("stmt", "params", orig=MockOrigError())
            return "success_val"

        # Build a mock session factory that yields proper async context managers
        mock_session = MagicMock()
        mock_begin = MagicMock()

        @asynccontextmanager
        async def begin_ctx():
            yield mock_begin

        @asynccontextmanager
        async def session_ctx():
            yield mock_session

        mock_session.begin = begin_ctx

        def factory():
            return session_ctx()

        result = await run_cockroach_transaction(factory, callback, max_retries=3)
        assert result == "success_val"
        assert call_count == 2
