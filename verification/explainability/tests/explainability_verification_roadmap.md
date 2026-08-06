# Scientific Verification Roadmap — Explainability (XAI) Module

**Subsystem:** Explainability & Interpretable ML (`explainability_service.py`, `realtime_explainer.py`, `value_objects_phase2.py`)  
**Auditor Role:** Senior Researcher in Explainable AI (XAI), Interpretable ML, & Scientific Software Verification  
**Evaluation Standard:** Publication-Quality XAI Scientific Verification Protocol  
**Date:** 2026-07-31  

---

## 1. Executive Summary

This document establishes a rigorous scientific verification roadmap for the **Explainability (XAI)** module. To transition the subsystem from heuristic implementations to publication-grade scientific credibility, every explanation algorithm, attribution metric, and recourse mechanism must undergo systematic validation across ten scientific verification dimensions:

```
+-----------------------------------------------------------------------------------+
|                        XAI VERIFICATION ROADMAP ARCHITECTURE                      |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  1. Numerical Reference Verification  ---> First-principles Shapley calculation   |
|  2. Property-Based Invariant Testing  ---> Efficiency, Symmetry, Dummy axioms     |
|  3. Sanity Checks (Adebayo et al.)    ---> Model & Label Randomization Tests      |
|  4. Faithfulness Evaluation           ---> Feature Deletion / Insertion AUC       |
|  5. Stability & Robustness Testing    ---> Lipschitz continuity under noise       |
|  6. Counterfactual Validity Audit     ---> Manifold proximity & recourse check    |
|  7. GNN Attribution Verification      ---> Mutual information mask optimization  |
|  8. Inference Replay Verification     ---> Independent score reconstruction       |
|  9. Memory & Cache Leak Profiling     ---> Tracemalloc & Redis disconnection test |
| 10. Latency SLA Benchmarking          ---> Microsecond online timing SLAs        |
|                                                                                   |
+-----------------------------------------------------------------------------------+
```

---

## 2. Verification Protocol per Explainability Component

### 2.1 KernelSHAP Feature Attribution Engine (`compute_shap_values`)

#### Target Component
`ExplainabilityService.compute_shap_values`

#### Recommended Verification Methodologies & Scientific Rationale

1. **Independent Mathematical Reference Implementation:**
   - **Method:** Construct an independent, exact Shapley value solver in Python using combinatorial power sets ($2^d$ feature subsets) for small feature dimension ($d=10$).
   - **Rationale:** Verifies whether `shap.KernelExplainer` outputs match exact mathematical Shapley values $\phi_i$ to float64 precision.
   - **Target Metric:** $\max_i |\phi_i^{\text{production}} - \phi_i^{\text{reference}}| < 10^{-4}$.

2. **Property-Based Testing (Hypothesis Framework):**
   - **Method:** Verify fundamental Shapley axioms across 1,000 randomized transaction vectors:
     - *Efficiency Axiom:* $\sum_{i=1}^d \phi_i = f(x) - \mathbb{E}[f(X)]$.
     - *Symmetry Axiom:* If $f(S \cup \{i\}) = f(S \cup \{j\})$ for all $S$, then $\phi_i = \phi_j$.
     - *Dummy / Null Player Axiom:* If feature $i$ has no impact on predictions, $\phi_i = 0$.
   - **Rationale:** Ensures attribution vectors satisfy theoretical game-theoretic guarantees.

3. **Sanity Checks (Model & Label Randomization Tests — Adebayo et al., NeurIPS 2018):**
   - **Method:** Cascading model weight randomization test. Progressively randomize PyTorch model layer weights from top to bottom and compute SHAP attributions at each step.
   - **Rationale:** If SHAP attributions remain unchanged when model weights are randomized, the explainer is insensitive to model parameters and merely acting as an edge detector or feature scaler.
   - **Target Metric:** Rank correlation (Spearman $\rho$) between trained model attributions and randomized model attributions must approach $0.0$.

4. **Faithfulness Evaluation (Feature Drop AUC / Pixel-Flipping):**
   - **Method:** Sort features by SHAP contribution descending. Progressively set top $k$ features to baseline value ($k=1 \dots d$) and track prediction drop $\Delta f(x)$.
   - **Rationale:** A faithful explanation must show a sharp decline in prediction confidence when top-attributed features are masked.
   - **Target Metric:** Area Under the Feature Deletion Curve ($\text{AUC}_{\text{drop}}$). Higher $\text{AUC}_{\text{drop}}$ indicates superior explanation faithfulness.

5. **Stability Evaluation (Lipschitz Continuity under Input Noise):**
   - **Method:** Inject small Gaussian perturbation $\epsilon \sim \mathcal{N}(0, \sigma^2 I)$ into input $x$ ($x' = x + \epsilon$). Measure local Lipschitz continuity ratio:
     $$L(x) = \max_{x'} \frac{\|E(x) - E(x')\|_2}{\|x - x'\|_2}$$
   - **Rationale:** Explanation stability ensures that minor input noise (e.g. slight sensor fluctuation) does not produce catastrophic jumps in feature importance rankings.

---

### 2.2 Counterfactual Remediation & Recourse Engine (`generate_counterfactuals`)

#### Target Component
`ExplainabilityService.generate_counterfactuals`

#### Recommended Verification Methodologies & Scientific Rationale

1. **Independent Mathematical Optimization Reference:**
   - **Method:** Implement an independent loss-minimization counterfactual solver (e.g. DiCE / Wachter et al.) solving:
     $$\min_{x^*} \mathcal{L}(f(x^*), \tau) + \lambda_1 \|x - x^*\|_1 + \lambda_2 \text{MAD}(x^*)$$
   - **Rationale:** Compares production rule-based recourse suggestions against formal minimal-distance optimization paths.

2. **Property-Based Invariant Testing:**
   - **Method:** Verify counterfactual logical invariants over 1,000 randomized alert instances:
     - *Recourse Invariant:* $S_{\text{remediated}} \le S_{\text{target}}$ iff `is_cleared == True`.
     - *Monotonicity Invariant:* $S_{\text{remediated}} < S_{\text{original}}$ for all flagged alerts ($S_{\text{original}} > S_{\text{target}}$).
     - *Non-Empty Changes Invariant:* `len(changes) > 0` whenever $S_{\text{original}} > S_{\text{target}}$.
   - **Rationale:** Prevents illogical state outputs where an alert claims to be cleared despite remediated score exceeding target threshold.

3. **Data Manifold Proximity & Actionability Check:**
   - **Method:** Evaluate whether remediated point $x^*$ lies within the valid empirical data manifold (e.g. $x_i^* \in [\text{min}_i, \text{max}_i]$) and does not modify immutable features (e.g. `account_age_days` decreasing).
   - **Rationale:** Ensures counterfactual recommendations are physically and operationally actionable for banking customers.

---

### 2.3 GNN Graph Attribution Engine (`explain_gnn_embedding`)

#### Target Component
`ExplainabilityService.explain_gnn_embedding`

#### Recommended Verification Methodologies & Scientific Rationale

1. **Independent GNNExplainer Reference Implementation:**
   - **Method:** Implement PyTorch Geometric's `GNNExplainer` module optimizing an edge mask $M \in [0, 1]^{|E|}$ via gradient descent to maximize mutual information:
     $$\max_{M} \text{MI}(Y, G_s) = -\sum_{c} y_c \log \hat{y}_c - \lambda_1 \|M\|_1 - \lambda_2 H(M)$$
   - **Rationale:** Validates production edge attributions against true mutual-information-maximizing subgraphs.

2. **Random Edge Mask Sanity Check:**
   - **Method:** Randomly delete 50% of entity graph edges and verify that structural edge attribution percentages update dynamically.
   - **Rationale:** Ensures graph attributions respond to topological graph changes rather than returning static positional weights.

3. **Property-Based Invariant Testing:**
   - **Method:** Verify edge percentage sum invariant across 500 graph neighborhoods:
     $$\sum_{i=1}^{|E_{\text{top}}|} \text{contribution\_percentage}_i = 100.0\%$$
   - **Rationale:** Guarantees proper normalization of subgraph edge driver reports.

---

### 2.4 Deterministic Decision Replay Engine (`replay_inference_audit`)

#### Target Component
`ExplainabilityService.replay_inference_audit`

#### Recommended Verification Methodologies & Scientific Rationale

1. **Independent Score Reconstruction Verification:**
   - **Method:** Modify `replay_inference_audit` to recalculate `reconstructed_score` from raw evaluated rule contributions $\sum c_i \times 1000$ independently, rather than assigning `reconstructed_score = alert.risk_score`.
   - **Rationale:** Eliminates the current trivial matching defect and verifies true inference reproducibility.

2. **Reproducibility & Historical Traceability Testing:**
   - **Method:** Replay inference across 1,000 archived historical alerts and compare reconstructed scores against original logs.
   - **Rationale:** Proves 100% deterministic decision replay for financial regulators.
   - **Target Metric:** Replay match rate = 100.0% (`audit_matched == True`).

---

### 2.5 Real-Time Online Feature Attribution (`explain_realtime_score` & `explain_async`)

#### Target Component
`FastInferenceExplainer.explain_realtime_score`, `explain_async`

#### Recommended Verification Methodologies & Scientific Rationale

1. **Latency SLA Performance Benchmarking:**
   - **Method:** Benchmark `explain_realtime_score` and `explain_async` across 10,000 iterations using `time.perf_counter()`.
   - **Rationale:** Confirms compliance with sub-millisecond real-time scoring SLAs.
   - **Target SLAs:** Realtime score attribution latency $< 0.1 \text{ ms}$; Redis cache hit latency $< 1.0 \text{ ms}$.

2. **Memory Leakage Profiling (`tracemalloc`):**
   - **Method:** Simulate Redis disconnection and execute 50,000 `explain_async` cache-miss calls. Profile memory growth of `_local_shap_cache`.
   - **Rationale:** Prevents unbounded memory leaks in production servers when Redis is offline.

3. **Boundary Value Testing:**
   - **Method:** Test extreme inputs (amount = $0.0$, velocity = $0$, negative amounts, unknown MCC categories).
   - **Rationale:** Ensures robust directional attributions without unhandled exceptions.

---

## 3. Verification Execution Matrix

| Verification Phase | Component | Method | Target Threshold / Metric | Priority |
|:---|:---|:---|:---|:---:|
| **Phase 1: Bug Remediation** | Decision Replay | Score reconstruction fix | `audit_matched == True` (independent) | **P1 (Critical)** |
| **Phase 1: Bug Remediation** | Async Cache | `_local_shap_cache` LRU bound | Zero unbounded memory growth | **P1 (Critical)** |
| **Phase 2: Numerical Verification** | SHAP Engine | First-principles Shapley solver | Max error $< 10^{-4}$ | **P2 (High)** |
| **Phase 2: Property Testing** | SHAP Engine | Hypothesis axiom suite | 1,000 / 1,000 trials passed | **P2 (High)** |
| **Phase 2: Invariant Testing** | Counterfactuals | Property-based bounds | $S_{\text{remediated}} \le S_{\text{target}}$ 100% | **P2 (High)** |
| **Phase 3: Sanity Checks** | SHAP Engine | Model Weight Randomization | Spearman $\rho \to 0.0$ on random weights | **P3 (Medium)** |
| **Phase 3: Faithfulness** | SHAP Engine | Feature Deletion Curve | Maximize $\text{AUC}_{\text{drop}}$ | **P3 (Medium)** |
| **Phase 3: Stability** | SHAP Engine | Lipschitz continuity test | $L(x) \le C$ under Gaussian noise | **P3 (Medium)** |
| **Phase 4: Benchmarking** | Realtime Engine | Latency SLA profiling | Latency $< 0.1 \text{ ms}$ | **P3 (Medium)** |

---

## 4. Expected Deliverables

Upon execution of this roadmap, the following scientific verification artifacts will be produced:

1. `explainability_reference_verification.py` & Report: Numerical Shapley calculation validation.
2. `test_explainability_hypothesis.py` & Report: Property-based testing of Shapley axioms & counterfactual invariants.
3. `test_explainability_adebayo_sanity.py` & Report: Model randomization sanity checks.
4. `test_explainability_faithfulness_stability.py` & Report: Feature deletion AUC & Lipschitz stability benchmarks.
5. `explainability_benchmark_scalability.py` & Report: Latency SLA & memory leakage profiling.
6. `verification/explainability/scientific_audit_report.md`: Final 14-section publication-quality audit report.

---

*End of Scientific Verification Roadmap — Explainability (XAI) Module*
