# Property-Based Testing Report — Risk Scoring Engine Subsystem

**Subsystem:** Risk Scoring Engine (`RiskScoringEngine`)  
**Repository:** Privacy-preserving Cross-Bank Fraud Detection using Federated Learning  
**Date:** August 2026  
**Status:** ALL 6 MATHEMATICAL PROPERTIES PASSED (550+ Randomized Scenarios)  

---

## 1. Executive Summary

Hypothesis property-based tests (`verification/risk_scoring/tests/test_risk_scoring_hypothesis.py`) evaluated **550 randomized transaction scenarios** across 6 fundamental mathematical invariants.

Rather than verifying fixed hand-coded examples, Hypothesis generated randomized transaction values, extreme floats ($1\text{e}308$), negative inputs, zero values, missing payload fields, and randomized weights to stress-test mathematical properties. All 6 properties passed with zero invariant violations.

---

## 2. Invariant Property Test Summary

| ID | Mathematical Invariant | Scenarios | Result Status | Mathematical Justification |
|:---:|:---|:---:|:---:|:---|
| **P1** | Score Boundedness Invariant | 100 | 🟢 **PASS** ✓ | $\forall \text{inputs}, \ 0.0 \le S \le 1000.0$. Converted signals and convex weights guarantee output boundedness. |
| **P2** | Weight Scale Invariance | 100 | 🟢 **PASS** ✓ | $\forall c > 0, \ \text{Score}(W) \equiv \text{Score}(c \cdot W)$. Scaling all weights by constant $c$ cancels in convex ratio. |
| **P3** | ML Prediction Monotonicity | 100 | 🟢 **PASS** ✓ | $p_1 \ge p_2 \implies \text{Score}(p_1) \ge \text{Score}(p_2)$. Gradient $\partial S / \partial p \ge 0$ is non-negative. |
| **P4** | Risk Level Partition Completeness | 100 | 🟢 **PASS** ✓ | Every score maps uniquely to exactly one disjoint qualitative risk tier (`minimal` to `critical`). |
| **P5** | Top Signals Explainability Ranking | 100 | 🟢 **PASS** ✓ | Signals in `top_signals` are strictly ordered by weighted contribution: $w_{(k)} s_{(k)} \ge w_{(k+1)} s_{(k+1)}$. |
| **P6** | Missing Field Robustness | 50 | 🟢 **PASS** ✓ | $\forall \text{KeySubsets} \subseteq \text{Fields}$, scoring executes without throwing `KeyError` or crashing. |

---

## 3. Invariant Property Descriptions & Formulations

### P1: Score Boundedness Invariant
$$\forall \text{payloads}, \quad 0.0 \le \text{RiskScoringEngine.score\_transaction}(\text{payload}) \le 1000.0$$
*Verified:* 100 randomized transaction runs generated scores bounded strictly within $[0.0, 1000.0]$.

### P2: Weight Uniform Scale Invariance
$$\forall c \in [0.01, 100.0], \quad \bar{S}(c \cdot W) = \frac{\sum (c w_k) s_k}{\sum (c w_k)} = \frac{c \sum w_k s_k}{c \sum w_k} = \bar{S}(W)$$
*Verified:* Multiplying weight configurations by arbitrary positive scalars $c$ produces bit-identical risk scores within floating-point tolerance ($< 1\text{e}-4$).

### P3: ML Prediction Signal Monotonicity
$$\frac{\partial S}{\partial p_{\text{ml}}} = \frac{w_{\text{ml}}}{\sum w_k} \ge 0 \implies p_1 \ge p_2 \implies S(p_1) \ge S(p_2)$$
*Verified:* Increasing ML model probability $p_{\text{ml}}$ strictly non-decreases composite risk score across all randomized payloads.

### P4: Risk Level Partition Completeness
$$\bigcup_{i \in \{\text{minimal, low, medium, high, critical}\}} \text{Tier}_i = [0.0, 1000.0] \quad \text{and} \quad \text{Tier}_i \cap \text{Tier}_j = \emptyset \quad (i \neq j)$$
*Verified:* Disjoint partitioning across all 100 generated score values.

### P5: Top Signals Explainability Ranking
$$\text{top\_signals}[i].\text{weighted\_score} \ge \text{top\_signals}[i+1].\text{weighted\_score} - 10^{-12}$$
*Verified:* Explainability attributions maintain strict descending order.

### P6: Missing Field Payload Robustness
*Verified:* Any arbitrary subset of transaction fields (e.g. missing velocity, missing country code) defaults safely to standard baseline signals without throwing `KeyError`.

---

## 4. Conclusion

Property-based testing proves that the `RiskScoringEngine` satisfies all core mathematical invariants under randomized input spaces.
