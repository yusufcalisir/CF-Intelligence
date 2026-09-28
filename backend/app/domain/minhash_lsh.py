"""MinHash Locality-Sensitive Hashing (LSH) Domain Module.

Enables privacy-preserving fuzzy entity matching and set intersection across
financial institutions without plaintext PII exposure.

Implements:
1. Character n-gram shingling (trigrams by default).
2. Ground-truth Jaccard similarity and distance metrics.
3. K-dimensional deterministic MinHash signatures (K in {16, 32, 64, 128, 256}).
4. Theoretical LSH banding, S-curve inflection modeling, and collision probability.
5. Standardized test vectors (Exact Match, Typo, Casing, Transposition, Non-Match).
6. Empirical collision rate characterization and error quantification across K.
"""

from __future__ import annotations

import hashlib
import math
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class VectorCategory(StrEnum):
    """Classification of string similarity test vectors."""

    EXACT_MATCH = "EXACT_MATCH"
    TYPO = "TYPO"
    CASING = "CASING"
    TRANSPOSITION = "TRANSPOSITION"
    NON_MATCH = "NON_MATCH"


def extract_character_shingles(
    text: str, n: int = 3, lowercase: bool = True, strip: bool = True
) -> set[str]:
    """Extracts character n-gram shingles from input string.

    Args:
        text: Raw input string (e.g. entity name, address, tax descriptor).
        n: Shingle length (default 3 for character trigrams).
        lowercase: Whether to convert text to lowercase before shingling.
        strip: Whether to strip leading and trailing whitespace.

    Returns:
        Set of unique character n-gram substrings.
    """
    if not text:
        return set()

    clean = text.strip() if strip else text
    if lowercase:
        clean = clean.lower()

    if not clean:
        return set()

    if len(clean) < n:
        return {clean}

    return {clean[i : i + n] for i in range(len(clean) - n + 1)}


def ground_truth_jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Computes exact ground-truth Jaccard similarity between two shingle sets.

    J(A, B) = |A ∩ B| / |A ∪ B|

    Args:
        set_a: First shingle set.
        set_b: Second shingle set.

    Returns:
        Exact Jaccard similarity in [0.0, 1.0].
    """
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0

    intersection_len = len(set_a & set_b)
    union_len = len(set_a | set_b)
    if union_len == 0:
        return 1.0

    return round(intersection_len / union_len, 6)


def ground_truth_jaccard_distance(set_a: set[str], set_b: set[str]) -> float:
    """Computes exact ground-truth Jaccard distance between two shingle sets.

    D_J(A, B) = 1.0 - J(A, B)
    """
    return round(1.0 - ground_truth_jaccard_similarity(set_a, set_b), 6)


def text_ground_truth_jaccard(text_a: str, text_b: str, n: int = 3) -> float:
    """Convenience helper computing exact 3-gram Jaccard similarity between two strings."""
    shingles_a = extract_character_shingles(text_a, n=n)
    shingles_b = extract_character_shingles(text_b, n=n)
    return ground_truth_jaccard_similarity(shingles_a, shingles_b)


def compute_minhash_signature(
    text: str, num_hashes: int = 64, n: int = 3
) -> list[int]:
    """Computes K-dimensional MinHash signature vector for input text.

    Uses deterministic 2-universal hash functions derived via SHA-256
    with index salt: h_i(shingle) = SHA-256(shingle || ":" || i) mod 1,000,000.
    100% interoperable and bit-identical with app.domain.fuzzy_psi.

    Args:
        text: Input string.
        num_hashes: Number of hash functions / signature dimensions K.
        n: Character n-gram shingle size.

    Returns:
        List of K integer hash values representing the MinHash signature.
    """
    if num_hashes <= 0:
        raise ValueError(f"num_hashes must be a positive integer, got {num_hashes}")

    if not text:
        return [0] * num_hashes

    shingles = extract_character_shingles(text, n=n, lowercase=True, strip=True)
    if not shingles:
        return [0] * num_hashes

    signature: list[int] = []
    for i in range(num_hashes):
        min_val = float("inf")
        for shingle in shingles:
            h_str = f"{shingle}:{i}"
            h_val = int(hashlib.sha256(h_str.encode("utf-8")).hexdigest(), 16)
            if h_val < min_val:
                min_val = h_val
        signature.append(int(min_val % 1000000))

    return signature


def estimate_jaccard_similarity(sig_a: list[int], sig_b: list[int]) -> float:
    """Estimates Jaccard similarity between two MinHash signatures.

    J_hat(A, B) = (1 / K) * sum_{i=1}^K I[s_A[i] == s_B[i]]

    Args:
        sig_a: MinHash signature vector for entity A.
        sig_b: MinHash signature vector for entity B.

    Returns:
        Estimated Jaccard similarity in [0.0, 1.0].
    """
    if not sig_a or not sig_b or len(sig_a) != len(sig_b) or len(sig_a) == 0:
        return 0.0

    matches = sum(1 for x, y in zip(sig_a, sig_b) if x == y)
    sim = matches / len(sig_a)
    return round(max(0.0, min(1.0, sim)), 4)


def theoretical_minhash_variance(jaccard: float, num_hashes: int) -> float:
    """Computes theoretical estimation variance for MinHash: Var(J_hat) = J*(1 - J) / K."""
    if num_hashes <= 0:
        return 0.0
    j_clamped = max(0.0, min(1.0, jaccard))
    return (j_clamped * (1.0 - j_clamped)) / num_hashes


def theoretical_minhash_std_err(jaccard: float, num_hashes: int) -> float:
    """Computes theoretical standard error: sigma(J_hat) = sqrt(J*(1 - J) / K)."""
    return math.sqrt(theoretical_minhash_variance(jaccard, num_hashes))


def partition_into_lsh_bands(
    signature: list[int], num_bands: int, rows_per_band: int = 1
) -> list[str]:
    """Partitions a MinHash signature into LSH band hashes for candidate bucket lookup.

    Args:
        signature: MinHash signature vector of length K.
        num_bands: Number of bands b.
        rows_per_band: Number of rows per band r (K >= b * r).

    Returns:
        List of b SHA-256 band hashes (16-char hex prefix).
    """
    if not signature or num_bands <= 0 or rows_per_band <= 0:
        return []

    band_hashes: list[str] = []
    if rows_per_band == 1:
        for idx, val in enumerate(signature[:num_bands]):
            band_repr = f"b{idx}:{val}"
            band_hashes.append(
                hashlib.sha256(band_repr.encode("utf-8")).hexdigest()[:16]
            )
    else:
        for b_idx in range(num_bands):
            start = b_idx * rows_per_band
            end = start + rows_per_band
            if start >= len(signature):
                break
            chunk = signature[start:end]
            chunk_str = ",".join(str(x) for x in chunk)
            band_repr = f"b{b_idx}:r{rows_per_band}:{chunk_str}"
            band_hashes.append(
                hashlib.sha256(band_repr.encode("utf-8")).hexdigest()[:16]
            )

    return band_hashes


def theoretical_collision_probability(
    jaccard_similarity: float, num_bands: int, rows_per_band: int
) -> float:
    """Computes theoretical LSH collision probability under (b, r) banding.

    P(collision | J, b, r) = 1 - (1 - J^r)^b

    Args:
        jaccard_similarity: True or estimated Jaccard similarity J in [0.0, 1.0].
        num_bands: Number of bands b.
        rows_per_band: Rows per band r.

    Returns:
        Probability of at least one band collision in [0.0, 1.0].
    """
    j = max(0.0, min(1.0, jaccard_similarity))
    if num_bands <= 0 or rows_per_band <= 0:
        return 0.0
    if j == 0.0:
        return 0.0
    if j == 1.0:
        return 1.0

    p_row = j**rows_per_band
    p_no_collision_band = 1.0 - p_row
    p_no_collision_all = p_no_collision_band**num_bands
    return round(max(0.0, min(1.0, 1.0 - p_no_collision_all)), 6)


def theoretical_s_curve_threshold(num_bands: int, rows_per_band: int) -> float:
    """Calculates S-curve inflection point threshold: tau* = (1 / b)^(1 / r)."""
    if num_bands <= 0 or rows_per_band <= 0:
        return 0.5
    return round((1.0 / num_bands) ** (1.0 / rows_per_band), 4)


@dataclass(frozen=True)
class VectorPairEvaluation:
    """Result of evaluating a single entity pair against MinHash LSH."""

    category: VectorCategory
    label: str
    text_a: str
    text_b: str
    num_hashes: int
    num_bands: int
    rows_per_band: int
    ground_truth_jaccard: float
    estimated_jaccard: float
    absolute_error: float
    theoretical_std_err: float
    collided_bands: int
    is_candidate_match: bool
    theoretical_collision_prob: float


@dataclass
class CollisionCharacterizationSummary:
    """Summary of empirical collision and accuracy metrics for a specific K configuration."""

    num_hashes: int
    num_bands: int
    rows_per_band: int
    s_curve_threshold: float
    total_pairs_evaluated: int
    mean_absolute_error: float
    max_absolute_error: float
    exact_match_fidelity: float  # Fraction of exact matches with J_hat == 1.0
    casing_invariance_fidelity: float  # Fraction of casing variations with J_hat == 1.0
    typo_candidate_recall: float  # Fraction of typo pairs detected as candidate matches
    transposition_candidate_recall: float  # Fraction of transposition pairs detected
    false_positive_collision_rate: float  # Candidate collision rate for non-matches
    evaluations: list[VectorPairEvaluation] = field(default_factory=list)

    @property
    def typo_recall(self) -> float:
        return self.typo_candidate_recall

    @property
    def transposition_recall(self) -> float:
        return self.transposition_candidate_recall


# Standardized deterministic test vectors for similarity characterization
DEFAULT_SIMILARITY_TEST_VECTORS: list[dict[str, Any]] = [
    # 1. Exact Matches
    {
        "category": VectorCategory.EXACT_MATCH,
        "label": "exact_standard_name",
        "text_a": "Acme Global Financial Holdings Ltd",
        "text_b": "Acme Global Financial Holdings Ltd",
    },
    {
        "category": VectorCategory.EXACT_MATCH,
        "label": "exact_merchant_descriptor",
        "text_a": "AMAZON EU SARL LUXEMBOURG",
        "text_b": "AMAZON EU SARL LUXEMBOURG",
    },
    {
        "category": VectorCategory.EXACT_MATCH,
        "label": "exact_individual_name",
        "text_a": "Alexander Hamilton",
        "text_b": "Alexander Hamilton",
    },
    # 2. Typos (Single insertion, deletion, or substitution)
    {
        "category": VectorCategory.TYPO,
        "label": "typo_omission_char",
        "text_a": "Alexander Hamilton",
        "text_b": "Alexandr Hamilton",  # missing 'e'
    },
    {
        "category": VectorCategory.TYPO,
        "label": "typo_substitution_char",
        "text_a": "Deutsche Bank AG Frankfurt",
        "text_b": "Deutshce Bank AG Frankfurt",  # 'sh' transposition
    },
    {
        "category": VectorCategory.TYPO,
        "label": "typo_insertion_char",
        "text_a": "BNP Paribas Fortis Brussels",
        "text_b": "BNPP Paribas Fortis Brussels",  # extra 'P'
    },
    # 3. Casing Variations
    {
        "category": VectorCategory.CASING,
        "label": "casing_upper_vs_lower",
        "text_a": "CREDIT AGRICOLE CORPORATE AND INVESTMENT BANK",
        "text_b": "credit agricole corporate and investment bank",
    },
    {
        "category": VectorCategory.CASING,
        "label": "casing_mixed_title",
        "text_a": "ING Bank Slaski Spolka Akcyjna",
        "text_b": "ing BANK slaski SPOLKA akcyjna",
    },
    # 4. Transpositions (Word-order permutations)
    {
        "category": VectorCategory.TRANSPOSITION,
        "label": "transposition_name_inversion",
        "text_a": "John Alexander Smith",
        "text_b": "Smith John Alexander",
    },
    {
        "category": VectorCategory.TRANSPOSITION,
        "label": "transposition_corporate_descriptor",
        "text_a": "Global Logistics Trade Partners",
        "text_b": "Trade Partners Global Logistics",
    },
    # 5. Non-Matches (Orthogonal / Unrelated entities)
    {
        "category": VectorCategory.NON_MATCH,
        "label": "non_match_unrelated_banks",
        "text_a": "Banco Santander SA Madrid",
        "text_b": "Tokyo Electron Device Corporation",
    },
    {
        "category": VectorCategory.NON_MATCH,
        "label": "non_match_unrelated_individuals",
        "text_a": "Maria Garcia Hernandez",
        "text_b": "Dmitry Ivanovich Smirnov",
    },
    {
        "category": VectorCategory.NON_MATCH,
        "label": "non_match_short_distinct",
        "text_a": "Alpha Consulting",
        "text_b": "Zeta Technologies",
    },
]


def evaluate_minhash_lsh_suite(
    test_vectors: list[dict[str, Any]] | None = None,
    num_hashes_configs: list[tuple[int, int, int]] | None = None,
    similarity_threshold: float = 0.40,
) -> dict[int, CollisionCharacterizationSummary]:
    """Runs a complete empirical characterization of MinHash LSH across K dimensions.

    Evaluates exact matches, typos, casing variations, transpositions, and non-matches
    against exact ground-truth Jaccard similarity.

    Args:
        test_vectors: Test pairs to evaluate (defaults to DEFAULT_SIMILARITY_TEST_VECTORS).
        num_hashes_configs: List of (K, num_bands, rows_per_band) configurations.
            Defaults to:
            - K=32:  b=8,  r=4 (S-curve tau* ≈ 0.59)
            - K=64:  b=16, r=4 (S-curve tau* ≈ 0.50)
            - K=128: b=16, r=8 (S-curve tau* ≈ 0.70)
        similarity_threshold: Threshold for candidate similarity match.

    Returns:
        Dictionary mapping K -> CollisionCharacterizationSummary.
    """
    vectors = test_vectors or DEFAULT_SIMILARITY_TEST_VECTORS
    configs = num_hashes_configs or [
        (32, 8, 4),
        (64, 16, 4),
        (128, 16, 8),
    ]

    summaries: dict[int, CollisionCharacterizationSummary] = {}

    for k, b, r in configs:
        evaluations: list[VectorPairEvaluation] = []
        errors: list[float] = []

        exact_correct = 0
        exact_total = 0
        casing_correct = 0
        casing_total = 0
        typo_hits = 0
        typo_total = 0
        transposition_hits = 0
        transposition_total = 0
        non_match_collisions = 0
        non_match_total = 0

        tau_star = theoretical_s_curve_threshold(b, r)

        for vec in vectors:
            cat = VectorCategory(vec["category"])
            label = vec["label"]
            text_a = vec["text_a"]
            text_b = vec["text_b"]

            # Ground truth
            shingles_a = extract_character_shingles(text_a, n=3)
            shingles_b = extract_character_shingles(text_b, n=3)
            gt_jaccard = ground_truth_jaccard_similarity(shingles_a, shingles_b)

            # MinHash estimation
            sig_a = compute_minhash_signature(text_a, num_hashes=k, n=3)
            sig_b = compute_minhash_signature(text_b, num_hashes=k, n=3)
            est_jaccard = estimate_jaccard_similarity(sig_a, sig_b)

            err = abs(est_jaccard - gt_jaccard)
            errors.append(err)
            std_err = theoretical_minhash_std_err(gt_jaccard, k)

            # LSH banding
            bands_a = partition_into_lsh_bands(sig_a, num_bands=b, rows_per_band=r)
            bands_b = partition_into_lsh_bands(sig_b, num_bands=b, rows_per_band=r)

            collided_bands = sum(1 for h1, h2 in zip(bands_a, bands_b) if h1 == h2)
            is_cand = collided_bands > 0
            p_coll = theoretical_collision_probability(gt_jaccard, b, r)

            eval_res = VectorPairEvaluation(
                category=cat,
                label=label,
                text_a=text_a,
                text_b=text_b,
                num_hashes=k,
                num_bands=b,
                rows_per_band=r,
                ground_truth_jaccard=gt_jaccard,
                estimated_jaccard=est_jaccard,
                absolute_error=round(err, 4),
                theoretical_std_err=round(std_err, 4),
                collided_bands=collided_bands,
                is_candidate_match=is_cand,
                theoretical_collision_prob=p_coll,
            )
            evaluations.append(eval_res)

            # Stratified categorization
            if cat == VectorCategory.EXACT_MATCH:
                exact_total += 1
                if est_jaccard == 1.0:
                    exact_correct += 1
            elif cat == VectorCategory.CASING:
                casing_total += 1
                if est_jaccard == 1.0:
                    casing_correct += 1
            elif cat == VectorCategory.TYPO:
                typo_total += 1
                if is_cand or est_jaccard >= similarity_threshold:
                    typo_hits += 1
            elif cat == VectorCategory.TRANSPOSITION:
                transposition_total += 1
                if is_cand or est_jaccard >= similarity_threshold:
                    transposition_hits += 1
            elif cat == VectorCategory.NON_MATCH:
                non_match_total += 1
                if is_cand:
                    non_match_collisions += 1

        mean_err = round(sum(errors) / len(errors), 4) if errors else 0.0
        max_err = round(max(errors), 4) if errors else 0.0

        summary = CollisionCharacterizationSummary(
            num_hashes=k,
            num_bands=b,
            rows_per_band=r,
            s_curve_threshold=tau_star,
            total_pairs_evaluated=len(evaluations),
            mean_absolute_error=mean_err,
            max_absolute_error=max_err,
            exact_match_fidelity=(exact_correct / exact_total) if exact_total else 1.0,
            casing_invariance_fidelity=(casing_correct / casing_total)
            if casing_total
            else 1.0,
            typo_candidate_recall=(typo_hits / typo_total) if typo_total else 1.0,
            transposition_candidate_recall=(transposition_hits / transposition_total)
            if transposition_total
            else 1.0,
            false_positive_collision_rate=(non_match_collisions / non_match_total)
            if non_match_total
            else 0.0,
            evaluations=evaluations,
        )
        summaries[k] = summary

    return summaries


class MinHashLSHIndex:
    """Thread-safe in-memory Locality-Sensitive Hashing index for entities.

    Stores entities partitioned by LSH band bucket hashes for O(1) candidate lookup
    without inspecting un-related entities or storing raw PII.
    """

    def __init__(
        self,
        num_hashes: int = 64,
        num_bands: int = 16,
        rows_per_band: int = 1,
        similarity_threshold: float = 0.40,
    ) -> None:
        self.num_hashes = num_hashes
        self.num_bands = num_bands
        self.rows_per_band = rows_per_band
        self.similarity_threshold = similarity_threshold
        self._index: dict[str, list[tuple[str, str, list[int]]]] = {}
        self._lock = threading.RLock()

    def index_entity(self, entity_id: str, bank_id: str, text: str) -> list[str]:
        """Indexes an entity by computing its MinHash signature and LSH band buckets."""
        sig = compute_minhash_signature(text, num_hashes=self.num_hashes)
        bands = partition_into_lsh_bands(
            sig, num_bands=self.num_bands, rows_per_band=self.rows_per_band
        )

        with self._lock:
            self._remove_entity_unlocked(entity_id)
            for band in bands:
                if band not in self._index:
                    self._index[band] = []
                self._index[band].append((entity_id, bank_id, sig))

        return bands

    def query(
        self, text: str, querying_bank_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Queries the LSH index for candidate entities exceeding the similarity threshold."""
        sig = compute_minhash_signature(text, num_hashes=self.num_hashes)
        bands = partition_into_lsh_bands(
            sig, num_bands=self.num_bands, rows_per_band=self.rows_per_band
        )

        candidates: dict[str, tuple[str, list[int], int]] = {}
        with self._lock:
            for band in bands:
                for ent_id, b_id, ent_sig in self._index.get(band, []):
                    if querying_bank_id is None or b_id != querying_bank_id:
                        prior = candidates.get(ent_id)
                        cnt = (prior[2] + 1) if prior else 1
                        candidates[ent_id] = (b_id, ent_sig, cnt)

        results: list[dict[str, Any]] = []
        for ent_id, (b_id, ent_sig, band_cnt) in candidates.items():
            est_sim = estimate_jaccard_similarity(sig, ent_sig)
            if est_sim >= self.similarity_threshold:
                results.append(
                    {
                        "entity_id": ent_id,
                        "bank_id": b_id,
                        "estimated_jaccard": est_sim,
                        "matched_bands": band_cnt,
                        "total_bands": self.num_bands,
                    }
                )

        results.sort(key=lambda x: x["estimated_jaccard"], reverse=True)
        return results

    def remove_entity(self, entity_id: str) -> bool:
        """Removes an entity from all LSH buckets (supporting GDPR right-to-erasure)."""
        with self._lock:
            return self._remove_entity_unlocked(entity_id)

    def _remove_entity_unlocked(self, entity_id: str) -> bool:
        removed = False
        empty_keys = []
        for band_key, entries in self._index.items():
            filtered = [e for e in entries if e[0] != entity_id]
            if len(filtered) < len(entries):
                removed = True
                self._index[band_key] = filtered
            if not filtered:
                empty_keys.append(band_key)
        for k in empty_keys:
            del self._index[k]
        return removed

    def clear(self) -> None:
        """Clears all indexed buckets."""
        with self._lock:
            self._index.clear()

    def count_entities(self) -> int:
        """Returns the total number of unique entities indexed."""
        with self._lock:
            all_ids = {e[0] for entries in self._index.values() for e in entries}
            return len(all_ids)
