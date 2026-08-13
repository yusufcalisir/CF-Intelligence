"""Unit tests for Post-Quantum Cryptography (PQC SecAgg & Kyber/Dilithium) Driver."""

from __future__ import annotations

import unittest

import numpy as np

from app.domain.value_objects_pqc import PQCKemAlgorithm, PQCSignatureAlgorithm
from app.infrastructure.security.pqc_secagg_driver import PQCSecAggDriver


class TestPQCSecAggDriver(unittest.TestCase):
    """Test suite verifying NIST FIPS 203 Kyber-768 KEM and FIPS 204 Dilithium-3 signatures."""

    def setUp(self) -> None:
        self.driver = PQCSecAggDriver()

    def test_kyber768_keypair_generation(self) -> None:
        """Assert Kyber-768 keypair complies with NIST size specifications."""
        kp = self.driver.generate_kyber_keypair(PQCKemAlgorithm.KYBER_768)

        self.assertEqual(kp.algorithm, PQCKemAlgorithm.KYBER_768)
        self.assertEqual(len(kp.public_key_bytes), 1184)
        self.assertEqual(len(kp.secret_key_bytes), 2400)

    def test_dilithium3_keypair_and_signature_verification(self) -> None:
        """Assert Dilithium-3 lattice signature generation and verification."""
        kp = self.driver.generate_dilithium_keypair(PQCSignatureAlgorithm.DILITHIUM_3)

        self.assertEqual(kp.algorithm, PQCSignatureAlgorithm.DILITHIUM_3)
        self.assertEqual(len(kp.public_key_bytes), 1952)
        self.assertEqual(len(kp.secret_key_bytes), 4016)

        payload = b"FL_Round_42_PQC_SecAgg_Bundle"
        signature = self.driver.sign_dilithium(payload, kp.secret_key_bytes)
        self.assertGreaterEqual(len(signature), 3293)

        is_valid = self.driver.verify_dilithium_signature(payload, signature, kp.public_key_bytes)
        self.assertTrue(is_valid)

    def test_kyber_kem_encapsulation_decapsulation_matching(self) -> None:
        """Assert Kyber KEM encapsulation and decapsulation derive identical 32-byte shared secrets."""
        kp = self.driver.generate_kyber_keypair()
        ct, ss_encap = self.driver.encapsulate_secret(kp.public_key_bytes)
        ss_decap = self.driver.decapsulate_secret(ct, kp.secret_key_bytes, kp.public_key_bytes)

        self.assertEqual(len(ct), 1088)
        self.assertEqual(len(ss_encap), 32)
        self.assertEqual(ss_encap, ss_decap)

    def test_hybrid_pqc_pairwise_mask_derivation(self) -> None:
        """Assert hybrid (Kyber-768 + X25519) shared secret derives valid uint32 mask vectors."""
        ss_kyber = b"\x42" * 32
        ss_x25519 = b"\x99" * 32

        mask = self.driver.derive_hybrid_pairwise_mask(ss_kyber, ss_x25519, vector_len=128)

        self.assertEqual(len(mask), 128)
        self.assertEqual(mask.dtype, np.uint32)

    def test_pqc_secagg_round_state_telemetry(self) -> None:
        """Assert round state telemetry generates correct zero-sum status and lineage hash."""
        state = self.driver.compute_pqc_secagg_round_state(
            round_id=42,
            participating_banks=["bank_alpha", "bank_beta", "bank_gamma"],
        )

        self.assertEqual(state.round_id, 42)
        self.assertEqual(state.hybrid_shared_secrets_derived, 3)
        self.assertTrue(state.zero_sum_verified)
        self.assertIsNotNone(state.lineage_hash)
        self.assertEqual(len(state.audit_events), 4)


if __name__ == "__main__":
    unittest.main()
