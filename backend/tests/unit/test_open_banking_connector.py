"""Unit tests for OpenBankingConnector & Berlin Group NextGenPSD2 REST Gateway (Section 8.3)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.infrastructure.connectors.open_banking_connector import OpenBankingConnector


def test_psd2_payload_mapping_to_normalized_transaction() -> None:
    """Verifies that NextGenPSD2 JSON booked/pending transaction payloads are mapped to NormalizedTransaction objects."""
    psd2_sample = {
        "accounts": {"iban": "DE89370400440532013000"},
        "transactions": {
            "booked": [
                {
                    "transactionId": "tx_booked_001",
                    "debtorAccount": {"iban": "DE89370400440532013000"},
                    "creditorAccount": {"iban": "DE89370400440532013999"},
                    "transactionAmount": {"amount": "145.50", "currency": "EUR"},
                    "bookingDate": "2026-07-23T10:00:00+00:00",
                    "merchantCategoryCode": "5411",
                }
            ],
            "pending": [
                {
                    "transactionId": "tx_pending_002",
                    "debtorAccount": {"iban": "DE89370400440532013000"},
                    "creditorAccount": {"iban": "DE89370400440532013888"},
                    "transactionAmount": {"amount": "49.99", "currency": "EUR"},
                    "bookingDate": "2026-07-23T10:15:00+00:00",
                    "merchantCategoryCode": "5999",
                }
            ],
        },
    }

    connector = OpenBankingConnector()
    txs = connector.parse_psd2_payload(psd2_sample)

    assert len(txs) == 2
    assert txs[0].transaction_id == "tx_booked_001"
    assert txs[0].account_id == "DE89370400440532013000"
    assert txs[0].counterparty_account_id == "DE89370400440532013999"
    assert txs[0].amount == 145.50
    assert txs[0].currency == "EUR"
    assert txs[0].channel_type == "OPEN_BANKING_PSD2"

    assert txs[1].transaction_id == "tx_pending_002"
    assert txs[1].amount == 49.99


def test_oauth2_token_fetch_and_mtls_header_injection() -> None:
    """Verifies OAuth2 bearer token generation and PSD2 header injection."""
    connector = OpenBankingConnector(
        auth_type="oauth2",
        client_id="tpp_alpha_id",
        tpp_signature_key="rsa_demo_key",
        access_token="psd2_test_token_123",
    )

    headers = connector._get_headers(body_bytes=b'{"test": 123}')

    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")
    assert "X-Request-ID" in headers
    assert "Digest" in headers
    assert headers["Digest"].startswith("SHA-256=")
    assert "TPP-Signature" in headers
    assert 'keyId="tpp_alpha_id"' in headers["TPP-Signature"]


def test_parse_batch_handles_empty_booked_list() -> None:
    """Verifies robustness when booked or pending transaction lists are empty."""
    empty_payload = {
        "accounts": {"iban": "DE89370400440532013000"},
        "transactions": {
            "booked": [],
            "pending": [],
        },
    }

    connector = OpenBankingConnector()
    txs = connector.parse_batch(empty_payload)

    assert len(txs) == 0
    streamed = list(connector.consume_stream())
    assert len(streamed) == 0


def test_fetch_account_transactions_fails_closed_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that unreachable remote PSD2 endpoint raises RuntimeError rather than fabricating transactions."""
    connector = OpenBankingConnector(
        base_url="https://invalid-psd2-sandbox.local/v1",
        access_token="valid_test_token_123",
    )
    def _mock_get(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("Network host unreachable")
    monkeypatch.setattr(httpx, "get", _mock_get)

    with pytest.raises(RuntimeError, match="PSD2 API request failed"):
        connector.fetch_account_transactions(account_id="DE89370400440532013111")
