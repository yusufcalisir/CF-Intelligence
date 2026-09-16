"""Hardening Unit Tests for Financial Message Standard Parsers & ISO 20022 Connector.

Validates zero-mock dynamic values, ISO 13616 IBAN & ISO 9362 BIC format checks,
salted HMAC-SHA256 zero-PII privacy transforms, strict error handling, and SIEM auditing.
"""

from __future__ import annotations

import pytest

from app.application.services.financial_message_parser import FinancialMessageParser
from app.infrastructure.connectors.iso20022_connector import ISO20022MessagingConnector

# ── Sample Data Fixtures ─────────────────────────────────────────────────────

SAMPLE_PACS008_VALID = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
    <FIToFICstmrCdtTrf>
        <GrpHdr>
            <MsgId>PACS008_HARDENED_001</MsgId>
        </GrpHdr>
        <CdtTrfTxInf>
            <PmtId><EndToEndId>E2E_123456</EndToEndId></PmtId>
            <IntrBkSttlmAmt Ccy="EUR">78500.25</IntrBkSttlmAmt>
            <DbtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></DbtrAcct>
            <CdtrAcct><Id><IBAN>FR7630006000011234567890189</IBAN></Id></CdtrAcct>
            <Dbtr><PstlAdr><Ctry>DE</Ctry></PstlAdr></Dbtr>
            <Cdtr><PstlAdr><Ctry>FR</Ctry></PstlAdr></Cdtr>
        </CdtTrfTxInf>
    </FIToFICstmrCdtTrf>
</Document>"""

SAMPLE_PAIN001_VALID = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.08">
    <CstmrCdtTrfInitn>
        <GrpHdr>
            <MsgId>PAIN001_HARDENED_002</MsgId>
        </GrpHdr>
        <PmtInf>
            <Dbtr><Nm>Corporate Sender Inc</Nm><PstlAdr><Ctry>GB</Ctry></PstlAdr></Dbtr>
            <DbtrAcct><Id><IBAN>GB29NWBK60161331926819</IBAN></Id></DbtrAcct>
            <CdtTrfTxInf>
                <Amt><InstdAmt Ccy="GBP">12450.00</InstdAmt></Amt>
                <Cdtr><Nm>European Vendor B.V.</Nm><PstlAdr><Ctry>NL</Ctry></PstlAdr></Cdtr>
                <CdtrAcct><Id><IBAN>NL91ABNA0417164300</IBAN></Id></CdtrAcct>
                <RmtInf><Ustrd>INVOICE-88991</Ustrd></RmtInf>
            </CdtTrfTxInf>
        </PmtInf>
    </CstmrCdtTrfInitn>
</Document>"""

SAMPLE_CAMT053_VALID = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
    <BkToCstmrStmt>
        <Stmt>
            <Acct><Id><IBAN>DE89370400440532013000</IBAN></Id></Acct>
            <Ntry>
                <NtryRef>CAMT_ENTRY_001</NtryRef>
                <Amt Ccy="EUR">3200.00</Amt>
                <NtryDtls><TxDtls><RltdPties><Cdtr><Nm>Supplier Alpha</Nm></Cdtr></RltdPties></TxDtls></NtryDtls>
            </Ntry>
            <Ntry>
                <NtryRef>CAMT_ENTRY_002</NtryRef>
                <Amt Ccy="EUR">5400.00</Amt>
                <NtryDtls><TxDtls><RltdPties><Dbtr><Nm>Client Beta</Nm></Dbtr></RltdPties></TxDtls></NtryDtls>
            </Ntry>
        </Stmt>
    </BkToCstmrStmt>
</Document>"""


# ── Tests ────────────────────────────────────────────────────────────────────

def test_iban_validation_iso13616() -> None:
    """Verifies ISO 13616 Mod-97 checksum validation on valid and malformed IBANs."""
    # Valid IBANs
    assert FinancialMessageParser.validate_iban("DE89 3704 0044 0532 0130 00") is True
    assert FinancialMessageParser.validate_iban("FR76 3000 6000 0112 3456 7890 189") is True
    assert FinancialMessageParser.validate_iban("GB29 NWBK 6016 1331 9268 19") is True

    # Invalid check digits
    assert FinancialMessageParser.validate_iban("DE89 3704 0044 0532 0130 99") is False
    assert FinancialMessageParser.validate_iban("FR76 3000 6000 0112 3456 7890 000") is False

    # Invalid lengths or formats
    assert FinancialMessageParser.validate_iban("DE89") is False
    assert FinancialMessageParser.validate_iban("") is False
    assert FinancialMessageParser.validate_iban(None) is False  # type: ignore[arg-type]
    assert FinancialMessageParser.validate_iban("INVALID_CHARS_@@@@") is False


def test_bic_validation_iso9362() -> None:
    """Verifies ISO 9362 BIC/SWIFT code regex validation."""
    assert FinancialMessageParser.validate_bic("ALICUS33XXX") is True
    assert FinancialMessageParser.validate_bic("DEUTDEDDXXX") is True
    assert FinancialMessageParser.validate_bic("BNPAFRPP") is True  # 8-char BIC

    # Invalid formats
    assert FinancialMessageParser.validate_bic("SHORT") is False
    assert FinancialMessageParser.validate_bic("TOOLONGBICCODE123") is False
    assert FinancialMessageParser.validate_bic("12345678") is False  # Bank code must be letters
    assert FinancialMessageParser.validate_bic("") is False


def test_dynamic_country_extraction() -> None:
    """Verifies dynamic country extraction from postal addresses and account numbers."""
    assert FinancialMessageParser.extract_country_code("DE8937040044", "DE") == "DE"
    assert FinancialMessageParser.extract_country_code("FR7630006000", None) == "FR"
    assert FinancialMessageParser.extract_country_code("/US123456", None) == "US"
    assert FinancialMessageParser.extract_country_code("ACC_99812", None) == "AC"
    assert FinancialMessageParser.extract_country_code("", None) == "XX"
    assert FinancialMessageParser.extract_country_code(None, None) == "XX"


def test_zero_pii_privacy_features_transformation() -> None:
    """Verifies transformation of parsed financial message into zero-PII privacy features."""
    parsed = {
        "message_type": "ISO20022_PACS008",
        "transaction_id": "TX_99182",
        "amount": 15000.0,
        "currency": "EUR",
        "date": "2026-09-16",
        "sender_name": "Alice Confidential",
        "sender_account": "DE89370400440532013000",
        "sender_bic": "DBANKDEDDXXX",
        "sender_country": "DE",
        "receiver_name": "Bob Secret",
        "receiver_account": "FR7630006000011234567890189",
        "receiver_bic": "BNPAFRPPXXX",
        "receiver_country": "FR",
        "remittance_info": "CONFIDENTIAL SETTLEMENT",
    }

    salt = "custom_audit_salt_2026"
    features = FinancialMessageParser.to_privacy_preserving_features(parsed, salt=salt)

    assert "sender_name" not in features
    assert "receiver_name" not in features
    assert features["sender_account_hash"] != "DE89370400440532013000"
    assert len(features["sender_account_hash"]) == 64  # SHA-256 hex length
    assert len(features["receiver_account_hash"]) == 64
    assert features["amount"] == 15000.0
    assert features["currency"] == "EUR"
    assert features["remittance_info"] == "[PROTECTED_PII]"


def test_iso20022_pacs008_zero_mock_amount_and_account_enforcement() -> None:
    """Verifies rejection of pacs.008 XML with missing/negative amounts or missing accounts."""
    connector = ISO20022MessagingConnector()

    # Missing amount
    bad_xml_amount = """<?xml version="1.0" encoding="UTF-8"?>
    <Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
        <FIToFICstmrCdtTrf>
            <CdtTrfTxInf>
                <DbtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></DbtrAcct>
                <CdtrAcct><Id><IBAN>FR7630006000011234567890189</IBAN></Id></CdtrAcct>
            </CdtTrfTxInf>
        </FIToFICstmrCdtTrf>
    </Document>"""
    with pytest.raises(ValueError, match="missing IntrBkSttlmAmt settlement amount"):
        connector.parse_pacs008_xml(bad_xml_amount)

    # Negative amount
    bad_xml_negative = """<?xml version="1.0" encoding="UTF-8"?>
    <Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
        <FIToFICstmrCdtTrf>
            <CdtTrfTxInf>
                <IntrBkSttlmAmt Ccy="EUR">-500.00</IntrBkSttlmAmt>
                <DbtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></DbtrAcct>
                <CdtrAcct><Id><IBAN>FR7630006000011234567890189</IBAN></Id></CdtrAcct>
            </CdtTrfTxInf>
        </FIToFICstmrCdtTrf>
    </Document>"""
    with pytest.raises(ValueError, match="invalid settlement amount"):
        connector.parse_pacs008_xml(bad_xml_negative)

    # Missing debtor account
    bad_xml_debtor = """<?xml version="1.0" encoding="UTF-8"?>
    <Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
        <FIToFICstmrCdtTrf>
            <CdtTrfTxInf>
                <IntrBkSttlmAmt Ccy="EUR">1200.00</IntrBkSttlmAmt>
                <CdtrAcct><Id><IBAN>FR7630006000011234567890189</IBAN></Id></CdtrAcct>
            </CdtTrfTxInf>
        </FIToFICstmrCdtTrf>
    </Document>"""
    with pytest.raises(ValueError, match="missing debtor account"):
        connector.parse_pacs008_xml(bad_xml_debtor)


def test_iso20022_pain001_credit_transfer_initiation_parsing() -> None:
    """Verifies pain.001 XML parsing with dynamic country and amount resolution."""
    connector = ISO20022MessagingConnector()
    tx = connector.parse_pain001_xml(SAMPLE_PAIN001_VALID)

    assert tx.transaction_id == "PAIN001_HARDENED_002"
    assert tx.amount == 12450.00
    assert tx.currency == "GBP"
    assert tx.account_id == "GB29NWBK60161331926819"
    assert tx.counterparty_account_id == "NL91ABNA0417164300"
    assert tx.origin_country == "GB"
    assert tx.destination_country == "NL"
    assert tx.channel_type == "ISO20022_PAIN001"


def test_swift_mt103_zero_mock_enforcement() -> None:
    """Verifies SWIFT MT103 parser enforces mandatory fields and rejects missing/zero amounts."""
    connector = ISO20022MessagingConnector()

    # Valid MT103 with BIC destination country
    valid_mt103 = """:20:SWIFT_TX_887711
:32A:260916CHF92000,50
:50K:/CH9300000000000000000
Swiss Corp AG
:59:/IT60X0542811101000000123456
Italian Beneficiary SpA
:57A:BCITITMMXXX
"""
    tx = connector.parse_swift_mt103(valid_mt103)
    assert tx.transaction_id == "SWIFT_TX_887711"
    assert tx.amount == 92000.50
    assert tx.currency == "CHF"
    assert tx.origin_country == "CH"
    assert tx.destination_country == "IT"

    # Missing mandatory :32A:
    bad_mt103 = """:20:SWIFT_FAIL
:50K:/CH9300000000000000000
:59:/IT60X0542811101000000123456
"""
    with pytest.raises(ValueError, match="missing or invalid mandatory field :32A: amount"):
        connector.parse_swift_mt103(bad_mt103)


def test_camt053_multi_entry_zero_mock_enforcement() -> None:
    """Verifies camt.053 XML statement parsing with multi-entry dynamic normalization."""
    connector = ISO20022MessagingConnector()
    txs = connector.parse_camt053_xml(SAMPLE_CAMT053_VALID)

    assert len(txs) == 2
    assert txs[0].transaction_id == "CAMT_ENTRY_001"
    assert txs[0].amount == 3200.00
    assert txs[0].origin_country == "DE"
    assert txs[0].account_id == "DE89370400440532013000"
    assert txs[0].counterparty_account_id == "Supplier Alpha"

    assert txs[1].transaction_id == "CAMT_ENTRY_002"
    assert txs[1].amount == 5400.00
    assert txs[1].origin_country == "DE"
    assert txs[1].account_id == "DE89370400440532013000"
    assert txs[1].counterparty_account_id == "Client Beta"


def test_pacs002_payment_status_report_normalization() -> None:
    """Verifies pacs.002 XML status report parsing satisfies NormalizedTransaction schema."""
    connector = ISO20022MessagingConnector()

    pacs002_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.002.001.10">
        <FIToFIPmtStsRpt>
            <GrpHdr><MsgId>PACS002_STATUS_99</MsgId></GrpHdr>
            <TxInfAndSts>
                <OrgnlEndToEndId>ORIG_TX_123</OrgnlEndToEndId>
                <TxSts>RJCT</TxSts>
                <OrgnlTxRef><Amt Ccy="EUR">4500.00</Amt></OrgnlTxRef>
            </TxInfAndSts>
        </FIToFIPmtStsRpt>
    </Document>"""

    tx = connector.parse_pacs002_xml(pacs002_xml)
    assert tx.transaction_id == "PACS002_STATUS_99"
    assert tx.amount == 4500.00
    assert tx.counterparty_account_id == "STATUS_RJCT"
    assert tx.channel_type == "ISO20022_PACS002"


def test_iso20022_connector_anonymize_transaction() -> None:
    """Verifies connector anonymize_transaction outputs zero raw PII NormalizedTransaction."""
    connector = ISO20022MessagingConnector()
    tx = connector.parse_pacs008_xml(SAMPLE_PACS008_VALID)

    anonymized = connector.anonymize_transaction(tx, salt="secagg_salt_99")

    assert anonymized.transaction_id == tx.transaction_id
    assert anonymized.amount == tx.amount
    assert anonymized.account_id != tx.account_id
    assert len(anonymized.account_id) == 64  # HMAC-SHA256 hex string
    assert anonymized.counterparty_account_id != tx.counterparty_account_id
    assert len(anonymized.counterparty_account_id) == 64


def test_batch_parsing_with_error_tracking_and_strict_mode() -> None:
    """Verifies parse_batch failure counting and strict mode exception raising."""
    connector = ISO20022MessagingConnector()

    batch_payload = [
        SAMPLE_PACS008_VALID,
        "INVALID_MESSAGE_STRING",
        SAMPLE_PAIN001_VALID,
    ]

    # Non-strict mode: parses valid items and records failure count
    parsed = connector.parse_batch(batch_payload, strict=False)
    assert len(parsed) == 2
    assert connector.failed_parse_count == 1

    # Strict mode: re-raises exception on bad item
    with pytest.raises(ValueError):
        connector.parse_batch(batch_payload, strict=True)


def test_xxe_and_malformed_xml_rejection() -> None:
    """Verifies rejection of XXE injection and malformed XML schemas with SIEM logging."""
    connector = ISO20022MessagingConnector()

    xxe_attack = """<?xml version="1.0"?>
    <!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/shadow">]>
    <Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
        <FIToFICstmrCdtTrf><GrpHdr><MsgId>&xxe;</MsgId></GrpHdr></FIToFICstmrCdtTrf>
    </Document>"""

    with pytest.raises(ValueError, match="XXE injection or DTD entities detected"):
        connector.validate_xml_schema(xxe_attack, "pacs.008.001.08.xsd")

    with pytest.raises(ValueError, match="empty content"):
        connector.validate_xml_schema("   ", "pacs.008.001.08.xsd")
