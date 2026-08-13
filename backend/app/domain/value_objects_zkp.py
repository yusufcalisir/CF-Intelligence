"""Zero-Knowledge Proof (zk-SNARK) Model Weight Attestation Domain Value Objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ZKSNARKAttestationProof:
    """Represents a Groth16 zk-SNARK attestation proof generated over BN254 elliptic curve.

    Proves that a bank's local model weight update w_local:
    1. Has Poseidon hash matching public_weight_hash (Poseidon(w_local) == H_w)
    2. Satisfies L2 norm bound ||w_local||_2 <= l2_norm_bound
    3. Has non-zero variance Var(w_local) > 0 (prevents zero-update free-riding attacks)
    """

    proof_id: str
    bank_id: str
    round_id: int
    pi_a: list[str]  # G1 element [x, y] in hex or decimal string
    pi_b: list[list[str]]  # G2 element [[x1, x2], [y1, y2]] in hex/dec string
    pi_c: list[str]  # G1 element [x, y] in hex or decimal string
    public_weight_hash: str  # Hex string Poseidon hash digest H_w
    l2_norm_bound: float
    vector_dimension: int
    created_at_timestamp: float


@dataclass(frozen=True)
class ZKCircuitParams:
    """Configuration parameters for the weight attestation zk-SNARK circuit."""

    curve_name: str = "BN254"
    proving_scheme: str = "Groth16"
    hash_algorithm: str = "Poseidon-BN254"
    max_vector_dimension: int = 10000
    constraint_count: int = 65536
    field_prime: str = "21888242871839275222246405745257275088548364400416034343698204186575808495617"


@dataclass(frozen=True)
class ZKPVerificationResult:
    """Output container for zk-SNARK verification evaluation."""

    is_valid: bool
    status_code: str  # e.g., 'VALID', 'INVALID_PROOF', 'NORM_EXCEEDED', 'HASH_MISMATCH'
    proof_id: str
    bank_id: str
    verification_time_ms: float
    verification_message: str
    pairing_check_passed: bool
    circuit_metadata: dict[str, Any] = field(default_factory=dict)
