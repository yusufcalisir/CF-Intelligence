"""Unit Tests for Regulatory SAR & ISO 20022 XML Generator & Schema Validation (Phase 25.1).

Validates:
1. FinCEN BSA 2.0 SAR XML generation and strict XSD schema validation.
2. UNODC goAML 4.0 XML generation and strict XSD schema validation.
3. ISO 20022 XML generation (pacs.008, pacs.002, camt.053) and schema validation.
4. Regulatory framing as schema-conforming export prototypes (PROTOTYPE_EXPORT).
5. XXE and DTD injection defenses.
6. Cryptographic SHA-256 integrity hash verification and thread-safe caching.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.domain.sar_generator import (
    ExportClassification,
    MissingRegulatoryFieldError,
    RegulatoryFramework,
    RegulatoryValidationReport,
    SARGenerator,
    SARSchemaValidationError,
    XXEInjectionDetectedError,
)


class TestFinCENSARXMLValidation:
    """Tests for FinCEN BSA 2.0 SAR XML generation and schema validation."""

    def test_generate_fincen_sar_xml_valid(self) -> None:
        """Verifies clean generation and XSD schema validation of FinCEN BSA 2.0 SAR XML."""
        xml_str, report = SARGenerator.generate_fincen_sar_xml(
            case_id="CASE-2026-98492",
            reporting_institution="Consortium Joint Investigation Unit",
            tin_type="EIN",
            activity_status="CONFIRMED_FRAUD",
            total_risk_score=925.50,
            priority="CRITICAL",
            summary="Coordinated multi-bank mule layering pattern detected across 3 institutions.",
            subjects=["hash_subj_alpha123456789", "hash_subj_beta987654321"],
            alert_ids=["ALT-001", "ALT-002", "ALT-003"],
            notes=[
                {
                    "author": "COMPLIANCE_LEAD_01",
                    "content": "Four-Eyes dual signoff verified: all 3 hops confirmed fraudulent.",
                    "timestamp": "2026-09-28T10:00:00Z",
                }
            ],
            timeline=[
                {
                    "event_type": "DETECTION",
                    "description": "Federated GNN model flagged high-risk ring topology.",
                    "actor": "CF-Intelligence-Engine",
                    "timestamp": "2026-09-28T09:30:00Z",
                }
            ],
            classification=ExportClassification.PROTOTYPE_EXPORT,
        )

        assert report.is_valid is True
        assert len(report.errors) == 0
        assert report.framework == RegulatoryFramework.FINCEN_SAR_2_0
        assert report.classification == ExportClassification.PROTOTYPE_EXPORT
        assert "<EFilingSubmission" in xml_str
        assert "CASE-2026-98492" in xml_str
        assert "hash_subj_alpha123456789" in xml_str
        assert "925.50" in xml_str
        assert report.sha256_hash == hashlib.sha256(xml_str.strip().encode("utf-8")).hexdigest()

    def test_fincen_sar_xml_missing_case_id(self) -> None:
        """Verifies that omitting case_id raises MissingRegulatoryFieldError."""
        with pytest.raises(MissingRegulatoryFieldError, match="case_id is required"):
            SARGenerator.generate_fincen_sar_xml(case_id="   ")

    def test_fincen_sar_xml_schema_rejection_on_invalid_structure(self) -> None:
        """Verifies that malformed XML structures violating FinCEN_SAR_2.0.xsd are rejected."""
        malformed_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <EFilingSubmission xmlns="http://www.fincen.gov/spec/bsa">
          <InvalidHeaderTag>
            <ActivityType>SAR</ActivityType>
          </InvalidHeaderTag>
        </EFilingSubmission>"""

        report = SARGenerator.validate_xml(
            malformed_xml,
            RegulatoryFramework.FINCEN_SAR_2_0,
            raise_on_error=False,
        )
        assert report.is_valid is False
        assert len(report.errors) > 0

        with pytest.raises(SARSchemaValidationError):
            SARGenerator.validate_xml(
                malformed_xml,
                RegulatoryFramework.FINCEN_SAR_2_0,
                raise_on_error=True,
            )


class TestUNODCgoAMLValidation:
    """Tests for UNODC goAML 4.0 XML report generation and schema validation."""

    def test_generate_goaml_sar_xml_valid(self) -> None:
        """Verifies clean generation and XSD schema validation of UNODC goAML 4.0 XML."""
        xml_str, report = SARGenerator.generate_goaml_sar_xml(
            rentity_id="BANK-ALPHA-DE",
            entity_reference="CASE-GOAML-2026-001",
            report_code="SAR",
            submission_code="E",
            rentity_branch="FRANKFURT_HQ",
            reporting_user="MLRO_OFFICER_04",
            reason="Unusual high-frequency cross-border transfers to offshore shell accounts.",
            action_taken="Accounts frozen under AMLD6 Art. 33 emergency provisions.",
            currency_code="EUR",
            transactions=[
                {
                    "transaction_number": "TX-2026-001",
                    "date_transaction": "2026-09-28T08:15:00Z",
                    "transmode_code": "EFT",
                    "amount_local": 250000.00,
                    "currency_code": "EUR",
                    "t_from": {
                        "from_funds_code": "WIRE",
                        "from_account": {
                            "institution_name": "Bank Alpha Germany",
                            "institution_code": "BKALDEFFXXX",
                            "account": "DE89370400440532013000",
                        },
                    },
                    "t_to": {
                        "to_funds_code": "WIRE",
                        "to_account": {
                            "institution_name": "Bank Beta France",
                            "institution_code": "BNPAFRPPXXX",
                            "account": "FR1420041010050500013M02606",
                        },
                    },
                }
            ],
            classification=ExportClassification.PROTOTYPE_EXPORT,
        )

        assert report.is_valid is True
        assert len(report.errors) == 0
        assert report.framework == RegulatoryFramework.UNODC_GOAML_4_0
        assert "<report>" in xml_str
        assert "BANK-ALPHA-DE" in xml_str
        assert "250000.00" in xml_str

    def test_goaml_xml_missing_mandatory_fields(self) -> None:
        """Verifies that omitting rentity_id or entity_reference raises MissingRegulatoryFieldError."""
        with pytest.raises(MissingRegulatoryFieldError, match="rentity_id is mandatory"):
            SARGenerator.generate_goaml_sar_xml(rentity_id="", entity_reference="CASE-01")

        with pytest.raises(MissingRegulatoryFieldError, match="entity_reference is mandatory"):
            SARGenerator.generate_goaml_sar_xml(rentity_id="BANK-01", entity_reference="")

    def test_goaml_xml_invalid_currency_code_rejected(self) -> None:
        """Verifies that an invalid 4-letter or lowercase currency code is rejected by goAML schema."""
        bad_currency_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <report>
          <rentity_id>BANK-01</rentity_id>
          <submission_code>E</submission_code>
          <report_code>SAR</report_code>
          <entity_reference>CASE-01</entity_reference>
          <report_date>2026-09-28T10:00:00Z</report_date>
          <currency_code_local>INVALID_CCY</currency_code_local>
          <reporting_user>OFFICER_01</reporting_user>
          <reason>Test reason</reason>
          <action>Test action</action>
        </report>"""

        report = SARGenerator.validate_xml(
            bad_currency_xml,
            RegulatoryFramework.UNODC_GOAML_4_0,
            raise_on_error=False,
        )
        assert report.is_valid is False
        assert any("currency_code_local" in err or "INVALID_CCY" in err or "pattern" in err for err in report.errors)


class TestISO20022XMLGeneratorsAndValidation:
    """Tests for ISO 20022 pacs.008, pacs.002, and camt.053 generation and validation."""

    def test_generate_iso20022_pacs008_valid(self) -> None:
        """Verifies pacs.008.001.08 credit transfer XML generation and schema validation."""
        xml_str, report = SARGenerator.generate_iso20022_pacs008_xml(
            msg_id="MSG-PACS008-2026-001",
            debtor_iban="DE89370400440532013000",
            creditor_iban="FR1420041010050500013M02606",
            amount=75000.00,
            currency="EUR",
            debtor_name="Originator Holding GmbH",
            creditor_name="Beneficiary Logistics SAS",
            debtor_country="DE",
            creditor_country="FR",
        )
        assert report.is_valid is True
        assert len(report.errors) == 0
        assert report.framework == RegulatoryFramework.ISO_20022_PACS008
        assert "MSG-PACS008-2026-001" in xml_str
        assert "75000.00" in xml_str

    def test_generate_iso20022_pacs008_negative_amount_rejected(self) -> None:
        """Verifies that non-positive pacs.008 amounts raise MissingRegulatoryFieldError."""
        with pytest.raises(MissingRegulatoryFieldError, match="strictly positive"):
            SARGenerator.generate_iso20022_pacs008_xml(
                msg_id="MSG-001",
                debtor_iban="DE89370400440532013000",
                creditor_iban="FR1420041010050500013M02606",
                amount=-100.0,
            )

    def test_generate_iso20022_pacs002_valid(self) -> None:
        """Verifies pacs.002.001.10 payment status report generation and validation."""
        xml_str, report = SARGenerator.generate_iso20022_pacs002_xml(
            msg_id="MSG-PACS002-2026-001",
            orig_msg_id="MSG-PACS008-2026-001",
            status="ACTC",
            amount=75000.00,
            currency="EUR",
            reason_code="AcceptedSettlementCompleted",
        )
        assert report.is_valid is True
        assert report.framework == RegulatoryFramework.ISO_20022_PACS002
        assert "MSG-PACS002-2026-001" in xml_str
        assert "ACTC" in xml_str

    def test_generate_iso20022_camt053_valid(self) -> None:
        """Verifies camt.053.001.08 bank-to-customer statement XML generation and validation."""
        entries = [
            {
                "ntry_ref": "NTRY-001",
                "amount": 12500.00,
                "currency": "EUR",
                "credit_debit": "CRDT",
                "status": "BOOK",
                "debtor_name": "Supplier A",
                "debtor_iban": "DE89370400440532013000",
                "creditor_name": "Merchant B",
                "creditor_iban": "FR1420041010050500013M02606",
            },
            {
                "ntry_ref": "NTRY-002",
                "amount": 45000.00,
                "currency": "EUR",
                "credit_debit": "DBIT",
                "status": "BOOK",
                "debtor_name": "Client C",
                "debtor_iban": "DE89370400440532013000",
                "creditor_name": "Logistics D",
                "creditor_iban": "NL91ABNA0417164300",
            },
        ]
        xml_str, report = SARGenerator.generate_iso20022_camt053_xml(
            stmt_id="STMT-2026-OCT-01",
            account_iban="DE89370400440532013000",
            entries=entries,
        )
        assert report.is_valid is True
        assert report.framework == RegulatoryFramework.ISO_20022_CAMT053
        assert "STMT-2026-OCT-01" in xml_str
        assert "NTRY-001" in xml_str
        assert "NTRY-002" in xml_str


class TestSecurityInvariantsAndFraming:
    """Tests for XXE injection defenses, prototype framing, and hash immutability."""

    def test_xxe_external_entity_injection_blocked(self) -> None:
        """Verifies that XXE entity expansion attacks are blocked with XXEInjectionDetectedError."""
        xxe_payload = """<?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE test [
          <!ENTITY xxe SYSTEM "file:///etc/passwd">
        ]>
        <EFilingSubmission xmlns="http://www.fincen.gov/spec/bsa">
          <SubmissionHeader>&xxe;</SubmissionHeader>
        </EFilingSubmission>"""

        with pytest.raises(XXEInjectionDetectedError, match="Forbidden XML External Entity"):
            SARGenerator.validate_xml(xxe_payload, RegulatoryFramework.FINCEN_SAR_2_0)

    def test_dtd_public_entity_injection_blocked(self) -> None:
        """Verifies that public DTD entities are blocked."""
        dtd_payload = """<?xml version="1.0"?>
        <!DOCTYPE foo PUBLIC "-//OASIS//DTD DocBook XML V4.1.2//EN" "http://attacker.com/evil.dtd">
        <report><rentity_id>1</rentity_id></report>"""

        with pytest.raises(XXEInjectionDetectedError):
            SARGenerator.validate_xml(dtd_payload, RegulatoryFramework.UNODC_GOAML_4_0)

    def test_empty_xml_payload_rejected(self) -> None:
        """Verifies that empty string or whitespace XML payload raises MissingRegulatoryFieldError."""
        with pytest.raises(MissingRegulatoryFieldError, match="XML payload is empty"):
            SARGenerator.validate_xml("   ", RegulatoryFramework.FINCEN_SAR_2_0)

    def test_prototype_framing_disclaimer_attached(self) -> None:
        """Verifies that all validation reports carry the authoritative prototype disclaimer."""
        _, report = SARGenerator.generate_fincen_sar_xml("CASE-DISCLAIMER-01")
        assert "SCHEMA-CONFORMING EXPORT PROTOTYPE" in report.framing_disclaimer
        assert "supervisory sandbox evaluation" in report.framing_disclaimer

    def test_report_serialization(self) -> None:
        """Verifies that RegulatoryValidationReport serializes cleanly to a dictionary."""
        report = RegulatoryValidationReport(
            is_valid=True,
            schema_name="FinCEN_SAR_2.0",
            framework=RegulatoryFramework.FINCEN_SAR_2_0,
            classification=ExportClassification.PROTOTYPE_EXPORT,
            sha256_hash="abcdef0123456789",
            errors=[],
            xml_byte_count=1024,
            framing_disclaimer=SARGenerator.REGULATORY_PROTOTYPE_DISCLAIMER,
        )
        data = report.to_dict()
        assert data["is_valid"] is True
        assert data["framework"] == "FinCEN_SAR_2.0"
        assert data["classification"] == "PROTOTYPE_EXPORT"
        assert data["sha256_hash"] == "abcdef0123456789"
        assert data["xml_byte_count"] == 1024

    def test_thread_safe_schema_cache(self) -> None:
        """Verifies that multi-threaded concurrent access to schema compilation is thread-safe."""
        schemas_to_test = [
            RegulatoryFramework.FINCEN_SAR_2_0,
            RegulatoryFramework.UNODC_GOAML_4_0,
            RegulatoryFramework.ISO_20022_PACS008,
            RegulatoryFramework.ISO_20022_PACS002,
            RegulatoryFramework.ISO_20022_CAMT053,
        ]

        def worker(framework: RegulatoryFramework) -> bool:
            compiled = SARGenerator.get_compiled_schema(framework)
            return compiled is not None

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, framework) for framework in schemas_to_test * 4]
            results = [f.result() for f in futures]

        assert all(results)
