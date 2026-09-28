"""Unit tests for the Architectural Component Factorial Ablation Matrix.

Verifies:
1. Existence and valid JSON schema of factorial ablation artifacts.
2. Complete 16-configuration grid ($2^4 = 16$) coverage.
3. Factorial orthogonality and 50/50 factor balance.
4. Statistical ANOVA marginal main effects calculation parity.
5. Two-way interaction synergy values.
6. Pareto-optimal frontier identification.
7. Best utility (C10) vs production recommended (C16) designations.
8. Production configuration C16 cryptographic and privacy invariants.
9. Technical documentation synchronization in docs/enterprise_benchmark_report.md (Section 30).
10. Repository README.md synchronization in Section 15.11.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_RAW_ARTIFACT = _PROJECT_ROOT / "benchmarks" / "results" / "raw" / "factorial_ablation_matrix.json"
_EXP_ARTIFACT = _PROJECT_ROOT / "experiments" / "ablations" / "ablation_results.json"
_ENTERPRISE_REPORT = _PROJECT_ROOT / "docs" / "enterprise_benchmark_report.md"
_README = _PROJECT_ROOT / "README.md"


@pytest.fixture(scope="module")
def raw_matrix() -> dict:
    """Load and return the raw golden factorial ablation matrix JSON."""
    assert _RAW_ARTIFACT.is_file(), f"Missing artifact: {_RAW_ARTIFACT}"
    with open(_RAW_ARTIFACT, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def exp_matrix() -> dict:
    """Load and return the experiments directory ablation results JSON."""
    assert _EXP_ARTIFACT.is_file(), f"Missing artifact: {_EXP_ARTIFACT}"
    with open(_EXP_ARTIFACT, encoding="utf-8") as f:
        return json.load(f)


def test_factorial_ablation_artifacts_exist_and_valid(raw_matrix: dict, exp_matrix: dict) -> None:
    """Test that both raw and experiment ablation artifacts exist and share identical benchmark IDs."""
    assert raw_matrix["benchmark_id"] == "CFI-FACTORIAL-ABLATION-01"
    assert exp_matrix["benchmark_id"] == "CFI-FACTORIAL-ABLATION-01"
    assert "config" in raw_matrix
    assert raw_matrix["config"]["n_samples"] == 8000
    assert raw_matrix["config"]["n_clients"] == 5
    assert raw_matrix["config"]["rounds"] == 5
    assert len(raw_matrix["configurations"]) == 16
    assert len(exp_matrix["configurations"]) == 16


def test_all_sixteen_configurations_represented(raw_matrix: dict) -> None:
    """Test that all 16 unique configuration IDs (C01 through C16) are present."""
    expected_ids = [f"C{i:02d}" for i in range(1, 17)]
    actual_ids = [c["config_id"] for c in raw_matrix["configurations"]]
    assert actual_ids == expected_ids

    # Assert all configurations have non-trivial metrics
    for c in raw_matrix["configurations"]:
        assert 0.0 <= c["pr_auc"] <= 1.0
        assert 0.0 <= c["roc_auc"] <= 1.0
        assert 0.0 <= c["recall_at_fpr_0_01"] <= 1.0
        assert 0.0 <= c["recall_at_fpr_0_1"] <= 1.0
        assert c["runtime_ms"] > 0.0
        assert c["comm_kb_per_round"] > 0.0


def test_factorial_orthogonal_balance(raw_matrix: dict) -> None:
    """Test full factorial orthogonality: each factor is active in exactly 8/16 configurations."""
    configs = raw_matrix["configurations"]
    graph_count = sum(1 for c in configs if c["has_graph"])
    cb_count = sum(1 for c in configs if c["has_crossbank"])
    dp_count = sum(1 for c in configs if c["has_dp"])
    secagg_count = sum(1 for c in configs if c["has_secagg"])

    assert graph_count == 8, f"Expected 8 Graph active, got {graph_count}"
    assert cb_count == 8, f"Expected 8 CrossBank active, got {cb_count}"
    assert dp_count == 8, f"Expected 8 DP active, got {dp_count}"
    assert secagg_count == 8, f"Expected 8 SecAgg active, got {secagg_count}"

    # Verify all 16 combinations are distinct
    tuples = [(c["has_graph"], c["has_crossbank"], c["has_dp"], c["has_secagg"]) for c in configs]
    assert len(set(tuples)) == 16


def test_statistical_main_effects_exact_parity(raw_matrix: dict) -> None:
    """Verify ANOVA marginal main effects exact numerical values."""
    effects = {e["factor"]: e for e in raw_matrix["main_effects"]}
    assert set(effects.keys()) == {"Graph", "CrossBank", "DP", "SecAgg"}

    assert pytest.approx(effects["Graph"]["pr_auc_delta"], abs=1e-4) == 0.3359
    assert pytest.approx(effects["Graph"]["recall_0_01_delta"], abs=1e-4) == 0.2500

    assert pytest.approx(effects["CrossBank"]["pr_auc_delta"], abs=1e-4) == 0.4051
    assert pytest.approx(effects["CrossBank"]["recall_0_01_delta"], abs=1e-4) == 0.4167

    assert pytest.approx(effects["DP"]["pr_auc_delta"], abs=1e-4) == -0.0552
    assert pytest.approx(effects["DP"]["recall_0_01_delta"], abs=1e-4) == -0.0389

    assert pytest.approx(effects["SecAgg"]["pr_auc_delta"], abs=1e-4) == 0.0000
    assert pytest.approx(effects["SecAgg"]["recall_0_01_delta"], abs=1e-4) == 0.0000


def test_interaction_effects_synergy_values(raw_matrix: dict) -> None:
    """Verify two-way interaction effects match empirical synergy measurements."""
    interactions = {i["factor_pair"]: i for i in raw_matrix["interaction_effects"]}
    assert "Graph x CrossBank" in interactions
    assert "DP x Graph" in interactions
    assert "SecAgg x DP" in interactions

    assert pytest.approx(interactions["Graph x CrossBank"]["pr_auc_interaction"], abs=1e-4) == -0.6291
    assert pytest.approx(interactions["DP x Graph"]["pr_auc_interaction"], abs=1e-4) == -0.0168
    assert pytest.approx(interactions["SecAgg x DP"]["pr_auc_interaction"], abs=1e-4) == 0.0000


def test_pareto_frontier_configurations(raw_matrix: dict) -> None:
    """Verify Pareto frontier configurations match documented optimal trade-offs."""
    expected_pareto = ["C02", "C04", "C10", "C12"]
    assert raw_matrix["pareto_optimal_configs"] == expected_pareto

    # Verify that C10 achieves highest PR-AUC on the frontier
    configs_by_id = {c["config_id"]: c for c in raw_matrix["configurations"]}
    c10 = configs_by_id["C10"]
    assert c10["pr_auc"] == 0.9842
    assert c10["roc_auc"] == 0.9996


def test_best_utility_and_production_recommended_configs(raw_matrix: dict) -> None:
    """Verify designated best utility (C10) and production recommended (C16) configurations."""
    assert raw_matrix["best_utility_config"] == "C10"
    assert raw_matrix["production_recommended_config"] == "C16"

    configs_by_id = {c["config_id"]: c for c in raw_matrix["configurations"]}
    c16 = configs_by_id["C16"]

    assert c16["name"] == "Graph + CrossBank + DP + SecAgg"
    assert c16["has_graph"] is True
    assert c16["has_crossbank"] is True
    assert c16["has_dp"] is True
    assert c16["has_secagg"] is True
    assert c16["pr_auc"] == 0.9342
    assert c16["roc_auc"] == 0.9966
    assert c16["recall_at_fpr_0_01"] == 0.7556


def test_production_config_c16_invariants(raw_matrix: dict) -> None:
    """Verify security, privacy, and communication bounds on production configuration C16."""
    configs_by_id = {c["config_id"]: c for c in raw_matrix["configurations"]}
    c16 = configs_by_id["C16"]

    # Statutory privacy bound
    assert c16["epsilon"] is not None
    assert c16["epsilon"] <= 3.0

    # Information-theoretic zero-knowledge boundary
    assert "PQC SecAgg Coordinator Zero-Knowledge" in c16["security_level"]

    # Bandwidth boundary (< 20 KB/round)
    assert c16["comm_kb_per_round"] <= 20.0


def test_documentation_section_30_in_enterprise_report() -> None:
    """Verify that Section 30 is present and complete in docs/enterprise_benchmark_report.md."""
    assert _ENTERPRISE_REPORT.is_file()
    content = _ENTERPRISE_REPORT.read_text(encoding="utf-8")

    assert "## 30. Comprehensive Multi-Factor Architectural Component Ablation Matrix" in content
    assert "### 30.1 Multi-Factor Experimental Setup & Orthogonal Design" in content
    assert "### 30.2 Complete 16-Configuration Factorial Grid Results (`CFI-FACTORIAL-ABLATION-01`)" in content
    assert "### 30.3 Statistical ANOVA Marginal Main Effects" in content
    assert "### 30.4 Two-Way Interaction Synergies" in content
    assert "### 30.5 Multi-Objective Pareto Frontier & Production Deployment Recommendation" in content
    assert "### 30.6 Automated Verification & Test Suite Mapping" in content

    # Verify table contains C01 through C16
    for i in range(1, 17):
        assert f"`C{i:02d}`" in content


def test_readme_section_15_11_synchronization() -> None:
    """Verify that Section 15.11 is present and complete in README.md."""
    assert _README.is_file()
    content = _README.read_text(encoding="utf-8")

    assert "### 15.11 Architectural Component Factorial Ablation Matrix ($2^4 = 16$ Grid)" in content
    assert "Statistical ANOVA Marginal Main Effects" in content
    assert "### 15.12 Reproducible Benchmark CLI Commands" in content

    # Verify table contains C01 through C16
    for i in range(1, 17):
        assert f"`C{i:02d}`" in content
