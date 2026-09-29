# Unified Scientific Metric Definition Standard & Evaluation Glossary

**Document Reference:** `CFI-SPEC-METRICS-2026-V1`  
**Applicable Governance Frameworks:** Federal Reserve SR 11-7 / OCC 2011-12 (Model Risk Management), EU Artificial Intelligence Act (Regulation (EU) 2024/1689) Article 15 (Accuracy, Robustness and Cybersecurity), Basel Committee on Banking Supervision (BCBS) Working Paper 32.

---

## 1. Overview & Epistemological Purpose

In enterprise federated machine learning and regulatory model risk validation, evaluation metrics must be mathematically unambiguous, strictly bounded, and suited to the underlying financial data distribution. 

Financial crime and Anti-Money Laundering (AML) transaction datasets exhibit **extreme class imbalance** (typically $\le 0.15\%$ fraud prevalence). Under extreme imbalance, standard machine learning metrics such as Accuracy or uncalibrated ROC-AUC produce dangerously misleading assessments:
- An uncalibrated trivial classifier predicting the majority class achieves $99.85\%$ accuracy while detecting **zero fraud**.
- Receiver Operating Characteristic (ROC-AUC) can remain superficially high ($> 0.95$) even when thousands of false alerts overwhelm human investigator queues, because the vast true negative denominator artificially suppresses the False Positive Rate ($\operatorname{FPR} = \frac{\mathrm{FP}}{\mathrm{FP} + \mathrm{TN}}$).

This specification establishes the authoritative mathematical formulations, boundary constraints, operational thresholds, and regulatory mappings for all quantitative metrics reported across CF-Intelligence.

---

## 2. Core Classification & Detection Performance Metrics

### 2.1 Precision-Recall Area Under Curve ($\operatorname{PR-AUC}$ / Average Precision)

#### Mathematical Definition
Precision-Recall Area Under the Curve evaluates the trade-off between positive predictive value (Precision) and sensitivity (Recall) across all classification thresholds $\theta \in [0, 1]$:

$$
\operatorname{PR-AUC} = \int_{0}^{1} p(r) \, dr \approx \sum_{k=1}^{K} (r_k - r_{k-1}) \cdot p(r_k)
$$

where Precision $p(r)$ and Recall $r$ are parameterized by threshold $\theta$:

$$
p(\theta) = \frac{\mathrm{TP}(\theta)}{\mathrm{TP}(\theta) + \mathrm{FP}(\theta)}, \quad r(\theta) = \frac{\mathrm{TP}(\theta)}{\mathrm{TP}(\theta) + \mathrm{FN}(\theta)}
$$

In finite sample implementations, CF-Intelligence uses the non-interpolated Average Precision ($\operatorname{AP}$) formulation:

$$
\operatorname{AP} = \sum_{n=1}^{N} (R_n - R_{n-1}) \cdot P_n
$$

where $P_n$ and $R_n$ are the precision and recall at the $n$-th operational threshold.

#### Operational Interpretation & Target Bounds
- **Range:** $\operatorname{PR-AUC} \in [\pi, 1.0]$, where $\pi = \frac{P}{P + N}$ is the baseline fraud prevalence (random guessing baseline).
- **Target Performance:**
  - $\operatorname{PR-AUC} \ge 0.75$: High-efficacy production champion candidate.
  - $\operatorname{PR-AUC} \in [0.40, 0.74]$: Acceptable multi-bank federated detector under non-IID skew ($\alpha \le 0.50$).
  - $\operatorname{PR-AUC} < 0.20$: Sub-baseline / non-converged local silo.

---

### 2.2 Receiver Operating Characteristic Area Under Curve ($\operatorname{ROC-AUC}$)

#### Mathematical Definition
The ROC curve traces the True Positive Rate ($\operatorname{TPR}$) against the False Positive Rate ($\operatorname{FPR}$) across all continuous classification thresholds:

$$
\operatorname{ROC-AUC} = \int_{0}^{1} \operatorname{TPR}(\operatorname{FPR}^{-1}(t)) \, dt
$$

Probabilistically, $\operatorname{ROC-AUC}$ equals the Wilcoxon-Mann-Whitney concordance probability that a randomly chosen fraudulent transaction $x^+$ receives a strictly higher predicted risk score than a randomly chosen legitimate transaction $x^-$:

$$
\operatorname{ROC-AUC} = P\left(\hat{s}(x^+) > \hat{s}(x^-) \mid y(x^+) = 1, y(x^-) = 0\right)
$$

In discrete finite samples:

$$
\operatorname{ROC-AUC} = \frac{1}{N^+ \cdot N^-} \sum_{i: y_i = 1} \sum_{j: y_j = 0} \left( \mathbb{I}(\hat{s}_i > \hat{s}_j) + \frac{1}{2} \mathbb{I}(\hat{s}_i = \hat{s}_j) \right)
$$

#### Operational Interpretation & Target Bounds
- **Range:** $\operatorname{ROC-AUC} \in [0.0, 1.0]$ (random baseline: $0.50$).
- **Regulatory Caveat (SR 11-7):** Must **never** be reported as the sole validation metric on imbalanced financial data. Must always be accompanied by $\operatorname{PR-AUC}$ and $\operatorname{Recall@FPR}$.

---

### 2.3 Recall at Fixed False Positive Rate ($\operatorname{Recall@FPR}$)

#### Mathematical Definition
In production anti-fraud operations, human compliance investigator capacity is strictly bounded. Models cannot be evaluated at arbitrary probability thresholds (e.g. $\theta = 0.50$). Instead, the operational threshold $\theta^*$ is calibrated to guarantee that false alarms do not exceed a statutory ceiling $\alpha_{\mathrm{target}}$:

$$
\theta^* = \inf \left\{ \theta \in [0, 1] : \operatorname{FPR}(\theta) \le \alpha_{\mathrm{target}} \right\}
$$

where:

$$
\operatorname{FPR}(\theta) = \frac{\sum_{j: y_j = 0} \mathbb{I}(\hat{s}_j \ge \theta)}{\sum_{j} \mathbb{I}(y_j = 0)}
$$

$\operatorname{Recall@FPR}$ is the sensitivity achieved at this operational threshold:

$$
\operatorname{Recall@}\alpha_{\mathrm{target}} = \operatorname{TPR}(\theta^*) = \frac{\sum_{i: y_i = 1} \mathbb{I}(\hat{s}_i \ge \theta^*)}{\sum_{i} \mathbb{I}(y_i = 1)}
$$

#### Standard Banking Operating Points
- **$\operatorname{Recall@0.01\%FPR}$ ($\alpha = 0.0001$):** Ultra-strict tier. Permits at most 1 false alarm per $10{,}000$ legitimate transactions. Target: $\ge 0.50$ ($50\%$ fraud caught before friction).
- **$\operatorname{Recall@0.1\%FPR}$ ($\alpha = 0.0010$):** Standard automated review tier. Permits at most 1 false alarm per $1{,}000$ legitimate transactions. Target: $\ge 0.60$.

---

## 3. Probability Calibration & Proper Scoring Rules

### 3.1 Expected Calibration Error ($\operatorname{ECE}$)

#### Mathematical Definition
Expected Calibration Error quantifies the discrepancy between model predicted probabilities and empirical ground-truth event frequencies. Predictions $\hat{p}_i \in [0, 1]$ are partitioned into $M$ equal-width bins $B_1, B_2, \dots, B_M \subset [0, 1]$ (default $M=10$):

$$
B_m = \left( \frac{m-1}{M}, \frac{m}{M} \right]
$$

$\operatorname{ECE}$ is the weighted average absolute difference between empirical accuracy and average confidence across all bins:

$$
\operatorname{ECE} = \sum_{m=1}^{M} \frac{|B_m|}{N} \left| \operatorname{acc}(B_m) - \operatorname{conf}(B_m) \right|
$$

where:

$$
\operatorname{conf}(B_m) = \frac{1}{|B_m|} \sum_{i \in B_m} \hat{p}_i, \quad \operatorname{acc}(B_m) = \frac{1}{|B_m|} \sum_{i \in B_m} y_i
$$

#### Operational Interpretation
- **Range:** $\operatorname{ECE} \in [0.0, 1.0]$.
- **Target:** $\operatorname{ECE} \le 0.030$ ($3.0\%$).
- **Implication:** Prevents overconfident deep learning models from distorting downstream Bayesian risk aggregators or automated payment blocking rules.

---

### 3.2 Brier Score ($\operatorname{BS}$)

#### Mathematical Definition
The Brier Score is a strictly proper scoring rule quantifying the mean squared error of probabilistic forecasts:

$$
\operatorname{BS} = \frac{1}{N} \sum_{i=1}^{N} (\hat{p}_i - y_i)^2
$$

Under Murphy's decomposition (Murphy, 1973), the Brier Score decomposes into three orthogonal components:

$$
\operatorname{BS} = \operatorname{Reliability} - \operatorname{Resolution} + \operatorname{Uncertainty}
$$

where:

$$
\operatorname{Reliability} = \sum_{m=1}^{M} \frac{|B_m|}{N} \left( \operatorname{conf}(B_m) - \operatorname{acc}(B_m) \right)^2
$$

$$
\operatorname{Resolution} = \sum_{m=1}^{M} \frac{|B_m|}{N} \left( \operatorname{acc}(B_m) - \bar{y} \right)^2
$$

$$
\operatorname{Uncertainty} = \bar{y}(1 - \bar{y}), \quad \bar{y} = \frac{1}{N}\sum_{i=1}^N y_i
$$

#### Operational Interpretation
- **Range:** $\operatorname{BS} \in [0.0, 1.0]$ (lower is strictly superior).
- **Target:** $\operatorname{BS} \le 0.020$ on low-prevalence financial streams.

---

## 4. Distribution Drift & Population Stability Metrics

### 4.1 Population Stability Index ($\operatorname{PSI}$)

#### Mathematical Definition
The Population Stability Index measures the shift between a baseline reference distribution $P$ (e.g. historical baseline or training partition) and an actual production distribution $Q$ partitioned across $B$ bins (default deciles $B=10$):

$$
\operatorname{PSI} = \sum_{b=1}^{B} (q_b - p_b) \cdot \ln\left( \frac{q_b + \epsilon_{\mathrm{smooth}}}{p_b + \epsilon_{\mathrm{smooth}}} \right)
$$

where $p_b$ is the proportion of reference samples in bin $b$, $q_b$ is the proportion of actual samples in bin $b$, and $\epsilon_{\mathrm{smooth}} = 10^{-7}$ prevents numerical division by zero.

#### Regulatory Traffic-Light Action Matrix (Federal Reserve SR 11-7 / OCC)

| $\operatorname{PSI}$ Value | Drift Classification | System Action & Governance Response |
| :--- | :--- | :--- |
| $\operatorname{PSI} < 0.10$ | **Stable / No Drift** | Model operates within validated baseline. No action required. |
| $0.10 \le \operatorname{PSI} \le 0.25$ | **Moderate Drift** | Warning issued. Retraining queue enters `MONITORING_ALERT` status. |
| $\operatorname{PSI} > 0.25$ | **Significant Drift** | Automated canary trigger: platform dispatches retrain task to coordinator. |

---

### 4.2 Jensen-Shannon Divergence ($\operatorname{JSD}$)

#### Mathematical Definition
The Jensen-Shannon Divergence is a symmetric, smoothed, and bounded version of the Kullback-Leibler ($\operatorname{KL}$) divergence between two probability distributions $P$ and $Q$:

$$
\operatorname{JSD}(P \parallel Q) = \frac{1}{2} D_{\mathrm{KL}}(P \parallel M) + \frac{1}{2} D_{\mathrm{KL}}(Q \parallel M)
$$

where $M = \frac{1}{2}(P + Q)$ is the equal mixture distribution, and $D_{\mathrm{KL}}$ is the discrete Kullback-Leibler divergence:

$$
D_{\mathrm{KL}}(P \parallel M) = \sum_{x} P(x) \ln\left( \frac{P(x)}{M(x)} \right)
$$

#### Key Mathematical Properties
1. **Symmetry:** $\operatorname{JSD}(P \parallel Q) = \operatorname{JSD}(Q \parallel P)$.
2. **Bounded:** When using base-2 logarithm ($\log_2$), $\operatorname{JSD} \in [0.0, 1.0]$. When using natural logarithm ($\ln$), $\operatorname{JSD} \in [0.0, \ln 2 \approx 0.6931]$.
3. **Metric Property:** The square root $\sqrt{\operatorname{JSD}(P \parallel Q)}$ satisfies the triangle inequality and qualifies as a true mathematical metric (Endres & Schindelin, 2003).

---

## 5. Financial Cost-Utility & Fairness Metrics

### 5.1 Financial Cost-Utility Function

#### Mathematical Definition
To ground statistical scores in bank profit-and-loss ($\text{P&L}$) economics, the platform defines the expected financial loss function $\mathcal{L}_{\mathrm{financial}}$ parameterized by empirical chargeback and review costs:

$$
\mathcal{L}_{\mathrm{financial}}(\theta) = C_{\mathrm{FN}} \cdot \mathrm{FN}(\theta) + C_{\mathrm{FP}} \cdot \mathrm{FP}(\theta) + C_{\mathrm{admin}} \cdot (\mathrm{TP}(\theta) + \mathrm{FP}(\theta))
$$

where empirical industry loss parameters are:
- $C_{\mathrm{FN}} = 850\text{ USD}$ (direct fraud chargeback, card scheme penalty, customer dispute settlement).
- $C_{\mathrm{FP}} = 25\text{ USD}$ (investigator triage labor, SMS two-factor step-up challenge, friction cost).
- $C_{\mathrm{admin}} = 5\text{ USD}$ (automated routing, database ingestion, compliance ledger overhead).

The optimal economic operating threshold $\theta^*_{\mathrm{cost}}$ minimizes total loss:

$$
\theta^*_{\mathrm{cost}} = \arg\min_{\theta \in [0, 1]} \mathcal{L}_{\mathrm{financial}}(\theta)
$$

---

### 5.2 Algorithmic Fairness Metrics (EEOC Four-Fifths & ECOA Regulation B)

Pursuant to the Equal Credit Opportunity Act (ECOA, 12 CFR Part 1002) and European Non-Discrimination mandates, proxy attributes ($A \in \{0, 1\}$) are audited for disparate impact:

1. **Disparate Impact Ratio ($\operatorname{DIR}$):**
   $$\operatorname{DIR} = \frac{P(\hat{Y}=1 \mid A=0)}{P(\hat{Y}=1 \mid A=1)}$$
   *EEOC 80% Rule Compliance Boundary:* $0.80 \le \operatorname{DIR} \le 1.25$.
2. **Equal Opportunity Difference ($\operatorname{EOD}$):**
   $$\operatorname{EOD} = \operatorname{TPR}_{A=0} - \operatorname{TPR}_{A=1} = P(\hat{Y}=1 \mid Y=1, A=0) - P(\hat{Y}=1 \mid Y=1, A=1)$$
   *Target Bound:* $|\operatorname{EOD}| \le 0.05$.
3. **Demographic Parity Difference ($\operatorname{DPD}$):**
   $$\operatorname{DPD} = P(\hat{Y}=1 \mid A=0) - P(\hat{Y}=1 \mid A=1)$$
   *Target Bound:* $|\operatorname{DPD}| \le 0.05$.

---

## 6. Master Summary of Unified Metric Definitions

| Metric Symbol | Full Metric Name | Domain & Purpose | Mathematical Bounds | Production Target | Regulatory Mapping | Implementation Module |
| :--- | :--- | :--- | :---: | :---: | :--- | :--- |
| $\operatorname{PR-AUC}$ | Precision-Recall AUC | Extreme Imbalance Detection | $[\pi, 1.0]$ | $\ge 0.75$ | SR 11-7 Outcomes Analysis | `metrics_service.py` |
| $\operatorname{ROC-AUC}$ | Receiver Operating Characteristic | General Discrimination Ranking | $[0.0, 1.0]$ | $\ge 0.95$ | SR 11-7 Model Discrimination | `metrics_service.py` |
| $\operatorname{Recall@0.1\%FPR}$ | Operational Recall | Fixed Investigator Capacity | $[0.0, 1.0]$ | $\ge 0.60$ | OCC 2011-12 SLA Bounds | `metrics_service.py` |
| $\operatorname{ECE}$ | Expected Calibration Error | Probability Reliability | $[0.0, 1.0]$ | $\le 0.03$ | EU AI Act Art. 15 (Accuracy) | `run_factorial_ablation.py` |
| $\operatorname{BS}$ | Brier Score | Proper Forecast Accuracy | $[0.0, 1.0]$ | $\le 0.02$ | SR 11-7 Probability Accuracy | `run_factorial_ablation.py` |
| $\operatorname{PSI}$ | Population Stability Index | Feature & Concept Drift | $[0.0, \infty)$ | $< 0.10$ | SR 11-7 Ongoing Monitoring | `banks.py`, `drift_service.py` |
| $\operatorname{JSD}$ | Jensen-Shannon Divergence | Symmetric Distribution Drift | $[0.0, 1.0]$ | $\le 0.15$ | BCBS 32 Model Risk | `banks.py`, `drift_service.py` |
| $\operatorname{DIR}$ | Disparate Impact Ratio | Algorithmic Non-Discrimination | $[0.0, \infty)$ | $[0.80, 1.25]$| ECOA Reg B / EEOC 80% Rule | `demographic_audit.py` |
