"""MinHash Locality-Sensitive Hashing (LSH) Fuzzy PSI Domain Module.

Enables privacy-preserving fuzzy entity matching across bank perimeters
using character 3-gram MinHash signatures and LSH band bucket partitioning.
Raw customer PII (names, addresses, phone numbers) is never transmitted.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field


def compute_minhash_signature(text: str, num_hashes: int = 16) -> list[int]:
    """Computes character 3-gram MinHash signature vector for input text.

    Args:
        text: Input string (e.g. name, merchant descriptor, standardized text).
        num_hashes: Number of deterministic hash functions / signature dimensions
            (e.g., 16 for fast approximation, 64 or 128 for high-precision cross-bank PSI).

    Returns:
        List of integer hash values representing the MinHash signature.
    """
    if num_hashes <= 0:
        raise ValueError(f"num_hashes must be a positive integer, got {num_hashes}")

    if not text:
        return [0] * num_hashes

    clean_text = text.lower().strip()
    shingles = (
        {clean_text}
        if len(clean_text) < 3
        else {clean_text[i : i + 3] for i in range(len(clean_text) - 2)}
    )

    signature = []
    for i in range(num_hashes):
        min_val = float("inf")
        for shingle in shingles:
            h_str = f"{shingle}:{i}"
            h_val = int(hashlib.sha256(h_str.encode("utf-8")).hexdigest(), 16)
            if h_val < min_val:
                min_val = h_val
        signature.append(int(min_val % 1000000))

    return signature


def calculate_jaccard_similarity(sig1: list[int], sig2: list[int]) -> float:
    """Estimates Jaccard similarity between two MinHash signature vectors."""
    if not sig1 or not sig2 or len(sig1) != len(sig2) or len(sig1) == 0:
        return 0.0
    matches = sum(1 for x, y in zip(sig1, sig2) if x == y)
    sim = matches / len(sig1)
    return round(max(0.0, min(1.0, sim)), 4)


def lsh_band_buckets(
    signature: list[int],
    num_bands: int = 16,
    rows_per_band: int = 1,
) -> list[str]:
    """Partitions a MinHash signature into LSH band hashes for candidate indexing.

    Args:
        signature: MinHash signature vector of length N (e.g., 16, 64, 128).
        num_bands: Number of bands to partition into (default 16).
        rows_per_band: Number of signature elements grouped per band (default 1).
            When rows_per_band == 1, operates in single-element high-recall mode.
            When rows_per_band > 1, groups adjacent elements into multi-row band chunks.

    Returns:
        List of SHA-256 band hashes suitable for exact bucket lookup.
    """
    if not signature or num_bands <= 0 or rows_per_band <= 0:
        return []

    band_hashes = []
    if rows_per_band == 1:
        # High-recall 1-element bands (preserves compatibility with signature[:num_bands])
        for idx, val in enumerate(signature[:num_bands]):
            band_repr = f"b{idx}:{val}"
            band_hash = hashlib.sha256(band_repr.encode("utf-8")).hexdigest()[:16]
            band_hashes.append(band_hash)
    else:
        # Multi-row band chunking (r elements per band, b bands)
        for b_idx in range(num_bands):
            start = b_idx * rows_per_band
            end = start + rows_per_band
            if start >= len(signature):
                break
            chunk = signature[start:end]
            chunk_str = ",".join(str(x) for x in chunk)
            band_repr = f"b{b_idx}:r{rows_per_band}:{chunk_str}"
            band_hash = hashlib.sha256(band_repr.encode("utf-8")).hexdigest()[:16]
            band_hashes.append(band_hash)

    return band_hashes


@dataclass
class FuzzyMatchCandidate:
    """Represents a matched fuzzy entity candidate pair."""

    entity_id_a: str
    entity_id_b: str
    jaccard_similarity: float
    matched_bands: int
    is_match: bool


@dataclass
class FuzzyPSIMatcher:
    """Locality-Sensitive Hashing (LSH) fuzzy matcher for privacy-preserving profile matching."""

    num_hashes: int = 16
    num_bands: int = 8
    rows_per_band: int = 1
    similarity_threshold: float = 0.25
    index: dict[str, list[tuple[str, str, list[int]]]] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def index_entity(self, entity_id: str, bank_id: str, raw_text: str) -> list[str]:
        """Indexes an entity profile by computing MinHash signature and LSH band buckets."""
        sig = compute_minhash_signature(raw_text, num_hashes=self.num_hashes)
        bands = lsh_band_buckets(
            sig, num_bands=self.num_bands, rows_per_band=self.rows_per_band
        )

        with self._lock:
            # Remove any prior entry for entity_id to ensure idempotency
            self._remove_entity_unlocked(entity_id)
            for band_hash in bands:
                if band_hash not in self.index:
                    self.index[band_hash] = []
                self.index[band_hash].append((entity_id, bank_id, sig))

        return bands

    def remove_entity(self, entity_id: str) -> bool:
        """Removes an entity from all LSH buckets (GDPR Art. 17 right-to-erasure)."""
        with self._lock:
            return self._remove_entity_unlocked(entity_id)

    def _remove_entity_unlocked(self, entity_id: str) -> bool:
        removed = False
        empty_buckets = []
        for band_hash, entries in self.index.items():
            new_entries = [e for e in entries if e[0] != entity_id]
            if len(new_entries) < len(entries):
                removed = True
                self.index[band_hash] = new_entries
            if not new_entries:
                empty_buckets.append(band_hash)
        for b in empty_buckets:
            del self.index[b]
        return removed

    def clear(self) -> None:
        """Clears all indexed buckets."""
        with self._lock:
            self.index.clear()

    def get_indexed_entity_count(self) -> int:
        """Returns the number of unique entities indexed in LSH buckets."""
        with self._lock:
            unique_ids = set()
            for entries in self.index.values():
                for ent_id, _, _ in entries:
                    unique_ids.add(ent_id)
            return len(unique_ids)

    def get_candidate_ids_for_text(self, raw_text: str) -> set[str]:
        """Returns candidate entity IDs sharing at least one LSH bucket with query text."""
        sig = compute_minhash_signature(raw_text, num_hashes=self.num_hashes)
        bands = lsh_band_buckets(
            sig, num_bands=self.num_bands, rows_per_band=self.rows_per_band
        )
        with self._lock:
            candidate_ids = set()
            for band_hash in bands:
                for cand_id, _, _ in self.index.get(band_hash, []):
                    candidate_ids.add(cand_id)
            return candidate_ids

    def match_profile(
        self, entity_id: str, bank_id: str, raw_text: str
    ) -> list[FuzzyMatchCandidate]:
        """Finds cross-bank candidate matches exceeding similarity threshold using LSH bucket lookup."""
        sig = compute_minhash_signature(raw_text, num_hashes=self.num_hashes)
        bands = lsh_band_buckets(
            sig, num_bands=self.num_bands, rows_per_band=self.rows_per_band
        )

        with self._lock:
            candidate_sigs: dict[str, tuple[list[int], int]] = {}
            for band_hash in bands:
                for cand_id, cand_bank, cand_sig in self.index.get(band_hash, []):
                    if cand_bank != bank_id:
                        cnt = candidate_sigs.get(cand_id, (cand_sig, 0))[1] + 1
                        candidate_sigs[cand_id] = (cand_sig, cnt)

        results = []
        for cand_id, (cand_sig, matched_bands) in candidate_sigs.items():
            jaccard_sim = calculate_jaccard_similarity(sig, cand_sig)
            if jaccard_sim >= self.similarity_threshold:
                results.append(
                    FuzzyMatchCandidate(
                        entity_id_a=entity_id,
                        entity_id_b=cand_id,
                        jaccard_similarity=round(jaccard_sim, 4),
                        matched_bands=matched_bands,
                        is_match=True,
                    )
                )

        return sorted(results, key=lambda c: c.jaccard_similarity, reverse=True)
