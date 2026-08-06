# Scientific Claim Classification Review — Explainability (XAI) Module

**Subsystem:** Explainability & Interpretable ML (`explainability_service.py`, `realtime_explainer.py`, `value_objects_phase2.py`)  
**Auditor Role:** Senior Researcher in Explainable AI (XAI), Interpretable ML, & Scientific Software Verification  
**Evaluation Standard:** Peer-Reviewed XAI Scientific Audit  
**Date:** 2026-07-31  

---

## 1. Executive Summary

This report performs a critical scientific review of every explainability, attribution, visualization, and interpretability claim made in the codebase, documentation, and user interfaces of the Explainability subsystem.

Each claim is rigorously evaluated against established XAI theoretical standards (Shapley axioms, GNNExplainer mutual information formulation, counterfactual proximity bounds, explanation fidelity metrics, and Grad-CAM gradient activation rules) and classified into one of three categories:
- **SUPPORTED:** Mathematically sound, fully implemented, and empirically verified.
- **PARTIALLY SUPPORTED:** Implemented via heuristics or approximations; operational bounds are weaker than claimed.
- **UNSUPPORTED:** Not implemented, mathematically inaccurate, or reliant on hardcoded synthetic placeholders.

### Classification Summary Table

| Claim Category | Tested Claim | Classification | Primary Scientific Defect | Recommended Scientifically Accurate Wording |
|:---|:---|:---:|:---|:---|
| **Feature Attribution** | *"Computes KernelSHAP feature attributions for PyTorch models"* | **PARTIALLY SUPPORTED** | Silent fallback to linear heuristic; ordinal encoding of nominal categories; static synthetic baseline | *"Computes KernelSHAP feature attributions for tabular PyTorch predictions using a synthetic baseline when dependencies exist, falling back to a linear heuristic otherwise."* |
| **Graph Attribution** | *"Provides GNNExplainer graph attributions isolating risk drivers"* | **UNSUPPORTED** | No mutual information optimization; uses positional edge list weights ($0.85-0.08i$); synthetic fallback | *"Provides heuristic edge-type ranking over 2-hop entity neighborhoods; does not execute GNNExplainer mutual-information optimization."* |
| **Algorithmic Recourse** | *"Provides minimal-change counterfactual explanations under GDPR Art. 22"* | **PARTIALLY SUPPORTED** | Hardcoded greedy rules; no proximity-constrained optimization ($\min d(x,x^*)$); fixed string templates | *"Generates rule-based heuristic recourse suggestions for high-risk alerts; does not perform optimization-based minimal-distance counterfactual search."* |
| **Inference Audit** | *"Executes deterministic decision replay for regulatory audits"* | **PARTIALLY SUPPORTED** | Trivial score matching (`reconstructed = alert.risk_score`) | *"Logs policy rule evaluations and metadata snapshots for audit tracking; independent score reconstruction verification is partially implemented."* |
| **Real-Time Scoring** | *"Sub-millisecond real-time feature attributions"* | **PARTIALLY SUPPORTED** | Evaluates only 3 hardcoded features (`mcc`, `velocity`, `amount`) | *"Provides low-latency rule-based risk direction vectors for 3 primary transaction features."* |
| **Risk Breakdown** | *"Multi-level risk score breakdown explaining why flagged"* | **PARTIALLY SUPPORTED** | Post-hoc score normalization calculated *from* final score $B = \text{score}/1000$ | *"Deconstructs final alert risk scores into proportional visual signal bars using post-hoc score normalization."* |
| **Explanation Fidelity** | *"Guarantees explanation fidelity and local accuracy"* | **UNSUPPORTED** | No quantitative fidelity metrics (Infidelity, Monotonicity) computed | *"Explanation fidelity metrics under input perturbation are unmeasured and not guaranteed."* |
| **Explanation Stability** | *"Provides robust and stable feature attributions"* | **UNSUPPORTED** | No Lipschitz continuity or stability metrics $\|E(x) - E(x')\|$ measured | *"Attribution stability under input noise is unmeasured and not guaranteed."* |
| **Grad-CAM Localization**| *"Grad-CAM gradient localization for GNN/Neural models"* | **UNSUPPORTED** | Zero gradient computation ($\frac{\partial y}{\partial A}$) implemented | *"Grad-CAM gradient-based localization is not implemented."* |

---

## 2. Detailed Scientific Claim Evaluations

### 2.1 Feature Importance & SHAP Values

#### Claimed Capability
*"Computes KernelSHAP feature attributions for PyTorch neural fraud detection models."*

#### Scientific Assessment: PARTIALLY SUPPORTED
1. **Implementation Reality:** When `shap` is installed and the PyTorch model file `global_model.pt` is present, `KernelExplainer` is called. However:
   - **Silent Heuristic Fallback:** If `shap` is missing or PyTorch loading fails, `compute_shap_values` silently executes a linear heuristic:
     $$\text{contribution} = w_i \times (0.5 + 0.5 \min(1.0, x_i))$$
     The caller receives a valid-looking list of dictionary feature contributions with no indication that SHAP was bypassed.
   - **Invalid Ordinal Encoding of Nominal Categoricals:** Nominal categories (`merchant_category`, `country_code`, `device_type`) are encoded as $x_i = \frac{\text{idx}}{N-1}$. This imposes an arbitrary metric topology (`retail`=0.0, `online_retail`=0.14, `other`=1.0) on unordered nominal sets, corrupting KernelSHAP kernel weight estimations.
   - **Static Synthetic Background Matrix:** KernelSHAP requires a background dataset representing the empirical distribution $\mathbb{E}[f(X)]$. The code uses a hardcoded $20 \times 10$ synthetic matrix with arbitrary fixed values (column 0 = 0.05, column 7 = 0.90), causing expectation bias.

#### Recommended Wording
> *"Computes KernelSHAP feature attributions for tabular PyTorch model predictions using a synthetic background baseline when SHAP dependencies are loaded, falling back to an analytical linear heuristic otherwise."*

---

### 2.2 GNN Attribution & GNNExplainer

#### Claimed Capability
*"Provides GNNExplainer graph attribution isolating structural risk drivers."*

#### Scientific Assessment: UNSUPPORTED
1. **Implementation Reality:** The function `explain_gnn_embedding` claims to compute GNNExplainer attributions. However:
   - **No Mutual Information Mask Optimization:** True GNNExplainer (Ying et al., NeurIPS 2019) optimizes an edge mask $M$ and feature mask $F$ to maximize mutual information:
     $$\max_{G_s, M} \text{MI}(Y, G_s) = H(Y) - H(Y \mid G_s, M)$$
   - **Positional Linear Weight Assignment:** The codebase assigns edge weights based on positional index in `subgraph.edges`:
     $$w_i = \begin{cases} 0.85 - 0.08i & \text{if rel} \in \{\text{shares\_device}, \text{linked\_alert}\} \\ 0.45 - 0.05i & \text{otherwise} \end{cases}$$
     This measures edge position in an arbitrary query list, NOT graph structural contribution to the GNN embedding.
   - **Hardcoded Fallback Entities:** If the entity has no graph edges, the function returns hardcoded synthetic nodes: `mule_account_8912` (54.6%), `suspicious_ip_192.168.4.12` (30.0%), `linked_alert_alt_401` (15.4%).

#### Recommended Wording
> *"Provides heuristic edge-type ranking over 2-hop entity neighborhoods; does not execute GNNExplainer mutual-information optimization."*

---

### 2.3 Counterfactual Explanations & Algorithmic Recourse

#### Claimed Capability
*"Generates actionable counterfactual explanation paths under GDPR Article 22."*

#### Scientific Assessment: PARTIALLY SUPPORTED
1. **Implementation Reality:** `generate_counterfactuals` produces recourse descriptions, but:
   - **Rule Heuristic, Not Optimization Search:** It does not solve a formal proximity-constrained optimization problem:
     $$\min_{x^*} d(x, x^*) \quad \text{s.t.} \quad f(x^*) \le \tau$$
   - **Hardcoded Placeholder String Formatting:** Emits hardcoded string values regardless of input transaction data:
     - `original_value = "RU"`, `remediated_value = "US"` (even if original country was not RU).
     - `original_value = "crypto_exchange"`, `remediated_value = "online_retail"`.
     - `original_value = "$1,250.00"`, `remediated_value = "$45.00"`.
   - **Artificial Clamping:** Remediated score is artificially forced below target score via `min(current_score, target_score - 10.0)`, guaranteeing `is_cleared = True` by construction rather than model evaluation.

#### Recommended Wording
> *"Generates rule-based heuristic recourse suggestions for high-risk alerts; does not perform optimization-based minimal-distance counterfactual search."*

---

### 2.4 Decision Replay & Inference Auditing

#### Claimed Capability
*"Executes deterministic decision replay for regulatory inference audits."*

#### Scientific Assessment: PARTIALLY SUPPORTED
1. **Implementation Reality:** `replay_inference_audit` evaluates 9 policy rules and logs metadata snapshots. However:
   - **Trivial Score Assignment:** `reconstructed_score` is directly assigned from `alert.risk_score` (`reconstructed_score = alert.risk_score`), rendering `audit_matched = abs(reconstructed_score - alert.risk_score) < 1.0` trivially `True` without verifying independent score reconstruction.

#### Recommended Wording
> *"Logs policy rule evaluations and metadata snapshots for audit tracking; independent score reconstruction verification is partially implemented."*

---

### 2.5 Explanation Fidelity & Stability

#### Claimed Capability
*"Guarantees explanation fidelity and local attribution stability."*

#### Scientific Assessment: UNSUPPORTED
1. **Implementation Reality:**
   - **No Fidelity Metrics:** Neither Explanation Infidelity (Yeh et al., 2019) nor Monotonicity ($f(x \setminus \{i\}) \le f(x)$) is computed.
   - **No Stability Metrics:** Attribution stability under input perturbation ($\|E(x) - E(x')\| / \|x - x'\|$) is unmeasured. Small noise in continuous inputs can produce jump discontinuities in the fallback heuristic.

#### Recommended Wording
> *"Explanation fidelity and stability metrics under input perturbations are unmeasured and not guaranteed."*

---

### 2.6 Grad-CAM Localization

#### Claimed Capability
*"Grad-CAM gradient localization for GNN and neural network models."*

#### Scientific Assessment: UNSUPPORTED
1. **Implementation Reality:** Grad-CAM (Selvaraju et al., ICCV 2017) requires computing target gradients with respect to intermediate feature activation maps:
   $$\alpha_k^c = \frac{1}{Z} \sum_i \sum_j \frac{\partial y^c}{\partial A_{i,j}^k}$$
   Zero gradient activation code is present in the Explainability module. Grad-CAM is completely unrepresented.

#### Recommended Wording
> *"Grad-CAM gradient-based localization is not implemented."*

---

## 3. Summary of Weakening Requirements for README & Documentation

To ensure publication-quality scientific integrity, the following claims must be weakened:

| Original Claim | Required Revision |
|:---|:---|
| *"Uses KernelSHAP to explain model predictions"* | Change to: *"Computes KernelSHAP feature attributions when SHAP is loaded, using a synthetic background baseline, or falls back to an analytical heuristic."* |
| *"GNNExplainer isolates graph structural fraud drivers"* | Change to: *"Ranks 2-hop neighborhood relationships by edge type using positional heuristics."* |
| *"Generates GDPR Art. 22 compliant counterfactual explanations"* | Change to: *"Provides rule-based recourse suggestions for high-risk alerts."* |
| *"Guarantees deterministic decision replay audits"* | Change to: *"Logs policy rule evaluations and snapshots for inference audit trails."* |
| *"Sub-millisecond real-time SHAP attributions"* | Change to: *"Sub-millisecond directional risk vectors for 3 core transaction features."* |

---

*End of Scientific Claim Classification Review — Explainability (XAI) Module*
