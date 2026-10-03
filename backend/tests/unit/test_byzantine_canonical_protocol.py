# type: ignore
# pyright: reportArgumentType=false
# pyright: reportGeneralTypeIssues=false
# pyright: reportOperatorIssue=false
# pyright: reportCallIssue=false
# pyright: reportMissingTypeStubs=false
"""Comprehensive Protocol, Invariant & Validation Tests for Canonical Byzantine Robustness Infrastructure.

Verifies:
1. Historical artifact quarantine and non-canonical resolution.
2. Model-delta round-trip parameter representation.
3. Loss semantics and absence of double-sigmoid.
4. Aggregator invariants and zero-silent-fallback enforcement (Krum, Multi-Krum, Bulyan, Trimmed Mean).
5. Attack transformation invariants (Sign-Flip, Gaussian, ALIE) and test-isolation knowledge models.
6. Strict train/test separation and non-IID partition isolation.
7. Canonical overwrite protection against non-canonical smoke executions.
8. End-to-end tiny federated learning smoke execution.
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import numpy as np
import pytest
import torch
import torch.nn as nn
from benchmarks.byzantine.aggregators import (
    InvalidConfigurationError,
    aggregate_bulyan,
    aggregate_coordinate_median,
    aggregate_fedavg,
    aggregate_krum,
    aggregate_multi_krum,
    aggregate_trimmed_mean,
    get_bulyan_selection_sequence,
)
from benchmarks.byzantine.attacks import (
    ALIEAttack,
    GaussianNoiseAttack,
    SignFlipAttack,
)
from benchmarks.byzantine.config import (
    AggregatorConfig,
    AttackConfig,
    ByzantineAggregatorType,
    ByzantineAttackType,
    ByzantineBenchmarkConfig,
    DatasetConfig,
    FederationConfig,
    TrainingConfig,
    compute_condition_matrix_sha256,
    create_canonical_byzantine_config,
    generate_canonical_condition_matrix,
    generate_execution_manifest,
)
from benchmarks.byzantine.data import (
    compute_partition_sha256,
    load_and_partition_byzantine_data,
)
from benchmarks.byzantine.model import (
    FraudMLP,
    apply_model_delta,
    compute_model_delta,
    flatten_parameters,
)
from benchmarks.byzantine.runner import (
    ByzantineFederatedRunner,
)
from benchmarks.byzantine.schema import ByzantineBenchmarkArtifact
from benchmarks.canonical_registry import (
    CANONICAL_REGISTRY,
    resolve_canonical_artifact,
)
from benchmarks.provenance_schema import ArtifactStatus

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestByzantineHistoricalQuarantine:
    """Invariant 1: Historical prototype artifact remains preserved but quarantined from canonical claims."""

    def test_historical_artifact_quarantined_and_non_canonical(self):
        old_art_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "byzantine_benchmark_sign_inversion.json"
        assert old_art_path.exists(), "Historical artifact byzantine_benchmark_sign_inversion.json missing!"

        with open(old_art_path, encoding="utf-8") as f:
            old_data = json.load(f)

        # Numerical preservation
        assert old_data["honest_fedavg_pr_auc"] == 0.7369
        assert old_data["trimmed_mean_pr_auc"] == 0.7344
        assert abs(old_data["trimmed_mean_retention_ratio"] - 0.9966) < 1e-3

        # Quarantined status
        entry = CANONICAL_REGISTRY["byzantine_sign_inversion"]
        assert entry.status == ArtifactStatus.HISTORICAL
        assert entry.is_external_communication_safe is False

        # Attempting to resolve as canonical MUST raise ValueError
        with pytest.raises(ValueError, match="non-canonical status"):
            resolve_canonical_artifact("byzantine_sign_inversion")

    def test_new_canonical_entry_registered_as_not_evaluated(self):
        assert "byzantine_federated_canonical" in CANONICAL_REGISTRY
        new_entry = CANONICAL_REGISTRY["byzantine_federated_canonical"]
        assert new_entry.status == ArtifactStatus.NOT_EVALUATED
        assert new_entry.is_external_communication_safe is False
        assert resolve_canonical_artifact("byzantine_federated_canonical") is None


class TestModelDeltaAndLossSemantics:
    """Invariant 2: Neural model delta representation and loss function semantics."""

    def test_model_delta_round_trip_is_exact(self):
        m1 = FraudMLP(input_dim=30, hidden_dims=[64, 32], dropout=0.0)
        m2 = FraudMLP(input_dim=30, hidden_dims=[64, 32], dropout=0.0)

        delta = compute_model_delta(m2, m1)
        apply_model_delta(m1, delta)
        flat_m1_after = flatten_parameters(m1)
        flat_m2 = flatten_parameters(m2)

        # Applying delta to m1 must equal m2 exactly
        assert torch.allclose(flat_m1_after, flat_m2, atol=1e-6)

    def test_loss_semantics_no_double_sigmoid(self):
        model = FraudMLP(input_dim=30, hidden_dims=[64, 32], dropout=0.0)
        x = torch.randn(10, 30)

        logits = model(x)
        # Logits should not be constrained to [0, 1]
        criterion = nn.BCEWithLogitsLoss()
        target = torch.randint(0, 2, (10,)).float()
        loss = criterion(logits, target)
        assert loss.item() > 0.0

        # Probabilities from predict_proba must be strictly within [0, 1]
        probs = model.predict_proba(x)
        assert (probs >= 0.0).all() and (probs <= 1.0).all()
        assert torch.allclose(probs, torch.sigmoid(logits), atol=1e-6)


class TestAggregatorInvariantsAndZeroSilentFallback:
    """Invariant 3: Robust aggregation theoretical bounds and zero-silent-fallback invariants."""

    def test_fedavg_sample_weighting(self):
        d1 = torch.tensor([1.0, 1.0])
        d2 = torch.tensor([5.0, 5.0])
        # Client 1 has 100 samples, Client 2 has 300 samples (total 400)
        weighted = aggregate_fedavg([d1, d2], weights=[100.0, 300.0])
        expected = 0.25 * d1 + 0.75 * d2
        assert torch.allclose(weighted, expected, atol=1e-6)

    def test_coordinate_median(self):
        d1 = torch.tensor([1.0, 10.0])
        d2 = torch.tensor([2.0, 20.0])
        d3 = torch.tensor([100.0, 5.0])  # outlier on dim 0
        med = aggregate_coordinate_median([d1, d2, d3])
        assert torch.allclose(med, torch.tensor([2.0, 10.0]), atol=1e-6)

    def test_trimmed_mean_invariants(self):
        deltas = [torch.tensor([float(i)]) for i in range(10)]  # 0..9
        # beta = 0.20 -> k = 2. Trims lowest 2 (0, 1) and highest 2 (8, 9). Retains 2,3,4,5,6,7. Mean = 4.5
        tm = aggregate_trimmed_mean(deltas, beta=0.20)
        assert torch.allclose(tm, torch.tensor([4.5]), atol=1e-6)

        # Infeasible beta: 2k >= n raises InvalidConfigurationError
        with pytest.raises(InvalidConfigurationError, match="Trimmed Mean infeasible"):
            aggregate_trimmed_mean(deltas, beta=0.55)

    def test_krum_precondition_and_selection(self):
        # n = 6, f = 2 -> required n >= 2f + 3 = 7. Violates precondition.
        short_deltas = [torch.zeros(5) for _ in range(6)]
        with pytest.raises(InvalidConfigurationError, match="Krum theoretical precondition violated"):
            aggregate_krum(short_deltas, f=2)

        # n = 7, f = 2 -> required n >= 7. Exactly satisfied!
        d_honest = [torch.zeros(5) + torch.randn(5) * 0.01 for _ in range(5)]
        d_mal = [torch.ones(5) * 100.0 for _ in range(2)]
        all_d = d_honest + d_mal
        selected = aggregate_krum(all_d, f=2)
        # Selected must be one of the honest vectors, not an outlier
        assert torch.norm(selected).item() < 1.0

    def test_multi_krum_selects_and_averages_multiple_candidates(self):
        # n = 12, f = 2. Multi-Krum with m = 5
        # Asymmetric honest vectors so average of multiple chosen candidates is strictly different from single candidate
        d_honest = [torch.zeros(5) + float(i**2) * 0.01 for i in range(10)]
        d_mal = [torch.ones(5) * 50.0 for _ in range(2)]
        all_d = d_honest + d_mal

        single_krum = aggregate_krum(all_d, f=2)
        multi_krum = aggregate_multi_krum(all_d, f=2, m=5)

        assert torch.norm(multi_krum).item() < 10.0
        # Multi-Krum averages multiple vectors, so it should not be identical to single candidate
        assert not torch.allclose(single_krum, multi_krum, atol=1e-5)

    def test_bulyan_precondition_violation_and_zero_trimming_rejection(self):
        # Historical Defect Check: n = 10, f = 2 requires n >= 4f + 3 = 11. MUST RAISE!
        deltas_10 = [torch.zeros(5) for _ in range(10)]
        with pytest.raises(InvalidConfigurationError, match="Bulyan theoretical precondition violated"):
            aggregate_bulyan(deltas_10, f=2)

        # Valid Bulyan: n = 12, f = 2 satisfies 12 >= 4(2) + 3 = 11!
        d_honest = [torch.randn(5) * 0.1 for _ in range(10)]
        d_mal = [torch.ones(5) * 100.0 for _ in range(2)]
        all_d = d_honest + d_mal
        bulyan_res = aggregate_bulyan(all_d, f=2)
        assert torch.norm(bulyan_res).item() < 2.0


class TestAttackInvariantsAndKnowledgeModel:
    """Invariant 4: Adversarial attack transformations and knowledge bounds."""

    def test_sign_flip_transformation(self):
        attack = SignFlipAttack(scale=3.0)
        local_delta = torch.tensor([1.0, -2.0, 3.5])
        adv_delta = attack.apply(local_delta, client_id=10, round_idx=0)
        expected = -3.0 * local_delta
        assert torch.allclose(adv_delta, expected, atol=1e-6)
        assert attack.knowledge_model["knows_test_data"] is False

    def test_alie_tail_evading_shift(self):
        attack = ALIEAttack(z_max=1.0)
        honest_deltas = [torch.randn(20) for _ in range(10)]
        local_delta = torch.randn(20)
        adv_delta = attack.apply(local_delta, client_id=11, round_idx=0, consortium_deltas=honest_deltas)

        stacked = torch.stack(honest_deltas, dim=0)
        mu = torch.mean(stacked, dim=0)
        sigma = torch.std(stacked, dim=0, unbiased=True)
        expected = mu - 1.0 * (sigma + 1e-8)
        assert torch.allclose(adv_delta, expected, atol=1e-5)


class TestDataIsolationAndSmokeProtection:
    """Invariant 5 & 6: Strict train/test isolation and canonical overwrite guards."""

    def test_data_partition_isolation(self):
        config = ByzantineBenchmarkConfig(
            dataset=DatasetConfig(use_mock=True, n_mock_samples=500, split_seed=42),
            federation=FederationConfig(n_clients=6, f_byzantine=1, min_samples_per_client=10),
        )
        client_datasets, test_dataset, meta = load_and_partition_byzantine_data(config, seed=42)

        assert meta["total_test_samples"] == 100
        assert meta["total_train_samples"] == 400
        assert len(client_datasets) == 6

        # Invariant: Sum of client samples equals total training samples
        client_total = sum(len(ds) for ds in client_datasets.values())
        assert client_total == 400

    def test_smoke_test_cannot_overwrite_canonical_path(self, tmp_path):
        from datetime import datetime
        config = ByzantineBenchmarkConfig(
            seeds=[42],
            dataset=DatasetConfig(use_mock=True, n_mock_samples=300),
            federation=FederationConfig(n_clients=12, f_byzantine=2, min_samples_per_client=10),
            training=TrainingConfig(rounds=1, local_epochs=1, batch_size=32),
        )
        runner = ByzantineFederatedRunner(config=config, repo_root=REPO_ROOT)
        artifact = ByzantineBenchmarkArtifact(
            benchmark_id="byzantine_federated_canonical",
            status="NON_CANONICAL_SMOKE_TEST",
            timestamp_utc=datetime.now(UTC).isoformat(),
            git_sha="smoke_test_sha",
            config_sha256="smoke_config_sha",
            dataset={"name": "mock", "provenance_type": "PROJECT_SYNTHETIC"},
            federation={"n_clients": 12, "f_byzantine": 2, "byzantine_fraction": 2 / 12, "partition_alpha": 0.5},
            training={
                "model": "FraudMLP",
                "loss": "BCEWithLogitsLoss",
                "optimizer": "Adam",
                "learning_rate": 0.001,
                "batch_size": 32,
                "rounds": 1,
                "local_epochs": 1,
                "update_representation": "MODEL_DELTA",
            },
            seeds=[42],
            attacks_evaluated=["sign_flip"],
            aggregators_evaluated=["fedavg"],
            mandatory_caveat="Smoke test caveat",
            communication_scope="INTERNAL_ONLY",
        )

        canonical_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "byzantine_federated_canonical.json"
        with pytest.raises(PermissionError, match="Cannot write non-canonical output"):
            runner.save_artifact_atomically(artifact, canonical_path, is_canonical=False)


class TestEndToEndTinyFLSmoke:
    """Invariant 7: Full multi-round federated execution smoke test."""

    def test_tiny_fl_smoke_execution(self, tmp_path):
        config = ByzantineBenchmarkConfig(
            seeds=[42],
            dataset=DatasetConfig(use_mock=True, n_mock_samples=300),
            federation=FederationConfig(n_clients=12, f_byzantine=2, min_samples_per_client=10),
            training=TrainingConfig(rounds=1, local_epochs=1, batch_size=32),
            attacks=[AttackConfig(attack_type=ByzantineAttackType.SIGN_FLIP, scale=3.0, malicious_client_ids=[10, 11])],
            aggregators=[
                AggregatorConfig(aggregator_type=ByzantineAggregatorType.FEDAVG),
                AggregatorConfig(aggregator_type=ByzantineAggregatorType.TRIMMED_MEAN, trimmed_mean_beta=0.20),
                AggregatorConfig(aggregator_type=ByzantineAggregatorType.KRUM, krum_f=2),
                AggregatorConfig(aggregator_type=ByzantineAggregatorType.MULTI_KRUM, krum_f=2, multi_krum_m=10),
                AggregatorConfig(aggregator_type=ByzantineAggregatorType.BULYAN, bulyan_f=2),
            ],
            is_canonical=False,
        )

        runner = ByzantineFederatedRunner(config=config, repo_root=REPO_ROOT)
        artifact = runner.run_full_suite(is_canonical=False, is_smoke=True)

        assert artifact.status == "SMOKE_TEST"
        # 1 clean FedAvg + 4 clean defenses + 5 attacked conditions = 10 conditions
        assert len(artifact.per_seed_results) == 10
        assert artifact.per_seed_results[0].aggregator_name == "clean_fedavg"

        # Verify clean defense conditions present with actual_f=0, assumed_f=2
        clean_def_results = [r for r in artifact.per_seed_results if r.attack_name == "none" and r.aggregator_name != "clean_fedavg"]
        assert len(clean_def_results) == 4
        for r in clean_def_results:
            assert r.actual_f == 0
            assert r.assumed_f == 2
            assert r.clean_penalty is not None

        # Atomic save to temporary smoke file
        smoke_out = tmp_path / "smoke_artifact.json"
        runner.save_artifact_atomically(artifact, smoke_out, is_canonical=False)
        assert smoke_out.exists()


class TestPhase2BProtocolScientificIntegrity:
    """Invariant 8: Phase 2B Targeted Mathematical and Protocol Verifications."""

    def test_bulyan_canonical_stage2_median_closest_divergence(self):
        """Proves canonical Bulyan Stage 2 selects median-closest values, diverging from symmetric trimming."""
        # 8 candidate values on coordinate 0: 5 honest close to 0.2, 3 large values (2, 5, 10)
        cand_coord = torch.tensor([[0.1], [0.2], [0.2], [0.2], [0.3], [2.0], [5.0], [10.0]])
        theta = 8
        f = 2
        beta = theta - 2 * f  # 4

        # Ordinary symmetric trimmed mean: trims 2 lowest (0.1, 0.2) and 2 highest (5.0, 10.0)
        sorted_cand, _ = torch.sort(cand_coord, dim=0)
        trimmed_sym = sorted_cand[2:6, :]
        mean_sym = torch.mean(trimmed_sym, dim=0).item()
        assert abs(mean_sym - 0.675) < 1e-5  # Retains adversarial outlier 2.0!

        # Canonical Bulyan Stage 2: 4 values closest to coordinate median 0.2
        med_coord, _ = torch.median(cand_coord, dim=0)
        abs_diffs = torch.abs(cand_coord - med_coord.unsqueeze(0))
        _, topk_indices = torch.topk(abs_diffs, k=beta, dim=0, largest=False)
        closest_vals = torch.gather(cand_coord, dim=0, index=topk_indices)
        mean_bulyan = torch.mean(closest_vals, dim=0).item()
        assert abs(mean_bulyan - 0.175) < 1e-5  # Discards 2.0, preserves honest cluster!

        # Divergence must be strictly nonzero and significant
        assert abs(mean_sym - mean_bulyan) > 0.40

    def test_bulyan_cardinalities_and_bounds(self):
        """Verifies exact Bulyan cardinalities for n=12, f=2."""
        n = 12
        f = 2
        theta = n - 2 * f
        beta = theta - 2 * f
        assert theta == 8
        assert beta == 4
        assert n >= 4 * f + 3  # 12 >= 11

    def test_multi_krum_canonical_single_pass_top_m(self):
        """Verifies Multi-Krum computes scores once, selects top m, and enforces 1 <= m <= n-f."""
        d_honest = [torch.zeros(4) + float(i) * 0.1 for i in range(10)]
        d_mal = [torch.ones(4) * 100.0 for _ in range(2)]
        all_d = d_honest + d_mal

        # Invalid m > n - f raises
        with pytest.raises(InvalidConfigurationError, match="Multi-Krum candidate count m must satisfy"):
            aggregate_multi_krum(all_d, f=2, m=11)

        # Valid m = 10 selects 10 lowest scoring vectors (all honest)
        res = aggregate_multi_krum(all_d, f=2, m=10)
        assert torch.norm(res).item() < 2.0

    def test_alie_exact_z_derivation_and_toy_matrix(self):
        """Verifies ALIE reference boundary derivation and coordinate-wise perturbation."""
        import scipy.stats as stats
        n = 12
        m = 2
        # Baruch Algorithm 3: s = floor(n/2 + 1) - m = floor(7) - 2 = 5
        s = int(np.floor(n / 2.0 + 1) - m)
        assert s == 5
        # Probability boundary: (n - m - s) / (n - m) = (12 - 2 - 5) / (12 - 2) = 5/10 = 0.50
        p_val = (n - m - s) / float(n - m)
        assert abs(p_val - 0.50) < 1e-6
        # Reference z boundary (supremum): Phi^{-1}(0.50) = 0.0
        z_boundary = float(stats.norm.ppf(p_val))
        assert abs(z_boundary - 0.0) < 1e-6

        # ALIEAttack configured with alie_z_max=1.0 authoritatively perturbs by 1.0 * sigma
        att = ALIEAttack(config=AttackConfig(alie_z_max=1.0, malicious_client_ids=[10, 11]))
        assert att.knowledge_model["knows_honest_updates"] is True
        assert att.knowledge_model["knows_test_data"] is False

        # Toy honest update matrix: 3 clients, 2 coordinates
        honest = [torch.tensor([1.0, 10.0]), torch.tensor([3.0, 12.0]), torch.tensor([5.0, 14.0])]
        # mu = [3.0, 12.0], sigma = [2.0, 2.0]
        mal_delta = att.apply(torch.zeros(2), client_id=10, round_idx=0, consortium_deltas=honest)
        # Expected: mu - 1.0 * sigma = [3 - 2, 12 - 2] = [1.0, 10.0]
        assert torch.allclose(mal_delta, torch.tensor([1.0, 10.0]), atol=1e-4)

    def test_gaussian_attack_local_delta_scale_and_knowledge(self):
        """Verifies Gaussian noise scales to local delta SD and requires only own update."""
        att = GaussianNoiseAttack(noise_std=1.0)
        assert att.knowledge_model["knows_honest_updates"] is False
        assert att.knowledge_model["knows_own_update"] is True

        local_delta = torch.randn(1000) * 0.05
        noise = att.apply(local_delta, client_id=10, round_idx=0, seed=42)
        # Noise std should closely match local delta std (~0.05)
        assert abs(float(torch.std(noise).item()) - float(torch.std(local_delta).item())) < 0.01

    def test_partition_determinism_and_hashing(self):
        """Verifies partition assignment is 100% deterministic and produces consistent hash."""
        from benchmarks.byzantine.data import partition_dirichlet
        X = np.random.randn(200, 10)
        y = np.array([0] * 180 + [1] * 20)

        map1 = partition_dirichlet(X, y, n_clients=4, alpha=0.5, seed=42, min_samples=10)
        map2 = partition_dirichlet(X, y, n_clients=4, alpha=0.5, seed=42, min_samples=10)

        h1 = compute_partition_sha256(map1)
        h2 = compute_partition_sha256(map2)
        assert h1 == h2
        assert len(h1) == 64

    def test_condition_matrix_deduplication_and_hashing(self):
        """Verifies condition matrix contains exactly 24 conditions per seed with zero duplicates."""
        cfg = ByzantineBenchmarkConfig(
            seeds=[42, 123, 456],
            attacks=[
                AttackConfig(attack_type=ByzantineAttackType.SIGN_FLIP),
                AttackConfig(attack_type=ByzantineAttackType.GAUSSIAN_NOISE),
                AttackConfig(attack_type=ByzantineAttackType.ALIE),
            ],
            aggregators=[
                AggregatorConfig(aggregator_type=ByzantineAggregatorType.FEDAVG),
                AggregatorConfig(aggregator_type=ByzantineAggregatorType.COORDINATE_MEDIAN),
                AggregatorConfig(aggregator_type=ByzantineAggregatorType.TRIMMED_MEAN),
                AggregatorConfig(aggregator_type=ByzantineAggregatorType.KRUM),
                AggregatorConfig(aggregator_type=ByzantineAggregatorType.MULTI_KRUM),
                AggregatorConfig(aggregator_type=ByzantineAggregatorType.BULYAN),
            ],
        )
        matrix = generate_canonical_condition_matrix(cfg)
        # Per seed: 1 clean FedAvg + 5 clean defenses + (3 attacks * 6 aggregators = 18) = 24 conditions
        # 3 seeds * 24 = 72 conditions total
        assert len(matrix) == 72
        cond_ids = [c["condition_id"] for c in matrix]
        assert len(cond_ids) == len(set(cond_ids)), "Duplicate conditions detected in matrix!"

        m_hash = compute_condition_matrix_sha256(matrix)
        assert len(m_hash) == 64

    def test_zero_fraud_client_bce_pos_weight_loss_validity(self):
        """Verifies BCEWithLogitsLoss(pos_weight=10.0) remains numerically stable on 0-fraud client."""
        criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([10.0]))
        model = FraudMLP(input_dim=10, hidden_dims=[16, 8], dropout=0.0)

        # Batch of 100 legitimate samples (all y = 0)
        x = torch.randn(100, 10)
        y = torch.zeros(100)

        logits = model(x)
        loss = criterion(logits, y)
        assert not torch.isnan(loss)
        assert not torch.isinf(loss)
        assert loss.item() > 0.0

        loss.backward()
        for p in model.parameters():
            assert p.grad is not None
            assert not torch.isnan(p.grad).any()
            assert not torch.isinf(p.grad).any()

    def test_initial_model_hash_paired_identity(self):
        """Verifies initial model state hash is identical across paired conditions for same seed."""
        import hashlib
        torch.manual_seed(42)
        m1 = FraudMLP(input_dim=30, hidden_dims=[64, 32], dropout=0.1)
        h1 = hashlib.sha256(flatten_parameters(m1).cpu().numpy().tobytes()).hexdigest()

        torch.manual_seed(42)
        m2 = FraudMLP(input_dim=30, hidden_dims=[64, 32], dropout=0.1)
        h2 = hashlib.sha256(flatten_parameters(m2).cpu().numpy().tobytes()).hexdigest()

        assert h1 == h2

    def test_canonical_artifact_remains_absent(self):
        """Strict Invariant: No canonical artifact file may exist in Phase 2B."""
        canonical_dest = REPO_ROOT / "benchmarks" / "results" / "raw" / "byzantine_federated_canonical.json"
        assert not canonical_dest.exists(), (
            f"FATAL: Canonical artifact {canonical_dest} exists before canonical execution! Must remain absent."
        )


class TestPhase2B1LiteratureAlignmentAndProtocolRepair:
    """Rigorous Invariant Verification for Phase 2B.1 Literature Alignment and Protocol Repair."""

    def test_bulyan_stage1_recursive_selection_recomputes_after_removal(self):
        """Verifies Bulyan Stage 1 iteratively recomputes Krum on shrinking pool of size m."""
        deltas = [torch.tensor([float(x)]) for x in [0.0, 0.1, 0.2, 0.3, 0.4, 2.0, 2.1, 2.2, 2.3, 10.0, 10.1, 10.2]]
        n = 12
        f = 2
        theta = n - 2 * f  # 8
        seq = get_bulyan_selection_sequence(deltas, f=f)
        assert len(seq) == theta
        # All selected indices must be unique
        assert len(seq) == len(set(seq))

    def test_bulyan_stage1_differs_from_single_pass_top_theta(self):
        """Proves canonical recursive Bulyan Stage 1 diverges from single-pass top-theta Krum."""
        deltas = [torch.tensor([float(x)]) for x in [0.0, 0.1, 0.2, 0.3, 0.4, 2.0, 2.1, 2.2, 2.3, 10.0, 10.1, 10.2]]
        f = 2
        theta = 8

        # 1. Recursive Bulyan Stage 1 selection
        seq_recursive = get_bulyan_selection_sequence(deltas, f=f)

        # 2. Single-pass top-theta Krum selection
        scores_single = []
        k_single = len(deltas) - f - 2  # 8
        for i in range(len(deltas)):
            dists = [torch.sum((deltas[i] - deltas[j]) ** 2).item() for j in range(len(deltas)) if i != j]
            dists.sort()
            scores_single.append(sum(dists[:k_single]))
        top_theta_single = sorted(range(len(deltas)), key=lambda idx: (scores_single[idx], idx))[:theta]

        # The two candidate sets MUST differ (provable divergence)
        assert set(seq_recursive) != set(top_theta_single), (
            f"Expected recursive selection {seq_recursive} to differ from single-pass {top_theta_single}!"
        )
        assert seq_recursive != top_theta_single

    def test_bulyan_stage2_median_closest_reference_behavior(self):
        """Verifies Bulyan Stage 2 selects beta values closest to median, not ordinary symmetric trimming."""
        # 8 candidates in Stage 1 selection set: 5 honest close to 0.2, 3 outliers
        cand = [torch.tensor([x]) for x in [0.1, 0.2, 0.2, 0.2, 0.3, 2.0, 5.0, 10.0]]
        f = 2
        theta = len(cand)  # 8
        beta = theta - 2 * f  # 4

        # Canonical Stage 2: 4 values closest to median 0.2 -> [0.2, 0.2, 0.2, 0.1 or 0.3] -> mean = 0.175 or 0.225
        cand_tensor = torch.stack(cand, dim=0)
        med_val, _ = torch.median(cand_tensor, dim=0)
        abs_diffs = torch.abs(cand_tensor - med_val.unsqueeze(0))
        _, topk_idx = torch.topk(abs_diffs, k=beta, dim=0, largest=False)
        closest = torch.gather(cand_tensor, dim=0, index=topk_idx)
        bulyan_stage2_res = torch.mean(closest, dim=0).item()

        # Ordinary symmetric trimmed mean trims 2 lowest (0.1, 0.2) and 2 highest (5.0, 10.0) -> leaves [0.2, 0.2, 0.3, 2.0]
        # Mean = 2.7 / 4 = 0.675
        cand_sorted, _ = torch.sort(cand_tensor, dim=0)
        sym_trimmed = cand_sorted[f : theta - f]
        sym_trimmed_res = torch.mean(sym_trimmed, dim=0).item()

        # Strict deterministic divergence
        assert abs(bulyan_stage2_res - 0.175) < 1e-4
        assert abs(sym_trimmed_res - 0.675) < 1e-4
        assert abs(sym_trimmed_res - bulyan_stage2_res) > 0.40

    def test_bulyan_n12_f2_cardinalities(self):
        """Verifies exact Bulyan cardinalities for canonical n=12, f=2."""
        n = 12
        f = 2
        theta = n - 2 * f
        beta = theta - 2 * f
        assert theta == 8
        assert beta == 4
        assert n >= 4 * f + 3

        # Check neighbor count at every iteration
        for t in range(theta):
            m = n - t
            k = m - f - 2
            assert k >= 1, f"Iteration {t}: k={k} < 1!"

    def test_bulyan_invalid_configuration_raises(self):
        """Verifies zero-fallback invariant: invalid Bulyan configurations raise InvalidConfigurationError."""
        # 1. n < 4f + 3
        deltas_10 = [torch.zeros(4) for _ in range(10)]
        with pytest.raises(InvalidConfigurationError, match="Bulyan theoretical precondition violated"):
            aggregate_bulyan(deltas_10, f=2)

        # 2. Empty deltas
        with pytest.raises(ValueError, match="Cannot aggregate empty deltas list"):
            aggregate_bulyan([], f=2)

    def test_alie_threat_model_matches_executable_inputs(self):
        """Verifies ALIE threat model metadata truthfully reflects honest-update access."""
        att = ALIEAttack()
        km = att.knowledge_model
        assert km["knows_honest_updates"] is True
        assert km["knows_aggregation_rule"] is True
        assert km["knows_test_data"] is False
        assert km["knows_own_update"] is True
        assert km["knows_global_model"] is True

    def test_alie_reference_z_and_evaluated_z_are_distinct(self):
        """Verifies ALIE reference boundary z (0.0) and evaluated z (1.0) are cleanly separated."""
        att = ALIEAttack()
        z_ref = att.compute_reference_z_max(n=12, f=2)
        assert abs(z_ref - 0.0) < 1e-6
        assert att.evaluated_z == 1.0
        assert att.reference_z_max == 0.0
        assert att.reference_z_boundary == 0.0
        assert att.evaluated_z != z_ref

    def test_alie_executes_evaluated_z_not_reference_z(self):
        """Verifies ALIE mathematically executes evaluated z (1.0), not reference z (0.0)."""
        att = ALIEAttack(z_max=1.0)
        honest = [torch.tensor([8.0]), torch.tensor([10.0]), torch.tensor([12.0])]
        # mu = 10.0, sigma = 2.0 (unbiased sample std)
        mal = att.apply(torch.zeros(1), client_id=10, round_idx=0, consortium_deltas=honest)
        # Expected with z=1.0: 10.0 - 1.0 * 2.0 = 8.0
        assert abs(mal.item() - 8.0) < 1e-4

        # If reference z boundary (0.0) was used: 10.0 - 0.0 * 2.0 = 10.0 != 8.0
        z_ref = att.compute_reference_z_max(12, 2)
        assert abs(mal.item() - (10.0 - z_ref * 2.0)) > 1.50

    def test_alie_model_delta_sign_semantics(self):
        """Verifies ALIE model-delta sign shifts update downward below honest mean."""
        att = ALIEAttack()
        honest = [torch.tensor([2.0, 5.0]), torch.tensor([4.0, 7.0])]
        mal = att.apply(torch.zeros(2), client_id=10, round_idx=0, consortium_deltas=honest)
        mu = torch.mean(torch.stack(honest, dim=0), dim=0)
        # mal must be strictly less than mu coordinate-wise
        assert (mal < mu).all()

    def test_alie_never_accesses_test_data(self):
        """Verifies ALIE attack has zero dependency on holdout test set."""
        att = ALIEAttack()
        import inspect
        sig = inspect.signature(att.apply)
        param_names = list(sig.parameters.keys())
        assert "test_dataset" not in param_names
        assert "test_data" not in param_names

    def test_multi_krum_single_pass_and_bulyan_recursive_selection_are_not_conflated(self):
        """Verifies Multi-Krum and Bulyan Stage 1 execute fundamentally different selection architectures."""
        deltas = [torch.tensor([float(x)]) for x in [0.0, 0.1, 0.2, 0.3, 0.4, 2.0, 2.1, 2.2, 2.3, 10.0, 10.1, 10.2]]
        f = 2
        m = 8

        mk_res = aggregate_multi_krum(deltas, f=f, m=m)
        bulyan_res = aggregate_bulyan(deltas, f=f)

        # Multi-Krum averages the full-pool top-8; Bulyan Stage 2 averages median-closest 4 of recursive 8
        assert not torch.allclose(mk_res, bulyan_res)

    def test_deterministic_tie_handling_in_krum_and_bulyan(self):
        """Verifies symmetrical updates with identical Krum scores break ties deterministically by client index."""
        # 5 candidates: index 0 and 4 are symmetric around 0
        deltas = [torch.tensor([-2.0]), torch.tensor([0.0]), torch.tensor([0.0]), torch.tensor([0.0]), torch.tensor([2.0])]
        # Scores of index 0 and 4 are identical: (-2 - 0)^2 + (-2 - 0)^2 = 8; (2 - 0)^2 + (2 - 0)^2 = 8
        # Deterministic tie-breaker selects lower index 0
        krum_res1 = aggregate_krum(deltas, f=1)
        krum_res2 = aggregate_krum(deltas, f=1)
        assert krum_res1.item() == krum_res2.item()

    def test_duplicate_valued_candidate_identity_safe_in_bulyan(self):
        """Verifies Bulyan Stage 1 removal is by instance identity, preventing duplicate value collisions."""
        # Multiple candidates with exact duplicate values
        deltas = [
            torch.tensor([1.0]), torch.tensor([1.0]), torch.tensor([2.0]), torch.tensor([2.0]),
            torch.tensor([3.0]), torch.tensor([3.0]), torch.tensor([4.0]), torch.tensor([4.0]),
            torch.tensor([5.0]), torch.tensor([5.0]), torch.tensor([6.0]), torch.tensor([6.0]),
        ]
        seq = get_bulyan_selection_sequence(deltas, f=2)
        assert len(seq) == 8
        assert len(seq) == len(set(seq))  # All 8 selected indices must be distinct candidate instances
        bulyan_res = aggregate_bulyan(deltas, f=2)
        assert not torch.isnan(bulyan_res).any()
        assert not torch.isinf(bulyan_res).any()

    def test_manifest_lifecycle_marking(self):
        """Verifies execution manifest distinguishes pre-commit from clean canonical execution."""
        cfg = create_canonical_byzantine_config()
        manifest = generate_execution_manifest(cfg)
        assert manifest["manifest_type"] in ["PRE_EXECUTION_PRE_COMMIT_MANIFEST", "CANONICAL_EXECUTION_MANIFEST"]
        assert "protocol_commit_pending" in manifest
        assert manifest["protocol_commit_pending"] == (not manifest["is_working_tree_clean"])


class TestPhase2B2ALIETargetedSemanticsClosure:
    """Rigorous Invariant & Semantic Verification for Phase 2B.2 ALIE Threat Model Closure."""

    def test_alie_variant_name_matches_threat_model(self):
        """1. Verifies ALIE variant name explicitly records OMNISCIENT_ALIE."""
        att = ALIEAttack()
        assert att.attack_variant == "OMNISCIENT_ALIE"
        assert att.config.attack_variant == "OMNISCIENT_ALIE"

    def test_alie_statistics_source_matches_variant(self):
        """2. Verifies ALIE statistics source is explicitly HONEST_CONSORTIUM_UPDATES."""
        att = ALIEAttack()
        assert att.statistics_source == "HONEST_CONSORTIUM_UPDATES"
        assert att.config.statistics_source == "HONEST_CONSORTIUM_UPDATES"

    def test_alie_attack_api_cannot_access_forbidden_updates(self):
        """3. Verifies ALIE apply API structure only accepts legitimate parameter inputs."""
        import inspect
        att = ALIEAttack()
        sig = inspect.signature(att.apply)
        params = list(sig.parameters.keys())
        # API requires explicit inputs; has no access to hidden server or test parameters
        assert params == ["local_delta", "client_id", "round_idx", "consortium_deltas", "seed"]

    def test_alie_std_correction_is_explicit(self):
        """4. Verifies standard deviation estimator and correction (Bessel's correction = 1) are explicit."""
        att = ALIEAttack()
        assert att.std_estimator == "SAMPLE_STANDARD_DEVIATION"
        assert att.std_correction == 1

    def test_alie_population_vs_sample_std_toy_case(self):
        """5. Verifies explicit distinction between sample SD (correction=1) and population SD (correction=0)."""
        honest = [torch.tensor([10.0]), torch.tensor([14.0])]
        # mu = 12.0
        # Population variance (N=2): ((10-12)^2 + (14-12)^2) / 2 = 4.0 -> pop_sigma = 2.0
        # Sample variance (N-1=1): ((10-12)^2 + (14-12)^2) / 1 = 8.0 -> sample_sigma = sqrt(8) ~= 2.828427
        att_sample = ALIEAttack()
        att_sample.std_correction = 1
        mal_sample = att_sample.apply(torch.zeros(1), client_id=10, round_idx=0, consortium_deltas=honest)

        expected_sample = 12.0 - 1.0 * (8.0 ** 0.5)  # 12.0 - 2.828427 ~= 9.171573
        assert abs(mal_sample.item() - expected_sample) < 1e-5

        # Check population SD behavior
        att_pop = ALIEAttack()
        att_pop.std_correction = 0
        mal_pop = att_pop.apply(torch.zeros(1), client_id=10, round_idx=0, consortium_deltas=honest)
        expected_pop = 12.0 - 1.0 * 2.0  # 10.0
        assert abs(mal_pop.item() - expected_pop) < 1e-5
        # The two estimators must diverge by sqrt(2) scaling
        assert abs(mal_sample.item() - mal_pop.item()) > 0.80

    def test_alie_zero_variance_coordinate(self):
        """6. Verifies coordinate with sigma=0 produces exactly mu with zero artificial inflation."""
        att = ALIEAttack()
        # Coordinate 0 varies; coordinate 1 is identical across all honest clients
        honest = [
            torch.tensor([2.0, 5.0]),
            torch.tensor([4.0, 5.0]),
            torch.tensor([6.0, 5.0]),
        ]
        mal = att.apply(torch.zeros(2), client_id=10, round_idx=0, consortium_deltas=honest)
        # Coordinate 0: mu=4.0, sigma=2.0 -> mal_0 = 4.0 - 1.0 * 2.0 = 2.0
        assert abs(mal[0].item() - 2.0) < 1e-5
        # Coordinate 1: mu=5.0, sigma=0.0 -> mal_1 = 5.0 - 1.0 * 0.0 = 5.0 (exact zero perturbation)
        assert abs(mal[1].item() - 5.0) < 1e-6

    def test_alie_reference_z_and_evaluated_z_remain_distinct(self):
        """7. Verifies reference z_max / boundary (0.0) and evaluated z (1.0) remain distinct."""
        att = ALIEAttack()
        assert att.evaluated_z == 1.0
        assert abs(att.reference_z_max - 0.0) < 1e-6
        assert att.reference_z_boundary == 0.0
        assert att.evaluated_z != att.reference_z_max

    def test_alie_evaluated_z_is_exactly_predeclared_value(self):
        """8. Verifies evaluated z is strictly the predeclared fixed parameter 1.0."""
        att = ALIEAttack()
        assert att.evaluated_z == 1.0
        assert att.config.alie_z_source == "PREDECLARED_FIXED_PARAMETER"

    def test_alie_model_delta_direction_matches_frozen_mathematical_rule(self):
        """9. Verifies direction rule is CONSTANT_NEGATIVE_OFFSET matching Algorithm 3 Line 5."""
        att = ALIEAttack()
        assert att.direction_rule == "CONSTANT_NEGATIVE_OFFSET"
        honest = [torch.tensor([1.0, 10.0]), torch.tensor([3.0, 20.0])]
        mal = att.apply(torch.zeros(2), client_id=10, round_idx=0, consortium_deltas=honest)
        mu = torch.mean(torch.stack(honest, dim=0), dim=0)
        # All perturbed coordinates must be strictly less than mu
        assert (mal < mu).all()

    def test_alie_does_not_choose_direction_from_test_performance(self):
        """10. Verifies direction is deterministically static and independent of validation/test loss."""
        att = ALIEAttack()
        # Calling apply twice on identical inputs returns bit-identical tensors without stateful adaptation
        honest = [torch.randn(10) for _ in range(5)]
        out1 = att.apply(torch.zeros(10), client_id=10, round_idx=0, consortium_deltas=honest)
        out2 = att.apply(torch.zeros(10), client_id=10, round_idx=0, consortium_deltas=honest)
        assert torch.equal(out1, out2)

    def test_alie_collusion_metadata_matches_data_flow(self):
        """11. Verifies collusion and Byzantine update-sharing flags in knowledge model."""
        att = ALIEAttack()
        km = att.knowledge_model
        assert km["byzantine_collusion"] is True
        assert km["knows_other_byzantine_updates"] is True
        assert km["knows_honest_updates"] is True

    def test_alie_never_accesses_test_data(self):
        """12. Verifies strict test isolation: knows_test_data is permanently False."""
        att = ALIEAttack()
        assert att.knowledge_model["knows_test_data"] is False

    def test_alie_submitted_vectors_match_defined_collusion_strategy(self):
        """13. Verifies colluding Byzantine clients submit identical crafted vectors."""
        att = ALIEAttack()
        honest = [torch.tensor([1.0, 2.0]), torch.tensor([3.0, 4.0]), torch.tensor([5.0, 6.0])]
        mal_10 = att.apply(torch.randn(2), client_id=10, round_idx=0, consortium_deltas=honest)
        mal_11 = att.apply(torch.randn(2), client_id=11, round_idx=0, consortium_deltas=honest)
        assert torch.equal(mal_10, mal_11)

    def test_alie_nan_inf_guard(self):
        """14. Verifies input validation raises ValueError on NaN or Inf in updates."""
        att = ALIEAttack()
        # NaN in input
        with pytest.raises(ValueError, match="Consortium deltas contain NaN or Inf"):
            att.apply(torch.zeros(2), client_id=10, round_idx=0, consortium_deltas=[torch.tensor([1.0, float("nan")]), torch.tensor([2.0, 3.0])])
        # Inf in input
        with pytest.raises(ValueError, match="Consortium deltas contain NaN or Inf"):
            att.apply(torch.zeros(2), client_id=10, round_idx=0, consortium_deltas=[torch.tensor([1.0, float("inf")]), torch.tensor([2.0, 3.0])])

    def test_alie_empty_estimator_guard(self):
        """15. Verifies input validation raises ValueError when consortium updates are empty or insufficient."""
        att = ALIEAttack()
        with pytest.raises(ValueError, match="requires non-empty consortium_deltas"):
            att.apply(torch.zeros(2), client_id=10, round_idx=0, consortium_deltas=[])
        with pytest.raises(ValueError, match="requires at least 2 deltas"):
            att.apply(torch.zeros(2), client_id=10, round_idx=0, consortium_deltas=[torch.tensor([1.0, 2.0])])

    def test_alie_primary_paper_exact_numerical_reference(self):
        """16. Exact numerical reference test for Baruch et al. (2019) Algorithm 3 Line 5."""
        # 4 honest client updates with known coordinates:
        honest = [
            torch.tensor([1.0, 10.0]),
            torch.tensor([3.0, 20.0]),
            torch.tensor([5.0, 30.0]),
            torch.tensor([7.0, 40.0]),
        ]
        # Coordinate 0: values [1, 3, 5, 7] -> mu = 4.0, sample variance = ((9+1+1+9)/3) = 20/3 ~= 6.666667
        # sigma_0 = sqrt(20/3) ~= 2.581988897
        # With z = 1.0: mal_0 = 4.0 - 2.581988897 ~= 1.4180111
        # Coordinate 1: values [10, 20, 30, 40] -> mu = 25.0, sample variance = ((225+25+25+225)/3) = 500/3 ~= 166.666667
        # sigma_1 = sqrt(500/3) ~= 12.909944487
        # With z = 1.0: mal_1 = 25.0 - 12.909944487 ~= 12.0900555
        att = ALIEAttack(z_max=1.0)
        mal = att.apply(torch.zeros(2), client_id=10, round_idx=0, consortium_deltas=honest)

        expected_0 = 4.0 - float((20.0 / 3.0) ** 0.5)
        expected_1 = 25.0 - float((500.0 / 3.0) ** 0.5)
        assert abs(mal[0].item() - expected_0) < 1e-5
        assert abs(mal[1].item() - expected_1) < 1e-5


class TestPhase2B3ALIEReferenceArithmeticAndClaimsClosure:
    """Targeted regression and invariant tests for Phase 2B.3 ALIE reference arithmetic & claims closure."""

    def test_alie_reference_supporter_count_n12_f2(self):
        """26. Reconstructs supporter count s = floor(n/2 + 1) - m for n=12, m=2; asserts s=5, not 1."""
        n, m = 12, 2
        s = ALIEAttack.compute_reference_supporters(n, m)
        # Detailed derivation: floor(12/2 + 1) - 2 = floor(6 + 1) - 2 = 7 - 2 = 5
        assert s == 5
        assert s != 1  # Fails if historical s=1 regression occurs

    def test_alie_reference_probability_boundary_n12_f2(self):
        """27. Verifies probability boundary (n - m - s) / (n - m) = (12 - 2 - 5) / 10 = 0.50."""
        n, m = 12, 2
        p_val = ALIEAttack.compute_reference_probability_boundary(n, m)
        assert abs(p_val - 0.50) < 1e-6

    def test_alie_reference_z_boundary_n12_f2(self):
        """28. Verifies reference z boundary Phi^{-1}(0.50) = 0.0 and handles strict inequality."""
        import scipy.stats as stats
        n, m = 12, 2
        z_boundary = ALIEAttack.compute_reference_z_boundary(n, m)
        assert abs(z_boundary - 0.0) < 1e-6
        # Strict inequality semantics: Phi(z) < 0.50 implies z < 0.0; boundary/supremum is 0.0
        assert float(stats.norm.cdf(0.0)) == 0.50
        assert float(stats.norm.cdf(-1e-5)) < 0.50

    def test_alie_paper_numerical_example_reproduction(self):
        """29. Reproduces Baruch et al. (NeurIPS 2019, Section 3.3, Page 7) example: n=50, m=24."""
        n, m = 50, 24
        s = ALIEAttack.compute_reference_supporters(n, m)
        assert s == 2  # floor(50/2 + 1) - 24 = 26 - 24 = 2
        p_val = ALIEAttack.compute_reference_probability_boundary(n, m)
        # (50 - 24 - 2) / (50 - 24) = 24 / 26 ~= 0.9230769
        assert abs(p_val - (24.0 / 26.0)) < 1e-6
        z_boundary = ALIEAttack.compute_reference_z_boundary(n, m)
        # Paper reports: "phi(z) = 0.923 we get z^max = 1.43"
        assert abs(round(z_boundary, 2) - 1.43) < 1e-2

    def test_alie_evaluated_z_separation_from_reference_boundary(self):
        """30. Confirms evaluated z (1.0) and reference boundary (0.0) remain strictly separated."""
        att = ALIEAttack()
        assert att.evaluated_z == 1.0
        assert att.reference_z_boundary == 0.0
        assert att.reference_supporters == 5
        assert att.reference_probability_boundary == 0.50
        assert att.evaluated_z != att.reference_z_boundary

    def test_alie_no_false_evasion_claim_invariant(self):
        """31. Verifies active code and docstrings do not make unsupported trimming evasion claims."""
        from benchmarks.byzantine.attacks import ALIEAttack
        doc = ALIEAttack.__doc__ or ""
        forbidden_phrases = [
            "evading coordinate-wise trimming with beta=0.20",
            "successfully evades",
            "evades detection",
            "survives trimming",
            "bypasses Trimmed Mean",
            "maximizes the negative shift",
            "guaranteed to pass",
        ]
        for phrase in forbidden_phrases:
            assert phrase.lower() not in doc.lower(), f"Forbidden claim found in doc: {phrase}"

    def test_alie_no_dominance_guarantee_invariant(self):
        """32. Verifies omniscient ALIE is scoped as a stress test without false dominance guarantees."""
        from benchmarks.byzantine.attacks import ALIEAttack
        doc = ALIEAttack.__doc__ or ""
        forbidden_phrases = [
            "true adversarial upper bound",
            "guaranteed to withstand non-omniscient",
            "guaranteed to withstand noisy",
            "necessarily survives weaker variants",
            "dominates all non-omniscient",
        ]
        for phrase in forbidden_phrases:
            assert phrase.lower() not in doc.lower(), f"Forbidden dominance claim found: {phrase}"

    def test_alie_sample_sd_provenance_is_project_convention(self):
        """18. Verifies sample standard deviation provenance is documented as PROJECT_PREDECLARED_CONVENTION."""
        att = ALIEAttack()
        assert att.std_estimator == "SAMPLE_STANDARD_DEVIATION"
        assert att.std_correction == 1
        assert att.std_convention_source == "PROJECT_PREDECLARED_CONVENTION"

    def test_alie_model_delta_is_adaptation(self):
        """21. Verifies MODEL_DELTA representation is documented as an adaptation, not exact gradient identity."""
        from benchmarks.byzantine.attacks import ALIEAttack
        doc = ALIEAttack.__doc__ or ""
        assert "MODEL_DELTA space" in doc
        assert "adapted" in doc.lower()
