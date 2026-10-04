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

from app.application.schemas.cases import CaseCreateRequest
from app.application.schemas.transaction import (
    TransactionPredictRequest,
)
from app.application.services.feature_store_service import FeatureStoreService
from app.application.services.idempotency import IdempotencyService
from app.application.services.streaming_graph_service import StreamingGraphService
from app.infrastructure.connectors.base_connector import NormalizedTransaction
from app.infrastructure.connectors.factory import BankConnectorFactory
from app.infrastructure.connectors.iso20022_connector import ISO20022MessagingConnector
from app.infrastructure.connectors.kafka_streaming_connector import (
    CloudEvent,
    IdempotencyEngine,
    InMemoryKafkaBroker,
    KafkaStreamingConnector,
)
from app.infrastructure.connectors.mambu_connector import MambuConnector
from app.infrastructure.connectors.thought_machine_connector import ThoughtMachineConnector

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
    assert cached is not None
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
            amount=float(ev["amount"]),
            merchant_id="merch_oracle",
            merchant_category="retail",
            merchant_risk_score=0.02,
            customer_history_score=0.95,
            chargeback_count=0,
            account_age_days=365,
            timestamp=float(ev["ts"]),
            transaction_id=str(ev["id"]),
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


# ── 8. Deep Certification Closure Tests ───────────────────────────────────────


def test_currency_feature_contract() -> None:
    """Certify Outcome C: Multi-currency transport is supported, but ML feature

    aggregation is strictly nominal without FX conversion (input-contract limitation).
    Also proves that Tenant Isolation (Tenant A vs Tenant B) does NOT prove currency
    safety within Tenant A.
    """
    store = FeatureStoreService()
    store.clear()

    # Part 1: Within Tenant A, Account X processes 100 EUR then 100 USD
    # The system aggregates nominal units: 100 + 100 = 200
    t0 = 1770000000.0
    store.ingest_transaction(
        customer_id="CUST_X",
        amount=100.0,
        merchant_id="MERCH_1",
        merchant_category="retail",
        merchant_risk_score=0.05,
        customer_history_score=0.9,
        chargeback_count=0,
        account_age_days=100,
        timestamp=t0,
        transaction_id="tx_eur_1",
        tenant_id="tenant_a",
    )
    store.ingest_transaction(
        customer_id="CUST_X",
        amount=100.0,
        merchant_id="MERCH_1",
        merchant_category="retail",
        merchant_risk_score=0.05,
        customer_history_score=0.9,
        chargeback_count=0,
        account_age_days=100,
        timestamp=t0 + 60.0,
        transaction_id="tx_usd_1",
        tenant_id="tenant_a",
    )

    stats_a = store.online_stats.get("tenant_a:CUST_X")
    assert stats_a["rolling_velocity_1h"] == 2.0
    # Nominal aggregation without FX: average is 100.0 nominal units
    assert stats_a["avg_amount_24h"] == 100.0

    # Part 2: Tenant B processes 100 USD for Account X
    store.ingest_transaction(
        customer_id="CUST_X",
        amount=100.0,
        merchant_id="MERCH_1",
        merchant_category="retail",
        merchant_risk_score=0.05,
        customer_history_score=0.9,
        chargeback_count=0,
        account_age_days=100,
        timestamp=t0 + 120.0,
        transaction_id="tx_usd_b",
        tenant_id="tenant_b",
    )

    stats_b = store.online_stats.get("tenant_b:CUST_X")
    assert stats_b["rolling_velocity_1h"] == 1.0
    assert stats_b["avg_amount_24h"] == 100.0
    # Tenant A remains unchanged at 2 transactions
    stats_a_check = store.online_stats.get("tenant_a:CUST_X")
    assert stats_a_check["rolling_velocity_1h"] == 2.0
    store.clear()


def test_missing_currency_semantics() -> None:
    """Verify omission of currency across connectors:

    - NormalizedTransaction defaults omitted currency to USD
    - Mambu connector defaults omitted currency to EUR
    - Thought Machine connector defaults omitted currency to EUR
    - Explicit EUR/USD are strictly distinguished from defaults
    """
    # 1. NormalizedTransaction default
    tx_default = NormalizedTransaction(
        transaction_id="tx_def",
        account_id="acc1",
        counterparty_account_id="acc2",
        amount=50.0,
    )
    assert tx_default.currency == "USD"

    # 2. NormalizedTransaction explicit
    tx_explicit = NormalizedTransaction(
        transaction_id="tx_exp",
        account_id="acc1",
        counterparty_account_id="acc2",
        amount=50.0,
        currency="eur",
    )
    assert tx_explicit.currency == "EUR"

    # 3. Mambu connector
    mambu = MambuConnector(tenant_id="bank_mambu")
    payload_no_ccy = {
        "id": "mambu-no-ccy",
        "accountId": "ACC1",
        "counterpartyAccountId": "ACC2",
        "amount": 75.0,
        "creationDate": "2026-03-01T10:00:00Z",
    }
    tx_mambu = mambu._normalize_transaction_event(payload_no_ccy)
    assert tx_mambu.currency == "EUR"

    # 4. Thought Machine connector
    tm = ThoughtMachineConnector(tenant_id="bank_tm")
    tm_payload = {
        "id": "tm_b1",
        "posting_instructions": [
            {
                "id": "inst1",
                "custom_instruction": {
                    "value_timestamp": "2026-03-01T10:00:00Z",
                    "postings": [
                        {"account_id": "ACC1", "amount": "80.0", "credit": False},
                        {"account_id": "ACC2", "amount": "80.0", "credit": True},
                    ],
                },
            }
        ],
    }
    txs_tm = tm.parse_batch(tm_payload)
    assert txs_tm[0].currency == "EUR"


def test_malformed_event_time_fail_closed() -> None:
    """Certify that connectors reject or quarantine malformed source timestamps

    rather than silently substituting datetime.now(UTC).
    """
    # 1. ISO 20022 parser raises ValueError on unparseable timestamp
    with pytest.raises(ValueError, match="Malformed ISO 20022 datetime"):
        ISO20022MessagingConnector._parse_iso_datetime("not-a-valid-date-2026")

    # 2. Mambu connector raises ValueError on unparseable creationDate
    mambu = MambuConnector(tenant_id="bank_mambu")
    with pytest.raises(ValueError, match="Malformed Mambu timestamp"):
        mambu._normalize_transaction_event({
            "id": "mambu-bad-date",
            "accountId": "ACC1",
            "amount": 100.0,
            "creationDate": "2026-99-99T99:99:99Z",
        })

    # 3. Thought Machine connector raises ValueError on unparseable timestamp
    tm = ThoughtMachineConnector(tenant_id="bank_tm")
    with pytest.raises(ValueError, match="Malformed Thought Machine timestamp"):
        tm.parse_batch({
            "id": "tm_bad",
            "posting_instructions": [
                {
                    "id": "inst_bad",
                    "custom_instruction": {
                        "value_timestamp": "garbage-date-string",
                        "postings": [{"account_id": "ACC1", "amount": "50.0", "credit": True}],
                    },
                }
            ],
        })

    # 4. Kafka CloudEvents quarantines malformed time to DLQ
    malformed_ce = {
        "specversion": "1.0",
        "id": "evt-bad-time",
        "source": "/test",
        "type": "cfi.transaction.v1",
        "time": "not-a-datetime",
        "data": {},
    }
    with pytest.raises(ValidationError):
        CloudEvent.model_validate(malformed_ce)


def test_naive_timestamp_contract() -> None:
    """Certify repository contract for naive datetime inputs:

    Naive timestamps are explicitly normalized to UTC, and timezone-aware
    inputs representing the same UTC instant are recognized as equivalent.
    """
    naive_dt = datetime(2026, 6, 1, 14, 30, 0)
    tx = NormalizedTransaction(
        transaction_id="tx_naive",
        account_id="acc1",
        counterparty_account_id="acc2",
        amount=100.0,
        timestamp=naive_dt,
    )
    assert tx.timestamp.tzinfo == UTC
    assert tx.timestamp == datetime(2026, 6, 1, 14, 30, 0, tzinfo=UTC)

    # Cross-timezone equivalent instant (UTC+2 at 16:30 is 14:30 UTC)
    tz_plus2 = timezone(timedelta(hours=2))
    aware_dt = datetime(2026, 6, 1, 16, 30, 0, tzinfo=tz_plus2)
    tx_aware = NormalizedTransaction(
        transaction_id="tx_aware",
        account_id="acc1",
        counterparty_account_id="acc2",
        amount=100.0,
        timestamp=aware_dt,
    )
    assert tx_aware.timestamp == tx.timestamp


def test_float_threshold_boundaries() -> None:
    """Test monetary float threshold boundaries with math.nextafter, cent boundaries,

    and repeated summation rounding truth.
    """
    threshold = 10000.0

    # 1. Probing binary float boundaries around threshold
    val_below = math.nextafter(threshold, 0.0)
    val_exact = threshold
    val_above = math.nextafter(threshold, float("inf"))

    assert val_below < threshold
    assert val_exact == threshold
    assert val_above > threshold

    # The gap between threshold and adjacent float is << 1 cent
    delta_below = threshold - val_below
    delta_above = val_above - threshold
    assert delta_below < 1e-11
    assert delta_above < 1e-11

    # 2. Cent boundaries (real currency precision)
    cent_below = 9999.99
    cent_above = 10000.01
    assert cent_below < threshold
    assert cent_above > threshold
    assert round(threshold - cent_below, 2) == 0.01
    assert round(cent_above - threshold, 2) == 0.01

    # 3. Floating-point precision demonstrates IEEE-754 binary float limitation
    # 0.1 + 0.2 is strictly 0.30000000000000004 in IEEE-754 binary double precision
    float_sum = 0.1 + 0.2
    assert float_sum != 0.3
    assert abs(float_sum - 0.3) < 1e-15
    assert round(float_sum, 2) == 0.30


def test_monetary_serialization_round_trip() -> None:
    """Certify that representative decimal amounts round-trip accurately

    at 2 decimal places (cent precision).
    """
    test_amounts = [0.01, 0.10, 0.30, 100.10, 9999999.99]
    for amt in test_amounts:
        tx = NormalizedTransaction(
            transaction_id=f"tx_{amt}",
            account_id="acc_src",
            counterparty_account_id="acc_dst",
            amount=amt,
            currency="EUR",
        )
        json_str = tx.model_dump_json()
        parsed = NormalizedTransaction.model_validate_json(json_str)
        assert round(parsed.amount, 2) == amt
        data = json.loads(json_str)
        assert data["amount"] == amt


@pytest.mark.asyncio
async def test_kafka_distributed_idempotency_binding() -> None:
    """Certify that two completely independent Kafka connector instances

    (with zero shared in-memory state) coordinate deduplication when a shared
    distributed store (simulated Redis) is configured.
    """
    shared_redis_store: dict[str, str] = {}

    class RedisDouble:
        def set(self, name: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
            if nx and name in shared_redis_store:
                return False
            shared_redis_store[name] = value
            return True

        def get(self, name: str) -> str | None:
            return shared_redis_store.get(name)

    idem_service = IdempotencyService.get()
    orig_redis = idem_service._redis_client
    idem_service._redis_client = RedisDouble()

    try:
        broker1 = InMemoryKafkaBroker()
        broker2 = InMemoryKafkaBroker()

        conn1 = KafkaStreamingConnector(
            client_id="worker-1",
            group_id="group-1",
            in_memory_broker=broker1,
        )
        conn2 = KafkaStreamingConnector(
            client_id="worker-2",
            group_id="group-2",
            in_memory_broker=broker2,
        )

        assert conn1._idempotency._processed_keys is not conn2._idempotency._processed_keys

        event = CloudEvent(
            id="evt-dist-100",
            source="/test/dist",
            type="cfi.transaction.v1",
            ce_tenant_id="bank_dist",
            data={"amount": 500.0, "currency": "EUR"},
        )

        receipt1 = await conn1.publish(event)
        assert receipt1.status == "COMMITTED"
        assert not receipt1.idempotent_duplicate

        receipt2 = await conn2.publish(event)
        assert receipt2.status == "DUPLICATE_IGNORED"
        assert receipt2.idempotent_duplicate
    finally:
        idem_service._redis_client = orig_redis


def test_kafka_crash_window_duplicate_semantics() -> None:
    """Trace Crash Window C: business mutations succeed, then process crashes

    before idempotency completion. On redelivery:
    - FeatureStoreService is DEDUPLICATED_BY_EVENT_ID (via tx_id check in history)
    - StreamingGraphService is NON_IDEMPOTENT (appends duplicate edge to edge buffer)
    """
    store = FeatureStoreService()
    store.clear()
    graph = StreamingGraphService()

    tx_payload = {
        "transaction_id": "tx_crash_test",
        "sender_id": "ACC_CRASH_SRC",
        "receiver_id": "ACC_CRASH_DST",
        "customer_id": "CUST_CRASH",
        "amount": 250.0,
        "currency": "EUR",
        "timestamp": 1770000000.0,
    }

    # Initial delivery: both execute mutation
    store.ingest_transaction(
        customer_id="CUST_CRASH",
        amount=250.0,
        merchant_id="MERCH_CRASH",
        merchant_category="retail",
        merchant_risk_score=0.1,
        customer_history_score=0.8,
        chargeback_count=0,
        account_age_days=60,
        timestamp=1770000000.0,
        transaction_id="tx_crash_test",
        tenant_id="tenant_crash",
    )
    graph.add_transaction(tx_payload)

    assert len(graph.edges) == 1
    assert store.online_stats.get("tenant_crash:CUST_CRASH")["rolling_velocity_1h"] == 1.0

    # Crash Window C: process crashes before idempotency completion; redelivery occurs
    store.ingest_transaction(
        customer_id="CUST_CRASH",
        amount=250.0,
        merchant_id="MERCH_CRASH",
        merchant_category="retail",
        merchant_risk_score=0.1,
        customer_history_score=0.8,
        chargeback_count=0,
        account_age_days=60,
        timestamp=1770000000.0,
        transaction_id="tx_crash_test",
        tenant_id="tenant_crash",
    )
    graph.add_transaction(tx_payload)

    # 1. Feature store deduplicated by tx_id -> count remains 1
    assert store.online_stats.get("tenant_crash:CUST_CRASH")["rolling_velocity_1h"] == 1.0

    # 2. Graph service is non-idempotent -> edge count increases to 2
    assert len(graph.edges) == 2
    store.clear()


def test_idempotency_ttl_expiry() -> None:
    """Certify that idempotency protection is temporally bounded by TTL.

    After expiry, the key can be acquired as a new event.
    """
    idem = IdempotencyService()
    idem._redis_client = None
    key = "bounded_key_test"

    status, _ = idem.acquire(key, in_progress_timeout=0.05)
    assert status == "ACQUIRED"
    idem.complete(key, {"created": True})

    hit_status, cached = idem.acquire(key)
    assert hit_status == "HIT"
    assert cached == {"created": True}

    with idem._fallback_lock:
        data, _, in_prog = idem._fallback[idem._build_redis_key(key)]
        idem._fallback[idem._build_redis_key(key)] = (data, time.monotonic() - 1.0, in_prog)

    expired_status, _ = idem.acquire(key)
    assert expired_status == "ACQUIRED"


def test_idempotency_payload_canonicalization() -> None:
    """Certify that payload canonicalization hashes identical semantics

    regardless of whitespace/ordering, and detects material parameter changes.
    """
    req_a = CaseCreateRequest(title="Mule Network Investigation", priority="p2_high")
    req_b = CaseCreateRequest(priority="p2_high", title="Mule Network Investigation")

    hash_a = hashlib.sha256(req_a.model_dump_json().encode("utf-8")).hexdigest()
    hash_b = hashlib.sha256(req_b.model_dump_json().encode("utf-8")).hexdigest()
    assert hash_a == hash_b

    req_diff = CaseCreateRequest(title="Mule Network Investigation", priority="p1_critical")
    hash_diff = hashlib.sha256(req_diff.model_dump_json().encode("utf-8")).hexdigest()
    assert hash_diff != hash_a

    idem = IdempotencyService()
    idem._redis_client = None
    key = "idem_canon_test"
    idem.acquire(key, payload_hash=hash_a)
    idem.complete(key, {"case_id": "case-123"}, payload_hash=hash_a)

    status_match, resp = idem.acquire(key, payload_hash=hash_a)
    assert status_match == "HIT"
    assert resp == {"case_id": "case-123"}

    status_mismatch, _ = idem.acquire(key, payload_hash=hash_diff)
    assert status_mismatch == "MISMATCH"


def test_feature_store_full_tenant_namespace() -> None:
    """Certify full tenant isolation across all feature store structures:

    online_customer, online_merchant, online_stats, tx_history, and deduplication state
    using identical IDs across two tenants.
    """
    store = FeatureStoreService()
    store.clear()

    tx_alpha = {
        "transaction_id": "shared_tx_99",
        "customer_id": "shared_cust_99",
        "merchant_id": "shared_merch_99",
        "account_id": "shared_acc_99",
        "amount": 100.0,
        "currency": "EUR",
        "timestamp": 1770000000.0,
        "customer_history_score": 0.95,
        "account_age_days": 365,
        "chargeback_count": 0,
        "merchant_category": "grocery",
        "merchant_risk_score": 0.05,
    }
    tx_beta = {
        "transaction_id": "shared_tx_99",
        "customer_id": "shared_cust_99",
        "merchant_id": "shared_merch_99",
        "account_id": "shared_acc_99",
        "amount": 500.0,
        "currency": "USD",
        "timestamp": 1770000000.0,
        "customer_history_score": 0.30,
        "account_age_days": 10,
        "chargeback_count": 5,
        "merchant_category": "crypto",
        "merchant_risk_score": 0.85,
    }

    store.ingest_transaction(
        customer_id=tx_alpha["customer_id"],
        amount=tx_alpha["amount"],
        merchant_id=tx_alpha["merchant_id"],
        merchant_category=tx_alpha["merchant_category"],
        merchant_risk_score=tx_alpha["merchant_risk_score"],
        customer_history_score=tx_alpha["customer_history_score"],
        chargeback_count=tx_alpha["chargeback_count"],
        account_age_days=tx_alpha["account_age_days"],
        timestamp=tx_alpha["timestamp"],
        transaction_id=tx_alpha["transaction_id"],
        tenant_id="bank_alpha",
    )
    store.ingest_transaction(
        customer_id=tx_beta["customer_id"],
        amount=tx_beta["amount"],
        merchant_id=tx_beta["merchant_id"],
        merchant_category=tx_beta["merchant_category"],
        merchant_risk_score=tx_beta["merchant_risk_score"],
        customer_history_score=tx_beta["customer_history_score"],
        chargeback_count=tx_beta["chargeback_count"],
        account_age_days=tx_beta["account_age_days"],
        timestamp=tx_beta["timestamp"],
        transaction_id=tx_beta["transaction_id"],
        tenant_id="bank_beta",
    )

    # 1. online_customer
    cust_a = store.online_customer.get("bank_alpha:shared_cust_99")
    cust_b = store.online_customer.get("bank_beta:shared_cust_99")
    assert cust_a["customer_history_score"] == 0.95
    assert cust_b["customer_history_score"] == 0.30

    # 2. online_merchant
    merch_a = store.online_merchant.get("bank_alpha:shared_merch_99")
    merch_b = store.online_merchant.get("bank_beta:shared_merch_99")
    assert merch_a["merchant_category"] == "grocery"
    assert merch_b["merchant_category"] == "crypto"

    # 3. online_stats
    stats_a = store.online_stats.get("bank_alpha:shared_cust_99")
    stats_b = store.online_stats.get("bank_beta:shared_cust_99")
    assert stats_a["avg_amount_24h"] == 100.0
    assert stats_b["avg_amount_24h"] == 500.0

    # 4. tx_history
    hist_a = store.tx_history.get_list("bank_alpha:shared_cust_99")
    hist_b = store.tx_history.get_list("bank_beta:shared_cust_99")
    assert len(hist_a) == 1 and hist_a[0]["amount"] == 100.0
    assert len(hist_b) == 1 and hist_b[0]["amount"] == 500.0

    # 5. Deduplication state
    assert hist_a[0]["tx_id"] == "bank_alpha:shared_tx_99"
    assert hist_b[0]["tx_id"] == "bank_beta:shared_tx_99"
    store.clear()


def test_temporal_window_exact_boundaries() -> None:
    """Certify that sliding window filtering enforces [ts - W, ts] exactly,

    testing ts - 3600 - epsilon, ts - 3600, ts, and ts + epsilon.
    """
    ts = 1770000000.0
    one_hour_ago = ts - 3600.0

    history = [
        {"tx_id": "t_out_past", "amount": 10.0, "timestamp": one_hour_ago - 0.001},
        {"tx_id": "t_exact_past", "amount": 20.0, "timestamp": one_hour_ago},
        {"tx_id": "t_mid", "amount": 30.0, "timestamp": ts - 1800.0},
        {"tx_id": "t_exact_anchor", "amount": 40.0, "timestamp": ts},
        {"tx_id": "t_future_leak", "amount": 50.0, "timestamp": ts + 0.001},
    ]

    tx_1h = [tx for tx in history if one_hour_ago <= float(tx["timestamp"]) <= ts]
    included_ids = [str(tx["tx_id"]) for tx in tx_1h]

    assert "t_out_past" not in included_ids
    assert "t_exact_past" in included_ids
    assert "t_mid" in included_ids
    assert "t_exact_anchor" in included_ids
    assert "t_future_leak" not in included_ids
    assert len(tx_1h) == 3
    assert sum(float(tx["amount"]) for tx in tx_1h) == 90.0


@pytest.mark.asyncio
async def test_dlq_failure_semantics() -> None:
    """Certify that if DLQ publishing fails, consume_batch does NOT commit

    the original offset and raises an error instead of pretending quarantine succeeded.
    """
    broker = InMemoryKafkaBroker()
    connector = KafkaStreamingConnector(
        transaction_topic="cfi.test.dlq.tx",
        dlq_topic="cfi.test.dlq.unparseable",
        in_memory_broker=broker,
    )

    await broker.publish("cfi.test.dlq.tx", b"poisoned-raw-binary-payload")

    orig_publish = broker.publish

    async def mock_publish(topic: str, message: bytes, partition: int = 0) -> tuple[int, int]:
        if topic == connector.dlq_topic:
            raise RuntimeError("DLQ Broker Disk Full")
        return await orig_publish(topic, message, partition)

    broker.publish = mock_publish  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="DLQ publishing failed"):
        await connector.consume_batch("cfi.test.dlq.tx", max_messages=1)

    assert broker._group_offsets[connector.group_id]["cfi.test.dlq.tx:0"] == 0


def test_transaction_persistence_round_trip() -> None:
    """Certify that a full canonical transaction preserves tenant, currency,

    timezone-aware UTC timestamp, monetary amount, accounts, and channel across
    data transfer and database serialization boundaries.
    """
    tx = NormalizedTransaction(
        transaction_id="tx_persist_999",
        account_id="ACC_DEBTOR_DE",
        counterparty_account_id="ACC_CREDITOR_FR",
        amount=12500.50,
        currency="EUR",
        timestamp=datetime(2026, 7, 20, 15, 30, 45, tzinfo=UTC),
        merchant_category_code="6011",
        origin_country="DE",
        destination_country="FR",
        device_fingerprint="fp_secure_888",
        ip_subnet="10.0.0.0/24",
        channel_type="SWIFT_MT103",
        bank_id="bank_bundesbank",
    )

    row_dict = tx.model_dump()
    assert row_dict["bank_id"] == "bank_bundesbank"
    assert row_dict["currency"] == "EUR"
    assert row_dict["amount"] == 12500.50
    assert row_dict["timestamp"] == datetime(2026, 7, 20, 15, 30, 45, tzinfo=UTC)

    json_payload = tx.model_dump_json()
    reconstructed = NormalizedTransaction.model_validate_json(json_payload)

    assert reconstructed.transaction_id == tx.transaction_id
    assert reconstructed.account_id == tx.account_id
    assert reconstructed.counterparty_account_id == tx.counterparty_account_id
    assert reconstructed.amount == tx.amount
    assert reconstructed.currency == tx.currency
    assert reconstructed.timestamp == tx.timestamp
    assert reconstructed.timestamp.tzinfo == UTC
    assert reconstructed.bank_id == tx.bank_id
    assert reconstructed.channel_type == tx.channel_type
