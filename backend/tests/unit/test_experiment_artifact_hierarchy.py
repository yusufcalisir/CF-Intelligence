"""Unit tests for Standardized Experiment Artifact Hierarchy & Automated Reporting.

Validates that all canonical benchmark experiment directories (paysim, ieee_cis,
credit_card, elliptic, amlsim, synthaml, amlnet, cross_bank) adhere to the
standardized 5-artifact hierarchy (config.json, results.json, metrics.csv,
report.md, plots/) and that ReportCompiler provides robust synthesis and
verification across the repository.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from experiments.harness.compile_reports import CANONICAL_DATASETS, ReportCompiler

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENTS_ROOT = REPO_ROOT / "experiments"


def test_all_canonical_dataset_directories_exist():
    """Verify that all canonical dataset directories exist in experiments/."""
    for ds_name in CANONICAL_DATASETS:
        ds_dir = EXPERIMENTS_ROOT / ds_name
        assert ds_dir.exists(), f"Missing dataset experiment directory: {ds_dir}"
        assert ds_dir.is_dir(), f"Expected directory at: {ds_dir}"


def test_standardized_artifact_suite_present_in_all_datasets():
    """Verify that all 8 datasets contain the complete 5-artifact suite."""
    audit = ReportCompiler.verify_artifact_hierarchy(experiments_root=EXPERIMENTS_ROOT)
    assert len(audit) == 8, f"Expected 8 datasets in audit, found {len(audit)}"

    for ds_name, checks in audit.items():
        assert checks["dir_exists"], f"{ds_name}: Directory does not exist"
        assert checks["config_json"], f"{ds_name}: Missing config.json"
        assert checks["results_json"], f"{ds_name}: Missing results.json"
        assert checks["metrics_csv"], f"{ds_name}: Missing metrics.csv"
        assert checks["report_md"], f"{ds_name}: Missing report.md"
        assert checks["plots_dir"], f"{ds_name}: Missing plots/ directory"
        assert checks["plots_has_images"], f"{ds_name}: plots/ contains fewer than 3 images"


def test_config_json_schema_validity():
    """Verify that config.json in each dataset folder is valid JSON with required keys."""
    for ds_name in CANONICAL_DATASETS:
        config_file = EXPERIMENTS_ROOT / ds_name / "config.json"
        assert config_file.exists(), f"Missing config.json in {ds_name}"

        with open(config_file, encoding="utf-8") as f:
            data = json.load(f)

        assert isinstance(data, dict), f"{ds_name}/config.json must be a JSON object"
        assert len(data) >= 3, f"{ds_name}/config.json contains insufficient metadata"

        if ds_name == "cross_bank":
            assert "benchmark_id" in data or "dataset_name" in data
            assert "num_scenarios" in data
        else:
            assert any(k in data for k in ["model_type", "strategy", "experiment_id", "dataset_name"])


def test_results_json_schema_validity():
    """Verify that results.json in each dataset folder is valid JSON with benchmark results."""
    for ds_name in CANONICAL_DATASETS:
        results_file = EXPERIMENTS_ROOT / ds_name / "results.json"
        assert results_file.exists(), f"Missing results.json in {ds_name}"

        with open(results_file, encoding="utf-8") as f:
            data = json.load(f)

        assert isinstance(data, dict), f"{ds_name}/results.json must be a JSON object"
        if ds_name == "cross_bank":
            assert "overall_federated_detection_rate" in data
            assert "scenarios" in data
        else:
            assert "final_metrics" in data or "history" in data


def test_metrics_csv_structure_and_non_empty():
    """Verify that metrics.csv in each dataset folder has headers and non-empty rows."""
    for ds_name in CANONICAL_DATASETS:
        csv_file = EXPERIMENTS_ROOT / ds_name / "metrics.csv"
        assert csv_file.exists(), f"Missing metrics.csv in {ds_name}"

        with open(csv_file, encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        assert len(rows) >= 2, f"{ds_name}/metrics.csv must contain header + at least 1 row"
        headers = rows[0]
        assert len(headers) >= 2, f"{ds_name}/metrics.csv header must contain at least 2 columns"


def test_report_md_sections_and_completeness():
    """Verify that report.md in each dataset folder is comprehensive and contains required sections."""
    for ds_name in CANONICAL_DATASETS:
        report_file = EXPERIMENTS_ROOT / ds_name / "report.md"
        assert report_file.exists(), f"Missing report.md in {ds_name}"

        content = report_file.read_text(encoding="utf-8")
        assert len(content) > 1000, f"{ds_name}/report.md is too short ({len(content)} characters)"

        if ds_name == "cross_bank":
            assert "Empirical Consortium Value" in content or "Executive Summary" in content
            assert "Information Gain" in content or "Scenario Breakdown" in content
        else:
            assert "Executive Summary & Core Results" in content
            assert "Hardware & Execution Environment" in content
            assert "Dataset Characteristics & Integrity" in content
            assert "Hyperparameter Matrix" in content
            assert "Publication Plot Gallery" in content
            assert "Model Governance" in content


def test_plots_directory_contains_publication_figures():
    """Verify that plots/ directory in each dataset folder contains valid PNG images."""
    for ds_name in CANONICAL_DATASETS:
        plots_dir = EXPERIMENTS_ROOT / ds_name / "plots"
        assert plots_dir.exists(), f"Missing plots/ in {ds_name}"

        png_files = list(plots_dir.glob("*.png"))
        assert len(png_files) >= 3, f"{ds_name}/plots/ must contain at least 3 PNG files, found {len(png_files)}"

        for pf in png_files:
            assert pf.stat().st_size > 1000, f"Plot file {pf} appears corrupt (< 1KB)"


def test_compile_reports_compiler_class_methods(tmp_path: Path):
    """Verify ReportCompiler programmatic synthesis and standardization methods."""
    test_result = {
        "experiment_id": "test_exp_001",
        "config": {
            "experiment_name": "UnitTestExperiment",
            "model_type": "DeepFraudMLP",
            "strategy": "FedAvg",
            "num_rounds": 3,
        },
        "hardware": {
            "os_platform": "Windows",
            "cpu_model": "TestCPU",
            "total_ram_gb": 16.0,
        },
        "dataset": {
            "dataset_name": "UnitTestDataset",
            "total_samples": 1000,
            "fraud_samples": 50,
            "fraud_rate": 0.05,
        },
        "final_metrics": {
            "pr_auc": 0.85,
            "roc_auc": 0.95,
            "f1_score": 0.80,
            "precision": 0.82,
            "recall": 0.78,
            "brier_score": 0.01,
        },
        "confusion_matrix": {
            "tn": 940,
            "fp": 10,
            "fn": 11,
            "tp": 39,
        },
        "history": [
            {"step": 1, "train_loss": 0.5, "val_loss": 0.4, "pr_auc": 0.75, "roc_auc": 0.85, "duration_seconds": 1.0},
            {"step": 2, "train_loss": 0.3, "val_loss": 0.2, "pr_auc": 0.85, "roc_auc": 0.95, "duration_seconds": 1.1},
        ],
        "status": "COMPLETED",
        "total_duration_seconds": 2.1,
    }

    out_md = tmp_path / "report.md"
    report_text = ReportCompiler.compile_markdown_report(test_result, output_path=out_md)

    assert out_md.exists()
    assert "UnitTestExperiment" in report_text
    assert "PR-AUC" in report_text
    assert "0.8500" in report_text


def test_compile_reports_cli_verify_flag():
    """Verify that python experiments/harness/compile_reports.py --verify executes with exit code 0."""
    result = subprocess.run(
        [sys.executable, "experiments/harness/compile_reports.py", "--verify"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"--verify failed with output:\n{result.stderr}\n{result.stdout}"
    assert "All canonical dataset experiment directories comply" in result.stdout
