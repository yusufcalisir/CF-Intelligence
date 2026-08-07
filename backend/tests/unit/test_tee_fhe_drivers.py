import unittest

import numpy as np

from app.domain.value_objects import ModelWeights
from app.infrastructure.security.fhe_driver import FHEDriver
from app.infrastructure.security.tee_driver import TEEDriver


class TestTEEFHEDrivers(unittest.TestCase):
    """Unit tests for TEE and FHE security drivers."""

    def setUp(self) -> None:
        self.simulation_id = "test_simulation_12345"
        self.layer_shapes: list[tuple[int, ...]] = [(2, 2), (2,)]
        self.weights_a = ModelWeights(
            layer_shapes=self.layer_shapes,
            flat_weights=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        )
        self.weights_b = ModelWeights(
            layer_shapes=self.layer_shapes,
            flat_weights=[3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
        )

    def test_fhe_driver_lifecycle(self) -> None:
        """Test FHE key generation, encryption, homomorphic addition, and decryption."""
        # 1. Generate keys
        key_ring = FHEDriver.generate_keys(self.simulation_id)
        self.assertIn(key_ring.poly_degree, [4096, 8192])
        self.assertTrue(key_ring.public_key.startswith("fhe_pub_key_"))
        self.assertTrue(key_ring.secret_key.startswith("fhe_sec_key_"))

        # 2. Encrypt weights
        enc_a = FHEDriver.encrypt_weights(self.weights_a, key_ring)
        enc_b = FHEDriver.encrypt_weights(self.weights_b, key_ring)

        self.assertEqual(enc_a.param_count, 6)
        self.assertEqual(enc_a.key_id, self.simulation_id)
        self.assertEqual(enc_a.noise_bound, 1e-9)

        # 3. Homomorphic average
        enc_avg = FHEDriver.homomorphic_average(
            [enc_a, enc_b],
            client_samples=[10, 10],
            public_context_bytes=key_ring.public_context_bytes,
        )
        self.assertEqual(enc_avg.key_id, self.simulation_id)
        self.assertGreater(enc_avg.noise_bound, 0.0)

        # 4. Decrypt weights
        decrypted = FHEDriver.decrypt_weights(enc_avg, key_ring, self.layer_shapes)
        self.assertEqual(decrypted.layer_shapes, self.layer_shapes)
        self.assertEqual(len(decrypted.flat_weights), 6)

        # The result of (1.0 + 3.0)/2 = 2.0, with very minor FHE noise
        np.testing.assert_allclose(
            decrypted.flat_weights, [2.0, 3.0, 4.0, 5.0, 6.0, 7.0], rtol=1e-5
        )

    def test_tee_driver_lifecycle(self) -> None:
        """Test TEE enclave creation, remote attestation, secure aggregation, and sealing."""
        # 1. Create enclave
        enclave_ctx = TEEDriver.create_enclave(self.simulation_id)
        self.assertEqual(enclave_ctx.enclave_id, self.simulation_id)
        self.assertTrue(enclave_ctx.attestation_public_key.startswith("tee_attest_pub_"))

        # 2. Generate Attestation Report
        report = TEEDriver.generate_attestation_report(enclave_ctx)
        self.assertEqual(report.enclave_id, self.simulation_id)
        self.assertEqual(report.mrenclave, enclave_ctx.mrenclave)
        self.assertTrue(report.verified)

        # 3. Secure Aggregation
        aggregated = TEEDriver.execute_secure_aggregation(
            enclave_ctx, [self.weights_a, self.weights_b], client_samples=[10, 10]
        )
        self.assertEqual(aggregated.layer_shapes, self.layer_shapes)
        # Plain fed_avg aggregate of [1.0, 3.0] is [2.0]
        np.testing.assert_allclose(aggregated.flat_weights, [2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

        # 4. Sealing/Unsealing
        sensitive_data = b"secret_transaction_data_record"
        key = b"enclave_sealing_key_99999"

        sealed = TEEDriver.seal_data(sensitive_data, key)
        self.assertNotEqual(sealed, sensitive_data)

        unsealed = TEEDriver.unseal_data(sealed, key)
        self.assertEqual(unsealed, sensitive_data)
