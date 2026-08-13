"""Unit tests for Autonomous Agentic AML Copilot & RAG Narrative Generator."""

from __future__ import annotations

import unittest

from app.application.services.aml_agentic_copilot import AMLAgenticCopilot
from app.domain.value_objects_copilot import AMLCopilotAnalysis


class TestAMLAgenticCopilot(unittest.TestCase):
    """Test suite verifying AML Copilot FinCEN 5-paragraph SAR narrative and 4-Eyes briefing synthesis."""

    def setUp(self) -> None:
        self.copilot = AMLAgenticCopilot()

    def test_narrative_synthesis_structure(self) -> None:
        """Assert synthesized SAR narrative includes all 5 FinCEN mandatory regulatory paragraphs."""
        analysis = self.copilot.generate_case_narrative(
            case_id="case_sample_101",
            case_title="Suspicious Structuring & Layering Flow",
            case_status="UNDER_INVESTIGATION",
            alert_ids=["alt_001", "alt_002"],
            risk_score=820.0,
        )

        self.assertIsInstance(analysis, AMLCopilotAnalysis)
        self.assertEqual(analysis.case_id, "case_sample_101")
        self.assertTrue(analysis.zero_pii_verified)
        self.assertIsNotNone(analysis.lineage_hash)

        narrative = analysis.fincen_sar_narrative
        self.assertIn("Paragraph 1: Introduction & Subject Overview", narrative)
        self.assertIn("Paragraph 2: Financial Mechanism & Transaction Hops", narrative)
        self.assertIn("Paragraph 3: SHAP Risk Attributions & Anomaly Drivers", narrative)
        self.assertIn("Paragraph 4: Graph Topology & Community Clusters", narrative)
        self.assertIn("Paragraph 5: Investigative Conclusion & Disposition", narrative)

    def test_four_eyes_briefing_generation(self) -> None:
        """Assert supervisor 4-Eyes briefing contains composite score and recommended action."""
        analysis = self.copilot.generate_case_narrative(
            case_id="case_sample_102",
            case_title="Cross-Border Wire Layering",
            case_status="ESCALATED",
            alert_ids=["alt_003"],
            risk_score=750.0,
            investigator_notes="High volume rapid transfer observed.",
        )

        briefing = analysis.four_eyes_briefing
        self.assertIn("BSA/AML Supervisor 4-Eyes Briefing", briefing)
        self.assertIn("CONFIRMED_SAR", analysis.recommended_action)
        self.assertIn("750.0", briefing)
        self.assertIn("High volume rapid transfer observed.", briefing)

    def test_low_risk_score_recommends_monitoring(self) -> None:
        """Assert low composite risk score (<600) recommends account monitoring instead of SAR filing."""
        analysis = self.copilot.generate_case_narrative(
            case_id="case_sample_103",
            case_title="Minor Velocity Variance",
            case_status="OPEN",
            alert_ids=["alt_004"],
            risk_score=450.0,
        )

        self.assertEqual(analysis.recommended_action, "MONITOR_ACCOUNT")
        self.assertIn("MONITOR_ACCOUNT", analysis.fincen_sar_narrative)


if __name__ == "__main__":
    unittest.main()
