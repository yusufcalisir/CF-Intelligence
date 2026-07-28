"""Integration tests for Section 38.2: Open Banking Connector Hardening."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import httpx

from app.infrastructure.connectors.open_banking_connector import OpenBankingConnector


def test_token_refreshed_before_expiry() -> None:
    """Verifies that OpenBankingConnector proactively refreshes OAuth2 token when <5 minutes remain."""
    connector = OpenBankingConnector(
        token_url="https://sandbox.berlingroup.org/oauth/token",
        auth_type="oauth2",
    )
    now = datetime.now(UTC).timestamp()

    # Set cached token expiring in 2 minutes (120s < 300s)
    connector._cached_token = "old_expiring_token_123"
    connector._token_expires_at = now + 120.0

    with patch.object(
        connector, "_get_oauth2_token", wraps=connector._get_oauth2_token
    ) as mock_token_get:
        # Requesting headers triggers _refresh_token_if_expiring
        headers = connector._get_headers()

        assert "Authorization" in headers
        # Assert _get_oauth2_token was called with force_refresh=True during refresh
        mock_token_get.assert_any_call(force_refresh=True)


def test_rate_limit_retry_logic() -> None:
    """Verifies that OpenBankingConnector retries on HTTP 429 using Retry-After header backoff."""
    connector = OpenBankingConnector(
        base_url="https://sandbox.berlingroup.org/psd2/v1",
    )

    # 1st call returns HTTP 429 with Retry-After: 0.01; 2nd call returns HTTP 200 OK
    resp_429 = MagicMock(spec=httpx.Response)
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "0.01"}

    resp_200 = MagicMock(spec=httpx.Response)
    resp_200.status_code = 200
    resp_200.json.return_value = {
        "transactions": {
            "booked": [
                {
                    "transactionId": "tx_429_retry_success",
                    "debtorAccount": {"iban": "DE89370400440532013000"},
                    "creditorAccount": {"iban": "DE89370400440532013999"},
                    "transactionAmount": {"amount": "120.00", "currency": "EUR"},
                    "bookingDate": "2026-07-28T12:00:00Z",
                }
            ]
        }
    }

    call_count = 0

    def mock_request_fn():
        nonlocal call_count
        call_count += 1
        return resp_429 if call_count == 1 else resp_200

    resp = connector._handle_rate_limit_and_execute(mock_request_fn)

    assert resp.status_code == 200
    assert call_count == 2
