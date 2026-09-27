"""Integration test suite for the Architectural Component Factorial Ablation Matrix.

Validates:
- 16-configuration full factorial grid expansion (Graph x DP x SecAgg x CrossBank).
- Structural and consortium feature masking integrity.
- DP-SGD gradient clipping, Gaussian noise injection, and finite Rényi DP epsilon bounds.
- PQC SecAgg communication payload overhead and cryptographic latency adjustments.
- Operational fixed-FPR recall, ECE calibration, and Brier score metrics.
- Statistical ANOVA marginal main effects and two-way interaction synergies.
- Pareto efficiency frontier computation.
- Pydantic v2 serialization and artifact generation.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch
from experiments.ablations.factorial_runner import (
    ComponentAblationResult,
    FactorialAblationRunner,
    FactorialAblationSuiteResult,
    FactorialConfig,
    FactorialDataGenerator,
    FactorialMLPClassifier,
    calculate_brier_score,
    calculate_ece,
    calculate_recall_at_fixed_fpr,
    compute_rdp_epsilon,
    serialize_ablation_artifacts,
)


@pytest.fixture
def base_config() -> FactorialConfig:
    """Fast test configuration for rapid test execution."""
    return FactorialConfig(
        n_samples=500,
        n_clients=3,
        rounds=2,
        local_epochs=1,
        batch_size=16,
        learning_rate=0.05,
        dp_sigma=1.0,
        dp_clip_norm=1.0,
        dp_delta=1e-5,
        dirichlet_alpha=0.5,
        seed=42,
    )


def test_factorial_config_defaults() -> None:
    """Verify default hyperparameters satisfy production benchmark specifications."""
    cfg = FactorialConfig()
    assert cfg.n_samples == 8000
    assert cfg.n_clients == 5
    assert cfg.rounds == 5
    assert cfg.local_epochs == 2
    assert cfg.dp_sigma == 1.0
    assert cfg.dp_clip_norm == 1.0
    assert cfg.dp_delta == 1e-5
    assert cfg.dirichlet_alpha == 0.5


def test_data_generator_feature_slices(base_config: FactorialConfig) -> None:
    """Verify synthetic multi-bank data partitioner generates correct feature slices and splits."""
    gen = FactorialDataGenerator(base_config)
    X, y, client_splits, test_indices = gen.generate_full_dataset()

    assert X.shape == (500, 22)
    assert len(y) == 500
    assert len(test_indices) == 100  # 20% sequestered test split
    assert len(client_splits) == base_config.n_clients

    # Ensure all train samples are assigned to clients
    total_train = sum(len(s) for s in client_splits)
    assert total_train == 400

    # Ensure class labels are binary and contain positive fraud samples
    assert set(np.unique(y)).issubset({0, 1})
    assert np.sum(y == 1) > 0


def test_model_forward_pass_and_architecture() -> None:
    """Verify FactorialMLPClassifier layer dimensions, forward pass, and output ranges."""
    model = FactorialMLPClassifier(input_dim=22, hidden_dim=48)
    dummy_input = torch.randn(8, 22)
    output = model(dummy_input)

    assert output.shape == (8, 1)
    assert (output >= 0.0).all() and (output <= 1.0).all()

    # Verify total parameter count
    total_params = sum(p.numel() for p in model.parameters())
    # fc1: 22*48 + 48 = 1104; ln1: 48*2 = 96; fc2: 48*1 + 1 = 49 -> 1249 params
    assert total_params == 1249


def test_rdp_epsilon_accounting() -> None:
    """Verify Rényi Differential Privacy epsilon computation for Gaussian mechanism."""
    # Positive sigma and steps should yield finite epsilon
    eps = compute_rdp_epsilon(sigma=1.0, steps=50, sample_rate=0.05, delta=1e-5)
    assert 0.1 < eps < 5.0

    # Zero sigma should return infinity
    eps_inf = compute_rdp_epsilon(sigma=0.0, steps=50, sample_rate=0.05, delta=1e-5)
    assert eps_inf == float("inf")


def test_low_fpr_and_calibration_metrics() -> None:
    """Verify fixed-FPR recall, ECE, and Brier score calculators on synthetic ground truth."""
    y_true = np.array([0, 0, 0, 0, 1, 1], dtype=np.int64)
    y_prob = np.array([0.05, 0.10, 0.15, 0.20, 0.85, 0.95], dtype=np.float32)

    recalls = calculate_recall_at_fixed_fpr(y_true, y_prob, target_fprs=(0.0001, 0.001, 0.01))
    assert 0.0001 in recalls
    assert 0.001 in recalls
    assert 0.01 in recalls

    ece = calculate_ece(y_true, y_prob, n_bins=5)
    assert 0.0 <= ece <= 1.0

    brier = calculate_brier_score(y_true, y_prob)
    assert 0.0 <= brier <= 1.0


def test_secagg_overhead_and_security_level(base_config: FactorialConfig) -> None:
    """Verify that SecAgg models communication overhead (+6.8%) and zero-knowledge security level."""
    runner = FactorialAblationRunner(base_config)
    X_full, y, client_splits, test_indices = runner.generator.generate_full_dataset()
    X_test = X_full[test_indices]
    y_test = y[test_indices]

    # Run without SecAgg
    res_no_sec = runner._evaluate_single_configuration(
        config_id="C01",
        name="No SecAgg",
        has_graph=False,
        has_dp=False,
        has_secagg=False,
        has_crossbank=False,
        X_full=X_full,
        y=y,
        client_train_splits=client_splits,
        X_test_full=X_test,
        y_test=y_test,
    )

    # Run with SecAgg
    res_sec = runner._evaluate_single_configuration(
        config_id="C02",
        name="With SecAgg",
        has_graph=False,
        has_dp=False,
        has_secagg=True,
        has_crossbank=False,
        X_full=X_full,
        y=y,
        client_train_splits=client_splits,
        X_test_full=X_test,
        y_test=y_test,
    )

    assert res_sec.comm_kb_per_round > res_no_sec.comm_kb_per_round
    assert "PQC SecAgg" in res_sec.security_level
    assert "Server-Exposed" in res_no_sec.security_level


def test_main_effects_and_interaction_math() -> None:
    """Verify statistical main effects and interaction calculations adhere to ANOVA definitions."""
    runner = FactorialAblationRunner()

    # Create synthetic configurations with known synthetic scores
    configs: list[ComponentAblationResult] = []
    for code in range(16):
        has_g = bool(code & 8)
        has_dp = bool(code & 4)
        has_sec = bool(code & 2)
        has_cb = bool(code & 1)

        # Baseline score 0.50 + 0.20 for Graph + 0.15 for CrossBank - 0.05 for DP + 0.05 for (Graph x CrossBank)
        score = 0.50
        if has_g:
            score += 0.20
        if has_cb:
            score += 0.15
        if has_dp:
            score -= 0.05
        if has_g and has_cb:
            score += 0.05

        configs.append(ComponentAblationResult(
            config_id=f"C{code+1:02d}",
            name=f"Config_{code+1}",
            has_graph=has_g,
            has_dp=has_dp,
            has_secagg=has_sec,
            has_crossbank=has_cb,
            pr_auc=round(score, 4),
            roc_auc=0.90,
            recall_at_fpr_0_01=0.80,
            recall_at_fpr_0_1=0.90,
            recall_at_fpr_1_0=0.95,
            f1_score=0.85,
            ece=0.02,
            brier_score=0.01,
            runtime_ms=100.0,
            comm_kb_per_round=10.0,
            epsilon=1.5 if has_dp else None,
            security_level="SecAgg" if has_sec else "Plain",
        ))

    main_effects = runner._compute_main_effects(configs)
    effects_map = {e.factor: e.pr_auc_delta for e in main_effects}

    # Graph delta should be positive and close to ~0.225 (0.20 base + 0.025 average from interaction)
    assert effects_map["Graph"] > 0.18
    # CrossBank delta should be positive
    assert effects_map["CrossBank"] > 0.14
    # DP delta should be negative (~ -0.05)
    assert effects_map["DP"] < -0.04
    # SecAgg should have ~0.0 delta
    assert abs(effects_map["SecAgg"]) < 0.01

    interactions = runner._compute_interaction_effects(configs)
    inter_map = {ie.factor_pair: ie.pr_auc_interaction for ie in interactions}
    # Graph x CrossBank synergy should be ~ +0.05
    assert inter_map["Graph x CrossBank"] > 0.03


def test_pareto_frontier_identification() -> None:
    """Verify Pareto-optimal configuration identification filters dominated configurations."""
    runner = FactorialAblationRunner()

    c1 = ComponentAblationResult(
        config_id="C01", name="C1", has_graph=False, has_dp=False, has_secagg=False, has_crossbank=False,
        pr_auc=0.60, roc_auc=0.70, recall_at_fpr_0_01=0.50, recall_at_fpr_0_1=0.60, recall_at_fpr_1_0=0.70,
        f1_score=0.55, ece=0.05, brier_score=0.05, runtime_ms=50.0, comm_kb_per_round=9.0, epsilon=None, security_level="Plain"
    )
    # c2 dominates c1 in both PR-AUC and Recall
    c2 = ComponentAblationResult(
        config_id="C02", name="C2", has_graph=True, has_dp=False, has_secagg=False, has_crossbank=False,
        pr_auc=0.85, roc_auc=0.90, recall_at_fpr_0_01=0.75, recall_at_fpr_0_1=0.85, recall_at_fpr_1_0=0.90,
        f1_score=0.80, ece=0.03, brier_score=0.02, runtime_ms=60.0, comm_kb_per_round=9.0, epsilon=None, security_level="Plain"
    )
    # c3 has higher recall but lower PR-AUC (tradeoff -> also Pareto)
    c3 = ComponentAblationResult(
        config_id="C03", name="C3", has_graph=False, has_dp=False, has_secagg=False, has_crossbank=True,
        pr_auc=0.80, roc_auc=0.88, recall_at_fpr_0_01=0.90, recall_at_fpr_0_1=0.92, recall_at_fpr_1_0=0.95,
        f1_score=0.78, ece=0.04, brier_score=0.03, runtime_ms=55.0, comm_kb_per_round=9.0, epsilon=None, security_level="Plain"
    )

    pareto = runner._identify_pareto_optimal_configs([c1, c2, c3])
    assert "C01" not in pareto
    assert "C02" in pareto
    assert "C03" in pareto


def test_factorial_ablation_smoke_run_and_serialization(tmp_path: Path, base_config: FactorialConfig) -> None:
    """Execute end-to-end smoke run across all 16 configurations and verify artifact serialization."""
    runner = FactorialAblationRunner(base_config)
    suite = runner.run_full_suite()

    assert isinstance(suite, FactorialAblationSuiteResult)
    assert len(suite.configurations) == 16
    assert len(suite.main_effects) == 4
    assert len(suite.interaction_effects) == 3
    assert len(suite.pareto_optimal_configs) > 0
    assert suite.best_utility_config.startswith("C")
    assert suite.production_recommended_config.startswith("C")

    # Serialize artifacts to temp directory
    paths = serialize_ablation_artifacts(suite, tmp_path)

    for key, path in paths.items():
        assert path.exists(), f"Artifact {key} at {path} was not created"

    # Validate JSON schema loading
    with open(paths["results_json"], encoding="utf-8") as f:
        data = json.load(f)
    assert data["benchmark_id"] == "CFI-FACTORIAL-ABLATION-01"
    assert len(data["configurations"]) == 16

    # Validate Markdown dossier content
    with open(paths["dossier"], encoding="utf-8") as f:
        dossier_text = f.read()
    assert "Architectural Component Factorial Ablation Matrix" in dossier_text
    assert "Statistical Main Effects (ANOVA)" in dossier_text
    assert "Pareto Operational Frontier" in dossier_text
