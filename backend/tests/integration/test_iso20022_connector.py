"""Integration tests for Section 38.2: ISO 20022 Connector Hardening."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.infrastructure.connectors.iso20022_connector import ISO20022MessagingConnector


def test_valid_pacs008_parsed_successfully() -> None:
    """Verifies that valid pacs.008 XML passes XSD validation and maps to NormalizedTransaction."""
    fixture_path = Path("backend/tests/fixtures/iso20022_sample_transactions.xml")
    if not fixture_path.exists():
        fixture_path = Path("tests/fixtures/iso20022_sample_transactions.xml")

    xml_text = fixture_path.read_text(encoding="utf-8")
    connector = ISO20022MessagingConnector()

    txs = connector.parse_camt053_xml(xml_text)

    assert len(txs) == 50
    assert txs[0].transaction_id == "TX_ISO_0001"
    assert txs[0].amount > 0
    assert txs[0].currency == "USD"


def test_invalid_xml_fails_xsd_validation() -> None:
    """Verifies that malformed XML fails XSD schema validation and raises ValueError."""
    malformed_xml = "<?xml version='1.0'?><InvalidRoot><Data>Bad</Data></InvalidRoot>"
    connector = ISO20022MessagingConnector()

    with pytest.raises(ValueError, match="ISO 20022 XML validation failed"):
        connector.parse_pacs008_xml(malformed_xml)


def test_parse_failure_logged_to_siem(caplog) -> None:
    """Verifies that XML/SWIFT parse failures log an ISO20022_PARSE_FAILURE event to SIEM."""
    connector = ISO20022MessagingConnector()

    with pytest.raises(ValueError):
        connector.parse_pacs008_xml("<<<CORRUPT_XML_PAYLOAD>>>")

    assert "ISO20022_PARSE_FAILURE" in caplog.text or "ISO 20022 parse failure" in caplog.text


def test_pain001_parsed_successfully() -> None:
    """Verifies that pain.001 credit transfer initiation XML is parsed into NormalizedTransaction."""
    pain001_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.08">
  <CstmrCdtTrfInitn>
    <GrpHdr>
      <MsgId>PAIN_INIT_001</MsgId>
    </GrpHdr>
    <PmtInf>
      <CdtTrfTxInf>
        <Amt><InstdAmt Ccy="EUR">750.50</InstdAmt></Amt>
        <DbtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></DbtrAcct>
        <CdtrAcct><Id><IBAN>FR7630006000011234567890123</IBAN></Id></CdtrAcct>
      </CdtTrfTxInf>
    </PmtInf>
  </CstmrCdtTrfInitn>
</Document>"""

    connector = ISO20022MessagingConnector()
    tx = connector.parse_pain001_xml(pain001_xml)

    assert tx.transaction_id == "PAIN_INIT_001"
    assert tx.amount == 750.50
    assert tx.currency == "EUR"
    assert tx.channel_type == "ISO20022_PAIN001"
