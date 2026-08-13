"""Targeted unit tests for P2P Curve25519 ECDH SecAgg Driver.

Covers:
  - Ephemeral keypair generation & authenticated bundle creation
  - HMAC bundle verification (valid & tampered)
  - ECDH key agreement symmetry (both sides derive identical seed)
  - HKDF seed uniqueness across rounds and pairs
  - PRG mask expansion determinism & modular distribution
  - 3-party zero-sum cancellation with float dequantization
  - Round ID mismatch guard
  - Coordinator aggregation numerical precision
"""

from __future__ import annotations

import os

import pytest

from app.infrastructure.security.p2p_secagg_driver import (
    ECDHPublicKeyBundle,
    P2PSecAggDriver,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ROUND_ID = 7


@pytest.fixture()
def driver_alpha() -> P2PSecAggDriver:
    return P2PSecAggDriver("bank_alpha", identity_secret=b"A" * 32)


@pytest.fixture()
def driver_beta() -> P2PSecAggDriver:
    return P2PSecAggDriver("bank_beta", identity_secret=b"B" * 32)


@pytest.fixture()
def driver_gamma() -> P2PSecAggDriver:
    return P2PSecAggDriver("bank_gamma", identity_secret=b"G" * 32)


@pytest.fixture()
def bundle_alpha(driver_alpha: P2PSecAggDriver) -> ECDHPublicKeyBundle:
    return driver_alpha.generate_round_keypair(ROUND_ID)


@pytest.fixture()
def bundle_beta(driver_beta: P2PSecAggDriver) -> ECDHPublicKeyBundle:
    return driver_beta.generate_round_keypair(ROUND_ID)


@pytest.fixture()
def bundle_gamma(driver_gamma: P2PSecAggDriver) -> ECDHPublicKeyBundle:
    return driver_gamma.generate_round_keypair(ROUND_ID)


# ---------------------------------------------------------------------------
# Test: Bundle structure & authentication
# ---------------------------------------------------------------------------


def test_bundle_fields_populated(bundle_alpha: ECDHPublicKeyBundle) -> None:
    assert bundle_alpha.bank_id == "bank_alpha"
    assert bundle_alpha.round_id == ROUND_ID
    assert len(bundle_alpha.public_key_bytes) == 32
    assert len(bundle_alpha.hmac_signature) == 32


def test_bundle_hmac_valid(
    bundle_alpha: ECDHPublicKeyBundle,
) -> None:
    assert P2PSecAggDriver.verify_peer_bundle(bundle_alpha, b"A" * 32)


def test_bundle_hmac_rejected_wrong_secret(
    bundle_alpha: ECDHPublicKeyBundle,
) -> None:
    assert not P2PSecAggDriver.verify_peer_bundle(bundle_alpha, b"X" * 32)


def test_bundle_hmac_rejected_tampered_pk(
    driver_alpha: P2PSecAggDriver,
    bundle_alpha: ECDHPublicKeyBundle,
) -> None:
    tampered = ECDHPublicKeyBundle(
        bank_id=bundle_alpha.bank_id,
        round_id=bundle_alpha.round_id,
        public_key_bytes=bytes(32),          # zeroed public key
        hmac_signature=bundle_alpha.hmac_signature,
    )
    assert not P2PSecAggDriver.verify_peer_bundle(tampered, b"A" * 32)


# ---------------------------------------------------------------------------
# Test: ECDH shared secret symmetry
# ---------------------------------------------------------------------------


def test_ecdh_seed_symmetric(
    driver_alpha: P2PSecAggDriver,
    driver_beta: P2PSecAggDriver,
    bundle_alpha: ECDHPublicKeyBundle,
    bundle_beta: ECDHPublicKeyBundle,
) -> None:
    """Both sides must arrive at the same HKDF seed."""
    seed_from_alpha = driver_alpha.derive_pairwise_seed(bundle_beta)
    seed_from_beta = driver_beta.derive_pairwise_seed(bundle_alpha)
    assert seed_from_alpha == seed_from_beta


def test_ecdh_seed_unique_per_round(
    driver_alpha: P2PSecAggDriver,
    driver_beta: P2PSecAggDriver,
) -> None:
    """Different rounds must produce different seeds even for the same pair."""
    driver_alpha.generate_round_keypair(1)
    b_beta_r1 = driver_beta.generate_round_keypair(1)
    seed_r1 = driver_alpha.derive_pairwise_seed(b_beta_r1)

    driver_alpha.generate_round_keypair(2)
    b_beta_r2 = driver_beta.generate_round_keypair(2)
    seed_r2 = driver_alpha.derive_pairwise_seed(b_beta_r2)

    assert seed_r1 != seed_r2


def test_ecdh_seed_unique_per_pair(
    driver_alpha: P2PSecAggDriver,
    driver_beta: P2PSecAggDriver,
    driver_gamma: P2PSecAggDriver,
    bundle_alpha: ECDHPublicKeyBundle,
    bundle_beta: ECDHPublicKeyBundle,
    bundle_gamma: ECDHPublicKeyBundle,
) -> None:
    """Alpha↔Beta seed must differ from Alpha↔Gamma seed."""
    seed_ab = driver_alpha.derive_pairwise_seed(bundle_beta)
    seed_ag = driver_alpha.derive_pairwise_seed(bundle_gamma)
    assert seed_ab != seed_ag


# ---------------------------------------------------------------------------
# Test: PRG mask expansion
# ---------------------------------------------------------------------------


def test_mask_expansion_deterministic(bundle_beta: ECDHPublicKeyBundle) -> None:
    seed = b"\xde\xad\xbe\xef" * 8
    mask1 = P2PSecAggDriver.expand_mask(seed, 128)
    mask2 = P2PSecAggDriver.expand_mask(seed, 128)
    assert mask1 == mask2


def test_mask_expansion_correct_length() -> None:
    for d in [1, 10, 63, 64, 65, 256, 1024]:
        mask = P2PSecAggDriver.expand_mask(os.urandom(32), d)
        assert len(mask) == d, f"Expected {d} elements, got {len(mask)}"


def test_mask_elements_in_modular_range() -> None:
    import os
    mask = P2PSecAggDriver.expand_mask(os.urandom(32), 512)
    assert all(0 <= v < 2**32 for v in mask)


# ---------------------------------------------------------------------------
# Test: Zero-Sum Cancellation (3-Party)
# ---------------------------------------------------------------------------


def test_three_party_zero_sum_cancellation(
    driver_alpha: P2PSecAggDriver,
    driver_beta: P2PSecAggDriver,
    driver_gamma: P2PSecAggDriver,
    bundle_alpha: ECDHPublicKeyBundle,
    bundle_beta: ECDHPublicKeyBundle,
    bundle_gamma: ECDHPublicKeyBundle,
) -> None:
    """Full 3-party SecAgg: coordinator output must match unmasked FedAvg average."""
    w_alpha = [1.0, 2.0, 3.0, 4.0, 5.0]
    w_beta  = [5.0, 4.0, 3.0, 2.0, 1.0]
    w_gamma = [2.0, 3.0, 4.0, 3.0, 2.0]

    expected_avg = [
        (w_alpha[i] + w_beta[i] + w_gamma[i]) / 3
        for i in range(5)
    ]

    # Each node masks with all peers except itself
    y_alpha = driver_alpha.compute_masked_vector(w_alpha, [bundle_beta, bundle_gamma])
    y_beta  = driver_beta.compute_masked_vector(w_beta,  [bundle_alpha, bundle_gamma])
    y_gamma = driver_gamma.compute_masked_vector(w_gamma, [bundle_alpha, bundle_beta])

    result = P2PSecAggDriver.aggregate_masked_vectors(
        {"bank_alpha": y_alpha, "bank_beta": y_beta, "bank_gamma": y_gamma}
    )

    import math
    for i in range(5):
        assert math.isclose(result[i], expected_avg[i], rel_tol=1e-4), (
            f"Dimension {i}: expected {expected_avg[i]:.6f}, got {result[i]:.6f}"
        )


def test_two_party_zero_sum_cancellation(
    driver_alpha: P2PSecAggDriver,
    driver_beta: P2PSecAggDriver,
    bundle_alpha: ECDHPublicKeyBundle,
    bundle_beta: ECDHPublicKeyBundle,
) -> None:
    """2-party cancellation correctness."""
    w_alpha = [10.0, -5.0, 0.5]
    w_beta  = [-2.0,  3.0, 1.5]

    expected_avg = [(w_alpha[i] + w_beta[i]) / 2 for i in range(3)]

    y_alpha = driver_alpha.compute_masked_vector(w_alpha, [bundle_beta])
    y_beta  = driver_beta.compute_masked_vector(w_beta,  [bundle_alpha])

    result = P2PSecAggDriver.aggregate_masked_vectors(
        {"bank_alpha": y_alpha, "bank_beta": y_beta}
    )

    import math
    for i in range(3):
        assert math.isclose(result[i], expected_avg[i], rel_tol=1e-4)


# ---------------------------------------------------------------------------
# Test: Guard rails
# ---------------------------------------------------------------------------


def test_derive_seed_before_keypair_raises() -> None:
    driver = P2PSecAggDriver("bank_x")
    fake_bundle = ECDHPublicKeyBundle(
        bank_id="bank_y",
        round_id=1,
        public_key_bytes=bytes(32),
        hmac_signature=bytes(32),
    )
    with pytest.raises(RuntimeError, match="generate_round_keypair"):
        driver.derive_pairwise_seed(fake_bundle)


def test_round_id_mismatch_raises(
    driver_alpha: P2PSecAggDriver,
    driver_beta: P2PSecAggDriver,
) -> None:
    driver_alpha.generate_round_keypair(round_id=5)
    bundle_beta_wrong_round = driver_beta.generate_round_keypair(round_id=9)

    with pytest.raises(ValueError, match="Round ID mismatch"):
        driver_alpha.derive_pairwise_seed(bundle_beta_wrong_round)


def test_aggregate_empty_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        P2PSecAggDriver.aggregate_masked_vectors({})


def test_compute_masked_vector_empty_weights_raises(
    driver_alpha: P2PSecAggDriver,
    bundle_beta: ECDHPublicKeyBundle,
) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        driver_alpha.compute_masked_vector([], [bundle_beta])
