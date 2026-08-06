"""Unit tests for DH-PSI and MinHash LSH Fuzzy PSI Domain Modules (Section 5.3)."""

from __future__ import annotations

import hashlib
import hmac

from app.domain.fuzzy_psi import (
    FuzzyPSIMatcher,
    calculate_jaccard_similarity,
    compute_minhash_signature,
    lsh_band_buckets,
)
from app.domain.psi_service import PRIME_BIT_LENGTH, PSI_PRIME


def test_dh_psi_commutative_property() -> None:
    """Verifies commutative Diffie-Hellman exponentiation: (H(x)^a)^b mod P == (H(x)^b)^a mod P."""
    element = "suspicious_account_12345"
    h_x = int(hashlib.sha256(element.encode("utf-8")).hexdigest(), 16)

    key_a = 0x1A2B3C4D5E6F7A8B9C0D1E2F3A4B5C6D7E8F9A0B
    key_b = 0x9F8E7D6C5B4A3F2E1D0C9B8A7F6E5D4C3B2A1F0E

    # Bank A encrypts first, then Bank B
    enc_a = pow(h_x, key_a, PSI_PRIME)
    double_enc_a = pow(enc_a, key_b, PSI_PRIME)

    # Bank B encrypts first, then Bank A
    enc_b = pow(h_x, key_b, PSI_PRIME)
    double_enc_b = pow(enc_b, key_a, PSI_PRIME)

    assert double_enc_a == double_enc_b, "DH-PSI commutative exponentiation equality failed!"
    assert PRIME_BIT_LENGTH == 2048


def test_minhash_lsh_fuzzy_matching() -> None:
    """Verifies character 3-gram MinHash signatures and LSH band bucket collision detection."""
    name_a = "yusuf calisir"
    name_b = "yusuf calisr"  # minor typo
    name_c = "jane smith"  # distinct entity

    sig_a = compute_minhash_signature(name_a, num_hashes=16)
    sig_b = compute_minhash_signature(name_b, num_hashes=16)
    sig_c = compute_minhash_signature(name_c, num_hashes=16)

    assert len(sig_a) == 16
    sim_ab = calculate_jaccard_similarity(sig_a, sig_b)
    sim_ac = calculate_jaccard_similarity(sig_a, sig_c)

    assert sim_ab > 0.3, f"Expected high similarity for typo match, got {sim_ab}"
    assert sim_ac < 0.2, f"Expected low similarity for distinct entity, got {sim_ac}"

    bands_a = lsh_band_buckets(sig_a, num_bands=4)
    bands_b = lsh_band_buckets(sig_b, num_bands=4)
    assert len(bands_a) == 4
    # At least one band bucket should collide for near-duplicate entities
    band_collisions = set(bands_a).intersection(set(bands_b))
    assert len(band_collisions) >= 1


def test_fuzzy_psi_matcher_indexing_and_matching() -> None:
    """Verifies FuzzyPSIMatcher indexing and cross-bank profile matching."""
    matcher = FuzzyPSIMatcher(num_hashes=16, num_bands=4, similarity_threshold=0.25)

    matcher.index_entity("ent_101", "bank_a", "yusuf calisir")
    matches = matcher.match_profile("ent_202", "bank_b", "yusuf calisr")

    assert len(matches) > 0
    best_match = matches[0]
    assert best_match.entity_id_b == "ent_101"
    assert best_match.is_match is True


def test_deterministic_hmac_identification_zero_pii() -> None:
    """Verifies tenant-salted HMAC tokenization consistency and Zero Raw PII policy."""
    raw_account = "TR990006200000000123456789"
    tenant_salt = "consortium_salt_xyz"

    token1 = hmac.new(
        tenant_salt.encode("utf-8"), raw_account.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    token2 = hmac.new(
        tenant_salt.encode("utf-8"), raw_account.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    assert token1 == token2
    assert raw_account not in token1
    assert len(token1) == 64
