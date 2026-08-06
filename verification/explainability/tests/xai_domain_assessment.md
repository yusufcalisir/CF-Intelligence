# Explainable AI (XAI) Domain & Practical Assessment Report

**Subsystem:** Explainability & Interpretable ML (`explainability_service.py`, `realtime_explainer.py`, `value_objects_phase2.py`)  
**Auditor Role:** Senior Researcher in Explainable AI (XAI), Interpretable ML, & Scientific Software Verification  
**Evaluation Standard:** XAI Practical & Human-Centered Evaluation Framework  
**Date:** 2026-08-01  

---

## 1. Executive Summary

This report performs a domain-specific Explainable AI (XAI) assessment of the Explainability subsystem. It analyzes explanation faithfulness, human interpretability, visualization quality, completeness, user misinterpretation risks, and fundamental scientific limitations.

Crucially, this assessment draws an explicit scientific distinction between **correlational feature attribution** (which the system implements) and **causal explanation / guaranteed feature importance** (which require structural causal models and optimization guarantees not present in this codebase).

```
+-----------------------------------------------------------------------------------+
|                        XAI PRACTICAL EVALUATION SUMMARY                           |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  1. Human Interpretability:   HIGH      (Structured reports, ASCII bars, text)    |
|  2. Attribution Consistency:  EXACT     (100% deterministic reproducibility)      |
|  3. Explanation Completeness: PARTIAL   (Real-time covers 3 of 10 features)        |
|  4. Local Faithfulness:       PARTIAL   (KernelSHAP faithful; fallback model-free)|
|  5. Causal Guarantees:        NONE      (Statistical sensitivity, not causal DAG) |
|                                                                                   |
+-----------------------------------------------------------------------------------+
```

---

## 2. Practical XAI Dimension Analysis

### 2.1 Explanation Faithfulness & Local Fidelity

* **KernelSHAP Path:** When PyTorch models and `shap` dependencies are present, KernelSHAP estimates additive feature attributions locally. Additive efficiency holds ($\sum \phi_i = f(x) - \mathbb{E}[f(x)]$), providing high local fidelity around the prediction instance.
* **Analytical Fallback Path:** When `shap` is absent, the linear heuristic $c_i = w_i \times (0.5 + 0.5 \min(1.0, x_i))$ transforms feature values directly without querying neural network parameters (Adebayo Spearman $\rho = 1.0$). While locally sensitive to feature scaling (Top-3 drop AUC = $26.44\%$), it lacks model faithfulness.

---

### 2.2 Human Interpretability & Analyst Usability

* **Visual Progress Bars:** `_format_explanation` converts normalized signal scores into intuitive ASCII visual progress bars (`[██████████░░░░░░░░░░] 55.4%`), allowing fraud investigators to quickly triage high-risk drivers.
* **Structured Analyst Briefs:** Risk signal breakdowns, top feature importances, and historical evidence are aggregated into structured natural language text summaries for regulatory audit logging.
* **Rating:** **HIGH**. The visualization format balances mathematical granularity with investigator readability.

---

### 2.3 Explanation Completeness & Feature Coverage

* **`explain_alert` & `compute_shap_values`:** Full completeness across all 10 tabular transaction features and 9 risk signals.
* **`explain_realtime_score`:** **Incomplete Feature Coverage**. Evaluates only 3 hardcoded features (`merchant_category`, `velocity_1h`, `amount`), omitting 7 input features (`customer_history_score`, `account_age_days`, `chargeback_count`, etc.).

---

### 2.4 Visualization Quality & Reporting Structure

* **Dataclass Value Objects:** `ExplainabilityReport`, `CounterfactualExplanation`, `DecisionReplayReport`, and `GNNExplanationReport` provide well-typed, immutable value objects for API serializability.
* **Multi-Layered Architecture:** Provides high-level summary cards for UI dashboards and granular signal objects for automated compliance systems.

---

## 3. Distinction: Statistical Attribution vs. Causal Explanations

A critical distinction must be communicated to domain experts and users:

```
+-----------------------------------------------------------------------------------+
|            STATISTICAL ATTRIBUTION vs CAUSAL EXPLANATION DISTINCTION              |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  Implemented Capability: Statistical Feature Attribution                          |
|  - Measures how much a feature value contributed to the model's prediction score  |
|    under the current static feature correlation structure.                        |
|  - Formula: phi_i = E[f(X) | X_i = x_i] - E[f(X)]                                 |
|                                                                                   |
|  NON-Implemented Capability: Causal Explanation / Intervention                    |
|  - Does NOT measure what WILL happen if a customer actively changes feature X_i   |
|    in the physical world (Interventional do-calculus: E[Y | do(X_i = v)]).         |
|  - Does NOT model structural causal graphs (DAGs) or confounding variables.      |
|                                                                                   |
+-----------------------------------------------------------------------------------+
```

---

## 4. Possible User Misinterpretation Risks & Mitigation

| Misinterpretation Risk | Analyst Misconception | Technical Reality | Recommended Mitigation Wording |
|:---|:---|:---|:---|
| **Causal Recourse Assumption** | *"If the customer lowers transaction amount to \$50, fraud risk will disappear."* | Counterfactuals use rule heuristics, not causal DAG interventions. | *"Recourse suggestions reflect heuristic score reductions, not causal guarantees."* |
| **SHAP Fallback Misattribution** | *"These SHAP values reflect PyTorch neural network layer weights."* | Fallback heuristic computes attributions directly from input values when `shap` is absent. | *"Label fallback output as 'Linear Heuristic Importance' rather than 'SHAP Value'."* |
| **Graph Driver Misinterpretation** | *"This edge was proven to be the primary structural cause of GNN fraud embedding."* | Edge ranking uses positional list indexing ($0.85 - 0.08i$), not mutual information. | *"Edge contributions represent heuristic neighborhood ranking."* |
| **Post-Hoc Score Breakdown** | *"These 9 risk signals were evaluated independently before calculating final score."* | Signal breakdown scores are computed post-hoc *from* the final alert risk score. | *"Risk breakdown visualizes post-hoc score distribution."* |

---

## 5. Remaining Scientific Limitations

1. **Absence of True GNNExplainer:** GNN attributions rely on positional edge type heuristics ($0.85 - 0.08i$) rather than mutual information optimization ($\max \text{MI}(Y, G_s)$).
2. **Static Background Baseline:** KernelSHAP uses a hardcoded $20 \times 10$ synthetic matrix rather than empirical background transaction samples.
3. **Ordinal Encoding of Nominal Categoricals:** Nominal categories (`merchant_category`, `country_code`) are encoded via linear index division (`idx / (N-1)`), introducing artificial metric distances.
4. **Trivial Decision Replay Matching:** `replay_inference_audit` sets `reconstructed_score = alert.risk_score` directly, making `audit_matched` trivially `True`.

---

*End of Explainable AI (XAI) Domain & Practical Assessment Report*
