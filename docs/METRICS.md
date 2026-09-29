# Unified Scientific Metric Definition Standard & Evaluation Glossary

**Document Reference:** `CFI-SPEC-METRICS-2026-V1`  
**Applicable Governance Frameworks:** Federal Reserve SR 11-7 / OCC 2011-12 (Model Risk Management), EU Artificial Intelligence Act (Regulation (EU) 2024/1689) Article 15 (Accuracy, Robustness and Cybersecurity), Basel Committee on Banking Supervision (BCBS) Working Paper 32.

---

## 1. Overview & Epistemological Purpose

In enterprise federated machine learning and regulatory model risk validation, evaluation metrics must be mathematically unambiguous, strictly bounded, and suited to the underlying financial data distribution. 

Financial crime and Anti-Money Laundering (AML) transaction datasets exhibit **extreme class imbalance** (typically $\le 0.15$% fraud prevalence). Under extreme imbalance, standard machine learning metrics such as Accuracy or uncalibrated ROC-AUC produce dangerously misleading assessments:
- An uncalibrated trivial classifier predicting the majority class achieves 99.85% accuracy while detecting **zero fraud**.
- Receiver Operating Characteristic (ROC-AUC) can remain superficially high ($> 0.95$) even when thousands of false alerts overwhelm human investigator queues, because the vast true negative denominator artificially suppresses the False Positive Rate ($\mathrm{FPR} = \frac{\mathrm{FP}}{\mathrm{FP} + \mathrm{TN}}$).

This specification establishes the authoritative mathematical formulations, boundary constraints, operational thresholds, and regulatory mappings for all quantitative metrics reported across CF-Intelligence.

---

## 2. Core Classification & Detection Performance Metrics

### 2.1 Precision-Recall Area Under Curve (PR-AUC / Average Precision)

#### Mathematical Definition
Precision-Recall Area Under the Curve evaluates the trade-off between positive predictive value (Precision) and sensitivity (Recall) across all classification thresholds $\theta \in [0, 1]$:

$$
\mathrm{PR\text{-}AUC} = \int_{0}^{1} p(r) \, dr \approx \sum_{k=1}^{K} (r_k - r_{k-1}) \cdot p(r_k)
$$

where Precision $p(r)$ and Recall $r$ are parameterized by threshold $\theta$:

$$
p(\theta) = \frac{\mathrm{TP}(\theta)}{\mathrm{TP}(\theta) + \mathrm{FP}(\theta)}, \quad r(\theta) = \frac{\mathrm{TP}(\theta)}{\mathrm{TP}(\theta) + \mathrm{FN}(\theta)}
$$

In finite sample implementations, CF-Intelligence uses the non-interpolated Average Precision ($\mathrm{AP}$) formulation:

$$
\mathrm{AP} = \sum_{n=1}^{N} (R_n - R_{n-1}) \cdot P_n
$$

where $P_n$ and $R_n$ are the precision and recall at the $n$-th operational threshold.

#### Operational Interpretation & Target Bounds
- **Range:** $\mathrm{PR\text{-}AUC} \in [\pi, 1.0]$, where $\pi = \frac{P}{P + N}$ is the baseline fraud prevalence (random guessing baseline).
- **Target Performance:**
  - $\mathrm{PR\text{-}AUC} \ge 0.75$: High-efficacy production champion candidate.
  - $\mathrm{PR\text{-}AUC} \in [0.40, 0.74]$: Acceptable multi-bank federated detector under non-IID skew ($\alpha \le 0.50$).
  - $\mathrm{PR\text{-}AUC} < 0.20$: Sub-baseline / non-converged local silo.

---

### 2.2 Receiver Operating Characteristic Area Under Curve (ROC-AUC)

#### Mathematical Definition
The ROC curve traces the True Positive Rate ($\mathrm{TPR}$) against the False Positive Rate ($\mathrm{FPR}$) across all continuous classification thresholds:

$$
\mathrm{ROC\text{-}AUC} = \int_{0}^{1} \mathrm{TPR}(\mathrm{FPR}^{-1}(t)) \, dt
$$

Probabilistically, $\mathrm{ROC\text{-}AUC}$ equals the Wilcoxon-Mann-Whitney concordance probability that a randomly chosen fraudulent transaction $x^+$ receives a strictly higher predicted risk score than a randomly chosen legitimate transaction $x^-$:

$$
\mathrm{ROC\text{-}AUC} = \mathbb{P}\left(\hat{s}(x^+) > \hat{s}(x^-) \mid y(x^+) = 1, y(x^-) = 0\right)
$$

In discrete finite samples:

$$
\mathrm{ROC\text{-}AUC} = \frac{1}{N^+ \cdot N^-} \sum_{i: y_i = 1} \sum_{j: y_j = 0} \left( \mathbb{I}(\hat{s}_i > \hat{s}_j) + \frac{1}{2} \mathbb{I}(\hat{s}_i = \hat{s}_j) \right)
$$

#### Operational Interpretation & Target Bounds
- **Range:** $\mathrm{ROC\text{-}AUC} \in [0.0, 1.0]$ (random baseline: $0.50$).
- **Regulatory Caveat (SR 11-7):** Must **never** be reported as the sole validation metric on imbalanced financial data. Must always be accompanied by $\mathrm{PR\text{-}AUC}$ and $\mathrm{Recall@FPR}$.

---

### 2.3 Recall at Fixed False Positive Rate (Recall@FPR)

#### Mathematical Definition
In production anti-fraud operations, human compliance investigator capacity is strictly bounded. Models cannot be evaluated at arbitrary probability thresholds (e.g. $\theta = 0.50$). Instead, the operational threshold $\theta^{\ast}$ is calibrated to guarantee that false alarms do not exceed a statutory ceiling $\alpha_{\mathrm{target}}$:

$$
\theta^{\ast} = \inf \left\lbrace \theta \in [0, 1] : \mathrm{FPR}(\theta) \le \alpha_{\mathrm{target}} \right\rbrace
$$

where:

$$
\mathrm{FPR}(\theta) = \frac{\sum_{j: y_j = 0} \mathbb{I}(\hat{s}_j \ge \theta)}{\sum_{j} \mathbb{I}(y_j = 0)}
$$

$\mathrm{Recall@FPR}$ is the sensitivity achieved at this operational threshold:

$$
\mathrm{Recall}(\alpha_{\mathrm{target}}) = \mathrm{TPR}(\theta^{\ast}) = \frac{\sum_{i: y_i = 1} \mathbb{I}(\hat{s}_i \ge \theta^{\ast})}{\sum_{i} \mathbb{I}(y_i = 1)}
$$

#### Standard Banking Operating Points
- **Recall @ 0.01% FPR ($\alpha = 0.0001$):** Ultra-strict tier. Permits at most 1 false alarm per $10{,}000$ legitimate transactions. Target: $\ge 0.50$ (50% fraud caught before friction).
- **Recall @ 0.1% FPR ($\alpha = 0.0010$):** Standard automated review tier. Permits at most 1 false alarm per $1{,}000$ legitimate transactions. Target: $\ge 0.60$.

---

## 3. Probability Calibration & Proper Scoring Rules

### 3.1 Expected Calibration Error (ECE)

#### Mathematical Definition
Expected Calibration Error quantifies the discrepancy between model predicted probabilities and empirical ground-truth event frequencies. Predictions $\hat{p}_i \in [0, 1]$ are partitioned into $M$ equal-width bins $B_1, B_2, \dots, B_M \subset [0, 1]$ (default $M=10$):

$$
B_m = \left( \frac{m-1}{M}, \frac{m}{M} \right]
$$

$\mathrm{ECE}$ is the weighted average absolute difference between empirical accuracy and average confidence across all bins:

$$
\mathrm{ECE} = \sum_{m=1}^{M} \frac{|B_m|}{N} \left| \mathrm{acc}(B_m) - \mathrm{conf}(B_m) \right|
$$

where:

$$
\mathrm{conf}(B_m) = \frac{1}{|B_m|} \sum_{i \in B_m} \hat{p}_i, \quad \mathrm{acc}(B_m) = \frac{1}{|B_m|} \sum_{i \in B_m} y_i
$$

#### Operational Interpretation
- **Range:** $\mathrm{ECE} \in [0.0, 1.0]$.
- **Target:** $\mathrm{ECE} \le 0.030$ (3.0%).
- **Implication:** Prevents overconfident deep learning models from distorting downstream Bayesian risk aggregators or automated payment blocking rules.

---

### 3.2 Brier Score (BS)

#### Mathematical Definition
The Brier Score is a strictly proper scoring rule quantifying the mean squared error of probabilistic forecasts:

$$
\mathrm{BS} = \frac{1}{N} \sum_{i=1}^{N} (\hat{p}_i - y_i)^2
$$

Under Murphy's decomposition (Murphy, 1973), the Brier Score decomposes into three orthogonal components:

$$
\mathrm{BS} = \mathrm{Reliability} - \mathrm{Resolution} + \mathrm{Uncertainty}
$$

where:

$$
\mathrm{Reliability} = \sum_{m=1}^{M} \frac{|B_m|}{N} \left( \mathrm{conf}(B_m) - \mathrm{acc}(B_m) \right)^2
$$

$$
\mathrm{Resolution} = \sum_{m=1}^{M} \frac{|B_m|}{N} \left( \mathrm{acc}(B_m) - \bar{y} \right)^2
$$

$$
\mathrm{Uncertainty} = \bar{y}(1 - \bar{y}), \quad \bar{y} = \frac{1}{N}\sum_{i=1}^N y_i
$$

#### Operational Interpretation
- **Range:** $\mathrm{BS} \in [0.0, 1.0]$ (lower is strictly superior).
- **Target:** $\mathrm{BS} \le 0.020$ on low-prevalence financial streams.

---

## 4. Distribution Drift & Population Stability Metrics

### 4.1 Population Stability Index (PSI)

#### Mathematical Definition
The Population Stability Index measures the shift between a baseline reference distribution $P$ (e.g. historical baseline or training partition) and an actual production distribution $Q$ partitioned across $B$ bins (default deciles $B=10$):

$$
\mathrm{PSI} = \sum_{b=1}^{B} (q_b - p_b) \cdot \ln\left( \frac{q_b + \epsilon_{\mathrm{smooth}}}{p_b + \epsilon_{\mathrm{smooth}}} \right)
$$

where $p_b$ is the proportion of reference samples in bin $b$, $q_b$ is the proportion of actual samples in bin $b$, and $\epsilon_{\mathrm{smooth}} = 10^{-7}$ prevents numerical division by zero.

#### Regulatory Traffic-Light Action Matrix (Federal Reserve SR 11-7 / OCC)

| PSI Value | Drift Classification | System Action & Governance Response |
| :--- | :--- | :--- |
| $\mathrm{PSI} < 0.10$ | **Stable / No Drift** | Model operates within validated baseline. No action required. |
| $0.10 \le \mathrm{PSI} \le 0.25$ | **Moderate Drift** | Warning issued. Retraining queue enters `MONITORING_ALERT` status. |
| $\mathrm{PSI} > 0.25$ | **Significant Drift** | Automated canary trigger: platform dispatches retrain task to coordinator. |

---

### 4.2 Jensen-Shannon Divergence (JSD)

#### Mathematical Definition
The Jensen-Shannon Divergence is a symmetric, smoothed, and bounded version of the Kullback-Leibler ($\mathrm{KL}$) divergence between two probability distributions $P$ and $Q$:

$$
\mathrm{JSD}(P \parallel Q) = \frac{1}{2} D_{\mathrm{KL}}(P \parallel M) + \frac{1}{2} D_{\mathrm{KL}}(Q \parallel M)
$$

where $M = \frac{1}{2}(P + Q)$ is the equal mixture distribution, and $D_{\mathrm{KL}}$ is the discrete Kullback-Leibler divergence:

$$
D_{\mathrm{KL}}(P \parallel M) = \sum_{x} P(x) \ln\left( \frac{P(x)}{M(x)} \right)
$$

#### Key Mathematical Properties
1. **Symmetry:** $\mathrm{JSD}(P \parallel Q) = \mathrm{JSD}(Q \parallel P)$.
2. **Bounded:** When using base-2 logarithm ($\log_2$), $\mathrm{JSD} \in [0.0, 1.0]$. When using natural logarithm ($\ln$), $\mathrm{JSD} \in [0.0, \ln 2 \approx 0.6931]$.
3. **Metric Property:** The square root $\sqrt{\mathrm{JSD}(P \parallel Q)}$ satisfies the triangle inequality and qualifies as a true mathematical metric (Endres & Schindelin, 2003).

---

## 5. Financial Cost-Utility & Fairness Metrics

### 5.1 Financial Cost-Utility Function

#### Mathematical Definition
To ground statistical scores in bank profit-and-loss (P&L) economics, the platform defines the expected financial loss function $\mathcal{L}_{\mathrm{financial}}$ parameterized by empirical chargeback and review costs:

$$
\mathcal{L}_{\mathrm{financial}}(\theta) = C_{\mathrm{FN}} \cdot \mathrm{FN}(\theta) + C_{\mathrm{FP}} \cdot \mathrm{FP}(\theta) + C_{\mathrm{admin}} \cdot (\mathrm{TP}(\theta) + \mathrm{FP}(\theta))
$$

where empirical industry loss parameters are:
- $C_{\mathrm{FN}} = 850\text{ USD}$ (direct fraud chargeback, card scheme penalty, customer dispute settlement).
- $C_{\mathrm{FP}} = 25\text{ USD}$ (investigator triage labor, SMS two-factor step-up challenge, friction cost).
- $C_{\mathrm{admin}} = 5\text{ USD}$ (automated routing, database ingestion, compliance ledger overhead).

The optimal economic operating threshold $\theta^{\ast}_{\mathrm{cost}}$ minimizes total loss:

$$
\theta^{\ast}_{\mathrm{cost}} = \arg\min_{\theta \in [0, 1]} \mathcal{L}_{\mathrm{financial}}(\theta)
$$

---

### 5.2 Algorithmic Fairness Metrics (EEOC Four-Fifths & ECOA Regulation B)

Pursuant to the Equal Credit Opportunity Act (ECOA, 12 CFR Part 1002) and European Non-Discrimination mandates, proxy attributes ($A \in \{0, 1\}$) are audited for disparate impact:

1. **Disparate Impact Ratio (DIR):**

   $$\mathrm{DIR} = \frac{\mathbb{P}(\hat{Y}=1 \mid A=0)}{\mathbb{P}(\hat{Y}=1 \mid A=1)}$$

   *EEOC 80% Rule Compliance Boundary:* $0.80 \le \mathrm{DIR} \le 1.25$.

2. **Equal Opportunity Difference (EOD):**

   $$\mathrm{EOD} = \mathrm{TPR}_{A=0} - \mathrm{TPR}_{A=1} = \mathbb{P}(\hat{Y}=1 \mid Y=1, A=0) - \mathbb{P}(\hat{Y}=1 \mid Y=1, A=1)$$

   *Target Bound:* $|\mathrm{EOD}| \le 0.05$.

3. **Demographic Parity Difference (DPD):**

   $$\mathrm{DPD} = \mathbb{P}(\hat{Y}=1 \mid A=0) - \mathbb{P}(\hat{Y}=1 \mid A=1)$$

   *Target Bound:* $|\mathrm{DPD}| \le 0.05$.

---

## 6. Master Summary of Unified Metric Definitions

| Metric Symbol | Full Metric Name | Domain & Purpose | Mathematical Bounds | Production Target | Regulatory Mapping | Implementation Module |
| :--- | :--- | :--- | :---: | :---: | :--- | :--- |
| **PR-AUC** | Precision-Recall AUC | Extreme Imbalance Detection | $[\pi, 1.0]$ | $\ge 0.75$ | SR 11-7 Outcomes Analysis | `metrics_service.py` |
| **ROC-AUC** | Receiver Operating Characteristic | General Discrimination Ranking | $[0.0, 1.0]$ | $\ge 0.95$ | SR 11-7 Model Discrimination | `metrics_service.py` |
| **Recall @ 0.1% FPR** | Operational Recall | Fixed Investigator Capacity | $[0.0, 1.0]$ | $\ge 0.60$ | OCC 2011-12 SLA Bounds | `metrics_service.py` |
| **ECE** | Expected Calibration Error | Probability Reliability | $[0.0, 1.0]$ | $\le 0.03$ | EU AI Act Art. 15 (Accuracy) | `run_factorial_ablation.py` |
| **BS** | Brier Score | Proper Forecast Accuracy | $[0.0, 1.0]$ | $\le 0.02$ | SR 11-7 Probability Accuracy | `run_factorial_ablation.py` |
| **PSI** | Population Stability Index | Feature & Concept Drift | $[0.0, \infty)$ | $< 0.10$ | SR 11-7 Ongoing Monitoring | `banks.py`, `drift_service.py` |
| **JSD** | Jensen-Shannon Divergence | Symmetric Distribution Drift | $[0.0, 1.0]$ | $\le 0.15$ | BCBS 32 Model Risk | `banks.py`, `drift_service.py` |
| **DIR** | Disparate Impact Ratio | Algorithmic Non-Discrimination | $[0.0, \infty)$ | $[0.80, 1.25]$ | ECOA Reg B / EEOC 80% Rule | `demographic_audit.py` |
