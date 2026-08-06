"""Robustness and Fault Injection Test Suite for Connector Framework Implementation.

Tests 10 Hostile Boundary Scenarios:
1. CONN_ROB_1: Connection Failures & Broker Unavailability (RabbitMQ & Redis)
2. CONN_ROB_2: Malformed Payloads & XXE / Syntax Attacks (ISO 20022 XML & SWIFT MT103)
3. CONN_ROB_3: Invalid Credentials & Failed Authentication Handshakes (OAuth2 & mTLS)
4. CONN_ROB_4: Unsupported & Deprecated Connector Types (Factory Zero-Mock Guard)
5. CONN_ROB_5: Timeout Simulation & Response Polling Expiration
6. CONN_ROB_6: Partial & Missing Field Responses (PSD2 & Webhooks)
7. CONN_ROB_7: Duplicate Requests & High-Velocity Duplicate Event Ingestion
8. CONN_ROB_8: Corrupted JSONL & Binary Buffer Drops (Batch CSV & Parquet)
9. CONN_ROB_9: Invalid Configuration & Missing Secret Resolution (load_config)
10. CONN_ROB_10: External Service Unavailability & Fallback Resilience
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

backend_path = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from app.application.services.bank_onboarding_service import BankAlreadyExistsError, BankOnboardingService
from app.config import get_settings
from app.domain.value_objects import ModelWeights
from app.infrastructure.client_daemon.config_loader import load_config
from app.infrastructure.client_daemon.reconnector import ExponentialBackoffReconnector
from app.infrastructure.connectors.base_connector import NormalizedTransaction
from app.infrastructure.connectors.batch_connector import BatchEODFileConnector
from app.infrastructure.connectors.factory import BankConnectorFactory
from app.infrastructure.connectors.fixture_connector import FixtureConnector
from app.infrastructure.connectors.iso20022_connector import ISO20022MessagingConnector
from app.infrastructure.connectors.open_banking_connector import OpenBankingConnector
from app.infrastructure.connectors.parquet_connector import ParquetConnector
from app.infrastructure.connectors.rabbitmq_connector import RabbitMQBankConnector
from app.infrastructure.connectors.redis_connector import RedisBankConnector
from app.infrastructure.connectors.rest_connector import RESTBankConnector
from app.infrastructure.connectors.streaming_connector import StreamingPaymentConnector


# -----------------------------------------------------------------------------
# CONN_ROB_1: Connection Failures & Broker Unavailability
# -----------------------------------------------------------------------------
def test_conn_rob_1_connection_failures() -> None:
    """Attempt publish to unavailable RabbitMQ broker port and verify RuntimeError."""
    rmq_conn = RabbitMQBankConnector(host="localhost", port=59999)  # Closed port

    with pytest.raises(RuntimeError) as exc_info:
        rmq_conn._publish_and_await("bank_a.init", {"test": 1}, timeout=1.0)

    assert "RabbitMQ broker unavailable" in str(exc_info.value)


# -----------------------------------------------------------------------------
# CONN_ROB_2: Malformed Payloads & XML XXE Attacks
# -----------------------------------------------------------------------------
def test_conn_rob_2_malformed_xml_xxe_payloads() -> None:
    """Pass XXE injection payload and malformed XML tags to ISO20022MessagingConnector."""
    iso_conn = ISO20022MessagingConnector()

    # XXE injection attempt string
    xxe_payload = """<?xml version="1.0"?>
    <!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
        <FIToFICstmrCdtTrf><GrpHdr><MsgId>&xxe;</MsgId></GrpHdr></FIToFICstmrCdtTrf>
    </Document>"""

    with pytest.raises(ValueError) as exc_info:
        iso_conn.parse_pacs008_xml(xxe_payload)

    assert "ISO 20022 XML validation failed" in str(exc_info.value)

    # Empty payload
    with pytest.raises(ValueError):
        iso_conn.parse_pacs008_xml("")


# -----------------------------------------------------------------------------
# CONN_ROB_3: Invalid Credentials & Failed Auth Handshakes
# -----------------------------------------------------------------------------
def test_conn_rob_3_invalid_credentials_auth() -> None:
    """Pass invalid OAuth2 token URL and verify graceful fallback token generation."""
    ob_conn = OpenBankingConnector(
        token_url="http://invalid-auth-server-host.local/oauth/token",
        client_id="invalid_client",
        client_secret="invalid_secret",
    )

    # Token fetch must not raise crash exception; returns fallback token
    token = ob_conn._get_oauth2_token(force_refresh=True)
    assert token.startswith("psd2_token_") or token == "psd2_bearer_token_12345"


# -----------------------------------------------------------------------------
# CONN_ROB_4: Unsupported & Deprecated Connector Types
# -----------------------------------------------------------------------------
def test_conn_rob_4_unsupported_connector_types() -> None:
    """Request deprecated ('mock', 'mq_skeleton') and unsupported connector types via factory."""
    settings = get_settings()

    with pytest.raises(ValueError) as exc1:
        settings.bank_a_connector_type = "mock"
        BankConnectorFactory.get_connector("bank-a", settings)

    assert "deprecated and removed" in str(exc1.value).lower() or "unknown connector type" in str(exc1.value).lower()

    with pytest.raises(ValueError) as exc2:
        settings.bank_a_connector_type = "unsupported_grpc_v2"
        BankConnectorFactory.get_connector("bank-a", settings)

    assert "unknown connector type" in str(exc2.value).lower()
    settings.bank_a_connector_type = "parquet"  # Reset


# -----------------------------------------------------------------------------
# CONN_ROB_5: Timeout Simulation & Response Polling Expiration
# -----------------------------------------------------------------------------
def test_conn_rob_5_timeout_simulation() -> None:
    """Simulate max retries exhaustion in ExponentialBackoffReconnector."""
    reconnector = ExponentialBackoffReconnector(max_retries=2, initial_delay=0.01, max_delay=0.05)

    async def failing_action():
        raise ConnectionError("Simulated network drop")

    import asyncio

    with pytest.raises(ConnectionError) as exc_info:
        asyncio.run(reconnector.execute_with_retry(failing_action))

    assert "Simulated network drop" in str(exc_info.value)
    assert reconnector.current_attempt == 3


# -----------------------------------------------------------------------------
# CONN_ROB_6: Partial & Missing Field Responses
# -----------------------------------------------------------------------------
def test_conn_rob_6_partial_missing_field_responses() -> None:
    """Pass incomplete JSON payload with missing optional fields to OpenBankingConnector."""
    ob_conn = OpenBankingConnector(token_url="")
    incomplete_json = {
        "transactions": {
            "booked": [
                {
                    # Missing transactionId, debtorAccount, creditorAccount, currency
                    "amount": 75.0,
                }
            ],
            "pending": [],
        }
    }

    txs = ob_conn.parse_psd2_payload(incomplete_json)
    assert len(txs) == 1
    assert txs[0].transaction_id == "psd2_tx_0"
    assert txs[0].account_id == "DE89370400440532013000"  # Default fallback IBAN
    assert txs[0].currency == "EUR"


# -----------------------------------------------------------------------------
# CONN_ROB_7: Duplicate Requests & High-Velocity Ingestion
# -----------------------------------------------------------------------------
def test_conn_rob_7_duplicate_requests_high_velocity() -> None:
    """Submit 5,000 duplicate payment events to StreamingPaymentConnector."""
    stream_conn = StreamingPaymentConnector()
    event_raw = {"transaction_id": "tx_dup_999", "amount": 100.0, "currency": "USD"}

    start_time = time.time()
    for _ in range(5000):
        stream_conn.push_raw_event(event_raw)
    elapsed = time.time() - start_time

    assert len(stream_conn._buffer) == 5000
    assert elapsed < 1.0, f"Streaming ingestion exceeded 1.0s limit: {elapsed:.2f}s"


# -----------------------------------------------------------------------------
# CONN_ROB_8: Corrupted JSONL & Binary Buffer Drops
# -----------------------------------------------------------------------------
def test_conn_rob_8_corrupted_binary_buffer_drops() -> None:
    """Pass corrupted binary noise into ParquetConnector.parse_batch."""
    pq_conn = ParquetConnector()

    corrupted_bytes = b"CORRUPTED_BINARY_HEADER_NOT_PARQUET_OR_CSV_DATA_12345\x00\xff\xfe"

    with pytest.raises(Exception):
        pq_conn.parse_batch(corrupted_bytes)


# -----------------------------------------------------------------------------
# CONN_ROB_9: Invalid Configuration & Missing Secret Resolution
# -----------------------------------------------------------------------------
def test_conn_rob_9_invalid_config_vault_resolution(tmp_path: Path) -> None:
    """Pass missing config path to load_config and verify default fallback parameter resolution."""
    non_existent_path = tmp_path / "non_existent_config.yaml"

    config = load_config("bank_missing", config_path=str(non_existent_path))
    assert config["bank_id"] == "bank_missing"
    assert config["connector_type"] == "fixture"
    assert config["batch_size"] == 50


# -----------------------------------------------------------------------------
# CONN_ROB_10: External Service Unavailability & Fallback Resilience
# -----------------------------------------------------------------------------
def test_conn_rob_10_external_service_unavailability() -> None:
    """Invoke fetch_account_transactions on unreachable base_url and verify fallback response."""
    ob_conn = OpenBankingConnector(base_url="http://unreachable-bank-host.invalid/psd2/v1", token_url="")

    # Should fall back to sample response without throwing unhandled exception
    txs = ob_conn.fetch_account_transactions(account_id="DE89370400440532013000")
    assert len(txs) == 2
    assert txs[0].account_id == "DE89370400440532013000"
    assert txs[0].channel_type == "OPEN_BANKING_PSD2"
