# Scientific Explanation Quality & Faithfulness Evaluation Report

**Subsystem:** Explainability & Interpretable ML (`explainability_service.py`, `realtime_explainer.py`)  
**Evaluation Script:** `scratch/explainability_quality_evaluation.py`  
**Raw Results:** `scratch/explainability_quality_results.json`  
**Evaluation Date:** 2026-08-01  
**Evaluation Standard:** Peer-Reviewed XAI Quality & Faithfulness Protocol  

---

## 1. Executive Summary

This report presents a quantitative scientific evaluation of the explanation quality, faithfulness, stability, reproducibility, and potential misleading risks of the Explainability subsystem. Crucially, the analysis distinguishes **implementation correctness** (whether code runs without bugs) from **explanation usefulness** (whether attributions provide true, faithful insights into model behavior).

### Key Quantitative Findings

| Dimension | Evaluated Metric | Quantitative Result | Target / Threshold | Quality Assessment |
|:---|:---|:---:|:---:|:---:|
| **Sanity Check (Adebayo)** | Spearman $\rho$ on Random Weights | **0.999999** | $\rho \to 0.0$ | ❌ **FAILED (Model Insensitive)** |
| **Explanation Faithfulness** | Top-3 Feature Deletion Drop AUC | **26.44% Drop** | $> 15.0\%$ | ✅ **PASS (Locally Linear)** |
| **Attribution Stability** | Max Lipschitz Ratio ($\sigma=0.05$) | **15.25** | $< 5.0$ | ⚠️ **PARTIAL (Localized Spikes)** |
| **Reproducibility** | Difference on Identical Inputs | **0.000000** | $0.0$ | ✅ **EXACT (Deterministic)** |
| **Implementation vs Usefulness** | Algorithmic Integrity vs Utility | **Correct Code / Post-Hoc Heuristic** | High Utility | ⚠️ **MISLEADING RISK** |

---

## 2. Detailed Quality & Sanity Evaluations

### 2.1 Adebayo Model Randomization Sanity Check (Adebayo et al., NeurIPS 2018)

#### Purpose & Methodology
Evaluates whether generated feature attributions depend on the trained model parameters or merely act as a static feature transformation. Feature attributions were computed for 20 transaction samples across normal vs weight-randomized model states, measuring Spearman rank correlation ($\rho$).

#### Quantitative Result
- **Mean Spearman Rank Correlation ($\rho$):** $\mathbf{0.9999999999999998} \approx 1.0$.

#### Scientific Interpretation
- **CRITICAL SANITY CHECK FAILURE:** A Spearman correlation of $\rho \approx 1.0$ under randomized model weights proves that when optional `shap` dependencies are absent (the default fallback state), the analytical fallback explainer is **100% insensitive to model parameters**.
- The fallback heuristic $c_i = w_i \times (0.5 + 0.5 \min(1.0, x_i))$ transforms input feature magnitudes directly without querying neural network weights. It functions as an input feature scaler rather than a model explainer.

---

### 2.2 Explanation Faithfulness (Feature Deletion Drop AUC)

#### Purpose & Methodology
Measures whether masking top-attributed features produces a corresponding drop in prediction scores. Top 3 attributed features were set to baseline values ($0.0$), measuring score reduction ratio $\frac{S_{\text{orig}} - S_{\text{masked}}}{S_{\text{orig}}}$.

#### Quantitative Result
- **Mean Top-3 Feature Drop Ratio:** $\mathbf{26.44\%}$.

#### Scientific Interpretation
- **PASS:** Zeroing the top 3 attributed features reduces prediction scores by an average of $26.44\%$, demonstrating reasonable local linear sensitivity.

---

### 2.3 Attribution Stability & Sensitivity to Perturbations

#### Purpose & Methodology
Evaluates local Lipschitz stability under 5% Gaussian noise injection $\epsilon \sim \mathcal{N}(0, 0.05^2 I)$ into numerical features (`transaction_amount`, `velocity`, `merchant_risk_score`). Measures Lipschitz ratio:
$$L(x) = \frac{\|E(x) - E(x')\|_2}{\|x - x'\|_2}$$

#### Quantitative Result
- **Mean Lipschitz Ratio:** $1.67$.
- **Max Lipschitz Ratio:** $\mathbf{15.25}$ (Exceeds stability threshold $5.0$).

#### Scientific Interpretation
- **PARTIALLY STABLE:** While average perturbation sensitivity is low ($L_{\text{mean}} = 1.67$), localized sensitivity spikes up to $L_{\text{max}} = 15.25$ occur near piecewise boundary thresholds (e.g. `velocity >= 5` or `amount >= 20000.0` in `FastInferenceExplainer`).

---

### 2.4 Reproducibility & Consistency

#### Quantitative Result
- **Max Difference on Identical Inputs:** $\mathbf{0.000000e+00}$.

#### Scientific Interpretation
- **EXACT:** All explanation algorithms produce 100% deterministic, reproducible attribution vectors when executed repeatedly on identical feature inputs.

---

## 3. Implementation Correctness vs. Explanation Usefulness

A critical distinction must be maintained between **code execution correctness** and **scientific explanation utility**:

```
+-----------------------------------------------------------------------------------+
|               IMPLEMENTATION CORRECTNESS vs EXPLANATION USEFULNESS                |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  1. Implementation Correctness (EXCELLENT):                                      |
|     - Code executes cleanly with float64 precision (0.0 error).                  |
|     - Output JSON payloads strictly conform to Pydantic schemas.                 |
|     - 100% deterministic reproducibility across identical inputs.                 |
|                                                                                   |
|  2. Explanation Usefulness (LIMITED / POTENTIALLY MISLEADING):                    |
|     - Fallback SHAP heuristic is model-insensitive (Adebayo Spearman rho = 1.0).   |
|     - Post-hoc alert risk breakdown calculates signal scores FROM final score.     |
|     - GNNExplainer relies on linear positional edge index weights (0.85 - 0.08i).  |
|                                                                                   |
+-----------------------------------------------------------------------------------+
```

---

## 4. Identified Limitations & Misleading Explanation Risks

1. **Misleading Fallback SHAP Values:** When `shap` is absent, the system returns feature attributions labeled as "SHAP values" that are actually generated by an un-model-trained linear heuristic.
2. **Circular Risk Score Breakdown:** In `explain_alert`, signal values $v_i = B \cdot c_i$ are computed *downstream* from the alert's final `risk_score` ($B = \text{risk\_score}/1000$). Investigators viewing the breakdown bar chart are seeing a visual redistribution of the final score rather than independent forward signal inputs.
3. **Pseudo-GNN Attributions:** `explain_gnn_embedding` presents edge percentage contributions as "GNNExplainer" results, but ranks edges by positional array index rather than structural graph importance.

---

## 5. Scientific Recommendations for Production Upgrade

1. **Explicit Explainer Source Flagging:** Modify `ExplainabilityReport` to return `explanation_method: "KERNEL_SHAP"` vs `explanation_method: "LINEAR_HEURISTIC_FALLBACK"`.
2. **Forward Signal Normalization:** Refactor `explain_alert` to calculate risk signals $v_i$ forward from raw feature inputs before aggregating into `risk_score`.
3. **Integration of PyTorch Geometric GNNExplainer:** Replace positional edge ranking with PyTorch Geometric's `torch_geometric.explain.GNNExplainer` for genuine mutual-information graph attribution.

---

*End of Scientific Explanation Quality & Faithfulness Evaluation Report*
