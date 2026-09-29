"""Unit Test Suite for Software Correctness vs Scientific Generalization Taxonomy.

Validates that:
1. docs/verification_taxonomy_spec.md exists and contains the authoritative dual-axis framework.
2. Both axes (Software Correctness vs Scientific Generalization) are rigorously defined with
   distinct failure modes, verification tooling, oracles, and regulatory mappings.
3. docs/architecture.md, docs/engineering-audit.md, and README.md maintain synchronized
   sections cross-referencing the taxonomy specification.
4. Physical repository segregation between Axis 1 (backend/tests/) and Axis 2 (benchmarks/, experiments/)
   is strictly enforced.
5. Federal Reserve SR 11-7, OCC 2011-12, and EU AI Act Annex IV regulatory mappings are present.
6. KaTeX mathematical formatting satisfies strict repository parser standards.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestVerificationTaxonomyIntegrity:
    """Validates structural and content integrity of the verification taxonomy."""

    def test_verification_taxonomy_spec_file_exists(self) -> None:
        """Verify docs/verification_taxonomy_spec.md exists and is non-empty."""
        spec_path = REPO_ROOT / "docs" / "verification_taxonomy_spec.md"
        assert spec_path.exists(), "docs/verification_taxonomy_spec.md must exist"
        content = spec_path.read_text(encoding="utf-8")
        assert len(content) > 1000, "Taxonomy specification must be non-trivial"

    def test_taxonomy_spec_core_sections_present(self) -> None:
        """Verify all mandated sections are present in docs/verification_taxonomy_spec.md."""
        spec_path = REPO_ROOT / "docs" / "verification_taxonomy_spec.md"
        content = spec_path.read_text(encoding="utf-8")

        required_sections = [
            "## 1. Executive Summary & Epistemological Mandate",
            "## 2. Comparative Epistemological Matrix",
            "## 3. Axis 1: Software Correctness Specification",
            "## 4. Axis 2: Scientific Generalization Specification",
            "## 5. Architectural Separation in Repository Structure",
            "## 6. Regulatory Model Risk Governance Alignment",
        ]
        for section in required_sections:
            assert section in content, f"Missing section: {section}"

    def test_taxonomy_matrix_dual_axis_structure(self) -> None:
        """Verify the comparative matrix delineates Axis 1 and Axis 2 across core dimensions."""
        spec_path = REPO_ROOT / "docs" / "verification_taxonomy_spec.md"
        content = spec_path.read_text(encoding="utf-8")

        assert "Axis 1: Software Correctness" in content
        assert "Axis 2: Scientific Generalization" in content
        assert "Deterministic Binary Oracle" in content
        assert "Stochastic Continuous Oracle" in content
        assert "Epistemic Danger" in content or "Epistemic Limit" in content

    def test_regulatory_framework_mapping_present(self) -> None:
        """Verify SR 11-7, OCC 2011-12, and EU AI Act Annex IV regulatory cross-references."""
        spec_path = REPO_ROOT / "docs" / "verification_taxonomy_spec.md"
        content = spec_path.read_text(encoding="utf-8")

        assert "SR 11-7" in content
        assert "OCC" in content
        assert "EU AI Act" in content or "2024/1689" in content
        assert "Annex IV" in content

    def test_architecture_doc_section_18_present(self) -> None:
        """Verify docs/architecture.md contains Section 18 for the verification taxonomy."""
        arch_path = REPO_ROOT / "docs" / "architecture.md"
        assert arch_path.exists(), "docs/architecture.md must exist"
        content = arch_path.read_text(encoding="utf-8")

        assert "## 18. Software Correctness vs. Scientific Generalization Taxonomy" in content
        assert "verification_taxonomy_spec.md" in content
        assert "DUAL-AXIS EVALUATION & GOVERNANCE FRAMEWORK" in content

    def test_engineering_audit_doc_taxonomy_present(self) -> None:
        """Verify docs/engineering-audit.md contains Subsection 3.1 for the verification taxonomy."""
        audit_path = REPO_ROOT / "docs" / "engineering-audit.md"
        assert audit_path.exists(), "docs/engineering-audit.md must exist"
        content = audit_path.read_text(encoding="utf-8")

        assert "### 3.1 Dual-Axis Verification Taxonomy" in content
        assert "verification_taxonomy_spec.md" in content
        assert "AXIS 1: SOFTWARE CORRECTNESS" in content
        assert "AXIS 2: SCIENTIFIC GENERALIZATION" in content

    def test_readme_taxonomy_section_present(self) -> None:
        """Verify README.md contains Subsection 14.1 with the dual-axis taxonomy."""
        readme_path = REPO_ROOT / "README.md"
        assert readme_path.exists(), "README.md must exist"
        content = readme_path.read_text(encoding="utf-8")

        assert "### 14.1 Dual-Axis Verification Taxonomy" in content
        assert "verification_taxonomy_spec.md" in content
        assert "AXIS 1: SOFTWARE CORRECTNESS" in content
        assert "AXIS 2: SCIENTIFIC GENERALIZATION" in content

    def test_physical_directory_segregation(self) -> None:
        """Verify physical directory segregation between Axis 1 and Axis 2 suites."""
        backend_tests = REPO_ROOT / "backend" / "tests"
        benchmarks_dir = REPO_ROOT / "benchmarks"
        experiments_dir = REPO_ROOT / "experiments"

        assert backend_tests.is_dir(), "backend/tests/ must exist for Axis 1"
        assert benchmarks_dir.is_dir(), "benchmarks/ must exist for Axis 2"
        assert experiments_dir.is_dir(), "experiments/ must exist for Axis 2"

        # Verify unit tests are in backend/tests/unit
        unit_dir = backend_tests / "unit"
        assert unit_dir.is_dir()
        unit_tests = list(unit_dir.glob("test_*.py"))
        assert len(unit_tests) >= 50, "backend/tests/unit must contain comprehensive test suite"

        # Verify runners are in benchmarks/runners
        runners_dir = benchmarks_dir / "runners"
        assert runners_dir.is_dir()
        runners = list(runners_dir.glob("run_*.py"))
        assert len(runners) >= 5, "benchmarks/runners must contain empirical runners"

    def test_eight_canonical_datasets_in_axis_2(self) -> None:
        """Verify all 8 canonical datasets are represented in Axis 2 documentation."""
        spec_path = REPO_ROOT / "docs" / "verification_taxonomy_spec.md"
        content = spec_path.read_text(encoding="utf-8").lower()

        canonical_datasets = [
            "paysim",
            "ieee_cis",
            "credit_card",
            "elliptic",
            "amlsim",
            "synthaml",
            "amlnet",
            "cross_bank",
        ]
        for ds in canonical_datasets:
            assert ds in content, f"Canonical dataset {ds} must be referenced in Axis 2"

    def test_katex_formatting_in_taxonomy_spec(self) -> None:
        """Verify KaTeX formatting adheres to zero underscore errors and balanced math blocks."""
        spec_path = REPO_ROOT / "docs" / "verification_taxonomy_spec.md"
        content = spec_path.read_text(encoding="utf-8")

        # Zero unescaped underscores in \text{} or \mathrm{}
        text_underscore_matches = re.findall(r"\\(?:text|mathrm)\{[^}]*_[^}]*\}", content)
        assert not text_underscore_matches, (
            f"KaTeX error: unescaped underscore in math text: {text_underscore_matches}"
        )

        # Balanced $$ delimiters
        double_dollar_count = content.count("$$")
        assert double_dollar_count % 2 == 0, f"Unbalanced $$ delimiters: {double_dollar_count}"
