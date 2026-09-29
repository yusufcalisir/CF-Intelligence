# Core Algorithm Specifications & Mathematical Foundations
## Privacy-Preserving Collaborative Financial Crime Intelligence Platform (CF-Intelligence)

This directory contains the authoritative mathematical, algorithmic, and implementation specifications for the core machine learning, privacy, security, and graph intelligence algorithms employed across CF-Intelligence.

---

### Algorithmic Architecture Index

| Algorithm | Primary Reference | Domain / Problem Solved | Implementation Module | Test Harness |
|:---|:---|:---|:---|:---|
| **[FedAvg](file:///docs/algorithms/fedavg.md)** | McMahan et al., 2017 | Distributed parameter optimization | `backend/app/application/services/fl_engine.py` | `test_fl_engine.py` |
| **[FedProx](file:///docs/algorithms/fedprox.md)** | Li et al., 2020 | Non-IID label skew & straggler robustness | `backend/app/application/services/fl_engine.py` | `test_fl_engine.py` |
| **[SCAFFOLD](file:///docs/algorithms/scaffold.md)** | Karimireddy et al., 2020 | Client drift correction via control variates | `backend/app/application/services/fl_engine.py` | `test_fl_engine.py` |
| **[Differential Privacy & RDP](file:///docs/algorithms/differential_privacy.md)** | Mironov, 2017; Abadi et al., 2016 | Information bounding & membership defense | `backend/app/application/services/privacy_service.py` | `test_privacy_service.py` |
| **[Curve25519 SecAgg](file:///docs/algorithms/secure_aggregation.md)** | Bonawitz et al., 2017 | Pairwise zero-sum update blinding | `backend/app/infrastructure/security/p2p_secagg_driver.py` | `test_p2p_secagg_driver.py` |
| **[Byzantine Robustness (Krum/Bulyan)](file:///docs/algorithms/byzantine_resilience.md)** | Blanchard et al., 2017; Guerraoui et al., 2018 | Poisoned / adversarial weight filtering | `backend/app/domain/byzantine_defense.py` | `test_byzantine_defense_branches.py` |
| **[GraphSAGE](file:///docs/algorithms/graphsage.md)** | Hamilton et al., 2017 | Inductive multi-hop transaction graph embeddings | `backend/app/application/services/graph_embedding_model.py` | `test_graph_embedding.py` |
| **[MinHash LSH Fuzzy PSI](file:///docs/algorithms/minhash_lsh.md)** | Broder, 1997 | Cross-bank entity matching without raw identifier exposure | `backend/app/domain/minhash_lsh.py`, `graph_engine.py` | `test_minhash_psi.py`, `test_graph_engine.py` |
| **[SHAP KernelExplainer](file:///docs/algorithms/shap_explainability.md)** | Lundberg & Lee, 2017 | Local cooperative game theory feature attribution | `backend/app/application/services/explainability_service.py` | `test_explainability_service.py` |

---

### Design Invariants Across All Implementations
1. **Zero Raw PII Transmission**: No raw customer names, account numbers, or plain transaction amounts leave local banking nodes.
2. **Deterministic Seed Control**: All randomized mechanisms (Gaussian DP, Shamir secret sharing, stochastic mini-batching) support explicit seed initialization for reproducible testing.
3. **Explicit Threat Boundaries**: Every algorithm document details the specific mathematical adversarial budget ($f < \frac{n-2}{2}$, $\epsilon \le \epsilon_{\max}$, $\delta = 10^{-5}$) under which guarantees hold.

---

### Mathematical & Empirical Evaluation Metric Standards

All quantitative evaluations and model risk validations across these algorithms strictly adhere to the unified standard defined in **[`docs/METRICS.md`](../METRICS.md)**:

| Metric Category | Standard Metrics | Formal Mathematical Target | Applicable Governance Framework |
| :--- | :--- | :---: | :--- |
| **Imbalance Detection** | $\operatorname{PR-AUC}$ (Average Precision), $\operatorname{Recall@0.1\%FPR}$ | $\operatorname{PR-AUC} \ge 0.75$, $\operatorname{Recall@0.1\%FPR} \ge 0.60$ | Federal Reserve SR 11-7 / OCC 2011-12 |
| **Probability Calibration** | $\operatorname{ECE}$ ($M=10$ bins), $\operatorname{BS}$ (Brier Score) | $\operatorname{ECE} \le 0.030$, $\operatorname{BS} \le 0.020$ | EU AI Act Art. 15 (Accuracy & Robustness) |
| **Population Drift** | $\operatorname{PSI}$ (Traffic-light matrix), $\operatorname{JSD}$ (Symmetric KL) | $\operatorname{PSI} < 0.10$ (stable), $\operatorname{JSD} \le 0.15$ | Basel Committee BCBS 32 Model Risk |
| **Economic & Fairness** | $\mathcal{L}_{\mathrm{financial}}$ ($C_{\mathrm{FN}}=850$, $C_{\mathrm{FP}}=25$), $\operatorname{DIR}$ | $\theta^*_{\mathrm{cost}} = \arg\min \mathcal{L}$, $0.80 \le \operatorname{DIR} \le 1.25$ | ECOA Reg B / EEOC 80% Rule |

