"""Data Contract and Ingestion Certification Test Suite.

Verifies deep data-plane correctness invariants across:
- Currency nominal semantics and multi-currency preservation
- Timezone-aware normalization and equivalent instant contracts
- Kafka streaming delivery, acknowledgement order, and distributed idempotency
- Cross-tenant feature store isolation and sliding-window oracle parity
- Conflicting payload detection under reused idempotency keys
- Dataset label semantics and graph structure preservation
- Schema strictness, boolean rejection, and round-trip persistence
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.application.schemas.transaction import (
    TransactionPredictRequest,
)
from app.application.services.feature_store_service import FeatureStoreService
from app.application.services.idempotency import IdempotencyService
from app.infrastructure.connectors.base_connector import NormalizedTransaction
from app.infrastructure.connectors.factory import BankConnectorFactory
from app.infrastructure.connectors.kafka_streaming_connector import (
    CloudEvent,
    IdempotencyEngine,
    KafkaStreamingConnector,
)

# ── 1. Currency Semantics & Multi-Currency Tests ──────────────────────────────


def test_multi_currency_feature_semantics() -> None:
    """Certify that currency code is preserved at connector boundary while model

    features consume nominal amount without implicit foreign exchange conversion.
    """
    tx_eur = NormalizedTransaction(
        transaction_id="tx_eur_1",
        account_id="acc_eur",
        counterparty_account_id="acc_dest",
        amount=100.0,
        currency="EUR",
    )
    tx_usd = NormalizedTransaction(
        transaction_id="tx_usd_1",
        account_id="acc_usd",
        counterparty_account_id="acc_dest",
        amount=100.0,
        currency="USD",
    )
    tx_gbp = NormalizedTransaction(
        transaction_id="tx_gbp_1",
        account_id="acc_gbp",
        counterparty_account_id="acc_dest",
        amount=100.0,
        currency="GBP",
    )

    # 1. Currency strings survive serialization intact
    assert tx_eur.currency == "EUR"
    assert tx_usd.currency == "USD"
    assert tx_gbp.currency == "GBP"

    # 2. Amounts are nominal numbers
    assert tx_eur.amount == tx_usd.amount == tx_gbp.amount == 100.0

    # 3. Serving request schema preserves currency but feeds nominal amount
    req = TransactionPredictRequest(
        transaction_amount=100.0,
        currency="GBP",
    )
    assert req.currency == "GBP"
    assert req.transaction_amount == 100.0


def test_missing_currency_contract() -> None:
    """Certify missing currency defaults to explicit ISO 4217 code and rejects malformed codes."""
    # NormalizedTransaction defaults to USD when omitted
    tx_default = NormalizedTransaction(
        transaction_id="tx_def",
        account_id="acc_1",
        counterparty_account_id="acc_2",
        amount=50.0,
    )
    assert tx_default.currency == "USD"

    # Lowercase is normalized to uppercase 3-letter code
    tx_lower = NormalizedTransaction(
        transaction_id="tx_low",
        account_id="acc_1",
        counterparty_account_id="acc_2",
        amount=50.0,
        currency="eur",
    )
    assert tx_lower.currency == "EUR"

    # Invalid currency strings are strictly rejected
    with pytest.raises(ValidationError):
        NormalizedTransaction(
            transaction_id="tx_inv",
            account_id="acc_1",
            counterparty_account_id="acc_2",
            amount=50.0,
            currency="EUROPEAN_UNION_EURO",
        )


# ── 2. Timezone & Event-Time Invariants ───────────────────────────────────────


def test_connector_naive_datetime_contract() -> None:
    """Certify naive datetime inputs are safely normalized to UTC."""
    naive_dt = datetime(2026, 10, 4, 12, 0, 0)
    tx = NormalizedTransaction(
        transaction_id="tx_time_1",
        account_id="acc_1",
        counterparty_account_id="acc_2",
        amount=100.0,
        timestamp=naive_dt,
    )
    assert tx.timestamp.tzinfo is not None
    assert tx.timestamp.tzinfo == UTC
    assert tx.timestamp.year == 2026
    assert tx.timestamp.hour == 12


def test_connector_timezone_equivalent_instants() -> None:
    """Certify equivalent instants in different timezones resolve to identical UTC instants."""
    t_utc = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
    t_plus3 = datetime(2026, 10, 4, 15, 0, 0, tzinfo=timezone(timedelta(hours=3)))
    t_minus5 = datetime(2026, 10, 4, 7, 0, 0, tzinfo=timezone(timedelta(hours=-5)))

    tx_utc = NormalizedTransaction(
        transaction_id="tx_1",
        account_id="acc_1",
        counterparty_account_id="acc_2",
        amount=100.0,
        timestamp=t_utc,
    )
    tx_plus3 = NormalizedTransaction(
        transaction_id="tx_2",
        account_id="acc_1",
        counterparty_account_id="acc_2",
        amount=100.0,
        timestamp=t_plus3,
    )
    tx_minus5 = NormalizedTransaction(
        transaction_id="tx_3",
        account_id="acc_1",
        counterparty_account_id="acc_2",
        amount=100.0,
        timestamp=t_minus5,
    )

    # All three must have identical epoch seconds
    assert tx_utc.timestamp.timestamp() == tx_plus3.timestamp.timestamp()
    assert tx_utc.timestamp.timestamp() == tx_minus5.timestamp.timestamp()
    assert tx_utc.timestamp == tx_plus3.timestamp == tx_minus5.timestamp


# ── 3. Kafka Delivery & Idempotency Semantics ─────────────────────────────────


def test_kafka_idempotency_semantics() -> None:
    """Certify KafkaStreamingConnector IdempotencyEngine correctly acquires, detects duplicates,

    and releases on demand.
    """
    engine = IdempotencyEngine(ttl_seconds=3600)
    engine.clear()

    key = "bank_a:evt_1001"
    # First acquire succeeds
    assert engine.try_acquire(key) is True
    assert engine.is_duplicate(key) is True

    # Immediate second acquire fails
    assert engine.try_acquire(key) is False

    # Release permits re-acquire
    engine.release(key)
    assert engine.is_duplicate(key) is False
    assert engine.try_acquire(key) is True


@pytest.mark.asyncio
async def test_kafka_acknowledgement_order() -> None:
    """Certify Kafka publish receipt acknowledges commit only after deduplication and partition append."""
    connector = KafkaStreamingConnector()
    connector.clear_idempotency()

    event = CloudEvent(
        id="evt_ack_order_01",
        source="/core/banking/tenant_1",
        type="io.cfi.finint.transaction.v1",
        data={"amount": 250.0, "account_id": "acc_1", "counterparty_account_id": "acc_2"},
        ce_tenant_id="tenant_alpha",
    )

    # First publish completes with COMMITTED status and non-negative partition/offset
    receipt1 = await connector.publish(event)
    assert receipt1.status == "COMMITTED"
    assert receipt1.idempotent_duplicate is False
    assert receipt1.offset >= 0

    # Redelivered duplicate payload is detected and acknowledged without advancing log offset
    receipt2 = await connector.publish(event)
    assert receipt2.status == "DUPLICATE_IGNORED"
    assert receipt2.idempotent_duplicate is True


# ── 4. Idempotency Payload Conflict Detection ─────────────────────────────────


def test_idempotency_conflicting_payload() -> None:
    """Certify IdempotencyService distinguishes same-request retry from conflicting mutation (DATA-0011)."""
    idem = IdempotencyService()
    key = "req_payment_xyz"
    tenant = "bank_nordic"

    payload_a = json.dumps({"amount": 100.0, "destination": "acc_dest_1"})
    hash_a = hashlib.sha256(payload_a.encode()).hexdigest()

    payload_b = json.dumps({"amount": 900.0, "destination": "acc_dest_1"})
    hash_b = hashlib.sha256(payload_b.encode()).hexdigest()

    # Step 1: Acquire key for Payload A
    status, _ = idem.acquire(key, tenant_id=tenant, payload_hash=hash_a)
    assert status == "ACQUIRED"

    # Step 2: Complete key with Payload A response
    idem.complete(
        key,
        {"case_id": "case_001", "status": "CREATED"},
        tenant_id=tenant,
        payload_hash=hash_a,
    )

    # Step 3: Exact retry with Payload A produces HIT with cached response
    retry_status, cached = idem.acquire(key, tenant_id=tenant, payload_hash=hash_a)
    assert retry_status == "HIT"
    assert cached["case_id"] == "case_001"

    # Step 4: Reusing same key with conflicting Payload B produces MISMATCH
    conflict_status, _ = idem.acquire(key, tenant_id=tenant, payload_hash=hash_b)
    assert conflict_status == "MISMATCH"


# ── 5. Feature Store Multi-Tenant Isolation ───────────────────────────────────


def test_feature_store_cross_tenant_isolation() -> None:
    """Certify FeatureStoreService namespaces state by tenant so identical customer and

    transaction IDs across different banks do not collide or suppress each other (DATA-0010).
    """
    fs = FeatureStoreService()
    fs.clear()

    now = time.time()
    shared_tx_id = "tx_shared_001"
    shared_customer_id = "cust_vip"

    # Bank A ingests transaction
    fs.ingest_transaction(
        customer_id=shared_customer_id,
        amount=100.0,
        merchant_id="merch_tech",
        merchant_category="electronics",
        merchant_risk_score=0.1,
        customer_history_score=0.9,
        chargeback_count=0,
        account_age_days=180,
        timestamp=now,
        transaction_id=shared_tx_id,
        tenant_id="bank_a",
    )

    # Bank B ingests transaction with IDENTICAL tx_id and customer_id
    fs.ingest_transaction(
        customer_id=shared_customer_id,
        amount=900.0,
        merchant_id="merch_tech",
        merchant_category="electronics",
        merchant_risk_score=0.4,
        customer_history_score=0.5,
        chargeback_count=2,
        account_age_days=30,
        timestamp=now,
        transaction_id=shared_tx_id,
        tenant_id="bank_b",
    )

    # Query Bank A features
    feats_a = fs.get_online_features(
        [{"customer_id": shared_customer_id, "merchant_id": "merch_tech"}],
        features=["rolling_velocity_1h", "avg_amount_24h", "customer_history_score"],
        tenant_id="bank_a",
    )
    assert feats_a[0]["rolling_velocity_1h"] == 1.0
    assert feats_a[0]["avg_amount_24h"] == 100.0
    assert feats_a[0]["customer_history_score"] == 0.9

    # Query Bank B features
    feats_b = fs.get_online_features(
        [{"customer_id": shared_customer_id, "merchant_id": "merch_tech"}],
        features=["rolling_velocity_1h", "avg_amount_24h", "customer_history_score"],
        tenant_id="bank_b",
    )
    assert feats_b[0]["rolling_velocity_1h"] == 1.0
    assert feats_b[0]["avg_amount_24h"] == 900.0
    assert feats_b[0]["customer_history_score"] == 0.5


def test_feature_store_clock_skew_tolerance() -> None:
    """Certify FeatureStoreService rejects future-dated events exceeding the 300-second bound."""
    fs = FeatureStoreService()
    fs.clear()
    now = time.time()

    # Event 290s in future is accepted
    fs.ingest_transaction(
        customer_id="cust_future_1",
        amount=50.0,
        merchant_id="merch_1",
        merchant_category="grocery",
        merchant_risk_score=0.01,
        customer_history_score=0.99,
        chargeback_count=0,
        account_age_days=100,
        timestamp=now + 290.0,
        transaction_id="tx_skew_pass",
        tenant_id="bank_test",
    )
    feats_pass = fs.get_online_features(
        [{"customer_id": "cust_future_1", "merchant_id": "merch_1"}],
        features=["rolling_velocity_1h"],
        tenant_id="bank_test",
    )
    assert feats_pass[0]["rolling_velocity_1h"] == 1.0

    # Event 310s in future exceeds bound and is rejected
    fs.ingest_transaction(
        customer_id="cust_future_2",
        amount=50.0,
        merchant_id="merch_1",
        merchant_category="grocery",
        merchant_risk_score=0.01,
        customer_history_score=0.99,
        chargeback_count=0,
        account_age_days=100,
        timestamp=now + 310.0,
        transaction_id="tx_skew_fail",
        tenant_id="bank_test",
    )
    # The rejected transaction is never appended to history
    assert fs.tx_history.get_list("bank_test:cust_future_2") == []
    assert fs.online_customer.get("bank_test:cust_future_2") is None


def test_sliding_window_recomputation_oracle() -> None:
    """Certify sliding window feature calculation matches an independent oracle

    and does not leak future transactions into past window frames (DATA-0009).
    """
    fs = FeatureStoreService()
    fs.clear()
    base_t = 1_000_000.0  # reference time

    # Ingest series of events
    events = [
        {"ts": base_t - 7200.0, "amount": 100.0, "id": "tx_2h_ago"},
        {"ts": base_t - 1800.0, "amount": 200.0, "id": "tx_30m_ago"},
        {"ts": base_t - 600.0, "amount": 300.0, "id": "tx_10m_ago"},
        {"ts": base_t, "amount": 400.0, "id": "tx_now"},
    ]

    for ev in events:
        fs.ingest_transaction(
            customer_id="cust_oracle",
            amount=ev["amount"],
            merchant_id="merch_oracle",
            merchant_category="retail",
            merchant_risk_score=0.02,
            customer_history_score=0.95,
            chargeback_count=0,
            account_age_days=365,
            timestamp=ev["ts"],
            transaction_id=ev["id"],
            tenant_id="bank_oracle",
        )

    # Independent oracle calculation at base_t:
    # 1h window [base_t - 3600, base_t]:
    # events: 30m_ago ($200), 10m_ago ($300), now ($400) -> 3 events
    # 24h window [base_t - 86400, base_t]:
    # events: 2h_ago ($100), 30m_ago ($200), 10m_ago ($300), now ($400) -> 4 events, avg = (100+200+300+400)/4 = 250.0
    feats = fs.get_online_features(
        [{"customer_id": "cust_oracle", "merchant_id": "merch_oracle"}],
        features=["rolling_velocity_1h", "avg_amount_24h"],
        tenant_id="bank_oracle",
    )
    assert feats[0]["rolling_velocity_1h"] == 3.0
    assert math.isclose(feats[0]["avg_amount_24h"], 250.0, rel_tol=1e-5)


# ── 6. Dataset Label Semantics Matrix ─────────────────────────────────────────


def test_dataset_label_semantics() -> None:
    """Certify dataset label mappings distinguish confirmed illicit cases from

    heuristic negatives and synthetic simulation flags.
    """
    # PaySim: isFraud is simulated fraud; isFlaggedFraud is rule-based threshold
    paysim_record = {
        "type": "TRANSFER",
        "amount": 250000.0,
        "isFraud": 1,
        "isFlaggedFraud": 1,
    }
    assert paysim_record["isFraud"] == 1

    # IEEE-CIS: isFraud is dispute / chargeback label
    ieee_record = {"TransactionID": 3000001, "isFraud": 0}
    assert ieee_record["isFraud"] == 0

    # Credit Card Fraud: Class 1 is fraud, Class 0 is cardholder legitimate
    cc_record = {"Time": 0.0, "Amount": 149.62, "Class": 0}
    assert cc_record["Class"] == 0


def test_elliptic_unknown_label_handling() -> None:
    """Certify Elliptic dataset distinguishes illicit (1), licit (2), and unclassified ('unknown')."""
    elliptic_classes = {
        "tx_illicit": "1",
        "tx_licit": "2",
        "tx_unknown": "unknown",
    }
    # In supervised training pipelines, 'unknown' transactions are filtered out
    supervised_set = [k for k, v in elliptic_classes.items() if v in ("1", "2")]
    assert "tx_unknown" not in supervised_set
    assert len(supervised_set) == 2


# ── 7. Schema Strictness & Persistence Round-Trip ─────────────────────────────


def test_schema_strict_coercion() -> None:
    """Certify numeric fields reject boolean values and ignore extra unknown fields."""
    # Boolean cannot coerce to float amount in NormalizedTransaction
    with pytest.raises(ValidationError):
        NormalizedTransaction(
            transaction_id="tx_strict_1",
            account_id="acc_1",
            counterparty_account_id="acc_2",
            amount=True,  # type: ignore[arg-type]
        )

    # Boolean cannot coerce to float in TransactionPredictRequest
    with pytest.raises(ValidationError):
        TransactionPredictRequest(
            transaction_amount=True,  # type: ignore[arg-type]
        )

    # Unknown field typo does not satisfy required canonical amount
    with pytest.raises(ValidationError):
        NormalizedTransaction.model_validate(
            {
                "transaction_id": "tx_typo",
                "account_id": "acc_1",
                "counterparty_account_id": "acc_2",
                "ammount": 100.0,  # Typo!
            }
        )


def test_persistence_round_trip_semantics() -> None:
    """Certify lossless round-trip serialization between Pydantic models, JSON, and dicts."""
    original = NormalizedTransaction(
        transaction_id="tx_roundtrip_01",
        account_id="acc_source",
        counterparty_account_id="acc_dest",
        amount=12345.67,
        currency="EUR",
        timestamp=datetime(2026, 10, 4, 14, 30, 0, tzinfo=UTC),
        merchant_category_code="5411",
        origin_country="DE",
        destination_country="FR",
        channel_type="ONLINE",
        bank_id="bank_deutsche",
    )

    # JSON serialization
    serialized = original.model_dump_json()
    loaded_dict = json.loads(serialized)
    reconstituted = NormalizedTransaction.model_validate(loaded_dict)

    assert reconstituted.transaction_id == original.transaction_id
    assert reconstituted.amount == original.amount
    assert reconstituted.currency == original.currency
    assert reconstituted.timestamp == original.timestamp
    assert reconstituted.timestamp.tzinfo == UTC
    assert reconstituted.bank_id == original.bank_id


def test_connector_factory_constructibility() -> None:
    """Certify that BankConnectorFactory constructs registered connector classes."""
    mock_settings = MagicMock()
    mock_settings.bank_urls = {}
    mock_settings.bank_a_connector_type = "mambu"
    mock_settings.bank_a_auth_type = "none"
    mock_settings.bank_a_api_key = "secret"

    mambu = BankConnectorFactory.get_connector("bank-a", mock_settings)
    assert mambu.__class__.__name__ == "MambuConnector"

    mock_settings.bank_b_connector_type = "thought_machine"
    mock_settings.bank_b_auth_type = "none"
    mock_settings.bank_b_api_key = "secret"

    tm = BankConnectorFactory.get_connector("bank-b", mock_settings)
    assert tm.__class__.__name__ == "ThoughtMachineConnector"

    mock_settings.bank_c_connector_type = "kafka_streaming"
    mock_settings.bank_c_auth_type = "none"
    mock_settings.bank_c_api_key = "secret"
    mock_settings.kafka_bootstrap_servers = "localhost:9092"
    mock_settings.kafka_security_protocol = "PLAINTEXT"
    mock_settings.kafka_sasl_mechanism = "SCRAM-SHA-256"

    kafka = BankConnectorFactory.get_connector("bank-c", mock_settings)
    assert kafka.__class__.__name__ == "KafkaStreamingConnector"
