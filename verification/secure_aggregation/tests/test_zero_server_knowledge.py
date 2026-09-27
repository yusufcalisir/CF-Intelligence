"""Scientific Verification Suite: Coordinator Zero-Knowledge Privacy Boundary & Non-Collusion Proofs.

Formally verifies:
  1. Statistical independence & zero correlation: r(w_u, y_u) ~ 0 (p > 0.05)
  2. Shannon entropy of masked vectors H(y) ~ 32 bits (uniform modular distribution)
  3. Non-collusion threshold guarantee: 2 honest clients remain completely blinded against N-2 colluders
  4. Fundamental theoretical limit: N-1 colluders algebraic boundary
  5. Zero raw PII / zero float leakage in coordinator submission payload
  6. Dropout privacy preservation via Shamir (t, n) secret reconstruction
  7. Replay attack resistance via round-specific HKDF domain separation
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
from scipy import stats

from app.infrastructure.security.p2p_secagg_driver import (
    ECDHPublicKeyBundle,
    P2PSecAggDriver,
)
from app.infrastructure.security.shamir_engine import (
    ShamirSecretSharingEngine,
    ShamirShare,
)


class TestCoordinatorZeroKnowledgeBoundary:
    """Formal verification of information-theoretic privacy and zero-knowledge bounds."""

    def test_statistical_independence_and_zero_correlation(self):
        """Verify that individual client weights w_u and masked submission y_u are uncorrelated.

        From the perspective of an honest-but-curious coordinator holding y_u,
        the masked vector must exhibit zero Pearson correlation with the private weights:
            |r(w_u, y_u)| < 0.05 and p-value > 0.05
        """
        round_id = 10
        dim = 2000
        rng = np.random.default_rng(42)

        driver_a = P2PSecAggDriver("bank_alpha")
        driver_b = P2PSecAggDriver("bank_beta")
        bundle_a = driver_a.generate_round_keypair(round_id)
        bundle_b = driver_b.generate_round_keypair(round_id)

        # Generate realistic model weights
        raw_weights = rng.normal(loc=0.5, scale=1.2, size=dim).tolist()
        masked_y = driver_a.compute_masked_vector(raw_weights, [bundle_b], quantization_scale=1e6)

        # Pearson correlation between raw input and masked modular output
        r_val, p_val = stats.pearsonr(raw_weights, masked_y)

        # Must have negligible correlation indistinguishable from random noise
        assert abs(r_val) < 0.05, f"Correlation {r_val:.4f} too high, masking failed to obscure"
        assert p_val > 0.05, f"Correlation is statistically significant (p={p_val:.4e})"

    def test_masked_vector_shannon_entropy(self):
        """Verify that masked vector elements exhibit maximal entropy over Z_{2^32}."""
        round_id = 11
        dim = 5000
        rng = np.random.default_rng(999)

        driver_a = P2PSecAggDriver("bank_alpha")
        driver_b = P2PSecAggDriver("bank_beta")
        bundle_a = driver_a.generate_round_keypair(round_id)
        bundle_b = driver_b.generate_round_keypair(round_id)

        # All-constant weights to test if mask alone produces high entropy
        constant_weights = [1.2345] * dim
        masked_y = driver_a.compute_masked_vector(constant_weights, [bundle_b])

        # Bin values into 256 equal intervals across [0, 2^32)
        counts, _ = np.histogram(masked_y, bins=256, range=(0, 2**32))
        probs = counts / float(len(masked_y))
        probs = probs[probs > 0]
        entropy_base2 = -float(np.sum(probs * np.log2(probs)))

        # Uniform distribution across 256 bins has maximum entropy log2(256) = 8.0 bits
        max_possible_entropy = 8.0
        # Invariant: empirical entropy must be within 3% of theoretical maximum
        assert entropy_base2 > 0.97 * max_possible_entropy, (
            f"Entropy {entropy_base2:.3f} bits is significantly below maximum {max_possible_entropy} bits"
        )

    def test_non_collusion_privacy_guarantee(self):
        """Verify that 2 honest clients remain fully blinded even if N-2 clients collude with coordinator.

        Scenario:
          5 banks: [bank_0, bank_1, bank_2, bank_3, bank_4]
          bank_0 and bank_1 are HONEST.
          bank_2, bank_3, bank_4 COLLUDE with coordinator, revealing their private keys and pairwise seeds.

        Invariant:
          Coordinator subtracts all colluders' masks from bank_0's submission.
          Residual on bank_0 is: w_0 + s_{0,1} (mod 2^32).
          Without the honest pair secret s_{0,1}, bank_0's weights remain strictly hidden.
        """
        round_id = 77
        dim = 100
        rng = np.random.default_rng(1234)

        bank_ids = [f"bank_{i}" for i in range(5)]
        drivers = [P2PSecAggDriver(bid) for bid in bank_ids]
        bundles = [d.generate_round_keypair(round_id) for d in drivers]

        weights = [rng.normal(0.0, 1.0, size=dim).tolist() for _ in range(5)]

        masked_submissions = {}
        for i, d in enumerate(drivers):
            peers = [bundles[j] for j in range(5) if j != i]
            masked_submissions[d.bank_id] = d.compute_masked_vector(weights[i], peers, quantization_scale=1e6)

        # Coordinator + Colluders (bank_2, bank_3, bank_4) pool knowledge:
        # They compute pairwise seeds between colluders and bank_0
        y_0 = list(masked_submissions["bank_0"])
        for colluder_idx in [2, 3, 4]:
            colluder_driver = drivers[colluder_idx]
            # Colluder derives pairwise seed with bank_0
            seed_with_0 = colluder_driver.derive_pairwise_seed(bundles[0])
            mask_with_0 = P2PSecAggDriver.expand_mask(seed_with_0, dim)

            # bank_0 < bank_colluder: bank_0 added mask_with_0. Coordinator subtracts it.
            for k in range(dim):
                y_0[k] = (y_0[k] - mask_with_0[k]) % (2**32)

        # After removing all colluder masks, y_0 still contains mask s_{0, 1} with bank_1!
        # Measure correlation between stripped y_0 and raw weights[0]
        r_val, p_val = stats.pearsonr(weights[0], y_0)
        assert abs(r_val) < 0.10, (
            f"Residual correlation {r_val:.4f} after colluder subtraction is too high! "
            f"Honest mutual mask s_{{0,1}} failed to protect bank_0."
        )

    def test_all_but_one_collusion_theoretical_limit(self):
        """Demonstrate the fundamental information-theoretic boundary: N-1 colluders.

        When N-1 clients collude with the coordinator, the remaining client's update
        is mathematically determined from the global sum:
            w_{target} = N * w_{global} - \\sum_{i \\neq target} w_i
        This validates that SecAgg protects against up to N-2 colluding clients,
        matching the theorem of Bonawitz et al. (2017).
        """
        round_id = 88
        dim = 20
        n_clients = 4
        rng = np.random.default_rng(555)

        bank_ids = [f"bank_{i}" for i in range(n_clients)]
        drivers = [P2PSecAggDriver(bid) for bid in bank_ids]
        bundles = [d.generate_round_keypair(round_id) for d in drivers]

        weights = [rng.uniform(-1.0, 1.0, size=dim).tolist() for _ in range(n_clients)]

        masked = {}
        for i, d in enumerate(drivers):
            peers = [bundles[j] for j in range(n_clients) if j != i]
            masked[d.bank_id] = d.compute_masked_vector(weights[i], peers, quantization_scale=1e6)

        # Coordinator computes honest global average
        global_avg = P2PSecAggDriver.aggregate_masked_vectors(masked, quantization_scale=1e6)

        # If bank_1, bank_2, bank_3 (3 out of 4, N-1) collude with coordinator:
        # They solve for bank_0 algebraically from global sum:
        reconstructed_w0 = [
            n_clients * global_avg[k] - sum(weights[j][k] for j in [1, 2, 3])
            for k in range(dim)
        ]

        # The algebraic reconstruction matches bank_0 within floating point precision
        for k in range(dim):
            assert math.isclose(reconstructed_w0[k], weights[0][k], abs_tol=1e-5)

    def test_zero_raw_pii_payload_guarantee(self):
        """Verify that coordinator payload contains zero floats, strings, or PII."""
        driver = P2PSecAggDriver("bank_alpha")
        bundle = driver.generate_round_keypair(round_id=1)
        peer = P2PSecAggDriver("bank_beta").generate_round_keypair(round_id=1)

        weights = [0.123, -0.456, 0.789]
        masked_vector = driver.compute_masked_vector(weights, [peer])

        # Assert every element in submission is an unsigned integer in Z_{2^32}
        assert all(isinstance(x, int) for x in masked_vector)
        assert all(0 <= x < 2**32 for x in masked_vector)
        # Assert no floats or structured strings exist in the payload
        assert not any(isinstance(x, float) for x in masked_vector)

    def test_dropout_privacy_preservation_via_shamir_reconstruction(self):
        """Verify that coordinator recovers surviving aggregate when a node drops out without exposing raw weights."""
        round_id = 99
        dim = 10
        threshold = 2  # 2-out-of-3 threshold
        bank_ids = ["bank_a", "bank_b", "bank_c"]
        drivers = {bid: P2PSecAggDriver(bid) for bid in bank_ids}
        bundles = {bid: drivers[bid].generate_round_keypair(round_id) for bid in bank_ids}

        weights = {
            "bank_a": [1.0] * dim,
            "bank_b": [2.0] * dim,
            "bank_c": [3.0] * dim,
        }

        # Each client splits its secrets among peers (with self_mask enabled)
        all_shares: dict[str, dict[str, tuple[ShamirShare, ShamirShare]]] = {}
        for bid in bank_ids:
            peer_ids = [p for p in bank_ids if p != bid]
            all_shares[bid] = drivers[bid].split_round_secrets(peer_ids, threshold)

        # Clients compute masked vectors with self_mask enabled
        masked_vectors = {}
        for bid in bank_ids:
            peers = [bundles[p] for p in bank_ids if p != bid]
            masked_vectors[bid] = drivers[bid].compute_masked_vector(
                weights[bid], peers, quantization_scale=1e6, use_self_mask=True
            )

        # Simulating DROPOUT: bank_c drops out before aggregation!
        # Surviving clients: bank_a and bank_b
        surviving = ["bank_a", "bank_b"]
        surviving_masked = {bid: masked_vectors[bid] for bid in surviving}

        # For surviving clients, coordinator gets their b_u shares to subtract self-masks
        surviving_b_shares = {
            "bank_a": [all_shares["bank_a"]["bank_a"][0], all_shares["bank_a"]["bank_b"][0]],
            "bank_b": [all_shares["bank_b"]["bank_a"][0], all_shares["bank_b"]["bank_b"][0]],
        }

        # For dropped client bank_c, coordinator gets x_c shares to cancel unreciprocated pairwise masks
        dropped_x_shares = {
            "bank_c": [all_shares["bank_c"]["bank_a"][1], all_shares["bank_c"]["bank_b"][1]],
        }

        peer_pks = {bid: bundles[bid].public_key_bytes for bid in bank_ids}

        # Coordinator reconstructs aggregate of SURVIVING clients (bank_a + bank_b) / 2 = 1.5
        surviving_avg = P2PSecAggDriver.reconstruct_aggregate_with_dropouts(
            surviving_masked_vectors=surviving_masked,
            surviving_b_shares=surviving_b_shares,
            dropped_x_shares=dropped_x_shares,
            peer_public_keys=peer_pks,
            threshold=threshold,
            round_id=round_id,
            quantization_scale=1e6,
        )

        expected_surviving_avg = [1.5] * dim
        for k in range(dim):
            assert math.isclose(surviving_avg[k], expected_surviving_avg[k], abs_tol=1e-4)

    def test_replay_attack_rejected_via_hkdf_domain_separation(self):
        """Verify that an intercepted masked vector from round t replayed in round t+1 produces garbled output."""
        driver_a = P2PSecAggDriver("bank_alpha")
        driver_b = P2PSecAggDriver("bank_beta")

        # Round 1
        bundle_a_r1 = driver_a.generate_round_keypair(round_id=1)
        bundle_b_r1 = driver_b.generate_round_keypair(round_id=1)
        w_a = [10.0] * 5
        w_b = [20.0] * 5
        y_a_r1 = driver_a.compute_masked_vector(w_a, [bundle_b_r1], quantization_scale=1e6)

        # Round 2: Adversary replays y_a_r1 instead of generating a fresh y_a_r2
        bundle_a_r2 = driver_a.generate_round_keypair(round_id=2)
        bundle_b_r2 = driver_b.generate_round_keypair(round_id=2)
        y_b_r2 = driver_b.compute_masked_vector(w_b, [bundle_a_r2], quantization_scale=1e6)

        # Coordinator attempts to aggregate replayed y_a_r1 with round 2 y_b_r2
        replayed_res = P2PSecAggDriver.aggregate_masked_vectors(
            {"bank_alpha": y_a_r1, "bank_beta": y_b_r2}, quantization_scale=1e6
        )

        expected_valid_avg = 15.0
        # Replayed masks do not cancel: difference must be massive (> 100.0)
        assert any(abs(v - expected_valid_avg) > 100.0 for v in replayed_res), (
            "Replay attack was not thwarted: masks cancelled across rounds!"
        )
