"""Targeted unit tests for ShamirSecretSharingEngine.

Covers:
  - (t, n) secret split & exact t reconstruction
  - Secret reconstruction with >t shares
  - Failure when <t shares provided
  - Serialization / deserialization roundtrip (to_bytes, from_bytes)
  - Boundary conditions (random 32-byte secrets, max size guards)
  - Duplicate share detection
"""

from __future__ import annotations

import os

import pytest

from app.infrastructure.security.shamir_engine import (
    ShamirSecretSharingEngine,
    ShamirShare,
)


@pytest.fixture()
def engine() -> ShamirSecretSharingEngine:
    return ShamirSecretSharingEngine()


def test_shamir_split_and_exact_threshold_reconstruction(
    engine: ShamirSecretSharingEngine,
) -> None:
    """Exact t shares must reconstruct the original 32-byte secret."""
    secret = os.urandom(32)
    threshold = 3
    total_shares = 5

    shares = engine.split_secret(secret, threshold=threshold, total_shares=total_shares)
    assert len(shares) == 5

    # Any subset of 3 shares should reconstruct
    reconstructed = engine.reconstruct_secret(shares[:3], threshold=threshold)
    assert reconstructed == secret

    # Another subset of 3 shares (e.g. shares[1], shares[3], shares[4])
    subset_2 = [shares[1], shares[3], shares[4]]
    reconstructed_2 = engine.reconstruct_secret(subset_2, threshold=threshold)
    assert reconstructed_2 == secret


def test_shamir_reconstruction_with_more_than_threshold(
    engine: ShamirSecretSharingEngine,
) -> None:
    """Providing all n shares (>t) must reconstruct the original secret."""
    secret = os.urandom(32)
    shares = engine.split_secret(secret, threshold=2, total_shares=4)
    reconstructed = engine.reconstruct_secret(shares, threshold=2)
    assert reconstructed == secret


def test_shamir_reconstruction_fails_with_fewer_than_threshold(
    engine: ShamirSecretSharingEngine,
) -> None:
    """Fewer than t shares must raise a ValueError."""
    secret = os.urandom(32)
    shares = engine.split_secret(secret, threshold=3, total_shares=5)

    with pytest.raises(ValueError, match="Insufficient shares"):
        engine.reconstruct_secret(shares[:2], threshold=3)


def test_shamir_share_serialization_roundtrip() -> None:
    """ShamirShare.to_bytes() and from_bytes() must be an exact identity operation."""
    original_share = ShamirShare(x=4, y=12345678901234567890)
    data = original_share.to_bytes()
    assert len(data) == 68

    deserialized = ShamirShare.from_bytes(data)
    assert deserialized.x == original_share.x
    assert deserialized.y == original_share.y


def test_shamir_share_deserialization_invalid_length() -> None:
    """Deserializing from data not equal to 68 bytes must raise ValueError."""
    with pytest.raises(ValueError, match="Invalid ShamirShare byte length"):
        ShamirShare.from_bytes(b"\x00" * 50)


def test_shamir_invalid_threshold_or_total(engine: ShamirSecretSharingEngine) -> None:
    """Invalid threshold parameters must raise ValueError."""
    secret = os.urandom(32)
    with pytest.raises(ValueError, match="Threshold t must be at least 2"):
        engine.split_secret(secret, threshold=1, total_shares=3)

    with pytest.raises(ValueError, match="cannot exceed total_shares"):
        engine.split_secret(secret, threshold=4, total_shares=3)


def test_shamir_duplicate_shares_rejected(engine: ShamirSecretSharingEngine) -> None:
    """Duplicate share evaluation points must raise ValueError."""
    secret = os.urandom(32)
    shares = engine.split_secret(secret, threshold=3, total_shares=5)
    duplicate_subset = [shares[0], shares[0], shares[1]]

    with pytest.raises(ValueError, match="Duplicate share"):
        engine.reconstruct_secret(duplicate_subset, threshold=3)
