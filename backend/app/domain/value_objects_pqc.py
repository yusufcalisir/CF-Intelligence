"""Domain value objects for Post-Quantum Cryptography (PQC SecAgg & Kyber/Dilithium)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PQCKemAlgorithm(StrEnum):
    """NIST FIPS 203 Module Lattice Key Encapsulation Mechanism (ML-KEM) standards."""

    KYBER_512 = "Kyber512"
    KYBER_768 = "Kyber768"  # NIST Security Level 3 (Default)
    KYBER_1024 = "Kyber1024"  # NIST Security Level 5


class PQCSignatureAlgorithm(StrEnum):
    """NIST FIPS 204 Module Lattice Digital Signature Algorithm (ML-DSA) standards."""

    DILITHIUM_2 = "Dilithium2"
    DILITHIUM_3 = "Dilithium3"  # NIST Security Level 3 (Default)
    DILITHIUM_5 = "Dilithium5"  # NIST Security Level 5


@dataclass(frozen=True)
class PQCKyberKeyPair:
    """NIST FIPS 203 CRYSTALS-Kyber KEM keypair container."""

    public_key_bytes: bytes  # 1,184 bytes for Kyber768
    secret_key_bytes: bytes  # 2,400 bytes for Kyber768
    algorithm: PQCKemAlgorithm = PQCKemAlgorithm.KYBER_768


@dataclass(frozen=True)
class PQCDilithiumKeyPair:
    """NIST FIPS 204 CRYSTALS-Dilithium Digital Signature keypair container."""

    public_key_bytes: bytes  # 1,952 bytes for Dilithium3
    secret_key_bytes: bytes  # 4,016 bytes for Dilithium3
    algorithm: PQCSignatureAlgorithm = PQCSignatureAlgorithm.DILITHIUM_3


@dataclass(frozen=True)
class PQCPublicKeyBundle:
    """Post-quantum ephemeral public key bundle advertised by a bank node in an FL round."""

    bank_id: str
    round_id: int
    kyber_pk_bytes: bytes
    x25519_pk_bytes: bytes
    dilithium_signature: bytes
    created_at_timestamp: float


@dataclass(frozen=True)
class PQCSecAggState:
    """Snapshot of PQC P2P SecAgg key exchange round status."""

    round_id: int
    participating_banks: list[str]
    quantum_security_level: str  # e.g., 'NIST Level 3 (256-bit Lattice Security)'
    kem_algorithm: str
    signature_algorithm: str
    hybrid_shared_secrets_derived: int
    zero_sum_verified: bool
    lineage_hash: str
    audit_events: list[dict[str, Any]]
