# Property-Based Testing Report — Explainability (XAI) Subsystem

**Module:** `explainability_service.py`, `realtime_explainer.py`  
**Test Suite:** `scratch/test_explainability_hypothesis.py`  
**Framework:** Hypothesis 6.x  
**Python Version:** 3.12  
**Test Execution Date:** 2026-08-01  
**Total Invariants Tested:** 6  
**Status:** ✅ 100% PASSED (6 / 6 Invariants)  

---

## 1. Executive Summary

Property-based testing using the **Hypothesis framework** was executed against the Explainability subsystem to verify mathematical, logical, and explainability invariants across hundreds of randomized scenarios. Unlike fixed unit tests, property-based tests continuously generate randomized feature vectors, prediction probabilities, extreme values ($10^{12}$), constant inputs, empty reason codes, and categorical variations.

All six tested invariants **passed with 100% success**, confirming mathematical consistency under valid inputs.

---

## 2. Invariants Tested & Verification Results

```
================================================================================
          EXPLAINABILITY HYPOTHESIS PROPERTY-BASED TEST RESULTS
================================================================================
Invariant 1: Signal Weights Sum to 1.0 & Scores in [0, 1]        ✅ PASSED (100/100)
Invariant 2: Remediated Score <= Target Score iff is_cleared     ✅ PASSED (100/100)
Invariant 3: Feature Array Length = 10 & Ordered Descending       ✅ PASSED (100/100)
Invariant 4: GNN Edge Attributions Sum to 100.0%                 ✅ PASSED (50/50)
Invariant 5: Real-Time Attributions Have Valid Bounds & Directions✅ PASSED (100/100)
Invariant 6: Decision Replay Evaluates 9 Policy Rules            ✅ PASSED (100/100)
================================================================================
```

---

## 3. Detailed Invariant Evaluations

### Invariant 1: Risk Signal Weights Sum to 1.0 & Normalized Score Bounds
* **Mathematical Statement:** $\sum_{i=1}^9 w_i = 1.0$ and $\tilde{v}_i \in [0.0, 1.0]$ for all 9 signals.
* **Randomized Scenarios:** Risk scores $S \in [0, 1000]$, random reason code subsets (0 to 9 codes), model confidence $[0, 1]$.
* **Hypothesis Result:** **PASS (100 trials)**. Signal weights sum to exactly $1.0$ ($\pm 10^{-5}$), and all normalized signal scores lie in $[0.0, 1.0]$.

---

### Invariant 2: Counterfactual Remediated Score & Recourse Invariant
* **Mathematical Statement:** For any alert with $S_{\text{orig}} > S_{\text{target}}$, the remediated explanation satisfies $S_{\text{remediated}} \le S_{\text{target}}$ iff `is_cleared == True`, and $S_{\text{remediated}} < S_{\text{orig}}$.
* **Randomized Scenarios:** Original risk scores $S_{\text{orig}} \in (350, 1000]$, target scores $S_{\text{target}} \in [100, 350]$, random reason code combinations.
* **Hypothesis Result:** **PASS (100 trials)**. Remediated scores consistently drop below target score, and `is_cleared` boolean strictly matches threshold comparison.

---

### Invariant 3: Feature Contribution Array Structure & Magnitude Sorting
* **Mathematical Statement:** Returned feature list contains exactly 10 items, sorted by absolute contribution magnitude descending: $|\phi_1| \ge |\phi_2| \ge \dots \ge |\phi_{10}|$.
* **Randomized Scenarios:** Amounts $[0, 10^{12}]$, velocities $[0, 100]$, account ages $[0, 3650]$, chargebacks $[0, 50]$, missing features, constant features ($0.0$).
* **Hypothesis Result:** **PASS (100 trials)**. Array length is invariant at 10 items, and ordering maintains strict magnitude sorting.

---

### Invariant 4: GNN Edge Contribution Percentage Sum
* **Mathematical Statement:** $\sum_{i=1}^{|E|} \text{contribution\_percentage}_i = 100.0\% \pm 0.5\%$.
* **Randomized Scenarios:** Random entity node IDs (`entity_node_1` to `entity_node_10000`), active graph and synthetic fallback paths.
* **Hypothesis Result:** **PASS (50 trials)**. Subgraph edge contribution percentages sum to $100.0\%$ on all iterations.

---

### Invariant 5: Real-Time Feature Attribution Bounds & Valid Directions
* **Mathematical Statement:** Attribution directions $\in \{\text{"INCREASES\_RISK"}, \text{"DECREASES\_RISK"}\}$ and contribution scores $\in [0.0, 1.0]$.
* **Randomized Scenarios:** Amounts $[0, 10^6]$, velocities $[0, 100]$, randomized merchant category strings (`crypto_exchange`, `retail`, `p2p_cash`, empty string, arbitrary text).
* **Hypothesis Result:** **PASS (100 trials)**. Real-time attribution engine handles arbitrary text strings and extreme amounts without invalid directions or out-of-bounds scores.

---

### Invariant 6: Decision Replay Policy Rule Integrity
* **Mathematical Statement:** Exactly 9 policy rules evaluated; each rule contribution $c_i = w_i \times \text{norm\_val}_i$.
* **Randomized Scenarios:** Risk scores $[50, 950]$, randomized reason code combinations.
* **Hypothesis Result:** **PASS (100 trials)**. Policy rule evaluation list length is invariant at 9 items, and rule contributions match weight multiplication.

---

## 4. Conclusion

The Hypothesis property-based test suite confirms that the Explainability module maintains **100% mathematical and schema consistency** across hundreds of randomized, edge-case, and extreme input combinations.
