"""Unit tests for enterprise bank connectors and data schemas."""

from datetime import datetime

from app.config import Settings
from app.infrastructure.connectors.base_connector import NormalizedTransaction
from app.infrastructure.connectors.batch_connector import BatchEODFileConnector
from app.infrastructure.connectors.factory import BankConnectorFactory
from app.infrastructure.connectors.iso20022_connector import ISO20022MessagingConnector
from app.infrastructure.connectors.rest_connector import RESTBankConnector
from app.infrastructure.connectors.streaming_connector import StreamingPaymentConnector


def test_normalized_transaction_schema():
    """Test NormalizedTransaction validation and default attributes."""
    tx = NormalizedTransaction(
        transaction_id="tx_1001",
        account_id="acc_debtor_99",
        counterparty_account_id="acc_creditor_88",
        amount=2500.50,
        currency="EUR",
        origin_country="DE",
        destination_country="FR",
    )
    assert tx.transaction_id == "tx_1001"
    assert tx.amount == 2500.50
    assert tx.currency == "EUR"
    assert tx.channel_type == "ONLINE"
    assert isinstance(tx.timestamp, datetime)


def test_streaming_payment_connector():
    """Test StreamingPaymentConnector event pushing and consumption."""
    connector = StreamingPaymentConnector(topic="payments.test")

    event = {
        "transaction_id": "tx_stream_01",
        "account_id": "acc_001",
        "counterparty_account_id": "acc_002",
        "amount": 750.0,
        "currency": "USD",
        "merchant_category_code": "5411",
    }
    tx = connector.push_raw_event(event)
    assert tx.transaction_id == "tx_stream_01"
    assert tx.amount == 750.0

    streamed = list(connector.consume_stream())
    assert len(streamed) == 1
    assert streamed[0].transaction_id == "tx_stream_01"


def test_iso20022_pacs008_parsing():
    """Test ISO 20022 pacs.008 XML message parsing."""
    connector = ISO20022MessagingConnector()

    xml_sample = """<?xml version="1.0" encoding="UTF-8"?>
    <Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
        <FIToFICstmrCdtTrf>
            <GrpHdr>
                <MsgId>PACS008_MSG_9921</MsgId>
            </GrpHdr>
            <CdtTrfTxInf>
                <IntrBkSttlmAmt Ccy="EUR">15400.00</IntrBkSttlmAmt>
                <DbtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></DbtrAcct>
                <CdtrAcct><Id><IBAN>FR7630006000011234567890189</IBAN></Id></CdtrAcct>
                <Dbtr><PstlAdr><Ctry>DE</Ctry></PstlAdr></Dbtr>
                <Cdtr><PstlAdr><Ctry>FR</Ctry></PstlAdr></Cdtr>
            </CdtTrfTxInf>
        </FIToFICstmrCdtTrf>
    </Document>"""

    tx = connector.parse_pacs008_xml(xml_sample)
    assert tx.transaction_id == "PACS008_MSG_9921"
    assert tx.amount == 15400.00
    assert tx.currency == "EUR"
    assert tx.origin_country == "DE"
    assert tx.destination_country == "FR"
    assert tx.channel_type == "ISO20022_PACS008"


def test_swift_mt103_parsing():
    """Test SWIFT MT103 text message parsing."""
    connector = ISO20022MessagingConnector()

    swift_sample = """:20:SWIFT_REF_88123
:32A:260722USD45000,00
:50K:/ACC_DEBTOR_123
John Doe Bank A
:59:/ACC_CREDITOR_456
Jane Smith Bank B"""

    tx = connector.parse_swift_mt103(swift_sample)
    assert tx.transaction_id == "SWIFT_REF_88123"
    assert tx.amount == 45000.00
    assert tx.currency == "USD"
    assert tx.channel_type == "SWIFT_MT103"


def test_batch_eod_file_connector():
    """Test BatchEODFileConnector CSV batch parsing."""
    connector = BatchEODFileConnector()

    csv_sample = """transaction_id,account_id,counterparty_account_id,amount,currency,merchant_category_code,origin_country,destination_country
tx_batch_1,acc_10,acc_20,120.00,USD,5912,US,CA
tx_batch_2,acc_11,acc_21,890.50,EUR,4814,DE,GB"""

    parsed = connector.parse_csv_stream(csv_sample)
    assert len(parsed) == 2
    assert parsed[0].transaction_id == "tx_batch_1"
    assert parsed[1].amount == 890.50

    streamed = list(connector.consume_stream())
    assert len(streamed) == 2


def test_rest_connector_webhook_ingestion():
    """Test RESTBankConnector webhook batch parsing and stream consumption."""
    connector = RESTBankConnector(base_url="http://localhost:8000")

    payload = [
        {
            "transaction_id": "wh_001",
            "account_id": "a1",
            "counterparty_account_id": "a2",
            "amount": 300.0,
        },
        {
            "transaction_id": "wh_002",
            "account_id": "b1",
            "counterparty_account_id": "b2",
            "amount": 600.0,
        },
    ]
    parsed = connector.parse_batch(payload)
    assert len(parsed) == 2
    assert parsed[0].transaction_id == "wh_001"
    assert parsed[0].channel_type == "REST_WEBHOOK"

    streamed = list(connector.consume_stream())
    assert len(streamed) == 2


def test_factory_connector_resolution():
    """Test BankConnectorFactory resolution of streaming, iso20022, and batch types."""
    settings = Settings()
    settings.bank_a_connector_type = "streaming"
    settings.bank_b_connector_type = "iso20022"
    settings.bank_c_connector_type = "batch"

    conn_a = BankConnectorFactory.get_connector("bank-a", settings)
    conn_b = BankConnectorFactory.get_connector("bank-b", settings)
    conn_c = BankConnectorFactory.get_connector("bank-c", settings)

    assert isinstance(conn_a, StreamingPaymentConnector)
    assert isinstance(conn_b, ISO20022MessagingConnector)
    assert isinstance(conn_c, BatchEODFileConnector)


def test_rest_connector_signs_payload_with_hmac():
    """Verify RESTBankConnector generates valid HMAC-SHA256 headers."""
    connector = RESTBankConnector(
        base_url="http://localhost:8000",
        signing_secret="test_secret_key_12345",
    )
    payload = {"bank_id": "bank_a", "step": "init"}
    body_bytes, headers = connector._sign_payload(payload, {"Content-Type": "application/json"})

    assert "X-Payload-Signature" in headers
    assert "X-Payload-Timestamp" in headers
    import hashlib
    import hmac

    expected = hmac.new(
        b"test_secret_key_12345",
        headers["X-Payload-Timestamp"].encode("utf-8") + b"." + body_bytes,
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-Payload-Signature"] == expected


def test_rest_connector_missing_signing_secret_fails_closed():
    """Verify RESTBankConnector refuses outbound dispatch when signing secret is empty."""
    import pytest
    from app.infrastructure.connectors.rest_connector import AuthenticationError

    connector = RESTBankConnector(
        base_url="http://localhost:8000",
        signing_secret="",
    )
    # Also override settings to empty
    connector.settings.payload_signing_secret = ""

    payload = {"bank_id": "bank_a", "step": "init"}
    with pytest.raises(AuthenticationError, match="requires a configured, non-empty payload_signing_secret"):
        connector._sign_payload(payload, {})
