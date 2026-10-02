"""Unit tests for Master Empirical Comparative Benchmark Matrix.

Validates the Strict Null Representation Invariant across all 8 canonical
benchmark datasets (PaySim, IEEE-CIS, European Credit Card, Elliptic Bitcoin Graph,
IBM AMLSim, SynthAML, AMLNet, and CFI-CrossBank Consortium), ensuring that
unexecuted benchmarks, unmeasured thresholds, or inapplicable paradigms
are strictly represented as null/None, never as fake 0.0000 fallbacks.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MATRIX_JSON_PATH = REPO_ROOT / "benchmarks" / "results" / "raw" / "master_benchmark_matrix.json"
GENERATOR_SCRIPT_PATH = REPO_ROOT / "benchmarks" / "generate_master_benchmark_matrix.py"
ENTERPRISE_REPORT_PATH = REPO_ROOT / "docs" / "enterprise_benchmark_report.md"

CANONICAL_DATASET_IDS = [
    "paysim",
    "ieee_cis",
    "credit_card",
    "elliptic",
    "amlsim",
    "synthaml",
    "amlnet",
    "cross_bank",
]


@pytest.fixture
def master_matrix_data() -> dict[str, Any]:
    """Loads and returns the master benchmark matrix JSON."""
    assert MATRIX_JSON_PATH.exists(), f"Master benchmark matrix not found at {MATRIX_JSON_PATH}"
    with open(MATRIX_JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_master_matrix_json_exists_and_schema_valid(master_matrix_data: dict[str, Any]) -> None:
    """Verifies that master_benchmark_matrix.json exists and conforms to v1.0.0 schema."""
    assert master_matrix_data["schema_version"] == "1.0.0"
    assert "report_name" in master_matrix_data
    assert "generated_at_utc" in master_matrix_data
    assert "invariants" in master_matrix_data
    assert "summary_statistics" in master_matrix_data
    assert "datasets" in master_matrix_data

    invariants = master_matrix_data["invariants"]
    assert invariants.get("strict_null_representation") is True
    assert invariants.get("zero_fake_defaults") is True
    assert invariants.get("canonical_datasets_count") == 8


def test_all_eight_canonical_datasets_represented(master_matrix_data: dict[str, Any]) -> None:
    """Verifies that all 8 canonical benchmark datasets are present in the matrix."""
    datasets = master_matrix_data["datasets"]
    assert len(datasets) == 8, f"Expected 8 datasets, found {len(datasets)}"
    for ds_id in CANONICAL_DATASET_IDS:
        assert ds_id in datasets, f"Missing canonical dataset: {ds_id}"
        ds_data = datasets[ds_id]
        assert "dataset_name" in ds_data
        assert "domain" in ds_data
        assert "real_vs_synthetic" in ds_data
        assert "paradigms" in ds_data


def test_strict_null_representation_invariant(master_matrix_data: dict[str, Any]) -> None:
    """Verifies that unexecuted runs strictly store null/None and never fabricate 0.0."""
    datasets = master_matrix_data["datasets"]

    for ds_id, ds_data in datasets.items():
        paradigms = ds_data.get("paradigms", {})

        fa = paradigms.get("federated_fedavg", {})
        if ds_id == "elliptic":
            # Elliptic is centralized GraphSAGE benchmark; cross-bank FL is NOT_EVALUATED
            assert fa.get("status") == "NOT_EVALUATED", f"{ds_id}: FedAvg must have status NOT_EVALUATED"
            assert fa.get("pr_auc") is None, f"{ds_id}: FedAvg PR-AUC must be None"
        else:
            # Federated FedAvg must be evaluated for all other datasets
            assert fa.get("status") == "EVALUATED", f"{ds_id}: FedAvg must have status EVALUATED"
            assert fa.get("pr_auc") is not None, f"{ds_id}: FedAvg PR-AUC must not be None"

        # Check every paradigm with status NOT_RUN or NOT_EVALUATED
        for p_name, p_data in paradigms.items():
            status = p_data.get("status")
            if status in ("NOT_RUN", "NOT_EVALUATED"):
                for metric_key in ["pr_auc", "roc_auc", "f1_score", "precision", "recall"]:
                    val = p_data.get(metric_key)
                    assert val is None, (
                        f"VIOLATION of Strict Null Invariant: {ds_id}.{p_name}.{metric_key} "
                        f"is {val} instead of null/None"
                    )


def test_evaluated_zero_distinction(master_matrix_data: dict[str, Any]) -> None:
    """Verifies that empirical zeros (e.g. extreme imbalance or zero-positive transfer) are preserved as 0.0."""
    datasets = master_matrix_data["datasets"]

    # In Cross-Bank Scenario 7, Bank Gamma isolated model with 0 training positives evaluates to exactly 0.0
    cb_iso = datasets["cross_bank"]["paradigms"]["isolated_silos"]
    assert cb_iso["status"] == "EVALUATED"
    assert cb_iso["zero_positive_transfer_isolated"] == 0.0
    assert cb_iso["zero_positive_transfer_federated"] == 1.0

    # PaySim FedAvg in canonical 636k evaluation has authentic non-zero metrics
    paysim_fa = datasets["paysim"]["paradigms"]["federated_fedavg"]
    assert paysim_fa["status"] == "EVALUATED"
    assert paysim_fa["roc_auc"] > 0.80
    assert paysim_fa["pr_auc"] > 0.10


def test_numerical_parity_with_experiment_results(master_matrix_data: dict[str, Any]) -> None:
    """Verifies exact numerical parity between master matrix and experiments/*/results.json."""
    datasets = master_matrix_data["datasets"]

    # Credit Card parity (verifies against canonical multi-seed controlled benchmark)
    cc_controlled = REPO_ROOT / "experiments" / "credit_card" / "multi_seed_controlled_results.json"
    if cc_controlled.exists():
        with open(cc_controlled, encoding="utf-8") as f:
            cc_d = json.load(f)
        fa_agg = cc_d["aggregate_summary"]["federated_fedavg"]
        cc_matrix_fa = datasets["credit_card"]["paradigms"]["federated_fedavg"]
        assert pytest.approx(cc_matrix_fa["pr_auc"], abs=1e-4) == fa_agg["pr_auc"]["mean"]
        assert pytest.approx(cc_matrix_fa["roc_auc"], abs=1e-4) == fa_agg["roc_auc"]["mean"]
    else:
        cc_exp = REPO_ROOT / "experiments" / "credit_card" / "results.json"
        if cc_exp.exists():
            with open(cc_exp, encoding="utf-8") as f:
                cc_d = json.load(f)
            fm = cc_d["final_metrics"]
            cc_matrix_fa = datasets["credit_card"]["paradigms"]["federated_fedavg"]
            assert pytest.approx(cc_matrix_fa["pr_auc"], abs=1e-4) == fm["pr_auc"]
            assert pytest.approx(cc_matrix_fa["roc_auc"], abs=1e-4) == fm["roc_auc"]

    # SynthAML parity (verifies against canonical artifact fraud_benchmark_synthaml.json)
    sy_can = REPO_ROOT / "benchmarks" / "results" / "raw" / "fraud_benchmark_synthaml.json"
    if sy_can.exists():
        with open(sy_can, encoding="utf-8") as f:
            sy_d = json.load(f)
        sy_fa = sy_d["federated_fedavg"]
        sy_matrix_fa = datasets["synthaml"]["paradigms"]["federated_fedavg"]
        assert pytest.approx(sy_matrix_fa["pr_auc"], abs=1e-4) == sy_fa["pr_auc"]
        assert pytest.approx(sy_matrix_fa["roc_auc"], abs=1e-4) == sy_fa["roc_auc"]
    else:
        sy_exp = REPO_ROOT / "experiments" / "synthaml" / "results.json"
        if sy_exp.exists():
            with open(sy_exp, encoding="utf-8") as f:
                sy_d = json.load(f)
            fm = sy_d["final_metrics"]
            sy_matrix_fa = datasets["synthaml"]["paradigms"]["federated_fedavg"]
            assert pytest.approx(sy_matrix_fa["pr_auc"], abs=1e-4) == fm["pr_auc"]
            assert pytest.approx(sy_matrix_fa["roc_auc"], abs=1e-4) == fm["roc_auc"]

    # Cross-Bank parity (verifies against canonical artifact fraud_benchmark_crossbank.json)
    cb_can = REPO_ROOT / "benchmarks" / "results" / "raw" / "fraud_benchmark_crossbank.json"
    if cb_can.exists():
        with open(cb_can, encoding="utf-8") as f:
            cb_d = json.load(f)
        cb_matrix_fa = datasets["cross_bank"]["paradigms"]["federated_fedavg"]
        assert pytest.approx(cb_matrix_fa["detection_rate"], abs=1e-4) == cb_d["overall_federated_detection_rate"]
        assert pytest.approx(cb_matrix_fa["pr_auc"], abs=1e-4) == cb_d["scenarios"]["SCENARIO_1"]["federated_pr_auc"]


def test_generator_programmatic_api() -> None:
    """Verifies programmatic API of MasterBenchmarkMatrixGenerator."""
    sys.path.insert(0, str(REPO_ROOT))
    from benchmarks.generate_master_benchmark_matrix import MasterBenchmarkMatrixGenerator

    generator = MasterBenchmarkMatrixGenerator()
    matrix = generator.build_matrix()
    assert matrix["schema_version"] == "1.0.0"
    assert len(matrix["datasets"]) == 8

    passed, errors = generator.verify_matrix(matrix)
    assert passed is True, f"Matrix verification failed: {errors}"
    assert len(errors) == 0


def test_generator_cli_verify_flag() -> None:
    """Verifies that generate_master_benchmark_matrix.py executes with --verify."""
    cmd = [sys.executable, str(GENERATOR_SCRIPT_PATH), "--verify"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Generator CLI --verify failed: {res.stderr}\n{res.stdout}"
    assert "Matrix verification PASSED" in res.stdout


def test_markdown_table_formatting_and_strict_nulls() -> None:
    """Verifies markdown table rendering and proper null em-dash formatting."""
    sys.path.insert(0, str(REPO_ROOT))
    from benchmarks.generate_master_benchmark_matrix import MasterBenchmarkMatrixGenerator

    generator = MasterBenchmarkMatrixGenerator()
    md_table = generator.format_markdown_table()

    # All canonical datasets must be named in the table
    assert "PaySim Mobile Money Fraud" in md_table
    assert "IEEE-CIS Fraud Detection" in md_table
    assert "European Credit Card Fraud" in md_table
    assert "Elliptic Bitcoin AML Graph" in md_table
    assert "IBM AMLSim Multi-Hop Banking" in md_table
    assert "Danish Spar Nord Bank SynthAML" in md_table
    assert "Australian AUSTRAC AMLNet" in md_table
    assert "CFI-CrossBank Multi-Bank Consortium" in md_table

    # Strict null representation markers
    assert "N/A (NOT RUN)" in md_table
    assert "—" in md_table


def test_documentation_section_29_in_enterprise_report() -> None:
    """Verifies Section 29 presence and contents in docs/enterprise_benchmark_report.md."""
    assert ENTERPRISE_REPORT_PATH.exists()
    content = ENTERPRISE_REPORT_PATH.read_text(encoding="utf-8")

    assert "## 29. Master Comparative Empirical Benchmark Matrix & Strict Null Representation" in content
    assert "29.1 Consolidated Empirical Benchmark Taxonomy" in content
    assert "29.2 Strict Null Representation Invariant" in content
    assert "29.3 Master Comparative Empirical Benchmark Matrix" in content
    assert "29.4 Comparative Empirical Insights & Mathematical Takeaways" in content
    assert "29.5 Automated Verification & Test Suite Mapping" in content

    # All 8 canonical dataset names in Section 29
    for ds_name in [
        "PaySim Mobile Money Fraud",
        "IEEE-CIS Fraud Detection",
        "European Credit Card Fraud",
        "Elliptic Bitcoin AML Graph",
        "IBM AMLSim Multi-Hop Banking",
        "Danish Spar Nord Bank SynthAML",
        "Australian AUSTRAC AMLNet",
        "CFI-CrossBank Multi-Bank Consortium",
    ]:
        assert ds_name in content, f"Missing dataset in Section 29: {ds_name}"


def test_katex_math_and_table_integrity() -> None:
    """Verifies KaTeX math formatting and table cell hygiene in Section 29."""
    assert ENTERPRISE_REPORT_PATH.exists()
    content = ENTERPRISE_REPORT_PATH.read_text(encoding="utf-8")
    sec_idx = content.find("## 29. Master Comparative Empirical Benchmark Matrix")
    assert sec_idx != -1

    sec29 = content[sec_idx:]
    # No unescaped pipe inside inline math
    lines = sec29.splitlines()
    in_table = False
    for line in lines:
        if line.startswith("|"):
            in_table = True
            # In table rows, verify no inline math has raw |
            if "$" in line:
                math_parts = line.split("$")
                # Odd index parts are inside $...$
                for i in range(1, len(math_parts), 2):
                    math_expr = math_parts[i]
                    assert "|" not in math_expr, f"Unescaped pipe in math mode in table: {line}"
        elif in_table and not line.strip():
            in_table = False
