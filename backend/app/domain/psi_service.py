"""Diffie-Hellman Private Set Intersection (DH-PSI 2048-bit) Domain Module.

Houses canonical 2048-bit MODP prime (NIST SP 800-131A / RFC 3526), group mapping,
modular exponentiations, commutativity verification, pure domain fuzzy feature extraction,
and the DiffieHellmanPSIProtocol domain protocol entity.
Enforces Clean Architecture: Domain layer has zero dependencies on Application or Infrastructure.
"""

from __future__ import annotations

import hashlib
from typing import Any

# NIST SP 800-131A / RFC 3526 2048-bit MODP prime (Group 14: 2^2048 - 2^1984 - 1 + 2^64 * { [2^1918 pi] + 124476 })
PSI_PRIME_HEX: str = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF"
)
PSI_PRIME: int = int(PSI_PRIME_HEX, 16)
PRIME_BIT_LENGTH: int = PSI_PRIME.bit_length()
# Safe prime order q = (p - 1) // 2 for cyclic subgroup G_q of quadratic residues
PSI_ORDER_Q: int = (PSI_PRIME - 1) // 2
PSI_GENERATOR: int = 2
PSI_DOMAIN_SEPARATOR: bytes = b"CFI-DH-PSI-2048-v1-DOMAIN-SEPARATION"


def hash_to_prime_subgroup(
    identifier: str | bytes | int,
    prime: int = PSI_PRIME,
    order_q: int = PSI_ORDER_Q,
) -> int:
    """Map identifier strictly into the prime-order subgroup G_q of Z_p* (order q).

    Uses domain-separated HMAC-SHA512 followed by modular squaring g = u^2 mod p.
    Because p = 2q + 1 is a safe prime, all quadratic residues in Z_p* generate G_q.
    This provably eliminates small-subgroup confinement attacks (g^q == 1 mod p).
    """
    import hmac

    if isinstance(identifier, int):
        raw = identifier.to_bytes((identifier.bit_length() + 7) // 8 or 1, "big")
    elif isinstance(identifier, bytes):
        raw = identifier
    else:
        raw = str(identifier).strip().encode("utf-8")

    digest = hmac.new(PSI_DOMAIN_SEPARATOR, raw, hashlib.sha512).digest()
    u = int.from_bytes(digest, "big") % (prime - 3) + 2
    # Squaring modulo p guarantees u^2 is a quadratic residue in G_q
    g = pow(u, 2, prime)
    if g <= 1 or g >= prime - 1:
        # Fallback to standard quadratic residue generator
        g = pow(3, 2, prime)
    return g


def validate_group_element(
    element_int: int,
    prime: int = PSI_PRIME,
    order_q: int = PSI_ORDER_Q,
) -> bool:
    """Validate that element belongs to cyclic prime-order subgroup G_q (order q).

    Verifies:
    1. Canonical range: 1 < element_int < prime - 1
    2. Euler quadratic residue / prime-order condition: element_int^q == 1 mod p
    """
    if element_int <= 1 or element_int >= prime - 1:
        return False
    return pow(element_int, order_q, prime) == 1


def derive_risk_payload_key(element_int: int, nonce: str) -> bytes:
    """Derive a 256-bit symmetric encryption key from a matching group element and session nonce."""
    import hmac

    elem_bytes = element_int.to_bytes((PRIME_BIT_LENGTH + 7) // 8, "big")
    salt = nonce.encode("utf-8")
    info = b"CFI-PSI-RISK-PAYLOAD-AESGCM-KEY"
    prk = hmac.new(salt, elem_bytes, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()


def encrypt_risk_payload(risk_context: dict[str, Any], key: bytes) -> str:
    """Encrypt entity risk context using AES-256-GCM keyed to its specific blinded element."""
    import json
    import secrets

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aesgcm = AESGCM(key)
    iv = secrets.token_bytes(12)
    plaintext = json.dumps(risk_context, sort_keys=True).encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, plaintext, None)
    return f"{iv.hex()}:{ciphertext.hex()}"


def decrypt_risk_payload(encrypted_payload: str, key: bytes) -> dict[str, Any] | None:
    """Attempt decryption of entity risk context using derived key. Returns None on tag mismatch."""
    import json

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        parts = encrypted_payload.split(":")
        if len(parts) != 2:
            return None
        iv = bytes.fromhex(parts[0])
        ciphertext = bytes.fromhex(parts[1])
        aesgcm = AESGCM(key)
        decrypted = aesgcm.decrypt(iv, ciphertext, None)
        return json.loads(decrypted.decode("utf-8"))
    except (InvalidTag, ValueError, Exception):
        return None


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
            double_blind_element(elem, second_exponent, self.prime) for elem in blinded_elements
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
    "PSI_ORDER_Q",
    "PSI_DOMAIN_SEPARATOR",
    "hash_to_group",
    "hash_to_prime_subgroup",
    "validate_group_element",
    "derive_risk_payload_key",
    "encrypt_risk_payload",
    "decrypt_risk_payload",
    "parse_blinded_element",
    "blind_element",
    "double_blind_element",
    "verify_commutativity",
    "extract_fuzzy_features",
    "_extract_fuzzy_features",
    "DiffieHellmanPSIProtocol",
]
