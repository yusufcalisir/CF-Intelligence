"""Scientific Verification Suite: TenSEAL CKKS Homomorphic Sum & Noise Bound Proofs."""

from __future__ import annotations

from hypothesis import given, settings, strategies as st
import numpy as np
import pytest

from app.domain.value_objects import ModelWeights
from app.infrastructure.security.fhe_driver import (
    TENSEAL_AVAILABLE,
    FHEDriver,
)


class TestCKKSHomomorphicLinearityVerification:
    """Mathematical verification of CKKS homomorphic linearity under float domain boundaries."""

    @pytest.mark.skipif(not TENSEAL_AVAILABLE, reason="TenSEAL library not installed in environment")
    @settings(max_examples=10, deadline=10000)
    @given(
        v1=st.lists(st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False), min_size=5, max_size=20),
        v2=st.lists(st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False), min_size=5, max_size=20),
    )
    def test_homomorphic_addition_linearity_property(self, v1: list[float], v2: list[float]):
        """Property-based verification: Dec(Enc(v1) + Enc(v2)) == v1 + v2 within CKKS error bound."""
        length = min(len(v1), len(v2))
        arr1 = np.array(v1[:length], dtype=np.float64)
        arr2 = np.array(v2[:length], dtype=np.float64)
        expected_sum = arr1 + arr2

        key_ring = FHEDriver.generate_keys("prop_fhe_verify", poly_degree=8192)
        shapes = [(length,)]

        enc1 = FHEDriver.encrypt_weights(ModelWeights(layer_shapes=shapes, flat_weights=arr1.tolist()), key_ring)
        enc2 = FHEDriver.encrypt_weights(ModelWeights(layer_shapes=shapes, flat_weights=arr2.tolist()), key_ring)

        enc_sum = FHEDriver.homomorphic_average(
            [enc1, enc2],
            client_samples=[1, 1],
            public_context_bytes=key_ring.public_context_bytes,
        )

        # Average is (v1 + v2) / 2
        dec_avg = FHEDriver.decrypt_weights(enc_sum, key_ring, shapes)
        actual_avg = np.array(dec_avg.flat_weights)
        expected_avg = expected_sum / 2.0

        abs_err = np.abs(expected_avg - actual_avg)
        max_err = float(np.max(abs_err))
        # Max Absolute Error bound for CKKS scheme with scale 2^40 should be < 1e-4
        assert max_err < 1e-4
