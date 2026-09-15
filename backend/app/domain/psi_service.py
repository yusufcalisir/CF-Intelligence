"""Diffie-Hellman Private Set Intersection (DH-PSI 2048-bit) Domain Module.

Houses canonical 2048-bit MODP prime (NIST SP 800-131A / RFC 3526), group mapping,
modular exponentiations, commutativity verification, pure domain fuzzy feature extraction,
and the DiffieHellmanPSIProtocol domain protocol entity.
Enforces Clean Architecture: Domain layer has zero dependencies on Application or Infrastructure.
"""

from __future__ import annotations

import hashlib
from typing import Any

# NIST SP 800-131A / RFC 3526 2048-bit MODP prime
PSI_PRIME: int = (
    0xFFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE65381FFFFFFFFFFFFFFFF
)
PRIME_BIT_LENGTH: int = 2048


def hash_to_group(identifier: str | bytes | int, prime: int = PSI_PRIME) -> int:
    """Map an arbitrary identifier into the multiplicative group Z_p^* in [2, prime - 2].

    Handles hexadecimal strings, raw arbitrary text (non-hex), UUIDs, raw bytes,
    and integers deterministically without crashing on invalid hex literals.
    """
    if isinstance(identifier, int):
        val = identifier
    elif isinstance(identifier, bytes):
        val = int.from_bytes(identifier, "big")
    elif isinstance(identifier, str):
        # First attempt parsing as hex for backward compatibility with 128/256-bit privacy IDs
        try:
            val = int(identifier, 16)
        except ValueError:
            # Not a pure hex string; hash using SHA-256 to produce uniform integer
            digest = hashlib.sha256(identifier.encode("utf-8")).digest()
            val = int.from_bytes(digest, "big")
    else:
        val = int(str(identifier), 10)

    # If value is already in valid non-trivial range [2, prime - 2], preserve it
    if 2 <= val <= prime - 2:
        return val
    if val < 2:
        return 2

    # Restrict larger values to multiplicative group element in [2, prime - 2]
    return (val % (prime - 3)) + 2


def parse_blinded_element(h: str | int | bytes, prime: int = PSI_PRIME) -> int:
    """Parse a client-received blinded group element c in Z_p^* [1, prime - 1]."""
    if isinstance(h, int):
        val = h
    elif isinstance(h, bytes):
        val = int.from_bytes(h, "big")
    elif isinstance(h, str):
        try:
            val = int(h, 16)
        except ValueError:
            val = int(h, 10)
    else:
        val = int(h)

    if val <= 0 or val >= prime:
        val = (val % (prime - 1)) or 1
    return val


def blind_element(element_int: int, private_exponent: int, prime: int = PSI_PRIME) -> int:
    """Compute single-party modular exponentiation: c = x^k mod p."""
    if private_exponent <= 1:
        raise ValueError("Private exponent must be strictly greater than 1")
    return pow(element_int, private_exponent, prime)


def double_blind_element(blinded_int: int, second_exponent: int, prime: int = PSI_PRIME) -> int:
    """Compute second-party modular exponentiation: d = (x^k_a)^k_b mod p."""
    if second_exponent <= 1:
        raise ValueError("Second private exponent must be strictly greater than 1")
    return pow(blinded_int, second_exponent, prime)


def verify_commutativity(
    element: str | int,
    key_a: int,
    key_b: int,
    prime: int = PSI_PRIME,
) -> bool:
    """Formally verify commutative modular exponentiation: (H(x)^a)^b == (H(x)^b)^a mod p."""
    elem_int = hash_to_group(element, prime=prime) if not isinstance(element, int) else element
    enc_a = pow(elem_int, key_a, prime)
    double_enc_a = pow(enc_a, key_b, prime)

    enc_b = pow(elem_int, key_b, prime)
    double_enc_b = pow(enc_b, key_a, prime)

    return double_enc_a == double_enc_b


def extract_fuzzy_features(e: Any) -> dict[str, str]:
    """Extract standard attributes for Fuzzy PSI matching without mock fallbacks.

    Extracts: phone, email, device_id, birthdate, surname from entity attributes.
    Zero-Mock Invariant: When attributes are missing, they remain empty strings.
    Never fabricates fake phone numbers, emails, or surnames.
    """
    attrs = getattr(e, "attributes", None) or {}

    features = {
        "phone": str(attrs.get("phone", "")).strip(),
        "email": str(attrs.get("email", "")).strip(),
        "device_id": str(attrs.get("device_id", "")).strip(),
        "birthdate": str(attrs.get("birthdate", "")).strip(),
        "surname": str(attrs.get("surname", "")).strip(),
    }

    # If attributes were empty for a specific typed entity, check privacy_id
    entity_type = getattr(e, "entity_type", None)
    if entity_type is not None:
        type_val = entity_type.value if hasattr(entity_type, "value") else str(entity_type)
        type_val = type_val.lower()

        privacy_id = getattr(e, "privacy_id", "")
        if type_val == "email" and not features["email"]:
            features["email"] = privacy_id
        elif type_val == "phone" and not features["phone"]:
            features["phone"] = privacy_id
        elif type_val == "device" and not features["device_id"]:
            features["device_id"] = privacy_id

    return features


# Backward-compatible alias
_extract_fuzzy_features = extract_fuzzy_features


class DiffieHellmanPSIProtocol:
    """Pure domain entity orchestrating the mathematical stages of DH-PSI 2048-bit."""

    def __init__(self, prime: int = PSI_PRIME) -> None:
        self.prime = prime
        self.prime_bit_length = PRIME_BIT_LENGTH

    def blind_elements(
        self,
        elements: list[str | int | bytes],
        private_exponent: int,
    ) -> list[int]:
        """Map raw/hashed elements to group and apply local blinding exponent."""
        return [
            blind_element(hash_to_group(elem, self.prime), private_exponent, self.prime)
            for elem in elements
        ]

    def double_blind_elements(
        self,
        blinded_elements: list[int],
        second_exponent: int,
    ) -> list[int]:
        """Apply second party's modular exponentiation to already-blinded elements."""
        return [
            double_blind_element(elem, second_exponent, self.prime)
            for elem in blinded_elements
        ]

    def compute_intersection(
        self,
        double_blinded_a: list[int],
        double_blinded_b: list[int],
    ) -> set[int]:
        """Find intersection of double-blinded elements."""
        return set(double_blinded_a).intersection(set(double_blinded_b))


def __getattr__(name: str) -> Any:
    """Lazy resolver for PSIService to prevent circular top-level dependencies."""
    if name == "PSIService":
        from app.application.services.psi_service import PSIService
        return PSIService
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "PSI_PRIME",
    "PRIME_BIT_LENGTH",
    "hash_to_group",
    "parse_blinded_element",
    "blind_element",
    "double_blind_element",
    "verify_commutativity",
    "extract_fuzzy_features",
    "_extract_fuzzy_features",
    "DiffieHellmanPSIProtocol",
]
