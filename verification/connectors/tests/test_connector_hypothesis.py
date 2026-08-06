"""Hypothesis Property-Based Test Suite for Connector Framework Invariants.

Verifies:
1. Property 1: Canonical Transaction Schema Invariant & Bounds (NormalizedTransaction)
2. Property 2: ISO 20022 & SWIFT Parsing Non-Crash Invariant (ISO20022MessagingConnector)
3. Property 3: HMAC-SHA256 Payload Signature Determinism & Tamper Sensitivity (RESTBankConnector)
4. Property 4: Full-Jitter Exponential Backoff Delay Boundedness (ExponentialBackoffReconnector)
5. Property 5: Open Banking PSD2 JSON Parsing Invariance (OpenBankingConnector)
6. Property 6: Factory Production Guard Policy Enforcement Invariant (BankConnectorFactory)
"""

from __future__ import annotations

import hmac
import hashlib
import json
import os
import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

backend_path = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from app.config import get_settings
from app.infrastructure.client_daemon.reconnector import ExponentialBackoffReconnector
from app.infrastructure.connectors.base_connector import NormalizedTransaction
from app.infrastructure.connectors.factory import BankConnectorFactory
from app.infrastructure.connectors.iso20022_connector import ISO20022MessagingConnector
from app.infrastructure.connectors.open_banking_connector import OpenBankingConnector
from app.infrastructure.connectors.rest_connector import RESTBankConnector


# -----------------------------------------------------------------------------
# Property 1: Canonical Transaction Schema Invariant & Bounds
# -----------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    tx_id=st.text(min_size=1, max_size=30),
    acc_id=st.text(min_size=1, max_size=30),
    cp_acc_id=st.text(min_size=1, max_size=30),
    amount=st.floats(min_value=0.01, max_value=1e8, allow_nan=False, allow_infinity=False),
    currency=st.sampled_from(["USD", "EUR", "GBP", "TRY", "JPY"]),
)
def test_property_normalized_transaction_schema(
    tx_id: str, acc_id: str, cp_acc_id: str, amount: float, currency: str
) -> None:
    """Technical Invariant: Valid amount > 0 always yields NormalizedTransaction with amount > 0."""
    tx = NormalizedTransaction(
        transaction_id=tx_id,
        account_id=acc_id,
        counterparty_account_id=cp_acc_id,
        amount=amount,
        currency=currency,
    )

    assert tx.transaction_id == tx_id
    assert tx.account_id == acc_id
    assert tx.counterparty_account_id == cp_acc_id
    assert tx.amount > 0.0
    assert tx.currency == currency


# -----------------------------------------------------------------------------
# Property 2: ISO 20022 & SWIFT Parsing Non-Crash Invariant
# -----------------------------------------------------------------------------
@settings(max_examples=100)
@given(raw_payload=st.text(min_size=0, max_size=500))
def test_property_iso20022_swift_parsing_non_crash(raw_payload: str) -> None:
    """Technical Invariant: Arbitrary payload input either parses cleanly or raises ValueError cleanly."""
    conn = ISO20022MessagingConnector()

    # Testing pacs.008 XML parser resilience
    try:
        conn.parse_pacs008_xml(raw_payload)
    except ValueError:
        pass  # Expected schema validation failure exception

    # Testing SWIFT MT103 text parser resilience
    try:
        conn.parse_swift_mt103(raw_payload)
    except ValueError:
        pass  # Expected format parse failure exception


# -----------------------------------------------------------------------------
# Property 3: HMAC-SHA256 Payload Signature Determinism & Tamper Sensitivity
# -----------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    bank_id=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=3, max_size=15),
    num_tx=st.integers(min_value=1, max_value=10000),
    mutation_val=st.integers(min_value=1, max_value=999),
)
def test_property_hmac_signature_determinism(bank_id: str, num_tx: int, mutation_val: int) -> None:
    """Technical Invariant: Sign(P) is deterministic; mutated payload alters signature."""
    conn = RESTBankConnector(base_url="http://localhost:8000")
    payload = {"bank_id": bank_id, "num_transactions": num_tx}

    body1, headers1 = conn._sign_payload(payload, {})
    body2, headers2 = conn._sign_payload(payload, {})

    assert body1 == body2

    secret = conn.settings.payload_signing_secret
    if secret:
        sig1 = headers1.get("X-Payload-Signature")
        ts1 = headers1.get("X-Payload-Timestamp")

        # Re-verify matching signature calculation
        expected_sig1 = hmac.new(secret.encode("utf-8"), ts1.encode("utf-8") + b"." + body1, hashlib.sha256).hexdigest()
        assert sig1 == expected_sig1

        # Mutate payload and verify signature change
        mutated_payload = {"bank_id": bank_id, "num_transactions": num_tx + mutation_val}
        body_mut, headers_mut = conn._sign_payload(mutated_payload, {})
        sig_mut = headers_mut.get("X-Payload-Signature")

        assert sig1 != sig_mut


# -----------------------------------------------------------------------------
# Property 4: Full-Jitter Exponential Backoff Delay Boundedness
# -----------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    attempt=st.integers(min_value=0, max_value=10),
    initial=st.floats(min_value=0.5, max_value=5.0, allow_nan=False, allow_infinity=False),
    max_d=st.floats(min_value=10.0, max_value=120.0, allow_nan=False, allow_infinity=False),
)
def test_property_jittered_backoff_boundedness(attempt: int, initial: float, max_d: float) -> None:
    """Technical Invariant: Delay d is strictly bounded in [0.5 * cap, 1.0 * cap]."""
    reconnector = ExponentialBackoffReconnector(max_retries=10, initial_delay=initial, max_delay=max_d, backoff_factor=2.0)
    reconnector.current_attempt = attempt

    delay = reconnector.compute_next_delay()

    calculated_cap = min(initial * (2.0**attempt), max_d)
    lower_bound = calculated_cap * 0.5 - 1e-5
    upper_bound = calculated_cap * 1.0 + 1e-5

    assert lower_bound <= delay <= upper_bound


# -----------------------------------------------------------------------------
# Property 5: Open Banking PSD2 JSON Parsing Invariance
# -----------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    tx_id=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=20),
    amount_str=st.sampled_from(["100.00", "250.50", "15.00", "9999.99"]),
    ccy=st.sampled_from(["EUR", "GBP", "USD"]),
)
def test_property_open_banking_psd2_parsing(tx_id: str, amount_str: str, ccy: str) -> None:
    """Technical Invariant: PSD2 JSON payloads parse into NormalizedTransactions without exception."""
    conn = OpenBankingConnector(token_url="")

    psd2_json = {
        "transactions": {
            "booked": [
                {
                    "transactionId": tx_id,
                    "debtorAccount": {"iban": "DE89370400440532013000"},
                    "creditorAccount": {"iban": "DE89370400440532013999"},
                    "transactionAmount": {"amount": amount_str, "currency": ccy},
                    "bookingDate": "2026-08-01T12:00:00Z",
                }
            ],
            "pending": [],
        }
    }

    txs = conn.parse_psd2_payload(psd2_json)
    assert len(txs) == 1
    assert txs[0].transaction_id == tx_id
    assert txs[0].amount == float(amount_str)
    assert txs[0].currency == ccy


# -----------------------------------------------------------------------------
# Property 6: Factory Production Policy Guard Invariant
# -----------------------------------------------------------------------------
@settings(max_examples=100)
@given(bad_type=st.sampled_from(["mock", "mq_skeleton", "invalid_custom_type", "test_stub"]))
def test_property_factory_production_guard(bad_type: str) -> None:
    """Technical Invariant: Production env APP_ENV=production raises ValueError on non-approved connectors."""
    os.environ["APP_ENV"] = "production"
    settings = get_settings()

    original_type = getattr(settings, "bank_a_connector_type", "parquet")
    settings.bank_a_connector_type = bad_type

    try:
        guard_raised = False
        try:
            BankConnectorFactory.get_connector("bank-a", settings)
        except ValueError:
            guard_raised = True

        assert guard_raised is True
    finally:
        os.environ["APP_ENV"] = "development"
        settings.bank_a_connector_type = original_type
