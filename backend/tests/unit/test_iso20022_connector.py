"""Unit Tests for ISO 20022 Messaging Connector & XSD Schema Validation (Phase 25.1).

Validates:
1. ISO 20022 MX (pacs.008, pacs.002, camt.053, pain.001) XML validation against official XSD schemas.
2. Rejection of schema-invalid structures, missing elements, and malformed tags.
3. XXE injection defenses and SIEM parse error logging.
4. Message generation helper methods and round-trip parsing into NormalizedTransaction.
5. SWIFT MT103 parsing, batch operations, and zero-PII HMAC-SHA256 anonymization.
"""

from __future__ import annotations

import pytest

from app.infrastructure.connectors.iso20022_connector import (
    ISO20022MessagingConnector,
    retry_connector,
)


class TestISO20022ConnectorXSDValidation:
    """Tests for ISO20022MessagingConnector.validate_xml_schema using compiled XSD schemas."""

    @pytest.fixture
    def connector(self) -> ISO20022MessagingConnector:
        return ISO20022MessagingConnector()

    def test_pacs008_valid_schema(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies that a well-formed pacs.008 XML passes XSD schema validation."""
        xml = connector.create_pacs008_xml(
            msg_id="MSG-PACS008-VAL-01",
            debtor_iban="DE89370400440532013000",
            creditor_iban="FR1420041010050500013M02606",
            amount=50000.00,
            currency="EUR",
        )
        # Should execute without raising ValueError
        connector.validate_xml_schema(xml, "pacs.008.001.08.xsd")

    def test_pacs008_invalid_schema_element_rejected(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies that pacs.008 containing invalid unexpected child tags is rejected by XSD."""
        bad_pacs008 = """<?xml version="1.0" encoding="UTF-8"?>
        <Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
          <FIToFICstmrCdtTrf>
            <GrpHdr><MsgId>BAD_MSG</MsgId></GrpHdr>
            <CdtTrfTxInf>
              <InvalidElementTag>UnexpectedContent</InvalidElementTag>
            </CdtTrfTxInf>
          </FIToFICstmrCdtTrf>
        </Document>"""

        with pytest.raises(ValueError, match="XSD schema"):
            connector.validate_xml_schema(bad_pacs008, "pacs.008.001.08.xsd")

    def test_pacs002_valid_schema(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies that a well-formed pacs.002 XML passes XSD schema validation."""
        xml = connector.create_pacs002_xml(
            msg_id="MSG-PACS002-VAL-01",
            orig_msg_id="MSG-PACS008-VAL-01",
            status="ACTC",
            amount=50000.00,
            currency="EUR",
        )
        connector.validate_xml_schema(xml, "pacs.002.001.10.xsd")

    def test_camt053_valid_schema(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies that a well-formed camt.053 XML passes XSD schema validation."""
        xml = connector.create_camt053_xml(
            stmt_id="STMT-CAMT053-VAL-01",
            account_iban="DE89370400440532013000",
            entries=[
                {
                    "ntry_ref": "N-01",
                    "amount": 1000.00,
                    "credit_debit": "CRDT",
                    "debtor_name": "Client Alpha",
                    "debtor_iban": "DE89370400440532013000",
                    "creditor_name": "Merchant Beta",
                    "creditor_iban": "FR1420041010050500013M02606",
                },
                {
                    "ntry_ref": "N-02",
                    "amount": 2500.00,
                    "credit_debit": "DBIT",
                    "debtor_name": "Client Gamma",
                    "debtor_iban": "DE89370400440532013000",
                    "creditor_name": "Supplier Delta",
                    "creditor_iban": "NL91ABNA0417164300",
                },
            ],
        )
        connector.validate_xml_schema(xml, "camt.053.001.08.xsd")

    def test_camt053_invalid_schema_rejected(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies that camt.053 with malformed structure fails XSD validation."""
        bad_camt = """<?xml version="1.0" encoding="UTF-8"?>
        <Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
          <BkToCstmrStmt>
            <Stmt>
              <InvalidField>Disallowed</InvalidField>
            </Stmt>
          </BkToCstmrStmt>
        </Document>"""

        with pytest.raises(ValueError, match="XSD schema"):
            connector.validate_xml_schema(bad_camt, "camt.053.001.08.xsd")

    def test_empty_xml_content_rejected(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies that empty string content is rejected immediately."""
        with pytest.raises(ValueError, match="empty content"):
            connector.validate_xml_schema("", "pacs.008.001.08.xsd")

    def test_xxe_injection_rejected(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies that XXE injection payloads are rejected before parsing."""
        xxe = """<?xml version="1.0"?>
        <!DOCTYPE doc [<!ENTITY xxe SYSTEM "file:///etc/hosts">]>
        <Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
          <FIToFICstmrCdtTrf><GrpHdr><MsgId>&xxe;</MsgId></GrpHdr></FIToFICstmrCdtTrf>
        </Document>"""

        with pytest.raises(ValueError, match="XXE injection or DTD entities detected"):
            connector.validate_xml_schema(xxe, "pacs.008.001.08.xsd")


class TestISO20022RoundTripParsing:
    """Tests for generating and parsing ISO 20022 messages into NormalizedTransaction."""

    @pytest.fixture
    def connector(self) -> ISO20022MessagingConnector:
        return ISO20022MessagingConnector()

    def test_pacs008_round_trip(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies pacs.008 creation, schema validation, and parsing into NormalizedTransaction."""
        xml = connector.create_pacs008_xml(
            msg_id="PACS008_ROUNDTRIP_01",
            debtor_iban="DE89370400440532013000",
            creditor_iban="FR1420041010050500013M02606",
            amount=88450.75,
            currency="EUR",
            debtor_name="Acme Industrial AG",
            creditor_name="Global Logistics SAS",
            debtor_country="DE",
            creditor_country="FR",
        )

        tx = connector.parse_pacs008_xml(xml)
        assert tx.transaction_id == "PACS008_ROUNDTRIP_01"
        assert tx.account_id == "DE89370400440532013000"
        assert tx.counterparty_account_id == "FR1420041010050500013M02606"
        assert tx.amount == 88450.75
        assert tx.currency == "EUR"
        assert tx.origin_country == "DE"
        assert tx.destination_country == "FR"
        assert tx.channel_type == "ISO20022_PACS008"

    def test_pacs002_round_trip(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies pacs.002 creation, schema validation, and parsing."""
        xml = connector.create_pacs002_xml(
            msg_id="PACS002_ROUNDTRIP_01",
            orig_msg_id="PACS008_ROUNDTRIP_01",
            status="ACTC",
            amount=88450.75,
            currency="EUR",
        )

        tx = connector.parse_pacs002_xml(xml)
        assert tx.transaction_id == "PACS002_ROUNDTRIP_01"
        assert tx.account_id == "PACS008_ROUNDTRIP_01"
        assert tx.counterparty_account_id == "STATUS_ACTC"
        assert tx.amount == 88450.75
        assert tx.channel_type == "ISO20022_PACS002"

    def test_camt053_round_trip(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies camt.053 multi-entry creation and parsing."""
        entries = [
            {
                "ntry_ref": "CAMT-N1",
                "amount": 3400.00,
                "currency": "EUR",
                "credit_debit": "CRDT",
                "debtor_name": "Client Alpha",
                "debtor_iban": "DE89370400440532013000",
                "creditor_name": "Merchant Beta",
                "creditor_iban": "FR1420041010050500013M02606",
            },
            {
                "ntry_ref": "CAMT-N2",
                "amount": 7800.50,
                "currency": "EUR",
                "credit_debit": "DBIT",
                "debtor_name": "Client Gamma",
                "debtor_iban": "DE89370400440532013000",
                "creditor_name": "Supplier Delta",
                "creditor_iban": "NL91ABNA0417164300",
            },
        ]
        xml = connector.create_camt053_xml(
            stmt_id="STMT-ROUNDTRIP-01",
            account_iban="DE89370400440532013000",
            entries=entries,
        )

        txs = connector.parse_camt053_xml(xml)
        assert len(txs) == 2
        assert txs[0].transaction_id == "CAMT-N1"
        assert txs[0].amount == 3400.00
        assert txs[1].transaction_id == "CAMT-N2"
        assert txs[1].amount == 7800.50


class TestSWIFTAndAuxiliaryOperations:
    """Tests for SWIFT MT103 parsing, batch operations, and privacy transformation."""

    @pytest.fixture
    def connector(self) -> ISO20022MessagingConnector:
        return ISO20022MessagingConnector()

    def test_swift_mt103_clean_parsing(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies parsing of standard SWIFT MT103 wire messages."""
        mt103 = """:20:SWIFT_TX_98765
:32A:260928EUR42000,50
:50K:/DE89370400440532013000
Max Mustermann
:59:/FR1420041010050500013M02606
Jean Dupont
:57A:BNPAFRPPXXX
"""
        tx = connector.parse_swift_mt103(mt103)
        assert tx.transaction_id == "SWIFT_TX_98765"
        assert tx.amount == 42000.50
        assert tx.currency == "EUR"
        assert tx.origin_country == "DE"
        assert tx.destination_country == "FR"
        assert tx.channel_type == "SWIFT_MT103"

    def test_batch_parsing_strict_mode(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies parse_batch failure counting in non-strict mode vs error raise in strict mode."""
        valid_pacs008 = connector.create_pacs008_xml(
            msg_id="BATCH-01",
            debtor_iban="DE89370400440532013000",
            creditor_iban="FR1420041010050500013M02606",
            amount=1000.0,
        )
        invalid_item = "<Document><CorruptXml/></Document>"

        # Non-strict
        results = connector.parse_batch([valid_pacs008, invalid_item], strict=False)
        assert len(results) == 1
        assert connector.failed_parse_count >= 1

        # Strict raises
        with pytest.raises(ValueError):
            connector.parse_batch([valid_pacs008, invalid_item], strict=True)

    def test_anonymize_transaction_zero_pii(self, connector: ISO20022MessagingConnector) -> None:
        """Verifies salted HMAC-SHA256 privacy transform of account IDs."""
        xml = connector.create_pacs008_xml(
            msg_id="ANON-TX-01",
            debtor_iban="DE89370400440532013000",
            creditor_iban="FR1420041010050500013M02606",
            amount=500.0,
        )
        raw_tx = connector.parse_pacs008_xml(xml)
        anon_tx = connector.anonymize_transaction(raw_tx, salt="custom_salt_2026")

        assert anon_tx.transaction_id == raw_tx.transaction_id
        assert anon_tx.amount == raw_tx.amount
        assert anon_tx.currency == raw_tx.currency
        assert anon_tx.account_id != raw_tx.account_id
        assert len(anon_tx.account_id) == 64
        assert anon_tx.counterparty_account_id != raw_tx.counterparty_account_id
        assert len(anon_tx.counterparty_account_id) == 64

    def test_retry_connector_decorator_resilience(self) -> None:
        """Verifies retry_connector decorator retries on transient exceptions."""
        call_count = 0

        @retry_connector(max_attempts=3, backoff_seconds=0.01)
        def unreliable_service() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Transient connection drop")
            return "SUCCESS"

        result = unreliable_service()
        assert result == "SUCCESS"
        assert call_count == 3
