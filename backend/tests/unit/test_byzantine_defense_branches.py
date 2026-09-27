"""Targeted Branch Coverage Tests for Spectral Byzantine Defense, Robust FL Aggregators & Attack Injector."""

from unittest.mock import MagicMock

import numpy as np
import pytest

from app.application.services.fl_engine import FederatedLearningEngine
from app.domain.attack_injector import AdversarialAttackInjector, AttackType
from app.domain.byzantine_defense import (
    ByzantineBreakdownAnalyzer,
    SpectralByzantineDefense,
    aggregate_bulyan,
    aggregate_coordinate_median,
    aggregate_fedavg,
    aggregate_krum,
    aggregate_trimmed_mean,
)
from app.domain.enums import AggregationMethod
from app.domain.value_objects import ModelWeights


class TestByzantineDefenseBranches:
    """Test every branch and threshold condition in Byzantine defense algorithms."""

    def test_spectral_byzantine_defense_edge_branches(self):
        defense = SpectralByzantineDefense(contamination_ratio=0.33)

        # 1. Branch: len(updates) <= 2 -> immediate bypass
        short_updates = {
            "bank_a": np.array([0.1, 0.2]),
            "bank_b": np.array([0.15, 0.25]),
        }
        sanitized, anomalies = defense.filter_anomalous_updates(short_updates)
        assert sanitized == short_updates
        assert anomalies == []

        # 2. Branch: Zero MAD (all nodes send identical gradient vectors)
        identical_updates = {
            "bank_a": np.array([1.0, 1.0, 1.0]),
            "bank_b": np.array([1.0, 1.0, 1.0]),
            "bank_c": np.array([1.0, 1.0, 1.0]),
        }
        sanitized, anomalies = defense.filter_anomalous_updates(identical_updates)
        assert len(sanitized) == 3
        assert anomalies == []

        # 3. Branch: Extreme Byzantine Gradient Poisoning (norm outlier)
        poisoned_updates = {
            "bank_a": np.array([0.5, 0.5, 0.5]),
            "bank_b": np.array([0.52, 0.48, 0.51]),
            "bank_c": np.array([0.49, 0.53, 0.50]),
            "bank_malicious": np.array([500.0, -400.0, 350.0]),
        }
        sanitized, anomalies = defense.filter_anomalous_updates(poisoned_updates)
        assert "bank_malicious" in anomalies
        assert "bank_malicious" not in sanitized
        assert len(sanitized) == 3

    def test_fl_engine_aggregation_branches(self):
        mock_settings = MagicMock()
        mock_model_service = MagicMock()
        mock_privacy_service = MagicMock()
        engine = FederatedLearningEngine(mock_settings, mock_model_service, mock_privacy_service)

        shapes = [(3,)]
        w1 = ModelWeights(layer_shapes=shapes, flat_weights=[1.0, 2.0, 3.0])
        w2 = ModelWeights(layer_shapes=shapes, flat_weights=[1.1, 1.9, 3.1])
        w3 = ModelWeights(layer_shapes=shapes, flat_weights=[0.9, 2.1, 2.9])
        w_poison = ModelWeights(layer_shapes=shapes, flat_weights=[100.0, 200.0, 300.0])

        weights = [w1, w2, w3, w_poison]
        samples = [100, 100, 100, 100]

        # 1. KRUM branch
        krum_res = engine.aggregate_parameters(weights, samples, method=AggregationMethod.KRUM)
        assert len(krum_res.flat_weights) == 3
        assert krum_res.flat_weights[0] < 10.0

        # 2. COORDINATE_WISE_MEDIAN branch
        median_res = engine.aggregate_parameters(weights, samples, method=AggregationMethod.COORDINATE_WISE_MEDIAN)
        assert len(median_res.flat_weights) == 3
        assert 0.9 <= median_res.flat_weights[0] <= 1.5

        # 3. TRIMMED_MEAN branch (n <= 2*f fallback branch with 2 clients)
        two_weights = [w1, w2]
        two_samples = [100, 100]
        trimmed_small = engine.aggregate_parameters(two_weights, two_samples, method=AggregationMethod.TRIMMED_MEAN)
        assert len(trimmed_small.flat_weights) == 3

        # TRIMMED_MEAN with 4 clients
        trimmed_res = engine.aggregate_parameters(weights, samples, method=AggregationMethod.TRIMMED_MEAN)
        assert len(trimmed_res.flat_weights) == 3
        assert trimmed_res.flat_weights[0] < 10.0

        # 4. BULYAN branch
        bulyan_res = engine.aggregate_parameters(weights, samples, method=AggregationMethod.BULYAN)
        assert len(bulyan_res.flat_weights) == 3
        assert bulyan_res.flat_weights[0] < 10.0

    def test_adversarial_attack_injector_modalities(self):
        """Verify all attack modalities in AdversarialAttackInjector."""
        rng = np.random.default_rng(42)
        honest = np.array([1.0, 2.0, -1.5, 0.5])

        # 1. Sign-Flip Inversion
        sf = AdversarialAttackInjector.inject_sign_flip(honest, scale=-3.0)
        np.testing.assert_allclose(sf, honest * -3.0)

        # 2. Scaled Update Outlier
        su = AdversarialAttackInjector.inject_scaled_update(honest, scale=100.0)
        np.testing.assert_allclose(su, honest * 100.0)

        # 3. Gaussian Noise Injection
        gn = AdversarialAttackInjector.inject_gaussian_noise(honest, std=5.0, rng=rng)
        assert gn.shape == honest.shape
        assert not np.allclose(gn, honest)

        # Tuple shape
        gn_shape = AdversarialAttackInjector.inject_gaussian_noise((10,), rng=rng)
        assert gn_shape.shape == (10,)

        # 4. Label Poisoning
        labels = np.array([0, 0, 1, 1, 0, 1, 0, 1])
        flipped_all = AdversarialAttackInjector.inject_label_poisoning(labels, poison_ratio=1.0)
        np.testing.assert_array_equal(flipped_all, 1 - labels)

        zero_flip = AdversarialAttackInjector.inject_label_poisoning(labels, poison_ratio=0.0)
        np.testing.assert_array_equal(zero_flip, labels)

        partial_flip = AdversarialAttackInjector.inject_label_poisoning(labels, poison_ratio=0.5, rng=rng)
        assert np.sum(partial_flip != labels) == 4

        # 5. Crafting poisoned gradients across all AttackType variants
        for att in [
            AttackType.SIGN_FLIP,
            AttackType.SCALED_UPDATE,
            AttackType.GAUSSIAN_NOISE,
            AttackType.LABEL_POISONING,
            AttackType.BACKDOOR_TRIGGER,
        ]:
            poisoned = AdversarialAttackInjector.craft_poisoned_gradient(honest, att, rng=rng)
            assert poisoned.shape == honest.shape

        # 6. Consortium round generation with f=0 and f=2
        honest_list = [honest.copy() for _ in range(5)]
        updates_clean, mal_clean = AdversarialAttackInjector.generate_consortium_round_updates(
            honest_list, n_byzantine=0, attack_type=AttackType.SIGN_FLIP
        )
        assert len(updates_clean) == 5
        assert len(mal_clean) == 0

        updates_poisoned, mal_idx = AdversarialAttackInjector.generate_consortium_round_updates(
            honest_list, n_byzantine=2, attack_type=AttackType.SIGN_FLIP, rng=rng
        )
        assert len(updates_poisoned) == 7
        assert mal_idx == [5, 6]

        with pytest.raises(ValueError):
            AdversarialAttackInjector.generate_consortium_round_updates(
                honest_list, n_byzantine=-1, attack_type=AttackType.SIGN_FLIP
            )

    def test_pure_byzantine_aggregators(self):
        """Verify mathematical invariants of standalone Byzantine aggregators."""
        u1 = np.array([1.0, 2.0, 3.0])
        u2 = np.array([1.1, 1.9, 3.1])
        u3 = np.array([0.9, 2.1, 2.9])
        u4 = np.array([1.05, 2.05, 2.95])
        u_poison = np.array([1000.0, 2000.0, 3000.0])

        honest_updates = [u1, u2, u3, u4]
        mixed_updates = [u1, u2, u3, u4, u_poison]

        # 1. FedAvg
        fedavg_clean = aggregate_fedavg(honest_updates)
        np.testing.assert_allclose(fedavg_clean, [1.0125, 2.0125, 2.9875])

        fedavg_poison = aggregate_fedavg(mixed_updates)
        assert fedavg_poison[0] > 100.0  # FedAvg broken by single outlier

        # 2. Coordinate-wise Median
        med_res = aggregate_coordinate_median(mixed_updates)
        assert 0.9 <= med_res[0] <= 1.2
        assert 1.9 <= med_res[1] <= 2.2
        assert 2.9 <= med_res[2] <= 3.2

        # 3. Trimmed Mean
        tm_res = aggregate_trimmed_mean(mixed_updates, trim_ratio=0.2)
        assert tm_res[0] < 5.0

        # Trimmed Mean edge case (k=0 fallback)
        tm_small = aggregate_trimmed_mean([u1, u2], trim_ratio=0.2)
        assert tm_small.shape == (3,)

        # 4. Krum
        krum_res = aggregate_krum(mixed_updates, f_byzantine=1)
        assert krum_res[0] < 5.0
        assert not np.allclose(krum_res, u_poison)

        # Krum fallback when n <= 2f + 2
        krum_fallback = aggregate_krum([u1, u2, u_poison], f_byzantine=1)
        assert krum_fallback[0] < 5.0  # falls back to median

        # 5. Bulyan
        bulyan_res = aggregate_bulyan(mixed_updates, f_byzantine=1)
        assert bulyan_res[0] < 5.0

        # Bulyan fallback when n < 4f + 3
        # For n=7, f=1: 4(1)+3 = 7, satisfies condition
        u5 = np.array([1.02, 1.98, 3.02])
        u6 = np.array([0.98, 2.02, 2.98])
        u7 = np.array([1.01, 2.01, 3.01])
        bulyan_7 = aggregate_bulyan([u1, u2, u3, u4, u5, u6, u7], f_byzantine=1)
        assert 0.95 <= bulyan_7[0] <= 1.05

        # Empty updates validation
        with pytest.raises(ValueError):
            aggregate_fedavg([])
        with pytest.raises(ValueError):
            aggregate_coordinate_median([])
        with pytest.raises(ValueError):
            aggregate_trimmed_mean([])
        with pytest.raises(ValueError):
            aggregate_krum([])
        with pytest.raises(ValueError):
            aggregate_bulyan([])

    def test_byzantine_breakdown_point_analyzer(self):
        """Verify theoretical breakdown bounds across consortium configurations."""
        analyzer = ByzantineBreakdownAnalyzer()

        # N = 10 clients
        assert analyzer.get_max_tolerable_byzantine(10, "fedavg") == 0
        assert analyzer.get_max_tolerable_byzantine(10, "bulyan") == 1      # (10-3)//4 = 1
        assert analyzer.get_max_tolerable_byzantine(10, "krum") == 3        # (10-3)//2 = 3
        assert analyzer.get_max_tolerable_byzantine(10, "trimmed_mean") == 2 # int(0.2*10) = 2
        assert analyzer.get_max_tolerable_byzantine(10, "median") == 4      # (10-1)//2 = 4

        # N = 20 clients
        assert analyzer.get_max_tolerable_byzantine(20, "bulyan") == 4      # (20-3)//4 = 4
        assert analyzer.get_max_tolerable_byzantine(20, "krum") == 8        # (20-3)//2 = 8

        # Comprehensive analysis report
        report = analyzer.analyze_consortium(n_clients=10, f_byzantine=2)
        assert report["n_clients"] == 10
        assert report["f_byzantine"] == 2
        assert report["byzantine_fraction"] == 0.2
        assert report["defenses"]["fedavg"]["status"] == "BREAKDOWN"
        assert report["defenses"]["krum"]["status"] == "RESILIENT"
        assert report["defenses"]["trimmed_mean"]["status"] == "RESILIENT"
        assert report["defenses"]["bulyan"]["status"] == "BREAKDOWN"  # for N=10, max f=1

    def test_byzantine_resilience_under_poisoning_attacks(self):
        """Verify robust aggregators neutralize adversarial poisoning vectors at 20% contamination."""
        rng = np.random.default_rng(123)
        honest_dir = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float64)

        # 8 honest clients with small variance
        honest_updates = [honest_dir + rng.normal(0.0, 0.05, size=4) for _ in range(8)]

        # 1. Extreme Scaled Update Outlier Attack (f=2 / N=10)
        mixed_scaled, _ = AdversarialAttackInjector.generate_consortium_round_updates(
            honest_updates=honest_updates,
            n_byzantine=2,
            attack_type=AttackType.SCALED_UPDATE,
            intensity=100.0,
            rng=rng,
        )

        fedavg_scaled = aggregate_fedavg(mixed_scaled)
        krum_scaled = aggregate_krum(mixed_scaled, f_byzantine=2)
        med_scaled = aggregate_coordinate_median(mixed_scaled)
        tm_scaled = aggregate_trimmed_mean(mixed_scaled, trim_ratio=0.2)
        bulyan_scaled = aggregate_bulyan(mixed_scaled, f_byzantine=2)

        # FedAvg is corrupted
        assert np.linalg.norm(fedavg_scaled) > 10.0

        # All robust aggregators isolate the 100x scaled outliers
        for agg_res in [krum_scaled, med_scaled, tm_scaled, bulyan_scaled]:
            assert np.linalg.norm(agg_res - honest_dir) < 0.5
            cos_sim = float(np.dot(agg_res, honest_dir) / (np.linalg.norm(agg_res) * np.linalg.norm(honest_dir)))
            assert cos_sim > 0.95

        # 2. Sign-Flip Inversion Attack (f=2 / N=10)
        mixed_sf, _ = AdversarialAttackInjector.generate_consortium_round_updates(
            honest_updates=honest_updates,
            n_byzantine=2,
            attack_type=AttackType.SIGN_FLIP,
            intensity=1.0,
            rng=rng,
        )

        krum_sf = aggregate_krum(mixed_sf, f_byzantine=2)
        med_sf = aggregate_coordinate_median(mixed_sf)
        tm_sf = aggregate_trimmed_mean(mixed_sf, trim_ratio=0.2)
        bulyan_sf = aggregate_bulyan(mixed_sf, f_byzantine=2)

        for agg_res in [krum_sf, med_sf, tm_sf, bulyan_sf]:
            cos_sim = float(np.dot(agg_res, honest_dir) / (np.linalg.norm(agg_res) * np.linalg.norm(honest_dir)))
            assert cos_sim > 0.85
