"""Unit and integration test suite for European FIU & UNODC goAML / AMLA Regulatory Exporter.

Validates:
- Regulatory report creation across STR, SAR, TTR, and AIF types.
- Statutory validation: transaction requirements for STR/TTR, threshold bounds for TTR.
- Legal basis validation: AMLD6, EU AMLA Single Rulebook, GDPR Art. 6(1)(f).
- Official UNODC goAML 4.0 XML generation and XML well-formedness.
- EU AMLA Single Rulebook JSON schema compliance.
- Strict schema validation engine: valid payloads pass, corrupt/incomplete payloads fail.
- Cryptographic digital envelope: canonical XML SHA-256 digest and HMAC-SHA256 seal.
- Dual-control supervisory workflow: self-approval blocked, legitimate approval succeeds.
- Regulatory transmission gateway: unapproved reports blocked, approved reports transmitted.
- Immutable SHA-256 hash-chained audit logging and tamper verification.
- Metrics calculation accuracy.
- FastAPI REST endpoints across both canonical and legacy routes (/api/v1 and /v1).
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.application.schemas.regulatory_schemas import (
    CreateRegulatoryReportRequest,
    LegalBasisSchema,
    PartySignatorySchema,
    RegulatoryTransactionSchema,
    ReportAccountSchema,
    SupervisorySignoffRequest,
    TransmitReportRequest,
)
from app.application.services.fiu_regulatory_service import (
    DualControlViolationError,
    FIURegulatoryService,
    InvalidReportStateError,
    RegulatoryValidationError,
    ReportNotApprovedError,
)
from app.domain.enums import (
    LegalBasisType,
    RegulatoryReportStatus,
    RegulatoryReportType,
    RegulatorySubmissionFormat,
    ReportingEntityRole,
)
from app.main import app

os.environ.setdefault("TESTING", "1")


# ── Fixtures & Helper Factories ────────────────────────────────────────────────

@pytest.fixture()
def svc() -> FIURegulatoryService:
    return FIURegulatoryService()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _make_legal_basis() -> LegalBasisSchema:
    return LegalBasisSchema(
        legal_basis_type=LegalBasisType.AMLD6_ART_33,
        regulatory_framework="AMLD6",
        article_reference="Directive (EU) 2018/1673 & 2015/849 Art. 33",
        legitimate_interest_justification=(
            "Cross-institution financial intelligence processing under GDPR Art. 6(1)(f) "
            "and Art. 9(2)(g) for the detection and prevention of money laundering."
        ),
        reporting_entity_role=ReportingEntityRole.CREDIT_INSTITUTION,
    )


def _make_account(
    name: str = "Originating Bank EU",
    bic: str = "DEUTDEFFXXX",
    iban: str = "DE89370400440532013000",
    client_name: str = "Max Mustermann",
) -> ReportAccountSchema:
    return ReportAccountSchema(
        institution_name=name,
        institution_bic=bic,
        account_number=iban,
        currency="EUR",
        balance=Decimal("250000.00"),
        signatory=PartySignatorySchema(
            signatory_id="SIG-99120",
            name=client_name,
            role="ACCOUNT_HOLDER",
            id_type="PASSPORT",
            id_number_masked="P****8821",
            country_code="DE",
        ),
    )


def _make_transaction(
    tx_num: str = "TX-2026-001",
    amount: Decimal = Decimal("45000.00"),
) -> RegulatoryTransactionSchema:
    return RegulatoryTransactionSchema(
        transaction_number=tx_num,
        internal_ref_number=f"INT-{tx_num}",
        transaction_location="ONLINE_SEPA_PORTAL",
        transaction_description="Suspicious high-velocity structured transfer to high-risk beneficiary",
        date_transaction="2026-09-22T14:30:00",
        value_date="2026-09-22",
        transmode_code="SEPA_INSTANT",
        amount_local=amount,
        currency_code="EUR",
        from_funds_code="WIRE_TRANSFER",
        from_account=_make_account(
            name="Bank Alpha Germany",
            bic="DEUTDEFFXXX",
            iban="DE89370400440532013000",
            client_name="Max Mustermann",
        ),
        to_funds_code="WIRE_TRANSFER",
        to_account=_make_account(
            name="Bank Beta France",
            bic="BNPAFRPPXXX",
            iban="FR7630004000010000000000000",
            client_name="Jean Dupont",
        ),
    )


def _make_str_request(
    officer: str = "compliance_officer_alpha",
    amount: Decimal = Decimal("85000.00"),
) -> CreateRegulatoryReportRequest:
    return CreateRegulatoryReportRequest(
        report_type=RegulatoryReportType.STR,
        rentity_id="FIU-REG-DE-00142",
        rentity_branch="FRANKFURT_CENTRAL_AML",
        entity_reference="CASE-2026-EU-4491",
        reporting_user=officer,
        reason="Multiple rapid pass-through transfers detected indicative of mule network smurfing.",
        action_taken="ACCOUNT_PROVISIONALLY_FROZEN",
        legal_basis=_make_legal_basis(),
        transactions=[_make_transaction("TX-STR-01", amount=amount)],
        currency_code_local="EUR",
    )


def _make_sar_request(
    officer: str = "compliance_officer_beta",
) -> CreateRegulatoryReportRequest:
    return CreateRegulatoryReportRequest(
        report_type=RegulatoryReportType.SAR,
        rentity_id="FIU-REG-FR-00812",
        rentity_branch="PARIS_SPECIAL_INVESTIGATIONS",
        entity_reference="CASE-SAR-2026-902",
        reporting_user=officer,
        reason="Suspicious corporate ownership layering identified with shell entities and nominee directors.",
        action_taken="ENHANCED_DUE_DILIGENCE_INITIATED",
        legal_basis=_make_legal_basis(),
        transactions=[],
        currency_code_local="EUR",
    )


# ── Unit Tests: Service Layer ──────────────────────────────────────────────────

class TestFIURegulatoryServiceLifecycle:
    """Test suite verifying report creation, validation, and lifecycle operations."""

    def test_create_valid_str_report(self, svc: FIURegulatoryService) -> None:
        req = _make_str_request()
        rep = svc.create_report(req)

        assert rep.report_id.startswith("RPT-")
        assert rep.report_type == RegulatoryReportType.STR
        assert rep.status == RegulatoryReportStatus.DRAFT
        assert rep.rentity_id == "FIU-REG-DE-00142"
        assert rep.reporting_user == "compliance_officer_alpha"
        assert len(rep.transactions) == 1
        assert rep.digital_envelope is not None
        assert len(rep.digital_envelope.canonical_xml_sha256) == 64
        assert len(rep.audit_trail) == 1
        assert rep.audit_trail[0].action == "REPORT_DRAFT_CREATED"

    def test_create_valid_sar_report_without_transactions(self, svc: FIURegulatoryService) -> None:
        req = _make_sar_request()
        rep = svc.create_report(req)

        assert rep.report_type == RegulatoryReportType.SAR
        assert rep.status == RegulatoryReportStatus.DRAFT
        assert len(rep.transactions) == 0
        assert "nominee directors" in rep.reason

    def test_create_str_fails_without_transactions(self, svc: FIURegulatoryService) -> None:
        req = CreateRegulatoryReportRequest(
            report_type=RegulatoryReportType.STR,
            rentity_id="FIU-REG-DE-00142",
            rentity_branch="BRANCH_1",
            entity_reference="CASE-FAIL-1",
            reporting_user="officer_1",
            reason="Suspicious transaction flow observed across multiple accounts.",
            action_taken="FLAGGED",
            legal_basis=_make_legal_basis(),
            transactions=[],  # Empty transactions for STR must fail
        )
        with pytest.raises(RegulatoryValidationError, match="requires at least one transaction"):
            svc.create_report(req)

    def test_create_ttr_requires_threshold_amount(self, svc: FIURegulatoryService) -> None:
        # TTR with amount < €10,000 must fail
        req_invalid = CreateRegulatoryReportRequest(
            report_type=RegulatoryReportType.TTR,
            rentity_id="FIU-REG-DE-00142",
            rentity_branch="BRANCH_1",
            entity_reference="CASE-TTR-FAIL",
            reporting_user="officer_1",
            reason="Cash threshold transaction monitoring report.",
            action_taken="LOGGED",
            legal_basis=_make_legal_basis(),
            transactions=[_make_transaction("TX-TTR-LOW", amount=Decimal("9500.00"))],
        )
        with pytest.raises(RegulatoryValidationError, match="require transactions ≥ €10,000"):
            svc.create_report(req_invalid)

        # TTR with amount >= €10,000 succeeds
        req_valid = CreateRegulatoryReportRequest(
            report_type=RegulatoryReportType.TTR,
            rentity_id="FIU-REG-DE-00142",
            rentity_branch="BRANCH_1",
            entity_reference="CASE-TTR-PASS",
            reporting_user="officer_1",
            reason="Cash threshold transaction monitoring report.",
            action_taken="LOGGED",
            legal_basis=_make_legal_basis(),
            transactions=[_make_transaction("TX-TTR-HIGH", amount=Decimal("15000.00"))],
        )
        rep = svc.create_report(req_valid)
        assert rep.report_type == RegulatoryReportType.TTR

    def test_create_fails_with_short_reason(self, svc: FIURegulatoryService) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="at least 20 characters"):
            CreateRegulatoryReportRequest(
                report_type=RegulatoryReportType.SAR,
                rentity_id="FIU-REG-DE-00142",
                rentity_branch="BRANCH_1",
                entity_reference="CASE-FAIL-2",
                reporting_user="officer_1",
                reason="Too short.",  # < 20 chars
                action_taken="FLAGGED",
                legal_basis=_make_legal_basis(),
                transactions=[],
            )

    def test_submit_for_approval_workflow(self, svc: FIURegulatoryService) -> None:
        rep = svc.create_report(_make_str_request())
        assert rep.status == RegulatoryReportStatus.DRAFT

        submitted = svc.submit_for_approval(rep.report_id, user_id="compliance_officer_alpha")
        assert submitted.status == RegulatoryReportStatus.PENDING_SUPERVISORY_APPROVAL

        # Cannot submit twice
        with pytest.raises(InvalidReportStateError, match="Only DRAFT reports"):
            svc.submit_for_approval(rep.report_id, user_id="compliance_officer_alpha")


class TestDualControlSupervisoryWorkflow:
    """Test suite verifying dual-control compliance and self-approval prevention."""

    def test_dual_control_blocks_self_approval(self, svc: FIURegulatoryService) -> None:
        rep = svc.create_report(_make_str_request(officer="officer_alice"))
        svc.submit_for_approval(rep.report_id, user_id="officer_alice")

        # Officer Alice attempts to approve her own filing
        signoff_req = SupervisorySignoffRequest(
            supervisor_id="officer_alice",
            approval_status="APPROVED",
            comments="I approve my own work.",
        )
        with pytest.raises(DualControlViolationError, match="Self-approval is strictly prohibited"):
            svc.supervisory_signoff(rep.report_id, signoff_req)

    def test_legitimate_supervisor_approval_succeeds(self, svc: FIURegulatoryService) -> None:
        rep = svc.create_report(_make_str_request(officer="officer_alice"))
        svc.submit_for_approval(rep.report_id, user_id="officer_alice")

        # Supervisor Bob approves Alice's filing
        signoff_req = SupervisorySignoffRequest(
            supervisor_id="supervisor_bob",
            approval_status="APPROVED",
            comments="Confirmed AML typology and supporting transaction hashes. Approved for transmission.",
        )
        approved = svc.supervisory_signoff(rep.report_id, signoff_req)
        assert approved.status == RegulatoryReportStatus.APPROVED
        assert approved.supervisor_id == "supervisor_bob"
        assert approved.approved_at is not None

        # Cannot approve an already approved report
        with pytest.raises(InvalidReportStateError, match="Cannot perform supervisory sign-off"):
            svc.supervisory_signoff(rep.report_id, signoff_req)

    def test_supervisor_rejection_workflow(self, svc: FIURegulatoryService) -> None:
        rep = svc.create_report(_make_str_request(officer="officer_alice"))
        svc.submit_for_approval(rep.report_id, user_id="officer_alice")

        signoff_req = SupervisorySignoffRequest(
            supervisor_id="supervisor_bob",
            approval_status="REJECTED",
            comments="Insufficient evidence regarding beneficiary UBO. Return to analyst.",
        )
        rejected = svc.supervisory_signoff(rep.report_id, signoff_req)
        assert rejected.status == RegulatoryReportStatus.REJECTED
        assert rejected.supervisor_id == "supervisor_bob"


class TestTransmissionGateway:
    """Test suite verifying transmission rules and digital envelope receipts."""

    def test_cannot_transmit_unapproved_report(self, svc: FIURegulatoryService) -> None:
        rep = svc.create_report(_make_str_request())
        tx_req = TransmitReportRequest(destination_fiu="EU_AMLA_SUPERVISORY_HUB")

        with pytest.raises(ReportNotApprovedError, match="Only APPROVED filings can be submitted"):
            svc.transmit_to_fiu(rep.report_id, tx_req, actor_id="operator_1")

    def test_transmit_approved_report_succeeds(self, svc: FIURegulatoryService) -> None:
        rep = svc.create_report(_make_str_request(officer="officer_alice"))
        svc.submit_for_approval(rep.report_id, user_id="officer_alice")
        svc.supervisory_signoff(
            rep.report_id,
            SupervisorySignoffRequest(
                supervisor_id="supervisor_bob",
                approval_status="APPROVED",
                comments="Approved.",
            ),
        )

        tx_req = TransmitReportRequest(
            destination_fiu="EU_AMLA_SUPERVISORY_HUB",
            submission_format=RegulatorySubmissionFormat.GOAML_XML_4_0,
        )
        receipt = svc.transmit_to_fiu(rep.report_id, tx_req, actor_id="dispatcher_1")

        assert receipt.status == "TRANSMITTED"
        assert receipt.transmission_id.startswith("TX-")
        assert receipt.destination_fiu == "EU_AMLA_SUPERVISORY_HUB"
        assert len(receipt.receipt_signature) == 64

        # Verify status updated
        final_rep = svc.get_report(rep.report_id)
        assert final_rep.status == RegulatoryReportStatus.TRANSMITTED
        assert final_rep.transmitted_at is not None


class TestXMLAndJSONGenerators:
    """Test suite verifying UNODC goAML 4.0 XML and EU AMLA JSON generation."""

    def test_generate_goaml_4_xml(self, svc: FIURegulatoryService) -> None:
        rep = svc.create_report(_make_str_request())
        xml_str = svc.generate_goaml_4_xml(rep.report_id)

        assert xml_str.startswith("<?xml")
        assert "<report>" in xml_str
        assert "<rentity_id>FIU-REG-DE-00142</rentity_id>" in xml_str
        assert "<report_code>STR</report_code>" in xml_str
        assert "<submission_code>E</submission_code>" in xml_str
        assert "<currency_code_local>EUR</currency_code_local>" in xml_str
        assert "<legal_basis>" in xml_str
        assert "<transaction>" in xml_str
        assert "<amount_local>85000.00</amount_local>" in xml_str
        assert "<institution_code>DEUTDEFFXXX</institution_code>" in xml_str
        assert "<institution_code>BNPAFRPPXXX</institution_code>" in xml_str
        assert "</report>" in xml_str

    def test_generate_amla_json(self, svc: FIURegulatoryService) -> None:
        rep = svc.create_report(_make_str_request())
        json_data = svc.generate_amla_json(rep.report_id)

        assert json_data["$schema"] == "https://amla.europa.eu/schemas/v1/sar-interchange.json"
        assert json_data["report_id"] == rep.report_id
        assert json_data["report_type"] == "STR"
        assert json_data["legal_basis"]["statutory_basis"] == "AMLD6_ART_33"
        assert json_data["financial_activity"]["transaction_count"] == 1
        assert json_data["financial_activity"]["total_volume"] == 85000.0


class TestSchemaValidationEngine:
    """Test suite verifying strict validation against UNODC goAML specifications."""

    def test_valid_xml_passes_validation(self, svc: FIURegulatoryService) -> None:
        rep = svc.create_report(_make_str_request())
        xml_str = svc.generate_goaml_4_xml(rep.report_id)
        result = svc.validate_schema(xml_str)

        assert result.is_valid is True
        assert result.error_count == 0
        assert len(result.validation_errors) == 0

    def test_invalid_syntax_xml_fails(self, svc: FIURegulatoryService) -> None:
        bad_xml = "<report><unclosed_tag>"
        result = svc.validate_schema(bad_xml)

        assert result.is_valid is False
        assert result.error_count >= 1
        assert "Syntax Error" in result.validation_errors[0].error_message

    def test_missing_mandatory_elements_fails(self, svc: FIURegulatoryService) -> None:
        corrupted_xml = (
            "<report>"
            "  <rentity_id></rentity_id>"  # empty
            "  <report_code>STR</report_code>"
            "</report>"
        )
        result = svc.validate_schema(corrupted_xml)
        assert result.is_valid is False
        assert result.error_count >= 3


class TestAuditChainAndMetrics:
    """Test suite verifying immutable hash-chain and aggregate metrics."""

    def test_audit_chain_integrity(self, svc: FIURegulatoryService) -> None:
        rep = svc.create_report(_make_str_request(officer="officer_alice"))
        svc.submit_for_approval(rep.report_id, user_id="officer_alice")
        svc.supervisory_signoff(
            rep.report_id,
            SupervisorySignoffRequest(
                supervisor_id="supervisor_bob",
                approval_status="APPROVED",
                comments="Approved.",
            ),
        )
        svc.transmit_to_fiu(
            rep.report_id,
            TransmitReportRequest(destination_fiu="EU_FIU"),
            actor_id="operator_1",
        )

        assert svc.verify_audit_chain(rep.report_id) is True

        # Tamper test
        internal_rep = svc._reports[rep.report_id]
        internal_rep.audit_trail[1].detail = "TAMPERED DETAIL"
        assert svc.verify_audit_chain(rep.report_id) is False

    def test_metrics_calculation(self, svc: FIURegulatoryService) -> None:
        svc.create_report(_make_str_request(amount=Decimal("50000.00")))
        svc.create_report(_make_sar_request())

        metrics = svc.get_metrics()
        assert metrics["total_reports"] == 2
        assert metrics["draft_reports"] == 2
        assert metrics["total_suspicious_volume_eur"] == Decimal("50000.00")
        assert metrics["reports_by_type"]["STR"] == 1
        assert metrics["reports_by_type"]["SAR"] == 1


# ── Integration Tests: FastAPI Endpoints ───────────────────────────────────────

class TestFastAPIEndpoints:
    """Integration test suite verifying REST API contracts and HTTP status codes."""

    def test_create_and_fetch_report(self, client: TestClient) -> None:
        payload = {
            "report_type": "STR",
            "rentity_id": "FIU-REG-DE-00142",
            "rentity_branch": "CENTRAL",
            "entity_reference": "API-CASE-01",
            "reporting_user": "officer_test",
            "reason": "Structured pass-through transfers detected by GNN fraud engine.",
            "action_taken": "FROZEN",
            "legal_basis": {
                "legal_basis_type": "AMLD6_ART_33",
                "regulatory_framework": "AMLD6",
                "article_reference": "Art. 33",
                "legitimate_interest_justification": "Legitimate interest justified under GDPR Art. 6(1)(f) and Art. 9(2)(g).",
                "reporting_entity_role": "CREDIT_INSTITUTION",
            },
            "transactions": [
                {
                    "transaction_number": "TX-API-01",
                    "internal_ref_number": "INT-01",
                    "transaction_location": "WEB",
                    "transaction_description": "Cross-border wire transfer",
                    "date_transaction": "2026-09-22T10:00:00",
                    "transmode_code": "SEPA_INSTANT",
                    "amount_local": "120000.00",
                    "currency_code": "EUR",
                    "from_funds_code": "WIRE",
                    "from_account": {
                        "institution_name": "Bank Alpha",
                        "institution_bic": "DEUTDEFFXXX",
                        "account_number": "DE89370400440532013000",
                        "currency": "EUR",
                    },
                    "to_funds_code": "WIRE",
                    "to_account": {
                        "institution_name": "Bank Beta",
                        "institution_bic": "BNPAFRPPXXX",
                        "account_number": "FR7630004000010000000000000",
                        "currency": "EUR",
                    },
                }
            ],
            "currency_code_local": "EUR",
        }

        # Test canonical route
        res = client.post("/api/v1/regulatory/reports", json=payload)
        assert res.status_code == 201
        data = res.json()
        report_id = data["report_id"]
        assert report_id.startswith("RPT-")
        assert data["status"] == "DRAFT"

        # Fetch report details
        get_res = client.get(f"/api/v1/regulatory/reports/{report_id}")
        assert get_res.status_code == 200
        assert get_res.json()["report_id"] == report_id

        # Test legacy route compatibility (/v1/regulatory)
        v1_res = client.get(f"/v1/regulatory/reports/{report_id}")
        assert v1_res.status_code == 200
        assert v1_res.json()["report_id"] == report_id

    def test_dual_control_api_forbidden_on_self_approval(self, client: TestClient) -> None:
        payload = {
            "report_type": "SAR",
            "rentity_id": "FIU-REG-DE-00142",
            "rentity_branch": "CENTRAL",
            "entity_reference": "API-CASE-DUAL",
            "reporting_user": "officer_alice",
            "reason": "Suspicious shell corporation network identified in corporate registry.",
            "action_taken": "FLAGGED",
            "legal_basis": {
                "legal_basis_type": "AMLD6_ART_33",
                "regulatory_framework": "AMLD6",
                "article_reference": "Art. 33",
                "legitimate_interest_justification": "Legitimate interest justified under GDPR Art. 6(1)(f) and Art. 9(2)(g).",
                "reporting_entity_role": "CREDIT_INSTITUTION",
            },
            "transactions": [],
            "currency_code_local": "EUR",
        }
        res = client.post("/api/v1/regulatory/reports", json=payload)
        report_id = res.json()["report_id"]

        # Alice attempts self-approval -> 403 Forbidden
        signoff_payload = {
            "supervisor_id": "officer_alice",
            "approval_status": "APPROVED",
            "comments": "Self-approving my own SAR.",
        }
        sign_res = client.post(f"/api/v1/regulatory/reports/{report_id}/signoff", json=signoff_payload)
        assert sign_res.status_code == 403
        assert "Self-approval is strictly prohibited" in sign_res.json()["detail"]

    def test_xml_export_and_validation_endpoints(self, client: TestClient) -> None:
        payload = {
            "report_type": "SAR",
            "rentity_id": "FIU-REG-DE-00142",
            "rentity_branch": "CENTRAL",
            "entity_reference": "API-CASE-XML",
            "reporting_user": "officer_carol",
            "reason": "Suspicious activity detected across high-risk jurisdiction corporate accounts.",
            "action_taken": "FLAGGED",
            "legal_basis": {
                "legal_basis_type": "AMLD6_ART_33",
                "regulatory_framework": "AMLD6",
                "article_reference": "Art. 33",
                "legitimate_interest_justification": "Legitimate interest justified under GDPR Art. 6(1)(f) and Art. 9(2)(g).",
                "reporting_entity_role": "CREDIT_INSTITUTION",
            },
            "transactions": [],
            "currency_code_local": "EUR",
        }
        res = client.post("/api/v1/regulatory/reports", json=payload)
        report_id = res.json()["report_id"]

        # Export XML
        xml_res = client.get(f"/api/v1/regulatory/reports/{report_id}/xml")
        assert xml_res.status_code == 200
        assert "application/xml" in xml_res.headers["content-type"]
        assert "<report>" in xml_res.text

        # Validate schema
        val_res = client.post(f"/api/v1/regulatory/reports/{report_id}/validate")
        assert val_res.status_code == 200
        assert val_res.json()["is_valid"] is True

        # Export AMLA JSON
        json_res = client.get(f"/api/v1/regulatory/reports/{report_id}/amla-json")
        assert json_res.status_code == 200
        assert json_res.json()["report_type"] == "SAR"

    def test_metrics_endpoint(self, client: TestClient) -> None:
        res = client.get("/api/v1/regulatory/metrics")
        assert res.status_code == 200
        data = res.json()
        assert "total_reports" in data
        assert "dual_control_enforcement_rate" in data

    def test_audit_trail_endpoint(self, client: TestClient) -> None:
        payload = {
            "report_type": "SAR",
            "rentity_id": "FIU-REG-DE-00142",
            "rentity_branch": "CENTRAL",
            "entity_reference": "API-CASE-AUDIT",
            "reporting_user": "officer_audit",
            "reason": "Suspicious transaction velocity burst observed in audit trial test.",
            "action_taken": "FLAGGED",
            "legal_basis": {
                "legal_basis_type": "AMLD6_ART_33",
                "regulatory_framework": "AMLD6",
                "article_reference": "Art. 33",
                "legitimate_interest_justification": "Legitimate interest justified under GDPR Art. 6(1)(f) and Art. 9(2)(g).",
                "reporting_entity_role": "CREDIT_INSTITUTION",
            },
            "transactions": [],
            "currency_code_local": "EUR",
        }
        res = client.post("/api/v1/regulatory/reports", json=payload)
        report_id = res.json()["report_id"]

        audit_res = client.get(f"/api/v1/regulatory/reports/{report_id}/audit-trail")
        assert audit_res.status_code == 200
        audit_data = audit_res.json()
        assert audit_data["report_id"] == report_id
        assert audit_data["chain_integrity_valid"] is True
        assert audit_data["entry_count"] >= 1

    def test_envelope_endpoint(self, client: TestClient) -> None:
        payload = {
            "report_type": "SAR",
            "rentity_id": "FIU-REG-DE-00142",
            "rentity_branch": "CENTRAL",
            "entity_reference": "API-CASE-ENV",
            "reporting_user": "officer_env",
            "reason": "Suspicious shell corporate transactions without legitimate commercial purpose.",
            "action_taken": "FLAGGED",
            "legal_basis": {
                "legal_basis_type": "AMLD6_ART_33",
                "regulatory_framework": "AMLD6",
                "article_reference": "Art. 33",
                "legitimate_interest_justification": "Legitimate interest justified under GDPR Art. 6(1)(f) and Art. 9(2)(g).",
                "reporting_entity_role": "CREDIT_INSTITUTION",
            },
            "transactions": [],
            "currency_code_local": "EUR",
        }
        res = client.post("/api/v1/regulatory/reports", json=payload)
        report_id = res.json()["report_id"]

        env_res = client.get(f"/api/v1/regulatory/reports/{report_id}/envelope")
        assert env_res.status_code == 200
        env_data = env_res.json()
        assert env_data["report_id"] == report_id
        assert len(env_data["canonical_xml_sha256"]) == 64
        assert len(env_data["digital_envelope_token"]) == 64

    def test_endpoint_error_handling(self, client: TestClient) -> None:
        # Non-existent report 404s
        assert client.get("/api/v1/regulatory/reports/RPT-NONEXISTENT").status_code == 404
        assert client.post("/api/v1/regulatory/reports/RPT-NONEXISTENT/submit?user_id=usr").status_code == 404
        assert client.get("/api/v1/regulatory/reports/RPT-NONEXISTENT/xml").status_code == 404
        assert client.get("/api/v1/regulatory/reports/RPT-NONEXISTENT/amla-json").status_code == 404
        assert client.post("/api/v1/regulatory/reports/RPT-NONEXISTENT/validate").status_code == 404
        assert client.get("/api/v1/regulatory/reports/RPT-NONEXISTENT/envelope").status_code == 404
        assert client.get("/api/v1/regulatory/reports/RPT-NONEXISTENT/audit-trail").status_code == 404

        # Transmit unapproved returns 409
        payload = {
            "report_type": "SAR",
            "rentity_id": "FIU-REG-DE-00142",
            "rentity_branch": "CENTRAL",
            "entity_reference": "API-CASE-TX-FAIL",
            "reporting_user": "officer_tx",
            "reason": "Suspicious high-risk wire sequence without economic rationale.",
            "action_taken": "FLAGGED",
            "legal_basis": {
                "legal_basis_type": "AMLD6_ART_33",
                "regulatory_framework": "AMLD6",
                "article_reference": "Art. 33",
                "legitimate_interest_justification": "Legitimate interest justified under GDPR Art. 6(1)(f) and Art. 9(2)(g).",
                "reporting_entity_role": "CREDIT_INSTITUTION",
            },
            "transactions": [],
            "currency_code_local": "EUR",
        }
        res = client.post("/api/v1/regulatory/reports", json=payload)
        report_id = res.json()["report_id"]

        tx_res = client.post(
            f"/api/v1/regulatory/reports/{report_id}/transmit",
            json={"destination_fiu": "EU_FIU", "submission_format": "GOAML_XML_4_0"},
        )
        assert tx_res.status_code == 409
        assert "Only APPROVED filings" in tx_res.json()["detail"]


class TestSchemaValidationEdgeCases:
    """Test suite verifying granular schema validator error detection."""

    def test_invalid_root_tag(self, svc: FIURegulatoryService) -> None:
        xml = "<invalidRoot><rentity_id>123</rentity_id></invalidRoot>"
        res = svc.validate_schema(xml)
        assert res.is_valid is False
        assert any("Root element must be <report>" in e.error_message for e in res.validation_errors)

    def test_invalid_currency_code(self, svc: FIURegulatoryService) -> None:
        xml = (
            "<report>"
            "  <rentity_id>FIU-1</rentity_id>"
            "  <submission_code>E</submission_code>"
            "  <report_code>SAR</report_code>"
            "  <entity_reference>REF-1</entity_reference>"
            "  <report_date>2026-09-22T10:00:00</report_date>"
            "  <currency_code_local>EUROPEAN_EURO</currency_code_local>"
            "  <reporting_user>usr1</reporting_user>"
            "  <reason>Valid grounds for suspicion under AML regulations.</reason>"
            "  <action>FLAGGED</action>"
            "  <legal_basis>"
            "    <legitimate_interest_justification>Detailed justification string exceeding 15 chars.</legitimate_interest_justification>"
            "  </legal_basis>"
            "</report>"
        )
        res = svc.validate_schema(xml)
        assert res.is_valid is False
        assert any("valid 3-letter ISO 4217 code" in e.error_message for e in res.validation_errors)

    def test_invalid_report_code(self, svc: FIURegulatoryService) -> None:
        xml = (
            "<report>"
            "  <rentity_id>FIU-1</rentity_id>"
            "  <submission_code>E</submission_code>"
            "  <report_code>UNKNOWN_CODE</report_code>"
            "  <entity_reference>REF-1</entity_reference>"
            "  <report_date>2026-09-22T10:00:00</report_date>"
            "  <currency_code_local>EUR</currency_code_local>"
            "  <reporting_user>usr1</reporting_user>"
            "  <reason>Valid grounds for suspicion under AML regulations.</reason>"
            "  <action>FLAGGED</action>"
            "  <legal_basis>"
            "    <legitimate_interest_justification>Detailed justification string exceeding 15 chars.</legitimate_interest_justification>"
            "  </legal_basis>"
            "</report>"
        )
        res = svc.validate_schema(xml)
        assert res.is_valid is False
        assert any("Report code 'UNKNOWN_CODE' is invalid" in e.error_message for e in res.validation_errors)

    def test_invalid_bic_in_xml(self, svc: FIURegulatoryService) -> None:
        xml = (
            "<report>"
            "  <rentity_id>FIU-1</rentity_id>"
            "  <submission_code>E</submission_code>"
            "  <report_code>STR</report_code>"
            "  <entity_reference>REF-1</entity_reference>"
            "  <report_date>2026-09-22T10:00:00</report_date>"
            "  <currency_code_local>EUR</currency_code_local>"
            "  <reporting_user>usr1</reporting_user>"
            "  <reason>Valid grounds for suspicion under AML regulations.</reason>"
            "  <action>FLAGGED</action>"
            "  <legal_basis>"
            "    <legitimate_interest_justification>Detailed justification string exceeding 15 chars.</legitimate_interest_justification>"
            "  </legal_basis>"
            "  <transaction>"
            "    <transaction_number>TX-1</transaction_number>"
            "    <amount_local>1000.00</amount_local>"
            "    <t_from>"
            "      <from_account>"
            "        <institution_code>INVALID_BIC_TOO_LONG_12345</institution_code>"
            "        <account>DE89370400440532013000</account>"
            "      </from_account>"
            "    </t_from>"
            "  </transaction>"
            "</report>"
        )
        res = svc.validate_schema(xml)
        assert res.is_valid is False
        assert any("not a valid BIC" in e.error_message for e in res.validation_errors)


class TestPydanticSchemaValidations:
    """Test suite verifying Pydantic v2 validators for BIC, IBAN, and amounts."""

    def test_invalid_bic_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Invalid BIC format"):
            ReportAccountSchema(
                institution_name="Bank Alpha",
                institution_bic="INVALID_BIC",  # Not 8 or 11 chars
                account_number="DE89370400440532013000",
            )

    def test_invalid_currency_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Invalid currency code"):
            ReportAccountSchema(
                institution_name="Bank Alpha",
                institution_bic="DEUTDEFFXXX",
                account_number="DE89370400440532013000",
                currency="123",  # 3 chars but not 3 uppercase letters
            )

    def test_zero_amount_transaction_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RegulatoryTransactionSchema(
                transaction_number="TX-ZERO",
                internal_ref_number="INT-ZERO",
                transaction_description="Zero amount test",
                date_transaction="2026-09-22T10:00:00",
                amount_local=Decimal("0.00"),  # Must be gt 0
                from_account=_make_account(),
                to_account=_make_account(),
            )

    def test_aif_report_with_fiu_ref(self, svc: FIURegulatoryService) -> None:
        req = CreateRegulatoryReportRequest(
            report_type=RegulatoryReportType.AIF,
            rentity_id="FIU-REG-DE-00142",
            rentity_branch="BRANCH_1",
            entity_reference="CASE-AIF-01",
            reporting_user="officer_aif",
            fiu_ref_number="FIU-PREV-REF-8842",
            reason="Additional beneficial ownership documentation submitted following FIU inquiry.",
            action_taken="DOCUMENTATION_ATTACHED",
            legal_basis=_make_legal_basis(),
            transactions=[],
        )
        rep = svc.create_report(req)
        assert rep.report_type == RegulatoryReportType.AIF
        assert rep.fiu_ref_number == "FIU-PREV-REF-8842"

        xml_str = svc.generate_goaml_4_xml(rep.report_id)
        assert "<fiu_ref_number>FIU-PREV-REF-8842</fiu_ref_number>" in xml_str

