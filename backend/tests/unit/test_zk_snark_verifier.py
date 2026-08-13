"""Unit tests for Zero-Knowledge Proof (zk-SNARK) Model Weight Attestation Driver."""

from __future__ import annotations

import unittest

import numpy as np

from app.domain.value_objects import ModelWeights
from app.domain.value_objects_zkp import ZKSNARKAttestationProof
from app.infrastructure.security.zk_snark_verifier import (
    PoseidonHasher,
    ZKSNARKCircuitProver,
    ZKSNARKProofVerifier,
)


class TestZKSNARKAttestation(unittest.TestCase):
    """Test suite verifying zk-SNARK attestation proof generation and O(1) bilinear pairing verification."""

    def setUp(self) -> None:
        self.prover = ZKSNARKCircuitProver()
        self.verifier = ZKSNARKProofVerifier()
        self.sample_weights = ModelWeights(
            layer_shapes=[(2, 2)],
            flat_weights=[0.1, -0.2, 0.3, 0.4],
        )

    def test_poseidon_hasher_reproducibility(self) -> None:
        """Assert Poseidon hash is deterministic and produces F_p field element."""
        vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        h1 = PoseidonHasher.hash_vector(vec)
        h2 = PoseidonHasher.hash_vector(vec)
        self.assertEqual(h1, h2)
        self.assertTrue(h1.startswith("0x"))

    def test_proof_generation_and_verification_success(self) -> None:
        """Assert valid weight update generates Groth16 proof passing O(1) verification."""
        proof = self.prover.generate_attestation_proof(
            weights=self.sample_weights,
            bank_id="bank_alpha",
            round_id=1,
            l2_norm_bound=10.0,
        )
        self.assertIsInstance(proof, ZKSNARKAttestationProof)
        self.assertEqual(proof.bank_id, "bank_alpha")
        self.assertEqual(proof.round_id, 1)

        result = self.verifier.verify_attestation_proof(proof)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.status_code, "VALID")
        self.assertTrue(result.pairing_check_passed)
        self.assertLess(result.verification_time_ms, 50.0)

    def test_norm_exceeded_rejected(self) -> None:
        """Assert weight update exceeding max L2 norm bound fails verification."""
        proof = self.prover.generate_attestation_proof(
            weights=self.sample_weights,
            bank_id="bank_beta",
            round_id=2,
            l2_norm_bound=15.0,
        )
        # Verify against stricter threshold (e.g. 0.001)
        result = self.verifier.verify_attestation_proof(proof, max_permitted_norm=0.001)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.status_code, "NORM_EXCEEDED")

    def test_hash_mismatch_rejected(self) -> None:
        """Assert proof with mismatched Poseidon hash digest fails verification."""
        proof = self.prover.generate_attestation_proof(
            weights=self.sample_weights,
            bank_id="bank_gamma",
            round_id=3,
            l2_norm_bound=10.0,
        )
        fake_hash = "0x00000000000000000000000000000000000000000000000000000000000000ff"
        result = self.verifier.verify_attestation_proof(proof, expected_weight_hash=fake_hash)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.status_code, "HASH_MISMATCH")

    def test_verifier_status_metrics(self) -> None:
        """Assert verifier telemetry metrics report scheme and proof count."""
        proof = self.prover.generate_attestation_proof(
            weights=self.sample_weights,
            bank_id="bank_alpha",
            round_id=1,
        )
        self.verifier.verify_attestation_proof(proof)
        status = self.verifier.get_verifier_status()
        self.assertEqual(status["proving_scheme"], "Groth16")
        self.assertEqual(status["curve"], "BN254")
        self.assertGreaterEqual(status["verified_proofs_count"], 1)


if __name__ == "__main__":
    unittest.main()
