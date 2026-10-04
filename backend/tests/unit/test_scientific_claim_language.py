"""Unit tests for Scientific Claim Language Refinement & Hyperbole Elimination.

Verifies that documentation, specifications, code, and UI components adhere to rigorous
scientific terminology, eliminating marketing superlatives ('bank-grade', 'unhackable',
'100% secure', 'tamper-proof', 'guaranteed defense') in favor of grounded engineering
terms ('tamper-evident', 'information-theoretic boundary', 'bounded differential privacy').
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
README_PATH = REPO_ROOT / "README.md"
DOCS_DIR = REPO_ROOT / "docs"
GRAPH_COMPONENT_PATH = (
    REPO_ROOT / "frontend" / "src" / "components" / "network" / "CrossBankTopologyGraph.tsx"
)


class TestScientificClaimLanguageIntegrity:
    """Automated verification suite for scientific language standardization and anti-hyperbole invariants."""

    def test_no_bank_grade_superlatives_in_documentation(self) -> None:
        """Assert 'bank-grade' does not appear as an affirmative claim in README or specs."""
        content = README_PATH.read_text(encoding="utf-8")
        assert "bank-grade" not in content.lower(), "'bank-grade' found in README.md"

        # Check key specs, ignoring engineering-audit which documents its removal
        for spec in ["architecture.md", "system_design.md", "threat_model.md"]:
            spec_file = DOCS_DIR / spec
            if spec_file.is_file():
                spec_content = spec_file.read_text(encoding="utf-8")
                assert "bank-grade" not in spec_content.lower(), f"'bank-grade' found in {spec}"

    def test_no_unhackable_or_bulletproof_claims(self) -> None:
        """Assert ungrounded absolutes ('unhackable', 'bulletproof', 'foolproof') do not appear in docs."""
        prohibited = [r"\bunhackable\b", r"\bbulletproof\b", r"\bfoolproof\b", r"\bunbreakable\b"]
        pattern = re.compile("|".join(prohibited), re.IGNORECASE)

        for md_file in [README_PATH, REPO_ROOT / "SYSTEM_CARD.md", REPO_ROOT / "MODEL_CARD.md"]:
            if md_file.is_file():
                content = md_file.read_text(encoding="utf-8")
                matches = pattern.findall(content)
                assert not matches, f"Prohibited terms {matches} found in {md_file.name}"

    def test_no_100_percent_secure_marketing_hyperbole(self) -> None:
        """Assert '100% SECURE' is purged from frontend UI components and documentation."""
        if GRAPH_COMPONENT_PATH.is_file():
            content = GRAPH_COMPONENT_PATH.read_text(encoding="utf-8")
            assert "100% SECURE" not in content, "Found '100% SECURE' in CrossBankTopologyGraph.tsx"
            assert "ZERO RAW PII" in content, "Expected 'ZERO RAW PII' badge in CrossBankTopologyGraph.tsx"

    def test_tamper_evident_not_tamper_proof_in_audit_docs(self) -> None:
        """Assert hash-chained logs use 'tamper-evident' rather than 'tamper-proof'."""
        audit_docs = [
            DOCS_DIR / "aml-platform.md",
            DOCS_DIR / "threat_model.md",
            DOCS_DIR / "data_retention_policy_spec.md",
            DOCS_DIR / "collaborative_aml_architecture.md",
        ]
        for doc in audit_docs:
            if doc.is_file():
                content = doc.read_text(encoding="utf-8")
                assert "tamper-proof" not in content.lower(), f"'tamper-proof' found in {doc.name}"
                assert "tamper-evident" in content.lower(), f"Expected 'tamper-evident' in {doc.name}"

    def test_differential_privacy_formulation_is_bounded_not_guaranteed(self) -> None:
        """Assert DP algorithm spec uses 'Information-Theoretic Defense Boundary'."""
        dp_doc = DOCS_DIR / "algorithms" / "differential_privacy.md"
        assert dp_doc.is_file()
        content = dp_doc.read_text(encoding="utf-8")
        assert "Guaranteed Defense" not in content
        assert "Information-Theoretic Defense Boundary" in content

    def test_unlearning_formulation_is_bounded_not_guaranteed(self) -> None:
        """Assert Enterprise Privacy Policy uses bounded DP terminology."""
        priv_doc = DOCS_DIR / "legal" / "enterprise_privacy_policy.md"
        assert priv_doc.is_file()
        content = priv_doc.read_text(encoding="utf-8")
        assert "Mathematical Differential Privacy Bounds" in content
        assert "bounded under the empirical" in content

    def test_memory_and_concurrency_claims_use_precise_engineering_terms(self) -> None:
        """Assert README memory and concurrency claims avoid marketing hyperbole."""
        content = README_PATH.read_text(encoding="utf-8")
        assert "maintaining bounded resident memory footprint" in content
        assert "enforcing race-free multi-tenant concurrency" in content

    def test_membership_inference_claim_uses_empirical_evaluation(self) -> None:
        """Assert README Section 6 unlearning description uses 'Empirical MIA Evaluation'."""
        content = README_PATH.read_text(encoding="utf-8")
        assert "Empirical MIA Evaluation" in content
        assert "mathematically verified" in content

    def test_system_card_ascii_box_alignment_preserved(self) -> None:
        """Assert SYSTEM_CARD.md uses 'Tamper-Evident' while preserving exact line width."""
        sc_file = REPO_ROOT / "SYSTEM_CARD.md"
        assert sc_file.is_file()
        lines = sc_file.read_text(encoding="utf-8").splitlines()
        target_line = next(line for line in lines if "Tamper-Evident" in line)
        assert target_line.endswith("│")
        assert target_line.startswith("│")
        # Line 67 inner box label check
        assert "SHA-256 Tamper-Evident Audit" in target_line

    def test_cross_bank_topology_graph_uses_zero_raw_pii_badge(self) -> None:
        """Assert CrossBankTopologyGraph uses technical indicator 'ZERO RAW PII'."""
        content = GRAPH_COMPONENT_PATH.read_text(encoding="utf-8")
        assert "ZERO RAW PII" in content
        assert "Zero raw PII or cross-bank edge leakage" in content
