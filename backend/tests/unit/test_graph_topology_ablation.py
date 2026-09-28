"""Unit and integration test suite for Graph Topology & Feature Paradigm Ablations.

Validates:
1. Controlled Feature Ablation (Tabular Only vs Graph Only vs Tabular + Graph).
2. Detection uplift metrics (Delta PR-AUC, Relative Gain, Recall @ strict FPR, Synergy).
3. Synthetic transaction network generation and multi-hop neighborhood aggregations.
4. Topology complexity and network density sensitivity sweeps (average degree, k-hop depth, graph structures).
5. Isolated node resilience and graceful fallback.
6. Pydantic v2 schema serialization and reproducibility invariants.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from experiments.ablations.graph_vs_tabular import (
    FeatureParadigm,
    GraphVsTabularConfig,
    GraphVsTabularSuiteResult,
    GraphVsTabularUpliftResult,
    ParadigmEvaluationResult,
    calculate_brier_score,
    calculate_ece,
    calculate_recall_at_fixed_fpr,
    format_markdown_table,
    generate_ablation_dataset,
    run_graph_vs_tabular_ablation,
    train_and_evaluate_paradigm,
)
from experiments.ablations.topology_sensitivity import (
    DegreePointResult,
    GraphStructureType,
    HopPointResult,
    IsolationDegradationPointResult,
    SyntheticTransactionGraph,
    TopologyModelPointResult,
    TopologySensitivityConfig,
    TopologySensitivitySuiteResult,
    format_sensitivity_tables,
    run_topology_sensitivity_sweep,
)


class TestFeatureParadigmAndDataGeneration:
    """Verifies feature paradigms, configuration models, and dataset partitioning."""

    def test_feature_paradigm_enum(self) -> None:
        """Verifies enum values for the three feature paradigms."""
        assert FeatureParadigm.TABULAR_ONLY == "TABULAR_ONLY"
        assert FeatureParadigm.GRAPH_ONLY == "GRAPH_ONLY"
        assert FeatureParadigm.TABULAR_PLUS_GRAPH == "TABULAR_PLUS_GRAPH"

    def test_config_defaults_and_validation(self) -> None:
        """Verifies default parameters and custom overrides."""
        cfg = GraphVsTabularConfig()
        assert cfg.n_samples == 6000
        assert cfg.fraud_prevalence == 0.03
        assert cfg.tabular_dim == 12
        assert cfg.graph_dim == 10
        assert cfg.test_ratio == 0.25

        custom_cfg = GraphVsTabularConfig(n_samples=1000, fraud_prevalence=0.05, seed=123)
        assert custom_cfg.n_samples == 1000
        assert custom_cfg.fraud_prevalence == 0.05
        assert custom_cfg.seed == 123

    def test_generate_ablation_dataset_shapes_and_splits(self) -> None:
        """Verifies array dimensions, feature counts, and split ratios."""
        cfg = GraphVsTabularConfig(n_samples=1000, test_ratio=0.20, seed=42)
        X_tab_tr, X_grp_tr, y_tr, X_tab_te, X_grp_te, y_te = generate_ablation_dataset(cfg)

        # Expected split: 800 train, 200 test
        assert len(X_tab_tr) == 800
        assert len(X_grp_tr) == 800
        assert len(y_tr) == 800

        assert len(X_tab_te) == 200
        assert len(X_grp_te) == 200
        assert len(y_te) == 200

        # Feature dimensions
        assert X_tab_tr.shape[1] == cfg.tabular_dim
        assert X_grp_tr.shape[1] == cfg.graph_dim
        assert X_tab_te.shape[1] == cfg.tabular_dim
        assert X_grp_te.shape[1] == cfg.graph_dim

        # Label consistency
        assert set(np.unique(y_tr)).issubset({0, 1})
        assert set(np.unique(y_te)).issubset({0, 1})
        assert int(np.sum(y_tr == 1)) > 0
        assert int(np.sum(y_te == 1)) > 0

    def test_reproducible_data_generation(self) -> None:
        """Verifies deterministic dataset generation across identical seeds."""
        cfg_a = GraphVsTabularConfig(n_samples=500, seed=99)
        cfg_b = GraphVsTabularConfig(n_samples=500, seed=99)

        X_tab_tr_a, _, y_tr_a, _, _, _ = generate_ablation_dataset(cfg_a)
        X_tab_tr_b, _, y_tr_b, _, _, _ = generate_ablation_dataset(cfg_b)

        np.testing.assert_allclose(X_tab_tr_a, X_tab_tr_b, rtol=1e-5)
        np.testing.assert_array_equal(y_tr_a, y_tr_b)


class TestMetricsAndCalibrationHelpers:
    """Verifies statistical metric calculators and operational FPR evaluations."""

    def test_calculate_recall_at_fixed_fpr_bounds(self) -> None:
        """Verifies recall calculation across strict FPR targets."""
        y_true = np.array([0] * 900 + [1] * 100)
        # Perfect predictions
        y_prob_perf = np.array([0.01] * 900 + [0.99] * 100)
        rec_perf = calculate_recall_at_fixed_fpr(y_true, y_prob_perf, target_fprs=(0.001, 0.01))
        assert rec_perf[0.01] == 1.0

        # Disjoint labels edge case
        rec_zero = calculate_recall_at_fixed_fpr(np.zeros(100), np.random.rand(100))
        assert all(v == 0.0 for v in rec_zero.values())

    def test_calculate_ece_metric(self) -> None:
        """Verifies Expected Calibration Error computation."""
        y_true = np.array([1, 1, 0, 0])
        y_prob_calibrated = np.array([0.9, 0.8, 0.1, 0.2])
        ece = calculate_ece(y_true, y_prob_calibrated, n_bins=5)
        assert 0.0 <= ece <= 1.0

        # Empty predictions
        assert calculate_ece(np.array([]), np.array([])) == 0.0

    def test_calculate_brier_score_metric(self) -> None:
        """Verifies Brier score calculation."""
        y_true = np.array([1, 0])
        y_prob = np.array([1.0, 0.0])
        assert calculate_brier_score(y_true, y_prob) == 0.0

        y_prob_wrong = np.array([0.0, 1.0])
        assert calculate_brier_score(y_true, y_prob_wrong) == 1.0


class TestParadigmEvaluationAndUplift:
    """Verifies model training, paradigm evaluations, and detection uplift quantification."""

    def test_train_and_evaluate_tabular_paradigm(self) -> None:
        """Verifies Tabular Only paradigm training and output metrics."""
        cfg = GraphVsTabularConfig(n_samples=800, epochs=3, seed=42)
        X_tab_tr, _, y_tr, X_tab_te, _, y_te = generate_ablation_dataset(cfg)

        res = train_and_evaluate_paradigm(
            FeatureParadigm.TABULAR_ONLY, X_tab_tr, y_tr, X_tab_te, y_te, cfg
        )

        assert isinstance(res, ParadigmEvaluationResult)
        assert res.paradigm == FeatureParadigm.TABULAR_ONLY
        assert res.feature_count == cfg.tabular_dim
        assert 0.0 <= res.pr_auc <= 1.0
        assert 0.0 <= res.roc_auc <= 1.0
        assert 0.0 <= res.f1_score <= 1.0
        assert 0.0 <= res.ece <= 1.0
        assert 0.0 <= res.brier_score <= 1.0
        assert res.training_time_ms > 0.0

    def test_run_graph_vs_tabular_ablation_uplift(self, tmp_path: Path) -> None:
        """Executes full ablation and verifies positive detection uplift."""
        cfg = GraphVsTabularConfig(
            n_samples=1200,
            epochs=4,
            seed=42,
            save_artifacts=True,
            output_dir=str(tmp_path),
        )
        suite = run_graph_vs_tabular_ablation(cfg)

        assert isinstance(suite, GraphVsTabularSuiteResult)
        assert suite.benchmark_id == "CFI-GRAPH-VS-TABULAR-01"
        assert len(suite.results) == 3

        tab = suite.results[FeatureParadigm.TABULAR_ONLY.value]
        grp = suite.results[FeatureParadigm.GRAPH_ONLY.value]
        hyb = suite.results[FeatureParadigm.TABULAR_PLUS_GRAPH.value]

        assert hyb.feature_count == tab.feature_count + grp.feature_count
        assert hyb.pr_auc > 0.0

        up = suite.uplift
        assert isinstance(up, GraphVsTabularUpliftResult)
        assert abs(up.delta_pr_auc - (hyb.pr_auc - tab.pr_auc)) < 1e-4
        assert abs(up.hybrid_pr_auc - hyb.pr_auc) < 1e-4
        assert abs(up.tabular_standalone_pr_auc - tab.pr_auc) < 1e-4

        # Markdown report generation
        md_table = format_markdown_table(suite)
        assert "Tabular Only" in md_table
        assert "Graph Only" in md_table
        assert "Tabular + Graph (Hybrid)" in md_table
        assert "Uplift" in md_table

        # File artifact serialization
        saved_file = tmp_path / "graph_vs_tabular_results.json"
        assert saved_file.exists()
        with open(saved_file, encoding="utf-8") as f:
            data = json.load(f)
            assert data["benchmark_id"] == "CFI-GRAPH-VS-TABULAR-01"


class TestSyntheticTransactionGraph:
    """Verifies synthetic transaction network generation and k-hop neighborhood aggregations."""

    def test_density_graph_generation(self) -> None:
        """Verifies graph generation with specified average degree."""
        n = 500
        target_degree = 4
        graph = SyntheticTransactionGraph(n_nodes=n, seed=42)
        graph.generate_base_features_and_fraud(0.03)
        graph.build_density_graph(target_avg_degree=target_degree)

        total_edges = sum(len(neighbors) for neighbors in graph.adj) // 2
        actual_avg_degree = (2.0 * total_edges) / n

        # Degree should be close to target (within tolerance due to multi-graph filtering)
        assert abs(actual_avg_degree - target_degree) < 1.0
        assert graph.X_tab.shape == (n, 12)
        assert len(graph.y) == n

    def test_scale_free_graph_hub_characteristics(self) -> None:
        """Verifies Barabási-Albert preferential attachment creates hubs."""
        n = 500
        graph = SyntheticTransactionGraph(n_nodes=n, seed=42)
        graph.generate_base_features_and_fraud(0.03)
        graph.build_scale_free_graph(edges_per_new_node=3)

        degrees = [len(neighbors) for neighbors in graph.adj]
        max_degree = max(degrees)
        avg_degree = float(np.mean(degrees))

        # Scale-free graphs have max degree significantly higher than average
        assert max_degree > avg_degree * 2.5

    def test_clustered_graph_clustering_coefficient(self) -> None:
        """Verifies Stochastic Block Model creates community clusters."""
        n = 400
        graph = SyntheticTransactionGraph(n_nodes=n, seed=42)
        graph.generate_base_features_and_fraud(0.03)
        graph.build_clustered_graph(n_clusters=8, pin=0.15, pout=0.001)

        cc = graph.calculate_clustering_coefficient()
        assert cc > 0.05

    def test_isolated_node_injection(self) -> None:
        """Verifies artificial severing of edges for isolated node evaluation."""
        n = 300
        graph = SyntheticTransactionGraph(n_nodes=n, seed=42)
        graph.generate_base_features_and_fraud(0.03)
        graph.build_density_graph(target_avg_degree=4)

        # Inject 20% isolated nodes
        graph.inject_isolated_nodes(fraction=0.20)
        degrees = [len(neighbors) for neighbors in graph.adj]
        zero_degrees = sum(1 for d in degrees if d == 0)

        assert zero_degrees >= int(n * 0.18)

    def test_k_hop_embedding_dimensions(self) -> None:
        """Verifies multi-hop embedding dimension scaling."""
        n = 200
        graph = SyntheticTransactionGraph(n_nodes=n, seed=42)
        graph.generate_base_features_and_fraud(0.03)
        graph.build_density_graph(target_avg_degree=4)

        # 0-hop: 4 features
        emb_0 = graph.compute_k_hop_embeddings(max_hops=0)
        assert emb_0.shape == (n, 4)

        # 1-hop: 8 features (4 base + 4 neighbor)
        emb_1 = graph.compute_k_hop_embeddings(max_hops=1)
        assert emb_1.shape == (n, 8)

        # 2-hop: 12 features (4 base + 4 1-hop + 4 2-hop)
        emb_2 = graph.compute_k_hop_embeddings(max_hops=2)
        assert emb_2.shape == (n, 12)

        # 3-hop: 16 features
        emb_3 = graph.compute_k_hop_embeddings(max_hops=3)
        assert emb_3.shape == (n, 16)


class TestTopologySensitivitySweep:
    """Verifies end-to-end execution of the topology sensitivity sweep."""

    def test_run_topology_sensitivity_sweep_execution(self, tmp_path: Path) -> None:
        """Executes lightweight sensitivity sweep and validates all sub-results."""
        cfg = TopologySensitivityConfig(
            n_nodes=600,
            degree_sweep=[1, 4, 16],
            hop_sweep=[0, 1, 2],
            seed=42,
            save_artifacts=True,
            output_dir=str(tmp_path),
        )

        suite = run_topology_sensitivity_sweep(cfg)

        assert isinstance(suite, TopologySensitivitySuiteResult)
        assert suite.benchmark_id == "CFI-TOPOLOGY-SENSITIVITY-01"

        # 1. Degree sweep checks
        assert len(suite.degree_sweep_results) == 3
        for deg_res in suite.degree_sweep_results:
            assert isinstance(deg_res, DegreePointResult)
            assert 0.0 <= deg_res.tabular_pr_auc <= 1.0
            assert 0.0 <= deg_res.hybrid_pr_auc <= 1.0
            assert deg_res.density_regime in ("SPARSE", "CRITICAL_FRAUD", "DENSE_INTERMEDIATE")

        # 2. Hop sweep checks
        assert len(suite.hop_sweep_results) == 3
        for hop_res in suite.hop_sweep_results:
            assert isinstance(hop_res, HopPointResult)
            assert 0.0 <= hop_res.pr_auc <= 1.0
            assert hop_res.aggregation_latency_ms >= 0.0

        # 3. Topology type checks
        assert len(suite.topology_type_results) == 3
        for top_res in suite.topology_type_results:
            assert isinstance(top_res, TopologyModelPointResult)
            assert top_res.topology_type in (
                GraphStructureType.RANDOM_ERDOS_RENYI,
                GraphStructureType.SCALE_FREE_BARABASI,
                GraphStructureType.CLUSTERED_COMMUNITIES,
            )

        # 4. Isolation checks
        assert len(suite.isolation_results) == 4
        for iso_res in suite.isolation_results:
            assert isinstance(iso_res, IsolationDegradationPointResult)
            assert 0.0 <= iso_res.isolated_fraction <= 0.50
            assert 0.0 <= iso_res.retained_uplift_pct <= 100.0

        # 5. Formatted markdown output
        tables_md = format_sensitivity_tables(suite)
        assert "Network Density & Degree Sweep" in tables_md
        assert "Search Depth & $k$-Hop Expansion Sweep" in tables_md
        assert "Structural Graph Topology Comparison" in tables_md
        assert "Disconnected Component & Isolated Node Resilience" in tables_md

        # 6. Artifact persistence
        saved_file = tmp_path / "topology_sensitivity_results.json"
        assert saved_file.exists()
        with open(saved_file, encoding="utf-8") as f:
            data = json.load(f)
            assert data["benchmark_id"] == "CFI-TOPOLOGY-SENSITIVITY-01"
            assert "optimal_degree_range" in data


class TestPydanticSchemaRoundTrip:
    """Verifies schema validation and JSON round-trip stability."""

    def test_graph_vs_tabular_suite_roundtrip(self) -> None:
        """Tests serialization and deserialization of GraphVsTabularSuiteResult."""
        cfg = GraphVsTabularConfig(n_samples=500, epochs=2)
        suite = run_graph_vs_tabular_ablation(cfg)

        json_str = suite.model_dump_json()
        deserialized = GraphVsTabularSuiteResult.model_validate_json(json_str)

        assert deserialized.benchmark_id == suite.benchmark_id
        assert deserialized.config.n_samples == suite.config.n_samples
        assert len(deserialized.results) == 3
        assert deserialized.uplift.delta_pr_auc == suite.uplift.delta_pr_auc

    def test_topology_sensitivity_suite_roundtrip(self) -> None:
        """Tests serialization and deserialization of TopologySensitivitySuiteResult."""
        cfg = TopologySensitivityConfig(n_nodes=400, degree_sweep=[2, 4], hop_sweep=[0, 1])
        suite = run_topology_sensitivity_sweep(cfg)

        json_str = suite.model_dump_json()
        deserialized = TopologySensitivitySuiteResult.model_validate_json(json_str)

        assert deserialized.benchmark_id == suite.benchmark_id
        assert len(deserialized.degree_sweep_results) == 2
        assert len(deserialized.hop_sweep_results) == 2
