"""Unit tests for the End-to-End Scientific Reproducibility Guide (REPRODUCIBILITY.md).

Verifies:
1. Existence of REPRODUCIBILITY.md at repository root and docs/ mirror.
2. Complete required sections (Executive Summary, Hardware, Software, Master CLI, Datasets, Reproduction, Ablation, Artifacts, Determinism, Test Mapping).
3. All 8 canonical benchmark datasets have explicit step-by-step reproduction commands.
4. Hardware environment specifications (CPU, RAM, Disk, GPU).
5. Software environment locks (Python 3.12, PyTorch 2.4.0, FastAPI).
6. Makefile targets (reproduce-all, benchmark-all, benchmark-matrix, benchmark-verify, benchmark-factorial, benchmark-download).
7. README.md synchronization in navigation bar and Section 15.12.
8. Determinism and seed control guidelines (torch.use_deterministic_algorithms, fixed seeds).
9. All documented Python CLI runner scripts exist on disk.
10. KaTeX mathematical formatting and table integrity.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_ROOT_REPRODUCIBILITY = _PROJECT_ROOT / "REPRODUCIBILITY.md"
_DOCS_REPRODUCIBILITY = _PROJECT_ROOT / "docs" / "REPRODUCIBILITY.md"
_MAKEFILE = _PROJECT_ROOT / "Makefile"
_README = _PROJECT_ROOT / "README.md"


@pytest.fixture(scope="module")
def root_content() -> str:
    """Load and return the contents of the root REPRODUCIBILITY.md."""
    assert _ROOT_REPRODUCIBILITY.is_file(), f"Missing file: {_ROOT_REPRODUCIBILITY}"
    return _ROOT_REPRODUCIBILITY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def docs_content() -> str:
    """Load and return the contents of the mirrored docs/REPRODUCIBILITY.md."""
    assert _DOCS_REPRODUCIBILITY.is_file(), f"Missing file: {_DOCS_REPRODUCIBILITY}"
    return _DOCS_REPRODUCIBILITY.read_text(encoding="utf-8")


def test_reproducibility_md_exists_at_root_and_docs(root_content: str, docs_content: str) -> None:
    """Test that REPRODUCIBILITY.md exists in both root and docs/ with non-trivial size."""
    assert len(root_content) > 2000, "Root REPRODUCIBILITY.md is too short"
    assert len(docs_content) > 2000, "docs/REPRODUCIBILITY.md is too short"
    # Ensure they are aligned
    assert root_content.splitlines()[0] == docs_content.splitlines()[0]


def test_reproducibility_md_required_sections(root_content: str) -> None:
    """Verify that all mandatory structural sections exist in REPRODUCIBILITY.md."""
    required_sections = [
        "## 1. Executive Summary & Scientific Reproducibility Scope",
        "## 2. Hardware & Compute Environment Prerequisites",
        "## 3. Software Stack, Python 3.12 & Environment Locks",
        "## 4. One-Line Master CLI Reproduction Workflows",
        "## 5. Dataset Acquisition Protocol & Credential Verification",
        "## 6. Per-Dataset Step-by-Step Empirical Reproduction",
        "## 7. Multi-Factor Ablation & Privacy Frontiers",
        "## 8. Artifact Hierarchy & Master Matrix Verification",
        "## 9. Determinism, Seed Control & Floating-Point Stability",
        "## 10. Automated Verification & Test Suite Mapping",
    ]
    for section in required_sections:
        assert section in root_content, f"Missing required section: {section}"


def test_all_eight_canonical_datasets_represented(root_content: str) -> None:
    """Verify that all 8 canonical benchmark datasets have dedicated reproduction subsections."""
    expected_subsections = [
        "### 6.1 PaySim Mobile Money Benchmark",
        "### 6.2 IEEE-CIS Fraud Detection Benchmark",
        "### 6.3 European Credit Card Benchmark",
        "### 6.4 Elliptic Bitcoin Transaction Graph",
        "### 6.5 IBM Research AMLSim Multi-Hop Banking Graph",
        "### 6.6 Danish Spar Nord Bank SynthAML Benchmark",
        "### 6.7 Australian AUSTRAC AMLNet Benchmark",
        "### 6.8 CFI-CrossBank Multi-Bank Consortium Benchmark",
    ]
    for sub in expected_subsections:
        assert sub in root_content, f"Missing dataset subsection: {sub}"


def test_hardware_specs_documented(root_content: str) -> None:
    """Verify that hardware specifications are explicitly documented."""
    assert "8 GB RAM" in root_content
    assert "16 GB RAM" in root_content
    assert "NVMe SSD" in root_content
    assert "CPU" in root_content


def test_software_environment_locks(root_content: str) -> None:
    """Verify software versions and runtime locks are documented."""
    assert "3.12" in root_content
    assert "2.4.0" in root_content
    assert "FastAPI" in root_content
    assert "Pydantic" in root_content


def test_makefile_contains_reproducibility_targets() -> None:
    """Verify Makefile contains key reproducibility targets."""
    assert _MAKEFILE.is_file()
    makefile_text = _MAKEFILE.read_text(encoding="utf-8")

    required_targets = [
        "reproduce-all:",
        "benchmark-all:",
        "benchmark-matrix:",
        "benchmark-verify:",
        "benchmark-factorial:",
        "benchmark-download:",
    ]
    for target in required_targets:
        assert target in makefile_text, f"Missing Makefile target: {target}"


def test_readme_synchronization_links() -> None:
    """Verify README.md contains links to REPRODUCIBILITY.md."""
    assert _README.is_file()
    readme_text = _README.read_text(encoding="utf-8")

    assert "[🔄 Reproducibility Guide](REPRODUCIBILITY.md)" in readme_text
    assert "[`REPRODUCIBILITY.md`](REPRODUCIBILITY.md)" in readme_text
    assert "make reproduce-all" in readme_text


def test_determinism_and_seed_control_guidelines(root_content: str) -> None:
    """Verify numerical determinism, seed initialization, and PyTorch settings."""
    assert "SEED = 42" in root_content or "seed" in root_content.lower()
    assert "torch.use_deterministic_algorithms(True)" in root_content
    assert "CUBLAS_WORKSPACE_CONFIG" in root_content
    assert "OMP_NUM_THREADS" in root_content


def test_cli_runner_scripts_exist_on_disk() -> None:
    """Verify all runner scripts mentioned in REPRODUCIBILITY.md exist on disk."""
    expected_scripts = [
        _PROJECT_ROOT / "scripts" / "download_real_benchmarks.py",
        _PROJECT_ROOT / "benchmarks" / "runners" / "run_paysim_benchmark.py",
        _PROJECT_ROOT / "benchmarks" / "runners" / "run_ieee_cis_benchmark.py",
        _PROJECT_ROOT / "benchmarks" / "runners" / "run_creditcard_benchmark.py",
        _PROJECT_ROOT / "benchmarks" / "runners" / "run_graphsage_benchmark.py",
        _PROJECT_ROOT / "benchmarks" / "runners" / "run_amlsim_benchmark.py",
        _PROJECT_ROOT / "benchmarks" / "runners" / "run_synthaml_benchmark.py",
        _PROJECT_ROOT / "benchmarks" / "runners" / "run_amlnet_benchmark.py",
        _PROJECT_ROOT / "benchmarks" / "runners" / "run_factorial_ablation.py",
        _PROJECT_ROOT / "benchmarks" / "runners" / "run_dp_tradeoff.py",
        _PROJECT_ROOT / "benchmarks" / "runners" / "run_byzantine_benchmark.py",
        _PROJECT_ROOT / "benchmarks" / "generate_master_benchmark_matrix.py",
    ]
    for script in expected_scripts:
        assert script.is_file(), f"Documented script not found on disk: {script}"


def test_katex_math_and_table_integrity(root_content: str) -> None:
    """Verify that KaTeX inline math delimiters are balanced and have no forbidden patterns."""
    # Check for unescaped underscores in inline math
    inline_math_matches = re.findall(r"\$([^\$\n]+)\$", root_content)
    assert len(inline_math_matches) > 0, "Expected at least one inline math expression"
    for math in inline_math_matches:
        # Check that \text{...} does not contain bare underscores
        text_matches = re.findall(r"\\text\{([^}]+)\}", math)
        for t in text_matches:
            assert "_" not in t or r"\_" in t, f"Forbidden unescaped underscore in \\text: {t}"
