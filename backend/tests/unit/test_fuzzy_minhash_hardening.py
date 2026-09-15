"""Unit tests for hardened Fuzzy MinHash LSH Entity Resolution (Phase 50 / Stage 31).

Verifies:
1. MinHash signature generation up to K=128 dimensions and validation of positive hash count.
2. Jaccard similarity bounds, clamping [0.0, 1.0], and mismatched vector length guards.
3. LSH multi-row banding (b x r = K) and single-element high-recall partitioning.
4. FuzzyPSIMatcher candidate retrieval and cross-bank candidate pruning.
5. FuzzyPSIMatcher GDPR Art. 17 right-to-erasure entity removal.
6. EntityResolutionService LSH integration during entity creation.
7. EntityResolutionService multi-tenant bank_id scoping.
8. EntityResolutionService delete_entity GDPR cascade cleanup.
9. EntityFuzzyResolveRequest schema alias normalization and input sanitization.
10. Concurrency safety under multi-threaded indexing, matching, and erasure.
"""

from __future__ import annotations

import concurrent.futures

import pytest

from app.application.schemas.phase2 import EntityFuzzyResolveRequest
from app.application.services.entity_resolution import EntityResolutionService
from app.domain.enums import EntityType
from app.domain.fuzzy_psi import (
    FuzzyPSIMatcher,
    calculate_jaccard_similarity,
    compute_minhash_signature,
    lsh_band_buckets,
)


def test_minhash_k128_dimension_and_positive_validation() -> None:
    """Verifies K=128 dimension generation and rejection of non-positive hash counts."""
    name = "Alexander Hamilton Financial Services"
    sig_128 = compute_minhash_signature(name, num_hashes=128)

    assert len(sig_128) == 128
    assert all(isinstance(x, int) for x in sig_128)
    assert all(0 <= x < 1000000 for x in sig_128)

    # Empty string returns vector of zeros
    empty_sig = compute_minhash_signature("", num_hashes=128)
    assert len(empty_sig) == 128
    assert empty_sig == [0] * 128

    # Rejection of invalid hash counts
    with pytest.raises(ValueError, match="positive integer"):
        compute_minhash_signature(name, num_hashes=0)

    with pytest.raises(ValueError, match="positive integer"):
        compute_minhash_signature(name, num_hashes=-16)


def test_jaccard_similarity_bounds_and_clamping() -> None:
    """Verifies Jaccard similarity edge conditions, clamping, and dimension mismatch guards."""
    sig1 = [100, 200, 300, 400]
    sig2 = [100, 200, 300, 400]
    sig3 = [500, 600, 700, 800]
    sig4 = [100, 200, 999, 888]

    assert calculate_jaccard_similarity(sig1, sig2) == 1.0
    assert calculate_jaccard_similarity(sig1, sig3) == 0.0
    assert calculate_jaccard_similarity(sig1, sig4) == 0.5

    # Dimension mismatch returns 0.0 safely
    assert calculate_jaccard_similarity(sig1, [100, 200]) == 0.0
    assert calculate_jaccard_similarity([], sig1) == 0.0
    assert calculate_jaccard_similarity(sig1, []) == 0.0


def test_lsh_multi_row_banding_and_exact_partitions() -> None:
    """Verifies single-element high-recall and multi-row LSH band chunking."""
    sig = compute_minhash_signature("yusuf calisir", num_hashes=128)

    # Single-element bands (rows_per_band = 1)
    bands_single = lsh_band_buckets(sig, num_bands=16, rows_per_band=1)
    assert len(bands_single) == 16
    assert all(len(b) == 16 for b in bands_single)

    # Multi-row bands (b=16, r=8 for K=128)
    bands_multi = lsh_band_buckets(sig, num_bands=16, rows_per_band=8)
    assert len(bands_multi) == 16
    assert all(len(b) == 16 for b in bands_multi)

    # Invalid configurations fail-safe to empty list
    assert lsh_band_buckets([], num_bands=16) == []
    assert lsh_band_buckets(sig, num_bands=0) == []
    assert lsh_band_buckets(sig, num_bands=16, rows_per_band=0) == []


def test_fuzzy_psi_matcher_candidate_retrieval() -> None:
    """Verifies FuzzyPSIMatcher candidate index retrieval and similarity thresholding."""
    matcher = FuzzyPSIMatcher(num_hashes=64, num_bands=16, similarity_threshold=0.3)

    matcher.index_entity("cust_01", "bank_alpha", "yusuf calisir")
    matcher.index_entity("cust_02", "bank_alpha", "mehmet yilmaz")
    matcher.index_entity("cust_03", "bank_beta", "yusuf calisr")  # typo

    assert matcher.get_indexed_entity_count() == 3

    # Direct candidate ID lookup
    candidates = matcher.get_candidate_ids_for_text("yusuf calisir")
    assert "cust_01" in candidates
    assert "cust_03" in candidates
    assert "cust_02" not in candidates

    # Cross-bank match profile
    matches = matcher.match_profile("cust_test", "bank_gamma", "yusuf calisir")
    matched_ids = [m.entity_id_b for m in matches]
    assert "cust_01" in matched_ids
    assert "cust_03" in matched_ids
    assert "cust_02" not in matched_ids


def test_fuzzy_psi_matcher_removal_and_gdpr() -> None:
    """Verifies that remove_entity cleans up LSH buckets and purges references."""
    matcher = FuzzyPSIMatcher(num_hashes=16, num_bands=8)
    matcher.index_entity("target_cust", "bank_alpha", "johnathan doe")

    assert matcher.get_indexed_entity_count() == 1
    candidates_before = matcher.get_candidate_ids_for_text("johnathan doe")
    assert "target_cust" in candidates_before

    # Remove entity
    removed = matcher.remove_entity("target_cust")
    assert removed is True
    assert matcher.get_indexed_entity_count() == 0

    candidates_after = matcher.get_candidate_ids_for_text("johnathan doe")
    assert "target_cust" not in candidates_after

    # Second removal returns False
    assert matcher.remove_entity("target_cust") is False


def test_entity_resolution_service_lsh_indexing_and_candidate_pruning() -> None:
    """Verifies EntityResolutionService automatically indexes entities in LSH and resolves matches."""
    service = EntityResolutionService(num_hashes=32, num_bands=16)
    service.clear_all()

    ent1 = service.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier="yusuf calisir",
        bank_id="bank_alpha",
    )
    ent2 = service.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier="yusuf calisr",
        bank_id="bank_beta",
    )
    ent3 = service.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier="robert johnson",
        bank_id="bank_gamma",
    )

    # Resolve fuzzy match
    matches = service.resolve_fuzzy_entities("yusuf calisir", threshold=0.3)
    matched_ids = [m["entity"].id for m in matches]

    assert ent1.id in matched_ids
    assert ent2.id in matched_ids
    assert ent3.id not in matched_ids


def test_entity_resolution_service_bank_id_scoping() -> None:
    """Verifies bank_id parameter restricts candidate matching to the specified bank tenant."""
    service = EntityResolutionService(num_hashes=16, num_bands=8)
    service.clear_all()

    service.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier="alice cooper",
        bank_id="bank_alpha",
    )
    service.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier="alice cooper",
        bank_id="bank_beta",
    )

    # Without bank_id filter, matches both banks
    matches_all = service.resolve_fuzzy_entities("alice cooper", threshold=0.5)
    assert len(matches_all) == 2

    # With bank_id filter, only returns the specified bank
    matches_alpha = service.resolve_fuzzy_entities(
        "alice cooper", threshold=0.5, bank_id="bank_alpha"
    )
    assert len(matches_alpha) == 1
    assert matches_alpha[0]["entity"].bank_id == "bank_alpha"


def test_entity_resolution_service_gdpr_erasure() -> None:
    """Verifies delete_entity cascades across primary store, hash index, and LSH matcher."""
    service = EntityResolutionService()
    service.clear_all()

    ent = service.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier="elizabeth warren",
        bank_id="bank_alpha",
    )
    assert service.get_entity(ent.id) is not None

    # Verify present in fuzzy matches
    matches_before = service.resolve_fuzzy_entities("elizabeth warren", threshold=0.5)
    assert any(m["entity"].id == ent.id for m in matches_before)

    # Perform GDPR deletion
    deleted = service.delete_entity(ent.id)
    assert deleted is True

    # Assert gone from entity store
    assert service.get_entity(ent.id) is None

    # Assert gone from fuzzy resolution
    matches_after = service.resolve_fuzzy_entities("elizabeth warren", threshold=0.5)
    assert not any(m["entity"].id == ent.id for m in matches_after)


def test_fuzzy_resolve_schema_alias_and_normalization() -> None:
    """Verifies EntityFuzzyResolveRequest alias normalization and boundary validation."""
    # Using alias raw_identifier and similarity_threshold
    req = EntityFuzzyResolveRequest.model_validate(
        {
            "raw_identifier": "  Acme Corp \x00\x08  ",
            "similarity_threshold": 0.65,
            "entity_type": "merchant",
            "bank_id": "bank_alpha",
            "limit": 25,
        }
    )

    assert req.query_name == "Acme Corp"
    assert req.threshold == 0.65
    assert req.entity_type == "merchant"
    assert req.bank_id == "bank_alpha"
    assert req.limit == 25


def test_fuzzy_psi_concurrent_indexing_and_erasure() -> None:
    """Verifies thread safety during high-concurrency indexing, querying, and deletion."""
    matcher = FuzzyPSIMatcher(num_hashes=16, num_bands=8)

    def worker(idx: int) -> None:
        entity_id = f"ent_{idx}"
        bank_id = f"bank_{idx % 4}"
        name = f"User Test Name {idx}"

        # Index
        matcher.index_entity(entity_id, bank_id, name)

        # Lookup
        candidates = matcher.get_candidate_ids_for_text(name)
        assert entity_id in candidates

        # Delete odd indexed entities
        if idx % 2 == 1:
            matcher.remove_entity(entity_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(50)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # 25 even-indexed entities should remain
    assert matcher.get_indexed_entity_count() == 25
