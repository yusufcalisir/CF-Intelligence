"""Unit and integration tests for data, ingestion, and connector deep correctness.

Verifies:
- DATA-INV-01: Identity preservation across ingestion boundaries
- DATA-INV-02: Strict multi-tenant isolation in idempotency and storage
- DATA-INV-03: Schema fidelity and rejection of non-finite/malformed inputs
- DATA-INV-04: Value fidelity and transaction direction preservation
- DATA-INV-05: Unit and monetary range bounds
- DATA-INV-06: Temporal fidelity and event-time preservation (no silent datetime.now overwrites)
- DATA-INV-08: Idempotency under duplicate and retry deliveries
- DATA-INV-11: Serialization round-trip fidelity
- DATA-INV-15: Concurrency safety in deduplication engines
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.application.schemas.event_schemas import CloudEvent
from app.application.schemas.transaction import TransactionPredictRequest
from app.application.services.feature_store_service import FeatureStoreService
from app.application.services.idempotency import IdempotencyService
from app.application.services.streaming_graph_service import StreamingGraphService
from app.infrastructure.connectors.base_connector import NormalizedTransaction
from app.infrastructure.connectors.factory import BankConnectorFactory
from app.infrastructure.connectors.iso20022_connector import ISO20022MessagingConnector
from app.infrastructure.connectors.kafka_streaming_connector import (
    InMemoryKafkaBroker,
    KafkaStreamingConnector,
)
from app.infrastructure.connectors.mambu_connector import MambuConnector
from app.infrastructure.connectors.thought_machine_connector import ThoughtMachineConnector

# ── 1. Multi-Tenant Idempotency Isolation (DATA-0001 / DATA-INV-02) ──────────


def test_idempotency_service_multi_tenant_isolation() -> None:
    """Verify that identical idempotency keys from different tenants never collide."""
    svc = IdempotencyService()
    svc._redis_client = None  # test in-memory fallback

    shared_key = "client-tx-uuid-9999"
    payload_a = {"tenant": "bank-alpha", "tx_id": "tx-001", "amount": 5000.0}
    payload_b = {"tenant": "bank-beta", "tx_id": "tx-002", "amount": 9000.0}

    # Bank A acquires and completes
    status_a, hit_a = svc.acquire(shared_key, tenant_id="bank-alpha")
    assert status_a == "ACQUIRED"
    assert hit_a is None
    svc.complete(shared_key, payload_a, tenant_id="bank-alpha")

    # Bank B acquires with the SAME key under its own tenant
    status_b, hit_b = svc.acquire(shared_key, tenant_id="bank-beta")
    assert status_b == "ACQUIRED", "Bank B was blocked by Bank A's key!"
    assert hit_b is None
    svc.complete(shared_key, payload_b, tenant_id="bank-beta")

    # Both retrieve their respective cached payloads without collision
    cached_a = svc.get_cached(shared_key, tenant_id="bank-alpha")
    cached_b = svc.get_cached(shared_key, tenant_id="bank-beta")

    assert cached_a is not None
    assert cached_a["tenant"] == "bank-alpha"
    assert cached_a["amount"] == 5000.0

    assert cached_b is not None
    assert cached_b["tenant"] == "bank-beta"
    assert cached_b["amount"] == 9000.0


# ── 2. Numeric Finiteness & Bounds Enforcement (DATA-0003 / DATA-INV-03) ─────


def test_normalized_transaction_rejects_non_finite_amounts() -> None:
    """Verify NormalizedTransaction rejects NaN, +Inf, -Inf, and negative amounts."""
    # Valid transaction succeeds
    tx = NormalizedTransaction(
        transaction_id="tx-100",
        account_id="ACC_A",
        counterparty_account_id="ACC_B",
        amount=150.75,
    )
    assert tx.amount == 150.75

    # Rejects NaN
    with pytest.raises(ValidationError):
        NormalizedTransaction(
            transaction_id="tx-101",
            account_id="ACC_A",
            counterparty_account_id="ACC_B",
            amount=float("nan"),
        )

    # Rejects +Inf
    with pytest.raises(ValidationError):
        NormalizedTransaction(
            transaction_id="tx-102",
            account_id="ACC_A",
            counterparty_account_id="ACC_B",
            amount=float("inf"),
        )

    # Rejects negative amount
    with pytest.raises(ValidationError):
        NormalizedTransaction(
            transaction_id="tx-103",
            account_id="ACC_A",
            counterparty_account_id="ACC_B",
            amount=-50.0,
        )


def test_transaction_predict_request_rejects_non_finite_values() -> None:
    """Verify TransactionPredictRequest rejects non-finite floats."""
    with pytest.raises(ValidationError):
        TransactionPredictRequest(transaction_amount=float("nan"))

    with pytest.raises(ValidationError):
        TransactionPredictRequest(transaction_amount=float("inf"))

    with pytest.raises(ValidationError):
        TransactionPredictRequest(transaction_amount=100.0, velocity=float("inf"))


# ── 3. Temporal Semantics & Timestamp Normalization (DATA-0004 / DATA-INV-06) ─


def test_naive_datetime_normalized_to_utc() -> None:
    """Verify naive datetimes are deterministically converted to UTC in NormalizedTransaction."""
    naive_dt = datetime(2026, 3, 15, 14, 30, 0)  # naive (no tzinfo)
    tx = NormalizedTransaction(
        transaction_id="tx-naive-1",
        account_id="ACC_1",
        counterparty_account_id="ACC_2",
        amount=50.0,
        timestamp=naive_dt,
    )
    assert tx.timestamp.tzinfo is not None
    assert tx.timestamp == datetime(2026, 3, 15, 14, 30, 0, tzinfo=UTC)


def test_iso20022_pacs008_preserves_source_event_time() -> None:
    """Verify ISO 20022 pacs.008 preserves historical CreDtTm rather than overwriting with now()."""
    connector = ISO20022MessagingConnector()
    custom_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
  <FIToFICstmrCdtTrf>
    <GrpHdr>
      <MsgId>MSG-HIST-2026</MsgId>
      <CreDtTm>2026-01-15T08:45:00+00:00</CreDtTm>
    </GrpHdr>
    <CdtTrfTxInf>
      <IntrBkSttlmAmt Ccy="EUR">2500.00</IntrBkSttlmAmt>
      <Dbtr><Nm>Sender AG</Nm><PstlAdr><Ctry>DE</Ctry></PstlAdr></Dbtr>
      <DbtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></DbtrAcct>
      <Cdtr><Nm>Beneficiary SARL</Nm><PstlAdr><Ctry>FR</Ctry></PstlAdr></Cdtr>
      <CdtrAcct><Id><IBAN>FR1420041010050500013M02606</IBAN></Id></CdtrAcct>
    </CdtTrfTxInf>
  </FIToFICstmrCdtTrf>
</Document>"""
    tx = connector.parse_pacs008_xml(custom_xml)
    expected_ts = datetime(2026, 1, 15, 8, 45, 0, tzinfo=UTC)
    assert tx.timestamp == expected_ts
    assert tx.amount == 2500.0
    assert tx.currency == "EUR"
    assert tx.bank_id == "ISO20022"


def test_mambu_connector_preserves_source_event_time() -> None:
    """Verify Mambu connector parses payload creationDate instead of defaulting to now()."""
    connector = MambuConnector(tenant_id="bank_mambu")
    payload = {
        "id": "mambu-tx-777",
        "accountId": "ACC_MAMBU_01",
        "counterpartyAccountId": "ACC_MAMBU_02",
        "amount": 1250.0,
        "currencyCode": "EUR",
        "creationDate": "2026-02-20T11:15:30Z",
    }
    tx = connector._normalize_transaction_event(payload)
    expected_ts = datetime(2026, 2, 20, 11, 15, 30, tzinfo=UTC)
    assert tx.timestamp == expected_ts
    assert tx.amount == 1250.0
    assert tx.bank_id == "bank_mambu"


def test_thought_machine_preserves_source_value_timestamp() -> None:
    """Verify Thought Machine Vault Core connector parses value_timestamp."""
    connector = ThoughtMachineConnector(tenant_id="bank_vault")
    payload = {
        "id": "tm_pib_42",
        "posting_instructions": [
            {
                "id": "tm_inst_01",
                "custom_instruction": {
                    "value_timestamp": "2026-03-01T16:00:00Z",
                    "postings": [
                        {"account_id": "ACC_DEBTOR", "amount": "450.00", "credit": False},
                        {"account_id": "ACC_CREDITOR", "amount": "450.00", "credit": True},
                    ],
                },
            }
        ],
    }
    txs = connector.parse_batch(payload)
    assert len(txs) == 1
    expected_ts = datetime(2026, 3, 1, 16, 0, 0, tzinfo=UTC)
    assert txs[0].timestamp == expected_ts
    assert txs[0].amount == 450.0
    assert txs[0].account_id == "ACC_DEBTOR"
    assert txs[0].counterparty_account_id == "ACC_CREDITOR"
    assert txs[0].bank_id == "bank_vault"


# ── 4. Connector Factory Dynamic Resolution (DATA-0005) ──────────────────────


def test_bank_connector_factory_resolves_all_approved_connectors() -> None:
    """Verify BankConnectorFactory instantiates Mambu, Thought Machine, and Kafka Streaming."""
    mock_settings = MagicMock()
    mock_settings.bank_urls = {}
    mock_settings.bank_a_connector_type = "mambu"
    mock_settings.bank_a_auth_type = "none"
    mock_settings.bank_a_api_key = "test-key"

    mambu_conn = BankConnectorFactory.get_connector("bank-a", mock_settings)
    assert isinstance(mambu_conn, MambuConnector)

    mock_settings.bank_b_connector_type = "thought_machine"
    mock_settings.bank_b_auth_type = "none"
    mock_settings.bank_b_api_key = "test-key"

    tm_conn = BankConnectorFactory.get_connector("bank-b", mock_settings)
    assert isinstance(tm_conn, ThoughtMachineConnector)

    mock_settings.bank_c_connector_type = "kafka_streaming"
    mock_settings.bank_c_auth_type = "none"
    mock_settings.bank_c_api_key = "test-key"
    mock_settings.kafka_bootstrap_servers = "localhost:9092"
    mock_settings.kafka_security_protocol = "PLAINTEXT"
    mock_settings.kafka_sasl_mechanism = "SCRAM-SHA-256"

    kafka_conn = BankConnectorFactory.get_connector("bank-c", mock_settings)
    assert isinstance(kafka_conn, KafkaStreamingConnector)


# ── 5. Kafka Deduplication & Concurrency Race Protection (DATA-0006) ─────────


@pytest.mark.asyncio
async def test_kafka_streaming_deduplication_and_concurrency_race() -> None:
    """Verify that concurrent duplicate publishes cannot bypass idempotency."""
    broker = InMemoryKafkaBroker()
    connector = KafkaStreamingConnector(in_memory_broker=broker, enable_idempotency=True)

    event = CloudEvent(
        source="urn:cfi:bank:ALPHA",
        type="org.cfi.finint.transaction.v1",
        data={"tx_id": "tx-12345", "amount": 100.0},
        idempotency_key="idemp-key-shared-01",
        tenant_id="bank_alpha",
    )

    # Concurrently publish the exact same event 5 times
    tasks = [connector.publish(event) for _ in range(5)]
    receipts = await asyncio.gather(*tasks)

    committed = [r for r in receipts if r.status == "COMMITTED"]
    duplicates = [r for r in receipts if r.status == "DUPLICATE_IGNORED"]

    assert len(committed) == 1, "Exactly one event should have been committed to Kafka"
    assert len(duplicates) == 4, "Concurrent duplicate deliveries should be ignored"


@pytest.mark.asyncio
async def test_kafka_streaming_tenant_scoped_idempotency() -> None:
    """Verify identical idempotency keys from different banks do not block each other."""
    broker = InMemoryKafkaBroker()
    connector = KafkaStreamingConnector(in_memory_broker=broker, enable_idempotency=True)

    event_alpha = CloudEvent(
        source="urn:cfi:bank:ALPHA",
        type="org.cfi.finint.transaction.v1",
        data={"tx_id": "alpha_01"},
        idempotency_key="shared_idem_id",
        tenant_id="bank_alpha",
    )

    event_beta = CloudEvent(
        source="urn:cfi:bank:BETA",
        type="org.cfi.finint.transaction.v1",
        data={"tx_id": "beta_01"},
        idempotency_key="shared_idem_id",
        tenant_id="bank_beta",
    )

    r_alpha = await connector.publish(event_alpha)
    r_beta = await connector.publish(event_beta)

    assert r_alpha.status == "COMMITTED"
    assert r_beta.status == "COMMITTED", "Bank Beta was incorrectly deduplicated against Bank Alpha!"


# ── 6. Graph Direction & Canonical Account Ingestion (DATA-0007 / DATA-INV-04) ─


def test_streaming_graph_service_preserves_direction_from_normalized_transaction() -> None:
    """Verify asymmetric transaction A -> B is ingested with correct edge direction."""
    graph_svc = StreamingGraphService(max_window_minutes=60)

    norm_tx = NormalizedTransaction(
        transaction_id="tx_graph_01",
        account_id="DEBTOR_ACCOUNT_ALICE",
        counterparty_account_id="CREDITOR_ACCOUNT_BOB",
        amount=500.0,
        currency="EUR",
        timestamp=datetime(2026, 3, 20, 10, 0, 0, tzinfo=UTC),
    )

    graph_svc.add_transaction(norm_tx.model_dump())

    # Check nodes were registered
    assert "DEBTOR_ACCOUNT_ALICE" in graph_svc.nodes
    assert "CREDITOR_ACCOUNT_BOB" in graph_svc.nodes

    # Check edges
    assert len(graph_svc.edges) == 1
    edge = graph_svc.edges[0]
    assert edge["from_id"] == "DEBTOR_ACCOUNT_ALICE"
    assert edge["to_id"] == "CREDITOR_ACCOUNT_BOB"
    assert edge["amount"] == 500.0
    assert edge["from_id"] != edge["to_id"]


# ── 7. Feature Store Deduplication & Double-Counting Immunity (DATA-0008) ─────


def test_feature_store_deduplicates_duplicate_deliveries() -> None:
    """Verify that retrying/redelivering the same transaction ID does not double-count velocity."""
    fs = FeatureStoreService()
    fs.settings.feature_store_enabled = True

    cust_id = "test_customer_idem_01"
    fs.tx_history.delete(cust_id)
    fs.online_stats.delete(cust_id)

    now_ts = datetime(2026, 3, 20, 12, 0, 0, tzinfo=UTC).timestamp()

    # First delivery
    fs.ingest_transaction(
        customer_id=cust_id,
        amount=250.0,
        merchant_id="merch_grocery",
        merchant_category="grocery",
        merchant_risk_score=0.05,
        customer_history_score=0.95,
        chargeback_count=0,
        account_age_days=365,
        timestamp=now_ts,
        transaction_id="tx_unique_001",
    )

    stats1 = fs.online_stats.get(cust_id)
    assert stats1 is not None
    assert stats1["rolling_velocity_1h"] == 1.0
    assert stats1["avg_amount_24h"] == 250.0

    # Duplicate redelivery with the SAME transaction_id
    fs.ingest_transaction(
        customer_id=cust_id,
        amount=250.0,
        merchant_id="merch_grocery",
        merchant_category="grocery",
        merchant_risk_score=0.05,
        customer_history_score=0.95,
        chargeback_count=0,
        account_age_days=365,
        timestamp=now_ts + 10.0,
        transaction_id="tx_unique_001",
    )

    stats2 = fs.online_stats.get(cust_id)
    assert stats2 is not None
    assert stats2["rolling_velocity_1h"] == 1.0, "Duplicate transaction inflated velocity!"
    assert stats2["avg_amount_24h"] == 250.0, "Duplicate transaction distorted avg amount!"

    # Clean up
    fs.tx_history.delete(cust_id)
    fs.online_stats.delete(cust_id)


# ── 8. Serialization Round-Trip Metamorphic Verification (DATA-INV-11) ────────


def test_cloudevent_serialization_round_trip() -> None:
    """Verify CloudEvent encode/decode round trip preserves all semantics identically."""
    event = CloudEvent(
        id="ce-meta-001",
        source="urn:cfi:bank:ALPHA",
        type="org.cfi.finint.transaction.v1",
        time=datetime(2026, 3, 20, 15, 0, 0, tzinfo=UTC),
        datacontenttype="application/json",
        subject="payment-instruction",
        data={
            "transaction_id": "tx_meta_100",
            "amount": "1250.50",
            "currency": "EUR",
        },
        bank_id="BANK_A",
        tenant_id="TENANT_ALPHA",
        idempotency_key="IDEM_KEY_123",
        signature="hmac_sig_val",
    )

    raw_bytes = event.to_json_bytes()
    decoded = CloudEvent.from_raw_json(raw_bytes)

    assert decoded.id == event.id
    assert decoded.source == event.source
    assert decoded.type == event.type
    assert decoded.time == event.time
    assert decoded.data["transaction_id"] == "tx_meta_100"
    assert decoded.ce_bank_id == "BANK_A"
    assert decoded.ce_tenant_id == "TENANT_ALPHA"
    assert decoded.ce_idempotency_key == "IDEM_KEY_123"
    assert decoded.ce_signature == "hmac_sig_val"
