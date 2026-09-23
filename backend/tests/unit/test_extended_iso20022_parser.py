"""Unit & Integration Tests for Extended ISO 20022 Financial Rails.

Covers:
- camt.053.001.08: Bank-to-Customer Statement batch XML with balance snapshots & entries
- pacs.002.001.10: Financial Institutional Payment Status Report with clearing reason codes
- pacs.003.001.08: Customer Direct Debit with unauthorized pull fraud risk assessment
- Heuristic risk scoring engine for direct debit pull anomalies
- Salted HMAC-SHA256 zero-PII privacy transforms
- Strict XXE, DTD expansion, and entity injection defenses
- Presentation router endpoints with FastAPI TestClient
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.application.services.financial_message_parser import (
    FinancialMessageParser,
    FinancialMessageParserError,
)
from app.infrastructure.connectors.iso20022_connector import ISO20022MessagingConnector
from app.main import app

# ── XML Fixtures ─────────────────────────────────────────────────────────────

SAMPLE_CAMT053_FULL = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
    <BkToCstmrStmt>
        <GrpHdr>
            <MsgId>CAMT053_MSG_998811</MsgId>
            <CreDtTm>2026-09-23T12:00:00Z</CreDtTm>
        </GrpHdr>
        <Stmt>
            <Id>STMT-2026-09-DE-001</Id>
            <CreDtTm>2026-09-23T12:00:00Z</CreDtTm>
            <Acct>
                <Id><IBAN>DE89370400440532013000</IBAN></Id>
                <Ccy>EUR</Ccy>
                <Svcr>
                    <FinInstnId><BICFI>DBEUMM21XXX</BICFI></FinInstnId>
                </Svcr>
            </Acct>
            <Bal>
                <Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp>
                <Amt Ccy="EUR">150000.00</Amt>
                <CdtDbtInd>CRDT</CdtDbtInd>
                <Dt><Dt>2026-09-23</Dt></Dt>
            </Bal>
            <Bal>
                <Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp>
                <Amt Ccy="EUR">142500.00</Amt>
                <CdtDbtInd>CRDT</CdtDbtInd>
                <Dt><Dt>2026-09-23</Dt></Dt>
            </Bal>
            <Ntry>
                <NtryRef>ENTRY-001</NtryRef>
                <Amt Ccy="EUR">12500.00</Amt>
                <CdtDbtInd>DBIT</CdtDbtInd>
                <Sts>BOOK</Sts>
                <BookgDt><Dt>2026-09-23</Dt></BookgDt>
                <ValDt><Dt>2026-09-23</Dt></ValDt>
                <NtryDtls>
                    <TxDtls>
                        <Refs><EndToEndId>E2E-CAMT-01</EndToEndId></Refs>
                        <RltdPties>
                            <Cdtr><Nm>Vendor Corp SAS</Nm></Cdtr>
                            <CdtrAcct><Id><IBAN>FR7630006000011234567890189</IBAN></Id></CdtrAcct>
                        </RltdPties>
                        <RmtInf><Ustrd>PAYMENT FOR INVOICE 9912</Ustrd></RmtInf>
                    </TxDtls>
                </NtryDtls>
            </Ntry>
            <Ntry>
                <NtryRef>ENTRY-002</NtryRef>
                <Amt Ccy="EUR">5000.00</Amt>
                <CdtDbtInd>CRDT</CdtDbtInd>
                <Sts>BOOK</Sts>
                <BookgDt><Dt>2026-09-23</Dt></BookgDt>
                <ValDt><Dt>2026-09-23</Dt></ValDt>
                <NtryDtls>
                    <TxDtls>
                        <Refs><EndToEndId>E2E-CAMT-02</EndToEndId></Refs>
                        <RltdPties>
                            <Dbtr><Nm>Client Netherlands BV</Nm></Dbtr>
                            <DbtrAcct><Id><IBAN>NL91ABNA0417164300</IBAN></Id></DbtrAcct>
                        </RltdPties>
                        <RmtInf><Ustrd>CONSULTING SERVICES RETAINER</Ustrd></RmtInf>
                    </TxDtls>
                </NtryDtls>
            </Ntry>
        </Stmt>
    </BkToCstmrStmt>
</Document>"""

SAMPLE_PACS002_REJECTED = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.002.001.10">
    <FIToFIPmtStsRpt>
        <GrpHdr>
            <MsgId>PACS002_MSG_774411</MsgId>
            <CreDtTm>2026-09-23T14:30:00Z</CreDtTm>
        </GrpHdr>
        <OrgnlGrpInfAndSts>
            <OrgnlMsgId>PACS008_ORIG_5522</OrgnlMsgId>
            <OrgnlMsgNmId>pacs.008.001.08</OrgnlMsgNmId>
        </OrgnlGrpInfAndSts>
        <TxInfAndSts>
            <StsId>STS-INFO-991</StsId>
            <OrgnlEndToEndId>E2E-ORIG-9910</OrgnlEndToEndId>
            <OrgnlTxId>TX-ORIG-8821</OrgnlTxId>
            <TxSts>RJCT</TxSts>
            <StsRsnInf>
                <Rsn><Cd>AC04</Cd></Rsn>
                <AddtlInf>Target account has been closed by debtor institution</AddtlInf>
            </StsRsnInf>
            <OrgnlTxRef>
                <Amt Ccy="EUR">45200.00</Amt>
                <Dbtr><Nm>Fictitious Sender AG</Nm></Dbtr>
                <DbtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></DbtrAcct>
                <DbtrAgt><FinInstnId><BICFI>DBEUMM21XXX</BICFI></FinInstnId></DbtrAgt>
                <Cdtr><Nm>Target Mule GmbH</Nm></Cdtr>
                <CdtrAcct><Id><IBAN>FR7630006000011234567890189</IBAN></Id></CdtrAcct>
                <CdtrAgt><FinInstnId><BICFI>BNPAFRPPXXX</BICFI></FinInstnId></CdtrAgt>
            </OrgnlTxRef>
        </TxInfAndSts>
    </FIToFIPmtStsRpt>
</Document>"""

SAMPLE_PACS002_ACCEPTED = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.002.001.10">
    <FIToFIPmtStsRpt>
        <GrpHdr>
            <MsgId>PACS002_ACCP_001</MsgId>
            <CreDtTm>2026-09-23T15:00:00Z</CreDtTm>
        </GrpHdr>
        <TxInfAndSts>
            <OrgnlEndToEndId>E2E-ACCP-8877</OrgnlEndToEndId>
            <TxSts>ACCP</TxSts>
            <OrgnlTxRef>
                <Amt Ccy="EUR">1200.00</Amt>
                <DbtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></DbtrAcct>
                <CdtrAcct><Id><IBAN>FR7630006000011234567890189</IBAN></Id></CdtrAcct>
            </OrgnlTxRef>
        </TxInfAndSts>
    </FIToFIPmtStsRpt>
</Document>"""

SAMPLE_PACS003_LEGITIMATE = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.003.001.08">
    <FIToFICstmrDrctDbt>
        <GrpHdr>
            <MsgId>PACS003_MSG_LEGIT</MsgId>
            <CreDtTm>2026-09-23T09:15:00Z</CreDtTm>
        </GrpHdr>
        <DrctDbtTxInf>
            <PmtId><EndToEndId>E2E-LEGIT-DD-01</EndToEndId></PmtId>
            <PmtTpInf><SeqTp>RCUR</SeqTp></PmtTpInf>
            <IntrBkSttlmAmt Ccy="EUR">129.50</IntrBkSttlmAmt>
            <IntrBkSttlmDt>2026-09-25</IntrBkSttlmDt>
            <DrctDbtTx>
                <MndtRltdInf>
                    <MndtId>MANDATE-ENERGY-DE-991244</MndtId>
                    <DtOfSgntr>2024-01-15</DtOfSgntr>
                </MndtRltdInf>
                <CdtrSchmeId><Id><PrvtId><Othr><Id>DE98ZZZ09999999999</Id></Othr></PrvtId></Id></CdtrSchmeId>
            </DrctDbtTx>
            <Cdtr>
                <Nm>Berlin Power &amp; Gas GmbH</Nm>
                <PstlAdr><Ctry>DE</Ctry></PstlAdr>
            </Cdtr>
            <CdtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></CdtrAcct>
            <CdtrAgt><FinInstnId><BICFI>DBEUMM21XXX</BICFI></FinInstnId></CdtrAgt>
            <Dbtr>
                <Nm>Hans Schmidt</Nm>
                <PstlAdr><Ctry>DE</Ctry></PstlAdr>
            </Dbtr>
            <DbtrAcct><Id><IBAN>DE02100100100123456789</IBAN></Id></DbtrAcct>
            <DbtrAgt><FinInstnId><BICFI>PBNKDEFFXXX</BICFI></FinInstnId></DbtrAgt>
            <RmtInf><Ustrd>MONTHLY ELECTRICITY SEPT 2026</Ustrd></RmtInf>
        </DrctDbtTxInf>
    </FIToFICstmrDrctDbt>
</Document>"""

SAMPLE_PACS003_SUSPICIOUS = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.003.001.08">
    <FIToFICstmrDrctDbt>
        <GrpHdr>
            <MsgId>PACS003_MSG_SUSPICIOUS</MsgId>
            <CreDtTm>2026-09-23T09:15:00Z</CreDtTm>
        </GrpHdr>
        <DrctDbtTxInf>
            <PmtId><EndToEndId>E2E-FRAUD-DD-99</EndToEndId></PmtId>
            <PmtTpInf><SeqTp>FRST</SeqTp></PmtTpInf>
            <IntrBkSttlmAmt Ccy="EUR">18500.00</IntrBkSttlmAmt>
            <IntrBkSttlmDt>2026-09-23</IntrBkSttlmDt>
            <DrctDbtTx>
                <MndtRltdInf>
                    <MndtId></MndtId>
                    <DtOfSgntr>2026-09-23</DtOfSgntr>
                </MndtRltdInf>
            </DrctDbtTx>
            <Cdtr>
                <Nm>Anonymous Shell Ltd</Nm>
                <PstlAdr><Ctry>CY</Ctry></PstlAdr>
            </Cdtr>
            <CdtrAcct><Id><IBAN>CY17002001280000001200527600</IBAN></Id></CdtrAcct>
            <Dbtr>
                <Nm>Victim Consumer</Nm>
                <PstlAdr><Ctry>FR</Ctry></PstlAdr>
            </Dbtr>
            <DbtrAcct><Id><IBAN>FR7699999999999999999999999</IBAN></Id></DbtrAcct>
            <RmtInf><Ustrd>URGENT DIRECT DEBIT CLAIM</Ustrd></RmtInf>
        </DrctDbtTxInf>
    </FIToFICstmrDrctDbt>
</Document>"""


# ── Tests ────────────────────────────────────────────────────────────────────


class TestCamt053BankStatementParser:
    """Verifies parsing of ISO 20022 camt.053 Bank-to-Customer Statements."""

    def test_parse_valid_camt053_full(self) -> None:
        """Parses multi-entry statement extracting balances, entries, and IBANs."""
        parsed = FinancialMessageParser.parse_iso_20022_camt053(SAMPLE_CAMT053_FULL)

        assert parsed["message_type"] == "ISO20022_CAMT053"
        assert parsed["statement_id"] == "STMT-2026-09-DE-001"
        assert parsed["account_iban"] == "DE89370400440532013000"
        assert parsed["account_currency"] == "EUR"
        assert parsed["servicer_bic"] == "DBEUMM21XXX"
        assert parsed["sender_country"] == "DE"
        assert parsed["entries_count"] == 2
        assert parsed["total_debit_amount"] == 12500.00
        assert parsed["total_credit_amount"] == 5000.00
        assert parsed["amount"] == 17500.00

        # Opening & closing balance verification
        assert parsed["opening_balance"] is not None
        assert parsed["opening_balance"]["amount"] == 150000.00
        assert parsed["opening_balance"]["balance_type"] == "OPBD"

        assert parsed["closing_balance"] is not None
        assert parsed["closing_balance"]["amount"] == 142500.00
        assert parsed["closing_balance"]["balance_type"] == "CLBD"

        # Entries verification
        e1 = parsed["entries"][0]
        assert e1["entry_reference"] == "ENTRY-001"
        assert e1["amount"] == 12500.00
        assert e1["credit_debit_indicator"] == "DBIT"
        assert e1["counterparty_name"] == "Vendor Corp SAS"
        assert e1["counterparty_iban"] == "FR7630006000011234567890189"
        assert e1["counterparty_country"] == "FR"
        assert e1["end_to_end_id"] == "E2E-CAMT-01"

        e2 = parsed["entries"][1]
        assert e2["entry_reference"] == "ENTRY-002"
        assert e2["amount"] == 5000.00
        assert e2["credit_debit_indicator"] == "CRDT"
        assert e2["counterparty_name"] == "Client Netherlands BV"
        assert e2["counterparty_country"] == "NL"

    def test_camt053_missing_statement_block_raises(self) -> None:
        """Fails when Stmt block is missing in camt.053 payload."""
        xml = "<Document><BkToCstmrStmt><GrpHdr><MsgId>M1</MsgId></GrpHdr></BkToCstmrStmt></Document>"
        with pytest.raises(FinancialMessageParserError, match="Stmt .* not found"):
            FinancialMessageParser.parse_iso_20022_camt053(xml)

    def test_camt053_missing_account_raises(self) -> None:
        """Fails when statement has no account identifier."""
        xml = "<Document><BkToCstmrStmt><Stmt><Id>S1</Id></Stmt></BkToCstmrStmt></Document>"
        with pytest.raises(FinancialMessageParserError, match="account identifier .* missing"):
            FinancialMessageParser.parse_iso_20022_camt053(xml)

    def test_camt053_entry_invalid_amount_raises(self) -> None:
        """Fails when statement entry has negative or non-numeric amount."""
        bad_xml = SAMPLE_CAMT053_FULL.replace(
            '<Amt Ccy="EUR">12500.00</Amt>',
            '<Amt Ccy="EUR">-10.00</Amt>',
        )
        with pytest.raises(FinancialMessageParserError, match="amount must be positive"):
            FinancialMessageParser.parse_iso_20022_camt053(bad_xml)


class TestPacs002PaymentStatusReportParser:
    """Verifies parsing of ISO 20022 pacs.002 Payment Status Reports."""

    def test_parse_rejected_status_with_reason_code(self) -> None:
        """Parses clearing rejection report with AC04 Closed Account code."""
        parsed = FinancialMessageParser.parse_iso_20022_pacs002(SAMPLE_PACS002_REJECTED)

        assert parsed["message_type"] == "ISO20022_PACS002"
        assert parsed["status_message_id"] == "PACS002_MSG_774411"
        assert parsed["status"] == "RJCT"
        assert parsed["is_rejected"] is True
        assert parsed["reason_code"] == "AC04"
        assert parsed["reason_description"] == "Closed Account Number"
        assert "closed by debtor" in parsed["additional_info"]
        assert parsed["amount"] == 45200.00
        assert parsed["original_end_to_end_id"] == "E2E-ORIG-9910"
        assert parsed["sender_name"] == "Fictitious Sender AG"
        assert parsed["sender_account"] == "DE89370400440532013000"
        assert parsed["receiver_name"] == "Target Mule GmbH"
        assert parsed["receiver_country"] == "FR"

    def test_parse_accepted_status(self) -> None:
        """Parses ACCP status report with accepted profile."""
        parsed = FinancialMessageParser.parse_iso_20022_pacs002(SAMPLE_PACS002_ACCEPTED)

        assert parsed["status"] == "ACCP"
        assert parsed["is_rejected"] is False
        assert parsed["reason_code"] == ""
        assert parsed["amount"] == 1200.00
        assert parsed["original_end_to_end_id"] == "E2E-ACCP-8877"


class TestPacs003CustomerDirectDebitParser:
    """Verifies parsing and validation of ISO 20022 pacs.003 Direct Debit messages."""

    def test_parse_valid_pacs003(self) -> None:
        """Parses standard SEPA direct debit message extracting mandate and sequence type."""
        parsed = FinancialMessageParser.parse_iso_20022_pacs003(SAMPLE_PACS003_LEGITIMATE)

        assert parsed["message_type"] == "ISO20022_PACS003"
        assert parsed["transaction_id"] == "E2E-LEGIT-DD-01"
        assert parsed["amount"] == 129.50
        assert parsed["currency"] == "EUR"
        assert parsed["sequence_type"] == "RCUR"
        assert parsed["mandate_id"] == "MANDATE-ENERGY-DE-991244"
        assert parsed["mandate_signature_date"] == "2024-01-15"
        assert parsed["creditor_scheme_id"] == "DE98ZZZ09999999999"
        assert parsed["receiver_name"] == "Berlin Power & Gas GmbH"
        assert parsed["receiver_country"] == "DE"
        assert parsed["sender_name"] == "Hans Schmidt"
        assert parsed["sender_country"] == "DE"

    def test_pacs003_missing_tx_info_raises(self) -> None:
        """Fails when DrctDbtTxInf block is missing."""
        xml = "<Document><FIToFICstmrDrctDbt><GrpHdr><MsgId>M1</MsgId></GrpHdr></FIToFICstmrDrctDbt></Document>"
        with pytest.raises(FinancialMessageParserError, match="DrctDbtTxInf .* not found"):
            FinancialMessageParser.parse_iso_20022_pacs003(xml)

    def test_pacs003_missing_or_negative_amount_raises(self) -> None:
        """Fails when direct debit settlement amount is missing or <= 0."""
        bad_xml = SAMPLE_PACS003_LEGITIMATE.replace(
            '<IntrBkSttlmAmt Ccy="EUR">129.50</IntrBkSttlmAmt>',
            '<IntrBkSttlmAmt Ccy="EUR">0.00</IntrBkSttlmAmt>',
        )
        with pytest.raises(FinancialMessageParserError, match="amount must be greater than zero"):
            FinancialMessageParser.parse_iso_20022_pacs003(bad_xml)


class TestDirectDebitRiskScoring:
    """Verifies heuristic fraud risk assessment for unauthorized debit pull schemes."""

    def test_legitimate_recurring_direct_debit_low_risk(self) -> None:
        """Recurring domestic retail direct debit scores as LOW risk."""
        parsed = FinancialMessageParser.parse_iso_20022_pacs003(SAMPLE_PACS003_LEGITIMATE)
        scored = FinancialMessageParser.score_direct_debit_risk(parsed)

        assert scored["risk_level"] == "LOW"
        assert scored["risk_score"] < 0.35
        assert scored["recommended_action"] == "ALLOW"
        assert scored["sequence_type"] == "RCUR"

    def test_suspicious_unauthorized_direct_debit_high_risk(self) -> None:
        """High-value cross-border FRST pull with missing mandate scores as HIGH risk."""
        parsed = FinancialMessageParser.parse_iso_20022_pacs003(SAMPLE_PACS003_SUSPICIOUS)
        scored = FinancialMessageParser.score_direct_debit_risk(parsed)

        assert scored["risk_level"] == "HIGH"
        assert scored["risk_score"] >= 0.70
        assert scored["recommended_action"] == "REJECT_AND_HOLD"
        assert any("mandate" in f.lower() for f in scored["risk_factors"])
        assert any("cross-border" in f.lower() for f in scored["risk_factors"])
        assert any("high-value" in f.lower() for f in scored["risk_factors"])

    def test_medium_risk_one_off_threshold(self) -> None:
        """One-Off sequence type with moderately elevated amount scores as MEDIUM risk."""
        data = {
            "transaction_id": "TX_TEST_MEDIUM",
            "amount": 3500.00,
            "currency": "EUR",
            "sequence_type": "OOFF",
            "mandate_id": "MNDT-OOFF-88",
            "mandate_signature_date": "2026-08-01",
            "date": "2026-09-23",
            "sender_account": "DE89370400440532013000",
            "receiver_account": "FR7630006000011234567890189",
            "sender_country": "DE",
            "receiver_country": "FR",
        }
        scored = FinancialMessageParser.score_direct_debit_risk(data)

        assert scored["risk_level"] == "MEDIUM"
        assert 0.35 <= scored["risk_score"] < 0.70
        assert scored["recommended_action"] == "FLAG_FOR_CONFIRMATION"


class TestAutoDetectionAndDispatch:
    """Verifies parse_message automatic standard identification."""

    def test_auto_detect_camt053(self) -> None:
        """Auto detects camt.053 XML by root structure."""
        parsed = FinancialMessageParser.parse_message(SAMPLE_CAMT053_FULL, "auto")
        assert parsed["message_type"] == "ISO20022_CAMT053"
        assert parsed["statement_id"] == "STMT-2026-09-DE-001"

    def test_auto_detect_pacs002(self) -> None:
        """Auto detects pacs.002 XML by status report structure."""
        parsed = FinancialMessageParser.parse_message(SAMPLE_PACS002_REJECTED, "auto")
        assert parsed["message_type"] == "ISO20022_PACS002"
        assert parsed["reason_code"] == "AC04"

    def test_auto_detect_pacs003(self) -> None:
        """Auto detects pacs.003 XML by direct debit structure."""
        parsed = FinancialMessageParser.parse_message(SAMPLE_PACS003_LEGITIMATE, "auto")
        assert parsed["message_type"] == "ISO20022_PACS003"
        assert parsed["mandate_id"] == "MANDATE-ENERGY-DE-991244"


class TestZeroPIIPrivacyTransforms:
    """Verifies salted HMAC-SHA256 privacy feature transformation."""

    def test_camt053_privacy_features(self) -> None:
        """Verifies zero raw PII in camt.053 privacy record."""
        parsed = FinancialMessageParser.parse_iso_20022_camt053(SAMPLE_CAMT053_FULL)
        privacy = FinancialMessageParser.to_privacy_preserving_features(parsed, salt="test_salt_123")

        assert len(privacy["sender_account_hash"]) == 64
        assert privacy["sender_account_hash"] != parsed["account_iban"]
        assert privacy["entries_count"] == 2
        assert privacy["total_credit_amount"] == 5000.00
        assert privacy["total_debit_amount"] == 12500.00

    def test_pacs003_privacy_features(self) -> None:
        """Verifies mandate ID and accounts are hashed with salted HMAC."""
        parsed = FinancialMessageParser.parse_iso_20022_pacs003(SAMPLE_PACS003_LEGITIMATE)
        privacy = FinancialMessageParser.to_privacy_preserving_features(parsed, salt="sdd_salt_456")

        assert len(privacy["sender_account_hash"]) == 64
        assert len(privacy["receiver_account_hash"]) == 64
        assert len(privacy["mandate_id_hash"]) == 64
        assert privacy["sequence_type"] == "RCUR"


class TestXMLSecurityAndXXEProtections:
    """Verifies strict defense against XXE, DTD expansion, and entity bomb payloads."""

    def test_camt053_xxe_rejection(self) -> None:
        """Rejects XXE entity declarations inside camt.053."""
        xxe = """<?xml version="1.0"?>
        <!DOCTYPE doc [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
        <Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
            <BkToCstmrStmt><Stmt><Id>&xxe;</Id></Stmt></BkToCstmrStmt>
        </Document>"""
        with pytest.raises(FinancialMessageParserError, match="strictly forbidden"):
            FinancialMessageParser.parse_iso_20022_camt053(xxe)

    def test_pacs003_xxe_rejection(self) -> None:
        """Rejects XXE entity declarations inside pacs.003."""
        xxe = """<?xml version="1.0"?>
        <!DOCTYPE doc [<!ENTITY xxe SYSTEM "file:///etc/hosts">]>
        <Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.003.001.08">
            <FIToFICstmrDrctDbt><DrctDbtTxInf><PmtId><EndToEndId>&xxe;</EndToEndId></PmtId></DrctDbtTxInf></FIToFICstmrDrctDbt>
        </Document>"""
        with pytest.raises(FinancialMessageParserError, match="strictly forbidden"):
            FinancialMessageParser.parse_iso_20022_pacs003(xxe)

    def test_empty_payload_rejection(self) -> None:
        """Rejects empty or whitespace-only message strings."""
        with pytest.raises(FinancialMessageParserError, match="Empty .* content"):
            FinancialMessageParser.parse_iso_20022_camt053("   ")


class TestISO20022ConnectorIntegration:
    """Verifies ISO20022MessagingConnector support for pacs.003."""

    def test_connector_parse_pacs003(self) -> None:
        """Parses pacs.003 directly into NormalizedTransaction entity."""
        connector = ISO20022MessagingConnector()
        tx = connector.parse_pacs003_xml(SAMPLE_PACS003_LEGITIMATE)

        assert tx.transaction_id == "E2E-LEGIT-DD-01"
        assert tx.amount == 129.50
        assert tx.currency == "EUR"
        assert tx.channel_type == "ISO20022_PACS003"
        assert tx.origin_country == "DE"

    def test_connector_parse_batch_with_pacs003(self) -> None:
        """Parses heterogeneous batch containing pacs.003 and camt.053."""
        connector = ISO20022MessagingConnector()
        batch = [SAMPLE_PACS003_LEGITIMATE, SAMPLE_CAMT053_FULL]
        results = connector.parse_batch(batch)

        assert len(results) == 3  # 1 pacs.003 + 2 camt.053 entries


class TestFinancialMessagesPresentationRouter:
    """Verifies FastAPI REST presentation endpoints using TestClient."""

    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(app)

    def test_camt053_parse_endpoint(self, client: TestClient) -> None:
        """Tests POST /api/v1/financial-messages/camt053/parse with privacy features."""
        resp = client.post(
            "/api/v1/financial-messages/camt053/parse",
            json={"raw_content": SAMPLE_CAMT053_FULL, "anonymize_pii": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message_type"] == "ISO20022_CAMT053"
        assert data["entries_count"] == 2
        assert data["is_valid_account_iban"] is True
        assert data["privacy_features"] is not None
        assert len(data["privacy_features"]["sender_account_hash"]) == 64

    def test_pacs002_parse_endpoint(self, client: TestClient) -> None:
        """Tests POST /api/v1/financial-messages/pacs002/parse."""
        resp = client.post(
            "/api/v1/financial-messages/pacs002/parse",
            json={"raw_content": SAMPLE_PACS002_REJECTED, "anonymize_pii": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message_type"] == "ISO20022_PACS002"
        assert data["status"] == "RJCT"
        assert data["reason_code"] == "AC04"
        assert data["reason_description"] == "Closed Account Number"

    def test_pacs003_parse_and_score_endpoint(self, client: TestClient) -> None:
        """Tests POST /api/v1/financial-messages/pacs003/parse with automated risk evaluation."""
        resp = client.post(
            "/api/v1/financial-messages/pacs003/parse",
            json={"raw_content": SAMPLE_PACS003_SUSPICIOUS, "score_risk": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message_type"] == "ISO20022_PACS003"
        assert data["risk_assessment"] is not None
        assert data["risk_assessment"]["risk_level"] == "HIGH"
        assert data["risk_assessment"]["recommended_action"] == "REJECT_AND_HOLD"

    def test_pacs003_score_risk_endpoint(self, client: TestClient) -> None:
        """Tests POST /api/v1/financial-messages/pacs003/score-risk endpoint."""
        resp = client.post(
            "/api/v1/financial-messages/pacs003/score-risk",
            json={"raw_content": SAMPLE_PACS003_LEGITIMATE},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["risk_level"] == "LOW"
        assert data["recommended_action"] == "ALLOW"

    def test_generic_parse_auto_endpoint(self, client: TestClient) -> None:
        """Tests POST /api/v1/financial-messages/parse with auto-detection."""
        resp = client.post(
            "/api/v1/financial-messages/parse",
            json={"raw_content": SAMPLE_PACS002_ACCEPTED, "message_type": "auto"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message_type"] == "ISO20022_PACS002"

    def test_supported_standards_catalog(self, client: TestClient) -> None:
        """Tests GET /api/v1/financial-messages/supported-standards."""
        resp = client.get("/api/v1/financial-messages/supported-standards")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_standards"] >= 6
        codes = [s["standard_code"] for s in data["standards"]]
        assert "camt.053.001.08" in codes
        assert "pacs.002.001.10" in codes
        assert "pacs.003.001.08" in codes

    def test_health_endpoint(self, client: TestClient) -> None:
        """Tests GET /api/v1/financial-messages/health probe."""
        resp = client.get("/api/v1/financial-messages/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "UP"

    def test_malformed_xml_returns_400_not_200(self, client: TestClient) -> None:
        """Zero Deceptive 200 Invariant: Malformed XML returns HTTP 400 Bad Request."""
        resp = client.post(
            "/api/v1/financial-messages/camt053/parse",
            json={"raw_content": "<Document><Malformed></Document>"},
        )
        assert resp.status_code == 400
        assert "failure" in resp.json()["detail"].lower() or "error" in resp.json()["detail"].lower()
