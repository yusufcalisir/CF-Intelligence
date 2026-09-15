"""Unit tests for Production Error Sanitization & Information Leakage Defense."""

from __future__ import annotations

import contextlib
import logging
import re
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.application.services.data_validator import (
    DataContractValidationError,
    DataValidatorService,
)
from app.infrastructure.security.error_handler import (
    format_safe_error_response,
    mask_pii_in_text,
)


@pytest.fixture
def error_app() -> FastAPI:
    """Create a test FastAPI instance with the safe error handler and failing routes."""
    test_app = FastAPI()

    @test_app.exception_handler(Exception)
    async def handle_exc(request: Request, exc: Exception):
        return format_safe_error_response(request, exc, status_code=500)

    @test_app.get("/trigger-db-error")
    async def trigger_db_error():
        # Simulate an internal DB exception with sensitive path/query info
        raise RuntimeError(
            "psycopg2.OperationalError: relation 'users_tbl_secret' does not exist "
            "at /var/www/internal_backend/app/db/session.py line 124 in execute_query"
        )

    @test_app.get("/trigger-file-error")
    async def trigger_file_error():
        # Simulate a filesystem path disclosure exception
        raise FileNotFoundError(
            "No such file or directory: 'C:\\Users\\ServerAdmin\\AppData\\Local\\secret_keys.pem'"
        )

    return test_app


def test_production_error_sanitization_strips_stack_trace_and_paths(error_app: FastAPI):
    """Verify that in production mode, sensitive internal paths and stack traces are NOT leaked to the client."""
    client = TestClient(error_app, raise_server_exceptions=False)

    # Force production mode
    with patch("app.infrastructure.security.error_handler.is_production_mode", return_value=True):
        res = client.get("/trigger-db-error")

        assert res.status_code == 500
        data = res.json()

        # 1. Must contain generic safe message
        assert "Something went wrong" in data["detail"]
        assert data["title"] == "Internal Server Error"

        # 2. Must contain incident ID for support correlation
        assert "incident_id" in data
        assert re.match(r"^inc_[a-f0-9]{12}$", data["incident_id"])
        assert res.headers.get("X-Incident-ID") == data["incident_id"]

        # 3. MUST NOT leak internal file paths, table names, or runtime internals
        body_str = res.text
        assert "psycopg2" not in body_str
        assert "users_tbl_secret" not in body_str
        assert "/var/www" not in body_str
        assert "session.py" not in body_str
        assert "execute_query" not in body_str


def test_production_error_sanitization_strips_windows_paths(error_app: FastAPI):
    """Verify Windows internal filesystem paths are completely stripped in production."""
    client = TestClient(error_app, raise_server_exceptions=False)

    with patch("app.infrastructure.security.error_handler.is_production_mode", return_value=True):
        res = client.get("/trigger-file-error")

        assert res.status_code == 500
        data = res.json()

        assert "Something went wrong" in data["detail"]
        assert "incident_id" in data

        body_str = res.text
        assert "ServerAdmin" not in body_str
        assert "secret_keys.pem" not in body_str
        assert "C:\\Users" not in body_str


def test_development_mode_allows_exception_details(error_app: FastAPI):
    """Verify that in development mode, developers get detailed exception strings for debugging."""
    client = TestClient(error_app, raise_server_exceptions=False)

    with patch("app.infrastructure.security.error_handler.is_production_mode", return_value=False):
        res = client.get("/trigger-db-error")

        assert res.status_code == 500
        data = res.json()
        assert "psycopg2.OperationalError" in data["detail"]
        assert "incident_id" in data
        assert data["exception_type"] == "RuntimeError"


def test_mask_pii_in_text_masks_financial_and_identity_attributes():
    """Verify type-salted HMAC-SHA256 masking for IBAN, SSN, PAN, Email, and Phone."""
    raw_text = (
        "Customer TR330006100511123456789012 with SSN 123-45-6789 "
        "and card 4111 2222 3333 4444 (email: fraud_ops@consortium.bank, phone: +15551234567) failed auth"
    )
    masked = mask_pii_in_text(raw_text)

    # 1. Raw PII must NOT appear in output
    assert "TR330006100511123456789012" not in masked
    assert "123-45-6789" not in masked
    assert "4111 2222 3333 4444" not in masked
    assert "fraud_ops@consortium.bank" not in masked
    assert "+15551234567" not in masked

    # 2. Authentic HMAC-SHA256 tokens must be injected
    assert "[MASKED_PII:IBAN:" in masked
    assert "[MASKED_PII:SSN_TCKN:" in masked
    assert "[MASKED_PII:CREDIT_CARD:" in masked
    assert "[MASKED_PII:EMAIL:" in masked
    assert "[MASKED_PII:PHONE:" in masked

    # 3. Deterministic: same text yields same masked tokens
    assert mask_pii_in_text(raw_text) == masked


def test_error_handler_masks_pii_in_server_logs_and_dev_responses(caplog: pytest.LogCaptureFixture):
    """Verify raw PII in exceptions is masked in server logs and dev-mode responses."""
    test_app = FastAPI()

    @test_app.exception_handler(Exception)
    async def handle_exc(request: Request, exc: Exception):
        return format_safe_error_response(request, exc, status_code=500)

    @test_app.get("/trigger-pii-error")
    async def trigger_pii():
        raise ValueError(
            "Account TR330006100511123456789012 and SSN 123-45-6789 failed KYC verification"
        )

    client = TestClient(test_app, raise_server_exceptions=False)

    with (
        caplog.at_level(logging.ERROR),
        patch("app.infrastructure.security.error_handler.is_production_mode", return_value=False),
    ):
        res = client.get("/trigger-pii-error")
        assert res.status_code == 500
        data = res.json()
        assert "TR330006100511123456789012" not in data["detail"]
        assert "123-45-6789" not in data["detail"]
        assert "[MASKED_PII:IBAN:" in data["detail"]
        assert "[MASKED_PII:SSN_TCKN:" in data["detail"]

    # Check server-side error log records: raw PII never logged
    log_text = caplog.text
    assert "TR330006100511123456789012" not in log_text
    assert "123-45-6789" not in log_text
    assert "[MASKED_PII:IBAN:" in log_text


def test_data_validator_streaming_rejects_raw_pii_columns():
    """Verify DataValidatorService rejects streaming batches containing cleartext PII columns."""
    service = DataValidatorService()
    df_with_pii = pd.DataFrame(
        {
            "transaction_amount": [100.0],
            "velocity": [1.0],
            "hour_of_day": [10],
            "merchant_risk_score": [0.1],
            "customer_history_score": [0.9],
            "chargeback_count": [0],
            "account_age_days": [100],
            "country_code": ["US"],
            "merchant_category": ["retail"],
            "device_type": ["mobile_app"],
            "customer_iban": ["TR330006100511123456789012"],
        }
    )

    with pytest.raises(DataContractValidationError, match="Zero Raw PII violation"):
        service.validate_streaming_batch(df_with_pii, "bank_test_pii")

    # Verify quarantine store has redacted PII
    assert "bank_test_pii" in service._quarantine_store
    quarantined = service._quarantine_store["bank_test_pii"][0]
    assert quarantined["customer_iban"].iloc[0] == "[REDACTED_QUARANTINE_PII]"


def test_data_validator_quarantine_store_is_bounded():
    """Verify quarantine store enforces bounded FIFO eviction at MAX_QUARANTINE_PER_BANK."""
    service = DataValidatorService()
    invalid_df = pd.DataFrame({"transaction_amount": [-10.0], "country_code": ["US"]})

    # Trigger 105 quarantine events
    for _ in range(105):
        with contextlib.suppress(DataContractValidationError):
            service.validate_streaming_batch(invalid_df, "bank_bound_test")

    assert len(service._quarantine_store["bank_bound_test"]) == service.MAX_QUARANTINE_PER_BANK
    assert len(service._quarantine_store["bank_bound_test"]) == 100


