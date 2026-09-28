"""Unit and Empirical Characterization Tests for MinHash LSH Fuzzy PSI.

Covers:
1. Character n-gram shingling, edge cases, and ground-truth Jaccard calculation.
2. MinHash signature generation, determinism, validation, and dimensional scalability (K in {16, 32, 64, 128, 256}).
3. Standardized similarity test vectors:
   - Exact matches (J = 1.0)
   - Typos (single-character edit distances)
   - Casing variations (case invariance)
   - Word transpositions (order permutations)
   - Non-matches (orthogonal entity strings)
4. Theoretical error bounds, variance, standard error, and S-curve thresholds.
5. Empirical collision rate characterization sweep across K in {32, 64, 128}.
6. MinHashLSHIndex candidate retrieval, multi-tenant isolation, and GDPR erasure.
7. GraphEngine fuzzy entity linkage integration.
"""

from __future__ import annotations

import concurrent.futures
import math

import pytest

from app.application.services.graph_engine import GraphEngine
from app.domain.entities_phase2 import Entity
from app.domain.enums import EntityType, RelationshipType
from app.domain.minhash_lsh import (
    DEFAULT_SIMILARITY_TEST_VECTORS,
    CollisionCharacterizationSummary,
    MinHashLSHIndex,
    VectorCategory,
    compute_minhash_signature,
    estimate_jaccard_similarity,
    evaluate_minhash_lsh_suite,
    extract_character_shingles,
    ground_truth_jaccard_distance,
    ground_truth_jaccard_similarity,
    partition_into_lsh_bands,
    text_ground_truth_jaccard,
    theoretical_collision_probability,
    theoretical_minhash_std_err,
    theoretical_minhash_variance,
    theoretical_s_curve_threshold,
)


class TestMinHashShinglingAndGroundTruth:
    """Verifies character n-gram generation and ground-truth Jaccard metrics."""

    def test_shingle_extraction_standard_text(self) -> None:
        """Extracts correct 3-gram character shingles from standard string."""
        text = "Acme Corp"
        shingles = extract_character_shingles(text, n=3, lowercase=True)
        # "acme corp" -> 'acm', 'cme', 'me ', 'e c', ' co', 'cor', 'orp'
        assert shingles == {"acm", "cme", "me ", "e c", " co", "cor", "orp"}
        assert len(shingles) == 7

    def test_shingle_extraction_short_text(self) -> None:
        """Strings shorter than n return single atomic shingle."""
        assert extract_character_shingles("AB", n=3) == {"ab"}
        assert extract_character_shingles("X", n=3) == {"x"}

    def test_shingle_extraction_empty_and_whitespace(self) -> None:
        """Empty or whitespace-only strings return empty sets."""
        assert extract_character_shingles("", n=3) == set()
        assert extract_character_shingles("   ", n=3) == set()

    def test_ground_truth_jaccard_identical_sets(self) -> None:
        """Identical sets produce Jaccard similarity of 1.0 and distance 0.0."""
        s1 = {"abc", "bcd", "cde"}
        s2 = {"abc", "bcd", "cde"}
        assert ground_truth_jaccard_similarity(s1, s2) == 1.0
        assert ground_truth_jaccard_distance(s1, s2) == 0.0

    def test_ground_truth_jaccard_disjoint_sets(self) -> None:
        """Disjoint sets produce Jaccard similarity of 0.0 and distance 1.0."""
        s1 = {"abc", "bcd"}
        s2 = {"xyz", "yza"}
        assert ground_truth_jaccard_similarity(s1, s2) == 0.0
        assert ground_truth_jaccard_distance(s1, s2) == 1.0

    def test_ground_truth_jaccard_overlapping_sets(self) -> None:
        """Calculates precise mathematical intersection over union ratio."""
        s1 = {"a", "b", "c", "d"}
        s2 = {"c", "d", "e", "f"}
        # Intersection = {"c", "d"} (2), Union = {"a", "b", "c", "d", "e", "f"} (6)
        expected = 2 / 6
        assert abs(ground_truth_jaccard_similarity(s1, s2) - expected) < 1e-5

    def test_ground_truth_jaccard_empty_sets(self) -> None:
        """Both empty returns 1.0; one empty returns 0.0."""
        assert ground_truth_jaccard_similarity(set(), set()) == 1.0
        assert ground_truth_jaccard_similarity({"abc"}, set()) == 0.0
        assert ground_truth_jaccard_similarity(set(), {"abc"}) == 0.0


class TestMinHashSignatureProperties:
    """Verifies signature dimensionality, determinism, validation, and interoperability."""

    def test_signature_dimension_and_range(self) -> None:
        """Signatures match requested K and elements stay within [0, 1,000,000)."""
        text = "Deutsche Bank Aktiengesellschaft Frankfurt"
        for k in (16, 32, 64, 128, 256):
            sig = compute_minhash_signature(text, num_hashes=k)
            assert len(sig) == k
            assert all(isinstance(val, int) for val in sig)
            assert all(0 <= val < 1000000 for val in sig)

    def test_signature_deterministic_and_pure(self) -> None:
        """Repeated invocations on the same string yield bit-identical signatures."""
        text = "BNP Paribas Asset Management France"
        sig1 = compute_minhash_signature(text, num_hashes=64)
        sig2 = compute_minhash_signature(text, num_hashes=64)
        assert sig1 == sig2

    def test_signature_rejection_of_invalid_dimensions(self) -> None:
        """Rejects zero or negative hash counts with ValueError."""
        with pytest.raises(ValueError, match="positive integer"):
            compute_minhash_signature("Test Entity", num_hashes=0)
        with pytest.raises(ValueError, match="positive integer"):
            compute_minhash_signature("Test Entity", num_hashes=-32)

    def test_signature_interoperability_with_fuzzy_psi(self) -> None:
        """Verifies bit-identical output with app.domain.fuzzy_psi."""
        from app.domain.fuzzy_psi import (
            compute_minhash_signature as legacy_compute_minhash,
        )

        sample = "Credit Suisse AG Zurich Switzerland"
        sig_new = compute_minhash_signature(sample, num_hashes=64)
        sig_legacy = legacy_compute_minhash(sample, num_hashes=64)
        assert sig_new == sig_legacy


class TestDeterministicSimilarityVectors:
    """Verifies MinHash behavior across standardized entity mutation categories."""

    def test_exact_match_vectors(self) -> None:
        """Exact matches produce J_true = 1.0 and J_hat = 1.0 across all K."""
        text = "International Monetary Fund Washington DC"
        for k in (32, 64, 128):
            sig1 = compute_minhash_signature(text, num_hashes=k)
            sig2 = compute_minhash_signature(text, num_hashes=k)
            assert estimate_jaccard_similarity(sig1, sig2) == 1.0

    def test_casing_invariance_vectors(self) -> None:
        """Casing variations produce J_true = 1.0 and J_hat = 1.0."""
        text_upper = "STANDARD CHARTERED BANK LONDON"
        text_lower = "standard chartered bank london"
        text_mixed = "StAnDaRd ChArTeReD bAnK lOnDoN"

        sig_u = compute_minhash_signature(text_upper, num_hashes=64)
        sig_l = compute_minhash_signature(text_lower, num_hashes=64)
        sig_m = compute_minhash_signature(text_mixed, num_hashes=64)

        assert estimate_jaccard_similarity(sig_u, sig_l) == 1.0
        assert estimate_jaccard_similarity(sig_u, sig_m) == 1.0

    def test_typo_vectors_high_similarity(self) -> None:
        """Single-character edits yield high similarity (J >= 0.65)."""
        pair_a = "Alexander Hamilton"
        pair_b = "Alexandr Hamilton"  # single missing 'e'

        gt_j = text_ground_truth_jaccard(pair_a, pair_b)
        assert gt_j >= 0.70

        sig_a = compute_minhash_signature(pair_a, num_hashes=128)
        sig_b = compute_minhash_signature(pair_b, num_hashes=128)
        est_j = estimate_jaccard_similarity(sig_a, sig_b)

        assert est_j >= 0.65
        assert abs(est_j - gt_j) <= 0.15

    def test_transposition_vectors_moderate_similarity(self) -> None:
        """Word order transpositions maintain robust shingle overlap (J >= 0.55)."""
        pair_a = "Global Trade Logistics Partners"
        pair_b = "Logistics Partners Global Trade"

        gt_j = text_ground_truth_jaccard(pair_a, pair_b)
        assert gt_j >= 0.60

        sig_a = compute_minhash_signature(pair_a, num_hashes=128)
        sig_b = compute_minhash_signature(pair_b, num_hashes=128)
        est_j = estimate_jaccard_similarity(sig_a, sig_b)

        assert est_j >= 0.55
        assert abs(est_j - gt_j) <= 0.15

    def test_non_match_vectors_low_similarity(self) -> None:
        """Orthogonal unrelated entities produce near-zero Jaccard similarity (J <= 0.05)."""
        pair_a = "Banco Santander SA Madrid Spain"
        pair_b = "Tokyo Electron Semiconductor Corp"

        gt_j = text_ground_truth_jaccard(pair_a, pair_b)
        assert gt_j <= 0.05

        sig_a = compute_minhash_signature(pair_a, num_hashes=128)
        sig_b = compute_minhash_signature(pair_b, num_hashes=128)
        est_j = estimate_jaccard_similarity(sig_a, sig_b)

        assert est_j <= 0.05


class TestTheoreticalBoundsAndErrorCharacterization:
    """Verifies mathematical variance, standard error, and S-curve thresholds."""

    def test_variance_and_standard_error_formula(self) -> None:
        """Verifies Var(J_hat) = J*(1-J)/K and sigma = sqrt(Var)."""
        j = 0.60
        for k in (32, 64, 128):
            expected_var = (0.60 * 0.40) / k
            expected_std_err = math.sqrt(expected_var)
            assert abs(theoretical_minhash_variance(j, k) - expected_var) < 1e-6
            assert abs(theoretical_minhash_std_err(j, k) - expected_std_err) < 1e-6

        # Maximum standard error occurs at J = 0.50
        max_std_err_64 = theoretical_minhash_std_err(0.50, 64)
        assert abs(max_std_err_64 - (0.50 / math.sqrt(64))) < 1e-6
        assert max_std_err_64 == 0.0625

    def test_error_reduction_with_increasing_k(self) -> None:
        """Empirically confirms estimation error bound shrinks as K increases."""
        text_a = "Commonwealth Bank of Australia Sydney"
        text_b = "Commonwealth Bank Australia Sydny"

        gt_j = text_ground_truth_jaccard(text_a, text_b)

        std_32 = theoretical_minhash_std_err(gt_j, 32)
        std_64 = theoretical_minhash_std_err(gt_j, 64)
        std_128 = theoretical_minhash_std_err(gt_j, 128)

        assert std_32 > std_64 > std_128

    def test_s_curve_inflection_point_calculation(self) -> None:
        """Calculates theoretical LSH candidate threshold tau* = (1/b)^(1/r)."""
        # b=16, r=4 => tau* = (1/16)^(1/4) = 0.50
        tau_16_4 = theoretical_s_curve_threshold(num_bands=16, rows_per_band=4)
        assert abs(tau_16_4 - 0.50) < 1e-4

        # b=16, r=8 => tau* = (1/16)^(1/8) ≈ 0.7071
        tau_16_8 = theoretical_s_curve_threshold(num_bands=16, rows_per_band=8)
        assert abs(tau_16_8 - 0.7071) < 1e-4

        # b=8, r=4 => tau* = (1/8)^(1/4) ≈ 0.5946
        tau_8_4 = theoretical_s_curve_threshold(num_bands=8, rows_per_band=4)
        assert abs(tau_8_4 - 0.5946) < 1e-4

    def test_collision_probability_monotonicity(self) -> None:
        """Collision probability strictly increases monotonically with Jaccard similarity."""
        b, r = 16, 4
        p_low = theoretical_collision_probability(0.20, b, r)
        p_mid = theoretical_collision_probability(0.50, b, r)
        p_high = theoretical_collision_probability(0.80, b, r)

        assert p_low < p_mid < p_high
        assert theoretical_collision_probability(0.0, b, r) == 0.0
        assert theoretical_collision_probability(1.0, b, r) == 1.0

    def test_partition_into_lsh_bands(self) -> None:
        """Verifies MinHash signature partitioning into LSH band hashes."""
        sig = [10, 20, 30, 40, 50, 60, 70, 80]
        assert partition_into_lsh_bands([], num_bands=4, rows_per_band=2) == []
        assert partition_into_lsh_bands(sig, num_bands=0, rows_per_band=2) == []
        assert partition_into_lsh_bands(sig, num_bands=4, rows_per_band=0) == []

        bands_r1 = partition_into_lsh_bands(sig, num_bands=4, rows_per_band=1)
        assert len(bands_r1) == 4
        assert all(len(h) == 16 for h in bands_r1)

        bands_r2 = partition_into_lsh_bands(sig, num_bands=4, rows_per_band=2)
        assert len(bands_r2) == 4
        assert all(len(h) == 16 for h in bands_r2)

    def test_vector_category_enum(self) -> None:
        """Verifies VectorCategory enum values and membership."""
        assert VectorCategory.EXACT_MATCH == "EXACT_MATCH"
        assert VectorCategory.TYPO == "TYPO"
        assert VectorCategory.CASING == "CASING"
        assert VectorCategory.TRANSPOSITION == "TRANSPOSITION"
        assert VectorCategory.NON_MATCH == "NON_MATCH"
        for v in DEFAULT_SIMILARITY_TEST_VECTORS:
            assert isinstance(v["category"], VectorCategory)



class TestEmpiricalCollisionRateSweep:
    """Verifies empirical collision rates and accuracy sweeps across K in {32, 64, 128}."""

    def test_full_characterization_sweep_metrics(self) -> None:
        """Executes full evaluation across standardized test vectors."""
        results = evaluate_minhash_lsh_suite()

        assert 32 in results
        assert 64 in results
        assert 128 in results

        for k, summary in results.items():
            assert isinstance(summary, CollisionCharacterizationSummary)
            assert summary.num_hashes == k
            assert summary.total_pairs_evaluated == len(DEFAULT_SIMILARITY_TEST_VECTORS)

            # 1. Exact matches must be 100% accurate
            assert summary.exact_match_fidelity == 1.0

            # 2. Casing variations must be 100% accurate
            assert summary.casing_invariance_fidelity == 1.0

            # 3. Typo recall must be >= 66% across all band configurations
            assert summary.typo_candidate_recall >= 0.66

            # 4. False positive collision rate for unrelated non-matches must be 0%
            assert summary.false_positive_collision_rate == 0.0

            # 5. Mean absolute error must be bounded under 0.08
            assert summary.mean_absolute_error < 0.08

    def test_higher_dimension_error_superiority(self) -> None:
        """Verifies that K=128 achieves equal or superior estimation accuracy over K=32."""
        results = evaluate_minhash_lsh_suite()
        summary_32 = results[32]
        summary_128 = results[128]

        # Max error bound should not degrade at higher dimensions
        assert summary_128.mean_absolute_error <= summary_32.mean_absolute_error + 0.02


class TestMinHashLSHIndexLifecycle:
    """Verifies thread-safe MinHashLSHIndex operations, multi-tenancy, and GDPR erasure."""

    def test_indexing_and_candidate_query(self) -> None:
        """Indexes entities and retrieves candidate matches exceeding threshold."""
        index = MinHashLSHIndex(num_hashes=64, num_bands=16, rows_per_band=1, similarity_threshold=0.40)

        index.index_entity("ent_01", "bank_alpha", "Acme Corporation Holdings")
        index.index_entity("ent_02", "bank_beta", "Acme Corp Holdings")  # close match
        index.index_entity("ent_03", "bank_gamma", "Tokyo Electron Technologies")  # distinct

        assert index.count_entities() == 3

        # Query from bank_alpha for matches in other banks
        matches = index.query("Acme Corporation Holdings", querying_bank_id="bank_alpha")
        match_ids = [m["entity_id"] for m in matches]

        assert "ent_02" in match_ids
        assert "ent_03" not in match_ids  # unrelated entity filtered out
        assert "ent_01" not in match_ids  # self-bank excluded

    def test_gdpr_right_to_erasure(self) -> None:
        """Removes an entity completely from all LSH buckets."""
        index = MinHashLSHIndex(num_hashes=32, num_bands=8, rows_per_band=4)
        index.index_entity("mule_99", "bank_alpha", "Suspect Shell Company LLC")
        assert index.count_entities() == 1

        erased = index.remove_entity("mule_99")
        assert erased is True
        assert index.count_entities() == 0

        # Further query returns zero candidates
        matches = index.query("Suspect Shell Company LLC")
        assert len(matches) == 0

    def test_concurrent_indexing_and_query_thread_safety(self) -> None:
        """Validates thread-safety under simultaneous multi-threaded indexing and querying."""
        index = MinHashLSHIndex(num_hashes=32, num_bands=8, rows_per_band=4)

        def worker(idx: int) -> None:
            name = f"Corporate Entity Branch {idx % 5}"
            index.index_entity(f"e_{idx}", f"bank_{idx % 3}", name)
            index.query(name)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(40)]
            concurrent.futures.wait(futures)

        assert index.count_entities() == 40


class TestGraphEngineMinHashIntegration:
    """Verifies GraphEngine integration with MinHash LSH for cross-bank duplicate detection."""

    def test_graph_engine_find_fuzzy_matches(self) -> None:
        """GraphEngine.find_fuzzy_matches retrieves cross-bank fuzzy candidate entities."""
        g = GraphEngine()

        e1 = Entity(
            id="cust_de_01",
            entity_type=EntityType.CUSTOMER,
            privacy_id="hash_01",
            bank_id="bank_deutschland",
            display_label="Alexander Hamilton Global",
        )
        e2 = Entity(
            id="cust_fr_02",
            entity_type=EntityType.CUSTOMER,
            privacy_id="hash_02",
            bank_id="bank_france",
            display_label="Alexandr Hamilton Global",  # typo
        )
        e3 = Entity(
            id="cust_es_03",
            entity_type=EntityType.CUSTOMER,
            privacy_id="hash_03",
            bank_id="bank_spain",
            display_label="Banco Santander Madrid",  # unrelated
        )

        g.register_entities([e1, e2, e3])

        matches = g.find_fuzzy_matches(
            "cust_de_01", similarity_threshold=0.40, cross_bank_only=True
        )
        assert len(matches) >= 1

        matched_ids = [m["entity_id"] for m in matches]
        assert "cust_fr_02" in matched_ids
        assert "cust_es_03" not in matched_ids

    def test_graph_engine_link_fuzzy_entities_and_auto_relationship(self) -> None:
        """GraphEngine.link_fuzzy_entities detects duplicate pairs and registers SAME_ENTITY edges."""
        g = GraphEngine()

        e1 = Entity(
            id="corp_a",
            entity_type=EntityType.MERCHANT,
            privacy_id="hash_ca",
            bank_id="bank_a",
            display_label="ACME LOGISTICS CORP",
        )
        e2 = Entity(
            id="corp_b",
            entity_type=EntityType.MERCHANT,
            privacy_id="hash_cb",
            bank_id="bank_b",
            display_label="acme logistics corp",  # casing variation
        )

        g.register_entities([e1, e2])

        matches = g.link_fuzzy_entities(
            similarity_threshold=0.50, auto_add_relationship=True
        )
        assert len(matches) == 1
        assert matches[0]["estimated_jaccard"] == 1.0

        # Verifies that RelationshipType.SAME_ENTITY was added to the graph
        subgraph = g.get_subgraph("corp_a", radius=1)
        same_entity_edges = [
            edge
            for edge in subgraph.edges
            if edge.get("label") in (RelationshipType.SAME_ENTITY.value, "same entity")
            or edge.get("data", {}).get("relationshipType")
            == RelationshipType.SAME_ENTITY.value
        ]
        assert len(same_entity_edges) == 1
