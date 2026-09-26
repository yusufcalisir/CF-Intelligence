# MinHash LSH Fuzzy Private Set Intersection (PSI) Specification

## 1. Problem Solved
In multi-bank financial crime intelligence, two banks frequently need to identify whether a suspect entity (e.g., mule account, shell corporation, or stolen device identifier) has interacted across both institutions without revealing their full customer or transaction directories to each other.

MinHash Locality-Sensitive Hashing (Broder, 1997) enables **Fuzzy Private Set Intersection (PSI)**, estimating Jaccard similarity between customer attribute sets with zero exposure of non-intersecting records.

---

## 2. Implementation in CF-Intelligence
- **Location**: [`backend/app/application/services/graph_engine.py`](file:///backend/app/application/services/graph_engine.py)
- **Mathematical Formulation**:
  For an entity attribute set $A \subseteq \mathcal{U}$:
  1. Apply $M$ independent hash functions $h_1, \dots, h_M$ drawn from a 2-universal family:
     $$h_m(x) = (a_m x + b_m) \bmod p$$
  2. Compute the MinHash signature vector:
     $$\mathbf{s}(A) = \left[ \min_{x \in A} h_1(x), \, \min_{x \in A} h_2(x), \, \dots, \, \min_{x \in A} h_M(x) \right]$$
  3. The probability of two sets having identical MinHash values matches their Jaccard similarity:
     $$P(\min h_m(A) = \min h_m(B)) = J(A, B) = \frac{|A \cap B|}{|A \cup B|}$$

- **LSH Banding Technique**:
  Signatures of length $M = 128$ are divided into $b$ bands of $r$ rows ($M = b \cdot r$):
  - Two entities are identified as candidate matches if their sub-vectors collide in at least one band.
  - S-curve probability threshold:
    $$P(\text{Candidate Match}) = 1 - (1 - J^r)^b$$
  - In CF-Intelligence, setting $b=16, r=8$ produces an inflection point at $J^* \approx \left(\frac{1}{b}\right)^{1/r} \approx 0.70$.

---

## 3. Threat Model & Privacy Guarantees
- **Zero Plaintext Exchange**: Banks exchange only 64-bit integer hashes derived from HMAC-SHA256 salted tokens.
- **CardinaIity Leakage**: While raw items are hidden, the number of colliding buckets reveals approximate Jaccard overlap cardinality.

---

## 4. Operational Limitations
- **False Positives**: Probabilistic collisions can occur between unrelated entities with tiny Jaccard similarity ($\approx 0.05$).
- **Hash Table Size**: Memory usage scales with $b \times \text{number of entities}$.

---

## 5. Test Suite Verification
- **Unit Tests**: [`backend/tests/unit/test_graph_engine.py`](file:///backend/tests/unit/test_graph_engine.py)
- **Fuzzy PSI Tests**: [`backend/tests/unit/test_fuzzy_psi.py`](file:///backend/tests/unit/test_fuzzy_psi.py)
