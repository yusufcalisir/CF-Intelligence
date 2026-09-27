"""Unit and integration tests for Cross-Bank Synthetic Consortium Benchmark (CFI-CrossBank-01).

Validates deterministic 7-scenario topology generation, information horizon isolation,
zero-positive cold-start invariants, and federated collaborative uplift.
"""

import os
import tempfile

import numpy as np
import pandas as pd
import pytest
import torch
from backend.app.domain.models.consortium import (
    ConsortiumBenchmarkResult,
    ConsortiumNode,
    CrossBankTransaction,
    InstitutionType,
    ScenarioMetrics,
)
from benchmarks.generators.cross_bank_network import (
    DEFAULT_CONSORTIUM_NODES,
    FEATURE_COLUMNS,
    SCENARIO_DEFINITIONS,
    CrossBankNetworkGenerator,
)
from experiments.cross_bank.run_consortium_benchmark import (
    ConsortiumMLPClassifier,
    aggregate_weights,
    calculate_recall_at_fpr,
    initialize_zero_positive_model,
    run_consortium_benchmark,
)
from pydantic import ValidationError


class TestConsortiumDomainModels:
    """Validate Pydantic v2 schemas and business rules for consortium models."""

    def test_consortium_node_instantiation_and_validation(self) -> None:
        node = ConsortiumNode(
            bank_id="bank_test",
            name="Bank Test",
            institution_type=InstitutionType.RETAIL,
            volume_share=0.45,
            account_count=1000,
            positive_prevalence=0.012,
            is_zero_positive=False,
        )
        assert node.bank_id == "bank_test"
        assert node.volume_share == 0.45

        # Value bounds validation
        with pytest.raises((ValidationError, ValueError)):
            ConsortiumNode.model_validate({
                "bank_id": "invalid",
                "name": "Invalid",
                "volume_share": 1.5,  # Must be <= 1.0
                "account_count": 0,
            })

    def test_cross_bank_transaction_model(self) -> None:
        tx = CrossBankTransaction(
            transaction_id="tx_test_001",
            step=12,
            source_bank="bank_a",
            target_bank="bank_b",
            source_account="acc_a_01",
            target_account="acc_b_01",
            amount=15420.50,
            payment_rail="SEPA_INSTANT",
            is_laundering=1,
            scenario_id="SCENARIO_2",
            hop_index=0,
            features={"amount": 15420.50, "is_cross_bank": 1.0},
        )
        assert tx.is_laundering == 1
        assert tx.source_bank != tx.target_bank
        assert tx.features["is_cross_bank"] == 1.0

    def test_scenario_metrics_and_result_schema(self) -> None:
        metric = ScenarioMetrics(
            scenario_id="SCENARIO_3",
            scenario_name="3-Bank Cycle",
            isolated_detection_rate=0.64,
            federated_detection_rate=1.0,
            pooled_detection_rate=1.0,
            delta_detection_rate=0.36,
            isolated_pr_auc=0.72,
            federated_pr_auc=0.96,
            delta_pr_auc=0.24,
            isolated_recall_at_01_fpr=0.55,
            federated_recall_at_01_fpr=0.98,
            rounds_to_detection=2,
            participating_institutions=3,
        )
        assert metric.delta_detection_rate == 0.36

        result = ConsortiumBenchmarkResult(
            benchmark_id="CFI-CrossBank-01",
            timestamp="2026-09-27T12:00:00Z",
            total_transactions=10000,
            total_accounts=10000,
            scenarios_evaluated=7,
            overall_isolated_detection_rate=0.80,
            overall_federated_detection_rate=1.0,
            overall_pooled_detection_rate=1.0,
            overall_delta_detection_rate=0.20,
            scenarios={"SCENARIO_3": metric},
            zero_positive_transfer_recall=1.0,
        )
        assert result.benchmark_id == "CFI-CrossBank-01"
        assert result.overall_delta_detection_rate == 0.20


class TestCrossBankNetworkGenerator:
    """Validate deterministic generation, information horizons, and scenarios."""

    def test_deterministic_reproducibility(self) -> None:
        gen1 = CrossBankNetworkGenerator(seed=42)
        df1 = gen1.generate_benchmark_dataset(n_total_transactions=500, timesteps=48)

        gen2 = CrossBankNetworkGenerator(seed=42)
        df2 = gen2.generate_benchmark_dataset(n_total_transactions=500, timesteps=48)

        pd.testing.assert_frame_equal(df1, df2)

    def test_scenarios_1_to_7_presence_and_typologies(self) -> None:
        gen = CrossBankNetworkGenerator(seed=42)
        df = gen.generate_benchmark_dataset(n_total_transactions=2000, timesteps=96)

        # Confirm all 7 scenarios exist in the generated dataset
        for sc_id in SCENARIO_DEFINITIONS:
            subset = df[df["scenario_id"] == sc_id]
            assert len(subset) > 0, f"Scenario {sc_id} missing from generated dataset"
            assert (subset["is_laundering"] == 1).all(), f"Scenario {sc_id} contains non-laundering labels"

    def test_information_horizon_isolation(self) -> None:
        gen = CrossBankNetworkGenerator(seed=42)
        df = gen.generate_benchmark_dataset(n_total_transactions=1500, timesteps=72)

        # Bank A View
        bank_a_view = gen.get_local_bank_view(df, "bank_a")
        # Assert Bank A sees ONLY transactions where source is Bank A or target is Bank A
        assert ((bank_a_view["source_bank"] == "bank_a") | (bank_a_view["target_bank"] == "bank_a")).all()

        # Strict Information Horizon: Zero transactions between Bank B and Bank C can exist in Bank A's view
        leakage = bank_a_view[
            (bank_a_view["source_bank"] == "bank_b") & (bank_a_view["target_bank"] == "bank_c")
        ]
        assert len(leakage) == 0, "Information Horizon violated: Bank A observed Bank B -> Bank C transaction"

    def test_chronological_train_test_split(self) -> None:
        gen = CrossBankNetworkGenerator(seed=42)
        df = gen.generate_benchmark_dataset(n_total_transactions=1000, timesteps=72)
        train_df, test_df = gen.split_chronological_train_test(df, split_ratio=0.80)

        assert len(train_df) > 0
        assert len(test_df) > 0
        # Zero future lookahead leakage invariant
        assert train_df["step"].max() <= test_df["step"].min()


class TestConsortiumModelAndOptimization:
    """Validate neural architecture, zero-positive handling, and parameter aggregation."""

    def test_consortium_mlp_forward_and_predict_proba(self) -> None:
        model = ConsortiumMLPClassifier(input_dim=len(FEATURE_COLUMNS), hidden_dim=32)
        dummy_x = np.random.randn(10, len(FEATURE_COLUMNS)).astype(np.float32)

        probs = model.predict_proba(dummy_x)
        assert probs.shape == (10,)
        assert (probs >= 0.0).all() and (probs <= 1.0).all()

    def test_single_class_zero_positive_prior_initialization(self) -> None:
        # Bank Gamma historical train set with zero positive examples
        model = ConsortiumMLPClassifier(input_dim=len(FEATURE_COLUMNS))
        dummy_x = np.random.randn(50, len(FEATURE_COLUMNS)).astype(np.float32)

        # Zero positive isolated silo initialization sets prior to 0.0
        model = initialize_zero_positive_model(model)
        probs = model.predict_proba(dummy_x)

        # Must predict near zero due to unobserved positive boundary
        assert (probs < 0.05).all(), "Zero-positive model predicted positive without positive labels"

    def test_sample_weighted_fedavg_aggregation(self) -> None:
        m1 = ConsortiumMLPClassifier(input_dim=4, hidden_dim=8)
        m2 = ConsortiumMLPClassifier(input_dim=4, hidden_dim=8)

        # Set distinct deterministic weights
        with torch.no_grad():
            for p in m1.parameters():
                p.fill_(1.0)
            for p in m2.parameters():
                p.fill_(3.0)

        # 50/50 weighting
        agg_state = aggregate_weights([m1, m2], [100, 100])
        first_weight = agg_state["net.0.weight"]
        assert np.allclose(first_weight.detach().cpu().numpy(), 2.0)

        # 25/75 weighting
        agg_state_skewed = aggregate_weights([m1, m2], [100, 300])
        skewed_weight = agg_state_skewed["net.0.weight"]
        assert np.allclose(skewed_weight.detach().cpu().numpy(), 2.5)

    def test_recall_at_fixed_fpr_computation(self) -> None:
        y_true = np.array([0, 0, 0, 0, 1, 1, 1, 1], dtype=int)
        y_score_perfect = np.array([0.1, 0.2, 0.25, 0.3, 0.7, 0.8, 0.9, 0.95], dtype=float)

        rec = calculate_recall_at_fpr(y_true, y_score_perfect, target_fpr=0.01)
        assert rec == 1.0


class TestEndToEndConsortiumBenchmark:
    """Validate full consortium benchmark execution and artifact creation."""

    def test_run_consortium_benchmark_small_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = run_consortium_benchmark(
                n_transactions=1500,
                rounds=2,
                local_epochs=1,
                seed=42,
                output_dir=tmp_dir,
            )

            assert isinstance(result, ConsortiumBenchmarkResult)
            assert result.benchmark_id == "CFI-CrossBank-01"
            assert result.scenarios_evaluated == 7
            assert len(result.scenarios) == 7

            # Collaborative uplift must be non-negative
            assert result.overall_delta_detection_rate >= 0.0

            # Scenario 7 zero-positive transfer uplift verification
            sc7 = result.scenarios["SCENARIO_7"]
            assert sc7.isolated_detection_rate == 0.0
            assert sc7.federated_detection_rate > sc7.isolated_detection_rate
            assert sc7.delta_detection_rate > 0.0

            # Verify file artifacts were written
            assert os.path.exists(os.path.join(tmp_dir, "results.json"))
            assert os.path.exists(os.path.join(tmp_dir, "scenario_breakdown.json"))
            assert os.path.exists(os.path.join(tmp_dir, "audit_dossier.md"))
            assert os.path.exists(os.path.join(tmp_dir, "plots", "scenario_detection_rates.png"))

    def test_benchmark_generator_namespace_export(self) -> None:
        from benchmarks.generators.cross_bank_network import (
            FEATURE_COLUMNS,
            SCENARIO_DEFINITIONS,
            CrossBankNetworkGenerator,
        )

        assert CrossBankNetworkGenerator is not None
        assert len(DEFAULT_CONSORTIUM_NODES) == 3
        assert len(FEATURE_COLUMNS) == 14
        assert len(SCENARIO_DEFINITIONS) == 7
