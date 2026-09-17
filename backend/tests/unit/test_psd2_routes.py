"""Unit and integration tests for Open Banking PSD2 and ISO 20022 API endpoints.

Tests Berlin Group NextGenPSD2 AISP/PISP flows, consent lifecycle, Mod-97 IBAN
validation, payment initiation, XXE injection protection, and dual routing.
"""

from __future__ import annotations

import time
from typing import Generator

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

# Valid German and French IBANs passing ISO 13616 Mod-97 checksum
VALID_DEBTOR_IBAN = "DE89370400440532013000"
VALID_CREDITOR_IBAN = "FR04300060000112345678901"
INVALID_MOD97_IBAN = "DE89370400440532013999"

SAMPLE_PACS008_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
    <FIToFICstmrCdtTrf>
        <GrpHdr>
            <MsgId>MSG-2026-PACS008-01</MsgId>
            <CreDtTm>2026-07-16T12:00:00Z</CreDtTm>
        </GrpHdr>
        <CdtTrfTxInf>
            <PmtId>
                <EndToEndId>E2E-REF-777888</EndToEndId>
                <TxId>TX-PACS008-1001</TxId>
            </PmtId>
            <IntrBkSttlmAmt Ccy="EUR">3500.00</IntrBkSttlmAmt>
            <IntrBkSttlmDt>2026-07-16</IntrBkSttlmDt>
            <Dbtr>
                <Nm>Atlas Commercial Logistics</Nm>
                <PstlAdr><Ctry>DE</Ctry></PstlAdr>
            </Dbtr>
            <DbtrAcct>
                <Id><IBAN>DE89370400440532013000</IBAN></Id>
            </DbtrAcct>
            <DbtrAgt>
                <FinInstnId><BICFI>DBANKDEDDXXX</BICFI></FinInstnId>
            </DbtrAgt>
            <Cdtr>
                <Nm>Pacific Import Export GmbH</Nm>
                <PstlAdr><Ctry>FR</Ctry></PstlAdr>
            </Cdtr>
            <CdtrAcct>
                <Id><IBAN>FR04300060000112345678901</IBAN></Id>
            </CdtrAcct>
            <CdtrAgt>
                <FinInstnId><BICFI>BNPAFRPPXXX</BICFI></FinInstnId>
            </CdtrAgt>
            <RmtInf>
                <Ustrd>FREIGHT FORWARDING SERVICES INVOICE 9942</Ustrd>
            </RmtInf>
        </CdtTrfTxInf>
    </FIToFICstmrCdtTrf>
</Document>"""

SAMPLE_PAIN001_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03">
    <CstmrCdtTrfInitn>
        <GrpHdr><MsgId>PAIN-INIT-001</MsgId></GrpHdr>
        <PmtInf>
            <Dbtr><Nm>Corporate Treasury</Nm></Dbtr>
            <DbtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></DbtrAcct>
            <CdtTrfTxInf>
                <PmtId><EndToEndId>TX-PAIN-999</EndToEndId></PmtId>
                <Amt><InstdAmt Ccy="EUR">12500.00</InstdAmt></Amt>
                <Cdtr><Nm>Vendor Alliance BV</Nm></Cdtr>
                <CdtrAcct><Id><IBAN>FR04300060000112345678901</IBAN></Id></CdtrAcct>
                <RmtInf><Ustrd>CONSULTING CONTRACT MILESTONE 2</Ustrd></RmtInf>
            </CdtTrfTxInf>
        </PmtInf>
    </CstmrCdtTrfInitn>
</Document>"""

SAMPLE_SWIFT_MT103 = """{1:F01ALICUS33XXXX0000000000}{2:I103DBANKDEDDXXXXN}{3:{108:998811}}{4:
:20:TX-SWIFT-2026-999
:32A:260716EUR25000,00
:50K:/DE89370400440532013000
GLOBAL ENTERPRISE HOLDINGS
FRANKFURT
:59:/FR04300060000112345678901
PARTNER LOGISTICS SA
:70:CONTRACT QUARTERLY SETTLEMENT
:57A:DBANKDEDDXXX
-}"""


@pytest.fixture
def test_client() -> Generator[TestClient, None, None]:
    client = TestClient(app)
    yield client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    settings = get_settings()
    payload = {
        "sub": "tpp_compliance_auditor",
        "tenant_id": "bank_a",
        "scope": "psd2:full",
        "exp": time.time() + 7200,
    }
    token = jwt.encode(payload, settings.psd2_jwt_secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


# ── Consent Lifecycle Tests ──────────────────────────────────────────────────


def test_psd2_consent_full_lifecycle(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Tests creating, fetching, and revoking a PSD2 consent."""
    valid_until = time.time() + 3600
    create_payload = {
        "account_id": "acc_corporate_01",
        "permissions": ["read_accounts", "read_transactions", "initiate_payments"],
        "valid_until": valid_until,
        "debtor_iban": VALID_DEBTOR_IBAN,
        "tenant_id": "bank_a",
    }
    # 1. Create Consent
    res = test_client.post("/api/v1/psd2/consents", json=create_payload, headers=auth_headers)
    assert res.status_code == 201
    created = res.json()
    consent_id = created["consent_id"]
    assert consent_id.startswith("consent_")
    assert created["status"] == "valid"
    assert created["account_id"] == "acc_corporate_01"
    assert "initiate_payments" in created["permissions"]

    # 2. Get Consent Details
    get_res = test_client.get(f"/api/v1/psd2/consents/{consent_id}", headers=auth_headers)
    assert get_res.status_code == 200
    details = get_res.json()
    assert details["consent_id"] == consent_id
    assert details["status"] == "valid"

    # 3. Revoke Consent
    del_res = test_client.delete(f"/api/v1/psd2/consents/{consent_id}", headers=auth_headers)
    assert del_res.status_code == 200
    revoked = del_res.json()
    assert revoked["status"] == "revoked"

    # 4. Verify Revocation Preserved
    verify_res = test_client.get(f"/api/v1/psd2/consents/{consent_id}", headers=auth_headers)
    assert verify_res.status_code == 200
    assert verify_res.json()["status"] == "revoked"


def test_psd2_consent_not_found_returns_404(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verifies that querying a non-existent consent returns RFC-compliant 404."""
    res = test_client.get("/api/v1/psd2/consents/consent_nonexistent_999", headers=auth_headers)
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_psd2_consent_past_expiration_rejected(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verifies that establishing a consent with past expiration returns 400 Bad Request."""
    res = test_client.post(
        "/api/v1/psd2/consents",
        json={
            "account_id": "acc_001",
            "permissions": ["read_accounts"],
            "valid_until": time.time() - 100,
        },
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert "future" in res.json()["detail"].lower()


def test_psd2_consent_invalid_iban_rejected(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verifies that establishing a consent with invalid debtor IBAN returns 400 Bad Request."""
    res = test_client.post(
        "/api/v1/psd2/consents",
        json={
            "account_id": "acc_001",
            "permissions": ["read_accounts"],
            "valid_until": time.time() + 3600,
            "debtor_iban": INVALID_MOD97_IBAN,
        },
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert "invalid debtor iban" in res.json()["detail"].lower()


# ── Payment Initiation Services (PISP) Tests ─────────────────────────────────


def test_psd2_payment_initiation_success(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Tests successful NextGenPSD2 payment initiation under SEPA rail."""
    payment_payload = {
        "debtor_account": VALID_DEBTOR_IBAN,
        "creditor_account": VALID_CREDITOR_IBAN,
        "instructed_amount": 1500.50,
        "currency": "EUR",
        "creditor_name": "Pacific Import Export GmbH",
        "debtor_name": "Atlas Commercial Logistics",
        "remittance_information": "INVOICE 88129",
        "payment_product": "sepa-credit-transfers",
    }
    res = test_client.post("/api/v1/psd2/payments", json=payment_payload, headers=auth_headers)
    assert res.status_code == 201
    data = res.json()
    assert data["payment_id"].startswith("pmt_")
    assert data["transaction_status"] in ("ACTC", "RCVD")
    assert data["debtor_account"] == VALID_DEBTOR_IBAN
    assert data["instructed_amount"] == 1500.50
    assert data["risk_score"] < 0.50
    assert not data["is_flagged_for_review"]

    # Retrieve payment status
    payment_id = data["payment_id"]
    status_res = test_client.get(f"/api/v1/psd2/payments/{payment_id}", headers=auth_headers)
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["payment_id"] == payment_id
    assert status_data["transaction_status"] == data["transaction_status"]


def test_psd2_payment_invalid_debtor_iban_rejected(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verifies that invalid debtor IBAN checksum is rejected with 400 Bad Request."""
    payment_payload = {
        "debtor_account": INVALID_MOD97_IBAN,
        "creditor_account": VALID_CREDITOR_IBAN,
        "instructed_amount": 500.0,
        "currency": "EUR",
        "creditor_name": "Legitimate Merchant",
    }
    res = test_client.post("/api/v1/psd2/payments", json=payment_payload, headers=auth_headers)
    assert res.status_code == 400
    assert "debtor iban" in res.json()["detail"].lower()


def test_psd2_payment_invalid_creditor_iban_rejected(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verifies that invalid creditor IBAN checksum is rejected with 400 Bad Request."""
    payment_payload = {
        "debtor_account": VALID_DEBTOR_IBAN,
        "creditor_account": INVALID_MOD97_IBAN,
        "instructed_amount": 500.0,
        "currency": "EUR",
        "creditor_name": "Legitimate Merchant",
    }
    res = test_client.post("/api/v1/psd2/payments", json=payment_payload, headers=auth_headers)
    assert res.status_code == 400
    assert "creditor iban" in res.json()["detail"].lower()


def test_psd2_payment_invalid_amount_rejected(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verifies that negative or zero transfer amount is rejected with 400 or 422."""
    payment_payload = {
        "debtor_account": VALID_DEBTOR_IBAN,
        "creditor_account": VALID_CREDITOR_IBAN,
        "instructed_amount": -100.0,
        "currency": "EUR",
        "creditor_name": "Beneficiary",
    }
    res = test_client.post("/api/v1/psd2/payments", json=payment_payload, headers=auth_headers)
    assert res.status_code in (400, 422)


def test_psd2_payment_with_consent_enforcement(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Tests payment initiation when bounded by a specific customer consent token."""
    # 1. Create consent with only read_accounts (missing initiate_payments)
    read_only_res = test_client.post(
        "/api/v1/psd2/consents",
        json={
            "account_id": "acc_restricted",
            "permissions": ["read_accounts"],
            "valid_until": time.time() + 3600,
        },
        headers=auth_headers,
    )
    assert read_only_res.status_code == 201
    restricted_consent_id = read_only_res.json()["consent_id"]

    # Payment attempt with restricted consent must be forbidden (403)
    payment_payload = {
        "debtor_account": VALID_DEBTOR_IBAN,
        "creditor_account": VALID_CREDITOR_IBAN,
        "instructed_amount": 200.0,
        "currency": "EUR",
        "creditor_name": "Merchant",
        "consent_id": restricted_consent_id,
    }
    res = test_client.post("/api/v1/psd2/payments", json=payment_payload, headers=auth_headers)
    assert res.status_code == 403
    assert "initiate_payments" in res.json()["detail"].lower()


def test_psd2_payment_high_amount_flags_review(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verifies that high-value transfers trigger elevated risk score and review flagging."""
    payment_payload = {
        "debtor_account": VALID_DEBTOR_IBAN,
        "creditor_account": VALID_CREDITOR_IBAN,
        "instructed_amount": 75000.0,
        "currency": "EUR",
        "creditor_name": "Offshore Holdings",
        "payment_product": "cross-border-credit-transfers",
    }
    res = test_client.post("/api/v1/psd2/payments", json=payment_payload, headers=auth_headers)
    assert res.status_code == 201
    data = res.json()
    assert data["risk_score"] >= 0.70
    assert data["is_flagged_for_review"]
    assert data["transaction_status"] == "RCVD"


def test_psd2_payment_not_found_returns_404(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verifies that querying a non-existent payment returns 404."""
    res = test_client.get("/api/v1/psd2/payments/pmt_nonexistent_12345", headers=auth_headers)
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


# ── ISO 20022 / Financial Message Parsing Tests ──────────────────────────────


def test_psd2_iso20022_parse_pacs008_endpoint(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Tests parsing raw ISO 20022 pacs.008 XML message via API endpoint."""
    res = test_client.post(
        "/api/v1/psd2/iso20022/parse",
        json={
            "raw_content": SAMPLE_PACS008_XML,
            "message_type": "pacs.008",
            "anonymize_pii": True,
        },
        headers=auth_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["message_type"] == "ISO20022_PACS008"
    assert data["amount"] == 3500.00
    assert data["currency"] == "EUR"
    assert data["sender_account"] == VALID_DEBTOR_IBAN
    assert data["is_valid_debtor_iban"]
    assert data["receiver_account"] == VALID_CREDITOR_IBAN
    assert data["is_valid_creditor_iban"]
    assert data["privacy_features"] is not None
    assert "sender_account_hash" in data["privacy_features"]


def test_psd2_iso20022_parse_pain001_endpoint(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Tests parsing raw ISO 20022 pain.001 XML message via API endpoint."""
    res = test_client.post(
        "/api/v1/psd2/iso20022/parse",
        json={
            "raw_content": SAMPLE_PAIN001_XML,
            "message_type": "pain.001",
            "anonymize_pii": True,
        },
        headers=auth_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["message_type"] == "SEPA_SCT"
    assert data["amount"] == 12500.00
    assert data["currency"] == "EUR"
    assert data["sender_account"] == VALID_DEBTOR_IBAN


def test_psd2_iso20022_parse_swift_mt103_endpoint(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Tests parsing raw SWIFT MT103 financial message via API endpoint."""
    res = test_client.post(
        "/api/v1/psd2/iso20022/parse",
        json={
            "raw_content": SAMPLE_SWIFT_MT103,
            "message_type": "mt103",
            "anonymize_pii": True,
        },
        headers=auth_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["message_type"] == "SWIFT_MT103"
    assert data["amount"] == 25000.00
    assert data["currency"] == "EUR"
    assert data["sender_account"] == VALID_DEBTOR_IBAN
    assert data["receiver_account"] == VALID_CREDITOR_IBAN


def test_psd2_iso20022_xxe_attack_mitigation(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Vector 8 & 12: Verifies that XML with DOCTYPE/external entity definitions is rejected immediately."""
    xxe_payload = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE test [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
    <FIToFICstmrCdtTrf>
        <CdtTrfTxInf>
            <IntrBkSttlmAmt Ccy="EUR">100.00</IntrBkSttlmAmt>
            <RmtInf><Ustrd>&xxe;</Ustrd></RmtInf>
        </CdtTrfTxInf>
    </FIToFICstmrCdtTrf>
</Document>"""

    res = test_client.post(
        "/api/v1/psd2/iso20022/parse",
        json={"raw_content": xxe_payload, "message_type": "auto"},
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert "disallowed dtd" in res.json()["detail"].lower() or "xxe" in res.json()["detail"].lower()


# ── Dual-Routing Compatibility Tests ─────────────────────────────────────────


def test_psd2_dual_routing_parity(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    """Tests that endpoints are accessible and identical under both /api/v1/psd2 and /v1/psd2."""
    # Create consent via /v1/psd2
    create_payload = {
        "account_id": "acc_1",
        "permissions": ["read_accounts", "read_transactions"],
        "valid_until": time.time() + 3600,
    }
    res_v1 = test_client.post("/v1/psd2/consents", json=create_payload, headers=auth_headers)
    assert res_v1.status_code == 201
    consent_id = res_v1.json()["consent_id"]

    # Query accounts via /api/v1/psd2
    res_api = test_client.get("/api/v1/psd2/accounts", headers={**auth_headers, "consent-id": consent_id})
    assert res_api.status_code == 200
    assert len(res_api.json()) == 1

    # Query accounts via /v1/psd2
    res_v1_accounts = test_client.get("/v1/psd2/accounts", headers={**auth_headers, "consent-id": consent_id})
    assert res_v1_accounts.status_code == 200
    assert res_v1_accounts.json() == res_api.json()
