# Scientific Verification Inventory — Explainability (XAI) Module

**Module Path:** `app.application.services.explainability_service`, `app.domain.realtime_explainer`, `app.domain.value_objects_phase2`  
**Auditor Role:** Senior Researcher in Explainable AI (XAI), Interpretable ML, & Scientific Software Verification  
**Evaluation Standard:** Systematic Scientific & Algorithmic Inventory  
**Date:** 2026-07-31  

---

## 1. Subsystem Architecture Overview

The Explainability module provides multi-layered explanations for fraud alerts, model predictions, graph embeddings, and regulatory audit compliance:

```
+-----------------------------------------------------------------------------------+
|                            EXPLAINABILITY MODULE ARCHITECTURE                     |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  1. ExplainabilityService                                                         |
|     ├── explain_alert()                 -> Multi-signal risk breakdown & report   |
|     ├── compute_shap_values()           -> KernelSHAP PyTorch / Heuristic SHAP    |
|     ├── generate_counterfactuals()       -> Recourse & GDPR Art. 22 remediation    |
|     ├── replay_inference_audit()        -> Deterministic policy rule audit replay |
|     └── explain_gnn_embedding()         -> GNN graph attribution & neighborhood   |
|                                                                                   |
|  2. FastInferenceExplainer                                                        |
|     ├── explain_realtime_score()        -> Sub-millisecond rule-based attribution |
|     ├── compute_shap()                  -> Redis-cached (300s TTL) async SHAP     |
|     └── explain_async()                 -> Cache-first async SHAP engine & webhook|
|                                                                                   |
|  3. Value Objects & Data Structures                                               |
|     ├── ExplainabilityReport            ├── CounterfactualExplanation             |
|     ├── DecisionReplayReport            ├── GNNExplanationReport                  |
|     └── RealtimeFeatureAttribution      └── PolicyRuleEvaluation                  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Complete Scientific Verification Inventory

### 2.1 Multi-Layer Alert Explanation Engine (`explain_alert`)

* **Component:** `ExplainabilityService.explain_alert` (`explainability_service.py` L45–L107)
* **Purpose:** Generates a comprehensive explainability report for a fraud alert combining feature importance, 9 business rule risk signals, historical evidence, confidence, and natural language text.
* **Mathematical Formulation:**
  - Risk Signal Map ($M$): 9 signals ($s_1, \dots, s_9$) with fixed weights $w_i \in [0.07, 0.25]$, $\sum_{i=1}^9 w_i = 1.0$.
  - Normalized Base Score: $B = \text{alert.risk\_score} / 1000.0$.
  - Raw Signal Score: $v_i = B \cdot c_i$, where $c_i = 1.0$ if reason code triggered, else $c_i \in [0.10, 0.40]$.
  - Scale Factor: $\gamma = \frac{B}{\sum_{i=1}^9 w_i v_i}$ (if $\sum w_i v_i > 0$, else $1.0$).
  - Normalized Score: $\tilde{v}_i = \min(1.0, v_i \cdot \gamma)$.
* **Explainability Claim:** Multi-level risk attribution breaking down final risk score into proportional constituent signal contributions.
* **Expected Invariant:**
  1. $\sum_{i=1}^9 w_i = 1.0$.
  2. $\tilde{v}_i \in [0.0, 1.0]$ for all $i$.
  3. Signal breakdown ranks highest-contributing triggers first.
* **Possible Implementation Risks:**
  - **Post-Hoc Pseudo-Decomposition:** $v_i$ is computed *from* `alert.risk_score` rather than from raw input features. The explanation is a synthetic post-hoc reconstruction, not the actual generative mechanism.
  - **Division by Zero:** If $\sum w_i v_i = 0$, scale factor defaults to $1.0$.
* **Edge Cases:** Alerts with no reason codes (`reason_codes = []`); alerts with extreme risk score ($0.0$ or $1000.0$).
* **Scientific Claim Being Made:** Provides human-interpretable, multi-factor risk attribution satisfying explainable AI requirements for financial fraud alerts.
* **Appropriate Verification Methodology:** Property-based testing (verifying scale factor positivity, bounds $[0, 1]$, and weight summation $\sum w_i = 1.0$), numerical reference verification of signal breakdown.

---

### 2.2 KernelSHAP Feature Attribution Engine (`compute_shap_values`)

* **Component:** `ExplainabilityService.compute_shap_values` (`explainability_service.py` L184–L356)
* **Purpose:** Computes SHAP (SHapley Additive exPlanations) values for a 10-feature transaction vector using PyTorch model `FraudDetectionModel` or fallback analytical heuristic.
* **Mathematical Formulation:**
  - Shapley Value Axiomatic Formula:
    $$\phi_i(f, x) = \sum_{S \subseteq N \setminus \{i\}} \frac{|S|!(|N|-|S|-1)!}{|N|!} \left[ f_x(S \cup \{i\}) - f_x(S) \right]$$
  - KernelSHAP implementation: `shap.KernelExplainer(predict_fn, baseline)` with $n_{\text{samples}} = 100$ over $20 \times 10$ synthetic background baseline.
* **Explainability Claim:** Additive feature attributions satisfying Efficiency ($\sum \phi_i = f(x) - \mathbb{E}[f(x)]$), Symmetry, Dummy, and Additivity axioms.
* **Expected Invariant:**
  1. Feature contributions array length = 10.
  2. Feature contributions sorted by absolute value descending ($|\phi_1| \ge |\phi_2| \ge \dots$).
* **Possible Implementation Risks:**
  - **Silent Fallback to Heuristic:** If `shap` is not installed or PyTorch model fails to load, code silently falls back to a linear heuristic ($w_i \times (0.5 + 0.5 \min(1.0, x_i))$). Callers cannot distinguish true SHAP values from heuristic weights.
  - **Ordinal Categorical Encoding:** Categorical features (`merchant_category`, `country_code`, `device_type`) are encoded via linear index division (`idx / (N-1)`), imposing arbitrary metric distance on nominal categories (e.g., `retail`=0.0, `other`=1.0).
  - **Static Background Baseline:** Baseline matrix is hardcoded ($20 \times 10$ zeros with fixed column constants), which does not represent the true empirical transaction distribution.
* **Edge Cases:** Missing feature keys in `txn_dict`; non-numeric string values; empty transaction dict `{}`.
* **Scientific Claim Being Made:** Computes mathematically rigorous SHAP feature importance for PyTorch neural fraud detection models.
* **Appropriate Verification Methodology:** Independent reference verification against first-principles Shapley calculations, property-based testing of efficiency axiom ($\sum \phi_i = \Delta y$).

---

### 2.3 Counterfactual Remediation & Recourse Engine (`generate_counterfactuals`)

* **Component:** `ExplainabilityService.generate_counterfactuals` (`explainability_service.py` L357–L463)
* **Purpose:** Identifies minimal feature modifications to lower an alert's risk score below `target_score` (default 350.0), providing actionable customer recourse under GDPR Art. 22.
* **Mathematical Formulation:**
  - Greedy Heuristic Rule Reduction:
    - Amount remediation: $\Delta S_{\text{amount}} = (S_{\text{orig}} - S_{\text{target}}) \times 0.55$.
    - Country remediation (RU $\to$ US): $\Delta S_{\text{geo}} = -180.0$.
    - Velocity remediation ($14 \to 1 \text{ txn/5min}$): $\Delta S_{\text{vel}} = -140.0$.
    - Merchant remediation ($\text{crypto} \to \text{retail}$): $\Delta S_{\text{merch}} = -110.0$.
  - Remediated Score: $S_{\text{final}} = \max(50.0, \min(S_{\text{current}}, S_{\text{target}} - 10.0))$.
* **Explainability Claim:** Actionable, minimal-change counterfactual explanation paths ($x \to x^*$) guaranteeing score reduction below target threshold.
* **Expected Invariant:**
  1. $S_{\text{remediated}} \le S_{\text{target}}$ iff `is_cleared == True`.
  2. $S_{\text{remediated}} < S_{\text{original}}$ for any flagged alert ($S_{\text{original}} > S_{\text{target}}$).
* **Possible Implementation Risks:**
  - **Rule-Based Heuristic, Not Optimization Search:** Does not solve formal proximity-constrained counterfactual optimization:
    $$\min_{x^*} d(x, x^*) \quad \text{s.t.} \quad f(x^*) \le \tau$$
  - **Hardcoded String Assumptions:** Emits hardcoded original values (`RU`, `crypto_exchange`, `$1,250.00`) regardless of actual input transaction values.
* **Edge Cases:** Alert score already below target score ($S_{\text{orig}} \le 350.0$); alerts with no recognized reason codes.
* **Scientific Claim Being Made:** Provides compliant algorithmic recourse paths satisfying GDPR Article 22(3) right to explanation.
* **Appropriate Verification Methodology:** Property-based invariant testing ($S_{\text{remediated}} \le S_{\text{target}}$), edge-case testing with arbitrary alert reason codes.

---

### 2.4 Deterministic Decision Replay Engine (`replay_inference_audit`)

* **Component:** `ExplainabilityService.replay_inference_audit` (`explainability_service.py` L465–L536)
* **Purpose:** Executes deterministic decision replay for regulatory inference audits by evaluating 9 policy rules and reconstructing risk scores.
* **Mathematical Formulation:**
  - Rule evaluation snapshot: 9 rules ($R_1, \dots, R_9$) with weights $w_i$.
  - Rule contribution: $c_i = w_i \times \text{norm\_val}_i$.
  - Reconstructed score: Set equal to `alert.risk_score`.
  - Audit Match Criterion: `abs(reconstructed_score - alert.risk_score) < 1.0`.
* **Explainability Claim:** Deterministic, reproducible decision replay audit trail for regulatory compliance.
* **Expected Invariant:** `audit_matched == True` for all valid alerts.
* **Possible Implementation Risks:**
  - **Trivial Audit Matching:** `reconstructed_score` is directly assigned from `alert.risk_score` (`reconstructed_score = alert.risk_score`), making `audit_matched` trivially `True` without actually recalculating the score from `evaluated_rules`.
* **Edge Cases:** Missing reason codes; corrupted timestamps.
* **Scientific Claim Being Made:** Enables 100% reproducible inference replay audits for financial regulators.
* **Appropriate Verification Methodology:** Unit testing of `PolicyRuleEvaluation` list construction, verification that reconstructed score independently equals raw score calculation.

---

### 2.5 GNN Embedding Graph Attribution Engine (`explain_gnn_embedding`)

* **Component:** `ExplainabilityService.explain_gnn_embedding` (`explainability_service.py` L538–L628)
* **Purpose:** Computes subgraph and edge attributions explaining GraphSAGE risk embedding classifications.
* **Mathematical Formulation:**
  - Queries `GraphEngine` for 2-hop neighborhood.
  - Edge Weight Assignment:
    $$w_i = \begin{cases} 0.85 - 0.08i & \text{if } \text{rel} \in \{\text{shares\_device}, \text{linked\_alert}\} \\ 0.45 - 0.05i & \text{otherwise} \end{cases}$$
  - Normalized Contribution Percentage:
    $$\text{pct}_i = \frac{w_i}{\sum_j w_j} \times 100\%$$
* **Explainability Claim:** GNNExplainer graph attribution identifying top edge drivers of GNN risk embeddings.
* **Expected Invariant:**
  1. $\sum_{i} \text{contribution\_percentage}_i = 100.0\%$.
  2. Edge contributions ordered by weight descending.
* **Possible Implementation Risks:**
  - **Heuristic Ranking vs True GNNExplainer:** Uses linear positional edge weighting ($0.85 - 0.08i$), NOT PyTorch Geometric's `GNNExplainer` mutual information optimization:
    $$\max_{G_s, M} \text{MI}(Y, G_s) = H(Y) - H(Y \mid G_s, M)$$
  - **Synthetic Fallback Subgraph:** If entity has no graph edges, falls back to hardcoded synthetic nodes (`mule_account_8912`, `suspicious_ip_192.168.4.12`).
* **Edge Cases:** Standalone node with 0 edges; dense node with > 100 edges.
* **Scientific Claim Being Made:** Provides GNN subgraph attributions isolating graph structural risk drivers.
* **Appropriate Verification Methodology:** Property-based testing of percentage normalization ($\sum \text{pct} = 100\%$), testing fallback vs active graph paths.

---

### 2.6 Real-Time Online Feature Attribution (`explain_realtime_score`)

* **Component:** `FastInferenceExplainer.explain_realtime_score` (`realtime_explainer.py` L29–L86)
* **Purpose:** Provides sub-millisecond feature attribution vectors for real-time transaction scoring without SHAP overhead.
* **Logic Formulation:**
  - Rules:
    - High-risk MCC (`crypto_exchange`, `gambling`, `p2p_cash`) $\implies +0.35$ `INCREASES_RISK`.
    - Velocity $\ge 5 \implies +0.25$ `INCREASES_RISK`; $\le 2 \implies +0.10$ `DECREASES_RISK`.
    - Amount $\ge 20000 \implies +0.40$ `INCREASES_RISK`; $< 500 \implies +0.15$ `DECREASES_RISK`.
* **Explainability Claim:** Fast, sub-millisecond online attribution vectors indicating contribution score and directional impact.
* **Expected Invariant:**
  1. Direction $\in \{\text{"INCREASES\_RISK"}, \text{"DECREASES\_RISK"}\}$.
  2. Contribution scores $\in [0.0, 1.0]$.
* **Possible Implementation Risks:**
  - **Incomplete Feature Coverage:** Evaluates only 3 hardcoded features (`merchant_category`, `velocity_1h`, `amount`). Ignores all other model input features.
* **Edge Cases:** Negative transaction amounts; whitespace/case variations in MCC strings (`CRYPTO_EXCHANGE`).
* **Scientific Claim Being Made:** Sub-millisecond online feature attribution for real-time fraud scoring SLAs.
* **Appropriate Verification Methodology:** Unit tests for directional assignments, boundary value testing on amount and velocity thresholds.

---

### 2.7 Async Cached SHAP Engine & Webhook Delivery (`compute_shap` & `explain_async`)

* **Component:** `FastInferenceExplainer.compute_shap`, `explain_async` (`realtime_explainer.py` L88–L186)
* **Purpose:** Asynchronously computes SHAP feature attributions, caches results in Redis (300s TTL) with in-memory fallback, and delivers HTTP webhooks.
* **Operational Logic:**
  - Redis Key: `cfi:shap:{transaction_id}`.
  - TTL: 300 seconds (5 minutes).
  - Webhook delivery via `httpx.post(webhook_url, json=res, timeout=3.0)`.
* **Explainability Claim:** Sub-millisecond cache-hit attribution retrieval with asynchronous background generation.
* **Expected Invariant:**
  1. Redis cache hit returns `source = "REDIS_CACHE"`.
  2. Serialized SHAP values match `RealtimeFeatureAttribution` schema.
* **Possible Implementation Risks:**
  - **Local Memory Leak:** In-memory fallback dictionary `_local_shap_cache` has no TTL or maximum size limit; grows indefinitely if Redis is offline.
  - **Synchronous Execution inside `explain_async`:** `compute_shap` is called synchronously on cache miss within `explain_async`, blocking the thread despite the name `explain_async`.
* **Edge Cases:** Unreachable Redis server; failing/timing-out webhook URL; duplicate transaction IDs.
* **Scientific Claim Being Made:** Scalable, low-latency explainability architecture for high-throughput fraud scoring pipelines.
* **Appropriate Verification Methodology:** Cache hit/miss unit testing, Redis disconnection fallback testing, memory leakage profiling of `_local_shap_cache`.

---

## 3. Inventory Summary & Scientific Verification Roadmap

| # | Component | Primary Algorithm | Claimed Capability | Primary Risk / Defect | Verification Method |
|---|-----------|------------------|-------------------|----------------------|---------------------|
| 1 | `explain_alert` | Post-hoc signal normalization | Multi-signal risk breakdown | Post-hoc score manipulation | Property-based testing & Reference verification |
| 2 | `compute_shap_values` | KernelSHAP & PyTorch wrapper | Additive SHAP feature values | Fallback to heuristic & Ordinal MCC encoding | Reference verification vs SHAP equations |
| 3 | `generate_counterfactuals` | Greedy heuristic reduction | GDPR Art. 22 algorithmic recourse | Hardcoded values & No optimization search | Invariant testing ($S_{\text{remediated}} \le S_{\text{target}}$) |
| 4 | `replay_inference_audit` | Policy rule evaluation | Deterministic inference replay | Trivial score assignment matching | Reconstruction independence test |
| 5 | `explain_gnn_embedding` | Positional edge weighting | GNNExplainer graph attribution | Positional weights vs True MI optimization | Percentage sum invariant ($\sum = 100\%$) |
| 6 | `explain_realtime_score` | Decision tree heuristic | Sub-millisecond online attribution | 3-feature coverage limitation | Boundary value testing |
| 7 | `compute_shap` / `explain_async` | Redis cache & HTTP webhook | Async cached SHAP pipeline | `_local_shap_cache` memory leak | Cache hit/miss & memory profiling |

---

*End of Scientific Verification Inventory — Explainability (XAI) Module*
