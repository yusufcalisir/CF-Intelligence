"""Unit tests for Live Open Banking & SWIFT ISO 20022 REST Gateway (Section 11.1)."""

from __future__ import annotations

from app.infrastructure.connectors.iso20022_connector import ISO20022MessagingConnector
from app.infrastructure.connectors.open_banking_connector import OpenBankingConnector


def test_open_banking_eidas_header_injection_and_token_refresh() -> None:
    """Verifies PSD2 eIDAS header generation and OAuth2 token refresh tracking."""
    connector = OpenBankingConnector(
        base_url="https://sandbox.berlingroup.org/psd2/v1",
        tpp_signature_key="dummy_rsa_key",
        access_token="psd2_test_token_live",
    )

    headers = connector._get_headers(body_bytes=b'{"sample":"payload"}')

    assert "X-Request-ID" in headers
    assert "Digest" in headers
    assert headers["Digest"].startswith("SHA-256=")
    assert "Authorization" in headers
    assert "TPP-Signature" in headers

    token1 = connector._get_oauth2_token()
    token2 = connector._get_oauth2_token()
    assert token1 == token2


def test_iso20022_pacs_008_xml_message_parsing() -> None:
    """Verifies parsing of ISO 20022 pacs.008 customer credit transfer XML payload."""
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
        <FIToFICstmrCdtTrf>
            <GrpHdr>
                <MsgId>PACS008_TEST_999</MsgId>
            </GrpHdr>
            <CdtTrfTxInf>
                <IntrBkSttlmAmt Ccy="EUR">2500.50</IntrBkSttlmAmt>
                <DbtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></DbtrAcct>
                <CdtrAcct><Id><IBAN>FR1420041010050500013M02606</IBAN></Id></CdtrAcct>
                <Dbtr><PstlAdr><Ctry>DE</Ctry></PstlAdr></Dbtr>
                <Cdtr><PstlAdr><Ctry>FR</Ctry></PstlAdr></Cdtr>
            </CdtTrfTxInf>
        </FIToFICstmrCdtTrf>
    </Document>"""

    connector = ISO20022MessagingConnector()
    tx = connector.parse_pacs008_xml(xml_content)

    assert tx.transaction_id == "PACS008_TEST_999"
    assert tx.amount == 2500.50
    assert tx.currency == "EUR"
    assert tx.account_id == "DE89370400440532013000"
    assert tx.counterparty_account_id == "FR1420041010050500013M02606"
    assert tx.channel_type == "ISO20022_PACS008"


def test_iso20022_camt_053_xml_statement_normalization() -> None:
    """Verifies parsing of ISO 20022 camt.053 bank statement XML into NormalizedTransactions."""
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
        <BkToCstmrStmt>
            <Stmt>
                <Acct><Id><IBAN>DE89370400440532013999</IBAN></Id></Acct>
                <Ntry>
                    <NtryRef>CAMT053_ENTRY_1</NtryRef>
                    <Amt Ccy="EUR">1250.00</Amt>
                    <NtryDtls><TxDtls><RltdPties><Cdtr><Nm>Merchant ABC</Nm></Cdtr></RltdPties></TxDtls></NtryDtls>
                </Ntry>
                <Ntry>
                    <NtryRef>CAMT053_ENTRY_2</NtryRef>
                    <Amt Ccy="USD">750.00</Amt>
                    <NtryDtls><TxDtls><RltdPties><Dbtr><Nm>Client XYZ</Nm></Dbtr></RltdPties></TxDtls></NtryDtls>
                </Ntry>
            </Stmt>
        </BkToCstmrStmt>
    </Document>"""

    connector = ISO20022MessagingConnector()
    txs = connector.parse_camt053_xml(xml_content)

    assert len(txs) == 2
    assert txs[0].transaction_id == "CAMT053_ENTRY_1"
    assert txs[0].amount == 1250.00
    assert txs[0].account_id == "DE89370400440532013999"
    assert txs[0].counterparty_account_id == "Merchant ABC"
    assert txs[1].transaction_id == "CAMT053_ENTRY_2"
    assert txs[1].currency == "USD"
