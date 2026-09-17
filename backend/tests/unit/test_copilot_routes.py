"""Unit tests for AML Copilot and Evidence Assembly Presentation Routes.

Validates:
- Dual-prefix routing: /api/v1/copilot and /v1/copilot
- Direct SAR narrative generation and cryptographic evidence assembly
- 404 Not Found validation when require_existing_case=True or querying case-specific endpoints
- 200 OK integration for active cases registered in CaseManagementService
- Input validation bounds (empty case_id, risk_score 0-1000)
- Zero-PII sanitization on investigator notes
- Copilot status and health endpoints
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.application.services.case_service import CaseManagementService, EvidenceRegistryService
from app.domain.enums import CasePriority
from app.main import app


class TestCopilotRoutes(unittest.TestCase):
    """Integration and contract test suite for copilot presentation endpoints."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self.case_service = CaseManagementService()
        self.evidence_service = EvidenceRegistryService()

    def test_copilot_status_and_health_dual_routing(self) -> None:
        """Assert status and health endpoints return 200 with operational metrics across both prefixes."""
        for path in [
            "/api/v1/copilot/status",
            "/v1/copilot/status",
            "/api/v1/copilot/health",
            "/v1/copilot/health",
        ]:
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, f"Failed on path {path}")
            data = res.json()
            self.assertEqual(data["status"], "active")
            self.assertEqual(data["zero_pii_engine"], "operational")
            self.assertIn("synthesized_analyses_count", data)
            self.assertIn("timestamp", data)

    def test_direct_generate_sar_dual_routing(self) -> None:
        """Assert /generate-sar works across canonical /api/v1 and legacy /v1 prefixes."""
        payload = {
            "case_id": "copilot_adhoc_case_101",
            "shap_attributions": [
                {"feature": "structuring_velocity", "impact": 0.45, "description": "High structuring"}
            ],
            "graph_nodes": {"connected_banks": ["Bank Alpha", "Bank Beta"], "layering_hops": 3},
            "custom_investigator_notes": "Ad-hoc simulation run.",
            "risk_score": 770.0,
        }

        for path in ["/api/v1/copilot/generate-sar", "/v1/copilot/generate-sar"]:
            res = self.client.post(path, json=payload)
            self.assertEqual(res.status_code, 200, f"Failed on path {path}")
            data = res.json()
            self.assertEqual(data["case_id"], "copilot_adhoc_case_101")
            self.assertEqual(data["recommended_action"], "CONFIRMED_SAR")
            self.assertTrue(data["zero_pii_verified"])
            self.assertIn("lineage_hash", data)
            # Verify alias fields
            self.assertIn("sar_narrative", data)
            self.assertIn("supervisor_briefing", data)
            self.assertEqual(data["sar_narrative"], data["fincen_sar_narrative"])

    def test_direct_assemble_evidence_dual_routing(self) -> None:
        """Assert /assemble-evidence returns cryptographic evidence package across both prefixes."""
        payload = {
            "case_id": "copilot_ev_case_102",
            "risk_score": 860.0,
            "custom_investigator_notes": "Layered wire evidence collected.",
        }

        for path in ["/api/v1/copilot/assemble-evidence", "/v1/copilot/assemble-evidence"]:
            res = self.client.post(path, json=payload)
            self.assertEqual(res.status_code, 200, f"Failed on path {path}")
            data = res.json()
            self.assertEqual(data["case_id"], "copilot_ev_case_102")
            self.assertIn("evidence_hash", data)
            self.assertIn("assembled_at", data)
            self.assertEqual(data["total_risk_score"], 860.0)

    def test_require_existing_case_404_validation(self) -> None:
        """Assert direct endpoints return 404 when require_existing_case=True and case does not exist."""
        payload = {
            "case_id": "definitely_non_existent_case_9999",
            "require_existing_case": True,
            "risk_score": 800.0,
        }

        res_sar = self.client.post("/api/v1/copilot/generate-sar", json=payload)
        self.assertEqual(res_sar.status_code, 404)
        self.assertIn("not found in case registry", res_sar.json()["detail"].lower())

        res_ev = self.client.post("/api/v1/copilot/assemble-evidence", json=payload)
        self.assertEqual(res_ev.status_code, 404)
        self.assertIn("not found in case registry", res_ev.json()["detail"].lower())

    def test_case_linked_endpoints_404_when_case_missing(self) -> None:
        """Assert case-specific copilot endpoints return 404 for unknown cases."""
        missing_id = "missing_copilot_case_404"
        for path in [
            f"/api/v1/copilot/cases/{missing_id}/evidence",
            f"/v1/copilot/cases/{missing_id}/evidence",
            f"/api/v1/copilot/cases/{missing_id}/summary",
            f"/v1/copilot/cases/{missing_id}/summary",
        ]:
            res = self.client.get(path)
            self.assertEqual(res.status_code, 404, f"Expected 404 on GET {path}")
            self.assertIn("not found in case registry", res.json()["detail"].lower())

        for path in [
            f"/api/v1/copilot/cases/{missing_id}/narrative",
            f"/v1/copilot/cases/{missing_id}/narrative",
        ]:
            res = self.client.post(path, json={"case_id": missing_id})
            self.assertEqual(res.status_code, 404, f"Expected 404 on POST {path}")

    def test_case_linked_endpoints_200_with_active_case(self) -> None:
        """Assert case-specific copilot endpoints return 200 and accurate dossier for registered case."""
        case = self.case_service.create_case(
            title="Copilot Registered Flow",
            priority=CasePriority.P2_HIGH,
            alert_ids=["alt_cp_101", "alt_cp_102"],
            total_risk_score=890.0,
        )
        self.assertIsNotNone(case)

        # Register evidence
        self.evidence_service.register_evidence(
            case_id=case.id,
            evidence_type="TRANSACTION_RECORD",
            title="Suspicious Structuring Batch",
            file_path="/var/data/structuring_batch.json",
            content="Transaction Structuring Evidence",
            uploaded_by="lead_investigator",
        )

        # 1. GET /cases/{case_id}/evidence
        res_ev = self.client.get(f"/api/v1/copilot/cases/{case.id}/evidence")
        self.assertEqual(res_ev.status_code, 200)
        ev_data = res_ev.json()
        self.assertEqual(ev_data["case_id"], case.id)
        self.assertEqual(ev_data["evidence_count"], 1)
        self.assertIn("evidence_hash", ev_data)

        # 2. POST /cases/{case_id}/narrative
        res_nar = self.client.post(
            f"/api/v1/copilot/cases/{case.id}/narrative",
            json={"case_id": case.id, "custom_investigator_notes": "Supervisor please review immediately."},
        )
        self.assertEqual(res_nar.status_code, 200)
        nar_data = res_nar.json()
        self.assertEqual(nar_data["case_id"], case.id)
        self.assertEqual(nar_data["recommended_action"], "ESCALATE_TO_FIU")
        self.assertTrue(nar_data["zero_pii_verified"])
        self.assertEqual(nar_data["evidence_count"], 1)

        # 3. GET /cases/{case_id}/summary
        res_sum = self.client.get(f"/api/v1/copilot/cases/{case.id}/summary")
        self.assertEqual(res_sum.status_code, 200)
        sum_data = res_sum.json()
        self.assertEqual(sum_data["case_id"], case.id)
        self.assertEqual(sum_data["recommended_action"], "ESCALATE_TO_FIU")

    def test_validation_bounds(self) -> None:
        """Assert input validation bounds on case_id and risk_score."""
        # Empty case_id
        res_empty = self.client.post("/api/v1/copilot/generate-sar", json={"case_id": "   "})
        self.assertEqual(res_empty.status_code, 422)

        # Out of bounds risk_score (> 1000.0)
        res_oob_high = self.client.post(
            "/api/v1/copilot/generate-sar",
            json={"case_id": "valid_id", "risk_score": 1500.0},
        )
        self.assertEqual(res_oob_high.status_code, 422)

        # Out of bounds risk_score (< 0.0)
        res_oob_low = self.client.post(
            "/api/v1/copilot/generate-sar",
            json={"case_id": "valid_id", "risk_score": -10.0},
        )
        self.assertEqual(res_oob_low.status_code, 422)

    def test_zero_pii_sanitization_in_narrative(self) -> None:
        """Assert raw PII in notes is masked with HMAC tokens and zero_pii_verified is True."""
        raw_notes = "Subject SSN 987-65-4321, email test.analyst@dark-web.io, IBAN TR330006100519786452100001"
        payload = {
            "case_id": "copilot_pii_shield_case",
            "custom_investigator_notes": raw_notes,
            "risk_score": 820.0,
        }

        res = self.client.post("/api/v1/copilot/generate-sar", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["zero_pii_verified"])
        narrative = data["fincen_sar_narrative"]
        briefing = data["four_eyes_briefing"]

        # Ensure no raw PII leaked into narrative or briefing
        self.assertNotIn("987-65-4321", narrative)
        self.assertNotIn("test.analyst@dark-web.io", narrative)
        self.assertNotIn("TR330006100519786452100001", narrative)
        self.assertNotIn("987-65-4321", briefing)


if __name__ == "__main__":
    unittest.main()
