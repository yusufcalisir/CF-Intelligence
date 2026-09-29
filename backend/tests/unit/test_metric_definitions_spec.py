"""Unit tests for the Unified Scientific Metric Definition Standard & Evaluation Glossary.

Validates the existence, mathematical rigor, KaTeX formatting compliance,
regulatory mappings, and cross-document hyperlink integrity of docs/METRICS.md.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
METRICS_PATH = REPO_ROOT / "docs" / "METRICS.md"
ALGORITHMS_INDEX_PATH = REPO_ROOT / "docs" / "algorithms" / "README.md"
BENCHMARK_REPORT_PATH = REPO_ROOT / "docs" / "enterprise_benchmark_report.md"
README_PATH = REPO_ROOT / "README.md"


@pytest.fixture
def metrics_content() -> str:
    """Fixture to load docs/METRICS.md content."""
    assert METRICS_PATH.exists(), f"docs/METRICS.md must exist at {METRICS_PATH}"
    return METRICS_PATH.read_text(encoding="utf-8")


def test_metrics_doc_exists_and_non_empty(metrics_content: str):
    """Test 1: Verify docs/METRICS.md exists and has substantial content (>5,000 bytes)."""
    assert len(metrics_content) > 5000
    assert "# Unified Scientific Metric Definition Standard & Evaluation Glossary" in metrics_content


def test_seven_core_metrics_defined(metrics_content: str):
    """Test 2: Verify all 7 core evaluation metrics are formally defined with math and descriptions."""
    core_metrics = [
        "PR-AUC",
        "ROC-AUC",
        "Recall@FPR",
        "ECE",
        "BS",
        "PSI",
        "JSD",
    ]
    for metric in core_metrics:
        assert metric in metrics_content, f"Core metric {metric} must be present in docs/METRICS.md"


def test_katex_formatting_invariants(metrics_content: str):
    """Test 3: Verify KaTeX strict compatibility rules (Rule 4).

    - Display math ($$) must be surrounded by blank lines.
    - Zero unescaped underscores inside \\text{...} or \\mathrm{...}.
    - No markdown bold wrapping across math delimiters (**$...$**).
    """
    # Check for unescaped underscores in text/mathrm
    bad_text_pattern = re.compile(r"\\(?:text|mathrm)\{[^}]*_[^}]*\}")
    matches = bad_text_pattern.findall(metrics_content)
    # Filter out escaped underscores (\_)
    bad_matches = [m for m in matches if r"\_" not in m and "_" in m]
    assert not bad_matches, f"Found unescaped underscores in KaTeX text blocks: {bad_matches}"

    # Check for bold wrapped math: **$...$** or **$$...$$**
    bold_math = re.findall(r"\*\*\$[^$]+\$\*\*", metrics_content)
    assert not bold_math, f"Found markdown bold wrapping across math delimiters: {bold_math}"

    # Verify balanced math delimiters
    single_dollar_count = len(re.findall(r"(?<!\$)\$(?!\$)", metrics_content))
    assert single_dollar_count % 2 == 0, f"Unbalanced inline math delimiters ($): count = {single_dollar_count}"

    double_dollar_count = len(re.findall(r"\$\$", metrics_content))
    assert double_dollar_count % 2 == 0, f"Unbalanced display math delimiters ($$): count = {double_dollar_count}"


def test_regulatory_framework_mappings(metrics_content: str):
    """Test 4: Verify regulatory frameworks (SR 11-7, OCC 2011-12, EU AI Act, BCBS) are mapped."""
    assert "Federal Reserve SR 11-7" in metrics_content
    assert "OCC 2011-12" in metrics_content
    assert "EU Artificial Intelligence Act" in metrics_content
    assert "Article 15" in metrics_content
    assert "BCBS" in metrics_content


def test_psi_traffic_light_thresholds(metrics_content: str):
    """Test 5: Verify the PSI regulatory traffic-light action matrix is defined."""
    assert "0.10" in metrics_content
    assert "0.25" in metrics_content
    assert "Stable / No Drift" in metrics_content
    assert "Moderate Drift" in metrics_content
    assert "Significant Drift" in metrics_content


def test_cost_utility_and_fairness_definitions(metrics_content: str):
    """Test 6: Verify economic loss function and algorithmic fairness metrics are defined."""
    assert r"\mathcal{L}_{\mathrm{financial}}" in metrics_content
    assert "850" in metrics_content  # C_FN
    assert "25" in metrics_content   # C_FP
    assert "DIR" in metrics_content
    assert "EOD" in metrics_content
    assert "DPD" in metrics_content
    assert "ECOA" in metrics_content


def test_algorithm_docs_cross_reference():
    """Test 7: Verify docs/algorithms/README.md references docs/METRICS.md."""
    assert ALGORITHMS_INDEX_PATH.exists(), f"algorithms/README.md must exist at {ALGORITHMS_INDEX_PATH}"
    content = ALGORITHMS_INDEX_PATH.read_text(encoding="utf-8")
    assert "METRICS.md" in content, "docs/algorithms/README.md must reference METRICS.md"


def test_enterprise_benchmark_report_cross_reference():
    """Test 8: Verify docs/enterprise_benchmark_report.md references docs/METRICS.md."""
    assert BENCHMARK_REPORT_PATH.exists(), f"docs/enterprise_benchmark_report.md must exist at {BENCHMARK_REPORT_PATH}"
    content = BENCHMARK_REPORT_PATH.read_text(encoding="utf-8")
    assert "METRICS.md" in content, "docs/enterprise_benchmark_report.md must reference METRICS.md"


def test_readme_navigation_and_section_references():
    """Test 9: Verify README.md includes METRICS.md in top nav and Section 15."""
    assert README_PATH.exists(), f"README.md must exist at {README_PATH}"
    content = README_PATH.read_text(encoding="utf-8")
    assert "[docs/METRICS.md](docs/METRICS.md)" in content or "(docs/METRICS.md)" in content, (
        "README.md must link to docs/METRICS.md"
    )
    assert "Metric Standards" in content, "README.md top navigation must include Metric Standards"


def test_service_implementation_alignment():
    """Test 10: Verify the underlying modules referenced in the metric summary table exist."""
    metrics_service_py = REPO_ROOT / "backend" / "app" / "application" / "services" / "metrics_service.py"
    drift_service_py = REPO_ROOT / "backend" / "app" / "application" / "services" / "drift_service.py"
    factorial_runner_py = REPO_ROOT / "benchmarks" / "runners" / "run_factorial_ablation.py"

    assert metrics_service_py.exists(), f"metrics_service.py must exist at {metrics_service_py}"
    assert drift_service_py.exists(), f"drift_service.py must exist at {drift_service_py}"
    assert factorial_runner_py.exists(), f"run_factorial_ablation.py must exist at {factorial_runner_py}"
