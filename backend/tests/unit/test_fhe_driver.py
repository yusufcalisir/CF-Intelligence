"""Unit tests for TenSEAL (Microsoft SEAL) CKKS Fully Homomorphic Encryption (FHE) Driver."""

from __future__ import annotations

import numpy as np
import pytest

from app.domain.value_objects import ModelWeights
from app.infrastructure.security.fhe_driver import (
    TENSEAL_AVAILABLE,
    FHEDriver,
)


class TestTenSEALFHEDriver:
    """TestSuite verifying CKKS homomorphic key generation, encryption, addition, and decryption."""

    def test_key_generation_and_serialization(self):
        """Verify generation of FHE keyring with public/secret context separation."""
        key_ring = FHEDriver.generate_keys("sim_fhe_101", poly_degree=8192)
        assert key_ring.key_id == "sim_fhe_101"
        assert key_ring.poly_degree == 8192
        assert len(key_ring.public_context_bytes) > 0
        assert len(key_ring.secret_context_bytes) > 0

    def test_encrypt_and_decrypt_weights(self):
        """Verify plaintext weights -> CKKS ciphertext -> decrypted plaintext cycle."""
        key_ring = FHEDriver.generate_keys("sim_fhe_102", poly_degree=8192)
        layer_shapes = [(2, 3), (3,)]
        flat = [0.5, -1.2, 3.4, 0.0, -0.7, 2.1, 1.1, 0.9, -0.4]
        weights = ModelWeights(layer_shapes=layer_shapes, flat_weights=flat)

        enc = FHEDriver.encrypt_weights(weights, key_ring)
        assert enc.key_id == "sim_fhe_102"
        assert enc.param_count == len(flat)

        dec = FHEDriver.decrypt_weights(enc, key_ring, layer_shapes)
        assert len(dec.flat_weights) == len(flat)

        if TENSEAL_AVAILABLE:
            # Verify high precision floating point recovery
            diff = np.abs(np.array(flat) - np.array(dec.flat_weights))
            assert np.max(diff) < 1e-4

    def test_homomorphic_weighted_averaging(self):
        """Verify homomorphic weighted sum directly on ciphertexts without secret key."""
        key_ring = FHEDriver.generate_keys("sim_fhe_103", poly_degree=8192)
        layer_shapes = [(2, 2)]

        w1_flat = [1.0, 2.0, 3.0, 4.0]
        w2_flat = [5.0, 6.0, 7.0, 8.0]
        w3_flat = [9.0, 10.0, 11.0, 12.0]

        enc1 = FHEDriver.encrypt_weights(ModelWeights(layer_shapes=layer_shapes, flat_weights=w1_flat), key_ring)
        enc2 = FHEDriver.encrypt_weights(ModelWeights(layer_shapes=layer_shapes, flat_weights=w2_flat), key_ring)
        enc3 = FHEDriver.encrypt_weights(ModelWeights(layer_shapes=layer_shapes, flat_weights=w3_flat), key_ring)

        # Equal weighting average: (w1 + w2 + w3) / 3
        enc_avg = FHEDriver.homomorphic_average(
            [enc1, enc2, enc3],
            client_samples=[100, 100, 100],
            public_context_bytes=key_ring.public_context_bytes,
        )

        dec_avg = FHEDriver.decrypt_weights(enc_avg, key_ring, layer_shapes)
        expected_avg = np.array([5.0, 6.0, 7.0, 8.0])

        if TENSEAL_AVAILABLE:
            diff = np.abs(expected_avg - np.array(dec_avg.flat_weights))
            assert np.max(diff) < 1e-4

    def test_mismatched_key_id_rejection(self):
        """Verify decryption throws ValueError when given mismatched key ring."""
        key_ring1 = FHEDriver.generate_keys("sim_fhe_key1", poly_degree=4096)
        key_ring2 = FHEDriver.generate_keys("sim_fhe_key2", poly_degree=4096)

        weights = ModelWeights(layer_shapes=[(2,)], flat_weights=[1.0, 2.0])
        enc = FHEDriver.encrypt_weights(weights, key_ring1)

        with pytest.raises(ValueError, match="Invalid secret key"):
            FHEDriver.decrypt_weights(enc, key_ring2, [(2,)])
