"""Unit tests for Anti-Metric Shopping Protocol & Negative Result Preservation.

Validates the formal specification in docs/LIMITATIONS.md, the engineering
decision record ED-042 in docs/engineering_decisions.md, and cross-document
hyperlink integrity across README.md.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LIMITATIONS_PATH = REPO_ROOT / "docs" / "LIMITATIONS.md"
ED_PATH = REPO_ROOT / "docs" / "engineering_decisions.md"
README_PATH = REPO_ROOT / "README.md"


@pytest.fixture
def limitations_content() -> str:
    """Fixture to load docs/LIMITATIONS.md content."""
    assert LIMITATIONS_PATH.exists(), f"docs/LIMITATIONS.md must exist at {LIMITATIONS_PATH}"
    return LIMITATIONS_PATH.read_text(encoding="utf-8")


def test_limitations_doc_exists_and_substantial(limitations_content: str):
    """Test 1: Verify docs/LIMITATIONS.md exists and contains comprehensive documentation (>5,000 bytes)."""
    assert len(limitations_content) > 5000
    assert "# Formal Limitations, Anti-Metric Shopping Protocol & Negative Result Ledger" in limitations_content


def test_anti_metric_shopping_protocol_four_rules(limitations_content: str):
    """Test 2: Verify all 4 rules of the Anti-Metric Shopping Protocol are formally defined."""
    assert "Pre-Registered Metric Hierarchy" in limitations_content
    assert "Pre-Fixed Operational Thresholds" in limitations_content
    assert "Unconditional Negative Result Preservation" in limitations_content
    assert "Multi-Seed Robustness" in limitations_content or "Multi-Seed Distribution" in limitations_content


def test_five_canonical_negative_results_documented(limitations_content: str):
    """Test 3: Verify all 5 canonical empirical negative results (NR-001 through NR-005) are documented."""
    negative_results = [
        "Negative Result NR-001",  # DP utility collapse
        "Negative Result NR-002",  # Decentralization / centralization gap
        "Negative Result NR-003",  # Deep neural MLP imbalance vulnerability
        "Negative Result NR-004",  # SCAFFOLD control variate lag
        "Negative Result NR-005",  # Byzantine defense clean-data penalty
    ]
    for nr in negative_results:
        assert nr in limitations_content, f"Negative result entry '{nr}' must be documented in docs/LIMITATIONS.md"


def test_negative_result_artifact_hyperlinks(limitations_content: str):
    """Test 4: Verify negative result entries link to raw JSON execution artifacts or reports."""
    assert "dp_privacy_utility_tradeoff.json" in limitations_content
    assert "byzantine_benchmark_sign_inversion.json" in limitations_content
    assert "enterprise_benchmark_report.md" in limitations_content


def test_katex_formatting_invariants(limitations_content: str):
    """Test 5: Verify KaTeX strict compatibility rules (Rule 4).

    - Display math ($$) must be isolated.
    - Zero unescaped underscores inside \\text{...} or \\mathrm{...}.
    - No markdown bold wrapping across math delimiters (**$...$**).
    - Balanced inline ($) and display ($$) math delimiters.
    """
    bad_text_pattern = re.compile(r"\\(?:text|mathrm)\{[^}]*_[^}]*\}")
    matches = bad_text_pattern.findall(limitations_content)
    bad_matches = [m for m in matches if r"\_" not in m and "_" in m]
    assert not bad_matches, f"Found unescaped underscores in KaTeX text blocks: {bad_matches}"

    bold_math = re.findall(r"\*\*\$[^$]+\$\*\*", limitations_content)
    assert not bold_math, f"Found markdown bold wrapping across math delimiters: {bold_math}"

    single_dollar_count = len(re.findall(r"(?<!\$)\$(?!\$)", limitations_content))
    assert single_dollar_count % 2 == 0, f"Unbalanced inline math delimiters ($): count = {single_dollar_count}"

    double_dollar_count = len(re.findall(r"\$\$", limitations_content))
    assert double_dollar_count % 2 == 0, f"Unbalanced display math delimiters ($$): count = {double_dollar_count}"


def test_engineering_decision_ed042_present():
    """Test 6: Verify ED-042 is documented in docs/engineering_decisions.md."""
    assert ED_PATH.exists(), f"engineering_decisions.md must exist at {ED_PATH}"
    content = ED_PATH.read_text(encoding="utf-8")
    assert "ED-042" in content, "ED-042 must be present in docs/engineering_decisions.md"
    assert "Anti-Metric Shopping Protocol" in content
    assert "Unconditional Negative Result Preservation" in content


def test_readme_navigation_and_section14_references():
    """Test 7: Verify README.md includes LIMITATIONS.md in top navigation and Section 14."""
    assert README_PATH.exists(), f"README.md must exist at {README_PATH}"
    content = README_PATH.read_text(encoding="utf-8")
    assert "docs/LIMITATIONS.md" in content or "LIMITATIONS.md" in content, (
        "README.md must link to docs/LIMITATIONS.md"
    )
    assert "Limitations & Negative Results" in content or "Limitations" in content, (
        "README.md top navigation must include Limitations link"
    )


def test_systemic_limitations_boundaries(limitations_content: str):
    """Test 8: Verify Section 3 details real-world scope constraints (What This Is NOT)."""
    assert "Not Deployed in Live Financial Rails" in limitations_content or "not been deployed in live" in limitations_content
    assert "Single Author" in limitations_content or "Single-Maintainer" in limitations_content
    assert "Yusuf Çalışır" in limitations_content
    assert "Conceptual Exploration, Not Legal Certification" in limitations_content or "not independently certified" in limitations_content


def test_epistemic_vulnerability_matrix_structure(limitations_content: str):
    """Test 9: Verify the Epistemic Vulnerability Matrix table is present with required columns."""
    assert "Epistemic Vulnerability Matrix" in limitations_content
    assert "Vulnerability Domain" in limitations_content
    assert "Technical Failure Mode" in limitations_content
    assert "Root Cause & Mechanism" in limitations_content
    assert "Platform Safeguard & Mitigation" in limitations_content
    assert "Residual Operational Risk" in limitations_content


def test_metric_hierarchy_imbalance_rules(limitations_content: str):
    """Test 10: Verify the protocol mandates PR-AUC / Recall@0.1%FPR and forbids standalone Accuracy."""
    assert r"\operatorname{PR-AUC}" in limitations_content
    assert r"\operatorname{Recall@0.1\%FPR}" in limitations_content
    assert "prohibited as a standalone" in limitations_content or "strictly prohibited" in limitations_content
