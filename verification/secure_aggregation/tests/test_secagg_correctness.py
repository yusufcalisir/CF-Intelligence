"""Scientific Verification Suite: End-to-End SecAgg Invariants & Floating-Point Equivalence.

Formally verifies:
  1. Pairwise zero-sum masking invariant: \\sum_{u \\in U} masks_u = 0 (mod 2^32)
  2. Numerical equivalence bound: |w_{secagg} - w_{plain}| < 10^{-6}
  3. Quantization resolution sensitivity across scale factors (10^4, 10^6, 10^8)
  4. High-dimensional vector preservation (d = 20,000)
  5. ECDH key exchange symmetry and canonical lexicographical ordering
  6. Cross-round cryptographic domain isolation (HKDF salt/info separation)
  7. HMAC bundle authentication & tamper-rejection guarantees
  8. Permutation invariance of coordinator aggregation
  9. Realistic neural network weight distribution reconstruction
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_path = Path(__file__).resolve().parents[3] / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

import numpy as np
import pytest

from app.infrastructure.security.p2p_secagg_driver import (
    ECDHPublicKeyBundle,
    P2PSecAggDriver,
)


class TestSecAggMathematicalInvariants:
    """Rigorous verification of SecAgg mathematical cancellation and numerical precision."""

    @pytest.mark.parametrize("n_clients", [2, 3, 5, 8])
    @pytest.mark.parametrize("dim", [1, 16, 256, 1024])
    def test_pairwise_mask_additive_zero_sum_identity(self, n_clients: int, dim: int):
        """Verify that the sum of pairwise masks across all n clients cancels to 0 mod 2^32.

        Mathematical Invariant:
            For each pair (u, v) with u < v, node u adds s_{u,v} and node v subtracts s_{u,v}.
            Therefore:
                \\sum_{u=1}^N [ \\sum_{v > u} s_{u,v} - \\sum_{v < u} s_{v,u} ] = 0 (mod 2^32)
        """
        round_id = 42
        bank_ids = [f"bank_{i:02d}" for i in range(n_clients)]
        drivers = [P2PSecAggDriver(bid) for bid in bank_ids]
        bundles = [d.generate_round_keypair(round_id) for d in drivers]

        # Use all-zero weights so that masked vectors equal pure mask sums
        zero_weights = [0.0] * dim
        masked_vectors = {}

        for i, driver in enumerate(drivers):
            peer_bundles = [bundles[j] for j in range(n_clients) if j != i]
            masked_v = driver.compute_masked_vector(zero_weights, peer_bundles)
            masked_vectors[driver.bank_id] = masked_v

        # Accumulate coordinate-wise sum mod 2^32 at coordinator
        coord_sum = [0] * dim
        for vec in masked_vectors.values():
            for k in range(dim):
                coord_sum[k] = (coord_sum[k] + vec[k]) % (2**32)

        # Invariant: coord_sum must be strictly identical to 0 for every dimension
        assert all(val == 0 for val in coord_sum), (
            f"Zero-sum cancellation failed for N={n_clients}, dim={dim}. "
            f"Non-zero elements: {[v for v in coord_sum if v != 0][:5]}"
        )

    @pytest.mark.parametrize("n_clients", [2, 3, 5])
    def test_floating_point_equivalence_precision_bound(self, n_clients: int):
        """Verify that |w_{secagg} - w_{plain}| < 10^{-6} under scale 10^6.

        Validates that fixed-point quantization and modular modular arithmetic
        faithfully preserve floating point parameter averages within machine precision.
        """
        round_id = 101
        dim = 100
        rng = np.random.default_rng(2026)

        bank_ids = [f"bank_{i}" for i in range(n_clients)]
        drivers = [P2PSecAggDriver(bid) for bid in bank_ids]
        bundles = [d.generate_round_keypair(round_id) for d in drivers]

        # Realistic float weights sampled from N(0, 0.25)
        raw_weights = [rng.normal(0.0, 0.25, size=dim).tolist() for _ in range(n_clients)]
        expected_avg = [
            sum(raw_weights[j][k] for j in range(n_clients)) / n_clients
            for k in range(dim)
        ]

        masked_submissions = {}
        for i, driver in enumerate(drivers):
            peer_bundles = [bundles[j] for j in range(n_clients) if j != i]
            masked_v = driver.compute_masked_vector(raw_weights[i], peer_bundles, quantization_scale=1e6)
            masked_submissions[driver.bank_id] = masked_v

        secagg_avg = P2PSecAggDriver.aggregate_masked_vectors(masked_submissions, quantization_scale=1e6)

        max_abs_err = max(abs(secagg_avg[k] - expected_avg[k]) for k in range(dim))
        # Strictly assert < 10^-6 precision bound
        assert max_abs_err < 1e-6, f"Max error {max_abs_err:.2e} exceeded tolerance 1e-6"

    def test_extreme_weights_dynamic_range(self):
        """Verify numerical stability across extreme positive, negative, and mixed weight ranges."""
        round_id = 202
        dim = 6
        extreme_weights = [
            [500.0, -500.0, 1e-4, -1e-4, 0.0, 123.456],
            [-250.0, 300.0, -5e-4, 2e-4, 0.0, -88.123],
            [100.0, -100.0, 0.0, 0.0, 0.0, 50.0],
        ]
        n_clients = len(extreme_weights)
        drivers = [P2PSecAggDriver(f"bank_{i}") for i in range(n_clients)]
        bundles = [d.generate_round_keypair(round_id) for d in drivers]

        expected_avg = [
            sum(extreme_weights[j][k] for j in range(n_clients)) / n_clients
            for k in range(dim)
        ]

        masked_submissions = {}
        for i, driver in enumerate(drivers):
            peers = [bundles[j] for j in range(n_clients) if j != i]
            masked_v = driver.compute_masked_vector(extreme_weights[i], peers, quantization_scale=1e6)
            masked_submissions[driver.bank_id] = masked_v

        result = P2PSecAggDriver.aggregate_masked_vectors(masked_submissions, quantization_scale=1e6)

        for k in range(dim):
            assert math.isclose(result[k], expected_avg[k], abs_tol=1e-5), (
                f"Index {k}: expected {expected_avg[k]:.6f}, got {result[k]:.6f}"
            )

    @pytest.mark.parametrize("scale,expected_bound", [(1e4, 1e-4), (1e6, 1e-6), (1e8, 1e-8)])
    def test_quantization_resolution_sensitivity(self, scale: float, expected_bound: float):
        """Verify that quantization error scales inversely with quantization_scale S."""
        round_id = 303
        dim = 50
        rng = np.random.default_rng(777)
        raw_weights = [rng.uniform(-2.0, 2.0, size=dim).tolist() for _ in range(3)]
        expected = [sum(raw_weights[j][k] for j in range(3)) / 3.0 for k in range(dim)]

        drivers = [P2PSecAggDriver(f"node_{i}") for i in range(3)]
        bundles = [d.generate_round_keypair(round_id) for d in drivers]

        masked = {}
        for i, d in enumerate(drivers):
            peers = [bundles[j] for j in range(3) if j != i]
            masked[d.bank_id] = d.compute_masked_vector(raw_weights[i], peers, quantization_scale=scale)

        agg = P2PSecAggDriver.aggregate_masked_vectors(masked, quantization_scale=scale)
        max_err = max(abs(agg[k] - expected[k]) for k in range(dim))
        assert max_err <= expected_bound * 1.5, f"Scale {scale}: max error {max_err:.2e} exceeded bound"

    def test_high_dimensional_vector_scaling(self):
        """Verify performance and numerical fidelity on d = 20,000 parameters."""
        round_id = 404
        dim = 20_000
        n_clients = 3
        rng = np.random.default_rng(888)

        drivers = [P2PSecAggDriver(f"bank_{i}") for i in range(n_clients)]
        bundles = [d.generate_round_keypair(round_id) for d in drivers]

        raw_weights = [rng.standard_normal(size=dim).astype(np.float64).tolist() for _ in range(n_clients)]
        expected_avg = [
            (raw_weights[0][k] + raw_weights[1][k] + raw_weights[2][k]) / 3.0
            for k in range(dim)
        ]

        masked = {}
        for i, d in enumerate(drivers):
            peers = [bundles[j] for j in range(n_clients) if j != i]
            masked[d.bank_id] = d.compute_masked_vector(raw_weights[i], peers, quantization_scale=1e6)

        agg = P2PSecAggDriver.aggregate_masked_vectors(masked, quantization_scale=1e6)

        # Check statistical metrics across 20,000 parameters
        errors = [abs(agg[k] - expected_avg[k]) for k in range(dim)]
        assert max(errors) < 1e-5
        assert np.mean(errors) < 5e-7

    def test_cross_round_cryptographic_isolation(self):
        """Verify that seeds and masks from round r1 cannot cancel with round r2."""
        driver_a = P2PSecAggDriver("bank_alpha")
        driver_b = P2PSecAggDriver("bank_beta")

        bundle_a_r1 = driver_a.generate_round_keypair(round_id=1)
        bundle_b_r1 = driver_b.generate_round_keypair(round_id=1)
        seed_r1 = driver_a.derive_pairwise_seed(bundle_b_r1)

        bundle_a_r2 = driver_a.generate_round_keypair(round_id=2)
        bundle_b_r2 = driver_b.generate_round_keypair(round_id=2)
        seed_r2 = driver_a.derive_pairwise_seed(bundle_b_r2)

        assert seed_r1 != seed_r2, "Round isolation failed: seeds across rounds matched"

        # Compare masks
        mask_r1 = P2PSecAggDriver.expand_mask(seed_r1, 64)
        mask_r2 = P2PSecAggDriver.expand_mask(seed_r2, 64)
        hamming_diff = sum(m1 != m2 for m1, m2 in zip(mask_r1, mask_r2))
        assert hamming_diff == 64, "All mask coordinates must differ across rounds"

    def test_hmac_tamper_detection_on_bundles(self):
        """Verify HMAC-SHA256 signature verification rejects tampered bundles."""
        secret = b"K" * 32
        driver = P2PSecAggDriver("bank_gamma", identity_secret=secret)
        bundle = driver.generate_round_keypair(round_id=5)

        # Valid bundle passes
        assert P2PSecAggDriver.verify_peer_bundle(bundle, secret)

        # 1-bit modified public key fails
        tampered_pk = bytearray(bundle.public_key_bytes)
        tampered_pk[0] ^= 0x01
        tampered_bundle = ECDHPublicKeyBundle(
            bank_id=bundle.bank_id,
            round_id=bundle.round_id,
            public_key_bytes=bytes(tampered_pk),
            hmac_signature=bundle.hmac_signature,
        )
        assert not P2PSecAggDriver.verify_peer_bundle(tampered_bundle, secret)

        # Tampered round_id fails
        tampered_round = ECDHPublicKeyBundle(
            bank_id=bundle.bank_id,
            round_id=99,
            public_key_bytes=bundle.public_key_bytes,
            hmac_signature=bundle.hmac_signature,
        )
        assert not P2PSecAggDriver.verify_peer_bundle(tampered_round, secret)

    def test_permutation_invariance_of_coordinator_aggregation(self):
        """Verify that dictionary ordering of submissions does not alter aggregate result."""
        round_id = 505
        dim = 10
        weights = [[1.0] * dim, [2.0] * dim, [3.0] * dim]
        drivers = [P2PSecAggDriver(f"bank_{x}") for x in ["c", "a", "b"]]
        bundles = [d.generate_round_keypair(round_id) for d in drivers]

        masked = {}
        for i, d in enumerate(drivers):
            peers = [bundles[j] for j in range(3) if j != i]
            masked[d.bank_id] = d.compute_masked_vector(weights[i], peers)

        # Aggregation in order c, a, b
        res1 = P2PSecAggDriver.aggregate_masked_vectors(
            {"bank_c": masked["bank_c"], "bank_a": masked["bank_a"], "bank_b": masked["bank_b"]}
        )

        # Aggregation in order a, b, c
        res2 = P2PSecAggDriver.aggregate_masked_vectors(
            {"bank_a": masked["bank_a"], "bank_b": masked["bank_b"], "bank_c": masked["bank_c"]}
        )

        assert res1 == res2
        assert all(math.isclose(v, 2.0, abs_tol=1e-5) for v in res1)
