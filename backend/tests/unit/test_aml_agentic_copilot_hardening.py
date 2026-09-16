"""Unit tests for Stage 43 AML Agentic Copilot & Evidence Assembly Hardening.

Covers:
- Evidence dossier assembly with registered artifacts and timeline events
- Deterministic PII sanitization via HMAC-SHA256 and dynamic zero_pii_verified checks
- Multi-tier disposition thresholds (MONITOR_ACCOUNT, CONFIRMED_SAR, ESCALATE_TO_FIU)
- Thread-safe concurrent execution
- Cryptographic lineage hash & evidence package hash determinism
- Router 404 validation on non-existent cases
- End-to-end integration with EvidenceRegistryService and CaseManagementService
- Direct /api/v1/copilot/generate-sar contract parity
"""

from __future__ import annotations

import concurrent.futures
import unittest

from fastapi.testclient import TestClient

from app.application.services.aml_agentic_copilot import AMLAgenticCopilot
from app.application.services.case_service import CaseManagementService, EvidenceRegistryService
from app.domain.enums import CasePriority
from app.main import app


class TestAMLAgenticCopilotHardening(unittest.TestCase):
    """Deep hardening tests for AML Agentic Copilot and Evidence Assembly."""

    def setUp(self) -> None:
        self.copilot = AMLAgenticCopilot()
        self.case_service = CaseManagementService()
        self.evidence_service = EvidenceRegistryService()
        self.client = TestClient(app)

    def test_evidence_assembly_with_artifacts_and_timeline(self) -> None:
        """Assert assemble_case_evidence packages timeline events and artifacts into a cryptographic dossier."""
        timeline = [
            {"event_type": "created", "description": "Case opened by analyst", "actor": "analyst_1", "timestamp": "2026-09-16T00:00:00Z"},
            {"event_type": "alert_linked", "description": "Linked alert alt_901", "actor": "system", "timestamp": "2026-09-16T00:05:00Z"},
        ]
        artifacts = [
            {"id": "ev_1", "title": "Wire SWIFT MT103 Log", "evidence_type": "SWIFT_PAYLOAD", "content_hash": "a1b2c3d4e5", "uploaded_by": "sec_agent", "uploaded_at": "2026-09-16T00:10:00Z"},
            {"id": "ev_2", "title": "Beneficiary Sanctions Screen", "evidence_type": "OFAC_SCREEN", "content_hash": "f6g7h8i9j0", "uploaded_by": "compliance_lead", "uploaded_at": "2026-09-16T00:12:00Z"},
        ]
        notes = ["Investigator observed layering pattern across offshore accounts."]

        dossier = self.copilot.assemble_case_evidence(
            case_id="case_hardened_01",
            case_title="Suspected Mule Network Layering",
            case_status="UNDER_INVESTIGATION",
            total_risk_score=780.0,
            alert_ids=["alt_901", "alt_902"],
            timeline_events=timeline,
            evidence_artifacts=artifacts,
            investigator_notes=notes,
        )

        self.assertEqual(dossier.case_id, "case_hardened_01")
        self.assertEqual(len(dossier.evidence_artifacts), 2)
        self.assertEqual(len(dossier.timeline_events), 2)
        self.assertEqual(len(dossier.investigator_notes), 1)
        self.assertIsNotNone(dossier.evidence_hash)
        self.assertEqual(len(dossier.evidence_hash), 64)  # Valid SHA-256

        analysis = self.copilot.synthesize_from_evidence(dossier)
        self.assertEqual(analysis.evidence_count, 2)
        self.assertEqual(analysis.timeline_event_count, 2)
        self.assertIn("2 formal evidence artifact(s)", analysis.fincen_sar_narrative)
        self.assertEqual(analysis.recommended_action, "CONFIRMED_SAR")

    def test_pii_detection_and_deterministic_masking(self) -> None:
        """Assert cleartext PII in investigator notes is deterministically replaced with HMAC tokens."""
        raw_notes = (
            "Subject John Doe with SSN 123-45-6789 and IBAN DE89370400440532013000 "
            "contacted via john.doe@suspicious-bank.com with phone +1 (555) 234-5678."
        )

        dossier = self.copilot.assemble_case_evidence(
            case_id="case_pii_01",
            case_title="PII Leakage Shield Test",
            total_risk_score=810.0,
            investigator_notes=raw_notes,
        )

        self.assertGreater(dossier.pii_sanitized_count, 0)
        sanitized_note = dossier.investigator_notes[0]

        # Verify no raw PII remains
        self.assertNotIn("123-45-6789", sanitized_note)
        self.assertNotIn("DE89370400440532013000", sanitized_note)
        self.assertNotIn("john.doe@suspicious-bank.com", sanitized_note)

        # Verify HMAC masked tokens exist
        self.assertIn("[MASKED_PII:SSN_TCKN:", sanitized_note)
        self.assertIn("[MASKED_PII:IBAN:", sanitized_note)
        self.assertIn("[MASKED_PII:EMAIL:", sanitized_note)

        # Verify synthesis honors sanitized notes and certifies zero PII
        analysis = self.copilot.synthesize_from_evidence(dossier)
        self.assertTrue(analysis.zero_pii_verified)
        self.assertNotIn("123-45-6789", analysis.four_eyes_briefing)
        self.assertIn("[MASKED_PII:", analysis.four_eyes_briefing)

    def test_zero_pii_verified_dynamic_calculation(self) -> None:
        """Assert zero_pii_verified is calculated dynamically and validates output cleanliness."""
        clean_dossier = self.copilot.assemble_case_evidence(
            case_id="case_clean_01",
            case_title="Sanitized Case",
            total_risk_score=700.0,
            investigator_notes="All customer accounts already pseudonymized with HMAC tokens.",
        )
        analysis = self.copilot.synthesize_from_evidence(clean_dossier)
        self.assertTrue(analysis.zero_pii_verified)

    def test_risk_score_disposition_thresholds(self) -> None:
        """Assert 3-tier risk calibration: MONITOR_ACCOUNT (<600), CONFIRMED_SAR (600-849), ESCALATE_TO_FIU (>=850)."""
        # 1. Low risk (<600)
        analysis_low = self.copilot.generate_case_narrative(
            case_id="case_thresh_low",
            case_title="Low Risk Flow",
            case_status="OPEN",
            alert_ids=["alt_01"],
            risk_score=420.0,
        )
        self.assertEqual(analysis_low.recommended_action, "MONITOR_ACCOUNT")
        self.assertIn("MONITOR_ACCOUNT", analysis_low.fincen_sar_narrative)

        # 2. High risk (600 - 849)
        analysis_mid = self.copilot.generate_case_narrative(
            case_id="case_thresh_mid",
            case_title="Elevated Risk Structuring",
            case_status="UNDER_INVESTIGATION",
            alert_ids=["alt_02"],
            risk_score=760.0,
        )
        self.assertEqual(analysis_mid.recommended_action, "CONFIRMED_SAR")
        self.assertIn("CONFIRMED_SAR", analysis_mid.fincen_sar_narrative)

        # 3. Critical risk (>=850)
        analysis_crit = self.copilot.generate_case_narrative(
            case_id="case_thresh_crit",
            case_title="Critical Laundering Flow",
            case_status="ESCALATED",
            alert_ids=["alt_03"],
            risk_score=940.0,
        )
        self.assertEqual(analysis_crit.recommended_action, "ESCALATE_TO_FIU")
        self.assertIn("ESCALATE_TO_FIU", analysis_crit.fincen_sar_narrative)
        self.assertIn("Critical", analysis_crit.four_eyes_briefing)

    def test_thread_safe_concurrent_synthesis(self) -> None:
        """Assert thread safety and atomic counter increments across concurrent worker threads."""
        initial_count = self.copilot.synthesized_analyses_count

        def _synthesize_task(idx: int) -> str:
            res = self.copilot.generate_case_narrative(
                case_id=f"case_thread_{idx}",
                case_title=f"Parallel Case {idx}",
                case_status="UNDER_INVESTIGATION",
                alert_ids=[f"alt_{idx}"],
                risk_score=700.0 + idx,
            )
            return res.lineage_hash

        num_tasks = 20
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_synthesize_task, i) for i in range(num_tasks)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), num_tasks)
        self.assertEqual(self.copilot.synthesized_analyses_count, initial_count + num_tasks)

    def test_lineage_hash_tamper_resistance(self) -> None:
        """Assert that altering case attributes produces a distinct cryptographic lineage hash."""
        dossier1 = self.copilot.assemble_case_evidence(
            case_id="case_hash_test",
            case_title="Lineage Integrity Test",
            total_risk_score=750.0,
        )
        analysis1 = self.copilot.synthesize_from_evidence(dossier1)

        dossier2 = self.copilot.assemble_case_evidence(
            case_id="case_hash_test",
            case_title="Lineage Integrity Test",
            total_risk_score=750.1,  # Small change in score
        )
        analysis2 = self.copilot.synthesize_from_evidence(dossier2)

        self.assertNotEqual(analysis1.lineage_hash, analysis2.lineage_hash)

    def test_router_missing_case_returns_404(self) -> None:
        """Assert endpoints return HTTP 404 when requested case does not exist (Vector 22 guard)."""
        res_post = self.client.post("/api/v1/cases/definitely_not_a_real_case_9999/copilot/narrative")
        self.assertEqual(res_post.status_code, 404)
        self.assertIn("not found", res_post.json()["detail"].lower())

        res_summary = self.client.get("/api/v1/cases/definitely_not_a_real_case_9999/copilot/summary")
        self.assertEqual(res_summary.status_code, 404)

        res_evidence = self.client.get("/api/v1/cases/definitely_not_a_real_case_9999/copilot/evidence")
        self.assertEqual(res_evidence.status_code, 404)

    def test_router_real_case_integration(self) -> None:
        """Assert router generates narrative with registered evidence for an active database case."""
        case = self.case_service.create_case(
            title="Real Case Copilot Integration",
            priority=CasePriority.P2_HIGH,
            alert_ids=["alt_real_10", "alt_real_20"],
            total_risk_score=790.0,
        )
        self.assertIsNotNone(case)

        # Register formal evidence artifact
        ev = self.evidence_service.register_evidence(
            case_id=case.id,
            evidence_type="SWIFT_MT103",
            title="SWIFT MT103 Layering Transfer",
            file_path="/var/data/evidence_swift_mt103.xml",
            content="<Document>Wire Transfer $85,000</Document>",
            uploaded_by="lead_investigator",
        )
        self.assertIsNotNone(ev)

        # 1. Call POST /{case_id}/copilot/narrative
        res_post = self.client.post(
            f"/api/v1/cases/{case.id}/copilot/narrative",
            json={"case_id": case.id, "include_fincen_narrative": True, "custom_investigator_notes": "Urgent review."},
        )
        self.assertEqual(res_post.status_code, 200)
        data = res_post.json()
        self.assertEqual(data["case_id"], case.id)
        self.assertEqual(data["recommended_action"], "CONFIRMED_SAR")
        self.assertEqual(data["evidence_count"], 1)
        self.assertTrue(data["zero_pii_verified"])
        self.assertIn("Paragraph 1: Introduction & Subject Overview", data["fincen_sar_narrative"])
        self.assertIn("Paragraph 5: Investigative Conclusion & Disposition", data["fincen_sar_narrative"])

        # 2. Call GET /{case_id}/copilot/summary
        res_summary = self.client.get(f"/api/v1/cases/{case.id}/copilot/summary")
        self.assertEqual(res_summary.status_code, 200)
        summary_data = res_summary.json()
        self.assertEqual(summary_data["case_id"], case.id)
        self.assertEqual(summary_data["evidence_count"], 1)
        self.assertIn("evidence_hash", summary_data)

        # 3. Call GET /{case_id}/copilot/evidence
        res_ev = self.client.get(f"/api/v1/cases/{case.id}/copilot/evidence")
        self.assertEqual(res_ev.status_code, 200)
        ev_data = res_ev.json()
        self.assertEqual(ev_data["case_id"], case.id)
        self.assertEqual(len(ev_data["evidence_artifacts"]), 1)
        self.assertEqual(ev_data["evidence_artifacts"][0]["title"], "SWIFT MT103 Layering Transfer")

    def test_direct_copilot_generate_sar_endpoint(self) -> None:
        """Assert /api/v1/copilot/generate-sar endpoint parity matching LandingPage.tsx contract."""
        payload = {
            "case_id": "direct_copilot_case_44",
            "shap_attributions": [
                {"feature": "rapid_structuring", "impact": 0.42, "description": "10 sub-$10k transfers in 2 hours"}
            ],
            "graph_nodes": {"connected_banks": ["Consortium Bank A", "Consortium Bank B"], "layering_hops": 4},
            "custom_investigator_notes": "Direct API trigger test.",
            "risk_score": 880.0,
        }

        # Test canonical prefix /api/v1/copilot/generate-sar
        res_api = self.client.post("/api/v1/copilot/generate-sar", json=payload)
        self.assertEqual(res_api.status_code, 200)
        data = res_api.json()
        self.assertEqual(data["case_id"], "direct_copilot_case_44")
        self.assertEqual(data["recommended_action"], "ESCALATE_TO_FIU")
        self.assertIn("rapid_structuring", str(data["top_risk_drivers"]))
        self.assertTrue(data["zero_pii_verified"])
        # Verify alias fields for LandingPage contract
        self.assertIn("sar_narrative", data)
        self.assertIn("supervisor_briefing", data)

        # Test legacy prefix /v1/copilot/generate-sar
        res_v1 = self.client.post("/v1/copilot/generate-sar", json=payload)
        self.assertEqual(res_v1.status_code, 200)
        self.assertEqual(res_v1.json()["case_id"], "direct_copilot_case_44")

        # Test evidence assembly endpoint
        res_ev = self.client.post("/api/v1/copilot/assemble-evidence", json=payload)
        self.assertEqual(res_ev.status_code, 200)
        self.assertIn("evidence_hash", res_ev.json())

    def test_evidence_dossier_hash_determinism(self) -> None:
        """Assert deterministic SHA-256 hashing for identical evidence inputs."""
        dossier1 = self.copilot.assemble_case_evidence(
            case_id="case_determ_01",
            case_title="Deterministic Test",
            total_risk_score=720.0,
            alert_ids=["alt_1", "alt_2"],
        )
        dossier2 = self.copilot.assemble_case_evidence(
            case_id="case_determ_01",
            case_title="Deterministic Test",
            total_risk_score=720.0,
            alert_ids=["alt_2", "alt_1"],  # Order varied, should sort canonically
        )
        self.assertEqual(dossier1.evidence_hash, dossier2.evidence_hash)


if __name__ == "__main__":
    unittest.main()
